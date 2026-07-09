"""CDIP MOP 1 km California nearshore forecast (combined Hs only).

The only source that models the water inside the Channel Islands' swell
shadow (post-refraction, ~1 km); global models systematically over-predict
there (data_exploration/model_exploration_devereaux.ipynb). Short horizon:
~4 days, 6-hourly — it feeds the model-comparison overlay, not the displayed
forecast.

Data comes from the THREDDS OPeNDAP endpoint as plain-text (.ascii) subset
requests, keeping the pipeline stdlib + requests: two tiny axis/shape reads,
then one subgrid read around the spot from which the nearest water cell is
picked (land cells hold the -999.99 fill value).

Coverage is California only (32-42N); spots outside the grid yield no rows,
which is fine — the source is non-critical.

Unlike Open-Meteo, the MOP forecast file is a pure forward forecast: it drops
timesteps that are now in the past. So the already-elapsed early hours of the
current day disappear once the file rolls forward, leaving the overlay's first
day short. To keep a full first day we persist each run's rows to a small
per-spot cache (data/mop_cache/<slug>.json — gitignored; CI restores it
between runs via actions/cache) and merge: live rows win for shared
timestamps, cached rows backfill the gap, and anything before the window
start is pruned so the cache only ever holds the current window.
"""
import json
import math
import os
import re
from datetime import datetime, timezone

import requests

NAME = 'cdip-mop'
CRITICAL = False
SOURCE = 'cdip:mop'

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data', 'mop_cache')

BASE = ('https://thredds.cdip.ucsd.edu/thredds/dodsC/'
        'cdip/model/MOP_grids/CA_0.01_forecast.nc')
FILL_CUTOFF = -100.0   # values below this are the -999.99 land fill
HALF = 6               # subgrid half-width (cells) searched for water
MAX_DIST_DEG = 0.05    # give up if the nearest water cell is > ~5 km away


def _get(url):
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    return r.text


def _ascii_body(constraint):
    text = _get(f'{BASE}.ascii?{constraint}')
    return re.split(r'^-{20,}$', text, maxsplit=1, flags=re.M)[1]


def _axis(name):
    """(origin, step) of a 1-D coordinate axis."""
    body = _ascii_body(f'{name}%5B0:1:1%5D')
    values = [float(v) for v in
              body.split(f'{name}[2]')[1].strip().splitlines()[0].split(',')]
    return values[0], values[1] - values[0]


def _shape():
    """(n_time, n_lat, n_lon) from the dataset descriptor."""
    dds = _get(f'{BASE}.dds')
    dims = {m.group(1): int(m.group(2)) for m in
            re.finditer(r'(\w+) = (\d+)', dds)}
    return dims['waveTime'], dims['metaLatitude'], dims['metaLongitude']


def _cache_path(spot):
    return os.path.join(CACHE_DIR, f'{spot.slug}.json')


def _load_cache(spot, start_utc, end_utc):
    """Cached rows still inside the window, keyed by ts_utc. {} if none/bad."""
    try:
        with open(_cache_path(spot)) as f:
            cached = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for ts_utc, value in cached.items():
        ts = datetime.fromisoformat(ts_utc)
        if start_utc <= ts <= end_utc:
            out[ts_utc] = value
    return out


def _save_cache(spot, rows):
    """Persist rows as {ts_utc: value}, pruned to the window by the caller."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    data = {r['ts_utc']: r['value'] for r in rows}
    with open(_cache_path(spot), 'w') as f:
        json.dump(data, f, separators=(',', ':'), sort_keys=True)


def _row(spot, ts_utc, value):
    return {
        'spot': spot.slug,
        'ts_utc': ts_utc,
        'variable': 'wave_height',
        'value': value,
        'source': SOURCE,
    }


def fetch(spot, start_utc, end_utc):
    """Live MOP rows merged with the cached window so the first day stays full.

    Live values win for shared timestamps; cached-only timestamps (the elapsed
    hours MOP has since dropped) backfill the gap. On a total live failure we
    still serve the cache — better a slightly stale first day than none — and
    the cache is left untouched. Non-critical, so we never raise.
    """
    cached = _load_cache(spot, start_utc, end_utc)
    try:
        live = _fetch_live(spot, start_utc, end_utc)
    except Exception as e:
        print(f"    {NAME}: live fetch failed ({e}); serving {len(cached)} cached rows")
        return [_row(spot, ts, v) for ts, v in cached.items()]

    merged = dict(cached)
    for r in live:
        merged[r['ts_utc']] = r['value']  # live wins

    rows = [_row(spot, ts, v) for ts, v in merged.items()]
    if rows:  # don't litter empty caches for spots outside the CA grid
        _save_cache(spot, rows)
    return rows


def _fetch_live(spot, start_utc, end_utc):
    n_time, n_lat, n_lon = _shape()
    lat0, dlat = _axis('metaLatitude')
    lon0, dlon = _axis('metaLongitude')

    li = round((spot.latitude - lat0) / dlat)
    lj = round((spot.longitude - lon0) / dlon)
    if not (0 <= li < n_lat and 0 <= lj < n_lon):
        print(f"    {NAME}: {spot.slug} outside the California MOP grid")
        return []

    ra, rb = max(li - HALF, 0), min(li + HALF, n_lat - 1)
    ca, cb = max(lj - HALF, 0), min(lj + HALF, n_lon - 1)
    body = _ascii_body(
        f'waveTime%5B0:1:{n_time - 1}%5D,'
        f'waveHs.waveHs%5B0:1:{n_time - 1}%5D%5B{ra}:1:{rb}%5D%5B{ca}:1:{cb}%5D')

    times = [
        datetime.fromtimestamp(int(v), tz=timezone.utc)
        for v in body.split(f'waveTime[{n_time}]')[1]
        .strip().splitlines()[0].split(',')]

    # hs[t][r][c] from lines like "[0][3], 0.55, 0.58, -999.99, ..."
    nr, nc = rb - ra + 1, cb - ca + 1
    hs = [[[FILL_CUTOFF - 1] * nc for _ in range(nr)] for _ in range(n_time)]
    for m in re.finditer(r'^\[(\d+)\]\[(\d+)\],(.+)$', body, flags=re.M):
        t, r = int(m.group(1)), int(m.group(2))
        hs[t][r] = [float(v) for v in m.group(3).split(',')]

    # nearest water cell (validity judged at the first time step)
    coslat = math.cos(math.radians(spot.latitude))
    best, best_d2 = None, None
    for r in range(nr):
        for c in range(nc):
            if hs[0][r][c] < FILL_CUTOFF:
                continue
            d2 = ((lat0 + (ra + r) * dlat - spot.latitude) ** 2
                  + (coslat * (lon0 + (ca + c) * dlon - spot.longitude)) ** 2)
            if best_d2 is None or d2 < best_d2:
                best, best_d2 = (r, c), d2
    if best is None or best_d2 > MAX_DIST_DEG ** 2:
        print(f"    {NAME}: no water cell within ~5 km of {spot.slug}")
        return []

    r, c = best
    return [
        _row(spot, ts.isoformat(), hs[t][r][c])
        for t, ts in enumerate(times)
        if hs[t][r][c] >= FILL_CUTOFF and start_utc <= ts <= end_utc
    ]
