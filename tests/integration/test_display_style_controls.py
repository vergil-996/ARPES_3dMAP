import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.app.enhancement_integration import EnhancementInteractionMixin
from bandscope.core.display_style import (
    is_independent,
    new_display_style,
)
from bandscope.core.local_enhancement import STATE_KEY
from bandscope.core.opacity_curve import insert_opacity_point
from bandscope.processing.local_enhancement import cached_finite_range, clear_range_cache, resolve_display
from bandscope.rendering.render_core import VisualEngine
from bandscope.rendering.refresh_pipeline import RefreshCause, RenderQuality
from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.ui.page_render_control import RenderControlPage


class DisplayControlStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.page = RenderControlPage()
        self.addCleanup(self.page.close)
        self.addCleanup(self.page.deleteLater)

    def test_independent_controls_roundtrip_and_old_state_gets_an_editable_curve(self):
        style = self.page.get_display_style()
        self.assertTrue(is_independent(style))
        self.page.opacity_editor.set_points([[0.0, 0.0], [0.4, 0.7], [1.0, 0.2]])
        saved = self.page.export_state()
        other = RenderControlPage()
        self.addCleanup(other.close)
        self.addCleanup(other.deleteLater)
        other.restore_state({**saved, "render_quality": {"resting": "fine"}})
        self.assertEqual(other.export_state(), saved)
        self.assertEqual(other.opacity_editor.points(),
                         [[0.0, 0.0], [0.4, 0.7], [1.0, 0.2]])
        self.assertNotIn("render_quality", saved)

        other.restore_state({
            "s_low": {"value": 7}, "s_gamma": {"value": 61}, "s_up": {"value": 88},
            "combo_map": "sigmoid",
        })
        restored = other.get_display_style()
        self.assertTrue(is_independent(restored))
        self.assertEqual(restored["levels"], (7.0, 61.0, 88.0))
        self.assertEqual(restored["opacity_mode"], "sigmoid")
        self.assertEqual(restored["opacity"]["points"],
                         [[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]])

    def test_opacity_curve_editor_forwards_preview_and_commit(self):
        committed = Mock()
        changed = Mock()
        self.page.display_value_committed.connect(committed)
        self.page.display_value_changed.connect(changed)
        curve = self.page.opacity_editor.curve
        curve.pointsChanged.emit()
        curve.pointsCommitted.emit()
        self.assertEqual(changed.call_count, 1)
        self.assertEqual(committed.call_count, 1)

    def test_header_mode_selector_does_not_toggle_opacity_card(self):
        expanded = self.page.grp_opacity.toggle_button.isChecked()
        self.page.opacity_editor.interpolation_selector.buttons["linear"].click()
        self.assertEqual(self.page.grp_opacity.toggle_button.isChecked(), expanded)
        self.assertEqual(self.page.get_display_style()["opacity"]["interpolation"], "linear")
        self.page.opacity_editor.interpolation_selector.buttons["smooth"].click()
        self.assertEqual(self.page.grp_opacity.toggle_button.isChecked(), expanded)
        self.assertEqual(self.page.get_display_style()["opacity"]["interpolation"], "smooth")

    def test_page_copy_preserves_smooth_split_coefficients(self):
        points, segments, _index = insert_opacity_point(
            [[0.0, 0.0], [0.25, 0.8], [0.6, 0.2], [1.0, 0.9]],
            0.4, interpolation="smooth",
        )
        self.page.opacity_editor.set_opacity({
            "points": [list(point) for point in points],
            "interpolation": "smooth", "segments": segments,
        })
        saved = self.page.export_state()
        other = RenderControlPage()
        self.addCleanup(other.close)
        self.addCleanup(other.deleteLater)
        other.restore_state(saved)
        self.assertEqual(other.get_display_style(), self.page.get_display_style())
        self.assertEqual(other.opacity_editor.segments(), segments)

    def test_color_lock_style_refresh_keeps_button_icon_only(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.page_render = self.page

        for checked, kind in ((True, "primary"), (False, "secondary")):
            with self.subTest(checked=checked):
                self.page.btn_lock.setChecked(checked)
                analyzer._update_color_lock_button_style()

                self.assertEqual(self.page.btn_lock.attachment().text(), "")
                self.assertEqual(self.page.btn_lock.property("buttonKind"), kind)

    def test_obsolete_mode_mapping_and_parameter_controls_are_removed(self):
        for name in ("combo_opacity_preset", "btn_apply_opacity_preset", "grp_normalize",
                     "combo_normalization", "display_parameter_labels", "combo_map",
                     "btn_apply_map", "combo_display_mode", "display_parameters"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(self.page, name))
        style = self.page.get_display_style()
        self.assertEqual(style["normalization"], {"mode": "full"})
        self.assertEqual(style["opacity"]["points"], [[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]])

        before = {key: style[key] for key in ("levels", "opacity_mode", "normalization")}
        self.page.opacity_editor.set_points([[0.0, 0.0], [0.25, 0.8], [1.0, 1.0]])
        changed = self.page.get_display_style()
        self.assertEqual(changed["opacity"]["points"],
                         [[0.0, 0.0], [0.25, 0.8], [1.0, 1.0]])
        for key, value in before.items():
            self.assertEqual(changed[key], value)

    def test_opacity_card_only_shows_for_three_dimensional_views(self):
        self.page.set_display_style(new_display_style(), view="3d")
        self.assertTrue(self.page.grp_opacity.isVisibleTo(self.page))
        self.page.set_display_view("2d")
        self.assertFalse(self.page.grp_opacity.isVisibleTo(self.page))
        self.page.set_display_view("path_slice")
        self.assertFalse(self.page.grp_opacity.isVisibleTo(self.page))


class DisplayRefreshContextTests(unittest.TestCase):
    def test_only_display_refreshes_reuse_the_scientific_context(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer._compute_render_context = Mock(return_value={"data": "new"})
        previous = {"data": "old"}
        for cause in (RefreshCause.DISPLAY_MAPPING, RefreshCause.TRANSFER_FUNCTION):
            with self.subTest(cause=cause):
                self.assertIs(analyzer._context_for_display_refresh(None, cause, previous), previous)
        for cause in (RefreshCause.DATA_SOURCE, RefreshCause.GEOMETRY, RefreshCause.DENOISE):
            with self.subTest(cause=cause):
                self.assertEqual(
                    analyzer._context_for_display_refresh(None, cause, previous), {"data": "new"}
                )

    def test_stale_page_context_is_never_reused_for_display_or_capture(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer._render_context_page_id = None
        analyzer._render_exact_ready = True
        analyzer.current_render_context = {"data": "stale"}
        analyzer._compute_render_context = Mock(return_value={"data": "fresh"})
        spec = SimpleNamespace(page_id="home")
        self.assertEqual(
            analyzer._context_for_display_refresh(spec, RefreshCause.DISPLAY_MAPPING,
                                                  analyzer.current_render_context),
            {"data": "fresh"},
        )
        self.assertEqual(analyzer._context_for_publication_capture(spec), {"data": "fresh"})
        analyzer._render_context_page_id = "home"
        self.assertIs(analyzer._context_for_publication_capture(spec), analyzer.current_render_context)

    def test_preview_signature_does_not_skip_the_release_exact_frame(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        spec = SimpleNamespace(page_id="home")
        analyzer.left_workspace = SimpleNamespace(
            current_spec=lambda: spec, home_spec=lambda: spec,
        )
        analyzer.rotation_angle = 0.0
        analyzer._render_exact_ready = False
        analyzer._last_completed_refresh = SimpleNamespace(
            page_id="home", quality=RenderQuality.PREVIEW,
        )
        analyzer._last_compute_backend = "CPU"
        analyzer._last_ui_transfer_signature = "same"
        analyzer._current_transfer_signature = Mock(return_value="same")
        analyzer._schedule_display_mapping_compute = Mock(return_value=False)
        analyzer._render_active_page_timed = Mock()
        analyzer._finish_ui_refresh = Mock()
        request = SimpleNamespace(
            page_id="home", cause=RefreshCause.TRANSFER_FUNCTION,
            quality=RenderQuality.EXACT, snapshot={},
        )
        analyzer._dispatch_refresh_request(request)
        analyzer._render_active_page_timed.assert_called_once_with(
            RenderQuality.EXACT, RefreshCause.TRANSFER_FUNCTION,
        )

        analyzer._last_completed_refresh.quality = RenderQuality.EXACT
        analyzer._render_active_page_timed.reset_mock()
        analyzer._dispatch_refresh_request(request)
        analyzer._render_active_page_timed.assert_not_called()

    def test_publication_capture_reuses_only_the_current_exact_page(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer._render_exact_ready = True
        analyzer._render_context_page_id = "page"
        analyzer.current_render_context = {"data": "exact"}
        analyzer._compute_render_context = Mock(return_value={"data": "recomputed"})
        spec = SimpleNamespace(page_id="page")
        self.assertIs(analyzer._context_for_publication_capture(spec), analyzer.current_render_context)
        analyzer._compute_render_context.assert_not_called()
        other = SimpleNamespace(page_id="other")
        self.assertEqual(analyzer._context_for_publication_capture(other), {"data": "recomputed"})


class DisplayStatisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        VisualEngine.unlock_data_range()
        clear_range_cache()
        self.addCleanup(clear_range_cache)
        self.addCleanup(VisualEngine.unlock_data_range)
        self.host = EnhancementInteractionMixin.__new__(EnhancementInteractionMixin)
        host = self.host
        host._display_stats_pool = None
        host._display_stats_timer = None
        host._display_stats_lock = threading.Lock()
        host._display_stats_token = 0
        host._display_stats_job = None
        host._display_stats_task = None
        host._display_stats_result = None
        host._display_stats_signature = None
        host._display_stats_pending_signature = None
        host._render_exact_ready = True
        host._update_export_button_states = Mock()
        host.request_refresh = Mock()
        host.global_denoise_methods = []
        host.rotation_angle = 0.0
        host.shared_denoise_version = 0
        data = np.random.default_rng(23).random((12, 10, 8), dtype=np.float32)
        data[0, 0, 0] = 100.0
        data[1, 0, 0] = np.nan
        self.data = data
        state = {
            "background": new_display_style(), "regions": [], "revision": 0, "common": False,
        }
        host.active_page_spec = SimpleNamespace(
            page_id="p", page_kind="home", params={STATE_KEY: state},
        )
        host.left_workspace = SimpleNamespace(current_spec=lambda: host.active_page_spec)
        host.current_render_context = {
            "view": "3d", "data": data,
            "coords": {key: np.arange(size, dtype=float)
                       for key, size in zip(("X", "Y", "E"), data.shape)},
        }
        self.addCleanup(self._close)

    def _close(self):
        pool = self.host._display_stats_pool
        if pool is not None:
            pool.waitForDone(5000)
        self.host._invalidate_display_statistics()

    def test_full_range_runs_in_worker_once_and_color_changes_reuse_it(self):
        host = self.host
        context = host.current_render_context
        state = host.active_page_spec.params[STATE_KEY]
        with patch("numpy.percentile", wraps=np.percentile) as percentile:
            self.assertTrue(host._prepare_display_statistics(context, host.active_page_spec, state))
            self.assertIsNotNone(host._display_stats_job)
            self.assertFalse(host._render_exact_ready)
            deadline = 5000
            while host._display_stats_job is not None and deadline > 0:
                QTest.qWait(20)
                deadline -= 20
            self.assertIsNone(host._display_stats_job)
            percentile.assert_not_called()
            self.assertFalse(host._prepare_display_statistics(context, host.active_page_spec, state))
            percentile.assert_not_called()
        hit, result = cached_finite_range(self.data, state["background"])
        self.assertTrue(hit)
        finite = self.data[np.isfinite(self.data)]
        np.testing.assert_allclose(result, (float(finite.min()), float(finite.max())))
        state["background"]["levels"] = (10, 80, 90)
        state["background"]["opacity"] = {"points": [[0.0, 0.0], [0.3, 0.6], [1.0, 1.0]]}
        self.assertFalse(host._prepare_display_statistics(context, host.active_page_spec, state))
        host.request_refresh.assert_called_once()

    def test_independent_cache_recomputes_only_the_changed_channel(self):
        host = self.host
        context = host.current_render_context
        spec = host.active_page_spec
        state = spec.params[STATE_KEY]
        first = host._enhancement_display(context, spec)
        first_color = first["color"]
        first_alpha = first["alpha"]

        state["background"]["levels"] = (10, 80, 90)
        with patch("bandscope.processing.display_transfer.independent_opacity_values") as opacity:
            second = host._enhancement_display(context, spec)
        opacity.assert_not_called()
        self.assertIs(second["alpha"], first_alpha)
        self.assertIsNot(second["color"], first_color)
        self.assertEqual(second["alpha_token"], first["alpha_token"])
        self.assertGreater(second["color_token"], first["color_token"])

        state["background"]["opacity"] = {"points": [[0.0, 0.0], [0.4, 0.8], [1.0, 1.0]]}
        with patch("bandscope.processing.display_transfer.normalized_color_samples") as normalized:
            third = host._enhancement_display(context, spec)
        normalized.assert_not_called()
        self.assertIs(third["color"], second["color"])
        self.assertIsNot(third["alpha"], second["alpha"])
        expected = resolve_display(context, state)
        np.testing.assert_array_equal(third["color"], expected["color"])
        np.testing.assert_array_equal(third["alpha"], expected["alpha"])

    def test_large_cpu_mapping_uses_latest_only_compute_transport(self):
        host = self.host
        data = np.random.default_rng(29).random((80, 80, 80), dtype=np.float32)
        context = {
            "view": "3d", "data": data,
            "coords": {key: np.arange(size, dtype=float)
                       for key, size in zip(("X", "Y", "E"), data.shape)},
        }
        host.current_render_context = context
        host._context_for_display_refresh = Mock(return_value=context)
        host._enhancement_cache = None
        host.refresh_coordinator = Mock()
        spec = host.active_page_spec
        state = spec.params[STATE_KEY]
        request = SimpleNamespace(cause=RefreshCause.DISPLAY_MAPPING)

        with patch("bandscope.processing.gpu_display.enabled", return_value=False):
            self.assertTrue(host._schedule_display_mapping_compute(request, spec))
        request_arg, work = host.refresh_coordinator.submit_compute.call_args.args
        self.assertIs(request_arg, request)
        result = work()
        key, context_key, fields, update = result.metadata["display_cache"]
        self.assertEqual(update, frozenset(("color", "alpha")))
        host._cache_display_fields(key, context_key, fields, update)

        state["background"]["opacity"] = {"points": [[0.0, 0.0], [0.5, 0.9], [1.0, 1.0]]}
        host.refresh_coordinator.reset_mock()
        with patch("bandscope.processing.gpu_display.enabled", return_value=False):
            self.assertTrue(host._schedule_display_mapping_compute(request, spec))
        _request_arg, work = host.refresh_coordinator.submit_compute.call_args.args
        latest = work()
        self.assertEqual(latest.metadata["display_cache"][3], frozenset(("alpha",)))
        self.assertIs(latest.value["color"], fields["color"])
        self.assertIsNot(latest.value["alpha"], fields["alpha"])


if __name__ == "__main__":
    unittest.main()
