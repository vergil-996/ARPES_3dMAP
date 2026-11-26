import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication
from scipy.ndimage import rotate as linear_rotate

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.crop_model import CropSelection, apply_crop_regions, selection_grid_mask
from bandscope.processing.compute_backends import CpuComputeBackend
from bandscope.processing.denoise_engines import DenoiseEngines
from bandscope.ui.result_workspace import AnalysisPageSpec
from tests.support.view_recipe import control_window


def _synthetic_data():
    shape = (13, 17, 3)
    x = np.arange(shape[0], dtype=np.float64)[:, None]
    y = np.arange(shape[1], dtype=np.float64)[None, :]
    peak_a = 18.0 * np.exp(-((x - 10.0) ** 2 + (y - 8.0) ** 2) / (2 * 0.48 ** 2))
    peak_b = 11.0 * np.exp(-((x - 2.0) ** 2 + (y - 8.0) ** 2) / (2 * 0.52 ** 2))
    frame = (peak_a + peak_b)[:, :, None] * np.asarray([1.0, 0.5, 1.5])
    data = np.stack([frame, frame * 2.75], axis=-1).astype(np.float32)
    coords = {
        "X": np.arange(shape[0], dtype=np.float64),
        "Y": np.arange(shape[1], dtype=np.float64),
        "E": np.asarray([1.0, 0.25, -0.5]),
        "delay": np.asarray([0.0, 2.0]),
    }
    return data, coords


def _selection(xy_bounds, operation="crop", e_flip=False):
    return CropSelection(
        "3d",
        ("X", "Y", "E"),
        (*xy_bounds, -0.5, 1.0),
        e_flip=e_flip,
        operation=operation,
    )


def _reference_rotation(frame, angle):
    return linear_rotate(
        frame,
        float(angle),
        axes=(0, 1),
        reshape=False,
        order=1,
        mode="constant",
        cval=0.0,
        prefilter=True,
    )


def _reference_mask(shape, logical_bounds, relative_angle):
    x_low, x_up, y_low, y_up, e_low, e_up = logical_bounds
    x = np.arange(shape[0], dtype=np.float64)[:, None]
    y = np.arange(shape[1], dtype=np.float64)[None, :]
    center_x = (shape[0] - 1) / 2.0
    center_y = (shape[1] - 1) / 2.0
    angle = np.deg2rad(relative_angle)
    cosine = np.cos(angle)
    sine = np.sin(angle)
    source_x = center_x + cosine * (x - center_x) + sine * (y - center_y)
    source_y = center_y - sine * (x - center_x) + cosine * (y - center_y)
    tolerance = 1e-9
    xy_mask = (
        (source_x >= x_low - tolerance)
        & (source_x <= x_up + tolerance)
        & (source_y >= y_low - tolerance)
        & (source_y <= y_up + tolerance)
    )
    e_mask = np.zeros(shape[2], dtype=bool)
    e_mask[int(np.floor(e_low)):int(np.ceil(e_up)) + 1] = True
    return xy_mask[:, :, None] & e_mask[None, None, :]


def _reference_result(data, regions, angle, time_index):
    rotated = _reference_rotation(data[..., time_index], angle)
    valid = _reference_mask(
        rotated.shape,
        (0, rotated.shape[0] - 1, 0, rotated.shape[1] - 1, 0, rotated.shape[2] - 1),
        angle,
    )
    for region in regions:
        mask = _reference_mask(
            rotated.shape,
            region["logical_bounds"],
            float(angle) - float(region["rotation_angle"]),
        )
        if region.get("operation", "crop") == "erase":
            valid &= ~mask
        else:
            valid &= mask
    return np.where(valid, rotated, np.nan)


def _reference_crop_components(source, region, angle):
    rotated = _reference_rotation(source, angle)
    selected = _reference_mask(
        rotated.shape,
        region["logical_bounds"],
        float(angle) - float(region["rotation_angle"]),
    )
    canvas = _reference_mask(
        rotated.shape,
        (0, rotated.shape[0] - 1, 0, rotated.shape[1] - 1, 0, rotated.shape[2] - 1),
        angle,
    )
    indices = np.where(selected)
    if not indices[0].size:
        raise AssertionError("The synthetic crop must contain selected samples.")
    slices = tuple(
        slice(int(values.min()), int(values.max()) + 1) for values in indices
    )
    return {
        "data": np.where(selected[slices], rotated[slices], 0),
        "spatial_valid_mask": selected[slices],
        "canvas_mask": canvas[slices],
    }


def _gaussian_methods():
    return [{
        "id": "gaussian",
        "label": DenoiseEngines.GAUSSIAN_METHOD_NAME,
        "params": {"sigma_kx": 1.2, "sigma_ky": 0.9, "sigma_e": 0.0},
        "enabled": True,
    }]


def _reference_local_denoise(data, coords, region, methods):
    selected = selection_grid_mask(
        CropSelection.from_dict(region), coords, data.shape[:3], rotation_angle=0.0
    )
    indices = np.where(selected)
    if not indices[0].size:
        raise AssertionError("The synthetic crop must contain original-grid samples.")
    slices = tuple(
        slice(int(values.min()), int(values.max()) + 1) for values in indices
    )
    local = np.where(
        selected[slices][..., None],
        data[slices + (slice(None),)],
        0.0,
    )
    processed = DenoiseEngines.apply_pipeline(local, methods, max_workers=1)
    result = np.zeros(data.shape, dtype=np.float32)
    result[slices + (slice(None),)] = processed
    return result


def _embed(values, bounds, shape, fill):
    values = np.asarray(values)
    if values.shape == shape:
        return values
    result = np.full(shape, fill, dtype=values.dtype)
    result[
        int(bounds[0]):int(bounds[1]) + 1,
        int(bounds[2]):int(bounds[3]) + 1,
        int(bounds[4]):int(bounds[5]) + 1,
    ] = values
    return result


def _full_actual(context, shape):
    if context.get("crop_empty"):
        return np.full(shape, np.nan, dtype=np.float64)
    bounds = context.get("data_bounds")
    data = _embed(context["data"], bounds, shape, np.nan)
    valid = np.ones(shape, dtype=bool)
    for key in ("spatial_valid_mask", "canvas_mask"):
        if context.get(key) is not None:
            valid &= _embed(context[key], bounds, shape, False)
    return np.where(valid, data, np.nan)


def _render(host, spec):
    base = {
        "view": "3d",
        "data": host.backend.rotate_volume(
            host.original_raw_data[..., host.time_index], host.rotation_angle
        ),
        "coords": host.core.coords,
        "rotation_angle": float(host.rotation_angle),
    }
    return apply_crop_regions(
        base,
        list(spec.params.get("crop_regions", ())),
        rotation_angle=float(host.rotation_angle),
    )


def _host():
    data, coords = _synthetic_data()
    host = My3DAnalyzer.__new__(My3DAnalyzer)
    host.original_raw_data = data
    host.core = AnalyzerCore()
    host.core.raw_data = data
    host.core.coords = coords
    host.backend = CpuComputeBackend(max_workers=1)
    host.time_index = 0
    host.rotation_angle = 30.0
    source = AnalysisPageSpec(
        "source", "来源", "home", "system", params={}, data_scope_id="roi-parent"
    )
    host.pages = [source]
    host.left_workspace = SimpleNamespace(
        current_spec=lambda: host.pages[-1],
        add_page=lambda spec: host.pages.append(spec),
    )
    host.crop_controller = SimpleNamespace(
        selection=None,
        popup=SimpleNamespace(hide=Mock(), set_error=Mock()),
    )
    host._render_exact_ready = True
    host._compute_render_context = lambda spec: _render(host, spec)
    host._persist_active_page_state = Mock()
    host._make_unique_page_title = lambda title: title
    host._make_page_id = lambda: f"child-{len(host.pages)}"
    host.global_denoise_methods = {}
    host._validate_denoise_for_descriptor = Mock()
    host._register_roi_scope = Mock()
    return host


class RotatedCropTests(unittest.TestCase):
    def assertMatchesReference(self, host, spec, angle, time_index):
        host.rotation_angle = float(angle)
        host.time_index = int(time_index)
        actual = _render(host, spec)
        expected = _reference_result(
            host.original_raw_data, spec.params["crop_regions"], angle, time_index
        )
        np.testing.assert_allclose(
            _full_actual(actual, expected.shape),
            expected,
            rtol=2e-5,
            atol=2e-6,
            equal_nan=True,
        )
        return actual

    def test_reference_rotation_checks_zero_and_known_peak_direction(self):
        data, _coords = _synthetic_data()
        np.testing.assert_array_equal(_reference_rotation(data[..., 0], 0.0), data[..., 0])
        single_peak = np.zeros(data.shape[:2], dtype=np.float32)
        single_peak[10, 8] = 1.0
        rotated = _reference_rotation(single_peak, 30.0)
        self.assertEqual(np.unravel_index(rotated.argmax(), rotated.shape), (9, 10))
        self.assertGreater(rotated[9, 10], rotated[3, 6])

    def test_same_peak_crop_and_erase_follow_frames_rotation_and_e_flip(self):
        for operation in ("crop", "erase"):
            with self.subTest(operation=operation):
                host = _host()
                source = host.pages[0]
                host.crop_controller.selection = _selection(
                    (8, 11, 9, 11), operation=operation, e_flip=True
                )
                host.on_cut()
                child = host.pages[-1]
                self.assertIsNot(child, source)
                self.assertEqual(child.source_page_id, source.page_id)
                self.assertEqual(child.data_scope_id, source.data_scope_id)
                self.assertEqual(source.params, {})
                host._register_roi_scope.assert_not_called()
                if operation == "crop":
                    host._validate_denoise_for_descriptor.assert_called_once()
                else:
                    host._validate_denoise_for_descriptor.assert_not_called()
                region = child.params["crop_regions"][0]
                self.assertEqual(region["rotation_angle"], 30.0)
                self.assertEqual(region["logical_bounds"], (8.0, 11.0, 9.0, 11.0, 0.0, 2.0))
                self.assertFalse(region["e_flip"])
                for time_index in (0, 1):
                    for angle in (30.0, 0.0, 30.0):
                        self.assertMatchesReference(host, child, angle, time_index)
                at_zero = self.assertMatchesReference(host, child, 0.0, 0)
                values = _full_actual(at_zero, host.original_raw_data.shape[:3])
                if operation == "crop":
                    self.assertTrue(np.isfinite(values[10, 8]).all())
                    self.assertTrue(np.isnan(values[2, 8]).all())
                else:
                    self.assertTrue(np.isnan(values[10, 8]).all())
                    self.assertTrue(np.isfinite(values[2, 8]).all())

    def test_repeated_crop_and_erase_replay_each_original_geometry(self):
        host = _host()
        source = host.pages[0]
        host.crop_controller.selection = _selection((2, 11, 5, 11))
        host.on_cut()
        cropped = host.pages[-1]

        host.rotation_angle = -20.0
        host.crop_controller.selection = _selection((8, 11, 8, 11), operation="erase")
        host.on_cut()
        erased = host.pages[-1]

        host.rotation_angle = 0.0
        host.crop_controller.selection = _selection((1, 4, 7, 9))
        host.on_cut()
        repeated = host.pages[-1]
        self.assertEqual(
            [region["rotation_angle"] for region in repeated.params["crop_regions"]],
            [30.0, -20.0, 0.0],
        )
        self.assertEqual(
            [region.get("operation", "crop") for region in repeated.params["crop_regions"]],
            ["crop", "erase", "crop"],
        )
        self.assertEqual(repeated.source_page_id, erased.page_id)
        self.assertEqual(erased.source_page_id, cropped.page_id)
        self.assertEqual(cropped.source_page_id, source.page_id)
        self.assertEqual(repeated.data_scope_id, source.data_scope_id)

        first_at_thirty = self.assertMatchesReference(host, repeated, 30.0, 1)
        for angle in (-20.0, 0.0, 30.0):
            self.assertMatchesReference(host, repeated, angle, 1)
        np.testing.assert_allclose(
            _full_actual(first_at_thirty, host.original_raw_data.shape[:3]),
            _full_actual(_render(host, repeated), host.original_raw_data.shape[:3]),
            equal_nan=True,
        )

        host.crop_controller.selection = _selection((1, 4, 5, 7), operation="erase")
        host.on_cut()
        emptied = host.pages[-1]
        self.assertEqual(emptied.source_page_id, repeated.page_id)
        self.assertEqual(len(emptied.params["crop_regions"]), 4)
        self.assertMatchesReference(host, emptied, 30.0, 1)

    def test_exact_result_wait_and_denoise_validation_still_block_commit(self):
        host = _host()
        host._render_exact_ready = False
        host._compute_render_context = Mock()
        host.crop_controller.selection = _selection((8, 11, 9, 11))
        host.on_cut()
        self.assertEqual(len(host.pages), 1)
        host._compute_render_context.assert_not_called()
        self.assertIn("等待", host.crop_controller.popup.set_error.call_args.args[0])

        host._render_exact_ready = True
        host._compute_render_context = lambda spec: _render(host, spec)
        host._validate_denoise_for_descriptor.side_effect = ValueError("去噪参数无效")
        host.on_cut()
        self.assertEqual(len(host.pages), 1)
        self.assertEqual(host.pages[0].params, {})
        host.crop_controller.popup.set_error.assert_called_with("去噪参数无效")


class RotatedCropDenoiseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_local_gaussian_differs_from_full_frame_and_follows_frame_and_angle(self):
        data, coords = _synthetic_data()
        methods = _gaussian_methods()
        full_denoised = DenoiseEngines.apply_pipeline(data, methods, max_workers=1)
        with TemporaryDirectory() as temp:
            path = Path(temp) / "rotated-crop-denoise.npz"
            np.savez(
                path,
                sample=data,
                kx=coords["X"],
                ky=coords["Y"],
                E=coords["E"],
                time=coords["delay"],
                axis_mapping_version=1,
                axis_order=np.asarray(["X", "Y", "E", "T"]),
            )
            window = control_window()
            self.addCleanup(window.close)
            self.assertTrue(window.load_data(str(path)))
            window.global_denoise_methods = methods
            window.shared_denoised_raw_data = full_denoised
            window.shared_denoise_signature = window._denoise_signature(methods)
            window.shared_denoise_version += 1
            window._invalidate_denoise_dependents()
            window.canvas_tool_overlay.set_rotation_angle(30.0)
            window.on_rotation_angle_changed()
            source = window.left_workspace.current_spec()
            window.crop_controller.set_selection(
                _selection((8, 11, 9, 11)), notify=False
            )
            window.on_cut()
            child = window.left_workspace.current_spec()
            self.assertIsNot(child, source)
            self.assertTrue(child.params["local_crop_denoise"])
            self.assertEqual(child.data_scope_id, source.data_scope_id)
            region = child.params["crop_regions"][0]

            local_denoised = _reference_local_denoise(data, coords, region, methods)
            references = {}
            roundtrip = {0: [], 1: []}
            for time_index in (0, 1):
                window.timeline_bar.slider_time.setValue(time_index)
                for angle in (30.0, 0.0, -20.0, 30.0):
                    window.canvas_tool_overlay.set_rotation_angle(angle)
                    window.on_rotation_angle_changed()
                    local = _reference_crop_components(
                        local_denoised[..., time_index], region, angle
                    )
                    full = _reference_crop_components(
                        full_denoised[..., time_index], region, angle
                    )
                    local_data = local["data"]
                    mask = local["spatial_valid_mask"] & local["canvas_mask"]
                    if time_index == 0 and angle == 30.0:
                        self.assertFalse(
                            np.allclose(
                                local_data[mask], full["data"][mask],
                                rtol=2e-5, atol=2e-6,
                            )
                        )
                    np.testing.assert_array_equal(
                        window.current_render_context["spatial_valid_mask"],
                        local["spatial_valid_mask"],
                    )
                    np.testing.assert_array_equal(
                        window.current_render_context["canvas_mask"], local["canvas_mask"]
                    )
                    np.testing.assert_allclose(
                        window.current_render_context["data"][mask],
                        local_data[mask],
                        rtol=2e-5,
                        atol=2e-6,
                    )
                    references[time_index, angle] = (local_data, full["data"], mask)
                    if angle == 30.0:
                        roundtrip[time_index].append(
                            np.array(window.current_render_context["data"], copy=True)
                        )

            for time_index in (0, 1):
                self.assertEqual(len(roundtrip[time_index]), 2)
                np.testing.assert_allclose(
                    roundtrip[time_index][0], roundtrip[time_index][1],
                    rtol=2e-5, atol=2e-6,
                )
            local_data, full_data, mask = references[0, 30.0]
            self.assertFalse(
                np.allclose(local_data[mask], full_data[mask], rtol=2e-5, atol=2e-6)
            )
            self.assertFalse(
                np.allclose(
                    references[0, 30.0][0], references[1, 30.0][0],
                    rtol=2e-5, atol=2e-6,
                )
            )


if __name__ == "__main__":
    unittest.main()
