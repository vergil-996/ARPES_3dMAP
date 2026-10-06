# -*- coding: utf-8 -*-
"""「二维积分演示」插件：纯数值部分与面板行为。

数值部分与 NumPy 参考计算逐项比较；面板用一个替身宿主驱动，不依赖主窗口。
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.extensions.api import (
    AnalysisCancelled,
    AnalysisCurve1D,
    AnalysisInput2D,
    AnalysisUnavailable,
    CancelToken,
    read_only_array,
)
from plugins.integral_demo.analysis import (
    AXIS_X,
    AXIS_Y,
    integrate_along,
    run_integral,
)
from plugins.integral_demo.entry import IntegralDemoPlugin
from plugins.integral_demo.panel import IntegralDemoPanel


def make_snapshot(data=None, *, x=None, y=None) -> AnalysisInput2D:
    data = np.asarray(data if data is not None else [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    x = np.asarray(x if x is not None else np.arange(data.shape[0]) * 0.5, dtype=float)
    y = np.asarray(y if y is not None else np.arange(data.shape[1]) * 2.0, dtype=float)
    return AnalysisInput2D(
        plugin_id="integral_demo",
        page_id="page-1",
        page_title="kx-E 积分结果",
        snapshot_id="snap-1",
        data_generation=7,
        data=read_only_array(data),
        x=read_only_array(x),
        y=read_only_array(y),
        x_label="kx",
        x_unit="1/Å",
        y_label="E",
        y_unit="eV",
        title="kx-E 积分结果",
    )


class IntegrateTests(unittest.TestCase):
    def test_integrate_along_x_matches_numpy(self):
        data = np.arange(12.0).reshape(3, 4) ** 2
        result = integrate_along(data, AXIS_X)
        np.testing.assert_array_equal(result, data.sum(axis=0))

    def test_integrate_along_y_matches_numpy(self):
        data = np.arange(12.0).reshape(3, 4) ** 2
        result = integrate_along(data, AXIS_Y)
        np.testing.assert_array_equal(result, data.sum(axis=1))

    def test_result_is_bitwise_identical(self):
        rng = np.random.default_rng(20261006)
        data = rng.random((17, 23))
        for axis in (AXIS_X, AXIS_Y):
            with self.subTest(axis=axis):
                reference = data.sum(axis=0 if axis == AXIS_X else 1)
                self.assertTrue(
                    np.array_equal(integrate_along(data, axis), reference),
                    "分块求和必须与 NumPy 参考逐位一致",
                )

    def test_unknown_axis_is_rejected(self):
        with self.assertRaises(ValueError):
            integrate_along(np.zeros((2, 2)), "z")

    def test_non_2d_is_rejected(self):
        with self.assertRaises(ValueError):
            integrate_along(np.zeros(3), AXIS_X)

    def test_cancel_is_checked_between_chunks(self):
        token = CancelToken()
        token.cancel()
        with self.assertRaises(AnalysisCancelled):
            integrate_along(np.zeros((4, 4)), AXIS_X, cancel=token, chunk=1)

    def test_small_input_uses_a_single_chunk(self):
        # 分块大小远大于常见二维结果：结果与直接 sum 完全相同，不引入额外舍入。
        data = np.arange(600.0).reshape(20, 30) / 7.0
        self.assertTrue(np.array_equal(integrate_along(data, AXIS_X), data.sum(axis=0)))


class RunIntegralTests(unittest.TestCase):
    def test_curve_uses_the_other_axis_and_keeps_units(self):
        snapshot = make_snapshot()
        curve = run_integral(snapshot, {"axis": AXIS_X}, CancelToken())
        self.assertIsInstance(curve, AnalysisCurve1D)
        np.testing.assert_array_equal(curve.x, snapshot.y)
        np.testing.assert_array_equal(curve.y, snapshot.data.sum(axis=0))
        self.assertEqual(curve.x_label, "E")
        self.assertEqual(curve.x_unit, "eV")
        self.assertEqual(curve.y_label, "Intensity")
        self.assertIn("沿 x 轴积分", curve.title)
        self.assertEqual(curve.params["axis"], AXIS_X)
        self.assertEqual(curve.params["source_page"], "page-1")
        self.assertEqual(curve.params["source_shape"], list(snapshot.shape))

    def test_integrate_along_y_returns_the_x_axis(self):
        snapshot = make_snapshot()
        curve = run_integral(snapshot, {"axis": AXIS_Y}, CancelToken())
        np.testing.assert_array_equal(curve.x, snapshot.x)
        self.assertEqual(curve.x_unit, "1/Å")

    def test_source_intensity_is_untouched(self):
        snapshot = make_snapshot()
        before = snapshot.data.tobytes()
        run_integral(snapshot, {"axis": AXIS_X}, CancelToken())
        self.assertEqual(snapshot.data.tobytes(), before)
        self.assertFalse(snapshot.data.flags.writeable)

    def test_unknown_axis_raises(self):
        with self.assertRaises(ValueError):
            run_integral(make_snapshot(), {"axis": "q"}, CancelToken())


class _StubHost:
    """替身宿主：记录调用，按脚本返回快照或忙碌。"""

    def __init__(self, snapshot=None, *, unavailable="", busy=False):
        self.snapshot = snapshot
        self.unavailable = unavailable
        self.busy = busy
        self.submitted = []
        self.cancelled = []

    def capture_analysis_input(self):
        if self.unavailable:
            raise AnalysisUnavailable(self.unavailable)
        return self.snapshot

    def submit_analysis(self, snapshot, work, *, title="", params=None):
        if self.busy:
            return None
        self.submitted.append((snapshot, work, title, params))
        return object()

    def cancel_analysis(self, handle):
        self.cancelled.append(handle)


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_run_submits_with_the_selected_axis(self):
        host = _StubHost(make_snapshot())
        panel = IntegralDemoPanel(host)
        panel.combo_axis.setCurrentIndex(panel.combo_axis.findData(AXIS_Y))
        panel.on_run()

        self.assertEqual(len(host.submitted), 1)
        snapshot, work, title, params = host.submitted[0]
        self.assertIs(snapshot, host.snapshot)
        self.assertIs(work, run_integral)
        self.assertEqual(params, {"axis": AXIS_Y})
        self.assertIn("积分", title)
        self.assertFalse(panel.btn_run.isEnabled())
        self.assertTrue(panel.btn_cancel.isEnabled())

    def test_unavailable_reason_is_shown(self):
        host = _StubHost(unavailable="当前结果正在计算中，请等计算完成后重试。")
        panel = IntegralDemoPanel(host)
        panel.on_run()
        self.assertIn("正在计算中", panel.status.text())
        self.assertEqual(host.submitted, [])

    def test_busy_does_not_start_a_task(self):
        host = _StubHost(make_snapshot(), busy=True)
        panel = IntegralDemoPanel(host)
        panel.on_run()
        self.assertIn("稍后再试", panel.status.text())
        self.assertTrue(panel.btn_run.isEnabled())

    def test_cancel_forwards_to_the_host(self):
        host = _StubHost(make_snapshot())
        panel = IntegralDemoPanel(host)
        panel.on_run()
        panel.on_cancel()
        self.assertEqual(len(host.cancelled), 1)

    def test_finished_restores_the_buttons(self):
        host = _StubHost(make_snapshot())
        panel = IntegralDemoPanel(host)
        panel.on_run()
        panel.on_task_finished("分析完成：结果页已加入左侧页面树。")
        self.assertTrue(panel.btn_run.isEnabled())
        self.assertFalse(panel.btn_cancel.isEnabled())


class PluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_manifest_declares_the_analysis_capabilities(self):
        import json
        from pathlib import Path

        manifest = json.loads(
            (Path(__file__).resolve().parents[3] / "plugins" / "integral_demo" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["api_version"], 2)
        self.assertEqual(
            set(manifest["capabilities"]),
            {"data_snapshot_2d", "analysis_task", "result_curve_1d"},
        )

    def test_panel_state_round_trips(self):
        plugin = IntegralDemoPlugin()
        host = _StubHost(make_snapshot())
        panel = plugin.create_panel(host)
        panel.combo_axis.setCurrentIndex(panel.combo_axis.findData(AXIS_Y))
        plugin._remember_axis()

        state = plugin.export_state()
        self.assertEqual(state, {"axis": AXIS_Y})

        plugin.restore_state({"axis": AXIS_X})
        self.assertEqual(plugin._last_axis, AXIS_X)

    def test_finished_callback_updates_the_panel(self):
        plugin = IntegralDemoPlugin()
        host = _StubHost(make_snapshot())
        panel = plugin.create_panel(host)
        panel.on_run()
        plugin.on_analysis_finished(object(), "succeeded")
        self.assertTrue(panel.btn_run.isEnabled())


if __name__ == "__main__":
    unittest.main()
