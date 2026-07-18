"""Shared reader for CDIP MOP alongshore-transect files (OPeNDAP .ascii).

Both per-transect products (`{transect}_forecast.nc`, `{transect}_nowcast.nc`)
are 1-D time series of bulk wave parameters at a single MOP prediction site,
so unlike the 1-km grid adapter there is no spatial search: read the time
axis descriptor, compute the index slice covering the requested window, and
pull waveHs/waveTp/waveDp over that slice in one request. Kept stdlib +
requests like the rest of the pipeline.
"""
import json
import math
import os
import re
from datetime import datetime, timezone

import requests

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data', 'mop_cache')

BASE = ('https://thredds.cdip.ucsd.edu/thredds/dodsC/'
        'cdip/model/MOP_alongshore/{transect}_{kind}.nc')
FILL_CUTOFF = -100.0   # values below this are the -999.99 fill
SLICE_PAD = 4          # extra steps each side in case the axis is irregular

# THREDDS variable -> (point key, store variable). Same mapping as the grid
# adapter: Tp/Dp land in the swell partition variables.
VARS = {
    'waveHs': ('h', 'wave_height'),
    'waveTp': ('p', 'swell_period'),
    'waveDp': ('d', 'swell_direction'),
}


def _get(url):
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    return r.text


def _ascii_body(base, constraint):
    text = _get(f'{base}.ascii?{constraint}')
    return re.split(r'^-{20,}$', text, maxsplit=1, flags=re.M)[1]


def _values(body, name):
    """The comma-separated values printed under a 1-D variable's header."""
    m = re.search(rf'^{name}\[\d+\]$', body, flags=re.M)
    if m is None:
        return None
    return body[m.end():].strip().splitlines()[0].split(',')


def fetch_points(transect, kind, start_utc, end_utc):
    """{ts_utc_iso: {'h','p','d'}} for one transect file over a window.

    kind is 'forecast' or 'nowcast'. Raises on network/parse errors —
    callers decide how to degrade.
    """
    base = BASE.format(transect=transect, kind=kind)

    n_time = int(re.search(r'waveTime = (\d+)', _get(f'{base}.dds')).group(1))
    head = _values(_ascii_body(base, 'waveTime%5B0:1:1%5D'), 'waveTime')
    origin, step = int(head[0]), int(head[1]) - int(head[0])

    i0 = math.floor((start_utc.timestamp() - origin) / step) - SLICE_PAD
    i1 = math.ceil((end_utc.timestamp() - origin) / step) + SLICE_PAD
    i0, i1 = max(i0, 0), min(i1, n_time - 1)
    if i1 < i0:
        return {}

    sl = f'%5B{i0}:1:{i1}%5D'
    body = _ascii_body(
        base, f'waveTime{sl},' + ','.join(f'{v}{sl}' for v in VARS))

    times = [int(v) for v in _values(body, 'waveTime')]
    series = {}
    for var in VARS:
        vals = _values(body, var)
        series[var] = [float(v) for v in vals] if vals else None
    if series['waveHs'] is None:
        raise ValueError('waveHs missing from response')

    out = {}
    for t, epoch in enumerate(times):
        ts = datetime.fromtimestamp(epoch, tz=timezone.utc)
        if not (start_utc <= ts <= end_utc):
            continue
        if series['waveHs'][t] < FILL_CUTOFF:
            continue
        pt = {}
        for var, (key, _) in VARS.items():
            vals = series[var]
            if vals is not None and vals[t] >= FILL_CUTOFF:
                pt[key] = vals[t]
        if pt:
            out[ts.isoformat()] = pt
    return out


def rows(spot, source, points):
    return [
        {'spot': spot.slug, 'ts_utc': ts_utc, 'variable': var,
         'value': pt[key], 'source': source}
        for ts_utc, pt in points.items()
        for key, var in VARS.values()
        if pt.get(key) is not None
    ]


def load_cache(cache_file, start_utc, end_utc):
    """Cached points still inside the window, {ts_utc: {'h','p','d'}}."""
    try:
        with open(cache_file) as f:
            cached = json.load(f)
    except (OSError, ValueError):
        return {}
    return {
        ts_utc: pt for ts_utc, pt in cached.items()
        if start_utc <= datetime.fromisoformat(ts_utc) <= end_utc
    }


def save_cache(cache_file, points):
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    with open(cache_file, 'w') as f:
        json.dump(points, f, separators=(',', ':'), sort_keys=True)
