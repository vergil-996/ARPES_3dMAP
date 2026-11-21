"""页面级时间区间与双端控件联动回归测试（离屏，无需 GPU/VTK）。"""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.ui.axis_interval_controller import AxisIntervalController
from bandscope.ui.timeline_bar import TimelineBar
from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.app.refactored_app import My3DAnalyzer

configure_qt_plugin_path()


def _page(page_id):
    return SimpleNamespace(
        page_id=page_id,
        page_kind="home",
        source_page_id=None,
        params={},
    )


def _build_analyzer():
    core = AnalyzerCore()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "time-interval.npz"
        np.savez(
            path,
            sample=np.ones((4, 5, 6, 5), dtype=np.float32),
            kx=np.linspace(-1, 1, 4),
            ky=np.linspace(-1, 1, 5),
            E=np.linspace(-2, 0, 6),
            time=np.array([-300.0, -80.0, 0.0, 150.0, 500.0]),
        )
        success, _info = core.load_npz(str(path))
        if success and core.source_identity.future is not None:
            core.source_identity.future.result(timeout=30)
    assert success

    analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
    analyzer.core = core
    analyzer.timeline_bar = TimelineBar()
    analyzer.time_space = None
    analyzer.time_interval = None
    analyzer.time_interval_controller = AxisIntervalController()
    analyzer.time_interval_controller.attach(
        interval_widget=analyzer.timeline_bar.time_interval_control,
        box_length=analyzer.timeline_bar.time_interval_control.input_length,
    )
    analyzer._syncing_controls = False
    analyzer.request_refresh = Mock()
    analyzer.timeline_bar.slider_time.valueChanged.connect(
        lambda value: analyzer.on_time_slider_changed(value)
    )
    analyzer.time_interval_controller.intervalChanged.connect(
        lambda: analyzer.on_time_interval_changed()
    )
    analyzer.time_interval_controller.intervalCommitted.connect(
        lambda: analyzer.flush_time_interval_refresh()
    )
    analyzer.time_interval_controller.lockChanged.connect(
        lambda locked: analyzer.on_time_interval_lock_changed(locked)
    )
    pages = {page_id: _page(page_id) for page_id in ("home", "other")}
    current = {"spec": pages["home"]}
    analyzer.left_workspace = SimpleNamespace(
        current_spec=lambda: current["spec"],
        page_by_id=lambda page_id: pages.get(page_id),
    )
    analyzer._test_pages = pages
    analyzer._test_current = current
    analyzer._bind_time_interval(current["spec"], animate=False)
    return analyzer


class TimeIntervalControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.analyzer = _build_analyzer()
        self.bar = self.analyzer.timeline_bar
        self.control = self.bar.time_interval_control
        self.spec = self.analyzer.left_workspace.current_spec()
        self.addCleanup(self.control.range_slider.coordinate_popup.close)
        self.addCleanup(self.bar.close)

    def _activate(self, spec):
        self.analyzer._test_current["spec"] = spec
        self.analyzer._bind_time_interval(spec, animate=False)

    def test_delay_axis_starts_as_a_locked_single_frame(self):
        interval = self.analyzer.time_interval
        self.assertEqual(self.control.title_label.text(), "时间延迟")
        self.assertTrue(interval.locked)
        self.assertEqual((interval.low, interval.up), (-300.0, -300.0))
        self.assertEqual(self.bar.slider_time.value(), 0)
        self.assertEqual(self.bar.slider_time.maximum(), 4)

    def test_frame_adapter_moves_the_same_page_interval(self):
        page_id = self.spec.page_id
        self.bar.slider_time.setValue(3)

        interval = self.analyzer.time_interval
        self.assertEqual(self.bar.slider_time.value(), 3)
        self.assertEqual((interval.low, interval.up), (150.0, 150.0))
        self.assertTrue(interval.locked)
        self.assertEqual(self.spec.page_id, page_id)
        self.assertEqual(self.spec.params["time_interval"]["low"], 150.0)
        self.analyzer.request_refresh.assert_called()

    def test_unlock_and_length_input_create_a_physical_time_interval(self):
        self.control.range_slider.lock_badge.click()
        self.control.input_length.setValue(230.0)
        self.control.input_length.editingFinished.emit()

        interval = self.analyzer.time_interval
        self.assertFalse(interval.locked)
        self.assertAlmostEqual(interval.low, -300.0)
        self.assertAlmostEqual(interval.up, -70.0)
        self.assertEqual(self.analyzer._time_indices_for_spec(self.spec), (0, 1))
        self.assertEqual(self.spec.params["time_interval"]["up"], -70.0)

        self.bar.slider_time.setValue(1)
        self.assertTrue(self.analyzer.time_interval.locked)
        self.assertEqual(self.analyzer.time_interval.length, 0.0)
        self.assertEqual(self.spec.params["time_interval"]["low"], -80.0)

    def test_popup_uses_physical_values_and_computation_uses_nearest_frame(self):
        self.control.range_slider.lock_badge.click()
        self.control.input_length.setValue(230.0)
        self.control.input_length.editingFinished.emit()
        slider = self.control.range_slider
        slider._show_popup("up")
        popup = slider.coordinate_popup
        popup.value_edit.setValue(100.0)
        popup.value_edit.editingFinished.emit()

        self.assertAlmostEqual(self.analyzer.time_interval.up, 100.0)
        self.assertEqual(self.analyzer._time_indices_for_spec(self.spec), (0, 3))

    def test_time_curve_page_hides_the_interval_group_and_home_restores_it(self):
        curve = _page("curve")
        curve.page_kind = "slice_dos"
        self.analyzer._test_pages["curve"] = curve

        self._activate(curve)
        self.assertTrue(self.bar.timeline_group.isHidden())
        self.assertFalse(self.bar.slider_time.isEnabled())
        self._activate(self.spec)
        self.assertFalse(self.bar.timeline_group.isHidden())
        self.assertTrue(self.bar.slider_time.isEnabled())

    def test_each_page_restores_its_own_frame(self):
        first = self.spec
        second = self.analyzer._test_pages["other"]
        self.bar.slider_time.setValue(2)
        self._activate(second)
        self.bar.slider_time.setValue(3)

        self._activate(first)
        self.assertEqual(self.bar.slider_time.value(), 2)
        self.assertEqual(self.analyzer.time_interval.center, 0.0)
        self._activate(second)
        self.assertEqual(self.bar.slider_time.value(), 3)
        self.assertEqual(self.analyzer.time_interval.center, 150.0)


if __name__ == "__main__":
    unittest.main()
