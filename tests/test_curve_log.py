import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication, QWidget

from refactored_app import My3DAnalyzer
from result_workspace import AnalysisPageSpec


def _curve_snapshot(x_data, y_data, *, title="曲线", source_title="源曲线", label="标签"):
    return {
        "curve_kind": "slice_dos",
        "title": title,
        "source_title": source_title,
        "label": label,
        "xlabel": "x",
        "x_data": list(x_data),
        "y_data": list(y_data),
    }


class LogCurveTransformTests(unittest.TestCase):
    def test_log10_curve_y_transforms_and_masks_non_positive(self):
        result = My3DAnalyzer._log10_curve_y(
            np.array([1.0, 10.0, 100.0, 0.0, -5.0, np.nan])
        )

        np.testing.assert_allclose(result[:3], [0.0, 1.0, 2.0], rtol=1e-12)
        self.assertTrue(np.all(np.isnan(result[3:])))

    def test_log10_curve_y_handles_empty_and_all_invalid(self):
        result = My3DAnalyzer._log10_curve_y(np.array([0.0, -1.0]))

        self.assertEqual(result.shape, (2,))
        self.assertTrue(np.all(np.isnan(result)))


class LogCurveContextTests(unittest.TestCase):
    def _make_spec(self, base_curve, overlay_curves=None):
        return AnalysisPageSpec(
            page_id="log_page",
            title="log10 - 源曲线",
            page_kind=My3DAnalyzer.LOG_1D_PAGE_KIND,
            source_module="data_process",
            params={
                "log_base": "log10",
                "base_curve": base_curve,
                "overlay_curves": overlay_curves or [],
            },
        )

    def test_single_curve_context(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        x_data = [1.0, 2.0, 3.0]
        y_data = [0.0, 1.0, np.nan]
        spec = self._make_spec(_curve_snapshot(x_data, y_data))

        context = analyzer._get_log_curve_context(spec)

        self.assertEqual(context["view"], "1d")
        self.assertEqual(context["title"], "log10 - 源曲线")
        self.assertEqual(context["ylabel"], "log10(Intensity) (a.u.)")
        self.assertEqual(context["xlabel"], "x")
        np.testing.assert_array_equal(np.asarray(context["x_data"]), x_data)
        np.testing.assert_array_equal(np.asarray(context["y_data"]), y_data)

    def test_multi_curve_context(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        x_data = [1.0, 2.0, 3.0]
        spec = self._make_spec(
            _curve_snapshot(x_data, [0.0, 1.0, 2.0]),
            [_curve_snapshot(x_data, [0.1, 0.2, 0.3], source_title="第二条")],
        )

        context = analyzer._get_log_curve_context(spec)

        self.assertEqual(context["view"], "1d_comparison")
        self.assertEqual(context["comparison_kind"], My3DAnalyzer.LOG_1D_PAGE_KIND)
        self.assertEqual(len(context["curves"]), 2)

    def test_overlay_with_mismatched_x_is_ignored(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        spec = self._make_spec(
            _curve_snapshot([1.0, 2.0, 3.0], [0.0, 1.0, 2.0]),
            [_curve_snapshot([4.0, 5.0, 6.0], [0.1, 0.2, 0.3])],
        )

        context = analyzer._get_log_curve_context(spec)

        self.assertEqual(context["view"], "1d")
        self.assertNotIn("curves", context)


class ApplyLogTests(unittest.TestCase):
    def _make_analyzer(self, spec, context):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.current_render_context = context
        analyzer.left_workspace = SimpleNamespace(
            current_spec=Mock(return_value=spec),
            add_page=Mock(),
        )
        analyzer._compute_render_context = Mock(return_value=None)
        analyzer._make_page_id = Mock(return_value="log_page_id")
        analyzer._make_unique_page_title = Mock(side_effect=lambda title: title)
        analyzer._seed_control_state_for_spec = Mock()
        analyzer._show_message = Mock()
        return analyzer

    @staticmethod
    def _make_spec():
        return AnalysisPageSpec(
            page_id="src_page",
            title="源曲线",
            page_kind="slice_dos",
            source_module="data_process",
            params={},
        )

    def test_single_curve_generates_log_page(self):
        spec = self._make_spec()
        x_data = [1.0, 2.0, 3.0]
        y_data = [1.0, 10.0, -5.0]
        analyzer = self._make_analyzer(
            spec,
            {
                "view": "1d",
                "x_data": x_data,
                "y_data": y_data,
                "title": "Slice Integrated Intensity vs Time",
                "xlabel": "Delay (ps)",
            },
        )

        result = analyzer._apply_log_to_current_curves()

        self.assertTrue(result)
        added = analyzer.left_workspace.add_page.call_args.args[0]
        self.assertEqual(added.page_id, "log_page_id")
        self.assertEqual(added.page_kind, My3DAnalyzer.LOG_1D_PAGE_KIND)
        self.assertEqual(added.source_page_id, "src_page")
        self.assertEqual(added.title, "log10 - 源曲线")
        self.assertEqual(added.params["log_base"], "log10")
        self.assertEqual(added.params["comparison_kind"], "slice_dos")
        self.assertEqual(added.params["overlay_curves"], [])
        np.testing.assert_allclose(
            np.asarray(added.params["base_curve"]["y_data"]),
            [0.0, 1.0, np.nan],
            rtol=1e-12,
        )

    def test_comparison_view_generates_multi_curve_log_page(self):
        spec = AnalysisPageSpec(
            page_id="cmp_page",
            title="比较 - 源曲线",
            page_kind=My3DAnalyzer.COMPARISON_PAGE_KIND,
            source_module="data_process",
            params={},
        )
        x_data = [1.0, 2.0, 3.0]
        analyzer = self._make_analyzer(
            spec,
            {
                "view": "1d_comparison",
                "xlabel": "x",
                "curves": [
                    _curve_snapshot(x_data, [1.0, 100.0, 1000.0]),
                    _curve_snapshot(x_data, [1.0, 10.0, 100.0], source_title="第二条"),
                ],
            },
        )

        result = analyzer._apply_log_to_current_curves()

        self.assertTrue(result)
        added = analyzer.left_workspace.add_page.call_args.args[0]
        self.assertEqual(added.page_kind, My3DAnalyzer.LOG_1D_PAGE_KIND)
        self.assertEqual(len(added.params["overlay_curves"]), 1)
        np.testing.assert_allclose(
            np.asarray(added.params["base_curve"]["y_data"]),
            [0.0, 2.0, 3.0],
            rtol=1e-12,
        )
        np.testing.assert_allclose(
            np.asarray(added.params["overlay_curves"][0]["y_data"]),
            [0.0, 1.0, 2.0],
            rtol=1e-12,
        )

    def test_all_non_positive_curve_is_rejected(self):
        spec = self._make_spec()
        analyzer = self._make_analyzer(
            spec,
            {
                "view": "1d",
                "x_data": [1.0, 2.0],
                "y_data": [0.0, -3.0],
                "title": "t",
                "xlabel": "x",
            },
        )

        result = analyzer._apply_log_to_current_curves()

        self.assertFalse(result)
        analyzer.left_workspace.add_page.assert_not_called()
        analyzer._show_message.assert_called_once()

    def test_missing_1d_context_is_rejected(self):
        spec = self._make_spec()
        analyzer = self._make_analyzer(spec, {"view": "2d"})

        result = analyzer._apply_log_to_current_curves()

        self.assertFalse(result)
        analyzer.left_workspace.add_page.assert_not_called()


class _FakeSignal:
    def connect(self, *args, **kwargs):
        pass


class _FakeAction:
    def __init__(self, text):
        self.text = text
        self.triggered = _FakeSignal()


class _FakeMenu:
    instances = []

    def __init__(self, owner):
        self._actions = []
        _FakeMenu.instances.append(self)

    def setStyleSheet(self, style):
        pass

    def addAction(self, text):
        action = _FakeAction(text)
        self._actions.append(action)
        return action

    def addSeparator(self):
        pass

    def exec_(self, *args):
        pass


class ContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _menu_actions(self, view):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.current_render_context = {"view": view}
        analyzer.canvas_2d = QWidget()
        analyzer._can_copy_current_curve = Mock(return_value=False)
        analyzer._can_paste_curve_to_current_page = Mock(return_value=False)
        analyzer.denoise_popup = SimpleNamespace(show_at=Mock())
        analyzer.waterfall_popup = SimpleNamespace(show_at=Mock())
        analyzer.update_controller = SimpleNamespace(check_for_updates=Mock())

        _FakeMenu.instances.clear()
        with patch("refactored_app.QMenu", _FakeMenu):
            My3DAnalyzer._show_curve_context_menu(analyzer, QPoint(3, 4))

        self.assertTrue(_FakeMenu.instances)
        return [action.text for action in _FakeMenu.instances[-1]._actions]

    def test_log_action_present_for_1d_view(self):
        self.assertIn("取对数 (log10)", self._menu_actions("1d"))

    def test_log_action_present_for_1d_comparison_view(self):
        self.assertIn("取对数 (log10)", self._menu_actions("1d_comparison"))

    def test_log_action_absent_for_2d_view(self):
        self.assertNotIn("取对数 (log10)", self._menu_actions("2d"))

    def test_log_action_absent_for_waterfall_view(self):
        self.assertNotIn("取对数 (log10)", self._menu_actions("waterfall"))


if __name__ == "__main__":
    unittest.main()
