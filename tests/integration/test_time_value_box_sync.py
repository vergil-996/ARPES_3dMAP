# -*- coding: utf-8 -*-
"""时间积分滑条 ↔ 物理值输入框联动回归测试（离屏，无需 GPU/VTK）。

借助 My3DAnalyzer.__new__ 绕过窗口构造，只装配真实 AnalyzerCore +
DataProcessPage，直接驱动 app 侧的联动 handler。
"""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.ui.page_data_process_v2 import DataProcessPage
from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.app.refactored_app import My3DAnalyzer

configure_qt_plugin_path()

class _FakeSlider:
    """bandscope.ui.timeline_bar.slider_time 的最小替身。"""

    def setRange(self, *_args):
        pass

    def setValue(self, *_args):
        pass

    def setToolTipConvertionFunc(self, *_args):
        pass


class _FakeTimelineBar:
    """timeline_bar 的最小替身：只保留加载流程会碰到的接口。"""

    def __init__(self):
        self.slider_time = _FakeSlider()

    def set_total_frames(self, _count):
        pass


def _build_analyzer():
    core = AnalyzerCore()
    # Nonuniform, signed delays exercise physical-value/index conversion without
    # requiring the maintainer's large experimental dataset.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "time-sync.npz"
        np.savez(path, sample=np.ones((4, 5, 6, 5), dtype=np.float32),
                 kx=np.linspace(-1, 1, 4), ky=np.linspace(-1, 1, 5),
                 E=np.linspace(-2, 0, 6), time=np.array([-300., -80., 0., 150., 500.]))
        success, info = core.load_npz(str(path))
    assert success, f"测试数据加载失败: {info}"

    analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
    analyzer.core = core
    analyzer.page_data = DataProcessPage()
    analyzer.timeline_bar = _FakeTimelineBar()
    analyzer._syncing_controls = False
    analyzer._syncing_axis_value_boxes = False
    analyzer.left_workspace = SimpleNamespace(current_spec=lambda: None)
    analyzer._connect_time_value_box_signals()
    analyzer._configure_loaded_time_controls(info)
    return analyzer


class TimeValueBoxSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.analyzer = _build_analyzer()
        self.page = self.analyzer.page_data
        self.delay = np.asarray(self.analyzer.core.coords["delay"], dtype=np.float64)
        self.t_max = int(self.analyzer.core.raw_data.shape[3]) - 1

    def test_boxes_initialized_from_delay_axis(self):
        page = self.page
        self.assertEqual(float(np.min(self.delay)), page.input_t_low.minimum())
        self.assertEqual(float(np.max(self.delay)), page.input_t_low.maximum())
        self.assertEqual(float(np.min(self.delay)), page.input_t_up.minimum())
        self.assertEqual(float(np.max(self.delay)), page.input_t_up.maximum())
        self.assertEqual(0, page.s_t_low.value())
        self.assertEqual(self.t_max, page.s_t_up.value())
        self.assertAlmostEqual(float(self.delay[0]), page.input_t_low.value(), places=6)
        self.assertAlmostEqual(float(self.delay[self.t_max]), page.input_t_up.value(), places=6)

    def test_slider_to_box_syncs_physical_value(self):
        page = self.page
        page.s_t_low.setValue(1)
        self.assertAlmostEqual(float(self.delay[1]), page.input_t_low.value(), places=6)
        page.s_t_up.setValue(self.t_max - 1)
        self.assertAlmostEqual(float(self.delay[self.t_max - 1]), page.input_t_up.value(), places=6)

    def test_box_to_slider_snaps_to_nearest_index(self):
        page = self.page
        target_index = min(1, self.t_max)
        page.input_t_low.setValue(float(self.delay[target_index]))
        self.assertEqual(target_index, page.s_t_low.value())
        self.assertAlmostEqual(float(self.delay[target_index]), page.input_t_low.value(), places=6)

        # 介于两帧之间的值应吸附到最近帧
        if self.t_max >= 2:
            between = float((self.delay[1] + self.delay[2]) / 2.0)
            page.input_t_up.setValue(between)
            self.assertIn(page.s_t_up.value(), (1, 2))
            self.assertAlmostEqual(
                float(self.delay[page.s_t_up.value()]), page.input_t_up.value(), places=6
            )

    def test_cross_constraint_still_holds_via_boxes(self):
        page = self.page
        page.s_t_up.setValue(1)
        page.input_t_low.setValue(float(self.delay[self.t_max]))
        self.assertEqual(page.s_t_up.value(), page.s_t_low.value())
        self.assertAlmostEqual(page.input_t_up.value(), page.input_t_low.value(), places=6)

    def test_state_round_trip_includes_time_boxes(self):
        page = self.page
        page.s_t_low.setValue(1)
        state = page.export_state()
        self.assertIn("input_t_low", state)
        self.assertIn("input_t_up", state)
        self.assertAlmostEqual(float(self.delay[1]), state["input_t_low"]["value"], places=6)

        page.s_t_low.setValue(0)
        page.restore_state(state)
        self.assertEqual(state, page.export_state())


if __name__ == "__main__":
    unittest.main()
