#!/usr/bin/env python3
"""Derive each spot's facing direction, offshore wind and swell window.

Writes data/spot_orientation.csv, which spots.load_spots reads as derived
rating metadata (hand-entered locations.csv columns still win). Run locally
when spots or the transect table change; CI only reads the output.

    python backend/build_spot_orientation.py [--grids PATH_TO_SALIX/data/grids]

FACING (deg true the break faces, pointing offshore)
  From the CDIP MOP shore normals in data/mop_transects.csv, starting at the
  transect the nearshore forecast already uses for the spot
  (mop_transects.transect_for) and averaging it with its consecutive
  alongshore neighbours as unit vectors, triangular weights:

      k=2 (~+/-200 m of coast)  -> the value used
      k=5, k=10 (~0.5 / 1 km)   -> stability check only; a spread > 20 deg
                                   flags a point/cove where the coast turns

  The walk stops at a county-prefix change, a > 400 m gap between
  neighbours, or a transect whose normal is CDIP's 1000 "no value" marker.
  Spots with no usable transect fall back to the centre of the open-water
  arc from the ray cast below (facing_source = rays).

SWELL WINDOW
  Rays every 2 deg from the transect prediction site (~10 m depth; the spot
  pin when there is no transect), stepped 0.4 km over the Salix elevation
  grids (NOAA CRM / ETOPO, land > 0 m). A ray is blocked only by land within
  30 km: headlands and the coast itself. Strict line of sight to 250 km
  made every distant island a wall (San Nicolas, 200+ km out, split Ocean
  Beach's window; 88 spots came out facing land), when in reality swell
  bends around islands and points. The cost: Channel Islands shadowing
  (35-45 km off the Santa Barbara / Ventura coast) is not modelled. Within
  +/-90 deg of the facing direction, gaps up to MAX_BRIDGE_DEG are bridged
  (a small island's shadow fills in as swell bends around it: Anacapa cut
  a 6 deg gap that split Ventura Point's window and threw away the arc it
  faces). The window is the open arc containing the facing direction, or
  the widest arc when land lies straight out (facing_blocked):
  swell_min / swell_max are its edges, swell_ideal the facing direction if
  it lies inside, else the arc centre. Every open arc is listed in
  open_arcs so split windows are visible for review.

OFFSHORE
  offshore_dir = facing + 180.
"""
import argparse
import csv
import math
import os
import re
import sys

import numpy as np
import xarray as xr
from scipy.spatial import cKDTree

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import mop_transects as mt  # noqa: E402
from spots import load_spots  # noqa: E402

LOCATIONS = os.path.join(BASE_DIR, '..', 'data', 'locations.csv')
OUT = os.path.join(BASE_DIR, '..', 'data', 'spot_orientation.csv')
DEFAULT_GRIDS = os.path.join(BASE_DIR, '..', '..', 'surflie_salix', 'data', 'grids')
GRID_FILES = ['sb_fine', 't1_outer', 't2_outer', 't3_outer', 't4_outer', 't5_outer']

WINDOWS = (2, 5, 10)        # consecutive transects each side
NO_NORMAL = 1000.0          # CDIP marker for "no shore normal"
MAX_GAP_KM = 0.4
SPREAD_FLAG = 20.0
RAY_STEP_DEG = 2
MAX_BRIDGE_DEG = 8          # blocked gaps this narrow count as open
RAY_STEP_KM = 0.4
RAY_MAX_KM = 30.0          # only land this close blocks (see docstring)
COVER_KM = 0.6              # nearest grid node farther than this => off-grid
KM_PER_DEG = 111.32
LAT_REF = 36.5              # flat-earth x scale for the KD tree

FIELDS = ['slug', 'transect', 'facing', 'facing_500m', 'facing_1km',
          'spread', 'facing_source', 'offshore_dir', 'swell_min',
          'swell_ideal', 'swell_max', 'open_arcs', 'flags']


def circ_mean(pairs):
    sx = sum(w * math.sin(math.radians(a)) for a, w in pairs)
    sy = sum(w * math.cos(math.radians(a)) for a, w in pairs)
    if abs(sx) < 1e-9 and abs(sy) < 1e-9:
        return None
    return math.degrees(math.atan2(sx, sy)) % 360


def facing_along(table, i0, k):
    """Weighted circular mean of shore normals over k consecutive transects
    each side of table[i0]; None if no usable normal."""
    pre = re.match(r'[A-Z]+', table[i0][0]).group()
    pairs = []
    if table[i0][5] != NO_NORMAL:
        pairs.append((table[i0][5], 1.0))
    for step in (-1, 1):
        i = i0
        for j in range(1, k + 1):
            n = i + step
            if not 0 <= n < len(table):
                break
            if not table[n][0].startswith(pre):
                break
            if mt._dist_km(table[i][1], table[i][2], table[n][1], table[n][2]) > MAX_GAP_KM:
                break
            if table[n][5] != NO_NORMAL:
                pairs.append((table[n][5], 1 - j / (k + 1)))
            i = n
    return circ_mean(pairs)


class Elevation:
    """Nearest-node elevation lookup over the union of the Salix grids."""

    def __init__(self, grid_dir):
        lons, lats, elev = [], [], []
        for name in GRID_FILES:
            d = xr.open_dataset(os.path.join(grid_dir, f'{name}.nc'))
            if d['lon'].ndim == 1:
                lo, la = np.meshgrid(d['lon'].values, d['lat'].values)
                el = d['elevation'].values
            else:
                lo, la, el = d['lon'].values, d['lat'].values, d['elevation'].values
            ok = np.isfinite(el)
            lons.append(lo[ok]); lats.append(la[ok]); elev.append(el[ok])
        self.elev = np.concatenate(elev)
        self.tree = cKDTree(self._xy(np.concatenate(lons), np.concatenate(lats)))

    @staticmethod
    def _xy(lon, lat):
        return np.c_[np.asarray(lon) * math.cos(math.radians(LAT_REF)) * KM_PER_DEG,
                     np.asarray(lat) * KM_PER_DEG]

    def land(self, lon, lat):
        """(is_land, covered) arrays for points."""
        d, i = self.tree.query(self._xy(lon, lat))
        covered = d <= COVER_KM
        return covered & (self.elev[i] > 0), covered


def open_bearings(elev, lat, lon, skip_km=0.0):
    """{bearing: open?} for rays every RAY_STEP_DEG from (lat, lon). Land
    closer than skip_km is ignored (a spot pin sitting on the beach)."""
    bearings = np.arange(0, 360, RAY_STEP_DEG)
    dist = np.arange(RAY_STEP_KM, RAY_MAX_KM + 1e-6, RAY_STEP_KM)
    b = np.radians(bearings)[:, None]
    dlat = dist[None, :] * np.cos(b) / KM_PER_DEG
    dlon = dist[None, :] * np.sin(b) / (KM_PER_DEG * math.cos(math.radians(lat)))
    is_land, covered = elev.land((lon + dlon).ravel(), (lat + dlat).ravel())
    is_land = is_land.reshape(dlat.shape)
    # Land counts only before the ray first leaves grid coverage (off-grid is
    # open ocean: the grids extend >100 km seaward everywhere).
    covered = covered.reshape(dlat.shape)
    left = np.cumsum(~covered, axis=1) > 0
    hit = (is_land & ~left & (dist[None, :] > skip_km)).any(axis=1)
    return {int(bb): not h for bb, h in zip(bearings, hit)}


def arcs_near(opens, facing):
    """Open arcs (start, end) clockwise, restricted to +/-90 of facing, with
    blocked gaps up to MAX_BRIDGE_DEG bridged. Bearings snapped to the ray grid."""
    offs = list(range(-90, 91, RAY_STEP_DEG))
    snap = lambda x: int(round(x / RAY_STEP_DEG) * RAY_STEP_DEG) % 360  # noqa: E731
    seq = [opens[snap(facing + o)] for o in offs]
    max_run = MAX_BRIDGE_DEG // RAY_STEP_DEG
    i = 0
    while i < len(seq):
        if seq[i]:
            i += 1
            continue
        j = i
        while j < len(seq) and not seq[j]:
            j += 1
        # Interior gap (open on both sides) narrow enough to bridge.
        if i > 0 and j < len(seq) and j - i <= max_run:
            seq[i:j] = [True] * (j - i)
        i = j
    arcs, start = [], None
    for i, ok in enumerate(seq + [False]):
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            arcs.append((offs[start], offs[i - 1]))
            start = None
    return [((facing + a) % 360, (facing + b) % 360, a, b) for a, b in arcs]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--grids', default=DEFAULT_GRIDS)
    args = ap.parse_args()

    table = mt._load_table()
    idx = {r[0]: i for i, r in enumerate(table)}
    elev = Elevation(args.grids)
    rows = []
    for spot in load_spots(LOCATIONS):
        label = mt.transect_for(spot)
        flags = []
        facings = [None] * len(WINDOWS)
        lat, lon = spot.latitude, spot.longitude
        if label:
            i0 = idx[label]
            facings = [facing_along(table, i0, k) for k in WINDOWS]
            lat, lon = table[i0][3], table[i0][4]       # prediction site
        # Prediction sites sit ~10 m deep; a bare spot pin may be on the sand.
        opens = open_bearings(elev, lat, lon, skip_km=0.0 if label else 1.0)

        facing, source = facings[0], 'mop'
        if facing is None:
            # No usable transect: centre of the widest open arc, searched
            # around both a west- and an east-facing guess (full circle).
            source = 'rays'
            flags.append('no_transect' if not label else 'no_normal')
            cands = [(g, a) for g in (270.0, 90.0) for a in arcs_near(opens, g)]
            g, best = max(cands, key=lambda x: x[1][3] - x[1][2], default=(270.0, None))
            facing = (g + (best[2] + best[3]) / 2) % 360 if best else 270.0

        known = [f for f in facings if f is not None]
        spread = max((mt._angle_diff(a, b) for a in known for b in known), default=None)
        if spread is not None and spread > SPREAD_FLAG:
            flags.append('coast_turns')

        arcs = arcs_near(opens, facing)
        if arcs:
            facing_arc = [x for x in arcs if x[2] <= 0 <= x[3]]
            lo_, hi_, a, b = facing_arc[0] if facing_arc else max(arcs, key=lambda x: x[3] - x[2])
            ideal = facing if a <= 0 <= b else (facing + (a + b) / 2) % 360
            if not facing_arc:
                flags.append('facing_blocked')
            if len(arcs) > 1:
                flags.append('split_window')
        else:
            lo_, hi_, ideal = (facing - 70) % 360, (facing + 70) % 360, facing
            flags.append('no_open_water')

        fmt = lambda x: '' if x is None else f'{x:.0f}'  # noqa: E731
        rows.append({
            'slug': spot.slug, 'transect': label or '',
            'facing': fmt(facing), 'facing_500m': fmt(facings[1]),
            'facing_1km': fmt(facings[2]), 'spread': fmt(spread),
            'facing_source': source, 'offshore_dir': fmt((facing + 180) % 360),
            'swell_min': fmt(lo_), 'swell_ideal': fmt(ideal), 'swell_max': fmt(hi_),
            'open_arcs': ';'.join(f'{x:.0f}-{y:.0f}' for x, y, _, _ in arcs),
            'flags': ';'.join(flags),
        })

    with open(OUT, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    flagged = sum(1 for r in rows if r['flags'])
    print(f'Wrote {len(rows)} spots to {os.path.relpath(OUT)} ({flagged} flagged)')


if __name__ == '__main__':
    main()
