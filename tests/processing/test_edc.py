import unittest

import numpy as np

from bandscope.core.crop_model import CropSelection
from bandscope.core.data_scope import DataScopeDescriptor, ScopedSpatialArray
from bandscope.processing.edc import integrate_edc, selection_index_bounds


class SelectionIndexBoundsTests(unittest.TestCase):
    def setUp(self):
        self.shape = (4, 3, 4)
        self.coords = {
            "X": np.arange(4, dtype=float),
            "Y": np.arange(3, dtype=float),
            "E": np.array([3.0, 1.0, 0.2, -1.0]),
        }

    def test_3d_nonuniform_descending_ranges_use_outward_rounding(self):
        selection = CropSelection(
            "3d", ("X", "Y", "E"), (0.2, 2.2, 0.2, 1.2, 0.4, 2.0)
        )
        self.assertEqual(
            selection_index_bounds(selection, self.coords, self.shape),
            (0, 3, 0, 2, 0, 2),
        )

    def test_2d_xy_selection_uses_full_energy_and_supports_swapped_axes(self):
        selection = CropSelection(
            "2d", ("Y", "X"), (0.2, 1.2, 1.2, 2.2)
        )
        self.assertEqual(
            selection_index_bounds(
                selection, {"X": self.coords["X"], "Y": self.coords["Y"]}, self.shape
            ),
            (1, 3, 0, 2, 0, 3),
        )

    def test_equal_3d_energy_limits_select_nearest_single_sample(self):
        selection = CropSelection(
            "3d", ("X", "Y", "E"), (0.0, 2.0, 0.0, 1.0, 1.4, 1.4)
        )
        self.assertEqual(
            selection_index_bounds(selection, self.coords, self.shape)[4:],
            (1, 1),
        )

    def test_partially_outside_selection_is_clamped_and_disjoint_is_none(self):
        partial = CropSelection(
            "3d", ("X", "Y", "E"), (-2.0, 1.2, 0.0, 1.0, 0.2, 1.0)
        )
        self.assertEqual(
            selection_index_bounds(partial, self.coords, self.shape)[:2],
            (0, 2),
        )
        disjoint = CropSelection(
            "3d", ("X", "Y", "E"), (10.0, 11.0, 0.0, 1.0, 0.2, 1.0)
        )
        self.assertIsNone(selection_index_bounds(disjoint, self.coords, self.shape))

    def test_invalid_axes_shapes_and_coordinates_raise_value_error(self):
        invalid_axis = CropSelection("2d", ("X", "E"), (0.0, 1.0, 0.0, 1.0))
        valid = CropSelection(
            "3d", ("X", "Y", "E"), (0.0, 1.0, 0.0, 1.0, 0.2, 1.0)
        )
        with self.assertRaises(ValueError):
            selection_index_bounds(invalid_axis, self.coords, self.shape)
        for shape in ((4, 3), (4, 3, 4, 1), (0, 3, 4), (4.5, 3, 4)):
            with self.subTest(shape=shape), self.assertRaises(ValueError):
                selection_index_bounds(valid, self.coords, shape)
        with self.assertRaises(ValueError):
            selection_index_bounds(
                valid, dict(self.coords, E=[3.0, 1.0, 2.0, -1.0]), self.shape
            )


class EdcIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.data = np.arange(4 * 3 * 5, dtype=np.float32).reshape(4, 3, 5)
        self.energy = np.array([2.0, 0.5, 0.0, -0.5, -2.0])

    def test_regular_integration_limits_energy_and_preserves_coordinate_order(self):
        result = integrate_edc(self.data, self.energy, (1, 2, 0, 1, 1, 3))
        expected = np.sum(self.data[1:3, 0:2, 1:4], axis=(0, 1), dtype=np.float64)
        np.testing.assert_allclose(result.intensity, expected)
        np.testing.assert_array_equal(result.energy, [0.5, 0.0, -0.5])
        self.assertEqual(result.index_bounds, (1, 2, 0, 1, 1, 3))
        self.assertEqual(result.intensity.dtype, np.float64)

    def test_multiple_selections_are_independent_calls(self):
        selections = (
            CropSelection("3d", ("X", "Y", "E"), (0, 1, 0, 1, 0.0, 0.5)),
            CropSelection("3d", ("X", "Y", "E"), (2, 3, 1, 2, -2.0, -0.5)),
        )
        coords = {"X": np.arange(4), "Y": np.arange(3), "E": self.energy}
        results = []
        for selection in selections:
            bounds = selection_index_bounds(selection, coords, self.data.shape)
            results.append(integrate_edc(self.data, self.energy, bounds))
        np.testing.assert_allclose(
            results[0].intensity,
            np.sum(self.data[0:2, 0:2, 1:3], axis=(0, 1), dtype=np.float64),
        )
        np.testing.assert_allclose(
            results[1].intensity,
            np.sum(self.data[2:4, 1:3, 3:5], axis=(0, 1), dtype=np.float64),
        )
        np.testing.assert_array_equal(results[0].energy, [0.5, 0.0])
        np.testing.assert_array_equal(results[1].energy, [-0.5, -2.0])

    def test_single_energy_point_returns_one_float64_value(self):
        result = integrate_edc(self.data, self.energy, (1, 3, 0, 2, 2, 2))
        np.testing.assert_allclose(
            result.intensity,
            np.sum(self.data[1:4, :, 2:3], axis=(0, 1), dtype=np.float64),
        )
        np.testing.assert_array_equal(result.energy, [0.0])
        self.assertEqual(result.intensity.shape, (1,))

    def test_scoped_volume_intersects_partial_roi_in_absolute_indices(self):
        descriptor = DataScopeDescriptor(
            "roi", (5, 4, 6, 1), (1, 3, 1, 3, 2, 5)
        )
        compact = np.arange(3 * 3 * 4, dtype=float).reshape(3, 3, 4)
        scoped = ScopedSpatialArray(compact, descriptor)
        energy = np.array([5.0, 4.0, 3.0, 2.0, 1.0, 0.0])
        result = integrate_edc(scoped, energy, (2, 4, 0, 2, 1, 4))
        np.testing.assert_allclose(
            result.intensity,
            np.sum(compact[1:3, 0:2, 0:3], axis=(0, 1), dtype=np.float64),
        )
        np.testing.assert_array_equal(result.energy, [3.0, 2.0, 1.0])
        self.assertEqual(result.index_bounds, (2, 3, 1, 2, 2, 4))

    def test_disjoint_roi_returns_none_for_full_and_scoped_volumes(self):
        self.assertIsNone(integrate_edc(self.data, self.energy, (8, 9, 0, 1, 0, 4)))
        descriptor = DataScopeDescriptor(
            "roi", (6, 3, 5, 1), (1, 3, 0, 2, 0, 4)
        )
        scoped = ScopedSpatialArray(np.ones((3, 3, 5)), descriptor)
        self.assertIsNone(integrate_edc(scoped, self.energy, (4, 5, 0, 2, 0, 4)))
        self.assertIsNone(integrate_edc(scoped, self.energy, (1, 3, 0, 2, 5, 5)))

    def test_all_nan_single_energy_slice_returns_nan_when_ignoring_nan(self):
        data = np.ones((2, 2, 3), dtype=float)
        data[:, :, 1] = np.nan
        result = integrate_edc(data, np.arange(3), (0, 1, 0, 1, 1, 1))
        np.testing.assert_allclose(result.intensity, [np.nan])

    def test_all_nan_selection_returns_nan_for_every_energy(self):
        data = np.full((2, 2, 3), np.nan)
        result = integrate_edc(data, np.arange(3), (0, 1, 0, 1, 0, 2))
        np.testing.assert_allclose(result.intensity, [np.nan, np.nan, np.nan])

    def test_ignore_nan_sums_partial_valid_samples(self):
        data = np.ones((2, 2, 3), dtype=float)
        data[0, 0, 0] = np.nan
        data[1, 1, 1] = np.nan
        result = integrate_edc(
            data, np.arange(3), (0, 1, 0, 1, 0, 2), ignore_nan=True
        )
        np.testing.assert_allclose(result.intensity, [3.0, 3.0, 4.0])

    def test_sum_without_ignore_nan_propagates_nan(self):
        data = np.ones((2, 2, 3), dtype=float)
        data[0, 0, 0] = np.nan
        data[:, :, 1] = np.nan
        result = integrate_edc(
            data, np.arange(3), (0, 1, 0, 1, 0, 2), ignore_nan=False
        )
        np.testing.assert_allclose(result.intensity, [np.nan, np.nan, 4.0])

    def test_invalid_volume_energy_and_bounds_raise_value_error(self):
        with self.assertRaises(ValueError):
            integrate_edc(np.ones((2, 2)), self.energy, (0, 1, 0, 1, 0, 4))
        with self.assertRaises(ValueError):
            integrate_edc(self.data, self.energy[:-1], (0, 1, 0, 1, 0, 4))
        for bounds in ((0, 1, 0, 1), (1, 0, 0, 1, 0, 4), (0.5, 1, 0, 1, 0, 4)):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                integrate_edc(self.data, self.energy, bounds)


if __name__ == "__main__":
    unittest.main()
