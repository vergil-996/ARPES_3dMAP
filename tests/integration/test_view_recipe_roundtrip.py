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
from bandscope.processing.denoise_config import (
    GAUSSIAN_METHOD_NAME,
    LOW_RANK_METHOD_NAME,
    MEDIAN_METHOD_NAME,
    NLM_METHOD_NAME,
    SG_METHOD_NAME,
    WAVELET_METHOD_NAME,
)
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
        self.assertTrue(all("path" not in source for source in snapshot.view_recipe["sources"].values()))
        path = self.path.parent / "figure.png.bsview.json"
        path.write_text(ViewRecipeDocument(snapshot.view_recipe).dumps(), encoding="utf-8")
        return path, snapshot

    @staticmethod
    def _denoise_params():
        return {
            GAUSSIAN_METHOD_NAME: {"sigma_kx": 0.0, "sigma_ky": 1.25, "sigma_e": 0.75},
            MEDIAN_METHOD_NAME: {"size_kx": 1, "size_ky": 3, "size_e": 5},
            SG_METHOD_NAME: {"window_length": 7, "polyorder": 3, "smoothing_axis": "ky"},
            WAVELET_METHOD_NAME: {
                "wavelet": "haar", "level": 1, "threshold_rule": "sure",
                "threshold_mode": "hard", "strength": 1.5, "noise_sigma": 0.25,
            },
            NLM_METHOD_NAME: {
                "patch_size": 3, "patch_distance": 2, "h_scale": 0.85, "noise_sigma": 0.25,
            },
            LOW_RANK_METHOD_NAME: {"rank": 2},
        }

    @classmethod
    def _denoise_nodes(cls, methods=None):
        return [
            {
                "id": f"node-{index}",
                "label": method,
                "params": copy.deepcopy(params),
                "enabled": True,
            }
            for index, (method, params) in enumerate(cls._denoise_params().items())
            if methods is None or method in methods
        ]

    def _set_denoise_drafts(self, window, methods=None):
        window.page_render.denoise_pipeline.restore_state({
            "nodes": self._denoise_nodes(methods),
        })

    def test_home_roundtrip_controls_camera_titles_and_relocation(self):
        w = self.window
        w.timeline_bar.slider_time.setValue(2)
        w.timeline_bar.set_rotation_angle(-7.5)
        w.on_rotation_angle_changed()
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
        self.assertTrue(other._load_view_recipe(
            path, confirm=False, relocate=lambda _: str(moved)
        ))
        self.assertEqual(other.timeline_bar.slider_time.value(), 2)
        self.assertEqual(other.timeline_bar.get_rotation_angle(), -7.5)
        self.assertEqual(other.canvas_tool_overlay.rotation_panel.dial.angle(), -7.5)
        self.assertTrue(other.canvas_tool_overlay.rotation_button.isChecked())
        self.assertIn("−7.5°", other.canvas_tool_overlay.rotation_button.toolTip())
        self.assertEqual(other._get_display_levels(), w._get_display_levels())
        restored_key = axis_title_key(other.left_workspace.home_page_id, "3d", ("X", "Y", "E"))
        self.assertEqual(other._axis_titles().text(restored_key, "X"), "Custom momentum")
        other._restore_recipe_scene(other.active_page_spec)
        np.testing.assert_allclose(other.plotter.camera.position, snapshot.camera["position"])
        np.testing.assert_allclose(other.plotter.camera.focal_point, snapshot.camera["focal_point"])
        np.testing.assert_array_equal(other.current_render_context["data"], snapshot.payload["volume"])

    def test_old_restricted_lock_is_rebuilt_on_restored_frame_and_survives_scrubbing(self):
        path, snapshot = self.save()
        from bandscope.core.display_style import create_display_state, new_display_style
        from bandscope.core.local_enhancement import STATE_KEY
        from bandscope.core.view_recipe import digest
        from bandscope.rendering.render_core import VisualEngine

        document = copy.deepcopy(snapshot.view_recipe)
        style = new_display_style()
        style["normalization"] = {"mode": "manual", "manual_range": (0.0, 0.5)}
        page = document["pages"][0]
        page["params"][STATE_KEY] = create_display_state(style)
        node = document["recipes"].pop(page["recipe_id"])
        node["params"] = copy.deepcopy(page["params"])
        key = digest(node)
        document["recipes"][key] = node
        page["recipe_id"] = key
        document["view_state"].update(
            locked_range=(0.0, 0.5),
            display={"background_style": copy.deepcopy(style), "resolved_range": (0.0, 0.5)},
        )
        document["schema_version"] = 3
        document["state_digest"] = state_digest(document)
        path.write_text(json.dumps(plain(document), allow_nan=False), encoding="utf-8")

        other = control_window()
        self.addCleanup(other.close)
        self.addCleanup(VisualEngine.unlock_data_range)
        self.assertTrue(other._load_view_recipe(
            path, confirm=False, relocate=lambda _: str(self.path),
        ))
        fields = other._enhancement_display(other.current_render_context, other.active_page_spec)
        finite = other.current_render_context["data"][np.isfinite(other.current_render_context["data"])]
        expected = (float(finite.min()), float(finite.max()))
        self.assertEqual(fields["background_range"], expected)
        self.assertEqual(VisualEngine.locked_data_range(), expected)
        restored_view = other._restored_recipe_views[other.active_page_spec.page_id]
        self.assertNotIn("relock_full_range", restored_view)
        self.assertEqual(restored_view["locked_range"], expected)

        other.timeline_bar.slider_time.setValue(1)
        other._enhancement_display(other.current_render_context, other.active_page_spec)
        self.assertEqual(VisualEngine.locked_data_range(), expected)
        VisualEngine.unlock_data_range()
        unlocked = other._enhancement_display(other.current_render_context, other.active_page_spec)
        finite = other.current_render_context["data"][np.isfinite(other.current_render_context["data"])]
        self.assertEqual(unlocked["background_range"], (float(finite.min()), float(finite.max())))

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
        self.assertEqual({p["title"] for p in snapshot.view_recipe["pages"]}, {"input", "用户命名的积分"})
        w.left_workspace.activate_page(home_id)
        self.assertTrue(w._load_view_recipe(path, confirm=False, relocate=lambda _: str(self.path)))
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
            self.window._load_view_recipe(path, confirm=False, relocate=lambda _: str(self.path))
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
        self._set_denoise_drafts(w, [GAUSSIAN_METHOD_NAME])
        methods = w._normalize_denoise_methods(w.page_render.get_denoise_settings())
        self.assertTrue(methods)
        expected = DenoiseEngines.apply_pipeline(w.original_raw_data, methods)
        w._commit_shared_denoise(methods, expected)
        path, before = self.save()
        other = control_window()
        self.addCleanup(other.close)
        with (
            patch.object(other, "on_apply_denoise") as auto_apply,
            patch.object(DenoiseEngines, "apply_pipeline", wraps=DenoiseEngines.apply_pipeline) as evaluate,
        ):
            self.assertTrue(other._load_view_recipe(
                path, confirm=False, relocate=lambda _: str(self.path),
            ))
        auto_apply.assert_not_called()
        self.assertEqual(evaluate.call_count, 1)
        self.assertEqual(other.global_denoise_methods, methods)
        self.assertEqual(other.page_render.get_denoise_settings(), methods)
        np.testing.assert_allclose(other.current_render_context["data"], before.payload["volume"], rtol=1e-6)

    def test_all_denoise_parameter_drafts_roundtrip_without_applying(self):
        w = self.window
        self._set_denoise_drafts(w)
        expected_params = self._denoise_params()
        selected = [GAUSSIAN_METHOD_NAME, NLM_METHOD_NAME, LOW_RANK_METHOD_NAME]
        nodes = w.page_render.denoise_pipeline.export_state()["nodes"]
        for node in nodes:
            node["enabled"] = node["label"] in selected
        w.page_render.denoise_pipeline.restore_state({"nodes": nodes})
        expected_methods = w.page_render.get_denoise_settings()
        self.assertEqual([method["label"] for method in expected_methods], selected)
        expected_controls = w._capture_global_denoise_control_state()
        self.assertEqual(set(expected_controls), {"nodes", "methods", "legacy_controls"})
        self.assertEqual(expected_controls["nodes"], nodes)
        self.assertEqual(expected_controls["methods"], expected_methods)

        path, snapshot = self.save()
        self.assertEqual(snapshot.view_recipe["environment"]["denoise_controls"]["nodes"], nodes)
        self.assertEqual(snapshot.view_recipe["environment"]["denoise_methods"], [])
        other = control_window()
        self.addCleanup(other.close)
        with patch.object(other, "on_apply_denoise") as auto_apply:
            self.assertTrue(other._load_view_recipe(
                path, confirm=False, relocate=lambda _: str(self.path),
            ))
        auto_apply.assert_not_called()
        self.assertEqual(other.global_denoise_methods, [])
        self.assertEqual(other.page_render.get_denoise_settings(), expected_methods)
        self.assertEqual(other._capture_global_denoise_control_state(), expected_controls)
        restored = other.page_render.denoise_pipeline.export_state()["nodes"]
        self.assertEqual(restored, nodes)
        self.assertEqual({node["label"]: node["params"] for node in restored}, expected_params)

    def test_all_denoise_methods_and_params_roundtrip_in_applied_pipeline(self):
        from bandscope.processing.denoise_engines import DenoiseEngines

        w = self.window
        expected_params = self._denoise_params()
        for method in (
            GAUSSIAN_METHOD_NAME,
            MEDIAN_METHOD_NAME,
            SG_METHOD_NAME,
            WAVELET_METHOD_NAME,
            NLM_METHOD_NAME,
            LOW_RANK_METHOD_NAME,
        ):
            with self.subTest(method=method):
                node = {
                    "id": "applied-node",
                    "label": method,
                    "params": copy.deepcopy(expected_params[method]),
                    "enabled": True,
                }
                w.page_render.denoise_pipeline.restore_state({"nodes": [node]})
                methods = w._normalize_denoise_methods(w.page_render.get_denoise_settings())
                self.assertEqual(len(methods), 1)
                self.assertEqual(methods[0]["id"], "applied-node")
                self.assertEqual(methods[0]["label"], method)
                self.assertEqual(methods[0]["params"], expected_params[method])

                processed = DenoiseEngines.apply_pipeline(w.original_raw_data, methods)
                w._commit_shared_denoise(methods, processed)
                expected_controls = w._capture_global_denoise_control_state()
                path, before = self.save()
                other = control_window()
                self.addCleanup(other.close)
                self.assertTrue(other._load_view_recipe(
                    path, confirm=False, relocate=lambda _: str(self.path),
                ))
                self.assertEqual(other.global_denoise_methods, methods)
                self.assertEqual(other._capture_global_denoise_control_state(), expected_controls)
                restored = other.page_render.denoise_pipeline.export_state()["nodes"]
                self.assertEqual(restored, [node])
                np.testing.assert_allclose(
                    other.shared_denoised_raw_data, processed, rtol=1e-6, atol=1e-7, equal_nan=True,
                )
                np.testing.assert_allclose(
                    other.current_render_context["data"], before.payload["volume"],
                    rtol=1e-6, atol=1e-7, equal_nan=True,
                )

    def test_repeated_denoise_nodes_keep_independent_params_and_disabled_nodes_in_recipe(self):
        from bandscope.processing.denoise_engines import DenoiseEngines

        w = self.window
        params = self._denoise_params()
        first_params = {**params[GAUSSIAN_METHOD_NAME], "sigma_kx": 0.5, "sigma_ky": 0.0}
        second_params = {**params[GAUSSIAN_METHOD_NAME], "sigma_kx": 2.5, "sigma_ky": 0.0}
        disabled_params = {**params[WAVELET_METHOD_NAME], "level": 99}
        nodes = [
            {"id": "first", "label": GAUSSIAN_METHOD_NAME, "params": first_params, "enabled": True},
            {"id": "second", "label": GAUSSIAN_METHOD_NAME, "params": second_params, "enabled": True},
            {"id": "disabled", "label": WAVELET_METHOD_NAME, "params": disabled_params, "enabled": False},
        ]
        w.page_render.denoise_pipeline.restore_state({"nodes": nodes})
        methods = w.page_render.get_denoise_settings()
        self.assertEqual([method["id"] for method in methods], ["first", "second"])
        self.assertEqual(methods[0]["params"], first_params)
        self.assertEqual(methods[1]["params"], second_params)
        processed = DenoiseEngines.apply_pipeline(w.original_raw_data, methods)
        w._commit_shared_denoise(methods, processed)

        path, before = self.save()
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["environment"]["denoise_controls"]["nodes"], nodes)
        self.assertEqual(document["environment"]["denoise_methods"], methods)
        other = control_window()
        self.addCleanup(other.close)
        with patch.object(other, "on_apply_denoise") as auto_apply:
            self.assertTrue(other._load_view_recipe(
                path, confirm=False, relocate=lambda _: str(self.path),
            ))
        auto_apply.assert_not_called()
        self.assertEqual(other.page_render.denoise_pipeline.export_state(), {"nodes": nodes})
        self.assertEqual(other.page_render.get_denoise_settings(), methods)
        self.assertEqual(other.global_denoise_methods, methods)
        np.testing.assert_allclose(
            other.shared_denoised_raw_data, processed, rtol=1e-6, atol=1e-7, equal_nan=True,
        )
        np.testing.assert_allclose(
            other.current_render_context["data"], before.payload["volume"],
            rtol=1e-6, atol=1e-7, equal_nan=True,
        )

    def test_legacy_and_mojibake_denoise_aliases_restore_current_algorithms(self):
        from bandscope.processing.denoise_engines import DenoiseEngines

        w = self.window
        path, snapshot = self.save()
        cases = (
            (
                "频域平滑",
                GAUSSIAN_METHOD_NAME,
                "gaussian",
                {"sigma_kx": 0.5, "sigma_ky": 0.0, "sigma_e": 0.0},
            ),
            (
                "滑动平均",
                MEDIAN_METHOD_NAME,
                "median",
                {"size_kx": 1, "size_ky": 1, "size_e": 3},
            ),
            (
                "Savitzky-Golay婊ゆ尝",
                SG_METHOD_NAME,
                "sg",
                {"window_length": 5, "polyorder": 2, "smoothing_axis": "ky"},
            ),
            (
                "灏忔尝鍘诲櫔",
                WAVELET_METHOD_NAME,
                "wavelet",
                {
                    "wavelet": "haar", "level": 1, "threshold_rule": "universal",
                    "threshold_mode": "soft", "strength": 1.0, "noise_sigma": 0.1,
                },
            ),
            (
                "贝叶斯去噪",
                NLM_METHOD_NAME,
                "nlmeans",
                {"patch_size": 3, "patch_distance": 1, "h_scale": 1.0, "noise_sigma": 0.1},
            ),
        )
        for alias, canonical, parameter_group, params in cases:
            with self.subTest(alias=alias):
                document = copy.deepcopy(snapshot.view_recipe)
                controls = {
                    "methods": [alias, "None", "None"],
                    parameter_group: copy.deepcopy(params),
                }
                document["environment"]["denoise_methods"] = [
                    {"label": alias, "params": copy.deepcopy(params)}
                ]
                document["environment"]["denoise_controls"] = controls
                document["state_digest"] = state_digest(document)
                path.write_text(ViewRecipeDocument(document).dumps(), encoding="utf-8")
                canonical_method = {"label": canonical, "params": copy.deepcopy(params)}
                expected = DenoiseEngines.apply_pipeline(w.original_raw_data, [canonical_method])

                other = control_window()
                self.addCleanup(other.close)
                self.assertTrue(other._load_view_recipe(
                    path, confirm=False, relocate=lambda _: str(self.path),
                ))
                restored = other.page_render.denoise_pipeline.export_state()["nodes"]
                self.assertEqual(len(restored), 1)
                self.assertEqual(restored[0]["label"], canonical)
                self.assertEqual(restored[0]["params"], params)
                self.assertTrue(restored[0]["enabled"])
                self.assertEqual(other.page_render.get_denoise_settings(), [{
                    "id": restored[0]["id"], "label": canonical, "params": params,
                }])
                np.testing.assert_allclose(
                    other.shared_denoised_raw_data, expected,
                    rtol=1e-6, atol=1e-7, equal_nan=True,
                )

    def test_sg_canonical_axis_keys_restore_in_recipe_controls(self):
        path, snapshot = self.save()
        for axis_key in ("kx", "ky", "e"):
            with self.subTest(axis_key=axis_key):
                document = copy.deepcopy(snapshot.view_recipe)
                params = {"window_length": 5, "polyorder": 2, "smoothing_axis": axis_key}
                document["environment"]["denoise_controls"] = {
                    "methods": [SG_METHOD_NAME, "None", "None"],
                    "sg": copy.deepcopy(params),
                }
                document["state_digest"] = state_digest(document)
                path.write_text(ViewRecipeDocument(document).dumps(), encoding="utf-8")

                other = control_window()
                self.addCleanup(other.close)
                self.assertTrue(other._load_view_recipe(
                    path, confirm=False, relocate=lambda _: str(self.path),
                ))
                self.assertEqual(other.global_denoise_methods, [])
                restored = other.page_render.denoise_pipeline.export_state()["nodes"]
                self.assertEqual(len(restored), 1)
                self.assertEqual(restored[0]["label"], SG_METHOD_NAME)
                self.assertEqual(restored[0]["params"], params)
                self.assertEqual(other.page_render.get_denoise_settings(), [{
                    "id": restored[0]["id"], "label": SG_METHOD_NAME, "params": params,
                }])

    def test_thin_roi_wavelet_preflight_rejection_keeps_committed_state(self):
        from bandscope.processing.denoise_engines import DenoiseEngines

        w = self.window
        w.page_render.denoise_pipeline.restore_state({"nodes": [{
            "id": "previous-gaussian",
            "label": GAUSSIAN_METHOD_NAME,
            "params": {"sigma_kx": 0.5, "sigma_ky": 0.0, "sigma_e": 0.0},
            "enabled": True,
        }]})
        previous_methods = w.page_render.get_denoise_settings()
        previous_data = DenoiseEngines.apply_pipeline(w.original_raw_data, previous_methods)
        w._commit_shared_denoise(previous_methods, previous_data)
        scope = w._register_roi_scope((0, 0, 0, 6, 0, 7))
        page = AnalysisPageSpec(
            "thin-roi",
            "Thin ROI",
            "home",
            "test",
            params={"control_state": copy.deepcopy(w._capture_control_state())},
            source_page_id=w.left_workspace.home_page_id,
            data_scope_id=scope.scope_id,
        )
        w.left_workspace.add_page_inactive(page)
        self.assertEqual(scope.compact_shape[:3], (1, 7, 8))

        previous_methods = copy.deepcopy(w.global_denoise_methods)
        previous_signature = w.shared_denoise_signature
        previous_data = w.shared_denoised_raw_data
        previous_values = previous_data.copy()
        previous_version = w.shared_denoise_version
        previous_scope_signatures = copy.deepcopy(w._scope_committed_signatures)
        previous_pending = (
            copy.deepcopy(w._pending_denoise_methods),
            w._pending_denoise_signature,
            w._pending_denoise_scope_id,
        )
        previous_ready = w._render_exact_ready
        previous_context = w.current_render_context

        w.page_render.denoise_pipeline.restore_state({"nodes": [{
            "id": "thin-wavelet",
            "label": WAVELET_METHOD_NAME,
            "params": {**self._denoise_params()[WAVELET_METHOD_NAME], "level": 1},
            "enabled": True,
        }]})
        with (
            patch.object(w, "_show_message") as show_message,
            patch.object(w.refresh_coordinator, "submit_compute") as submit_compute,
            patch.object(w, "request_refresh") as request_refresh,
        ):
            w.on_apply_denoise()

        show_message.assert_not_called()
        self.assertEqual(w.page_render.denoise_pipeline._execution_state, "error")
        message = w.page_render.denoise_pipeline.error_label.text()
        self.assertIn(scope.label, message)
        self.assertIn(WAVELET_METHOD_NAME, message)
        self.assertRegex(message, r"(?:请求|级别|层数|requested)[^\n]*1")
        self.assertRegex(message, r"(?:上限|可用|maximum)[^\n]*0")
        submit_compute.assert_not_called()
        request_refresh.assert_not_called()
        self.assertEqual(w.global_denoise_methods, previous_methods)
        self.assertIs(w.shared_denoised_raw_data, previous_data)
        np.testing.assert_array_equal(w.shared_denoised_raw_data, previous_values)
        self.assertEqual(w.shared_denoise_signature, previous_signature)
        self.assertEqual(w.shared_denoise_version, previous_version)
        self.assertEqual(w._scope_committed_signatures, previous_scope_signatures)
        self.assertEqual(
            (w._pending_denoise_methods, w._pending_denoise_signature, w._pending_denoise_scope_id),
            previous_pending,
        )
        self.assertEqual(w._render_exact_ready, previous_ready)
        self.assertIs(w.current_render_context, previous_context)

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
        w._load_view_recipe(path, confirm=False, relocate=lambda _: str(self.path))
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
        w._load_view_recipe(path, confirm=False, relocate=lambda _: str(second))
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
        from bandscope.core.display_style import create_display_state, new_display_style, new_render_quality
        style = new_display_style()
        style["opacity"] = {"points": [[0.0, 0.0], [0.2, 0.6], [1.0, 1.0]]}
        quality = new_render_quality()
        quality["interpolation"] = "nearest"
        page.params["local_enhancement"] = create_display_state(style)
        self.addCleanup(w.settings.remove, "rendering/quality")
        w.set_render_quality(quality)
        w.left_workspace.activate_page(page.page_id)
        path, before = self.save()
        target = next(p for p in before.view_recipe["pages"] if p["page_id"] == "plugin")
        self.assertEqual(target["params"]["derived_params"], {"step": 1})
        self.assertEqual(
            target["params"]["local_enhancement"]["background"]["opacity"]["points"],
            [[0.0, 0.0], [0.2, 0.6], [1.0, 1.0]],
        )
        self.assertEqual(target["params"]["render_quality"]["interpolation"], "nearest")
        w.set_render_quality(new_render_quality())
        w._load_view_recipe(path, confirm=False, relocate=lambda _: str(self.path))
        self.assertEqual(capture_snapshot(w).payload["render_quality"], new_render_quality())
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
                self.window._load_view_recipe(path, confirm=False, relocate=lambda _: str(self.path))
        self.assertIs(self.window.core, original)
        self.assertEqual(list(self.window.left_workspace.page_specs), pages)

    def test_frozen_effect_wins_over_installed_plugin_settings(self):
        path, snapshot = self.save()
        document = snapshot.view_recipe
        weights = np.linspace(.2, 1.7, self.raw.shape[2])
        document["view_state"]["opacity_multiplier"] = weights.tolist()
        document["state_digest"] = state_digest(document)
        path.write_text(ViewRecipeDocument(document).dumps(), encoding="utf-8")
        self.window._load_view_recipe(path, confirm=False, relocate=lambda _: str(self.path))
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
        axis_spec = w._build_canvas_axis_slice_spec(w.left_workspace.current_spec(), 0)
        w.left_workspace.add_page(axis_spec)
        w._on_page_create_requested(axis_spec.page_id, "edc_waterfall:create")
        derived = w.left_workspace.current_spec()
        self.assertEqual(derived.page_kind, "plugin_derived")
        self.assertEqual(derived.source_page_id, axis_spec.page_id)
        path, _ = self.save()
        with patch.object(record.instance, "compute_derived_result", wraps=record.instance.compute_derived_result) as compute:
            w._load_view_recipe(path, confirm=False, relocate=lambda _: str(self.path))
            self.assertGreaterEqual(compute.call_count, 1)
        self.assertFalse(w.active_page_spec.params.get("_restored_result"))


if __name__ == "__main__":
    unittest.main()
