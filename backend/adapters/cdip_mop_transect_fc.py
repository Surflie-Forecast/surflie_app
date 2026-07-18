"""CDIP MOP alongshore-transect forecast (Hs + peak period/direction).

The WW3-driven forecast at the spot's matched ~100 m MOP transect
(`MOP_alongshore/{transect}_forecast.nc`): finer in space and longer
(3-hourly, ~7-10 days) than the 1-km grid product, which stays in the
pipeline as fallback for spots that fail transect selection.

Like the grid file, the transect forecast is a pure forward forecast that
drops elapsed timesteps, so each run's points merge with a small per-spot
cache (data/mop_cache/<slug>_transect_fc.json) to keep the overlay's first
day full — same scheme as cdip_mop, see its module docstring.

Non-critical: spots with no transect within ~1.5 km (outside CA, inland,
wrong coastline face) yield no rows, and any live failure serves the cache.
"""
import os

from mop_transects import transect_for
from . import _cdip_transect as tr

NAME = 'cdip-mop-transect'
CRITICAL = False
SOURCE = 'cdip:mop-transect-fc'


def _cache_file(spot):
    return os.path.join(tr.CACHE_DIR, f'{spot.slug}_transect_fc.json')


def fetch(spot, start_utc, end_utc):
    transect = transect_for(spot)
    if transect is None:
        print(f"    {NAME}: no MOP transect within ~1.5 km of {spot.slug}")
        return []

    cached = tr.load_cache(_cache_file(spot), start_utc, end_utc)
    try:
        live = tr.fetch_points(transect, 'forecast', start_utc, end_utc)
    except Exception as e:
        print(f"    {NAME}: live fetch failed ({e}); "
              f"serving {len(cached)} cached points")
        return tr.rows(spot, SOURCE, cached)

    merged = dict(cached)
    merged.update(live)  # live wins per timestamp
    if merged:
        tr.save_cache(_cache_file(spot), merged)
    return tr.rows(spot, SOURCE, merged)
