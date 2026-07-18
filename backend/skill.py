"""Forecast-skill scoring: each wave forecast vs the observed-driven nowcast.

The CDIP MOP nowcast at a spot's matched transect is driven by measured
buoy spectra — the closest thing to ground truth at a spot with no sensor.
This job accumulates, per spot / forecast source / lead-time bucket, the
bias and RMSE of forecast Hs against that truth. It is the evidence base
for promoting better nearshore sources (e.g. the surf_nearshore product)
from overlay to primary display.

How it works, incrementally across daily runs:

  1. Archive: the first run of each UTC day snapshots every forecast
     source's future wave_height points from the store dump into
     data/skill/<slug>.json under a "<source>|<date>" key.
  2. Score: forecast points that have since matured (their valid time now
     lies inside the nowcast's trailing window) are compared against the
     truth series at exactly matching timestamps; each error lands in the
     issue-relative lead-day bucket. A per-run scored-until watermark
     guarantees each point is counted once.
  3. Publish: api/skill/<slug>.json with n / bias / rmse per source and
     lead day, for inspection and any future UI.

Runs after the builders inside the pipeline (best-effort, never raises out).
State lives in data/skill/ — persisted across CI runs via the actions
cache, like the MOP forecast cache.
"""
import csv
import json
import math
import os
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_DUMP = os.path.join(BASE_DIR, '..', 'data', 'store.csv')
SKILL_DIR = os.path.join(BASE_DIR, '..', 'data', 'skill')

TRUTH_SOURCE = 'cdip:mop-nowcast'
MAX_RUN_AGE_DAYS = 20   # > the longest forecast horizon (16 d) + slack


def _wave_series():
    """{(spot, source): {ts_iso: hs}} for every wave_height row in the dump."""
    out = {}
    with open(STORE_DUMP, newline='') as f:
        for r in csv.DictReader(f):
            if r['variable'] != 'wave_height':
                continue
            out.setdefault((r['spot'], r['source']), {})[r['ts_utc']] = \
                float(r['value'])
    return out


def _state_path(slug):
    return os.path.join(SKILL_DIR, f'{slug}.json')


def _load_state(slug):
    try:
        with open(_state_path(slug)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'runs': {}, 'buckets': {}}


def _archive(state, slug, series, now):
    """Snapshot each forecast source's future points, once per UTC day."""
    today = now.date().isoformat()
    for (spot, source), points in series.items():
        if spot != slug or source == TRUTH_SOURCE:
            continue
        key = f'{source}|{today}'
        if key in state['runs']:
            continue
        future = {ts: v for ts, v in points.items()
                  if datetime.fromisoformat(ts) > now}
        if future:
            state['runs'][key] = {
                'issued': now.isoformat(), 'scored_until': '', 'ts': future}


def _score(state, truth):
    """Fold newly matured forecast points into the lead-day buckets."""
    scored = 0
    for key, run in state['runs'].items():
        source = key.split('|', 1)[0]
        issued = datetime.fromisoformat(run['issued'])
        watermark = run['scored_until']
        for ts in sorted(run['ts']):
            if ts <= watermark or ts not in truth:
                continue
            err = run['ts'][ts] - truth[ts]
            lead = str(int((datetime.fromisoformat(ts) - issued)
                           .total_seconds() // 86400))
            bucket = state['buckets'].setdefault(source, {}).setdefault(
                lead, [0, 0.0, 0.0])
            bucket[0] += 1
            bucket[1] += err
            bucket[2] += err * err
            run['scored_until'] = max(run['scored_until'], ts)
            scored += 1
    return scored


def _prune(state, now):
    for key in list(state['runs']):
        issued = datetime.fromisoformat(state['runs'][key]['issued'])
        if (now - issued).days > MAX_RUN_AGE_DAYS:
            del state['runs'][key]


def _summary(state):
    return {
        source: [
            {'lead_days': int(lead), 'n': n,
             'bias': round(s / n, 3), 'rmse': round(math.sqrt(sq / n), 3)}
            for lead, (n, s, sq) in sorted(
                buckets.items(), key=lambda kv: int(kv[0]))
            if n
        ]
        for source, buckets in state['buckets'].items()
    }


def score_spots(spots, api_dir, now=None):
    """Archive + score + publish for every spot. Returns points scored."""
    now = now or datetime.now(timezone.utc)
    series = _wave_series()
    os.makedirs(SKILL_DIR, exist_ok=True)
    total = 0
    for spot in spots:
        state = _load_state(spot.slug)
        _archive(state, spot.slug, series, now)
        truth = series.get((spot.slug, TRUTH_SOURCE), {})
        scored = _score(state, truth) if truth else 0
        _prune(state, now)
        with open(_state_path(spot.slug), 'w') as f:
            json.dump(state, f, separators=(',', ':'))

        summary = _summary(state)
        if summary:
            out = os.path.join(api_dir, 'skill', f'{spot.slug}.json')
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, 'w') as f:
                json.dump({'truth': TRUTH_SOURCE,
                           'updated_utc': now.isoformat(),
                           'sources': summary}, f, separators=(',', ':'))
        print(f"  {spot.slug}: {len(state['runs'])} archived runs, "
              f"+{scored} points scored")
        total += scored
    return total


if __name__ == '__main__':
    import sys
    sys.path.insert(0, BASE_DIR)
    from spots import load_spots
    score_spots(
        load_spots(os.path.join(BASE_DIR, '..', 'data', 'locations.csv')),
        os.path.join(BASE_DIR, '..', 'api'))
