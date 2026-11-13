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
    new_render_quality,
)
from bandscope.core.local_enhancement import RENDER_QUALITY_KEY, STATE_KEY
from bandscope.processing.local_enhancement import cached_finite_range, clear_range_cache
from bandscope.rendering.render_core import VisualEngine
from bandscope.rendering.refresh_pipeline import RefreshCause
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

    def test_independent_controls_roundtrip_and_old_state_resets_everything(self):
        style = self.page.get_display_style()
        self.assertTrue(is_independent(style))
        self.page.combo_normalization.setCurrentIndex(self.page.combo_normalization.findData("manual"))
        self.page.manual_low.setValue(-3.0)
        self.page.manual_high.setValue(9.0)
        slider, _spin, _scale = self.page.display_parameters["threshold"]
        slider.setValue(217)
        self.page.combo_resting_quality.setCurrentIndex(
            self.page.combo_resting_quality.findData("fine")
        )
        saved = self.page.export_state()
        other = RenderControlPage()
        self.addCleanup(other.close)
        self.addCleanup(other.deleteLater)
        other.restore_state(saved)
        self.assertEqual(other.export_state(), saved)
        self.assertEqual(other.display_parameters["threshold"][0].value(), 217)
        self.assertEqual(other.combo_resting_quality.currentData(), "fine")

        other.restore_state({
            "s_low": {"value": 7}, "s_gamma": {"value": 61}, "s_up": {"value": 88},
            "combo_map": "sigmoid",
        })
        restored = other.get_display_style()
        self.assertFalse(is_independent(restored))
        self.assertEqual(restored["levels"], (7.0, 61.0, 88.0))
        self.assertEqual(restored["opacity_mode"], "sigmoid")
        self.assertFalse(other.grp_opacity.isVisible())
        self.assertEqual(other.display_parameters["threshold"][0].value(), 0)
        self.assertEqual(other.get_render_quality()["mode"], "legacy")

    def test_presets_are_removed_and_custom_parameters_have_tooltips(self):
        self.assertFalse(hasattr(self.page, "combo_opacity_preset"))
        self.assertFalse(hasattr(self.page, "btn_apply_opacity_preset"))
        style = self.page.get_display_style()
        self.assertEqual(style["opacity"]["preset"], "custom")
        expected = {
            "阈值（%）": "阈值以下体素完全透明，用于去雾",
            "起始过渡宽度（%）": "阈值边界开始的平滑过渡宽度",
            "透明度 Gamma": "控制不透明度随强度变化曲线形状",
        }
        for name, tooltip in expected.items():
            key = {"阈值（%）": "threshold", "起始过渡宽度（%）": "transition", "透明度 Gamma": "gamma"}[name]
            slider, spin, _scale = self.page.display_parameters[key]
            self.assertEqual(self.page.display_parameter_labels[name].hint, tooltip)
            self.assertEqual(slider.toolTip(), str(slider.value()))
            self.assertEqual(spin.toolTip(), "")

        from siui.core import SiGlobal
        from siui.components.tooltip import ToolTipWindow

        tooltip_window = ToolTipWindow()
        previous_window = SiGlobal.siui.windows.get("TOOL_TIP")
        SiGlobal.siui.windows["TOOL_TIP"] = tooltip_window
        try:
            label = self.page.display_parameter_labels["阈值（%）"]
            label.enterEvent(None)
            self.assertEqual(tooltip_window.text_label.text(), expected["阈值（%）"])
            self.assertTrue(tooltip_window.is_shown)
            self.assertIs(tooltip_window.nowInsideOf(), label)
            label.leaveEvent(None)
        finally:
            tooltip_window.close()
            if previous_window is None:
                SiGlobal.siui.windows.pop("TOOL_TIP", None)
            else:
                SiGlobal.siui.windows["TOOL_TIP"] = previous_window

        before = {key: style[key] for key in ("levels", "opacity_mode", "normalization")}
        self.page.display_parameters["threshold"][0].setValue(21739)
        self.page.display_parameters["transition"][0].setValue(0)
        self.page.display_parameters["gamma"][0].setValue(130)
        self.page.display_parameters["density"][0].setValue(400)
        changed = self.page.get_display_style()
        self.assertEqual(changed["opacity"], {
            "threshold": 21.739, "transition": 0.0, "gamma": 1.3,
            "density": 40.0, "preset": "custom",
        })
        for key, value in before.items():
            self.assertEqual(changed[key], value)


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

    def test_percentile_runs_in_worker_once_and_color_changes_reuse_it(self):
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
            self.assertEqual(percentile.call_count, 1)
            self.assertFalse(host._prepare_display_statistics(context, host.active_page_spec, state))
            self.assertEqual(percentile.call_count, 1)
        hit, result = cached_finite_range(self.data, state["background"])
        self.assertTrue(hit)
        finite = self.data[np.isfinite(self.data)]
        np.testing.assert_allclose(result, np.percentile(finite, (0.0, 99.5)))
        state["background"]["levels"] = (10, 80, 90)
        state["background"]["opacity"]["density"] = 35.0
        self.assertFalse(host._prepare_display_statistics(context, host.active_page_spec, state))
        host.request_refresh.assert_called_once()


if __name__ == "__main__":
    unittest.main()
