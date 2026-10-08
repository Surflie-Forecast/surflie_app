"""Grid-search the three-zone wind factor (+ storm steepness) against the
Surfline sample, at the MOP-derived offshore bearing for Old Man's (34 deg),
subject to surf-sense guards.

    python data_exploration/quality_labels/sweep_wind.py

n = 24 hours from one spot, and only 4 of them above 15 kt, so the sample
can't say how bad e.g. 17 kt offshore on a clean swell should be. GUARDS
encode those cases as label constraints on a reference hour (1.5 m, 14 s,
swell from the ideal direction: energy x direction = 0.85); a config must
pass every guard, and Spearman vs Surfline picks among the survivors.

The report shows the top configs, the share of near-best configs using each
value (a winner piled at a grid edge means the range should widen), the
modal config, and leave-one-day-out validation.

v1 (first run) offered strong_span 8/12/16 with a fixed 0.4 floor; 98% of
near-best configs took the steepest option, i.e. the grid edge. v2 widens
those ranges and adds the guards.
"""
import itertools
import os
import sys
from collections import Counter
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'backend'))
import rating_config as rc  # noqa: E402
from candidates import rows, spearman  # noqa: E402

OFFSHORE = 34.0
GRID = {
    'full':         [20, 30, 40],             # deg off offshore with whole boost
    'ramp':         [15, 30],                 # deg over which boost fades to 0
    'cross_end':    [90, 100, 120],           # deg where onshore ramp starts
    'cross_pen':    [0.15, 0.3, 0.5],         # cross-shore penalty, frac of onshore
    'peak_kt':      [6, 8, 10],               # offshore boost peaks here
    'boost_end':    [12, 15, 18],             # offshore boost gone by here
    'boost':        [0.06, 0.12, 0.2],        # max offshore boost
    'onshore_sat':  [10, 12, 15],             # kt where onshore penalty is full strength
    'strong_kt':    [8, 10, 12, 14],          # all-direction penalty starts
    'strong_span':  [3, 4, 5, 6, 8, 10],      # kt from start to the floor
    'floor':        [0.15, 0.2, 0.3, 0.4, 0.5],
    'steep':        [0.010, 0.012, 0.013, 0.015, 0.018],
}
# The app's settings (rating_config.py), applied 2026-10-07 from this sweep.
# Before: cross_end 100, cross_pen 0.3, onshore_sat 15, strong_kt 12, floor 0.4
# (rho +0.898, failed the 17 kt / 25 kt offshore and 15 kt cross guards).
CURRENT = dict(full=30, ramp=30, cross_end=120, cross_pen=0.15, peak_kt=8,
               boost_end=15, boost=0.12, onshore_sat=12, strong_kt=10,
               strong_span=8, floor=0.2, steep=0.013)

# Reference hour: 1.5 m, 14 s from the ideal direction (energy x dir = 0.85).
REF = 0.85
# (description, wind kt, deg off straight offshore, min score, max score)
# Score bands: Poor <22, Fair 22-41, Good 42-69, Excellent 70-91, Epic 92+.
GUARDS = [
    ('light offshore 6 kt -> Excellent+',   6,   0, 70, 101),
    ('glassy 2 kt -> Excellent+',           2,  90, 70, 101),
    ('moderate offshore 12 kt -> Good+',   12,   0, 42, 101),
    ('strong offshore 17 kt -> Fair or less', 17, 0, 0, 42),
    ('gale offshore 25 kt -> Poor',        25,   0, 0, 22),
    ('light cross-shore 6 kt -> Good+',     6,  80, 42, 101),
    ('cross-shore 15 kt -> Fair or less',  15,  80, 0, 42),
    ('light onshore 6 kt -> Good or less',  6, 180, 0, 70),
    ('onshore 10 kt -> Fair or less',      10, 180, 0, 42),
    ('onshore 15 kt -> Poor',              15, 180, 0, 22),
]


def wind(speed, off, c):
    if speed < 3.0:
        return 1.0
    zero = c['full'] + c['ramp']
    if off <= zero:
        a = 1.0 if off <= c['full'] else (zero - off) / c['ramp']
        pk, end = c['peak_kt'], c['boost_end']
        bump = (speed - 3.0) / (pk - 3.0) if speed <= pk else max(0.0, (end - speed) / (end - pk))
        f = 1.0 + c['boost'] * a * bump
    else:
        ce = max(c['cross_end'], zero + 1)
        if off <= ce:
            a = c['cross_pen'] * (off - zero) / (ce - zero)
        else:
            a = c['cross_pen'] + (1 - c['cross_pen']) * (off - ce) / (180.0 - ce)
        strength = min((speed - 3.0) / (c['onshore_sat'] - 3.0), 1.0) ** 0.7
        f = max(0.1, 1.0 - 0.9 * a * strength)
    if speed > c['strong_kt']:
        f *= max(c['floor'], 1.0 - (1.0 - c['floor']) * (speed - c['strong_kt']) / c['strong_span'])
    return f


def steep(h, per, thr):
    s = h / (1.56 * per ** 2)
    return 1.0 if s <= thr else max(0.5, 1.0 - 0.5 * (s - thr) / thr)


# The profile the app builds for Old Man's (generated orientation: offshore
# 34, swell window 176-304, ideal 214), so figures match compare_surfline.py.
from spots import load_spots  # noqa: E402
_SPOT = next(s for s in load_spots(os.path.join(HERE, '..', '..', 'data', 'locations.csv'))
             if s.slug == 'old-man-s-at-tourmaline')
PROFILE = rc.build_profile(_SPOT.profile_meta, coast_normal=_SPOT.coast_normal)
OFFSHORE = PROFILE.offshore_dir
DATA = rows()
PRE = [(v, k, a, rc._energy_factor(a[0], a[1], PROFILE) * rc._direction_factor(a[2], PROFILE),
        rc._ang_diff(a[4], OFFSHORE)) for v, k, a in DATA]
SL = [p[0] for p in PRE]
DAYS = sorted({i // 8 for i in range(len(PRE))})   # 8 three-hourly rows per local day


def scores(c, idx=None):
    idx = range(len(PRE)) if idx is None else idx
    return [PRE[i][3] * wind(PRE[i][2][3], PRE[i][4], c) * steep(PRE[i][2][0], PRE[i][2][1], c['steep'])
            for i in idx]


def guard_fails(c):
    return [g[0] for g in GUARDS if not g[3] <= REF * 100 * wind(g[1], g[2], c) < g[4]]


def evaluate(combo):
    c = dict(zip(GRID, combo))
    if c['boost_end'] <= c['peak_kt'] or guard_fails(c):
        return None
    folds = [spearman([PRE[i][0] for i in tr], scores(c, tr))
             for tr in ([i for i in range(len(PRE)) if i // 8 != d] for d in DAYS)]
    return combo, spearman(SL, scores(c)), folds


def main():
    combos = list(itertools.product(*GRID.values()))
    workers = max(1, (os.cpu_count() or 4) - 2)     # leave 2 cores free
    with Pool(workers) as pool:
        res = [r for r in pool.imap_unordered(evaluate, combos, chunksize=4000) if r]
    print(f'{len(combos)} configs, {len(res)} pass every guard, {workers} workers')

    print(f'\nCurrent config: rho {spearman(SL, scores(CURRENT)):+.3f}, '
          f'fails guards: {guard_fails(CURRENT) or "none"}')

    res.sort(key=lambda r: -r[1])
    top = res[0][1]
    near = [r for r in res if r[1] >= top - 0.01]
    print(f'\nBest passing: {top:+.3f}; {len(near)} configs within 0.01.')
    print('  rho    ' + ' '.join(f'{k:>11}' for k in GRID))
    for combo, rho, _ in res[:8]:
        print(f'  {rho:+.3f} ' + ' '.join(f'{str(v):>11}' for v in combo))

    print('\nShare of near-best configs using each value  (* = grid edge holds the most)')
    modal = {}
    for j, k in enumerate(GRID):
        cnt = Counter(r[0][j] for r in near)
        modal[k] = cnt.most_common(1)[0][0]
        edge = '*' if modal[k] in (GRID[k][0], GRID[k][-1]) and len(GRID[k]) > 2 else ' '
        print(f'  {k:12}{edge} ' + '  '.join(f'{v}: {cnt[v] / len(near):4.0%}' for v in GRID[k]))

    fails = guard_fails(modal)
    print(f'\nModal config: rho {spearman(SL, scores(modal)):+.3f}, guards '
          f'{"all pass" if not fails else "FAIL " + str(fails)}')
    print('  ' + ', '.join(f'{k}={v}' for k, v in modal.items()))

    print('\nGuards on the modal config (reference 1.5 m 14 s at ideal):')
    for g in GUARDS:
        s = REF * 100 * wind(g[1], g[2], modal)
        print(f'  {g[0]:40} score {s:5.1f}  {rc.bucket_for_score(s)[0]}')

    print('\nLeave-one-day-out (best passing config on 2 days, scored on the 3rd):')
    for fi, d in enumerate(DAYS):
        best = max(res, key=lambda r: r[2][fi])
        test = [i for i in range(len(PRE)) if i // 8 == d]
        c = dict(zip(GRID, best[0]))
        print(f'  held-out day {d}: tuned {spearman([PRE[i][0] for i in test], scores(c, test)):+.2f}'
              f'  modal {spearman([PRE[i][0] for i in test], scores(modal, test)):+.2f}'
              f'  current {spearman([PRE[i][0] for i in test], scores(CURRENT, test)):+.2f}')


if __name__ == '__main__':
    main()
