"""Unit tests for the lone-bar colour despeckle (builders._despeckle).

Run:  python -m unittest discover backend/tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from builders import _despeckle
from rating_config import bucket_for_score


def bars(*scores):
    out = []
    for s in scores:
        rating, color = bucket_for_score(s)
        out.append({'score': s, 'rating': rating, 'color': color})
    return out


def labels(entries):
    return [e['rating'] for e in entries]


class DespeckleTest(unittest.TestCase):
    def test_near_threshold_lone_bar_takes_neighbours_label(self):
        e = bars(31, 27, 30)               # Fair starts at 28
        _despeckle(e)
        self.assertEqual(labels(e), ['Fair'] * 3)
        self.assertEqual(e[1]['score'], 27)  # score untouched

    def test_lone_bar_works_upward(self):
        e = bars(20, 29, 22)
        _despeckle(e)
        self.assertEqual(labels(e), ['Poor to fair'] * 3)

    def test_real_drop_stays(self):
        e = bars(35, 20, 33)               # 8 pts below Fair
        _despeckle(e)
        self.assertEqual(labels(e)[1], 'Poor to fair')

    def test_neighbours_must_agree(self):
        e = bars(31, 27, 26)
        _despeckle(e)
        self.assertEqual(labels(e), ['Fair', 'Poor to fair', 'Poor to fair'])

    def test_two_bar_run_stays(self):
        e = bars(29, 27, 26, 30)
        _despeckle(e)
        self.assertEqual(labels(e)[1:3], ['Poor to fair'] * 2)

    def test_uses_original_labels_not_recoloured_ones(self):
        e = bars(30, 27, 30, 27, 30)       # alternating: both lone 27s recolour
        _despeckle(e)
        self.assertEqual(labels(e), ['Fair'] * 5)


if __name__ == '__main__':
    unittest.main()
