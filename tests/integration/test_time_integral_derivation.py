"""当前视图的时间区间求和：不创建独立时间积分页面。"""
import copy
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.axis_interval import AxisInterval
from bandscope.core.crop_model import CropSelection
from bandscope.ui.result_workspace import AnalysisPageSpec
from tests.support.view_recipe import control_window, synthetic_npz


class CurrentViewTimeIntervalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "source.npz"
        self.data = synthetic_npz(self.path, seed=19)
        self.window = control_window()
        self.addCleanup(self.window.close)
        self.assertTrue(self.window.load_data(str(self.path)))
        identity = self.window.core.source_identity
        if identity is not None and identity.future is not None:
            identity.future.result(timeout=30)

    def _set_range(self, spec, low, up):
        coords = self.window.core.coords["delay"]
        spec.params["time_interval"] = AxisInterval.from_indices(
            coords, low, up, locked=low == up, axis_key="delay"
        ).as_dict()
        self.window._bind_time_interval(spec, animate=False)

    def _create_home_edc(self, *selections):
        home = self.window.left_workspace.current_spec()
        self._set_range(home, 0, 0)
        context = self.window.current_render_context
        self.window.crop_controller.activate(home.page_id, context)
        for index, selection in enumerate(selections):
            if index:
                self.window.crop_controller.add_selection(context)
            self.window.crop_controller.set_selection(selection, notify=False)
        page_id = self.window._create_edc_result_from_selections()
        self.assertIsNotNone(page_id)
        return self.window.left_workspace.current_spec()

    def _expected_edc(self, metadata, time_index):
        x_low, x_up, y_low, y_up, e_low, e_up = metadata["index_bounds"]
        selected = self.data[
            x_low:x_up + 1,
            y_low:y_up + 1,
            e_low:e_up + 1,
            time_index,
        ]
        return np.sum(selected, axis=(0, 1), dtype=np.float64)

    def _assert_edc_context(self, spec, context, time_index, *, logged=False):
        saved_curves = [
            spec.params["base_curve"],
            *(spec.params.get("overlay_curves") or ()),
        ]
        if context["view"] == "1d":
            actual_curves = [{
                "curve_id": saved_curves[0]["curve_id"],
                "title": context["title"],
                "x_data": context["x_data"],
                "y_data": context["y_data"],
            }]
            metadata_items = [context]
        else:
            actual_curves = context["curves"]
            metadata_items = context["curve_metadata"]
        self.assertEqual(len(actual_curves), len(saved_curves))
        delay = self.window.core.coords["delay"]
        for saved, actual, metadata in zip(saved_curves, actual_curves, metadata_items):
            self.assertEqual(actual["curve_id"], saved["curve_id"])
            expected_title = saved["title"] if context["view"] != "1d" else spec.title
            self.assertEqual(actual["title"], expected_title)
            np.testing.assert_array_equal(actual["x_data"], saved["x_data"])
            expected = self._expected_edc(metadata, time_index)
            if logged:
                expected = np.log10(expected)
            np.testing.assert_allclose(actual["y_data"], expected, rtol=1e-12, atol=1e-12)
            self.assertEqual(tuple(metadata["source_t_indices"]), (time_index, time_index))
            self.assertEqual(
                tuple(metadata["source_t_range"]),
                (float(delay[time_index]), float(delay[time_index])),
            )

    def _preview_time(self, time_index):
        delay = self.window.core.coords["delay"]
        control = self.window.timeline_bar.time_interval_control
        control.endpointPreview.emit("low", float(delay[time_index]))
        return self.window.current_render_context

    def test_home_sums_only_the_current_view_and_collapses_back_to_a_frame(self):
        spec = self.window.left_workspace.current_spec()
        original = (spec.page_id, spec.page_kind, len(self.window.left_workspace.page_specs))
        self._set_range(spec, 0, 1)
        context = self.window._compute_render_context(spec)
        np.testing.assert_allclose(
            context["data"], np.sum(self.data[..., 0:2], axis=3), rtol=2e-6, atol=2e-6
        )
        self.assertEqual(
            (spec.page_id, spec.page_kind, len(self.window.left_workspace.page_specs)), original
        )

        self._set_range(spec, 2, 2)
        context = self.window._compute_render_context(spec)
        np.testing.assert_allclose(context["data"], self.data[..., 2], rtol=2e-6, atol=2e-6)
        self.assertEqual(
            (spec.page_id, spec.page_kind, len(self.window.left_workspace.page_specs)), original
        )

    def test_spatial_and_time_intervals_compose_on_the_same_2d_page(self):
        source = self.window.left_workspace.current_spec()
        spec = self.window._build_canvas_axis_slice_spec(source, 2)
        self.window.left_workspace.add_page(spec)
        original = (spec.page_id, spec.page_kind, len(self.window.left_workspace.page_specs))
        self._set_range(spec, 0, 1)

        spatial = AxisInterval.from_dict(spec.params["axis_interval"])
        low, up = spatial.to_indices(self.window.core.coords["E"])
        expected = np.sum(np.sum(self.data[..., 0:2], axis=3)[:, :, low:up + 1], axis=2)
        context = self.window._get_axis_integral_context(
            spec, self.window.core.raw_data, self.window.core.coords
        )
        np.testing.assert_allclose(context["data"], expected, rtol=2e-6, atol=2e-6)
        self.assertEqual(
            (spec.page_id, spec.page_kind, len(self.window.left_workspace.page_specs)), original
        )

    def test_energy_dos_reads_its_own_time_interval(self):
        home = self.window.left_workspace.current_spec()
        self._set_range(home, 0, 1)
        spec = self.window._build_energy_dos_spec()
        self.window.left_workspace.add_page(spec)
        original = (spec.page_id, spec.page_kind, len(self.window.left_workspace.page_specs))
        raw, coords = self.window._get_display_state_for_spec(spec)

        context = self.window._get_energy_dos_context(spec, raw, coords)
        np.testing.assert_allclose(
            context["y_data"], np.sum(self.data[..., 0:2], axis=(0, 1, 3)), rtol=2e-6, atol=2e-6
        )

        self._set_range(spec, 1, 2)
        context = self.window._get_energy_dos_context(spec, raw, coords)
        np.testing.assert_allclose(
            context["y_data"], np.sum(self.data[..., 1:3], axis=(0, 1, 3)), rtol=2e-6, atol=2e-6
        )
        self.assertEqual(
            (spec.page_id, spec.page_kind, len(self.window.left_workspace.page_specs)), original
        )

    def test_single_selection_edc_recomputes_during_preview_and_exact_refresh(self):
        spec = self._create_home_edc(
            CropSelection("3d", ("X", "Y", "E"), (-1.0, -0.5, -2.0, 2.0, -1.0, 1.0))
        )
        self.assertEqual(spec.page_kind, "edc_curve")
        self.assertTrue(self.window._time_page_editable(spec))
        self.assertTrue(self.window.timeline_bar.slider_time.isEnabled())
        frozen_y = copy.deepcopy(spec.params["base_curve"]["y_data"])

        for time_index in range(3):
            with self.subTest(time_index=time_index):
                context = self._preview_time(time_index)
                self._assert_edc_context(spec, context, time_index)
                if time_index == 1:
                    _title, _name, exported = self.window._build_edc_export_payload(
                        spec, None, {}
                    )
                    np.testing.assert_allclose(
                        exported["intensity"], context["y_data"], rtol=1e-6, atol=1e-6
                    )
                    np.testing.assert_array_equal(exported["source_t_indices"], [1, 1])
                    self.window.timeline_bar.time_interval_control.interactionFinished.emit()
                    self._assert_edc_context(
                        spec, self.window.current_render_context, time_index
                    )

        self._set_range(spec, 0, 1)
        integrated = self.window._compute_render_context(spec)
        x_low, x_up, y_low, y_up, e_low, e_up = spec.params["curve_metadata"][0]["index_bounds"]
        time_source = np.sum(self.data[..., 0:2], axis=3, dtype=np.float32)
        expected = np.sum(
            time_source[x_low:x_up + 1, y_low:y_up + 1, e_low:e_up + 1],
            axis=(0, 1),
            dtype=np.float64,
        )
        np.testing.assert_allclose(integrated["y_data"], expected, rtol=1e-12, atol=1e-12)
        self.assertEqual(integrated["source_t_indices"], (0, 1))

        np.testing.assert_array_equal(spec.params["base_curve"]["y_data"], frozen_y)
        external = copy.deepcopy(spec)
        external.params["base_curve"]["recipe_id"] = "other-file-recipe"
        self.assertFalse(self.window._edc_time_recomputable(external))
        self.assertFalse(self.window._time_page_editable(external))
        np.testing.assert_array_equal(
            self.window._compute_render_context(external)["y_data"], frozen_y
        )

    def test_single_edc_log_export_uses_the_current_frame_context(self):
        source = self._create_home_edc(
            CropSelection("3d", ("X", "Y", "E"), (-1.0, -0.5, -2.0, 2.0, -1.0, 1.0))
        )
        self.assertTrue(self.window._apply_log_to_current_curves())
        log_spec = self.window.left_workspace.current_spec()
        context = self._preview_time(2)
        self.assertEqual(context["view"], "1d")
        self.assertNotIn("curves", context)

        _title, _name, exported = self.window._build_edc_log_export_payload(log_spec)
        expected = np.log10(self._expected_edc(log_spec.params["curve_metadata"][0], 2))
        np.testing.assert_allclose(exported["intensity_1"], expected, rtol=1e-6, atol=1e-6)
        np.testing.assert_array_equal(
            exported["E_1"], np.asarray(context["x_data"], dtype=np.float32)
        )
        np.testing.assert_array_equal(exported["source_t_indices_1"], [2, 2])
        np.testing.assert_array_equal(
            exported["index_bounds_1"],
            source.params["curve_metadata"][0]["index_bounds"],
        )
        self.assertFalse(np.allclose(
            exported["intensity_1"], log_spec.params["base_curve"]["y_data"]
        ))

    def test_dynamic_edc_pages_skip_volume_jobs_while_axis_pages_keep_precompute(self):
        source = self._create_home_edc(
            CropSelection("3d", ("X", "Y", "E"), (-1.0, -0.5, -2.0, 2.0, -1.0, 1.0))
        )
        self.assertTrue(self.window._apply_log_to_current_curves())
        log_spec = self.window.left_workspace.current_spec()
        request = SimpleNamespace(snapshot={"rotation_angle": 0.0})
        with patch.object(
            self.window,
            "_get_display_state_for_spec",
            side_effect=AssertionError("Dynamic 1D pages must not resolve a volume source"),
        ):
            for spec in (source, log_spec):
                with self.subTest(page_kind=spec.page_kind):
                    self.assertIsNone(self.window._volume_source_descriptor(spec, request))

        home = self.window.left_workspace.page_by_id(source.source_page_id)
        axis = self.window._build_canvas_axis_slice_spec(home, 2)
        self.window.left_workspace.add_page(axis)
        self._set_range(axis, 0, 1)
        descriptor = self.window._volume_source_descriptor(axis, request)
        self.assertIsNotNone(descriptor)
        self.assertIn(descriptor[1].operation, {"prepare_volume", "prepare_axis_prefix"})

    def test_multi_selection_edc_and_derived_log_follow_their_own_time_intervals(self):
        comparison = self._create_home_edc(
            CropSelection("3d", ("X", "Y", "E"), (-1.0, -0.5, -2.0, 2.0, -1.0, 1.0)),
            CropSelection("3d", ("X", "Y", "E"), (0.5, 1.0, 0.0, 2.0, -1.0, 1.0)),
        )
        self.assertEqual(comparison.page_kind, "curve_comparison_1d")
        self.assertTrue(self.window._time_page_editable(comparison))
        for time_index in (0, 1):
            context = self._preview_time(time_index)
            self._assert_edc_context(comparison, context, time_index)
        self.window.timeline_bar.time_interval_control.interactionFinished.emit()
        self._assert_edc_context(comparison, self.window.current_render_context, 1)

        self.assertTrue(self.window._apply_log_to_current_curves())
        log_spec = self.window.left_workspace.current_spec()
        self.assertEqual(log_spec.page_kind, "log_curve")
        self.assertIn("edc_source", log_spec.params)
        self.assertTrue(self.window._time_page_editable(log_spec))
        self.assertEqual(
            log_spec.params["curve_metadata"][0]["source_t_indices"], (1, 1)
        )
        for time_index in (1, 2):
            context = self._preview_time(time_index)
            self._assert_edc_context(log_spec, context, time_index, logged=True)
        self.window.timeline_bar.time_interval_control.interactionFinished.emit()
        self._assert_edc_context(log_spec, self.window.current_render_context, 2, logged=True)

        _title, _name, exported = self.window._build_edc_log_export_payload(log_spec)
        for index, metadata in enumerate(log_spec.params["curve_metadata"], 1):
            np.testing.assert_allclose(
                exported[f"intensity_{index}"],
                np.log10(self._expected_edc(metadata, 2)),
                rtol=1e-6,
                atol=1e-6,
            )
            np.testing.assert_array_equal(exported[f"source_t_indices_{index}"], [2, 2])

        self.window.left_workspace.activate_page(comparison.page_id)
        self._assert_edc_context(comparison, self.window.current_render_context, 1)

    def test_axis_integral_edc_replays_its_frozen_spatial_source(self):
        home = self.window.left_workspace.current_spec()
        self._set_range(home, 0, 0)
        axis = self.window._build_canvas_axis_slice_spec(home, 2)
        self.window.left_workspace.add_page(axis)
        self._set_range(axis, 0, 0)
        context = self.window.current_render_context
        self.window.crop_controller.activate(axis.page_id, context)
        self.window.crop_controller.set_selection(
            CropSelection("2d", ("X", "Y"), (-1.0, -0.5, -2.0, 2.0)),
            notify=False,
        )
        page_id = self.window._create_edc_result_from_selections()
        self.assertIsNotNone(page_id)
        spec = self.window.left_workspace.current_spec()
        self.assertTrue(self.window._time_page_editable(spec))

        for time_index in (0, 2):
            context = self._preview_time(time_index)
            self._assert_edc_context(spec, context, time_index)
        self.window.timeline_bar.time_interval_control.interactionFinished.emit()
        self._assert_edc_context(spec, self.window.current_render_context, 2)

    def test_edc_time_recompute_keeps_frozen_scope_crop_and_rotation(self):
        home = self.window.left_workspace.current_spec()
        scope = self.window._register_roi_scope((0, 4, 1, 6, 0, 7))
        home.data_scope_id = scope.scope_id
        home.params["crop_regions"] = [
            CropSelection(
                "3d", ("X", "Y", "E"), (-1.0, 0.2, -2.0, 2.0, -1.0, 1.0)
            ).to_dict()
        ]
        self.window.rotation_angle = 15.0
        spec = self._create_home_edc(
            CropSelection("3d", ("X", "Y", "E"), (-1.0, -0.5, -2.0, 2.0, -1.0, 1.0))
        )
        self.assertEqual(spec.params["source_rotation_angle"], 15.0)
        np.testing.assert_array_equal(
            spec.params["source_scope_metadata"]["roi_index_bounds"], scope.spatial_bounds
        )
        home.data_scope_id = "full"
        home.params["crop_regions"] = []
        self.window.rotation_angle = 80.0

        context = self._preview_time(2)
        full = np.zeros(self.data.shape[:3], dtype=np.float32)
        full[scope.spatial_slices] = self.data[scope.spatial_slices + (2,)]
        rotated = self.window.backend_manager.cpu.rotate_volume(full, 15.0)
        x_low, x_up, y_low, y_up, e_low, e_up = spec.params["curve_metadata"][0]["index_bounds"]
        expected = np.sum(
            rotated[x_low:x_up + 1, y_low:y_up + 1, e_low:e_up + 1],
            axis=(0, 1),
            dtype=np.float64,
        )
        np.testing.assert_allclose(context["y_data"], expected, rtol=1e-12, atol=1e-12)
        self.assertEqual(self.window.rotation_angle, 80.0)

    def test_copy_pasted_comparison_and_its_log_retain_frozen_recipe_curves(self):
        self._create_home_edc(
            CropSelection("3d", ("X", "Y", "E"), (-1.0, -0.5, -2.0, 2.0, -1.0, 1.0))
        )
        self._preview_time(1)
        self.assertTrue(self.window._copy_current_curve_to_clipboard())
        self.assertTrue(self.window._paste_curve_to_comparison_page())
        comparison = self.window.left_workspace.current_spec()
        self.assertNotIn("edc_source", comparison.params)
        self.assertFalse(self.window._time_page_editable(comparison))
        frozen = copy.deepcopy(self.window.current_render_context)
        self.assertTrue(all(
            curve.get("recipe_id") for curve in frozen["curves"]
        ))

        comparison.params["time_interval"] = AxisInterval.from_indices(
            self.window.core.coords["delay"], 2, 2, locked=True, axis_key="delay"
        ).as_dict()
        changed = self.window._compute_render_context(comparison)
        for before, after in zip(frozen["curves"], changed["curves"]):
            np.testing.assert_array_equal(after["y_data"], before["y_data"])

        self.assertTrue(self.window._apply_log_to_current_curves())
        log_spec = self.window.left_workspace.current_spec()
        self.assertNotIn("edc_source", log_spec.params)
        self.assertFalse(self.window._time_page_editable(log_spec))
        frozen_log = copy.deepcopy(self.window.current_render_context)
        log_spec.params["time_interval"] = AxisInterval.from_indices(
            self.window.core.coords["delay"], 0, 0, locked=True, axis_key="delay"
        ).as_dict()
        changed_log = self.window._compute_render_context(log_spec)
        for before, after in zip(frozen_log["curves"], changed_log["curves"]):
            np.testing.assert_array_equal(after["y_data"], before["y_data"])


class LegacyTimePageMigrationTests(unittest.TestCase):
    def test_legacy_page_kind_becomes_an_ordinary_home_page(self):
        spec = AnalysisPageSpec(
            "legacy", "旧时间积分", "time_integral", "test",
            params={"t_low": 0, "t_up": 1},
        )
        self.assertEqual(spec.page_kind, "home")

    def test_static_volume_is_not_sliced(self):
        raw = np.ones((2, 3, 4), dtype=np.float32)
        self.assertIs(My3DAnalyzer._time_range_data(raw, 0, 0), raw)


if __name__ == "__main__":
    unittest.main()
