# -*- coding: utf-8 -*-
"""阶段 F 端到端：合成演示插件 → 快照 → 后台积分 → 结果页 → 展示与导出。

演示插件走真实的构建与安装事务（``build_plugin.py`` + ``install_package``），
主窗口只替换渲染相关的部件，页面工作区、结果页创建、1D 上下文与导出都走真实
代码路径。不需要 OpenGL：本用例不创建 3D 场景。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.app_metadata import APP_VERSION
from bandscope.extensions.plugin_host import PluginSession
from bandscope.extensions.plugin_manager import PluginManager, install_package
from scripts.release.build_plugin import build as build_package
from tests.support.pages import make_workspace, page_spec
from tests.support.plugins import synthetic_source

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PAGE_ID = "page-1"
SOURCE_DATA = np.arange(12.0).reshape(3, 4) ** 2
X_COORDS = np.array([-1.0, 0.0, 1.0])
Y_COORDS = np.array([0.0, 0.5, 1.0, 1.5])


def render_context():
    return {
        "view": "2d",
        "data": SOURCE_DATA,
        "slice_info": {"axis": 2, "mode": "integral", "range": (0, 2)},
        "coords": {"X": X_COORDS, "Y": Y_COORDS},
        "plot_axes": {"x_key": "X", "y_key": "Y", "x_label": "kx", "y_label": "E"},
        "plot_logical_bounds": {"x_low": 0, "x_up": 2, "y_low": 0, "y_up": 3},
    }


class PluginAnalysisFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.sandbox = Path(self._root.name)
        self.root = self.sandbox / "extensions"

        # 真实构建 + 真实安装事务：演示插件和正式插件走同一条路。
        archive = build_package("integral_demo", self.sandbox / "release", APP_VERSION)
        install_package(archive, root=self.root, source=synthetic_source())

        self.manager = PluginManager(root=self.root)
        self.addCleanup(self.manager.shutdown)
        self.manager.startup()
        self.assertTrue(self.manager.record("integral_demo").ready,
                        self.manager.record("integral_demo").load_error)

        self.workspace = make_workspace()
        self.workspace.add_page(page_spec(SOURCE_PAGE_ID, "kx-E 结果", "axis_integral"))
        self.toasts = []

        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.left_workspace = self.workspace
        analyzer.current_render_context = render_context()
        analyzer._render_exact_ready = True
        analyzer.shared_denoise_version = 3
        analyzer._closing_down = False
        analyzer.core = SimpleNamespace(
            coord_sources={"X": "file", "Y": "file"},
            coord_units={"X": "1/Å", "Y": "eV"},
        )
        analyzer.timeline_bar = SimpleNamespace(slider_time=SimpleNamespace(value=lambda: 2))
        analyzer._current_delay_text = lambda index: f"{index} fs"
        analyzer._seed_control_state_for_spec = lambda spec: None
        analyzer._toast_success = lambda title, text: self.toasts.append((title, text))
        self.analyzer = analyzer

        self.session = PluginSession(analyzer, manager=self.manager)
        self.addCleanup(self.session.shutdown)

    def wait_for(self, predicate, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        QApplication.processEvents()
        return predicate()

    def wait_for_result(self, timeout=8.0):
        """等到分析结果页真的出现。

        不能用“页数变多”当条件：工作区本来就有主页与来源页，那条件一开始就成立，
        会在结果页出现之前抢先通过。
        """
        return self.wait_for(lambda: bool(self.result_specs()), timeout=timeout)

    def result_specs(self):
        return [
            spec
            for spec in self.workspace.page_specs.values()
            if spec.page_kind == "plugin_curve"
        ]

    def mount_panels(self):
        """挂载分析面板，返回 ``plugin_id -> panel``。"""
        panels = []
        page = SimpleNamespace(
            mount_analysis_card=lambda pid, title, panel: panels.append((pid, panel)) or panel,
            set_analysis_card_visible=lambda pid, visible, animate=True: None,
        )
        self.session.mount_analysis_cards(page)
        return dict(panels)

    def test_demo_plugin_runs_end_to_end(self):
        panels = self.mount_panels()
        self.assertIn("integral_demo", panels)
        panel = panels["integral_demo"]

        # 默认沿 x 轴积分：结果横轴是 y（E），纵轴是第一维求和。
        panel.on_run()
        self.assertTrue(self.wait_for_result())
        self.assertEqual(len(self.result_specs()), 1)
        spec = self.result_specs()[0]

        # 结果挂在来源页下，且不强制切换当前页。
        self.assertEqual(spec.source_page_id, SOURCE_PAGE_ID)
        self.assertEqual(self.workspace.current_spec().page_id, SOURCE_PAGE_ID)
        self.assertTrue(self.toasts)

        analysis = spec.params["plugin_analysis"]
        self.assertEqual(analysis["plugin_id"], "integral_demo")
        self.assertEqual(analysis["plugin_version"], "1.0.0")
        self.assertEqual(analysis["source_page_id"], SOURCE_PAGE_ID)
        self.assertEqual(analysis["params"]["axis"], "x")
        # 插件返回的参数原样登记，便于复现与对照。
        self.assertEqual(analysis["params"]["source_page"], SOURCE_PAGE_ID)
        self.assertEqual(analysis["params"]["source_shape"], [3, 4])
        self.assertEqual(analysis["data_generation"], 3)

        expected = SOURCE_DATA.sum(axis=0)
        curve = spec.params["base_curve"]
        np.testing.assert_array_equal(np.asarray(curve["y_data"]), expected)
        np.testing.assert_array_equal(np.asarray(curve["x_data"]), Y_COORDS)
        self.assertEqual(curve["xlabel"], "E (eV)")

        # 展示走宿主既有的 1D 路径。
        context = self.analyzer._get_plugin_curve_context(spec)
        self.assertEqual(context["view"], "1d")
        np.testing.assert_array_equal(np.asarray(context["y_data"]), expected)

        # 导出复用宿主已有的 1D 表格/矩阵导出。
        title, default_name, payload = self.analyzer._build_plugin_curve_export_payload(spec)
        self.assertIn("导出分析结果", title)
        self.assertTrue(default_name.endswith(".mat"))
        np.testing.assert_allclose(payload["intensity"], expected.astype(np.float32))
        np.testing.assert_allclose(payload["E"], Y_COORDS.astype(np.float32))
        self.assertEqual(self.analyzer._find_tabular_primary(payload)[0], "curve")

    def test_second_run_integrates_the_other_axis(self):
        panels = self.mount_panels()
        panel = panels["integral_demo"]
        panel.combo_axis.setCurrentIndex(panel.combo_axis.findData("y"))
        panel.on_run()
        self.assertTrue(self.wait_for_result())

        spec = self.result_specs()[0]
        np.testing.assert_array_equal(
            np.asarray(spec.params["base_curve"]["y_data"]), SOURCE_DATA.sum(axis=1)
        )
        np.testing.assert_array_equal(np.asarray(spec.params["base_curve"]["x_data"]), X_COORDS)

    def test_original_intensity_is_untouched(self):
        before = SOURCE_DATA.tobytes()
        panels = self.mount_panels()
        panels["integral_demo"].on_run()
        self.assertTrue(self.wait_for_result())
        self.assertEqual(analyzer_data_bytes(self.analyzer), before)

    def test_v1_and_v2_panels_go_to_different_slots(self):
        # 只安装演示插件（v2）时，3D 卡片槽为空、分析槽有面板。
        mounted = []
        page_render = SimpleNamespace(
            mount_extension_card=lambda pid, title, panel: mounted.append(pid) or panel
        )
        self.session.mount_cards(page_render)
        self.assertEqual(mounted, [])
        self.assertEqual(self.session.card_ids(), [])
        self.assertIn("integral_demo", self.mount_panels())

    def test_result_survives_plugin_shutdown(self):
        panels = self.mount_panels()
        panels["integral_demo"].on_run()
        self.assertTrue(self.wait_for_result())
        self.session.shutdown()

        spec = self.result_specs()[0]
        context = self.analyzer._get_plugin_curve_context(spec)
        np.testing.assert_array_equal(
            np.asarray(context["y_data"]), SOURCE_DATA.sum(axis=0)
        )


def analyzer_data_bytes(analyzer) -> bytes:
    return np.asarray(analyzer.current_render_context["data"]).tobytes()


if __name__ == "__main__":
    unittest.main()
