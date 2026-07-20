"""Tide predictions from NOAA CO-OPS, cached PER STATION, with an
Open-Meteo fallback.

Tide predictions are deterministic harmonics — they never change between
runs — and many spots share the same nearest station (277 CA spots resolve
to a couple dozen stations), so both the fetch and the rolling cache are
keyed by STATION (data/tide_cache/station_<id>.json, gitignored; CI
persists it via actions/cache), covering exactly the app's display window.
Each unique station is fetched at most once per run and its rows fan out
to every spot assigned to it; a run whose window is fully covered makes
zero API calls. Spot->station assignments persist in
data/tide_cache/stations_map.json so a spot never silently changes datum;
a station failing reassigns its spots to their next-nearest station.

From each station we fetch two products:

  - 6-minute resolution predictions -> variable "tide_height"
  - high/low peaks                  -> variables "tide_high" / "tide_low"

If NOAA can't serve the missing span (the CO-OPS predictions product has
been unreliable since its Jul 2026 cloud migration), a non-empty cache is
served as-is — better a day short at the tail than blank cards. Only with
an empty cache do we fall back to Open-Meteo Marine's hourly
sea_level_height_msl: the hourly curve becomes tide_height and highs/lows
are local extrema refined by parabolic interpolation. Fallback heights are
relative to MSL rather than NOAA's MLLW datum (lows go negative) and are
never cached, so NOAA takes over again the moment it recovers.

All timestamps are requested in GMT and stored as UTC; builders convert to
each spot's local timezone.
"""
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from . import _openmeteo

NAME = 'noaa-tides'
CRITICAL = False  # the app degrades gracefully without tide data

STATIONS_URL = ('https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/'
                'stations.json?type=tidepredictions')
PREDICTIONS_URL = 'https://api.tidesandcurrents.noaa.gov/api/prod/datagetter'
HEADERS = {'User-Agent': 'surf-forecast-pipeline (github.com/pcarbomestre)'}

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data', 'tide_cache')

MARINE_URL = 'https://marine-api.open-meteo.com/v1/marine'
FALLBACK_SOURCE = 'openmeteo:sealevel'

_stations_cache = None


def _haversine_miles(lat1, lon1, lat2, lon2):
    r = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _nearest_stations(lat, lon, limit=3):
    global _stations_cache
    if _stations_cache is None:
        resp = requests.get(STATIONS_URL, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        # The tidepredictions list mixes harbor/reference stations with
        # subordinate ones (type 'S') that 403 on the datagetter predictions
        # product. Excluding subordinates keeps every candidate a station
        # that can actually answer — the ~18 doomed fetches per run (each a
        # 403) were most of the tide stage's wall time at 276 spots.
        _stations_cache = [
            s for s in resp.json().get('stations', [])
            if 'lat' in s and 'lng' in s and s.get('type') != 'S'
        ]
    ranked = sorted(
        _stations_cache,
        key=lambda s: _haversine_miles(lat, lon, float(s['lat']), float(s['lng'])))
    return ranked[:limit]


def _fetch_predictions(station_id, start_utc, end_utc, interval=None,
                       attempts=3, backoff=2):
    params = {
        'product': 'predictions',
        'station': station_id,
        'begin_date': start_utc.strftime('%Y%m%d'),
        'end_date': end_utc.strftime('%Y%m%d'),
        'datum': 'MLLW',
        'time_zone': 'gmt',
        'units': 'metric',
        'format': 'json',
    }
    if interval:
        params['interval'] = interval
    # The predictions endpoint intermittently returns "No Predictions data
    # was found" for stations that do have data (observed Jul 2026); the
    # same request succeeds on retry, so treat an empty body as transient.
    # A 4xx (e.g. 403 for subordinate stations that don't serve this
    # product) is permanent — raise immediately instead of retrying with
    # backoff. Empty bodies stay transient (retried).
    for attempt in range(attempts):
        if attempt:
            time.sleep(backoff * attempt)
        resp = requests.get(PREDICTIONS_URL, params=params, headers=HEADERS, timeout=60)
        if 400 <= resp.status_code < 500:
            resp.raise_for_status()
        resp.raise_for_status()
        predictions = resp.json().get('predictions')
        if predictions:
            return predictions
    return []


def _points(predictions, start_utc, end_utc, variable=None):
    """Normalize raw API predictions to cache points within the window."""
    points = []
    for p in predictions:
        try:
            ts = datetime.strptime(p['t'], '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
            value = float(p['v'])
        except (KeyError, ValueError):
            continue
        if not (start_utc <= ts <= end_utc):
            continue
        var = variable
        if var is None:  # peaks carry their own type: H/HH -> high, L/LL -> low
            kind = p.get('type', '')
            var = 'tide_high' if kind.startswith('H') else 'tide_low'
        points.append({'t': ts.isoformat(), 'var': var, 'v': value})
    return points


def _fetch_station_points(station_id, start_utc, end_utc):
    """Both products for one station and span, or [] if the curve is empty."""
    raw = _fetch_predictions(station_id, start_utc, end_utc)
    if not raw:
        return []
    points = _points(raw, start_utc, end_utc, variable='tide_height')
    peaks = _fetch_predictions(station_id, start_utc, end_utc, interval='hilo')
    points += _points(peaks, start_utc, end_utc)
    return points


# --- rolling per-station cache ---------------------------------------------

def _cache_path(station_id):
    return os.path.join(CACHE_DIR, f'station_{station_id}.json')


def _load_cache(station_id):
    try:
        with open(_cache_path(station_id)) as f:
            cache = json.load(f)
        if cache.get('station') and isinstance(cache.get('points'), list):
            return cache
    except Exception:
        pass
    return {'station': station_id, 'station_name': '', 'covered_through': '',
            'points': []}


def _save_cache(cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(cache['station']), 'w') as f:
        json.dump(cache, f)


MAP_FILE = os.path.join(CACHE_DIR, 'stations_map.json')


def _load_assignments():
    try:
        with open(MAP_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_assignments(amap):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(MAP_FILE, 'w') as f:
        json.dump(amap, f, sort_keys=True)


def _migrate_slug_caches():
    """One-time: fold pre-2026-07-19 per-spot caches (<slug>.json) into
    station caches + assignments — newest cache per station wins, the old
    files are removed so they don't live in the actions cache forever."""
    try:
        names = os.listdir(CACHE_DIR)
    except OSError:
        return
    amap = _load_assignments()
    changed = False
    for name in names:
        if not name.endswith('.json') or name.startswith('station_') \
                or name == os.path.basename(MAP_FILE):
            continue
        path = os.path.join(CACHE_DIR, name)
        try:
            with open(path) as f:
                old = json.load(f)
            station = old.get('station')
            if station and isinstance(old.get('points'), list):
                cur = _load_cache(station)
                if old.get('covered_through', '') >= cur.get('covered_through', ''):
                    _save_cache(old)
                amap.setdefault(name[:-5], station)
                changed = True
            os.remove(path)
        except Exception:
            continue
    if changed:
        _save_assignments(amap)


def _merge_points(cache, new_points):
    """Merge by (variable, timestamp); new values win on overlap."""
    merged = {(p['var'], p['t']): p for p in cache['points']}
    merged.update({(p['var'], p['t']): p for p in new_points})
    cache['points'] = [merged[k] for k in sorted(merged)]


def _cache_rows(spot, cache):
    source = f"noaa:{cache['station']}"
    return [{
        'spot': spot.slug,
        'ts_utc': p['t'],
        'variable': p['var'],
        'value': p['v'],
        'source': source,
    } for p in cache['points']]


# --- Open-Meteo fallback --------------------------------------------------

def _fallback_rows(spot, start_utc, end_utc):
    """Derive tide_height + tide_high/tide_low from Open-Meteo sea level.

    The hourly sea_level_height_msl series is stored as tide_height; peaks
    are interior local extrema, with time and height refined by fitting a
    parabola through the extremum and its two neighbours.
    """
    rows, _ = _openmeteo.fetch_hourly_rows_batch(
        MARINE_URL, [(spot.slug, spot.latitude, spot.longitude)],
        {'sea_level_height_msl': 'tide_height'}, FALLBACK_SOURCE,
        start_utc, end_utc, extra_params={})
    series = sorted(
        (datetime.fromisoformat(r['ts_utc']), r['value']) for r in rows)
    for i in range(1, len(series) - 1):
        (_, prev), (ts, v), (_, nxt) = series[i - 1], series[i], series[i + 1]
        # Strict on the left so a flat pair of equal values yields one peak.
        is_max = v > prev and v >= nxt
        is_min = v < prev and v <= nxt
        if not (is_max or is_min):
            continue
        denom = prev - 2 * v + nxt
        offset = 0.5 * (prev - nxt) / denom if denom else 0.0
        rows.append({
            'spot': spot.slug,
            'ts_utc': (ts + timedelta(hours=offset)).isoformat(),
            'variable': 'tide_high' if is_max else 'tide_low',
            'value': v - 0.25 * (prev - nxt) * offset,
            'source': FALLBACK_SOURCE,
        })
    return rows


# --- entry point ----------------------------------------------------------

def _station_window(station_id, start_utc, end_utc):
    """The station's cache covering the window, topped up if needed.

    Returns the cache dict (possibly partial — better a day short at the
    tail than blank cards) or None when the station has nothing at all.
    """
    cache = _load_cache(station_id)

    # Days that scrolled out of the display window are gone for good.
    cutoff = start_utc.isoformat()
    cache['points'] = [p for p in cache['points'] if p['t'] >= cutoff]

    end_date = end_utc.date().isoformat()
    if cache['points'] and cache['covered_through'] >= end_date:
        _save_cache(cache)  # persist the prune
        print(f"    tide cache covers window (station {cache['station_name']} "
              f"{station_id}); no fetch")
        return cache

    # Top up (or seed) — the missing span only, so the whole series keeps
    # one station's datum.
    try:
        if cache['points'] and cache['covered_through']:
            covered = datetime.fromisoformat(cache['covered_through']).replace(
                tzinfo=timezone.utc)
            missing_start = max(start_utc, covered)
        else:
            missing_start = start_utc
        points = _fetch_station_points(station_id, missing_start, end_utc)
    except Exception as e:
        print(f"    ! station {station_id} fetch failed ({e})")
        points = []
    if points:
        _merge_points(cache, points)
        cache['covered_through'] = end_date
        _save_cache(cache)
        print(f"    tide cache through {end_date} (station "
              f"{cache['station_name'] or station_id})")
        return cache

    if cache['points']:
        print(f"    ! station {station_id} top-up failed; serving cache "
              f"through {cache['covered_through']}")
        return cache
    return None


def fetch_batch(spots, start_utc, end_utc):
    """Each unique station fetched once (threaded), rows fanned out to its
    spots. Spots whose assigned station fails move to their next-nearest
    (up to 3 tries), then to the per-spot Open-Meteo sea-level fallback."""
    _migrate_slug_caches()
    amap = _load_assignments()

    rows = []
    pending = list(spots)
    tried = {spot.slug: set() for spot in spots}
    for _ in range(3):
        groups = {}
        for spot in pending:
            sid = amap.get(spot.slug)
            if sid is None or sid in tried[spot.slug]:
                sid = next(
                    (s['id'] for s in
                     _nearest_stations(spot.latitude, spot.longitude)
                     if s['id'] not in tried[spot.slug]), None)
            if sid is None:
                continue  # all candidates tried; falls through to fallback
            tried[spot.slug].add(sid)
            groups.setdefault(sid, []).append(spot)
        if not groups:
            break

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = dict(zip(groups, pool.map(
                lambda sid: _station_window(sid, start_utc, end_utc),
                groups)))

        pending = []
        for sid, sp_list in groups.items():
            cache = results[sid]
            if cache is not None and cache['points']:
                for spot in sp_list:
                    amap[spot.slug] = sid
                    rows.extend(_cache_rows(spot, cache))
            else:
                pending.extend(sp_list)
        if not pending:
            break

    _save_assignments(amap)

    for spot in pending:
        print(f"    ! no NOAA station usable near {spot.name}; falling back "
              f"to Open-Meteo sea level (MSL datum, uncached)")
        try:
            rows.extend(_fallback_rows(spot, start_utc, end_utc))
        except Exception as e:
            print(f"    ! sea-level fallback failed for {spot.slug} ({e})")
    return rows
