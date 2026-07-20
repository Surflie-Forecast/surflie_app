"""CDIP MOP 1 km California nearshore forecast (Hs + peak period/direction).

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


# THREDDS grid variable -> (point key, store variable). Tp/Dp are the peak
# period/direction — the same role the swell partition plays for the global
# models, so they land in the same store variables and feed alarms/UI alike.
GRID_VARS = {
    'waveHs': ('h', 'wave_height'),
    'waveTp': ('p', 'swell_period'),
    'waveDp': ('d', 'swell_direction'),
    # Energy-mean period for the comparison overlay (the grid file has no
    # waveDm, so mean direction is transect/offshore-only).
    'waveTa': ('a', 'wave_period'),
}


def _load_cache(spot, start_utc, end_utc):
    """Cached points still inside the window, {ts_utc: {'h','p','d'}}.
    Pre-Tp/Dp caches stored a bare Hs float per timestamp — normalize."""
    try:
        with open(_cache_path(spot)) as f:
            cached = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for ts_utc, value in cached.items():
        ts = datetime.fromisoformat(ts_utc)
        if start_utc <= ts <= end_utc:
            out[ts_utc] = value if isinstance(value, dict) else {'h': value}
    return out


def _save_cache(spot, points):
    """Persist {ts_utc: point-dict}, pruned to the window by the caller."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(spot), 'w') as f:
        json.dump(points, f, separators=(',', ':'), sort_keys=True)


def _rows(spot, points):
    return [
        {'spot': spot.slug, 'ts_utc': ts_utc, 'variable': var,
         'value': pt[key], 'source': SOURCE}
        for ts_utc, pt in points.items()
        for key, var in GRID_VARS.values()
        if pt.get(key) is not None
    ]


def fetch(spot, start_utc, end_utc):
    """Live MOP points merged with the cached window so the first day stays
    full. Live values win for shared timestamps; cached-only timestamps (the
    elapsed hours MOP has since dropped) backfill the gap. On a total live
    failure we still serve the cache — better a slightly stale first day than
    none — and the cache is left untouched. Non-critical, so we never raise.
    """
    cached = _load_cache(spot, start_utc, end_utc)
    try:
        live = _fetch_live(spot, start_utc, end_utc)
    except Exception as e:
        print(f"    {NAME}: live fetch failed ({e}); serving {len(cached)} cached rows")
        return _rows(spot, cached)

    merged = dict(cached)
    merged.update(live)  # live wins per timestamp

    if merged:  # don't litter empty caches for spots outside the CA grid
        _save_cache(spot, merged)
    return _rows(spot, merged)


def _parse_grid(body, var, n_time, nr, nc):
    """grid[t][r][c] for one variable's section of a multi-grid .ascii body.

    Each grid prints as a '{var}[nt][nr][nc]' header line followed by
    '[t][r], v, v, ...' lines; slice the body to this variable's section so
    the shared line format can't bleed between variables."""
    m = re.search(rf'^{var}\[\d+\]\[\d+\]\[\d+\]$', body, flags=re.M)
    if not m:
        return None
    nxt = re.search(r'^wave\w+\[\d+\]', body[m.end():], flags=re.M)
    section = body[m.start():m.end() + nxt.start()] if nxt else body[m.start():]
    grid = [[[FILL_CUTOFF - 1] * nc for _ in range(nr)] for _ in range(n_time)]
    for mm in re.finditer(r'^\[(\d+)\]\[(\d+)\],(.+)$', section, flags=re.M):
        t, r = int(mm.group(1)), int(mm.group(2))
        grid[t][r] = [float(v) for v in mm.group(3).split(',')]
    return grid


def _fetch_live(spot, start_utc, end_utc):
    """{ts_utc: {'h','p','d'}} at the nearest water cell, one .ascii read."""
    n_time, n_lat, n_lon = _shape()
    lat0, dlat = _axis('metaLatitude')
    lon0, dlon = _axis('metaLongitude')

    li = round((spot.latitude - lat0) / dlat)
    lj = round((spot.longitude - lon0) / dlon)
    if not (0 <= li < n_lat and 0 <= lj < n_lon):
        print(f"    {NAME}: {spot.slug} outside the California MOP grid")
        return {}

    ra, rb = max(li - HALF, 0), min(li + HALF, n_lat - 1)
    ca, cb = max(lj - HALF, 0), min(lj + HALF, n_lon - 1)
    subgrid = f'%5B0:1:{n_time - 1}%5D%5B{ra}:1:{rb}%5D%5B{ca}:1:{cb}%5D'
    body = _ascii_body(
        f'waveTime%5B0:1:{n_time - 1}%5D,'
        + ','.join(f'{v}.{v}{subgrid}' for v in GRID_VARS))

    times = [
        datetime.fromtimestamp(int(v), tz=timezone.utc)
        for v in body.split(f'waveTime[{n_time}]')[1]
        .strip().splitlines()[0].split(',')]

    nr, nc = rb - ra + 1, cb - ca + 1
    grids = {v: _parse_grid(body, v, n_time, nr, nc) for v in GRID_VARS}
    hs = grids['waveHs']
    if hs is None:
        raise ValueError('waveHs grid missing from response')

    # nearest water cell (validity judged at the first time step of Hs)
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
        return {}

    r, c = best
    out = {}
    for t, ts in enumerate(times):
        if not (start_utc <= ts <= end_utc) or hs[t][r][c] < FILL_CUTOFF:
            continue
        pt = {}
        for var, (key, _) in GRID_VARS.items():
            g = grids[var]
            if g is not None and g[t][r][c] >= FILL_CUTOFF:
                pt[key] = g[t][r][c]
        if pt:
            out[ts.isoformat()] = pt
    return out
