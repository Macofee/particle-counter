import unittest

import cv2
import numpy as np

from engine import _bin_index, _maximum_feret_diameter


class MaximumFeretTests(unittest.TestCase):
    def test_uses_diagonal_instead_of_bounding_box_side(self):
        contour = np.array([[[0, 0]], [[3, 0]], [[3, 4]], [[0, 4]]], dtype=np.int32)

        self.assertAlmostEqual(_maximum_feret_diameter(contour), 6.0)

    def test_single_pixel_has_one_pixel_extent(self):
        contour = np.array([[[8, 12]]], dtype=np.int32)

        self.assertEqual(_maximum_feret_diameter(contour), 1.0)

    def test_rotating_calipers_matches_brute_force_for_random_hulls(self):
        random = np.random.default_rng(20260627)
        for _ in range(100):
            points = random.integers(0, 500, size=(40, 1, 2), dtype=np.int32)
            hull = cv2.convexHull(points).reshape(-1, 2).astype(np.float64)
            deltas = hull[:, np.newaxis, :] - hull[np.newaxis, :, :]
            expected = np.sqrt(np.max(np.sum(deltas * deltas, axis=2))) + 1.0

            self.assertAlmostEqual(_maximum_feret_diameter(points), expected)


class BinBoundaryTests(unittest.TestCase):
    def test_upper_boundary_stays_in_lower_bin(self):
        self.assertEqual(_bin_index(50.0), 0)
        self.assertEqual(_bin_index(100.0), 1)
        self.assertEqual(_bin_index(200.0), 2)

    def test_value_above_boundary_moves_to_next_bin(self):
        self.assertEqual(_bin_index(50.01), 1)
        self.assertEqual(_bin_index(200.01), 3)


if __name__ == "__main__":
    unittest.main()
