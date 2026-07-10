"""Buoy comparison files: the notebook's apples-to-apples panel as static JSON.

Reproduces "Apples-to-apples at the buoys" from
data_exploration/model_exploration_devereaux.ipynb: per buoy, its observed Hs
(CDIP ERDDAP), CDIP's MOP forecast at that exact site (THREDDS
MOP_validation, ~8-day horizon), and the global wave models re-fetched at the
buoy's coordinates. Left of "now", every line is checkable against reality.

Outputs:

    api/buoys.json          all active California buoys (id, name, position)
    api/buoys/{id}.json     observed + MOP + per-model Hs for DETAIL_BUOYS

One ERDDAP request covers every station's last PAST_DAYS of observations and
positions; "active" means an observation within the last ACTIVE_WITHIN_H
hours, "California" is taken from the station name (', CA '). Detail files
are only built for DETAIL_BUOYS for now — each one costs a THREDDS read plus
one Open-Meteo call per model.

This runs outside the store: buoys aren't spots, and the panel needs past
observations the store's forecast window never holds. Non-critical — the
pipeline logs and moves on if any of it fails.
"""
import csv
import os
import re
from datetime import datetime, timedelta, timezone
from io import StringIO
from zoneinfo import ZoneInfo

import requests

from adapters import _openmeteo, openmeteo_marine
from builders import WAVE_MODELS, _write_json
from spots import Spot

PAST_DAYS = 3            # observation window shown left of "now"
# Fetch each model to its full native horizon: shorter models (MFWAM ~10,
# GWAM ~8, MOP ~8) simply end sooner and the frontend draws each to wherever
# its data runs out. Open-Meteo marine caps the request at today_UTC + 15
# (an end_date beyond that 400s), which is also GFS Wave's practical reach —
# so 15 requests the widest horizon the API allows without being clipped.
FORECAST_DAYS = 15
ACTIVE_WITHIN_H = 24     # a buoy is active if it reported within this window
DETAIL_BUOYS = ('071',)  # buoys that get a full comparison file (071 = Harvest)

ERDDAP = 'https://erddap.cdip.ucsd.edu/erddap/tabledap/wave_agg.csv'
MOP_URL = ('https://thredds.cdip.ucsd.edu/thredds/dodsC/'
           'cdip/model/MOP_validation/BP{sid}_forecast.nc')
FILL_CUTOFF = -100.0     # values below this are the -999.99 fill
TZ = ZoneInfo('America/Los_Angeles')  # CA buoys only, so one timezone


def _fmt_local(ts_utc):
    return ts_utc.astimezone(TZ).strftime('%Y-%m-%d %H:%M:%S')


def _pretty_name(meta_name):
    """'HARVEST, CA BUOY - 071p1' -> 'Harvest'."""
    return meta_name.split(' BUOY')[0].rsplit(', CA', 1)[0].strip().title()


def _fetch_observations(start_utc):
    """{station_id: {'name', 'latitude', 'longitude', 'observed': [(ts, hs)]}}
    for every active California buoy, from a single ERDDAP request."""
    query = ('?station_id,metaStationName,time,latitude,longitude,waveHs'
             f"&time>={start_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    r = requests.get(ERDDAP + query, timeout=90)
    r.raise_for_status()

    reader = csv.DictReader(StringIO(r.text))
    next(reader)  # row 1 = units
    buoys = {}
    for row in reader:
        sid, name = row['station_id'], row['metaStationName']
        if not sid or ', CA ' not in name:
            continue
        entry = buoys.setdefault(sid, {
            'name': _pretty_name(name), 'observed': []})
        entry['latitude'] = float(row['latitude'])
        entry['longitude'] = float(row['longitude'])
        if row['waveHs']:
            ts = datetime.fromisoformat(row['time'].replace('Z', '+00:00'))
            entry['observed'].append((ts, float(row['waveHs'])))

    cutoff = datetime.now(timezone.utc) - timedelta(hours=ACTIVE_WITHIN_H)
    return {
        sid: e for sid, e in buoys.items()
        if e['observed'] and max(ts for ts, _ in e['observed']) >= cutoff}


def _fetch_mop_forecast(sid, start_utc):
    """[(ts, hs)] from the buoy's MOP validation forecast, sliced from
    start_utc. Same plain-ascii OPeNDAP subsetting as adapters/cdip_mop.py,
    but the file is a 1-D series at the buoy site — no spatial search."""
    base = MOP_URL.format(sid=sid)

    dds = requests.get(f'{base}.dds', timeout=90).text
    n = int(re.search(r'waveTime = (\d+)', dds).group(1))

    def ascii_body(constraint):
        r = requests.get(f'{base}.ascii?{constraint}', timeout=90)
        r.raise_for_status()
        return re.split(r'^-{20,}$', r.text, maxsplit=1, flags=re.M)[1]

    def values(body, name, count):
        return [float(v) for v in
                body.split(f'{name}[{count}]')[1].strip().splitlines()[0].split(',')]

    # Regular hourly axis: origin + step give the slice start directly (the
    # actual timestamps come back with the data, so a mild irregularity only
    # shifts where the slice begins, never what the points mean).
    t0, t1 = values(ascii_body('waveTime%5B0:1:1%5D'), 'waveTime', 2)
    step = t1 - t0
    i0 = max(0, min(n - 1, int((start_utc.timestamp() - t0) // step)))

    body = ascii_body(f'waveTime%5B{i0}:1:{n - 1}%5D,waveHs%5B{i0}:1:{n - 1}%5D')
    times = values(body, 'waveTime', n - i0)
    hs = values(body, 'waveHs', n - i0)
    return [
        (datetime.fromtimestamp(int(t), tz=timezone.utc), v)
        for t, v in zip(times, hs)
        if v >= FILL_CUTOFF
        and datetime.fromtimestamp(int(t), tz=timezone.utc) >= start_utc]


def _fetch_models_at(sid, latitude, longitude, start_utc, end_utc):
    """Per-model Hs series at the buoy's coordinates via Open-Meteo.

    Open-Meteo 400s if end_date runs past its forecast horizon (~today_UTC +
    15). FORECAST_DAYS sits at that edge, so a local/UTC date skew can nudge
    the request one day over — retry with a day trimmed off before giving up.
    """
    buoy_spot = Spot(name=f'buoy {sid}', slug=f'buoy-{sid}', region='',
                     latitude=latitude, longitude=longitude, timezone=str(TZ))

    def fetch(model, source, end):
        return _openmeteo.fetch_hourly_rows(
            'https://marine-api.open-meteo.com/v1/marine', buoy_spot,
            {'wave_height': 'wave_height'}, source, start_utc, end,
            extra_params={'models': model, 'cell_selection': 'sea'})

    out = []
    for model, source in openmeteo_marine.MODELS:
        try:
            rows = fetch(model, source, end_utc)
        except Exception:
            try:
                rows = fetch(model, source, end_utc - timedelta(days=1))
            except Exception as e:
                print(f"    ! buoy {sid} / {model}: {e}")
                continue
        series = sorted(
            (datetime.fromisoformat(r['ts_utc']), r['value']) for r in rows)
        if series and max(v for _, v in series) > 0.01:
            out.append((source.split(':', 1)[1], WAVE_MODELS[source], series))
    return out


def _series_json(series):
    return [{'t': _fmt_local(ts), 'v': round(v, 2)} for ts, v in series]


def build_buoys(api_dir):
    now = datetime.now(timezone.utc)
    start_utc = now - timedelta(days=PAST_DAYS)
    end_utc = now + timedelta(days=FORECAST_DAYS)

    buoys = _fetch_observations(start_utc)
    _write_json(os.path.join(api_dir, 'buoys.json'), [
        {
            'id': sid,
            'name': e['name'],
            'latitude': e['latitude'],
            'longitude': e['longitude'],
        }
        for sid, e in sorted(buoys.items())
    ])
    print(f"  ✓ buoys.json: {len(buoys)} active CA buoys")

    for sid in DETAIL_BUOYS:
        if sid not in buoys:
            print(f"  ! buoy {sid} not active; skipping detail file")
            continue
        e = buoys[sid]

        models_out = []
        try:
            mop = _fetch_mop_forecast(sid, start_utc)
            if mop:
                models_out.append(
                    {'id': 'mop', 'label': 'CDIP MOP forecast',
                     'series': _series_json(mop)})
        except Exception as err:
            print(f"    ! buoy {sid} MOP forecast: {err}")

        for model_id, label, series in _fetch_models_at(
                sid, e['latitude'], e['longitude'], start_utc, end_utc):
            models_out.append(
                {'id': model_id, 'label': label,
                 'series': _series_json(series)})

        _write_json(os.path.join(api_dir, 'buoys', f'{sid}.json'), {
            'id': sid,
            'label': f"CDIP {sid} — {e['name']}",
            'latitude': e['latitude'],
            'longitude': e['longitude'],
            'unit': 'm',
            'observed': _series_json(e['observed']),
            'models': models_out,
        })
        print(f"  ✓ buoys/{sid}.json: {len(e['observed'])} obs, "
              f"{len(models_out)} forecast series")
