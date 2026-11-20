"""Canvas-created axis slices, frozen sources, thickening and recipe recovery."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.core.axis_interval import AxisInterval
from bandscope.core.crop_model import CropSelection, apply_crop_regions
from bandscope.core.view_recipe import ViewRecipeDocument
from bandscope.exporting.publication_export import capture_snapshot
from bandscope.ui.result_workspace import AnalysisPageSpec
from tests.support.view_recipe import control_window, synthetic_npz


def assert_close(actual, expected):
    np.testing.assert_allclose(actual, expected, rtol=2e-6, atol=2e-6, equal_nan=True)


class CanvasAxisSliceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "source.npz"
        self.data = synthetic_npz(self.path, seed=4, descending=True)
        self.window = control_window()
        self.addCleanup(self.window.close)
        self.assertTrue(self.window.load_data(str(self.path)))
        identity = self.window.core.source_identity
        if identity is not None and identity.future is not None:
            identity.future.result(timeout=30)
        self.source = self.window.left_workspace.current_spec()

    def _build(self, axis, *, source=None, activate=False):
        spec = self.window._build_canvas_axis_slice_spec(source or self.source, axis)
        self.assertIsNotNone(spec)
        if activate:
            self.window.left_workspace.add_page(spec)
        return spec

    def _context(self, spec):
        raw, coords = self.window._get_display_state_for_spec(spec)
        return self.window._get_axis_integral_context(spec, raw, coords)

    def test_three_directions_start_at_physical_quartiles_and_sum_inclusively(self):
        for axis, plane in ((0, "YZ"), (1, "XZ"), (2, "XY")):
            with self.subTest(axis=axis):
                spec = self._build(axis)
                interval = AxisInterval.from_dict(spec.params["axis_interval"])
                coords = self.window._coords_for_axis(axis)
                self.assertAlmostEqual(interval.low, float(np.min(coords)) + 0.25 * np.ptp(coords))
                self.assertAlmostEqual(interval.up, float(np.min(coords)) + 0.75 * np.ptp(coords))
                self.assertFalse(interval.locked)
                self.assertEqual(spec.title, f"{plane} 切片")
                low, up = interval.to_indices(coords)
                slices = [slice(None)] * 3
                slices[axis] = slice(low, up + 1)
                expected = np.sum(self.data[..., 0][tuple(slices)], axis=axis)
                assert_close(self._context(spec)["data"], expected)

    def test_single_available_position_opens_as_a_locked_single_layer(self):
        path = Path(self.temp.name) / "single-axis.npz"
        sample = np.arange(1 * 3 * 4 * 2, dtype=np.float32).reshape(1, 3, 4, 2)
        np.savez(path, sample=sample, kx=np.array([2.0]), ky=np.arange(3.0),
                 E=np.arange(4.0), time=np.arange(2.0))
        self.assertTrue(self.window.load_data(str(path)))
        source = self.window.left_workspace.current_spec()

        spec = self._build(0, source=source)
        interval = AxisInterval.from_dict(spec.params["axis_interval"])
        self.assertEqual((interval.low, interval.up), (2.0, 2.0))
        self.assertTrue(interval.locked)
        assert_close(self._context(spec)["data"], sample[0, :, :, 0])

    def test_repeated_pages_keep_independent_ranges_and_return_to_their_source(self):
        first = self._build(2, activate=True)
        self.window.left_workspace.activate_page(self.source.page_id)
        second = self._build(2, activate=True)
        self.assertNotEqual(first.page_id, second.page_id)
        self.assertNotEqual(first.title, second.title)
        original_second = dict(second.params["axis_interval"])

        self.window.left_workspace.activate_page(first.page_id)
        self.window.axis_interval_controller.interval.set_length(0.2)
        self.window._persist_axis_interval_for_current_page()
        self.assertEqual(second.params["axis_interval"], original_second)

        self.window.on_return_to_3d_source()
        self.assertEqual(self.window.left_workspace.current_spec().page_id, self.source.page_id)
        self.window.left_workspace.activate_page(second.page_id)
        self.assertEqual(second.params["axis_interval"], original_second)

    def test_cropped_slice_returns_to_the_original_3d_not_its_2d_parent(self):
        first = self._build(2, activate=True)
        cropped = AnalysisPageSpec(
            "cropped", "裁剪切片", "axis_integral_crop", "test",
            source_page_id=first.page_id, params=dict(first.params),
        )
        self.window.left_workspace.add_page(cropped)
        self.window.on_return_to_3d_source()
        self.assertEqual(self.window.left_workspace.current_spec().page_id, self.source.page_id)

    def test_frame_is_frozen_when_the_source_changes(self):
        spec = self._build(0)
        expected = self._context(spec)["data"].copy()
        self.window.timeline_bar.slider_time.setValue(2)
        assert_close(self._context(spec)["data"], expected)
        self.assertEqual(spec.params["source_3d"]["params"]["source_t_index"], 0)

    def test_time_integral_volume_is_the_actual_source(self):
        self.window.page_data.s_t_low.setValue(0)
        self.window.page_data.s_t_up.setValue(1)
        time_source = self.window._build_time_integral_spec()
        self.window.left_workspace.add_page(time_source)
        spec = self._build(2, source=time_source)
        interval = AxisInterval.from_dict(spec.params["axis_interval"])
        low, up = interval.to_indices(self.window.core.coords["E"])
        volume = np.sum(self.data[..., 0:2], axis=3)
        assert_close(self._context(spec)["data"], np.sum(volume[:, :, low:up + 1], axis=2))

    def test_crop_and_erasure_are_replayed_before_integration_and_export(self):
        regions = [
            CropSelection("3d", ("X", "Y", "E"), (-0.5, 0.8, -1.0, 1.5, -0.6, 0.6)).to_dict(),
            CropSelection("3d", ("X", "Y", "E"), (-0.2, 0.2, -0.5, 0.5, -0.2, 0.2), operation="erase").to_dict(),
        ]
        self.source.params["crop_regions"] = regions
        spec = self._build(2)
        source_context = apply_crop_regions(
            {"view": "3d", "data": self.data[..., 0], "coords": self.window.core.coords}, regions,
        )
        interval = AxisInterval.from_dict(spec.params["axis_interval"])
        low, up = interval.to_indices(self.window.core.coords["E"])
        bounds = source_context["data_bounds"]
        local_low = low - bounds[4]
        local_up = up - bounds[4]
        compact = np.nansum(source_context["data"][:, :, local_low:local_up + 1], axis=2)
        expected = np.zeros(self.data.shape[:2], dtype=np.float64)
        expected[
            bounds[0]:bounds[1] + 1,
            bounds[2]:bounds[3] + 1,
        ] = compact
        assert_close(self._context(spec)["data"], expected)

        raw, coords = self.window._get_display_state_for_spec(spec)
        exported = self.window._get_axis_integral_export_context(spec, raw, coords)
        assert_close(exported["export_data"]["sample"], expected)

    def test_3d_second_derivative_is_used_instead_of_the_raw_volume(self):
        derivative = AnalysisPageSpec(
            "derivative", "三维二阶导", "second_derivative", "test",
            source_page_id=self.source.page_id,
            params={
                "source_page_kind": "home", "source_view": "3d", "derivative_axis": 2,
                "source_mode": "frame", "source_t_index": 0,
                "second_derivative_params": {"threshold": 0.0},
            },
        )
        self.window.left_workspace.add_page(derivative)
        spec = self._build(0, source=derivative)
        source_context = self.window._compute_render_context(derivative)
        interval = AxisInterval.from_dict(spec.params["axis_interval"])
        low, up = interval.to_indices(self.window.core.coords["X"])
        expected = np.sum(source_context["data"][low:up + 1], axis=0)
        assert_close(self._context(spec)["data"], expected)

    def test_single_crop_unlocks_and_thickens_without_losing_2d_tail(self):
        x = self.window.core.coords["X"]
        regions = [
            CropSelection("3d", ("X", "Y", "E"), (float(x[2]), float(x[2]), -2.0, 2.0, -1.0, 1.0)).to_dict(),
            CropSelection("2d", ("Y", "E"), (-1.0, 1.0, -0.5, 0.5)).to_dict(),
            CropSelection("2d", ("Y", "E"), (-0.2, 0.2, -0.2, 0.2), operation="erase").to_dict(),
        ]
        spec = AnalysisPageSpec(
            "single", "单层", "home", "test", source_page_id=self.source.page_id,
            params={
                "source_t_index": 0, "crop_regions": regions,
                "home_slice_info": {"axis": 0, "index": 2, "mode": "integral", "range": (2, 2)},
                "axis_interval": self.window._axis_space_for_index(0).as_interval(
                    low=float(x[2]), up=float(x[2]), locked=True,
                ).as_dict(),
            },
        )
        self.window.left_workspace.add_page(spec)
        self.window.axis_interval_controller.set_locked(False)
        self.window.timeline_bar.interval_control.input_length.setValue(0.8)
        self.window.timeline_bar.interval_control.input_length.editingFinished.emit()
        self.window._persist_axis_interval_for_current_page()

        raw, coords = self.window._get_display_state_for_spec(spec)
        base = self.window._get_home_render_context(spec, raw, coords)
        expected = apply_crop_regions(base, regions[1:])["data"]
        actual = self.window._compute_render_context(spec)["data"]
        assert_close(actual, expected)
        self.assertGreater(spec.params["home_slice_info"]["range"][1],
                           spec.params["home_slice_info"]["range"][0])
        self.assertEqual(spec.params["crop_regions"][1:], regions[1:])

    def test_recipe_restores_the_same_frozen_source_and_interval(self):
        spec = self._build(1, activate=True)
        expected = self._context(spec)["data"].copy()
        snapshot = capture_snapshot(self.window)
        self.assertIsNotNone(snapshot.view_recipe, snapshot.recipe_error)
        recipe_path = Path(self.temp.name) / "axis.bsview.json"
        recipe_path.write_text(ViewRecipeDocument(snapshot.view_recipe).dumps(), encoding="utf-8")

        self.assertTrue(self.window._load_view_recipe(
            recipe_path, confirm=False, relocate=lambda _: str(self.path),
        ))
        restored = self.window.active_page_spec
        self.assertEqual(restored.params["axis_interval"], spec.params["axis_interval"])
        self.assertEqual(restored.params["source_3d"]["page_kind"], "home")
        self.assertEqual(
            restored.params["source_3d"]["params"]["source_t_index"],
            spec.params["source_3d"]["params"]["source_t_index"],
        )
        self.assertEqual(restored.params["source_3d"]["rotation_angle"], 0.0)
        assert_close(self._context(restored)["data"], expected)


if __name__ == "__main__":
    unittest.main()
