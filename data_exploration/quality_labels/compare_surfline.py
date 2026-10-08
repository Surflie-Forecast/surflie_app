"""Score Surfline's own forecast inputs with our rating engine and compare.

Surfline's site and API sit behind a Cloudflare bot challenge, so this uses a
public sample of raw Surfline API responses (rating + wave + wind, same spot,
same hours) saved in github.com/TGOlson/surfline/sample-data. The spot is Old
Man's at Tourmaline, which is also in data/locations.csv at identical
coordinates, so the comparison is like for like.

The sample is fetched at run time into .cache/ (not committed).

    python data_exploration/quality_labels/compare_surfline.py
"""
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'backend'))
from rating_config import BUCKETS, build_profile, bucket_for_score, score_conditions  # noqa: E402
from spots import load_spots  # noqa: E402

RAW = 'https://raw.githubusercontent.com/TGOlson/surfline/main/sample-data/tourmy-{}.json'
CACHE = os.path.join(HERE, '.cache')
FT = 0.3048

# Surfline's 7-step scale (API rating.value), with the colors sampled from the
# bars in support.surfline.com article 36277684017819 ("Surf Ratings & Colors").
# value is continuous in the API (e.g. 2.67) and key is its bucket.
SURFLINE_SCALE = [
    ('VERY_POOR',    0, 1, '#f1496a'),
    ('POOR',         1, 2, '#fd9500'),
    ('POOR_TO_FAIR', 2, 3, '#ffce1c'),
    ('FAIR',         3, 4, '#09d572'),
    ('FAIR_TO_GOOD', 4, 5, '#009371'),
    ('GOOD',         5, 5, '#6751f4'),   # forecaster only, overrides bar 5
    ('EPIC',         6, 5, '#631ec7'),   # forecaster only, overrides bar 5
]


def load(name):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f'{name}.json')
    if not os.path.exists(path):
        urllib.request.urlretrieve(RAW.format(name), path)
    with open(path) as f:
        return json.load(f)


def main():
    ratings = {x['timestamp']: x['rating'] for x in load('ratings')['data']['rating']}
    waves = {x['timestamp']: x for x in load('waves')['data']['wave']}
    winds = {x['timestamp']: x for x in load('wind')['data']['wind']}

    # The profile the app builds for this spot: generated orientation
    # (data/spot_orientation.csv, MOP shore normal -> offshore 34 deg) plus any
    # locations.csv overrides - exactly what builders.build_spot uses.
    spot = next(s for s in load_spots(os.path.join(HERE, '..', '..', 'data', 'locations.csv'))
                if s.slug == 'old-man-s-at-tourmaline')
    app_profile = build_profile(spot.profile_meta, coast_normal=spot.coast_normal)
    # Reference: the old untuned profile every spot used before orientation
    # was generated (coast_normal 270 -> offshore 90, swell window 200-340).
    tuned_profile = build_profile({}, coast_normal=270.0)

    tz = timezone(timedelta(hours=-8))
    rows = []
    for t in sorted(ratings):
        w, wi = waves.get(t), winds.get(t)
        if not w or not wi:
            continue
        # Combined Hs from the swell partitions (ft -> m), primary = most impact.
        hs = math.sqrt(sum(s['height'] ** 2 for s in w['swells'])) * FT
        prim = max(w['swells'], key=lambda s: s['impact'])
        args = (hs, prim['period'], prim['direction'], wi['speed'], wi['direction'])
        s_app = score_conditions(*args, profile=app_profile)
        s_tun = score_conditions(*args, profile=tuned_profile)
        rows.append({
            'time': datetime.fromtimestamp(t, tz).strftime('%m-%d %H'),
            'sl_key': ratings[t]['key'], 'sl_value': ratings[t]['value'],
            'surf_ft': f"{w['surf']['raw']['min']:.1f}-{w['surf']['raw']['max']:.1f}",
            'hs_m': hs, 'per': prim['period'], 'dir': prim['direction'],
            'wind_kt': wi['speed'], 'wind_dir': wi['direction'],
            'wind_type': wi['directionType'],
            'app_score': s_app, 'app_label': bucket_for_score(s_app)[0],
            'tuned_score': s_tun, 'tuned_label': bucket_for_score(s_tun)[0],
        })

    print(f"{'local':8} {'surfline':13} {'val':>4} {'surf ft':>9} {'Hs m':>5} "
          f"{'per':>3} {'wind':>12} {'type':12} {'ours(app)':>14} {'ours(old 270)':>16}")
    for r in rows:
        print(f"{r['time']:8} {r['sl_key']:13} {r['sl_value']:>4} {r['surf_ft']:>9} "
              f"{r['hs_m']:5.2f} {r['per']:>3} {r['wind_kt']:5.1f}kt@{r['wind_dir']:3.0f} "
              f"{r['wind_type']:12} {r['app_score']:5.0f} {r['app_label']:9}"
              f"{r['tuned_score']:6.0f} {r['tuned_label']}")

    # Rank agreement: does our score order the hours the way Surfline does?
    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        rk = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            for k in range(i, j + 1):
                rk[order[k]] = (i + j) / 2
            i = j + 1
        return rk

    def spearman(a, b):
        ra, rb = ranks(a), ranks(b)
        ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
        cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
        return cov / math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))

    sl = [r['sl_value'] for r in rows]
    print(f"\nn={len(rows)}  Spearman vs Surfline value: "
          f"app profile {spearman(sl, [r['app_score'] for r in rows]):+.2f}, "
          f"old coast_normal=270 {spearman(sl, [r['tuned_score'] for r in rows]):+.2f}")
    print('\nOur buckets:     ' + '  '.join(f"{b['name']}>={b['floor']}" for b in BUCKETS))
    print('Surfline scale:  ' + '  '.join(f'{k}={v}' for k, v, _, _ in SURFLINE_SCALE))


if __name__ == '__main__':
    main()
