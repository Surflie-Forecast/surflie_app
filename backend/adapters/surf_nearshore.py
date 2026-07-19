"""Surflie's own MOP-style nearshore forecast (surflie_salix pipeline).

The private repo pcarbomestre/surflie_salix runs deep-water forecasts
through SWAN-derived transform coefficients at the MOP transects and
publishes one gzipped JSON artifact for all ~1800 Santa Barbara sites every
6 h. One download therefore serves every spot: this adapter fetches the
artifact once per run (fetch_batch) and slices out each spot's matched
transect via the same spot->transect selection as the CDIP transect
adapters.

Each artifact starts at its model cycle, so elapsed hours of the local day
are missing from a fresh download — points merge with a small per-spot
cache (data/mop_cache/<slug>_salix.json) to keep the overlay's first day
full, same scheme as cdip_mop_transect_fc.

Non-critical: on a missing SALIX_TOKEN or any live failure, print and serve
the caches — never raise.
"""
import gzip
import json
import os
from datetime import datetime

import requests

from mop_transects import transect_for
from . import _cdip_transect as tr

NAME = 'surf-nearshore'
CRITICAL = False
SOURCE = 'surflie:nearshore'

URL = ('https://raw.githubusercontent.com/pcarbomestre/surflie_salix/'
       'forecast/out/forecast_latest.json.gz')


def _cache_file(spot):
    return os.path.join(tr.CACHE_DIR, f'{spot.slug}_salix.json')


def _download():
    token = os.environ.get('SALIX_TOKEN', '')
    if not token:
        raise RuntimeError('SALIX_TOKEN not set')
    r = requests.get(URL, headers={'Authorization': f'Bearer {token}'},
                     timeout=90)
    r.raise_for_status()
    raw = r.content
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    return json.loads(raw)


def _site_points(artifact, label, start_utc, end_utc):
    """{ts_utc_iso: {'h','p','d'}} for one site, clipped to the window."""
    site = artifact['sites'].get(label)
    if site is None:
        return {}
    out = {}
    for i, stamp in enumerate(artifact['times']):
        ts = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if not (start_utc <= ts <= end_utc):
            continue
        pt = {}
        for src_key, key in (('hs', 'h'), ('tp', 'p'), ('dp', 'd')):
            vals = site.get(src_key)
            if vals is not None and vals[i] is not None:
                pt[key] = vals[i]
        if pt:
            out[ts.isoformat()] = pt
    return out


def fetch_batch(spots, start_utc, end_utc):
    try:
        artifact = _download()
    except Exception as e:
        print(f"  {NAME}: live fetch failed ({e}); serving caches")
        artifact = None

    rows = []
    for spot in spots:
        transect = transect_for(spot)
        if transect is None:
            continue
        cached = tr.load_cache(_cache_file(spot), start_utc, end_utc)
        if artifact is None:
            rows.extend(tr.rows(spot, SOURCE, cached))
            continue
        merged = dict(cached)
        merged.update(_site_points(artifact, transect, start_utc, end_utc))
        if merged:
            tr.save_cache(_cache_file(spot), merged)
        rows.extend(tr.rows(spot, SOURCE, merged))
    return rows
