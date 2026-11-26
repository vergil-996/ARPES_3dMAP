import unittest

import numpy as np

from bandscope.core.crop_model import (
    CropSelection,
    apply_crop_regions,
    apply_selection,
    capture_selection_geometry,
    export_cropped_context,
    selection_grid_mask,
)


def _rotate_linear_reference(source, angle):
    source = np.asarray(source, dtype=float)
    nx, ny = source.shape[:2]
    cx, cy = (nx - 1) / 2.0, (ny - 1) / 2.0
    radians = np.deg2rad(angle)
    cosine, sine = np.cos(radians), np.sin(radians)
    output = np.zeros_like(source, dtype=float)
    for x in range(nx):
        for y in range(ny):
            source_x = cx + cosine * (x - cx) + sine * (y - cy)
            source_y = cy - sine * (x - cx) + cosine * (y - cy)
            if not (0 <= source_x <= nx - 1 and 0 <= source_y <= ny - 1):
                continue
            x0, y0 = int(np.floor(source_x)), int(np.floor(source_y))
            x1, y1 = min(x0 + 1, nx - 1), min(y0 + 1, ny - 1)
            fx, fy = source_x - x0, source_y - y0
            output[x, y] = (
                source[x0, y0] * (1 - fx) * (1 - fy)
                + source[x1, y0] * fx * (1 - fy)
                + source[x0, y1] * (1 - fx) * fy
                + source[x1, y1] * fx * fy
            )
    return output


def _creation_mask_reference(selection, shape, current_angle):
    nx, ny, _ = shape
    ranges = {}
    for index, key in enumerate(selection.axes):
        ranges[key] = selection.logical_bounds[2 * index:2 * index + 2]
    if selection.view == "2d":
        ranges["E"] = (0, shape[2] - 1)
    cx, cy = (nx - 1) / 2.0, (ny - 1) / 2.0
    radians = np.deg2rad(selection.rotation_angle - current_angle)
    cosine, sine = np.cos(radians), np.sin(radians)
    mask = np.zeros(shape, dtype=bool)
    for x in range(nx):
        for y in range(ny):
            creation_x = cx + cosine * (x - cx) - sine * (y - cy)
            creation_y = cy + sine * (x - cx) + cosine * (y - cy)
            selected = (
                ranges["X"][0] <= creation_x <= ranges["X"][1]
                and ranges["Y"][0] <= creation_y <= ranges["Y"][1]
            )
            if selected:
                mask[x, y, ranges["E"][0]:ranges["E"][1] + 1] = True
    return mask


def _canvas_reference(shape, current_angle, source_bounds=None):
    nx, ny = shape[:2]
    bounds = source_bounds or (0, nx - 1, 0, ny - 1)
    cx, cy = (nx - 1) / 2.0, (ny - 1) / 2.0
    radians = np.deg2rad(current_angle)
    cosine, sine = np.cos(radians), np.sin(radians)
    mask = np.zeros(shape, dtype=bool)
    for x in range(nx):
        for y in range(ny):
            source_x = cx + cosine * (x - cx) + sine * (y - cy)
            source_y = cy - sine * (x - cx) + cosine * (y - cy)
            if bounds[0] <= source_x <= bounds[1] and bounds[2] <= source_y <= bounds[3]:
                mask[x, y, :] = True
    return mask


def _bounding_slices(mask):
    indices = np.where(mask)
    return tuple(slice(int(values.min()), int(values.max()) + 1) for values in indices)


def _replay_reference(frame, regions, current_angle):
    data = _rotate_linear_reference(frame, current_angle)
    valid = np.ones(frame.shape, dtype=bool)
    canvas = _canvas_reference(frame.shape, current_angle)
    compact = None
    for selection in regions:
        selected = _creation_mask_reference(selection, frame.shape, current_angle)
        if selection.operation == "crop":
            data = np.where(selected, data, 0)
            valid &= selected
            compact = selected if compact is None else compact & selected
        else:
            data = data.copy()
            data[selected] = np.nan
            valid &= ~selected
    if compact is not None:
        slices = _bounding_slices(compact)
        data, valid, canvas = data[slices], valid[slices], canvas[slices]
    return data, valid, canvas


class RotatedCropReplayTests(unittest.TestCase):
    def setUp(self):
        self.shape = (9, 11, 4, 2)
        self.coords = {
            "X": np.arange(9, dtype=float),
            "Y": np.arange(11, dtype=float),
            "E": np.arange(4, dtype=float),
        }
        self.peak_a = (2, 8)
        self.peak_b = (6, 2)
        self.frames = np.zeros(self.shape, dtype=float)
        for time_index in range(2):
            for energy in range(4):
                self.frames[self.peak_a + (energy, time_index)] = 10 + energy + 40 * time_index
                self.frames[self.peak_b + (energy, time_index)] = 100 + 10 * energy + 70 * time_index
        self.crop = CropSelection(
            "3d", ("X", "Y", "E"), (0, 8, 2, 8, 1, 2),
            rotation_angle=30, logical_bounds=(0, 8, 2, 8, 1, 2),
        )
        self.erase = CropSelection(
            "3d", ("X", "Y", "E"), (4, 6, 0, 3, 1, 2), operation="erase",
            rotation_angle=-20, logical_bounds=(4, 6, 0, 3, 1, 2),
        )

    def context(self, time_index, angle):
        return {
            "view": "3d",
            "data": _rotate_linear_reference(self.frames[..., time_index], angle),
            "coords": self.coords,
            "rotation_angle": angle,
        }

    def test_crop_then_erase_replays_each_frame_without_accumulating_rotation(self):
        regions = [self.crop.to_dict(), self.erase.to_dict()]
        first = None
        for time_index in range(2):
            for angle in (30, 0, -20, 30):
                with self.subTest(time_index=time_index, angle=angle):
                    expected, valid, canvas = _replay_reference(
                        self.frames[..., time_index], [self.crop, self.erase], angle
                    )
                    result = apply_crop_regions(self.context(time_index, angle), regions)
                    self.assertFalse(result["crop_empty"])
                    np.testing.assert_equal(result["data"], expected)
                    np.testing.assert_array_equal(result["spatial_valid_mask"], valid)
                    np.testing.assert_array_equal(result["canvas_mask"], canvas)
                    self.assertEqual(result["data_bounds"][4:], (1, 2))
                    np.testing.assert_array_equal(
                        export_cropped_context(result)["E"], self.coords["E"][1:3]
                    )
                    if time_index == 0 and angle == 30:
                        if first is None:
                            first = result
                        else:
                            np.testing.assert_equal(result["data"], first["data"])
                            np.testing.assert_array_equal(
                                result["spatial_valid_mask"], first["spatial_valid_mask"]
                            )

    def test_real_zero_canvas_zero_and_erasure_remain_distinct(self):
        result = apply_crop_regions(
            self.context(0, 30), [self.crop.to_dict(), self.erase.to_dict()]
        )
        data = result["data"]
        valid = result["spatial_valid_mask"]
        canvas = result["canvas_mask"]
        self.assertTrue(np.any(valid & canvas & (data == 0)))
        self.assertTrue(np.any(valid & ~canvas & (data == 0)))
        self.assertTrue(np.any(~valid & np.isnan(data)))
        at_zero = apply_crop_regions(
            self.context(0, 0), [self.crop.to_dict(), self.erase.to_dict()]
        )
        self.assertTrue(np.any(
            ~at_zero["spatial_valid_mask"]
            & np.isfinite(at_zero["data"]) & (at_zero["data"] == 0)
        ))
        export = export_cropped_context(result)
        np.testing.assert_array_equal(export["spatial_valid_mask"], valid)
        np.testing.assert_array_equal(export["canvas_mask"], canvas)

    def test_selection_mask_supports_independent_edc_sums_for_each_frame(self):
        selection = CropSelection(
            "2d", ("X", "Y"), (0, 8, 2, 8), rotation_angle=30,
            logical_bounds=(0, 8, 2, 8),
        )
        for time_index in range(2):
            rotated = _rotate_linear_reference(self.frames[..., time_index], -15)
            actual_mask = selection_grid_mask(
                selection, self.coords, self.shape[:3], rotation_angle=-15
            )
            expected_mask = _creation_mask_reference(selection, self.shape[:3], -15)
            np.testing.assert_array_equal(actual_mask, expected_mask)
            actual = np.asarray([
                np.sum(rotated[:, :, energy][actual_mask[:, :, energy]])
                for energy in range(self.shape[2])
            ])
            expected = np.asarray([
                np.sum(rotated[:, :, energy][expected_mask[:, :, energy]])
                for energy in range(self.shape[2])
            ])
            np.testing.assert_allclose(actual, expected)

    def test_e_collapse_then_rotated_2d_erase_uses_the_same_full_grid(self):
        collapse = CropSelection(
            "3d", ("X", "Y", "E"), (0, 8, 2, 8, 1, 1),
            rotation_angle=30, logical_bounds=(0, 8, 2, 8, 1, 1),
        )
        erase = CropSelection(
            "2d", ("X", "Y"), (4, 6, 0, 3), operation="erase",
            rotation_angle=-20, logical_bounds=(4, 6, 0, 3),
        )
        expected, valid, _canvas = _replay_reference(
            self.frames[..., 1], [collapse, erase], 0
        )
        result = apply_crop_regions(self.context(1, 0), [collapse, erase], rotation_angle=0)
        self.assertEqual(result["view"], "2d")
        self.assertEqual(result["slice_info"]["index"], 1)
        np.testing.assert_equal(result["data"], expected[:, :, 0])
        np.testing.assert_array_equal(result["spatial_valid_mask"], valid[:, :, 0])

    def test_axis_slice_replay_includes_its_normal_range_in_xy_geometry(self):
        selection = CropSelection(
            "2d", ("X", "E"), (0, 8, 1, 2), rotation_angle=30,
            logical_bounds=(0, 8, 1, 2),
        )
        volume_selection = CropSelection(
            "3d", ("X", "Y", "E"), (0, 8, 5, 5, 1, 2), rotation_angle=30,
            logical_bounds=(0, 8, 5, 5, 1, 2),
        )
        full_mask = selection_grid_mask(
            selection, self.coords, self.shape[:3], rotation_angle=0,
            normal_index_bounds=(5, 5),
        )
        expected_mask = _creation_mask_reference(volume_selection, self.shape[:3], 0)
        np.testing.assert_array_equal(full_mask, expected_mask)
        rotated = _rotate_linear_reference(self.frames[..., 0], 0)
        context = {
            "view": "2d", "data": rotated[:, 5, :], "coords": self.coords,
            "slice_info": {"axis": 1, "index": 5}, "rotation_angle": 0,
        }
        result = apply_selection(context, selection, rotation_angle=0)
        local_mask = full_mask[:, 5, :]
        expected_local = np.zeros((self.shape[0], self.shape[2]), dtype=bool)
        expected_local[4, 1:3] = True
        self.assertTrue(np.any(full_mask))
        np.testing.assert_array_equal(local_mask, expected_local)
        slices = _bounding_slices(local_mask)
        np.testing.assert_equal(
            result["data"], np.where(local_mask, rotated[:, 5, :], 0)[slices]
        )

    def test_normal_bounds_accept_range_only_integral_and_legacy_index_contexts(self):
        rotated = _rotate_linear_reference(self.frames[..., 0], 30)
        cases = (
            (
                "range-only integral",
                {"axis": 1, "mode": "integral", "range": (1, 3)},
                np.sum(rotated[:, 1:4, :], axis=1),
            ),
            (
                "single index",
                {"axis": 1, "index": 2},
                rotated[:, 2, :],
            ),
        )
        for name, slice_info, data in cases:
            with self.subTest(context=name):
                context = {
                    "view": "2d", "data": data, "coords": self.coords,
                    "slice_info": slice_info, "rotation_angle": 30,
                }
                selection = CropSelection("2d", ("X", "E"), (2, 6, 1, 2))
                captured = capture_selection_geometry(context, selection)
                self.assertEqual(captured.rotation_angle, 30)
                self.assertEqual(captured.logical_bounds, (2, 6, 1, 2))
                result = apply_selection(context, captured)
                self.assertFalse(result["crop_empty"])
                np.testing.assert_equal(result["data"], data[2:7, 1:3])
                self.assertEqual(
                    result["plot_logical_bounds"],
                    {"x_low": 2, "x_up": 6, "y_low": 1, "y_up": 2},
                )

    def test_axis_only_legacy_context_keeps_unangled_zero_degree_record(self):
        data = self.frames[:, :, 0, 0]
        context = {
            "view": "2d", "data": data, "coords": self.coords,
            "slice_info": {"axis": 2}, "rotation_angle": 0,
        }
        selection = CropSelection("2d", ("X", "Y"), (2, 6, 1, 5))
        captured = capture_selection_geometry(context, selection)
        self.assertEqual(captured, selection)
        self.assertIsNone(captured.rotation_angle)
        self.assertIsNone(captured.logical_bounds)
        result = apply_selection(context, captured)
        np.testing.assert_array_equal(result["data"], data[2:7, 1:6])

    def test_capture_roundtrip_and_descending_energy_keep_absolute_ranges(self):
        coords = dict(self.coords, E=np.asarray([2.0, 1.0, -1.0, -2.0]))
        context = {
            "view": "3d", "data": self.frames[..., 0], "coords": coords,
            "rotation_angle": 12.5,
        }
        selection = CropSelection(
            "3d", ("X", "Y", "E"), (0, 8, 2, 8, -1, 1), e_flip=True
        )
        captured = capture_selection_geometry(context, selection)
        self.assertEqual(captured.rotation_angle, 12.5)
        self.assertEqual(captured.logical_bounds, (0, 8, 2, 8, 1, 2))
        self.assertEqual(CropSelection.from_dict(captured.to_dict()), captured)
        result = apply_selection(context, captured)
        self.assertEqual(result["data_bounds"], (0, 8, 2, 8, 1, 2))
        np.testing.assert_array_equal(export_cropped_context(result)["E"], [1, -1])
        np.testing.assert_equal(result["data"], self.frames[0:9, 2:9, 1:3, 0])

    def test_off_center_source_roi_uses_full_rotation_center_and_source_canvas(self):
        source_bounds = (2, 8, 1, 8, 1, 2)
        embedded = np.zeros(self.shape[:3], dtype=float)
        embedded[2:9, 1:9, 1:3] = self.frames[2:9, 1:9, 1:3, 0]
        rotated = _rotate_linear_reference(embedded, 30)
        selection = CropSelection(
            "3d", ("X", "Y", "E"), (0, 8, 0, 10, 0, 3),
            rotation_angle=30, logical_bounds=(0, 8, 0, 10, 0, 3),
        )
        context = {
            "view": "3d", "data": rotated, "coords": self.coords,
            "rotation_angle": 30, "source_data_bounds": source_bounds,
        }
        result = apply_selection(context, selection)
        expected_canvas = _canvas_reference(self.shape[:3], 30, source_bounds[:4])
        expected_canvas[:, :, 0] = False
        expected_canvas[:, :, 3] = False
        np.testing.assert_array_equal(result["canvas_mask"], expected_canvas)
        np.testing.assert_equal(result["data"], rotated)
        self.assertTrue(np.any(result["spatial_valid_mask"] & expected_canvas & (rotated == 0)))
        self.assertTrue(np.any(result["spatial_valid_mask"] & ~expected_canvas & (rotated == 0)))

    def test_legacy_selection_ignores_current_angle_and_compact_roi_stays_compact(self):
        compact = self.frames[1:7, 2:9, 1:3, 0]
        context = {
            "view": "3d", "data": compact, "coords": self.coords,
            "data_bounds": (1, 6, 2, 8, 1, 2), "full_shape": self.shape[:3],
            "rotation_angle": 30,
        }
        legacy = CropSelection(
            "3d", ("X", "Y", "E"), (2, 5, 3, 7, 1, 2)
        ).to_dict()
        legacy.pop("rotation_angle")
        result = apply_crop_regions(context, [legacy])
        np.testing.assert_array_equal(result["data"], compact[1:5, 1:6])
        self.assertEqual(result["data_bounds"], (2, 5, 3, 7, 1, 2))
        self.assertTrue(np.shares_memory(result["data"], compact))
        self.assertNotIn("spatial_valid_mask", result)


if __name__ == "__main__":
    unittest.main()
