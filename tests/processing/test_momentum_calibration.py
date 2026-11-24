import unittest
from dataclasses import FrozenInstanceError

import numpy as np

from bandscope.processing.momentum_calibration import calibrate_momentum_axes


class MomentumCalibrationTests(unittest.TestCase):
    def test_fractional_points_interpolate_nonuniform_signed_axes(self):
        x = np.array([-2.0, 1.0, 5.0])
        y = np.array([10.0, 4.0, -8.0])
        original_x = x.copy()
        original_y = y.copy()

        result = calibrate_momentum_axes(
            x, y, (0.25, 0.5), (1.5, 1.25), 20.0, unit="Å⁻¹"
        )

        self.assertEqual(result.point_a, (0.25, 0.5))
        self.assertEqual(result.point_b, (1.5, 1.25))
        self.assertEqual(result.unit, "Å⁻¹")
        self.assertAlmostEqual(result.source_distance, np.hypot(4.25, -6.0))
        self.assertEqual(result.target_distance, 20.0)
        self.assertAlmostEqual(result.scale, 20.0 / result.source_distance)
        np.testing.assert_allclose(result.x, original_x * result.scale)
        np.testing.assert_allclose(result.y, original_y * result.scale)
        np.testing.assert_array_equal(x, original_x)
        np.testing.assert_array_equal(y, original_y)

    def test_descending_axes_keep_direction_zero_and_nonuniform_spacing(self):
        x = np.array([8.0, 3.0, -1.0, -9.0])
        y = np.array([-2.0, -4.0, -7.0])

        result = calibrate_momentum_axes(x, y, (0.5, 1.25), (2.5, 0.5), 30.0)

        expected_source = np.hypot(-10.5, 1.75)
        self.assertAlmostEqual(result.source_distance, expected_source)
        np.testing.assert_allclose(result.x, x * result.scale)
        np.testing.assert_allclose(result.y, y * result.scale)
        np.testing.assert_allclose(
            np.diff(result.x) / np.diff(x), np.full(3, result.scale)
        )
        np.testing.assert_allclose(
            np.diff(result.y) / np.diff(y), np.full(2, result.scale)
        )
        self.assertTrue(np.all(np.diff(result.x) < 0))
        self.assertTrue(np.all(np.diff(result.y) < 0))
        self.assertEqual(result.unit, "")

    def test_uniform_scale_also_preserves_ascending_direction_and_zero(self):
        result = calibrate_momentum_axes(
            [-4.0, 0.0, 2.0, 9.0], [-3.0, 0.0, 6.0], (0, 0), (3, 2), 5.0
        )

        self.assertAlmostEqual(result.source_distance, np.hypot(13.0, 9.0))
        self.assertEqual(result.x[1], 0.0)
        self.assertEqual(result.y[1], 0.0)
        self.assertTrue(np.all(np.diff(result.x) > 0))
        self.assertTrue(np.all(np.diff(result.y) > 0))
        np.testing.assert_allclose(result.y, np.array([-3.0, 0.0, 6.0]) * result.scale)

    def test_result_and_coordinate_arrays_are_immutable(self):
        result = calibrate_momentum_axes([0, 1], [0, 1], (0, 0), (1, 1), 2.0)

        with self.assertRaises(FrozenInstanceError):
            result.scale = 3.0
        with self.assertRaises(ValueError):
            result.x[0] = 3.0
        with self.assertRaises(ValueError):
            result.y[0] = 3.0
        self.assertIsInstance(result.point_a, tuple)
        self.assertIsInstance(result.point_b, tuple)

    def test_single_sample_axis_uses_its_only_coordinate(self):
        result = calibrate_momentum_axes([7.0], [-2.0, 4.0], (0, 0), (0, 1), 12.0)

        self.assertEqual(result.source_distance, 6.0)
        self.assertEqual(result.scale, 2.0)
        np.testing.assert_array_equal(result.x, [14.0])
        np.testing.assert_array_equal(result.y, [-4.0, 8.0])

    def test_non_finite_axes_and_points_are_rejected(self):
        cases = (
            (([0.0, np.nan], [0.0, 1.0], (0, 0), (1, 1), 1.0)),
            (([0.0, 1.0], [0.0, np.inf], (0, 0), (1, 1), 1.0)),
            (([0.0, 1.0], [0.0, 1.0], (np.nan, 0), (1, 1), 1.0)),
            (([0.0, 1.0], [0.0, 1.0], (0, 0), (1, -np.inf), 1.0)),
        )
        for arguments in cases:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                calibrate_momentum_axes(*arguments)

    def test_invalid_target_distances_are_rejected(self):
        for target in (np.nan, np.inf, -np.inf, 0.0, -1.0):
            with self.subTest(target=target), self.assertRaises(ValueError):
                calibrate_momentum_axes([0, 1], [0, 1], (0, 0), (1, 1), target)

    def test_coincident_logical_and_physical_points_are_rejected(self):
        with self.assertRaises(ValueError):
            calibrate_momentum_axes([0, 1], [0, 1], (0.5, 0.5), (0.5, 0.5), 1.0)
        with self.assertRaises(ValueError):
            calibrate_momentum_axes([2, 2], [0, 1], (0, 0), (1, 0), 1.0)

    def test_out_of_bounds_points_are_rejected(self):
        points = (
            ((-0.01, 0), (1, 1)),
            ((2.01, 0), (1, 1)),
            ((0, -0.01), (1, 1)),
            ((0, 1.01), (1, 1)),
            ((0, 0), (-1, 1)),
            ((0, 0), (1, 2)),
        )
        for point_a, point_b in points:
            with self.subTest(point_a=point_a, point_b=point_b), self.assertRaises(ValueError):
                calibrate_momentum_axes([0, 1, 2], [0, 1], point_a, point_b, 1.0)

    def test_empty_or_malformed_inputs_are_rejected(self):
        cases = (
            ([], [0, 1], (0, 0), (0, 1), 1.0),
            ([0, 1], [], (0, 0), (1, 0), 1.0),
            ([0, 1], [0, 1], (0,), (1, 1), 1.0),
            ([0, 1], [0, 1], (0, 0), (1, 1, 1), 1.0),
            ([0, 1], [0, 1], (0, 0), (1, 1), "invalid"),
        )
        for arguments in cases:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                calibrate_momentum_axes(*arguments)


if __name__ == "__main__":
    unittest.main()
