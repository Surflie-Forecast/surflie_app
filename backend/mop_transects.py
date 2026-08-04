"""Spot -> CDIP MOP transect selection.

CDIP models the California coast at ~100 m-spaced alongshore transects
("MOP points", e.g. B0161). The vendored table data/mop_transects.csv is a
slim cut of CDIP's static CA_v1.1_transect_definitions.txt: one row per
transect with the backbeach point, the prediction site (~10 m depth), and
the shore normal (deg true, pointing offshore).

Selection for a spot (lat, lon):
  1. nearest transect by prediction-site distance (cos-lat scaled);
  2. reject if > MAX_DIST_KM (2 km): the spot is inland, outside CA, or too
     far from any modeled transect — callers yield no rows, matching the
     non-critical convention of the grid adapter. Raised from 1.5 km on
     2026-08-04: CDIP's transects thin out along the wide straight beaches
     of Santa Cruz and Humboldt, leaving 14 spots (Tolowa Dunes, Capitola,
     Moonstone, Little River …) with a nearest transect at 1.5-2.4 km and
     so no nearshore forecast at all. 2 km recovers 11 of them; the
     remainder sit far enough out that a transect would be a weak claim
     about local conditions;
  3. sanity: the bearing from the transect's backbeach point to the spot
     must sit within +/-90 deg of the shore normal, so a spot never snaps
     across a headland or harbor mouth to the wrong coastline face. Spots
     within BEARING_SKIP_KM of the backbeach skip the check (bearing is
     noise at beach scale). A candidate that fails falls through to the
     next-nearest transect inside the distance cap.

Chosen transects are memoized per spot slug in data/mop_cache/
transect_map.json (spots never move; the cache directory is persisted
across CI runs like the MOP forecast cache).
"""
import csv
import json
import math
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TABLE_FILE = os.path.join(BASE_DIR, '..', 'data', 'mop_transects.csv')
CACHE_FILE = os.path.join(BASE_DIR, '..', 'data', 'mop_cache',
                          'transect_map.json')

MAX_DIST_KM = 2.0
BEARING_SKIP_KM = 0.3
KM_PER_DEG = 111.32

_table = None       # loaded rows: (label, back_lat, back_lon, pred_lat, pred_lon, normal)
_map = None         # slug -> label|None, backed by CACHE_FILE


def _load_table():
    global _table
    if _table is None:
        _table = []
        with open(TABLE_FILE, newline='') as f:
            for r in csv.DictReader(f):
                _table.append((
                    r['label'],
                    float(r['back_lat']), float(r['back_lon']),
                    float(r['pred_lat']), float(r['pred_lon']),
                    float(r['shore_normal']),
                ))
    return _table


def _dist_km(lat1, lon1, lat2, lon2):
    coslat = math.cos(math.radians(lat1))
    return KM_PER_DEG * math.hypot(lat2 - lat1, coslat * (lon2 - lon1))


def _bearing(lat1, lon1, lat2, lon2):
    """Compass bearing (deg) from point 1 to point 2, flat-earth (fine at
    the sub-2 km scales this module deals in)."""
    coslat = math.cos(math.radians(lat1))
    return math.degrees(
        math.atan2(coslat * (lon2 - lon1), lat2 - lat1)) % 360


def _angle_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def select_transect(lat, lon):
    """(label, dist_km) of the best MOP transect for a point, or None."""
    candidates = []
    for label, blat, blon, plat, plon, normal in _load_table():
        d = _dist_km(lat, lon, plat, plon)
        if d <= MAX_DIST_KM:
            candidates.append((d, label, blat, blon, normal))
    for d, label, blat, blon, normal in sorted(candidates):
        if _dist_km(lat, lon, blat, blon) > BEARING_SKIP_KM:
            bearing = _bearing(blat, blon, lat, lon)
            if _angle_diff(bearing, normal) > 90:
                continue  # wrong coastline face; try the next-nearest
        return label, d
    return None


def _load_map():
    global _map
    if _map is None:
        try:
            with open(CACHE_FILE) as f:
                _map = json.load(f)
        except (OSError, ValueError):
            _map = {}
    return _map


def transect_for(spot):
    """Memoized transect label for a spot, or None if no transect fits.

    Only hits are memoized. Caching a miss would pin the spot to "no
    transect" for as long as the cache survives — and CI persists it across
    runs — so a later MAX_DIST_KM raise or transect-table refresh would
    silently not apply to exactly the spots it was meant to fix.
    """
    cached = _load_map()
    if cached.get(spot.slug):
        return cached[spot.slug]
    picked = select_transect(spot.latitude, spot.longitude)
    if not picked:
        return None
    cached[spot.slug] = picked[0]
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, 'w') as f:
        json.dump(cached, f, indent=1, sort_keys=True)
    return cached[spot.slug]
