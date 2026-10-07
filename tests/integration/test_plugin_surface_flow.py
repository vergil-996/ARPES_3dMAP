# -*- coding: utf-8 -*-
"""阶段 2 端到端：三维快照 → 后台能带重构 → 面结果页 → 展示与导出。

能带重构插件走真实的构建与安装事务（``build_plugin.py`` + ``install_package``），
主窗口只替换渲染相关的部件；数据域取数、结果页创建、面结果上下文与导出都走真实
代码路径。数据是测试内现算的合成体，不依赖本机实验文件，也不需要 OpenGL。
"""
from __future__ import annotations

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
from plugins.band_reconstruct import init_surface as IS
from plugins.band_reconstruct.synthetic import SyntheticBand, gaussian_edc_volume
from scripts.release.build_plugin import build as build_package
from tests.support.pages import make_workspace, page_spec
from tests.support.plugins import synthetic_source

PLUGIN_ID = "band_reconstruct"
SOURCE_PAGE_ID = "page-1"
SHAPE = (24, 24, 80)
ENERGY = (-0.4, 0.018, 80)
X_COORDS = np.linspace(-1.0, 1.0, SHAPE[0])
Y_COORDS = np.linspace(-1.0, 1.0, SHAPE[1])
E_COORDS = ENERGY[0] + ENERGY[1] * np.arange(SHAPE[2])


def make_dataset():
    """单条抛物带的合成体数据（24×24×80，18 meV 间隔）。"""
    truth = IS.parabolic_surface(X_COORDS, Y_COORDS, e0=0.0, a_x=0.35, a_y=0.35)
    dataset = gaussian_edc_volume(
        X_COORDS,
        Y_COORDS,
        E_COORDS,
        [SyntheticBand("Band 1", truth, 1.0)],
        sigma_e=0.03,
        background=0.01,
        noise="poisson",
        noise_level=5e3,
        seed=5,
    )
    return dataset, truth


class PluginSurfaceFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.sandbox = Path(self._root.name)
        self.root = self.sandbox / "extensions"

        # 真实构建 + 真实安装事务：插件源码、清单与打包路径一起过一遍。
        archive = build_package(PLUGIN_ID, self.sandbox / "release", APP_VERSION)
        install_package(archive, root=self.root, source=synthetic_source())

        self.manager = PluginManager(root=self.root)
        self.addCleanup(self.manager.shutdown)
        self.manager.startup()
        record = self.manager.record(PLUGIN_ID)
        self.assertTrue(record.ready, record.load_error)

        self.dataset, self.truth = make_dataset()
        self.raw_data = self.dataset.volume[:, :, :, np.newaxis].copy()
        self.coords = {
            "X": X_COORDS.copy(),
            "Y": Y_COORDS.copy(),
            "E": E_COORDS.copy(),
            "delay": np.array([0.0]),
        }
        self.workspace = make_workspace()
        self.workspace.add_page(page_spec(SOURCE_PAGE_ID, "kx-ky 体数据", "home"))
        self.toasts = []
        self.visible_calls = []

        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.left_workspace = self.workspace
        analyzer.current_render_context = None
        analyzer._render_exact_ready = True
        analyzer.shared_denoise_version = 11
        analyzer.original_raw_data = self.raw_data
        analyzer.original_coords = self.coords
        analyzer.rotation_angle = 0.0
        analyzer._closing_down = False
        analyzer.core = SimpleNamespace(
            raw_data=self.raw_data,
            coords=self.coords,
            coord_sources={"X": "file", "Y": "file", "E": "file"},
            coord_units={"X": "1/Å", "Y": "1/Å", "E": "eV"},
        )
        analyzer.timeline_bar = SimpleNamespace(slider_time=SimpleNamespace(value=lambda: 0))
        analyzer._current_delay_text = lambda index: f"{index} fs"
        analyzer._seed_control_state_for_spec = lambda spec: None
        analyzer._toast_success = lambda title, text: self.toasts.append((title, text))
        analyzer._get_display_state_for_spec = lambda spec=None: (self.raw_data, self.coords)
        self.analyzer = analyzer

        self.session = PluginSession(analyzer, manager=self.manager)
        self.addCleanup(self.session.shutdown)

    # ------------------------------------------------------------------ 工具
    def wait_for(self, predicate, timeout=20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        QApplication.processEvents()
        return predicate()

    def surface_specs(self):
        return [
            spec
            for spec in self.workspace.page_specs.values()
            if spec.page_kind == "plugin_surface"
        ]

    def wait_for_results(self, count=1, timeout=20.0):
        return self.wait_for(lambda: len(self.surface_specs()) >= count, timeout=timeout)

    def mount_panels(self):
        panels = []
        page = SimpleNamespace(
            mount_analysis_card=lambda pid, title, panel: panels.append((pid, panel)) or panel,
            set_analysis_card_visible=lambda pid, visible, animate=True: self.visible_calls.append(
                (pid, bool(visible))
            ),
        )
        self.session.mount_analysis_cards(page)
        return dict(panels)

    def run_one_band(self, panel):
        panel.spin_bands.setValue(1)
        row = panel._band_rows[0]
        row.box_e0.setValue(-0.02)
        row.box_shape.setValue(0.30)  # 抛物面抬升（真值：0.35 × 2 = 0.7 eV 到角落）
        panel.spin_maxiter.setValue(120)
        panel.on_run()

    # ------------------------------------------------------------------ 用例
    def test_end_to_end_surface_result(self):
        panels = self.mount_panels()
        self.assertIn(PLUGIN_ID, panels)
        panel = panels[PLUGIN_ID]

        self.run_one_band(panel)
        self.assertTrue(self.wait_for_results(), "面结果页没有出现")

        specs = self.surface_specs()
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        # 挂在来源页下，且不强制切换当前页。
        self.assertEqual(spec.source_page_id, SOURCE_PAGE_ID)
        self.assertEqual(self.workspace.current_spec().page_id, SOURCE_PAGE_ID)
        self.assertTrue(self.toasts)

        analysis = spec.params["plugin_analysis"]
        self.assertEqual(analysis["plugin_id"], PLUGIN_ID)
        self.assertEqual(analysis["plugin_version"], "1.1.0")
        self.assertEqual(analysis["data_generation"], 11)
        self.assertEqual(analysis["scope_id"], "full")

        payload = spec.params["base_surface"]
        np.testing.assert_allclose(np.asarray(payload["x"]), X_COORDS)
        np.testing.assert_allclose(np.asarray(payload["y"]), Y_COORDS)
        self.assertEqual(len(payload["bands"]), 1)
        z = np.asarray(payload["bands"][0]["z"])
        self.assertEqual(z.shape, SHAPE[:2])
        # 重构结果应当贴近真值（合成数据、验收线远宽于这里的容差）。
        self.assertLess(float(np.sqrt(np.mean((z - self.truth) ** 2))), 0.05)
        self.assertEqual(payload["z_unit"], "eV")

        # 展示：面结果页走二维渲染路径。
        context = self.analyzer._get_plugin_surface_context(spec)
        self.assertEqual(context["view"], "2d")
        self.assertEqual(context["plot_axes"]["x_key"], "X")
        np.testing.assert_allclose(np.asarray(context["data"]), z)
        self.assertEqual(context["plugin_surface"]["band_count"], 1)

        # 导出：矩阵 + x/y 坐标 + 来源元数据。
        title, default_name, export_data = self.analyzer._build_plugin_surface_export_payload(spec)
        self.assertIn("导出分析结果", title)
        self.assertTrue(default_name.endswith(".mat"))
        np.testing.assert_allclose(export_data["sample"], z.astype(np.float32))
        np.testing.assert_allclose(export_data["kx"], X_COORDS.astype(np.float32))
        np.testing.assert_allclose(export_data["ky"], Y_COORDS.astype(np.float32))
        self.assertEqual(self.analyzer._find_tabular_primary(export_data)[0], "matrix")
        self.assertEqual(str(export_data["plugin_id"][0]), PLUGIN_ID)

    def test_snapshot_matches_three_d_view(self):
        """快照取数与三维视图一致：数据域、当前帧、去噪代次、不含旋转。"""
        snapshot = self.session.capture_analysis_input_3d(PLUGIN_ID)
        self.assertEqual(snapshot.shape, SHAPE)
        self.assertEqual(snapshot.data_generation, 11)
        self.assertEqual(float(snapshot.rotation_angle), 0.0)
        self.assertEqual(snapshot.x_unit, "1/Å")
        self.assertEqual(snapshot.e_unit, "eV")
        self.assertEqual(snapshot.x_label, "kx")
        self.assertEqual(snapshot.frame_index, 0)
        np.testing.assert_allclose(snapshot.volume, self.raw_data[:, :, :, 0])
        # 只读：插件改不动快照。
        with self.assertRaises(ValueError):
            snapshot.volume[0, 0, 0] = 1.0

    def test_capture_reports_reason_when_data_is_busy(self):
        from bandscope.extensions.api import AnalysisUnavailable

        self.analyzer._render_exact_ready = False
        with self.assertRaises(AnalysisUnavailable):
            self.session.capture_analysis_input_3d(PLUGIN_ID)
        self.analyzer._render_exact_ready = True

        self.analyzer.original_raw_data = None
        with self.assertRaises(AnalysisUnavailable):
            self.session.capture_analysis_input_3d(PLUGIN_ID)

    def test_crop_restricts_the_snapshot(self):
        """ROI 数据域下快照与坐标一起按数据域切片。"""
        from bandscope.core.data_scope import DataScopeDescriptor, ScopedDataVolume

        descriptor = DataScopeDescriptor(
            "roi-1", (SHAPE[0], SHAPE[1], SHAPE[2], 1), bounds=(4, 19, 0, 23, 8, 71), generation=1
        )
        compact = ScopedDataVolume(descriptor, self.raw_data[4:20, 0:24, 8:72, :])
        self.analyzer._get_display_state_for_spec = lambda spec=None: (compact, self.coords)

        snapshot = self.session.capture_analysis_input_3d(PLUGIN_ID)
        self.assertEqual(snapshot.shape, (16, 24, 64))
        np.testing.assert_allclose(snapshot.x, X_COORDS[4:20])
        np.testing.assert_allclose(snapshot.e, E_COORDS[8:72])
        self.assertEqual(snapshot.scope_label, "ROI #1")

    def test_panel_visibility_follows_snapshot_capability(self):
        self.mount_panels()
        self.session.set_analysis_cards_visible("3d")
        self.session.set_analysis_cards_visible("2d")
        self.assertEqual(
            self.visible_calls,
            [(PLUGIN_ID, True), (PLUGIN_ID, False)],
            "三维快照插件的面板应只在三维视图显示",
        )

    def test_progress_reaches_the_panel(self):
        panels = self.mount_panels()
        panel = panels[PLUGIN_ID]
        self.run_one_band(panel)
        self.assertTrue(
            self.wait_for(lambda: panel.progress.value() > 0, timeout=10.0),
            "面板没有收到进度",
        )
        self.assertTrue(self.wait_for_results())

    def test_cancel_discards_the_result(self):
        panels = self.mount_panels()
        panel = panels[PLUGIN_ID]
        panel.spin_bands.setValue(1)
        panel.spin_maxiter.setValue(4000)  # 足够长，来得及取消
        panel.on_run()
        self.assertTrue(self.wait_for(lambda: panel._handle is not None, timeout=5.0))
        self.session.cancel_analysis(panel._handle)
        self.assertTrue(self.wait_for(lambda: panel.btn_run.isEnabled(), timeout=20.0))
        self.assertEqual(self.surface_specs(), [])
        self.assertIn("取消", panel.status.text())

    def test_stale_result_is_dropped(self):
        panels = self.mount_panels()
        panel = panels[PLUGIN_ID]
        panel.spin_bands.setValue(1)
        panel.spin_maxiter.setValue(4000)
        panel.on_run()
        self.assertTrue(self.wait_for(lambda: panel._handle is not None, timeout=5.0))
        self.analyzer.shared_denoise_version = 12  # 数据换代：结果必须作废
        self.assertTrue(self.wait_for(lambda: panel.btn_run.isEnabled(), timeout=30.0))
        self.assertEqual(self.surface_specs(), [])
        self.assertIn("作废", panel.status.text())

    # ------------------------------------------------------------------ 三维叠加
    def three_d_context(self):
        return {
            "view": "3d",
            "data": self.raw_data[:, :, :, 0],
            "coords": self.coords,
            "full_shape": SHAPE,
        }

    def attach_overlay(self):
        from bandscope.rendering.surface_overlay import SurfaceOverlayManager
        from tests.support.overlay import FakePlotter

        plotter = FakePlotter()
        self.analyzer.plotter = plotter
        self.analyzer.surface_overlay = SurfaceOverlayManager(plotter)
        return plotter

    def test_overlay_layers_follow_results_and_generation(self):
        panels = self.mount_panels()
        self.run_one_band(panels[PLUGIN_ID])
        self.assertTrue(self.wait_for_results())

        layers = self.analyzer._surface_overlay_layers()
        self.assertEqual(len(layers), 1)
        layer = layers[0]
        spec = self.surface_specs()[0]
        self.assertTrue(layer.key.startswith(spec.page_id))
        self.assertIn(spec.title, layer.label)
        self.assertEqual(layer.color, "#ff8a3d")
        np.testing.assert_allclose(layer.z, np.asarray(spec.params["base_surface"]["bands"][0]["z"]))

        # 数据换代：过期结果不再进入叠加层。
        self.analyzer.shared_denoise_version += 1
        self.assertEqual(self.analyzer._surface_overlay_layers(), [])

    def test_sync_surface_overlays_builds_and_clears_actors(self):
        plotter = self.attach_overlay()
        panels = self.mount_panels()
        self.run_one_band(panels[PLUGIN_ID])
        self.assertTrue(self.wait_for_results())
        spec = self.surface_specs()[0]

        self.analyzer._sync_surface_overlays(self.three_d_context(), spec)
        key = f"{spec.page_id}:0"
        self.assertIn(key, plotter.actors)
        actor = plotter.actors[key]
        self.assertGreater(actor.mesh.n_points, 0)
        self.assertEqual(actor.origin, (100.0, 100.0, 100.0))
        self.assertAlmostEqual(actor.options["opacity"], 0.9)

        # 切到二维页：叠加层清空。
        self.analyzer._sync_surface_overlays({"view": "2d"}, spec)
        self.assertEqual(plotter.actors, {})
        self.assertIn(key, plotter.removed)

    def test_overlay_visibility_toggle_hides_without_rebuild(self):
        plotter = self.attach_overlay()
        panels = self.mount_panels()
        self.run_one_band(panels[PLUGIN_ID])
        self.assertTrue(self.wait_for_results())
        spec = self.surface_specs()[0]
        self.analyzer._sync_surface_overlays(self.three_d_context(), spec)
        key = f"{spec.page_id}:0"
        actor = plotter.actors[key]

        self.analyzer.on_overlay_visibility_changed(key, False)
        self.assertFalse(actor.visible)
        self.assertEqual(plotter.render_count, 1)
        self.assertIs(plotter.actors[key], actor, "切显隐不该重建几何")

        self.analyzer.on_overlay_visibility_changed(key, True)
        self.assertTrue(actor.visible)

    def test_overlay_geometry_rotates_with_the_volume(self):
        plotter = self.attach_overlay()
        panels = self.mount_panels()
        self.run_one_band(panels[PLUGIN_ID])
        self.assertTrue(self.wait_for_results())
        spec = self.surface_specs()[0]

        self.analyzer.rotation_angle = 0.0
        self.analyzer._sync_surface_overlays(self.three_d_context(), spec)
        before = np.asarray(plotter.actors[f"{spec.page_id}:0"].mesh.points).copy()

        self.analyzer.rotation_angle = 30.0
        self.analyzer._sync_surface_overlays(self.three_d_context(), spec)
        after = np.asarray(plotter.actors[f"{spec.page_id}:0"].mesh.points)
        # 旋转后点位重算，且转到数组框外的角被裁掉。
        self.assertTrue(
            before.shape != after.shape or not np.allclose(before, after),
            "旋转后叠加几何没有重建",
        )
        # 高度（能量方向）不受平面旋转影响。
        self.assertAlmostEqual(float(after[:, 2].min()), float(before[:, 2].min()), places=4)

    def test_overlay_card_lists_bands(self):
        from tests.support.overlay import FakePlotter

        plotter = FakePlotter()
        self.analyzer.plotter = plotter
        page = SimpleNamespace(
            rows=[],
            visible=[],
            set_overlay_layers=lambda layers: page.rows.append(list(layers)),
            set_overlay_card_visible=lambda visible: page.visible.append(bool(visible)),
        )
        self.analyzer.page_render = page
        z = np.zeros(SHAPE[:2])
        spec = self.analyzer.add_plugin_result_page(
            title="双带面",
            source_page_id=SOURCE_PAGE_ID,
            source_title="体数据",
            data_scope_id="full",
            params={
                "plugin_analysis": {"data_generation": int(self.analyzer.shared_denoise_version)},
                "surface_kind": "plugin_surface",
                "base_surface": {
                    "x": X_COORDS,
                    "y": Y_COORDS,
                    "bands": [
                        {"label": "Band 1", "z": z, "color": "#ff8a3d", "opacity": None},
                        {"label": "Band 2", "z": z + 0.1, "color": "#4dd2ff", "opacity": None},
                    ],
                    "selected_band": 0,
                    "z_label": "E",
                    "z_unit": "eV",
                },
            },
            result_kind="surface",
        )
        from bandscope.rendering.surface_overlay import SurfaceOverlayManager

        self.analyzer.surface_overlay = SurfaceOverlayManager(plotter)
        self.analyzer._sync_surface_overlays(self.three_d_context(), spec)
        self.assertEqual(len(page.rows[-1]), 2)
        self.assertEqual(page.visible[-1], True)
        labels = [row[1] for row in page.rows[-1]]
        self.assertTrue(all("Band" in label for label in labels))

    def test_surface_page_renders_through_the_2d_path(self):
        """面结果页真的能画出来：走宿主的二维渲染路径并落在物理坐标上。"""
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        from bandscope.rendering.render_core import VisualEngine

        z = np.linspace(0.0, 1.0, SHAPE[0] * SHAPE[1]).reshape(SHAPE[:2])
        spec = self.analyzer.add_plugin_result_page(
            title="单带面",
            source_page_id=SOURCE_PAGE_ID,
            source_title="体数据",
            data_scope_id="full",
            params={
                "surface_kind": "plugin_surface",
                "base_surface": {
                    "x": X_COORDS,
                    "y": Y_COORDS,
                    "bands": [{"label": "Band 1", "z": z, "color": "#ff8a3d", "opacity": None}],
                    "selected_band": 0,
                    "x_label": "kx",
                    "y_label": "ky",
                    "z_label": "E",
                    "z_unit": "eV",
                },
            },
            result_kind="surface",
        )
        context = self.analyzer._get_plugin_surface_context(spec)
        figure = Figure(figsize=(6, 5), dpi=100)
        canvas = FigureCanvasAgg(figure)
        axes = figure.add_subplot(111)
        VisualEngine.render_2d_slice(
            axes, canvas, context["data"], context["slice_info"], (0, 50, 100), context["coords"]
        )
        self.assertTrue(axes.images, "面结果没有画出图像")
        extent = [float(value) for value in axes.images[0].get_extent()]
        self.assertAlmostEqual(extent[0], float(X_COORDS[0]), places=6)
        self.assertAlmostEqual(extent[1], float(X_COORDS[-1]), places=6)
        self.assertAlmostEqual(extent[2], float(Y_COORDS[0]), places=6)
        self.assertAlmostEqual(extent[3], float(Y_COORDS[-1]), places=6)
        self.assertEqual(axes.get_title(), "Band 1")

    def test_header_band_selector_appears_for_multi_band_pages(self):
        """多带结果页在页头出现带选择器；单带页没有。"""
        from PyQt5.QtWidgets import QComboBox

        z = np.zeros(SHAPE[:2])
        two_bands = {
            "x": X_COORDS,
            "y": Y_COORDS,
            "bands": [
                {"label": "Band 1", "z": z, "color": "#ff8a3d", "opacity": None},
                {"label": "Band 2", "z": z + 0.1, "color": "#4dd2ff", "opacity": None},
            ],
            "selected_band": 0,
            "x_label": "kx",
            "y_label": "ky",
            "z_label": "E",
            "z_unit": "eV",
        }
        spec = self.analyzer.add_plugin_result_page(
            title="双带面", source_page_id=SOURCE_PAGE_ID, source_title="体数据",
            data_scope_id="full", params={"surface_kind": "plugin_surface", "base_surface": two_bands},
            result_kind="surface",
        )
        self.analyzer._sync_surface_band_selector(spec)
        combo = self.workspace.header_extra
        self.assertIsInstance(combo, QComboBox)
        self.assertEqual(combo.count(), 2)
        combo.setCurrentIndex(1)
        self.assertEqual(spec.params["selected_band"], 1)

        # 单带页清空选择器。
        single = dict(two_bands, bands=two_bands["bands"][:1])
        spec.params["base_surface"] = single
        self.analyzer._surface_header_signature = None
        self.analyzer._sync_surface_band_selector(spec)
        self.assertIsNone(self.workspace.header_extra)

    def test_band_selector_switches_pages(self):
        """面板交回多条带时：一带一页，页内切换改变显示的数据。"""
        panels = self.mount_panels()
        panel = panels[PLUGIN_ID]
        self.run_one_band(panel)
        self.assertTrue(self.wait_for_results())

        spec = self.surface_specs()[0]
        payload = spec.params["base_surface"]
        second = np.asarray(payload["bands"][0]["z"]) + 0.05
        payload["bands"].append({"label": "Band 2", "z": second, "color": "#4dd2ff", "opacity": None})
        first_data = np.asarray(self.analyzer._get_plugin_surface_context(spec)["data"])
        self.analyzer._on_surface_band_changed(spec.page_id, 1)
        self.assertEqual(spec.params["selected_band"], 1)
        switched = np.asarray(self.analyzer._get_plugin_surface_context(spec)["data"])
        np.testing.assert_allclose(switched, second)
        self.assertFalse(np.allclose(first_data, switched))


if __name__ == "__main__":
    unittest.main()
