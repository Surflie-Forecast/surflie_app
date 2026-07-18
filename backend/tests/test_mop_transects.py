"""Unit tests for spot -> MOP transect selection.

Run:  python -m unittest discover backend/tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import mop_transects as mt


class SelectTransect(unittest.TestCase):
    def test_ventura_spot_gets_ve_transect(self):
        picked = mt.select_transect(34.2741527, -119.2943249)  # C Street
        self.assertIsNotNone(picked)
        self.assertTrue(picked[0].startswith('VE'), picked)
        self.assertLess(picked[1], mt.MAX_DIST_KM)

    def test_sb_channel_spot_gets_b_county_transect(self):
        picked = mt.select_transect(34.40755366, -119.8760267)  # Devereaux
        self.assertIsNotNone(picked)
        self.assertTrue(picked[0].startswith('B0'), picked)

    def test_santa_cruz_spot_gets_sc_transect(self):
        picked = mt.select_transect(36.95313283, -122.0235701)  # Steamer Lane
        self.assertIsNotNone(picked)
        self.assertTrue(picked[0].startswith('SC'), picked)

    def test_inland_and_out_of_state_rejected(self):
        self.assertIsNone(mt.select_transect(39.7, -105.0))    # Denver
        self.assertIsNone(mt.select_transect(39.09, -120.03))  # Lake Tahoe

    def test_wrong_face_candidate_is_skipped(self):
        # A point ~1 km landward (anti-normal) of a transect's backbeach must
        # not snap to it: either no transect fits or the pick's own shore
        # normal points broadly toward the test point.
        table = mt._load_table()
        label, blat, blon, plat, plon, normal = next(
            t for t in table if t[0] == 'B0161')
        rad = mt.math.radians(normal + 180)          # 1 km landward
        lat = blat + (mt.math.cos(rad) / mt.KM_PER_DEG)
        lon = blon + (mt.math.sin(rad)
                      / (mt.KM_PER_DEG * mt.math.cos(mt.math.radians(blat))))
        picked = mt.select_transect(lat, lon)
        if picked is not None:
            _, plat2, plon2, _, _, normal2 = next(
                t for t in table if t[0] == picked[0])
            bearing = mt._bearing(plat2, plon2, lat, lon)
            self.assertLessEqual(mt._angle_diff(bearing, normal2), 90)

    def test_angle_diff_wraps(self):
        self.assertEqual(mt._angle_diff(350, 10), 20)
        self.assertEqual(mt._angle_diff(90, 270), 180)


if __name__ == '__main__':
    unittest.main()
