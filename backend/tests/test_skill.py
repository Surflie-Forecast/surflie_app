"""Unit tests for the forecast-skill scoring job (backend/skill.py).

Simulates two pipeline days with a synthetic store dump: day 1 archives a
forecast, day 2 scores its matured points against the nowcast truth.

Run:  python -m unittest discover backend/tests
"""
import csv
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import skill


class FakeSpot:
    slug = 'testspot'


def _write_dump(path, rows):
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['spot', 'ts_utc', 'variable',
                                          'value', 'source'])
        w.writeheader()
        for ts, var, val, src in rows:
            w.writerow({'spot': 'testspot', 'ts_utc': ts.isoformat(),
                        'variable': var, 'value': val, 'source': src})


class SkillScoring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.api = os.path.join(self.tmp.name, 'api')
        self._orig = (skill.STORE_DUMP, skill.SKILL_DIR)
        skill.STORE_DUMP = os.path.join(self.tmp.name, 'store.csv')
        skill.SKILL_DIR = os.path.join(self.tmp.name, 'skill')

    def tearDown(self):
        skill.STORE_DUMP, skill.SKILL_DIR = self._orig
        self.tmp.cleanup()

    def test_archive_then_score(self):
        day1 = datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc)
        day2 = day1 + timedelta(days=1)
        hours = [day1 + timedelta(hours=h) for h in (6, 12, 30, 36)]

        # Day 1: forecast rows only (nothing matured yet)
        _write_dump(skill.STORE_DUMP,
                    [(ts, 'wave_height', 1.0 + 0.1 * i, 'cdip:mop-transect-fc')
                     for i, ts in enumerate(hours)])
        scored = skill.score_spots([FakeSpot()], self.api, now=day1)
        self.assertEqual(scored, 0)

        # Day 2: nowcast truth now covers the first two forecast hours
        _write_dump(skill.STORE_DUMP,
                    [(hours[0], 'wave_height', 0.8, 'cdip:mop-nowcast'),
                     (hours[1], 'wave_height', 1.3, 'cdip:mop-nowcast')])
        scored = skill.score_spots([FakeSpot()], self.api, now=day2)
        self.assertEqual(scored, 2)

        out = json.load(open(os.path.join(self.api, 'skill', 'testspot.json')))
        rows = out['sources']['cdip:mop-transect-fc']
        self.assertEqual(len(rows), 1)          # both points are lead-day 0
        self.assertEqual(rows[0]['lead_days'], 0)
        self.assertEqual(rows[0]['n'], 2)
        # errors: 1.0-0.8=+0.2 and 1.1-1.3=-0.2 -> bias 0, rmse 0.2
        self.assertAlmostEqual(rows[0]['bias'], 0.0, places=3)
        self.assertAlmostEqual(rows[0]['rmse'], 0.2, places=3)

        # Day 2 again with the same truth: watermark stops double counting
        scored = skill.score_spots([FakeSpot()], self.api, now=day2)
        self.assertEqual(scored, 0)

    def test_old_runs_pruned(self):
        day1 = datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc)
        _write_dump(skill.STORE_DUMP,
                    [(day1 + timedelta(hours=6), 'wave_height', 1.0,
                      'openmeteo:gfswave016')])
        skill.score_spots([FakeSpot()], self.api, now=day1)
        later = day1 + timedelta(days=skill.MAX_RUN_AGE_DAYS + 1)
        _write_dump(skill.STORE_DUMP, [])
        skill.score_spots([FakeSpot()], self.api, now=later)
        state = json.load(
            open(os.path.join(skill.SKILL_DIR, 'testspot.json')))
        self.assertEqual(state['runs'], {})


if __name__ == '__main__':
    unittest.main()
