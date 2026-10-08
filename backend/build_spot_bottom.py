#!/usr/bin/env python3
"""Classify each spot's bottom as reef, point or beach.

Writes data/spot_bottom.csv. Run locally when spots or the transect table
change; CI only reads the output. Needs network access and the LERC decoder
(local only, not in requirements.txt):

    pip install lerc
    python backend/build_spot_bottom.py

SUBSTRATE (hard_share)
  CDFW "Predicted Nearshore Benthic Substrates of California" (BIOS ds3091,
  CC BY 4.0): a statewide 10 m hard/soft seabed raster derived from the
  rugosity of the California Seafloor Mapping Program surveys. Sampled every
  10 m along three parallel lines (the spot's MOP transect and copies 100 m
  either side), from the backbeach out to the ~10 m-depth prediction site.
  hard_share = hard pixels / mapped pixels; coverage = mapped / sampled.
  Tiles are cached in data/bottom_cache/ (gitignored).
  Cobble and boulder points read soft: loose cobbles aren't rugose at 10 m.

HEADLAND (convexity)
  How much the coast bends seaward around the transect, from the MOP shore
  normals: the signed change in normal between the transects ~500 m either
  side, oriented so positive = convex (normals fan apart, a headland) and
  negative = concave (a bay). Points break around headlands, which the
  substrate map misses when the bottom is cobble.

SHORELINE (rock / armoured / gravel / sand / flat shares)
  CDFW "Shoreline Types" (BIOS ds3115, NOAA Environmental Sensitivity Index
  compilation, CC BY 4.0): classified shoreline segments. Length-weighted
  share of each group within SHORE_RADIUS_M of the backbeach point; a segment
  listing several types splits its length between them. It describes the
  shoreline at the waterline, not the seabed where waves break, so it backs
  up the substrate map rather than replacing it. Cached per spot in
  data/bottom_cache/esi_<slug>.json.

BOTTOM TYPE
  point  convexity >= POINT_DEG: the coast bends seaward (a headland), whatever
         the substrate - rocky headlands (Steamer Lane, Point Dume) are points
  reef   hard_share >= REEF_HARD, or hard_share >= REEF_HARD_WITH_ROCK with a
         mostly rocky shoreline (rock >= ROCKY_SHORE): catches reefs just under
         the cutoff (Natural Bridges, San Onofre, Church, Sunny Cove)
  beach  everything else, including spots the raster doesn't cover

FLAGS (for hand review, never change the type)
  armoured_shore  shoreline mostly riprap / seawall with no rock or gravel: a
                  harbour or jetty can fake a headland (Oceanside Harbor), but
                  real points with seawalls (Ventura Point) get it too
  no_shoreline    no mapped shoreline within SHORE_RADIUS_M: around Seal Beach /
                  Anaheim Bay these coincide with jetty-made "points"
"""
import csv
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from pyproj import Transformer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import mop_transects as mt  # noqa: E402
from spots import load_spots  # noqa: E402

LOCATIONS = os.path.join(BASE_DIR, '..', 'data', 'locations.csv')
ORIENTATION = os.path.join(BASE_DIR, '..', 'data', 'spot_orientation.csv')
OUT = os.path.join(BASE_DIR, '..', 'data', 'spot_bottom.csv')
CACHE = os.path.join(BASE_DIR, '..', 'data', 'bottom_cache')

SERVICE = ('https://tiledimageservices2.arcgis.com/Uq9r85Potqm3MfRV/arcgis/rest/'
           'services/biosds3091_cru/ImageServer')
ORIGIN_X, ORIGIN_Y = -436528.970884573, 459866.804858047   # EPSG:3310 tile origin
RES, TILE, LEVEL = 10.0, 256, 9                             # 10 m pixels
HARD, SOFT = 1, 2
SHORE_SERVICE = ('https://services2.arcgis.com/Uq9r85Potqm3MfRV/arcgis/rest/services/'
                 'biosds3115_fnu/FeatureServer/0/query')
SHORE_GROUP = {**{c: 'rock' for c in ('1A', '1C', '2', '2A', '8A')},
               **{c: 'armoured' for c in ('1B', '6B', '8B', '8C')},
               **{c: 'gravel' for c in ('4', '5', '6A')},
               **{c: 'sand' for c in ('3', '3A')},
               **{c: 'flat' for c in ('7', '9', '10', '10A')}}
SHORE_GROUPS = ('rock', 'armoured', 'gravel', 'sand', 'flat')
SHORE_RADIUS_M = 250.0

LINE_OFFSETS_M = (-100.0, 0.0, 100.0)
HEADLAND_WINDOW_KM = 0.5
NO_NORMAL = 1000.0           # CDIP marker for "no shore normal"
REEF_HARD = 0.40
REEF_HARD_WITH_ROCK = 0.25
ROCKY_SHORE = 0.50
ARMOURED_SHORE = 0.80
POINT_DEG = 20.0
MIN_COVERAGE = 0.30          # below this, hard_share is not trusted

FIELDS = ['slug', 'transect', 'hard_share', 'coverage', 'convexity',
          *(f'shore_{g}' for g in SHORE_GROUPS), 'shoreline',
          'bottom_type', 'reason', 'flags']

_to3310 = Transformer.from_crs(4326, 3310, always_xy=True)


def _tile_path(row, col):
    return os.path.join(CACHE, f'{LEVEL}_{row}_{col}.lerc')


def _fetch_tile(rc):
    """Download one tile into the cache (an empty file marks 'no data')."""
    path = _tile_path(*rc)
    if os.path.exists(path):
        return
    try:
        blob = urllib.request.urlopen(f'{SERVICE}/tile/{LEVEL}/{rc[0]}/{rc[1]}',
                                      timeout=60).read()
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        blob = b''
    with open(path, 'wb') as f:
        f.write(blob)


class Substrate:
    def __init__(self):
        import lerc                      # local-only dependency
        self._lerc = lerc
        self._tiles = {}

    @staticmethod
    def tile_of(x, y):
        return (int((ORIGIN_Y - y) // (RES * TILE)),
                int((x - ORIGIN_X) // (RES * TILE)))

    def _tile(self, rc):
        if rc not in self._tiles:
            with open(_tile_path(*rc), 'rb') as f:
                blob = f.read()
            arr = None
            if blob:
                res, data, valid = self._lerc.decode(blob)[:3]
                if res == 0:
                    mask = valid.astype(bool) if valid is not None else np.ones(data.shape, bool)
                    arr = np.where(mask, data, 0).astype(np.int8)
            self._tiles[rc] = arr
        return self._tiles[rc]

    def value(self, x, y):
        row, col = self.tile_of(x, y)
        a = self._tile((row, col))
        if a is None:
            return 0
        px = int((x - ORIGIN_X) / RES) - col * TILE
        py = int((ORIGIN_Y - y) / RES) - row * TILE
        return int(a[py, px]) if 0 <= py < a.shape[0] and 0 <= px < a.shape[1] else 0


def transect_points(r):
    """Sample points (EPSG:3310) on three lines parallel to the transect."""
    x0, y0 = _to3310.transform(r[2], r[1])
    x1, y1 = _to3310.transform(r[4], r[3])
    L = math.hypot(x1 - x0, y1 - y0)
    if L < 20:
        return []
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    pts = []
    for off in LINE_OFFSETS_M:
        ox, oy = -uy * off, ux * off
        pts += [(x0 + ox + ux * s, y0 + oy + uy * s) for s in np.arange(0, L, RES)]
    return pts


def convexity(table, i0):
    """Signed bend of the coast around table[i0] (deg), + = headland.
    Walks across county prefixes (Rincon and Trestles sit on county lines);
    only a > 400 m gap between neighbours stops it."""
    def walk(step):
        i, dist = i0, 0.0
        while True:
            n = i + step
            if not 0 <= n < len(table):
                return None
            gap = mt._dist_km(table[i][1], table[i][2], table[n][1], table[n][2])
            if gap > 0.4:
                return None
            dist += gap
            i = n
            if dist >= HEADLAND_WINDOW_KM and table[i][5] != NO_NORMAL:
                return i

    a, b = walk(-1), walk(1)
    if a is None or b is None:
        return None
    # Walking a -> b along the coast, is the sea on the right or the left?
    walk_bearing = mt._bearing(table[a][1], table[a][2], table[b][1], table[b][2])
    normal = table[i0][5] if table[i0][5] != NO_NORMAL else table[b][5]
    sea_right = ((normal - walk_bearing) % 360) < 180
    turn = (table[b][5] - table[a][5] + 180) % 360 - 180   # clockwise change a -> b
    # With the sea on the right, a headland turns the coast left (normals
    # rotate anticlockwise); with the sea on the left, the reverse.
    return -turn if sea_right else turn


def _shore_path(slug):
    return os.path.join(CACHE, f'esi_{slug}.json')


def _fetch_shore(job):
    """Download the shoreline segments around one spot into the cache."""
    slug, lat, lon = job
    path = _shore_path(slug)
    if os.path.exists(path):
        return
    q = {'geometry': f'{lon},{lat}', 'geometryType': 'esriGeometryPoint', 'inSR': 4326,
         'distance': SHORE_RADIUS_M, 'units': 'esriSRUnit_Meter',
         'spatialRel': 'esriSpatialRelIntersects',
         'outFields': 'shoretype_1,shoretype_2,shoretype_3', 'returnGeometry': 'true',
         'outSR': 4326, 'f': 'json'}
    d = json.load(urllib.request.urlopen(f'{SHORE_SERVICE}?{urllib.parse.urlencode(q)}',
                                         timeout=90))
    if 'error' in d:
        raise RuntimeError(f'shoreline query failed for {slug}: {d["error"]}')
    d['_center'] = [lat, lon]
    with open(path, 'w') as f:
        json.dump(d, f)


def shoreline_shares(slug):
    """{group: length share} within SHORE_RADIUS_M, or None if no shoreline."""
    with open(_shore_path(slug)) as f:
        d = json.load(f)
    lat0, lon0 = d['_center']
    kx, ky = math.cos(math.radians(lat0)) * 111320.0, 111320.0
    w = dict.fromkeys(SHORE_GROUPS, 0.0)
    for feat in d.get('features', []):
        a = feat['attributes']
        codes = [a.get(k, '').strip() for k in ('shoretype_1', 'shoretype_2', 'shoretype_3')]
        groups = [SHORE_GROUP[c] for c in codes if c in SHORE_GROUP]
        if not groups:
            continue
        for path in feat['geometry'].get('paths', []):
            for (x1, y1), (x2, y2) in zip(path, path[1:]):
                mx, my = (x1 + x2) / 2, (y1 + y2) / 2
                if math.hypot((mx - lon0) * kx, (my - lat0) * ky) > SHORE_RADIUS_M:
                    continue
                seg = math.hypot((x2 - x1) * kx, (y2 - y1) * ky)
                for g in groups:
                    w[g] += seg / len(groups)
    total = sum(w.values())
    return {g: v / total for g, v in w.items()} if total else None


def main():
    table = mt._load_table()
    idx = {r[0]: i for i, r in enumerate(table)}
    with open(ORIENTATION, newline='') as f:
        transects = {r['slug']: r['transect'] for r in csv.DictReader(f)}
    spots = load_spots(LOCATIONS)

    lines = {s.slug: transect_points(table[idx[transects[s.slug]]])
             for s in spots if transects.get(s.slug)}
    needed = {Substrate.tile_of(x, y) for pts in lines.values() for x, y in pts}
    os.makedirs(CACHE, exist_ok=True)
    missing = [rc for rc in needed if not os.path.exists(_tile_path(*rc))]
    print(f'{len(needed)} tiles needed, {len(missing)} to download')
    with ThreadPoolExecutor(8) as pool:          # network-bound
        list(pool.map(_fetch_tile, missing))

    shore_jobs = []
    for s in spots:
        t = transects.get(s.slug)
        lat, lon = ((table[idx[t]][1], table[idx[t]][2]) if t else (s.latitude, s.longitude))
        shore_jobs.append((s.slug, lat, lon))
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(_fetch_shore, shore_jobs))

    sub = Substrate()
    rows = []
    for s in spots:
        t = transects.get(s.slug) or ''
        hard = cov = conv = None
        shore = shoreline_shares(s.slug)
        if t:
            vals = [sub.value(x, y) for x, y in lines[s.slug]]
            mapped = [v for v in vals if v in (HARD, SOFT)]
            cov = len(mapped) / len(vals) if vals else 0.0
            hard = sum(v == HARD for v in mapped) / len(mapped) if mapped else None
            conv = convexity(table, idx[t])
        trusted = hard is not None and cov >= MIN_COVERAGE
        rocky = shore is not None and shore['rock'] >= ROCKY_SHORE
        if conv is not None and conv >= POINT_DEG:
            kind, why = 'point', f'coast bends {conv:.0f} deg seaward'
        elif trusted and hard >= REEF_HARD:
            kind, why = 'reef', f'{hard:.0%} hard bottom'
        elif trusted and hard >= REEF_HARD_WITH_ROCK and rocky:
            kind, why = 'reef', f"{hard:.0%} hard bottom, {shore['rock']:.0%} rocky shoreline"
        else:
            kind = 'beach'
            why = ('no transect' if not t else
                   'substrate not mapped' if not trusted else f'{hard:.0%} hard, no headland')
        flags = []
        if shore and shore['armoured'] >= ARMOURED_SHORE and shore['rock'] + shore['gravel'] < 0.1:
            flags.append('armoured_shore')
        if shore is None:
            flags.append('no_shoreline')
        fmt = lambda v, p: '' if v is None else f'{v:.{p}f}'  # noqa: E731
        rows.append({'slug': s.slug, 'transect': t, 'hard_share': fmt(hard, 2),
                     'coverage': fmt(cov, 2), 'convexity': fmt(conv, 0),
                     **{f'shore_{g}': fmt(shore[g] if shore else None, 2) for g in SHORE_GROUPS},
                     'shoreline': max(SHORE_GROUPS, key=shore.get) if shore else '',
                     'bottom_type': kind, 'reason': why, 'flags': ';'.join(flags)})

    with open(OUT, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    counts = {k: sum(r['bottom_type'] == k for r in rows) for k in ('reef', 'point', 'beach')}
    flagged = sum(1 for r in rows if r['flags'])
    print(f'Wrote {len(rows)} spots to {os.path.relpath(OUT)}: {counts}, {flagged} flagged')


if __name__ == '__main__':
    main()
