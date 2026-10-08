#!/usr/bin/env python3
"""Build the spot orientation review page (a standalone HTML file).

Fills template.html with the app's current scoring engine (js/rating.js),
each spot's orientation and rating profile (data/spot_orientation.csv +
locations.csv overrides, exactly what builders.build_spot uses) and the
CDIP MOP backbeach points as the coastline. The page works offline; the
only external load is two Google Fonts.

    python data_exploration/quality_labels/review_page/build_review_page.py [-o OUT]

Writes spot-orientation.html next to this script by default (gitignored:
it is generated, ~470 KB). Rebuild after any change to rating_config.py
(run backend/sync_rating_logic.py first) or to the orientation file, since
the page is a snapshot of both.

Publishing: the shared page is a Claude artifact. From a Claude session, ask
it to publish the built file; to update the existing page instead of making
a new one, pass its URL (you need edit access):
    https://claude.ai/artifact/EvtJo9KWnUEMT15bDLxf8V
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', '..', '..')
sys.path.insert(0, os.path.join(ROOT, 'backend'))
import mop_transects as mt  # noqa: E402
from spots import load_spots  # noqa: E402


def num(x):
    return None if x == '' else float(x)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('-o', '--out', default=os.path.join(HERE, 'spot-orientation.html'))
    args = ap.parse_args()

    with open(os.path.join(ROOT, 'data', 'spot_orientation.csv'), newline='') as f:
        orient = {r['slug']: r for r in csv.DictReader(f)}
    table = mt._load_table()
    idx = {r[0]: i for i, r in enumerate(table)}
    bottom = {}
    bpath = os.path.join(ROOT, 'data', 'spot_bottom.csv')
    if os.path.exists(bpath):
        with open(bpath, newline='') as f:
            bottom = {r['slug']: r for r in csv.DictReader(f)}

    spots = []
    for s in load_spots(os.path.join(ROOT, 'data', 'locations.csv')):
        r = orient.get(s.slug)
        if r is None:
            print(f'  ! {s.slug} missing from spot_orientation.csv; skipped '
                  '(rerun backend/build_spot_orientation.py)')
            continue
        t = r['transect']
        spots.append({
            'slug': s.slug, 'name': s.name, 'region': s.region,
            'lat': round(s.latitude, 5), 'lon': round(s.longitude, 5),
            'tr': t,
            'plat': round(table[idx[t]][3], 5) if t else None,
            'plon': round(table[idx[t]][4], 5) if t else None,
            'facing': num(r['facing']), 'f500': num(r['facing_500m']),
            'f1k': num(r['facing_1km']), 'spread': num(r['spread']),
            'src': r['facing_source'], 'off': num(r['offshore_dir']),
            'arcs': r['open_arcs'],
            'flags': [x for x in r['flags'].split(';') if x],
            'bottom': (bottom.get(s.slug) or {}).get('bottom_type'),
            'hard': num((bottom.get(s.slug) or {}).get('hard_share', '')),
            'bcov': num((bottom.get(s.slug) or {}).get('coverage', '')),
            'bend': num((bottom.get(s.slug) or {}).get('convexity', '')),
            'bwhy': (bottom.get(s.slug) or {}).get('reason'),
            'shore': {g: num((bottom.get(s.slug) or {}).get(f'shore_{g}', ''))
                      for g in ('rock', 'armoured', 'gravel', 'sand', 'flat')},
            'bflags': [x for x in ((bottom.get(s.slug) or {}).get('flags') or '').split(';') if x],
            'meta': s.profile_meta,      # what the app scores with
            'hand': s.rating_meta,       # hand-entered overrides only
        })
    coast = [[r[0], round(r[1], 5), round(r[2], 5)] for r in table]

    with open(os.path.join(HERE, 'template.html')) as f:
        page = f.read()
    with open(os.path.join(ROOT, 'js', 'rating.js')) as f:
        engine = f.read()
    page = page.replace('__ENGINE__', engine).replace(
        '__DATA__', json.dumps({'spots': spots, 'coast': coast}, separators=(',', ':')))
    with open(args.out, 'w') as f:
        f.write(page)
    print(f'Wrote {os.path.relpath(args.out)} ({len(spots)} spots, {len(page) // 1024} KB)')


if __name__ == '__main__':
    main()
