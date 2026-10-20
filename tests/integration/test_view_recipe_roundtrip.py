import copy
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.app.view_recipe_integration import RecipeEvaluator
from bandscope.core.view_recipe import RecipeError, ViewRecipeDocument, plain, state_digest
from bandscope.exporting.publication_export import capture_snapshot
from bandscope.ui.axis_title_controller import axis_title_key
from bandscope.ui.result_workspace import AnalysisPageSpec
from bandscope.core.crop_model import CropSelection
from tests.support.view_recipe import control_window, recipe_document, synthetic_npz


class RecipeRoundtripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "input.npz"
        self.raw = synthetic_npz(self.path, descending=True)
        self.window = control_window()
        self.addCleanup(self.window.close)
        self.window.load_data(str(self.path))

    def save(self, snapshot=None):
        snapshot = snapshot or capture_snapshot(self.window)
        self.assertIsNotNone(snapshot.view_recipe, snapshot.recipe_error)
        path = self.path.parent / "figure.png.bsview.json"
        path.write_text(ViewRecipeDocument(snapshot.view_recipe).dumps(), encoding="utf-8")
        return path, snapshot

    def test_home_roundtrip_controls_camera_titles_and_relocation(self):
        w = self.window
        w.timeline_bar.slider_time.setValue(2)
        w.page_render.s_low.setValue(13)
        w.page_render.s_gamma.setValue(62)
        w.page_render.s_up.setValue(91)
        w.plotter.camera.position = (450, -250, 330)
        w.plotter.camera.focal_point = (115, 95, 100)
        key = axis_title_key(w.left_workspace.home_page_id, "3d", ("X", "Y", "E"))
        w._axis_titles().set_text(key, "X", "Custom momentum")
        path, snapshot = self.save()
        moved = self.path.with_name("moved.npz")
        self.path.rename(moved)
        d = json.loads(path.read_text(encoding="utf-8"))
        d["sources"][d["primary_source_id"]]["path"] = moved.name
        path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        other = control_window()
        self.addCleanup(other.close)
        self.assertTrue(other._load_view_recipe(path, confirm=False))
        self.assertEqual(other.timeline_bar.slider_time.value(), 2)
        self.assertEqual(other._get_display_levels(), w._get_display_levels())
        restored_key = axis_title_key(other.left_workspace.home_page_id, "3d", ("X", "Y", "E"))
        self.assertEqual(other._axis_titles().text(restored_key, "X"), "Custom momentum")
        other._restore_recipe_scene(other.active_page_spec)
        np.testing.assert_allclose(other.plotter.camera.position, snapshot.camera["position"])
        np.testing.assert_allclose(other.plotter.camera.focal_point, snapshot.camera["focal_point"])
        np.testing.assert_array_equal(other.current_render_context["data"], snapshot.payload["volume"])

    def test_only_target_ancestors_restored_and_recomputed(self):
        w = self.window
        home = w.left_workspace.home_spec()
        home_id = home.page_id
        params = {"t_low": 0, "t_up": 2, "control_state": copy.deepcopy(home.params["control_state"])}
        page = AnalysisPageSpec(w._make_page_id(), "用户命名的积分", "time_integral", "test",
                                params=params, source_page_id=home_id, title_overridden=True)
        w.left_workspace.add_page_inactive(AnalysisPageSpec(w._make_page_id(), "无关", "energy_dos", "test",
                                      params={"control_state": copy.deepcopy(home.params["control_state"])},
                                      source_page_id=home_id))
        w.left_workspace.add_page(page)
        path, snapshot = self.save()
        self.assertEqual({p["title"] for p in snapshot.view_recipe["pages"]}, {"原始视图", "用户命名的积分"})
        w.left_workspace.activate_page(home_id)
        self.assertTrue(w._load_view_recipe(path, confirm=False))
        # 复现文件作为新数据源打开：页面 ID 重新分配，页面数量与目标页保持。
        self.assertEqual(len(w.left_workspace.page_specs), 2)
        self.assertEqual(w.active_page_spec.title, "用户命名的积分")
        self.assertNotEqual(w.active_page_spec.page_id, page.page_id)
        np.testing.assert_allclose(w.current_render_context["data"], self.raw.sum(axis=3), rtol=1e-6)

    def test_preflight_failure_preserves_existing_workspace(self):
        path, _ = self.save()
        original = self.window.core.raw_data
        old_pages = list(self.window.left_workspace.page_specs)
        synthetic_npz(self.path, seed=7)
        with self.assertRaisesRegex(RecipeError, "SHA-256"):
            self.window._load_view_recipe(path, confirm=False)
        self.assertIs(self.window.core.raw_data, original)
        self.assertEqual(list(self.window.left_workspace.page_specs), old_pages)

    def test_missing_file_relocate_cancel_is_non_destructive(self):
        path, _ = self.save()
        original = self.window.core.raw_data
        self.path.rename(self.path.with_suffix(".moved"))
        self.assertFalse(self.window._load_view_recipe(path, confirm=False, relocate=lambda _: None))
        self.assertIs(self.window.core.raw_data, original)

    def test_native_preflight_matches_live_numerics_for_each_kind(self):
        cases = [
            ("time_integral", {"t_low": 0, "t_up": 2}),
            ("axis_integral", {"axis_index": 0, "low": 1, "up": 3, "mid": 2, "source_t_index": 1}),
            ("axis_integral_crop", {"axis_index": 0, "low": 1, "up": 3, "mid": 2,
                "source_t_index": 1, "crop_k_low": 1, "crop_k_up": 4, "crop_e_low": 1, "crop_e_up": 6}),
            ("second_derivative", {"source_page_kind": "home", "source_view": "3d",
                                    "source_t_index": 1, "derivative_axis": 2}),
            ("energy_dos", {"t_index": 1}),
            ("edc_curve", {"source_mode": "frame", "source_t_index": 1,
                           "axis_index": 2, "crop_k_low": 1, "crop_k_up": 3,
                           "crop_e_low": 1, "crop_e_up": 4}),
        ]
        for kind, params in cases:
            with self.subTest(kind=kind):
                core, d = recipe_document(self.path, kind=kind, params=params)
                evaluator = RecipeEvaluator(My3DAnalyzer, core, d)
                page = evaluator.pages["target"]
                result = evaluator.evaluate(page)
                self.assertIn(result["view"], ("1d", "2d", "3d"))
                values = result.get("data", result.get("y_data"))
                self.assertGreater(np.asarray(values).size, 0)
                self.window.timeline_bar.restore_state(page.params["control_state"]["image"])
                live = self.window._compute_render_context(page)
                np.testing.assert_allclose(values, live.get("data", live.get("y_data")), equal_nan=True)

    def test_preview_recipe_is_frozen_when_source_frame_changes(self):
        path, snapshot = self.save()
        old = copy.deepcopy(snapshot.view_recipe)
        self.window.timeline_bar.slider_time.setValue(2)
        self.assertEqual(snapshot.view_recipe, old)
        self.assertNotEqual(capture_snapshot(self.window).view_recipe["state_digest"], old["state_digest"])

    def test_global_denoise_restored_independently_of_page_snapshot(self):
        from bandscope.processing.denoise_engines import DenoiseEngines
        w = self.window
        w.page_render.combo_n1.setCurrentIndex(1)
        methods = w._normalize_denoise_methods(w.page_render.get_denoise_settings())
        self.assertTrue(methods)
        expected = DenoiseEngines.apply_pipeline(w.original_raw_data, methods)
        w._commit_shared_denoise(methods, expected)
        path, before = self.save()
        other = control_window()
        self.addCleanup(other.close)
        other._load_view_recipe(path, confirm=False)
        self.assertEqual(other.global_denoise_methods, methods)
        np.testing.assert_allclose(other.current_render_context["data"], before.payload["volume"], rtol=1e-6)

    def test_roi_and_ordered_crop_erase_chain_roundtrip(self):
        w = self.window
        scope = w._register_roi_scope((1, 4, 1, 5, 1, 6))
        params = {"control_state": copy.deepcopy(w._capture_control_state()), "crop_regions": [
            CropSelection("3d", ("X", "Y", "E"), (-.65, .65, -1.4, 1.4, -.75, .75)).to_dict(),
            CropSelection("3d", ("X", "Y", "E"), (-.3, .3, -.8, .8, -.2, .2), operation="erase").to_dict()]}
        page = AnalysisPageSpec("roi", "ROI", "home", "test", params=params,
                               source_page_id=w.left_workspace.home_page_id,
                               data_scope_id=scope.scope_id)
        w.left_workspace.add_page(page)
        path, before = self.save()
        w._load_view_recipe(path, confirm=False)
        self.assertEqual(w.active_page_spec.data_scope_id, scope.scope_id)
        self.assertEqual(w.data_scopes[scope.scope_id], scope)
        after = capture_snapshot(w)
        np.testing.assert_allclose(after.payload["volume"], before.payload["volume"], equal_nan=True)

    def test_cross_file_comparison_and_log_retain_source_recipes(self):
        w = self.window
        def energy_page(pid):
            p = AnalysisPageSpec(pid, pid, "energy_dos", "test",
                                 source_page_id=w.left_workspace.home_page_id,
                                 params={"t_index": 0})
            w._seed_control_state_for_spec(p)
            w.left_workspace.add_page(p)
        energy_page("from_a")
        self.assertTrue(w._copy_current_curve_to_clipboard())
        frozen_id = w.curve_clipboard["recipe_id"]
        second = self.path.with_name("b.npz")
        synthetic_npz(second, seed=9, descending=True)
        w.load_data(str(second))
        energy_page("from_b")
        self.assertTrue(w._paste_curve_to_comparison_page())
        self.assertTrue(w._apply_log_to_current_curves())
        before = copy.deepcopy(w.current_render_context)
        path, snapshot = self.save()
        self.assertEqual(len(snapshot.view_recipe["sources"]), 2)
        self.assertIn(frozen_id, snapshot.view_recipe["recipes"])
        self.path.unlink()  # Auxiliary data is not required to view frozen curves.
        w._load_view_recipe(path, confirm=False)
        self.assertEqual(w.active_page_spec.page_kind, "log_curve")
        for a, b in zip(before["curves"], w.current_render_context["curves"]):
            np.testing.assert_allclose(a["y_data"], b["y_data"], equal_nan=True)

    def test_missing_plugin_restores_waterfall_and_successful_params(self):
        w = self.window
        context = {"view": "waterfall", "energy_axis": np.linspace(-1, 1, 8),
                   "curves": np.ones((2, 8)), "raw_curves": np.ones((2, 8))*3,
                   "k_values": np.array([0., 1.]), "sample_indices": np.array([0, 1]),
                   "title": "Saved waterfall", "xlabel": "Intensity", "ylabel": "E"}
        page = AnalysisPageSpec("plugin", "插件结果", "plugin_derived", "test",
                                source_page_id=w.left_workspace.home_page_id,
                                params={"derived_params": {"step": 1}})
        w._seed_control_state_for_spec(page)
        w.left_workspace.add_page_inactive(page)
        w._remember_derived_recipe(page, context)
        saved = copy.deepcopy(w._recipe_derived_results[page.page_id])
        page.params["_restored_result"] = saved
        page.params["derived_params"] = {"step": -1}  # A failed edit must not describe the visible result.
        w.left_workspace.activate_page(page.page_id)
        path, before = self.save()
        target = next(p for p in before.view_recipe["pages"] if p["page_id"] == "plugin")
        self.assertEqual(target["params"]["derived_params"], {"step": 1})
        w._load_view_recipe(path, confirm=False)
        self.assertTrue(w.current_render_context["restored_snapshot"])
        self.assertIn("无法重新计算", w.left_workspace.scope_label.text())
        np.testing.assert_allclose(w.current_render_context["raw_curves"], context["raw_curves"])
        self.assertIsNotNone(capture_snapshot(w).view_recipe)

    def test_commit_failure_rolls_back_to_previous_data_and_pages(self):
        path, _ = self.save()
        original = self.window.core
        pages = list(self.window.left_workspace.page_specs)
        apply = self.window._apply_recipe_workspace
        def fail_after_apply(*args):
            apply(*args)
            raise RuntimeError("commit failed")
        with patch.object(self.window, "_apply_recipe_workspace", side_effect=fail_after_apply):
            with self.assertRaisesRegex(RuntimeError, "commit failed"):
                self.window._load_view_recipe(path, confirm=False)
        self.assertIs(self.window.core, original)
        self.assertEqual(list(self.window.left_workspace.page_specs), pages)

    def test_frozen_effect_wins_over_installed_plugin_settings(self):
        path, snapshot = self.save()
        document = snapshot.view_recipe
        weights = np.linspace(.2, 1.7, self.raw.shape[2])
        document["view_state"]["opacity_multiplier"] = weights.tolist()
        document["state_digest"] = state_digest(document)
        path.write_text(ViewRecipeDocument(document).dumps(), encoding="utf-8")
        self.window._load_view_recipe(path, confirm=False)
        with patch.object(self.window.plugin_session, "voxel_multiplier", side_effect=AssertionError("live plugin called")):
            _, actual = self.window._plugin_render_context(self.window.current_render_context)
            np.testing.assert_allclose(actual, weights)
            after = capture_snapshot(self.window)
            np.testing.assert_allclose(after.payload["opacity_multiplier"], weights)

    def test_matching_derived_plugin_recomputes_from_original_data(self):
        from bandscope.app_metadata import APP_VERSION
        from bandscope.extensions.plugin_manager import PluginManager, install_package
        from scripts.release.build_plugin import build as build_package
        from tests.support.plugins import synthetic_source
        root = Path(self.temp.name)
        archive = build_package("edc_waterfall", root / "release", APP_VERSION)
        install_package(archive, root=root / "extensions", source=synthetic_source())
        manager = PluginManager(root=root / "extensions")
        manager.startup()
        w = self.window
        old_manager = w.plugin_session.manager
        self.addCleanup(old_manager.shutdown)
        w.plugin_session.manager = manager
        record = manager.record("edc_waterfall")
        self.assertTrue(record.ready, record.load_error)
        w.page_data.combo_ax.setCurrentIndex(0)
        w.on_apply_axis_integral()
        pid = w.plugin_session.invoke_integration_action("edc_waterfall", "create")
        self.assertIsNotNone(pid)
        path, _ = self.save()
        with patch.object(record.instance, "compute_derived_result", wraps=record.instance.compute_derived_result) as compute:
            w._load_view_recipe(path, confirm=False)
            self.assertGreaterEqual(compute.call_count, 1)
        self.assertFalse(w.active_page_spec.params.get("_restored_result"))


if __name__ == "__main__":
    unittest.main()
