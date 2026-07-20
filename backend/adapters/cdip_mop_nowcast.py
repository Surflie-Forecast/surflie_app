"""CDIP MOP alongshore-transect nowcast — observed-driven reference line.

`MOP_alongshore/{transect}_nowcast.nc` is driven by measured buoy spectra:
the best available "what is it actually doing" at a spot with no sensor.
This is not a forecast — it feeds (a) the model-comparison overlay's
observed-driven reference line for the elapsed part of the window and
(b) forecast-skill scoring (backend/skill.py), which compares each
forecast source against these rows at matched timestamps.

The window fetched is the trailing TRAIL_DAYS (default 3) up to now — the
file is a rolling ~15-month archive, hourly, so no cache is needed; past
points never disappear upstream. Rows before the app's display window are
harmless (the overlay clips to its time axis) and are exactly what the
skill job needs.

Non-critical, never raises.
"""
from datetime import datetime, timedelta, timezone

from mop_transects import transect_for
from . import _batch
from . import _cdip_transect as tr

NAME = 'cdip-mop-nowcast'
CRITICAL = False
SOURCE = 'cdip:mop-nowcast'
TRAIL_DAYS = 3


def fetch(spot, start_utc, end_utc):
    transect = transect_for(spot)
    if transect is None:
        print(f"    {NAME}: no MOP transect within ~1.5 km of {spot.slug}")
        return []

    now = datetime.now(timezone.utc)
    lo = min(start_utc, now - timedelta(days=TRAIL_DAYS))
    hi = min(end_utc, now + timedelta(hours=1))
    try:
        points = tr.fetch_points(transect, 'nowcast', lo, hi)
    except Exception as e:
        print(f"    {NAME}: fetch failed ({e})")
        return []
    return tr.rows(spot, SOURCE, points)


def fetch_batch(spots, start_utc, end_utc):
    """Threaded per-spot reads; transect map prewarmed serially."""
    return _batch.pooled_fetch_batch(fetch, NAME, spots, start_utc, end_utc,
                                     prewarm=transect_for)
