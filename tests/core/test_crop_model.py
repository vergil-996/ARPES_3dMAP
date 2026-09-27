import unittest

import numpy as np

from bandscope.core.crop_model import (CropSelection, apply_crop_regions, apply_selection,
                        export_cropped_context, selection_for_context)


class CropModelTests(unittest.TestCase):
    def setUp(self):
        self.coords = {"X": np.arange(6.), "Y": np.arange(7.), "E": np.arange(8.)}
        self.volume = np.arange(6*7*8, dtype=float).reshape(6, 7, 8)
        self.context = {"view": "3d", "data": self.volume, "coords": self.coords}

    def select(self, view, bounds, axes=None, flip=False):
        axes = axes or (("X", "Y", "E") if view == "3d" else ("X", "E"))
        return CropSelection(view, axes, tuple(bounds), flip)

    def test_volume_crops_without_mutating_or_copying_source(self):
        result = apply_selection(self.context, self.select("3d", (1, 4, 2, 5, 3, 7)))
        np.testing.assert_array_equal(result["data"], self.volume[1:5, 2:6, 3:8])
        self.assertEqual(result["data_bounds"], (1, 4, 2, 5, 3, 7))
        self.assertTrue(np.shares_memory(result["data"], self.volume))
        self.assertNotIn("data_bounds", self.context)

    def test_single_layer_honors_both_other_ranges_and_exports_2d(self):
        result = apply_selection(self.context, self.select("3d", (1, 4, 2, 2, 3, 7)))
        self.assertEqual(result["view"], "2d")
        np.testing.assert_array_equal(result["data"], self.volume[1:5, 2, 3:8])
        self.assertEqual(result["slice_info"]["extent_override"], [1, 4, 3, 7])
        export = export_cropped_context(result)
        np.testing.assert_array_equal(export["kx"], [1, 2, 3, 4])
        np.testing.assert_array_equal(export["E"], [3, 4, 5, 6, 7])

    def test_repeated_volume_crop_uses_absolute_domain(self):
        first = self.select("3d", (1, 4, 2, 5, 3, 7))
        second = self.select("3d", (-100, 3, 3, 100, -100, 5))
        result = apply_crop_regions(self.context, [first.to_dict(), second.to_dict()])
        np.testing.assert_array_equal(result["data"], self.volume[1:4, 3:6, 3:6])
        self.assertEqual(result["data_bounds"], (1, 3, 3, 5, 3, 5))

    def test_ordinary_slice_gets_axis_metadata(self):
        context = {"view": "2d", "data": self.volume[:, 2, :], "slice_info": {"axis": 1, "index": 2}, "coords": self.coords}
        selection = selection_for_context(context)
        self.assertEqual(selection.axes, ("X", "E"))
        result = apply_selection(context, self.select("2d", (1, 4, 3, 6)))
        np.testing.assert_array_equal(result["data"], self.volume[1:5, 2, 3:7])

    def test_descending_axes_and_repeated_2d_crop(self):
        context = {"view": "2d", "data": self.volume[:, 2, :], "slice_info": {"axis": 1, "index": 2},
                   "coords": dict(self.coords, X=self.coords["X"][::-1], E=self.coords["E"][::-1])}
        result = apply_selection(context, self.select("2d", (1, 4, 2, 6)))
        result = apply_selection(result, self.select("2d", (2, 3, 3, 5)))
        np.testing.assert_array_equal(result["data"], self.volume[2:4, 2, 2:5])
        self.assertEqual(result["slice_info"]["extent_override"], [3, 2, 5, 3])

    def test_2d_flip_selection_matches_visible_pixels(self):
        data = self.volume[:, 2, :]
        context = {"view": "2d", "data": data, "slice_info": {"axis": 1, "index": 2}, "coords": self.coords}
        result = apply_selection(context, self.select("2d", (1, 4, 0, 2), flip=True))
        np.testing.assert_array_equal(result["data"][:, ::-1], data[1:5, ::-1][:, :3])

    def test_curve_removes_middle_points_without_bridging_gaps(self):
        context = {"view": "1d", "x_data": np.arange(6.), "y_data": np.array([0, 2, 9, 3, 8, 4]), "xlabel": "Delay (ps)"}
        result = apply_selection(context, self.select("1d", (1, 5, 1, 4)))
        np.testing.assert_array_equal(result["x_data"], [1, 2, 3, 4, 5])
        np.testing.assert_allclose(result["y_data"], [2, np.nan, 3, np.nan, 4], equal_nan=True)
        np.testing.assert_allclose(export_cropped_context(result)["intensity"], result["y_data"], equal_nan=True)

    def test_curve_flip_is_applied_once_and_chain_does_not_restore_removed_points(self):
        context = {"view": "1d", "x_data": np.arange(6.), "y_data": np.arange(6.), "xlabel": "Energy (eV)"}
        result = apply_selection(context, self.select("1d", (0, 3, 3, 5), flip=True))
        np.testing.assert_allclose(result["y_data"][::-1], [5, 4, 3, np.nan], equal_nan=True)
        result = apply_selection(result, self.select("1d", (-2, 9, -2, 9), flip=True))
        self.assertEqual(np.count_nonzero(np.isfinite(result["y_data"])), 3)

    def test_comparison_uses_one_rectangle_for_all_curves(self):
        context = {"view": "1d_comparison", "curves": [
            {"label": "a", "x_data": np.arange(4.), "y_data": np.arange(4.)},
            {"label": "b", "x_data": np.arange(4.), "y_data": np.arange(4.) + 2},
        ]}
        result = apply_selection(context, self.select("1d_comparison", (1, 3, 1, 3)))
        np.testing.assert_allclose(result["curves"][1]["y_data"], [3, np.nan, np.nan], equal_nan=True)
        self.assertEqual(result["curves"][0]["label"], "a")

    def test_waterfall_uses_display_offsets_and_keeps_raw_mask(self):
        context = {"view": "waterfall", "energy_axis": np.arange(4.), "k_values": np.arange(3.), "offset_step": 2,
                   "curves": np.tile([0., .5, 1., .5], (3, 1)), "raw_curves": np.arange(12.).reshape(3, 4)}
        result = apply_selection(context, self.select("waterfall", (2.3, 3.1, 1, 3)))
        np.testing.assert_array_equal(result["curve_offsets"], [0, 2, 4])
        self.assertTrue(np.isnan(result["curves"][[0, 2]]).all())
        np.testing.assert_array_equal(result["raw_curves"][1], [5, 6, 7])
        self.assertTrue(np.isnan(export_cropped_context(result)["intensity"][[0, 2]]).all())

    def test_fixed_region_reapplies_to_new_data_and_can_be_empty(self):
        region = self.select("1d", (1, 4, 0, 3))
        for shift, empty in ((0, False), (100, True)):
            context = {"view": "1d", "x_data": np.arange(6.), "y_data": np.arange(6.) + shift}
            result = apply_crop_regions(context, [region.to_dict()])
            self.assertEqual(result["crop_empty"], empty)
            if empty:
                with self.assertRaises(ValueError):
                    export_cropped_context(result)

    def test_validation_and_no_overlap(self):
        for bounds in ((2, 1, 0, 1), (0, np.inf, 0, 1), (0, 0, 1, 2)):
            with self.assertRaises(ValueError):
                self.select("2d", bounds)
        result = apply_selection(self.context, self.select("3d", (20, 30, 0, 1, 0, 1)))
        self.assertTrue(result["crop_empty"])


if __name__ == "__main__":
    unittest.main()
