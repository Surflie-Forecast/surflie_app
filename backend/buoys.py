"""Buoy comparison files: the notebook's apples-to-apples panel as static JSON.

Reproduces "Apples-to-apples at the buoys" from
data_exploration/model_exploration_devereaux.ipynb: per buoy, its observed Hs
(CDIP ERDDAP), CDIP's MOP forecast at that exact site (THREDDS
MOP_validation, ~8-day horizon), and the global wave models re-fetched at the
buoy's coordinates. Left of "now", every line is checkable against reality.

Outputs:

    api/buoys.json          the roster (id, name, position, stale flag)
    api/buoys/{id}.json     observed + MOP + per-model Hs, one per buoy

TWO observation sources, split by the roster's `source` column:

  - CDIP (`cdip`, the default for a blank column) — the nearshore Waverider
    array. Batched ERDDAP queries, plus the MOP forecast at the exact site.
  - NDBC (`ndbc`) — NOAA's offshore buoys, for the stations CDIP doesn't
    operate. OBSERVATIONS ONLY: one realtime2 text file per station, SST
    included inline. They have no MOP transect, so their only forecast is
    the global models re-fetched at their coordinates. NDBC also mirrors the
    CDIP buoys at lower resolution — we deliberately do NOT use it for
    those; CDIP is the operator and the better source.

WHICH buoys is static: data/buoys.csv, committed like locations.csv, is the
roster the scheduled pipeline builds — no per-run discovery, so the set of
detail files never changes on its own. Refresh it explicitly with

    python backend/buoys.py --refresh-roster

which re-queries ERDDAP for active California stations ("active" = an
observation within ACTIVE_WITHIN_H hours, "California" = ', CA ' in the
station name) and rewrites the CSV for you to review and commit. That
discovery only knows CDIP; the hand-curated NDBC rows are carried through
untouched. Every roster buoy gets a detail file, because the frontend lets
the user open ANY buoy (map markers and search) — not just each spot's
assigned one.

The live data for that fixed roster is fetched with a fixed, small request
budget (~300 sequential requests before; ~2 + 5 + 2/buoy now, the per-buoy
part parallelized):

  - ONE ERDDAP query for every station's last PAST_DAYS of Hs/Tp/Dp (the
    observed series AND the "latest" headline come out of it), plus ONE for
    the latest SST of every station.
  - FIVE Open-Meteo multi-coordinate requests (one per wave model, all
    buoys batched — same pattern the spot pipeline uses).
  - TWO THREDDS reads per buoy for the MOP validation forecast (shape +
    tail), run through a small thread pool. A shared Session keeps
    connections alive; requests carry a 30s timeout instead of 90s so a
    slow run degrades instead of snowballing into a CI timeout.

build_buoys also fills in each spot's blank closest_buoy (new spots):
nearest roster buoy by great-circle distance, persisted back to
locations.csv by the pipeline's save_spots so it's computed once.

On top of that static assignment, every run re-points spots.json's
closest_buoy at a swell-aware pick: the nearest fresh buoy lying TOWARD
today's swell source (it samples the waves before they arrive), falling
back to plain proximity when no fresh buoy faces the swell.

This runs outside the store: buoys aren't spots, and the panel needs past
observations the store's forecast window never holds. Non-critical — the
pipeline logs and moves on if any of it fails, and a single buoy's failure
never blocks the others.
"""
import csv
import json
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import StringIO
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter

from adapters import _openmeteo, openmeteo_marine
from builders import WAVE_MODELS, _write_json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROSTER_FILE = os.path.join(BASE_DIR, '..', 'data', 'buoys.csv')
ROSTER_FIELDS = ['station_id', 'name', 'latitude', 'longitude', 'source']

PAST_DAYS = 3            # observation window shown left of "now"
# Fetch each model to its full native horizon: shorter models (MFWAM ~10,
# GWAM ~8, MOP ~8) simply end sooner and the frontend draws each to wherever
# its data runs out. Open-Meteo accepts end_date up to today_UTC + 16 and
# null-pads past each model's real horizon (GFS Wave ~today+15 18:00 UTC),
# so 16 requests the widest horizon; _openmeteo.get_json clamps and retries
# if a date skew ever pushes the request over the cap.
FORECAST_DAYS = 16
ACTIVE_WITHIN_H = 24     # roster refresh: active = reported within this
# MOP validation tail: the axis is hourly but NOT gap-free (offline
# stretches are simply absent), so index math can't find "PAST_DAYS ago".
# The window we display is PAST_DAYS + the ~8-day MOP horizon ≈ 264 hourly
# samples; a 480-sample tail covers it with slack even across gaps, in one
# read instead of the old estimate-then-page-back walk.
MOP_TAIL_SAMPLES = 480
MOP_WORKERS = 6          # THREDDS reads in flight; be polite to CDIP

ERDDAP = 'https://erddap.cdip.ucsd.edu/erddap/tabledap/wave_agg.csv'
SST_ERDDAP = 'https://erddap.cdip.ucsd.edu/erddap/tabledap/sst_agg.csv'
# NDBC: observations ONLY, for the offshore stations CDIP doesn't operate.
# One text file per station, newest row first, 'MM' for missing. These buoys
# have no MOP transect, so they carry the global models as their only
# forecast — see build_buoys.
NDBC_REALTIME = 'https://www.ndbc.noaa.gov/data/realtime2/{sid}.txt'
NDBC_WORKERS = 6         # one request per NDBC station; be polite to NOAA
SRC_CDIP, SRC_NDBC = 'cdip', 'ndbc'
MOP_URL = ('https://thredds.cdip.ucsd.edu/thredds/dodsC/'
           'cdip/model/MOP_validation/BP{sid}_forecast.nc')
FILL_CUTOFF = -100.0     # values below this are the -999.99 fill
TIMEOUT = 30             # seconds; retries below make up for the short leash
TZ = ZoneInfo('America/Los_Angeles')  # CA buoys only, so one timezone

# Keep-alive across the ~60 CDIP requests per run: from a GitHub runner each
# fresh TLS handshake costs seconds, and it's pure waste against two hosts.
_session = requests.Session()
_session.mount('https://', HTTPAdapter(max_retries=1, pool_maxsize=MOP_WORKERS + 2))


def _get(url, timeout=TIMEOUT):
    r = _session.get(url, timeout=timeout)
    r.raise_for_status()
    return r


def _fmt_local(ts_utc):
    return ts_utc.astimezone(TZ).strftime('%Y-%m-%d %H:%M:%S')


def _parse_utc(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def _num(raw, fill=True):
    """float from an ERDDAP cell, or None if it isn't a real measurement.
    A station that's reporting but has no wave solution for a sample sends
    the literal 'NaN' (not an empty cell), which is truthy and survives
    float() — round() on it then dies. Fill values get the same treatment
    as in the THREDDS path.

    fill=False skips the FILL_CUTOFF test, for values that are legitimately
    very negative: every California longitude is below the cutoff."""
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    if math.isnan(v):
        return None
    return None if fill and v < FILL_CUTOFF else v


# ---------------------------------------------------------------------------
# Static roster (data/buoys.csv)

def load_roster(path=ROSTER_FILE):
    """{station_id: {'name', 'latitude', 'longitude', 'source'}} from the
    static CSV. A blank/absent source column means CDIP — the column was
    added when NDBC-only stations joined the roster, and every pre-existing
    row is CDIP."""
    roster = {}
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            roster[row['station_id'].strip()] = {
                'name': row['name'].strip(),
                'latitude': float(row['latitude']),
                'longitude': float(row['longitude']),
                'source': (row.get('source') or '').strip() or SRC_CDIP,
            }
    return roster


def _by_source(roster, source):
    return {sid: e for sid, e in roster.items() if e['source'] == source}


def _pretty_name(meta_name):
    """'HARVEST, CA BUOY - 071p1' -> 'Harvest'."""
    return meta_name.split(' BUOY')[0].rsplit(', CA', 1)[0].strip().title()


def refresh_roster(path=ROSTER_FILE):
    """Rewrite the roster CSV from a live ERDDAP discovery of active CA
    stations. Explicit-only (never run by the pipeline): review the diff,
    prune what you don't want, commit."""
    start = datetime.now(timezone.utc) - timedelta(hours=ACTIVE_WITHIN_H)
    query = ('?station_id,metaStationName,time,latitude,longitude,waveHs'
             f"&time>={start.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    r = _get(ERDDAP + query, timeout=90)

    reader = csv.DictReader(StringIO(r.text))
    next(reader)  # row 1 = units
    stations = {}
    for row in reader:
        sid, name = row['station_id'], row['metaStationName']
        lat = _num(row['latitude'], fill=False)
        lon = _num(row['longitude'], fill=False)
        if (not sid or ', CA ' not in name or _num(row['waveHs']) is None
                or lat is None or lon is None):
            continue
        stations[sid] = {
            'station_id': sid,
            'name': _pretty_name(name),
            'latitude': lat,
            'longitude': lon,
            'source': SRC_CDIP,
        }

    # This discovery only knows CDIP. NDBC rows are hand-curated (they have
    # no equivalent "active CA stations" query), so carry them through
    # untouched instead of dropping them on every roster refresh.
    kept = 0
    try:
        for sid, e in load_roster(path).items():
            if e['source'] != SRC_CDIP and sid not in stations:
                stations[sid] = {'station_id': sid, 'name': e['name'],
                                 'latitude': e['latitude'],
                                 'longitude': e['longitude'],
                                 'source': e['source']}
                kept += 1
    except FileNotFoundError:
        pass

    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=ROSTER_FIELDS)
        writer.writeheader()
        for sid in sorted(stations):
            writer.writerow(stations[sid])
    print(f"✓ {path}: {len(stations) - kept} active CA CDIP stations "
          f"(reported within {ACTIVE_WITHIN_H}h)"
          f"{f' + {kept} NDBC rows kept' if kept else ''}")


# ---------------------------------------------------------------------------
# Batched live fetches

def _fetch_observations(station_ids, start_utc):
    """{sid: {'observed': [(ts, hs)], 'latest': {...}}} for every roster
    station, from ONE ERDDAP request covering Hs/Tp/Dp. 'latest' is the
    newest reading — the values CDIP's station page headlines (SST is merged
    in separately by _fetch_latest_sst)."""
    query = ('?station_id,time,waveHs,waveTp,waveDp'
             f"&time>={start_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    r = _get(ERDDAP + query, timeout=90)

    reader = csv.DictReader(StringIO(r.text))
    next(reader)  # row 1 = units
    wanted = set(station_ids)
    out = {}
    for row in reader:
        sid = row['station_id']
        hs = _num(row['waveHs'])
        if sid not in wanted or hs is None:
            continue
        ts = _parse_utc(row['time'])
        e = out.setdefault(sid, {'observed': [], 'latest': None, '_ts': None})
        e['observed'].append((ts, hs))
        if e['_ts'] is None or ts > e['_ts']:  # don't trust row order
            latest = {'time': _fmt_local(ts), 'hs': round(hs, 2)}
            tp, dp = _num(row.get('waveTp')), _num(row.get('waveDp'))
            if tp is not None:
                latest['tp'] = round(tp, 1)
            if dp is not None:
                latest['dp'] = round(dp)
            e['latest'], e['_ts'] = latest, ts
    for e in out.values():
        e['observed'].sort()
        del e['_ts']
    return out


def _ndbc_rows(text):
    """Data rows of an NDBC realtime2 file as lists of fields, newest first.
    Two leading '#' lines are the header and the units."""
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            fields = line.split()
            if len(fields) >= 15:
                yield fields


def _ndbc_num(raw):
    """NDBC writes 'MM' for a missing value; everything else is numeric."""
    if raw == 'MM':
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    return None if math.isnan(v) or v < FILL_CUTOFF else v


def _fetch_ndbc_observations(station_ids, start_utc):
    """Same shape as _fetch_observations, for NDBC stations: {sid: {'observed',
    'latest'}}. One request per station (NDBC has no batched endpoint like
    ERDDAP's), run through a small pool.

    Columns are positional in a fixed 19-field layout:
        0-4 YY MM DD hh mm   8 WVHT(m)  9 DPD(s)  11 MWD(degT)  14 WTMP(degC)

    SST rides along in the same file, so these stations need no second
    request — unlike CDIP, where SST is a separate aggregate."""
    def fetch(sid):
        text = _get(NDBC_REALTIME.format(sid=sid)).text
        observed, latest, latest_ts = [], None, None
        for f in _ndbc_rows(text):
            hs = _ndbc_num(f[8])
            if hs is None:
                continue  # met-only row: this station reports wind every
                          # 10 min but waves only every 30-60
            try:
                ts = datetime(*(int(x) for x in f[:5]), tzinfo=timezone.utc)
            except ValueError:
                continue
            if ts < start_utc:
                break  # rows are newest-first, so everything below is older
            observed.append((ts, hs))
            if latest_ts is None or ts > latest_ts:
                latest = {'time': _fmt_local(ts), 'hs': round(hs, 2)}
                tp, dp = _ndbc_num(f[9]), _ndbc_num(f[11])
                sst = _ndbc_num(f[14])
                if tp is not None:
                    latest['tp'] = round(tp, 1)
                if dp is not None:
                    latest['dp'] = round(dp)
                if sst is not None:
                    latest['sst'] = round(sst, 1)
                latest_ts = ts
        observed.sort()
        return sid, observed, latest

    out = {}
    if not station_ids:
        return out
    with ThreadPoolExecutor(max_workers=NDBC_WORKERS) as pool:
        futures = [pool.submit(fetch, sid) for sid in sorted(station_ids)]
        for fut in futures:
            try:
                sid, observed, latest = fut.result()
            except Exception as err:
                print(f"    ! NDBC station fetch failed: {err}")
                continue
            if observed:
                out[sid] = {'observed': observed, 'latest': latest}
    return out


def _fetch_latest_sst(station_ids, since_utc):
    """{sid: sst_degC}: the newest SST per station, one ERDDAP request.
    orderByMax("station_id,time") groups by station and keeps each group's
    latest row; the time floor bounds the server-side scan."""
    query = ('?station_id,time,sstSeaSurfaceTemperature'
             f"&time>={since_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"
             '&orderByMax(%22station_id,time%22)')
    r = _get(SST_ERDDAP + query)

    reader = csv.DictReader(StringIO(r.text))
    next(reader)  # units row
    wanted = set(station_ids)
    out = {}
    for row in reader:
        sst = _num(row['sstSeaSurfaceTemperature'])
        if row['station_id'] in wanted and sst is not None:
            out[row['station_id']] = round(sst, 1)
    return out


def _fetch_models(roster, start_utc, end_utc, skipped_sources=()):
    """{sid: [(model_id, label, [(ts, {'v','p','d'})])]}: per-model forecast
    at every buoy's coordinates, ONE multi-coordinate Open-Meteo request per
    model (the same batching the spot pipeline uses). Each point carries Hs
    ('v') plus swell period ('p') and direction ('d') where the model serves
    them (ECMWF WAM is Hs-only) — the extra variables ride the same request
    and feed the swell alarms (backend/alerts.py); the chart keeps reading
    'v'. A model whose cell is dry at a buoy (all ~0 Hs) is dropped for that
    buoy, as before. Sources in skipped_sources (no new run upstream — see
    pipeline --scheduled) aren't fetched; the detail writer carries their
    series over from the previous files."""
    coords = [(sid, e['latitude'], e['longitude'])
              for sid, e in sorted(roster.items())]
    out = {sid: [] for sid in roster}
    keymap = {'wave_height': 'v', 'swell_period': 'p', 'swell_direction': 'd'}
    for model, source in openmeteo_marine.MODELS:
        if source in skipped_sources:
            continue
        try:
            rows, _ = _openmeteo.fetch_hourly_rows_batch(
                'https://marine-api.open-meteo.com/v1/marine', coords,
                {'wave_height': 'wave_height',
                 'swell_wave_period': 'swell_period',
                 'swell_wave_direction': 'swell_direction'},
                source, start_utc, end_utc,
                extra_params={'models': model, 'cell_selection': 'sea'})
        except Exception as e:
            print(f"    ! models batch {model}: {e}")
            continue
        by_sid = {}
        for row in rows:
            key = keymap.get(row['variable'])
            if key is None:
                continue
            by_sid.setdefault(row['spot'], {}).setdefault(
                datetime.fromisoformat(row['ts_utc']), {})[key] = row['value']
        for sid, points in by_sid.items():
            series = sorted(points.items())
            hs = [pt['v'] for _, pt in series if pt.get('v') is not None]
            if hs and max(hs) > 0.01:
                out[sid].append(
                    (source.split(':', 1)[1], WAVE_MODELS[source], series))
    return out


def _fetch_mop_forecast(sid, start_utc):
    """[(ts, {'v','p','d'})] from the buoy's MOP validation forecast, sliced
    from start_utc: Hs plus peak period/direction, same point shape as the
    Open-Meteo model series (chart reads 'v', alarms read 'p'/'d'). Two
    reads: the .dds for the axis length, then one tail read of the last
    MOP_TAIL_SAMPLES points — see the constant for why a fixed tail replaces
    index math on this gappy axis."""
    base = MOP_URL.format(sid=sid)

    dds = _get(f'{base}.dds').text
    n = int(re.search(r'waveTime = (\d+)', dds).group(1))
    i0 = max(0, n - MOP_TAIL_SAMPLES)

    rng = f'%5B{i0}:1:{n - 1}%5D'
    r = _get(f'{base}.ascii?waveTime{rng},waveHs{rng},waveTp{rng},waveDp{rng}')
    body = re.split(r'^-{20,}$', r.text, maxsplit=1, flags=re.M)[1]

    def values(name):
        try:
            return [float(v) for v in
                    body.split(f'{name}[{n - i0}]')[1].strip().splitlines()[0].split(',')]
        except IndexError:
            return None  # variable absent from this station's file

    tp, dp = values('waveTp'), values('waveDp')
    out = []
    for i, (t, hs) in enumerate(zip(values('waveTime'), values('waveHs'))):
        ts = datetime.fromtimestamp(int(t), tz=timezone.utc)
        if hs < FILL_CUTOFF or ts < start_utc:
            continue
        pt = {'v': hs}
        if tp and tp[i] >= FILL_CUTOFF:
            pt['p'] = tp[i]
        if dp and dp[i] >= FILL_CUTOFF:
            pt['d'] = dp[i]
        out.append((ts, pt))
    return out


def _series_json(series):
    """Points are (ts, float) for plain Hs series (observed, MOP) or
    (ts, {'v','p','d'}) for model series; points without Hs are dropped
    (the chart requires 'v'), period/direction ride along when present."""
    out = []
    for ts, v in series:
        if isinstance(v, dict):
            if v.get('v') is None:
                continue
            pt = {'t': _fmt_local(ts), 'v': round(v['v'], 2)}
            if v.get('p') is not None:
                pt['p'] = round(v['p'], 1)
            if v.get('d') is not None:
                pt['d'] = round(v['d'])
            out.append(pt)
        else:
            out.append({'t': _fmt_local(ts), 'v': round(v, 2)})
    return out


# ---------------------------------------------------------------------------
# Spot -> buoy assignment (static fill + per-run swell-aware pick)

def _km(lat1, lon1, lat2, lon2):
    """Great-circle distance (haversine), km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(p1) * math.cos(p2)
         * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(a))


def _bearing(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing from point 1 to point 2, degrees 0-360."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = (math.cos(p1) * math.sin(p2)
         - math.sin(p1) * math.cos(p2) * math.cos(dl))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _ang_diff(a, b):
    """Smallest absolute difference between two bearings, degrees 0-180."""
    return abs((a - b + 180) % 360 - 180)


SWELL_CONE_DEG = 75  # a buoy "faces" the swell within this half-angle


def _swell_direction(api_dir, slug):
    """Today's swell FROM-direction at the spot, read from the forecast file
    build_all just wrote (None if it's missing or malformed)."""
    try:
        with open(os.path.join(api_dir, 'forecast', f'{slug}.json')) as f:
            return float(json.load(f)['forecast'][0]['swell']['direction'])
    except Exception:
        return None


def _pick_swell_buoys(spots, buoys, api_dir):
    """slug -> station id: the nearest buoy lying TOWARD today's swell source
    (bearing spot->buoy within SWELL_CONE_DEG of the swell's FROM-direction),
    so it samples the waves before they reach the spot. Only buoys with
    recent observations qualify — a silent station can't vouch for the
    swell. Falls back to the CSV's plain-proximity closest_buoy when no
    fresh buoy faces the swell or the swell direction is unknown."""
    picks = {}
    for spot in spots:
        swell_from = _swell_direction(api_dir, spot.slug)
        pick = None
        if swell_from is not None:
            in_cone = [
                (_km(spot.latitude, spot.longitude,
                     e['latitude'], e['longitude']), sid)
                for sid, e in buoys.items()
                if _ang_diff(_bearing(spot.latitude, spot.longitude,
                                      e['latitude'], e['longitude']),
                             swell_from) <= SWELL_CONE_DEG]
            if in_cone:
                dist, pick = min(in_cone)
                print(f"  ✓ {spot.name}: swell from {swell_from:.0f}° -> "
                      f"buoy {pick} ({buoys[pick]['name']}, {dist:.0f} km)")
        if pick is None and spot.closest_buoy:
            pick = spot.closest_buoy
            print(f"  ✓ {spot.name}: no fresh buoy toward the swell; "
                  f"falling back to nearest ({pick})")
        if pick:
            picks[spot.slug] = pick
    return picks


def _patch_spots_json(api_dir, picks):
    """Point spots.json's closest_buoy at the per-run swell-aware picks
    (build_all wrote it before the buoy step, with the CSV's static
    proximity values)."""
    path = os.path.join(api_dir, 'spots.json')
    try:
        with open(path) as f:
            entries = json.load(f)
        for e in entries:
            if picks.get(e.get('slug')):
                e['closest_buoy'] = picks[e['slug']]
        _write_json(path, entries)
    except Exception as err:
        print(f"  ! spots.json buoy patch failed: {err}")


def _assign_closest_buoys(spots, roster):
    """Fill each spot's blank closest_buoy with the nearest roster station.

    Runs once per new spot: the pipeline persists the value to locations.csv
    right after, and a non-blank (including hand-edited) value is left alone.
    """
    for spot in spots:
        if spot.closest_buoy:
            continue
        sid = min(roster, key=lambda s: _km(
            spot.latitude, spot.longitude,
            roster[s]['latitude'], roster[s]['longitude']))
        # Spot is frozen (identity fields); this is the one sanctioned fill.
        object.__setattr__(spot, 'closest_buoy', sid)
        print(f"  ✓ {spot.name}: closest buoy assigned -> "
              f"{sid} ({roster[sid]['name']})")


# ---------------------------------------------------------------------------

def build_buoys(api_dir, spots=(), roster_path=ROSTER_FILE,
                skipped_sources=()):
    now = datetime.now(timezone.utc)
    start_utc = now - timedelta(days=PAST_DAYS)
    end_utc = now + timedelta(days=FORECAST_DAYS)

    try:
        roster = load_roster(roster_path)
    except FileNotFoundError:
        print(f"  ! no buoy roster at {roster_path} — "
              "run: python backend/buoys.py --refresh-roster")
        return
    if not roster:
        print(f"  ! empty buoy roster at {roster_path}")
        return

    _assign_closest_buoys(spots, roster)

    # One request each: every station's 3-day obs series (+ latest Hs/Tp/Dp)
    # and every station's latest SST. Either failing degrades that field to
    # absent — never the whole build.
    cdip_roster = _by_source(roster, SRC_CDIP)
    ndbc_roster = _by_source(roster, SRC_NDBC)
    try:
        obs = _fetch_observations(cdip_roster, start_utc)
    except Exception as err:
        obs = {}
        print(f"  ! observations fetch failed: {err}")
    try:
        sst = _fetch_latest_sst(cdip_roster, now - timedelta(days=2))
    except Exception as err:
        sst = {}
        print(f"  ! SST fetch failed: {err}")
    # NDBC stations are independent per request, so a failure there can only
    # cost those stations — never the CDIP ones.
    try:
        obs.update(_fetch_ndbc_observations(ndbc_roster, start_utc))
    except Exception as err:
        print(f"  ! NDBC observations fetch failed: {err}")

    # Per-run swell-aware pick among FRESH buoys: the "Closest buoy" button
    # opens the nearest station toward today's swell source, not just the
    # nearest. No obs (fetch failed or station silent) -> CSV fallback.
    fresh = {sid: {**roster[sid], **obs[sid]} for sid in obs}
    _patch_spots_json(api_dir, _pick_swell_buoys(spots, fresh, api_dir))

    _write_json(os.path.join(api_dir, 'buoys.json'), [
        {
            'id': sid,
            'name': e['name'],
            'latitude': e['latitude'],
            'longitude': e['longitude'],
            **({} if sid in obs else {'stale': True}),
        }
        for sid, e in sorted(roster.items())
    ])
    print(f"  ✓ buoys.json: {len(roster)} roster buoys "
          f"({len(obs)} reporting)")

    # 1 request per model with a new run upstream; the rest carry forward
    models = _fetch_models(roster, start_utc, end_utc, skipped_sources)
    carried_ids = {s.split(':', 1)[1] for s in skipped_sources}

    # The only remaining per-buoy requests: the two MOP THREDDS reads.
    # Independent per buoy -> a small pool; failures are per-buoy and logged.
    def fetch_mop(sid):
        try:
            return sid, _fetch_mop_forecast(sid, start_utc), None
        except Exception as err:
            return sid, [], err

    # MOP is a CDIP product: NDBC stations have no transect and simply carry
    # the global models as their forecast.
    with ThreadPoolExecutor(max_workers=MOP_WORKERS) as pool:
        mop_results = list(pool.map(fetch_mop, sorted(cdip_roster)))
    mop_results += [(sid, [], None) for sid in sorted(ndbc_roster)]

    for sid, mop, mop_err in mop_results:
        e = roster[sid]
        if mop_err:
            print(f"    ! buoy {sid} MOP forecast: {mop_err}")
        path = os.path.join(api_dir, 'buoys', f'{sid}.json')

        # Skipped models keep the series the previous file already has
        # (built from the same upstream run the skip detected).
        carried = []
        if carried_ids:
            try:
                with open(path) as f:
                    carried = [m for m in json.load(f).get('models', [])
                               if m['id'] in carried_ids]
            except Exception:
                pass  # no previous file — that model sits this run out

        models_out = []
        if mop:
            models_out.append({'id': 'mop', 'label': 'CDIP MOP forecast',
                               'series': _series_json(mop)})
        for model_id, label, series in models.get(sid, []):
            models_out.append(
                {'id': model_id, 'label': label,
                 'series': _series_json(series)})
        models_out.extend(carried)

        latest = (obs.get(sid) or {}).get('latest')
        if latest and sid in sst:
            latest['sst'] = sst[sid]
        observed = (obs.get(sid) or {}).get('observed', [])

        _write_json(path, {
            'id': sid,
            'label': f"{'NDBC' if e['source'] == SRC_NDBC else 'CDIP'} "
                     f"{sid} — {e['name']}",
            'latitude': e['latitude'],
            'longitude': e['longitude'],
            'unit': 'm',
            'latest': latest,
            'observed': _series_json(observed),
            'models': models_out,
        })
        print(f"  ✓ buoys/{sid}.json: {len(observed)} obs, "
              f"{len(models_out)} forecast series"
              f"{', latest obs' if latest else ''}")


def refresh_observations(api_dir, roster_path=ROSTER_FILE):
    """Patch ONLY the live-observation fields into the already-built files:
    observed + latest in each buoys/{id}.json, stale flags in buoys.json.
    Forecast series (MOP + models) are left exactly as the last full build
    wrote them.

    This is the 30-minute job: CDIP buoys report every half hour and the
    sample lands in ERDDAP ~25-35 min after measurement, so a full 6h
    pipeline run leaves most of the day's observations unpublished. Two
    ERDDAP requests, no THREDDS, no Open-Meteo. Raises if the files or the
    fetch are missing/broken — the caller (CI) should then skip the deploy
    and leave the published snapshot alone.
    """
    now = datetime.now(timezone.utc)
    roster = load_roster(roster_path)
    start_utc = now - timedelta(days=PAST_DAYS)
    obs = _fetch_observations(_by_source(roster, SRC_CDIP), start_utc)
    try:
        sst = _fetch_latest_sst(_by_source(roster, SRC_CDIP),
                                now - timedelta(days=2))
    except Exception as err:
        sst = {}
        print(f"! SST fetch failed (continuing without): {err}")
    # NDBC carries its own SST inline, so it needs no companion request. A
    # failure here leaves those stations stale without touching CDIP's.
    try:
        obs.update(_fetch_ndbc_observations(
            _by_source(roster, SRC_NDBC), start_utc))
    except Exception as err:
        print(f"! NDBC observations fetch failed (continuing without): {err}")

    index_path = os.path.join(api_dir, 'buoys.json')
    with open(index_path) as f:
        index = json.load(f)
    for e in index:
        if e['id'] in obs:
            e.pop('stale', None)
        else:
            e['stale'] = True
    _write_json(index_path, index)

    patched = 0
    for sid in roster:
        path = os.path.join(api_dir, 'buoys', f'{sid}.json')
        try:
            with open(path) as f:
                detail = json.load(f)
        except FileNotFoundError:
            continue  # not in the last full build; nothing to patch
        latest = (obs.get(sid) or {}).get('latest')
        if latest and sid in sst:
            latest['sst'] = sst[sid]
        detail['latest'] = latest
        detail['observed'] = _series_json(
            (obs.get(sid) or {}).get('observed', []))
        _write_json(path, detail)
        patched += 1

    if not patched:
        raise RuntimeError('no buoy detail files found to patch')
    print(f"✓ observations refreshed: {patched} buoys patched, "
          f"{len(obs)} reporting")
    # Roster stations with no detail file yet (newly added): this job only
    # patches, so they stay absent until the next full build creates them.
    missing = len(roster) - patched
    if missing:
        print(f"  note: {missing} roster buoys have no detail file yet — "
              "they appear after the next full build")


if __name__ == '__main__':
    api_dir = os.path.join(BASE_DIR, '..', 'api')
    if '--refresh-roster' in sys.argv:
        refresh_roster()
    elif '--refresh-obs' in sys.argv:
        refresh_observations(api_dir)
    else:
        sys.exit('usage: python backend/buoys.py '
                 '--refresh-roster | --refresh-obs')
