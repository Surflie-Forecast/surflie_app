"""Prototype wind / steepness variants against the Surfline sample.

Nothing here touches the app: each variant monkeypatches rating_config's
factors in-process, rescores the 24 Old Man's hours from compare_surfline.py,
and reports Spearman vs Surfline's continuous `value` under both the app
profile (offshore 90) and the Surfline-implied one (offshore 30).

    python data_exploration/quality_labels/candidates.py

n = 24 from one spot and one swell event: use this to rule ideas out, not to
fit constants.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'backend'))
import rating_config as rc  # noqa: E402
from compare_surfline import FT, load  # noqa: E402


def wind_current(speed, direction, p):
    return rc._wind_factor(speed, direction, p)


def wind_v2(speed, direction, p):
    """Offshore boost peaks at ~8 kt and is gone by 15 kt; onshore bites
    sooner (saturates at 15 kt); every direction loses quality above 15 kt."""
    if speed < 3.0:
        return 1.0
    align = math.cos(math.radians(rc._ang_diff(direction, p.offshore_dir)))
    if align >= 0:
        # Triangular bump: 0 at 3 kt, peak at 8 kt, 0 again at 15 kt.
        bump = (speed - 3.0) / 5.0 if speed <= 8.0 else max(0.0, (15.0 - speed) / 7.0)
        f = 1.0 + 0.12 * align * bump
    else:
        strength = min((speed - 3.0) / 12.0, 1.0) ** 0.7
        f = max(0.1, 1.0 - 0.9 * (-align) * strength)
    # Strength penalty, direction-blind: 1.0 at 15 kt -> 0.4 at 27 kt.
    if speed > 15.0:
        f *= max(0.4, 1.0 - 0.6 * (speed - 15.0) / 12.0)
    return f


def _wind_v3(speed, direction, p, full=30.0, zero=60.0, cross=100.0):
    """wind_v2's speed curves on a narrower direction window, by angle off
    straight offshore: <= full -> whole boost, tapering to none at `zero`;
    zero..cross -> cross-shore, a penalty growing to 0.3 of onshore's;
    beyond cross -> onshore penalty, full at 180."""
    if speed < 3.0:
        return 1.0
    off = rc._ang_diff(direction, p.offshore_dir)
    if off <= zero:
        a = 1.0 if off <= full else (zero - off) / (zero - full)
        bump = (speed - 3.0) / 5.0 if speed <= 8.0 else max(0.0, (15.0 - speed) / 7.0)
        f = 1.0 + 0.12 * a * bump
    else:
        if off <= cross:
            a = 0.3 * (off - zero) / (cross - zero)
        else:
            a = 0.3 + 0.7 * (off - cross) / (180.0 - cross)
        strength = min((speed - 3.0) / 12.0, 1.0) ** 0.7
        f = max(0.1, 1.0 - 0.9 * a * strength)
    if speed > 15.0:
        f *= max(0.4, 1.0 - 0.6 * (speed - 15.0) / 12.0)
    return f


def wind_v3(speed, direction, p):
    return _wind_v3(speed, direction, p)


def wind_v3_wide(speed, direction, p):
    return _wind_v3(speed, direction, p, full=45.0, zero=75.0, cross=110.0)


def steepness_factor(height, period):
    """Storm / closeout proxy from deep-water steepness Hs / L0.
    1.0 up to 0.015 (organised swell), 0.5 by 0.030 (storm sea)."""
    l0 = 1.56 * period ** 2
    s = height / max(l0, 1e-6)
    if s <= 0.015:
        return 1.0
    return max(0.5, 1.0 - 0.5 * (s - 0.015) / 0.015)


VARIANTS = {
    'current':          (wind_current, False),
    'wind_v2':          (wind_v2, False),
    'current+steep':    (wind_current, True),
    'wind_v2+steep':    (wind_v2, True),
    'wind_v3+steep':    (wind_v3, True),
    'v3_wide+steep':    (wind_v3_wide, True),
}


def rows():
    ratings = {x['timestamp']: x['rating'] for x in load('ratings')['data']['rating']}
    waves = {x['timestamp']: x for x in load('waves')['data']['wave']}
    winds = {x['timestamp']: x for x in load('wind')['data']['wind']}
    out = []
    for t in sorted(ratings):
        w, wi = waves.get(t), winds.get(t)
        if not w or not wi:
            continue
        hs = math.sqrt(sum(s['height'] ** 2 for s in w['swells'])) * FT
        prim = max(w['swells'], key=lambda s: s['impact'])
        out.append((ratings[t]['value'], ratings[t]['key'],
                    (hs, prim['period'], prim['direction'], wi['speed'], wi['direction'])))
    return out


def score(args, profile, wind_fn, steep):
    h, per, sd, ws, wd = args
    raw = (rc._energy_factor(h, per, profile) * rc._direction_factor(sd, profile)
           * wind_fn(ws, wd, profile))
    if steep:
        raw *= steepness_factor(h, per)
    return max(0.0, min(100.0, raw * 100.0))


def spearman(a, b):
    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        rk, i = [0.0] * len(xs), 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            for k in range(i, j + 1):
                rk[order[k]] = (i + j) / 2
            i = j + 1
        return rk
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    return cov / math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))


def main():
    data = rows()
    sl = [v for v, _, _ in data]
    profiles = {
        'off=34 (MOP)': rc.build_profile({'offshore_dir': 34.0}, coast_normal=270.0),
        'off=90 (app)': rc.build_profile({}, coast_normal=270.0),
        'off=30': rc.build_profile({'offshore_dir': 30.0}, coast_normal=270.0),
    }
    print(f"{'variant':16}" + ''.join(f'{k:>14}' for k in profiles))
    scored = {}
    for name, (fn, steep) in VARIANTS.items():
        cells = []
        for pname, prof in profiles.items():
            s = [score(a, prof, fn, steep) for _, _, a in data]
            scored[(name, pname)] = s
            cells.append(f'{spearman(sl, s):+14.2f}')
        print(f'{name:16}' + ''.join(cells))

    best = scored[('wind_v2+steep', 'off=30')]
    cur = scored[('current', 'off=30')]
    print('\nPer hour, off=30:  surfline | current | wind_v2+steep')
    for (v, k, a), c, b in zip(data, cur, best):
        print(f'  {k:13} {v:>4}  Hs {a[0]:4.2f} {a[1]:>2}s  {a[3]:4.1f}kt@{a[4]:3.0f}'
              f'   {c:5.1f} {rc.bucket_for_score(c)[0]:9}  {b:5.1f} {rc.bucket_for_score(b)[0]}')


if __name__ == '__main__':
    main()
