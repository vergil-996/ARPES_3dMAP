# -*- coding: utf-8 -*-
"""API 3 端到端：构建并安装 EDC 瀑布图插件 → 下拉动作 → 冻结来源 → 结果页 → 展示/导出。

插件走真实的构建与安装事务（``build_plugin.py`` + ``install_package``），主窗口
只替换渲染部件；冻结来源、二维基础上下文、页面创建、下拉分派、参数提交、自动
标题、数值导出与 E 翻转都走真实代码路径。不需要 OpenGL。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from bandscope.app_metadata import APP_VERSION
from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.extensions.derived_host import (
    DERIVED_PARAMS_KEY,
    DERIVED_SOURCE_KEY,
)
from bandscope.extensions.plugin_host import PluginSession
from bandscope.extensions.plugin_manager import PluginManager, install_package
from bandscope.ui.page_data_process_v2 import DataProcessPage
from scripts.release.build_plugin import build as build_package
from tests.support.pages import make_workspace, page_spec
from tests.support.plugins import synthetic_source

NX, NY, NE, NT = 5, 7, 9, 2
RAW = np.arange(NX * NY * NE * NT, dtype=np.float32).reshape(NX, NY, NE, NT)
COORDS = {
    "X": np.linspace(-0.4, 0.4, NX),
    "Y": np.linspace(-0.6, 0.6, NY),
    "E": np.linspace(-1.0, 0.8, NE),
    "delay": np.array([0.0, 1.0]),
}
SOURCE_PAGE_ID = "page-1"
OTHER_PAGE_ID = "page-2"


class _Switch:
    def __init__(self, checked=False):
        self.checked = bool(checked)

    def isChecked(self):
        return self.checked

    def setChecked(self, value):
        self.checked = bool(value)


def clone_coords():
    return {key: np.array(values, dtype=np.float64, copy=True) for key, values in COORDS.items()}


class PluginDerivedFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.sandbox = Path(self._root.name)
        self.root = self.sandbox / "extensions"

        archive = build_package("edc_waterfall", self.sandbox / "release", APP_VERSION)
        install_package(archive, root=self.root, source=synthetic_source())

        self.manager = PluginManager(root=self.root)
        self.addCleanup(self.manager.shutdown)
        self.manager.startup()
        record = self.manager.record("edc_waterfall")
        self.assertTrue(record.ready, record.load_error)

        self.workspace = make_workspace()
        self.workspace.add_page(
            page_spec(
                SOURCE_PAGE_ID,
                "X-Integral",
                "axis_integral",
                params={
                    "axis_index": 0,
                    "low": 1,
                    "up": 3,
                    "mid": 2,
                    "source_mode": "frame",
                    "source_t_index": 0,
                    "source_t_low": 0,
                    "source_t_up": 1,
                },
            )
        )
        self.messages = []
        self.refreshes = 0
        self.analyzer = self._make_analyzer()
        self.session = PluginSession(self.analyzer, manager=self.manager)
        self.addCleanup(self.session.shutdown)
        self.analyzer.plugin_session = self.session

    def _make_analyzer(self, *, switch_flip=False, install_plugin=True):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.left_workspace = self.workspace
        analyzer.core = AnalyzerCore()
        analyzer.core.coords = clone_coords()
        analyzer.core.coord_sources = {
            "X": "file", "Y": "file", "E": "file", "delay": "file",
        }
        analyzer.core.coord_units = {"X": "1/Å", "Y": "1/Å", "E": "eV"}
        analyzer.core.has_time_axis = True
        analyzer.core.raw_data = RAW
        analyzer.original_raw_data = RAW
        analyzer.original_coords = clone_coords()
        analyzer.base_raw_data = RAW
        analyzer.base_coords = clone_coords()
        analyzer.shared_denoise_version = 1
        analyzer.global_denoise_methods = []
        analyzer.rotation_angle = 0.0
        analyzer._rotation_cache = {}
        analyzer._axis_prefix_cache = None
        analyzer._second_derivative_volume_cache = None
        analyzer.data_scopes = {}
        analyzer._initialize_data_scopes()
        analyzer.timeline_bar = SimpleNamespace(
            slider_time=SimpleNamespace(value=lambda: 0),
            switch_flip=_Switch(switch_flip),
        )
        analyzer.page_data = DataProcessPage()
        analyzer.axis_source_mode = "frame"
        analyzer.axis_interval = None
        analyzer.axis_space = None
        analyzer._seed_control_state_for_spec = lambda spec: None
        # 页面参数即真值：真实窗口里这里还会先把当前页的控件状态写回参数，
        # 该逻辑另有覆盖；本用例冻结的参数直接来自页面字典。
        analyzer._persist_axis_integral_page_state = lambda spec=None: None
        analyzer._show_message = lambda title, text, icon=None: self.messages.append((title, text))
        analyzer.global_refresh = self._count_refresh
        analyzer._update_export_button_states = lambda: None
        analyzer._closing_down = False
        if not install_plugin:
            analyzer.plugin_session = None
        return analyzer

    def _count_refresh(self):
        self.refreshes += 1

    # ------------------------------------------------------------------ 工具
    def derived_specs(self):
        return [
            spec
            for spec in self.workspace.page_specs.values()
            if spec.page_kind == "plugin_derived"
        ]

    def select_other_action(self, key):
        self.analyzer._sync_integration_actions()
        combo = self.analyzer.page_data.combo_other
        for index in range(combo.count()):
            if self.analyzer.page_data._other_keys[index] == key:
                combo.setCurrentIndex(index)
                return
        self.fail(f"下拉里没有动作 {key}")

    def expected_x_integral(self, axis_index=0, low=1, up=3, k_step=None, crop=None):
        """独立复算：积分 → 裁剪 → 最近邻采样 → 逐曲线归一化。"""
        data = RAW[:, :, :, 0]
        if axis_index == 0:
            plane = np.sum(data[low:up + 1, :, :], axis=0)
            k_key = "Y"
        else:
            plane = np.sum(data[:, low:up + 1, :], axis=1)
            k_key = "X"
        k_full = np.asarray(COORDS[k_key], dtype=np.float64)
        e_full = np.asarray(COORDS["E"], dtype=np.float64)
        if crop is not None:
            x_low, x_up, y_low, y_up = crop
        else:
            x_low, x_up, y_low, y_up = 0, k_full.size - 1, 0, e_full.size - 1
        k_crop = k_full[x_low:x_up + 1]
        plane = plane[x_low:x_up + 1, y_low:y_up + 1]
        if k_step is None:
            diffs = np.abs(np.diff(k_full))
            k_step = max(float(np.median(diffs)), 1e-4)
        positions = list(np.arange(k_crop.min(), k_crop.max() + k_step * 0.5, k_step))
        positions.extend([k_crop.min(), k_crop.max()])
        indices = sorted(
            {int(np.argmin(np.abs(k_crop - position))) for position in positions},
            key=lambda idx: float(k_crop[idx]),
        )
        indices = np.asarray(indices, dtype=int)
        curves = plane[indices, :]
        normalized = np.asarray(
            [c / np.max(c) if abs(np.max(c)) > 1e-12 and np.isfinite(np.max(c)) else np.zeros_like(c)
             for c in curves]
        )
        return {
            "k": k_crop[indices],
            "sample_indices": indices + x_low,
            "energy": e_full[y_low:y_up + 1],
            "raw": curves,
            "normalized": normalized,
            "k_step": k_step,
        }

    # ------------------------------------------------------------------ 用例
    def test_action_registration_and_page_creation(self):
        self.analyzer._sync_integration_actions()
        combo = self.analyzer.page_data.combo_other
        texts = [combo.itemText(i) for i in range(combo.count())]
        self.assertEqual(texts, ["切片内强度积分", "能级态密度", "EDC瀑布图", "单条 EDC 曲线", "二阶导"])

        self.select_other_action("edc_waterfall:create")
        self.analyzer.on_apply_other_integral()

        specs = self.derived_specs()
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        # 创建后进入瀑布页。
        self.assertEqual(self.workspace.current_spec().page_id, spec.page_id)
        self.assertEqual(spec.source_page_id, SOURCE_PAGE_ID)
        self.assertTrue(spec.title.startswith("EDC瀑布图 [ky, X中心"))

        source = spec.params[DERIVED_SOURCE_KEY]
        self.assertEqual(source["source_page_id"], SOURCE_PAGE_ID)
        self.assertEqual(source["source_page_kind"], "axis_integral")
        self.assertEqual(source["axis_index"], 0)
        self.assertEqual((source["integral_low"], source["integral_up"], source["integral_mid"]), (1, 3, 2))
        self.assertEqual(source["source_mode"], "frame")
        self.assertEqual(source["frame_index"], 0)
        self.assertEqual(source["scope_id"], "full")
        self.assertIsNone(source["crop_rect"])

    def test_rendered_context_matches_independent_computation(self):
        self.select_other_action("edc_waterfall:create")
        self.analyzer.on_apply_other_integral()
        spec = self.derived_specs()[0]
        context = self.analyzer._compute_render_context(spec)
        expected = self.expected_x_integral()

        self.assertEqual(context["view"], "waterfall")
        np.testing.assert_allclose(context["k_values"], expected["k"])
        np.testing.assert_array_equal(context["sample_indices"], expected["sample_indices"])
        np.testing.assert_allclose(context["energy_axis"], expected["energy"])
        np.testing.assert_allclose(context["curves"], expected["normalized"])
        np.testing.assert_allclose(context["raw_curves"], expected["raw"])
        self.assertAlmostEqual(float(context["k_step"]), expected["k_step"])
        self.assertEqual(context["k_axis_key"], "Y")
        self.assertEqual(context["offset_step"], 1.2)
        # 中心值取被积轴（X）在中位下标上的物理坐标：X[2] = 0。
        self.assertEqual(context["title"], "EDC Waterfall [ky, X center 0, step 0.200]")
        self.assertEqual(spec.params[DERIVED_PARAMS_KEY], {})

    def test_y_integral_source_uses_the_x_axis_and_manual_step(self):
        self.workspace.page_specs[SOURCE_PAGE_ID].params["axis_index"] = 1
        self.workspace.page_specs[SOURCE_PAGE_ID].params["axis_name"] = "Y轴"
        self.select_other_action("edc_waterfall:create")
        self.session.save_derived_defaults(
            "edc_waterfall", {"k_step_mode": "manual", "k_step": 0.3}
        )
        self.analyzer.on_apply_other_integral()
        spec = self.derived_specs()[0]
        self.assertEqual(spec.params[DERIVED_PARAMS_KEY]["k_step_mode"], "manual")
        context = self.analyzer._compute_render_context(spec)
        expected = self.expected_x_integral(axis_index=1, k_step=0.3)
        self.assertEqual(context["k_axis_key"], "X")
        self.assertTrue(context["title"].startswith("EDC Waterfall [kx, Y center"))
        np.testing.assert_allclose(context["k_values"], expected["k"])
        np.testing.assert_array_equal(context["sample_indices"], expected["sample_indices"])

    def test_crop_chain_limits_the_source(self):
        spec_page = self.workspace.page_specs[SOURCE_PAGE_ID]
        spec_page.params.update({"crop_k_low": 2, "crop_k_up": 5, "crop_e_low": 1, "crop_e_up": 4})
        self.select_other_action("edc_waterfall:create")
        self.analyzer.on_apply_other_integral()
        spec = self.derived_specs()[0]
        self.assertEqual(spec.params[DERIVED_SOURCE_KEY]["crop_rect"], [2, 5, 1, 4])
        context = self.analyzer._compute_render_context(spec)
        expected = self.expected_x_integral(crop=(2, 5, 1, 4))
        np.testing.assert_allclose(context["k_values"], expected["k"])
        np.testing.assert_allclose(context["energy_axis"], expected["energy"])
        # 裁剪后缀按完整轴坐标生成：Y[2]=-0.2、Y[5]=0.4、E[1]=-0.775、E[4]=-0.1。
        self.assertIn("ky -0.2~0.4", context["title"])
        self.assertIn("E -0.78~-0.1", context["title"])
        self.assertEqual(context["crop_rect"], {"x_low": 2, "x_up": 5, "y_low": 1, "y_up": 4})

    def test_erase_source_is_rejected(self):
        spec_page = self.workspace.page_specs[SOURCE_PAGE_ID]
        spec_page.params["crop_regions"] = [
            {"view": "2d", "axes": ("Y", "E"), "bounds": (0.0, 0.2, 0.0, 0.5), "operation": "erase"}
        ]
        self.select_other_action("edc_waterfall:create")
        self.analyzer.on_apply_other_integral()
        self.assertEqual(self.derived_specs(), [])
        self.assertTrue(self.messages)
        self.assertIn("擦除", self.messages[-1][1])

    def test_e_flip_keeps_screen_publication_and_export_consistent(self):
        from bandscope.exporting.publication_export import PublicationSnapshot, _freeze_waterfall

        self.select_other_action("edc_waterfall:create")
        self.analyzer.on_apply_other_integral()
        spec = self.derived_specs()[0]

        context = self.analyzer._compute_render_context(spec)
        raw_data, coords = self.analyzer._get_display_state_for_spec(spec)
        _, _, export = self.analyzer._build_export_payload(spec, raw_data, coords)

        # 屏幕（翻转关）与出版图：E 升序 + 数据对齐。
        render_off = self.analyzer._render_context_for_visual_flip(context)
        snapshot = PublicationSnapshot(
            snapshot_id="s", source_page_id=spec.page_id, source_page_title=spec.title,
            page_kind=spec.page_kind, view="waterfall", view_family="1d",
            captured_at="", cmap_name="magma", levels_params=(0.0, 1.0, 1.0),
            locked_range=None, coords=coords,
            coord_sources=dict(self.analyzer.core.coord_sources),
            coord_units=dict(self.analyzer.core.coord_units),
            source_desc="", source_revision="",
        )
        _freeze_waterfall(None, snapshot, render_off)
        payload_off = dict(snapshot.payload)

        # E 翻转打开：只镜像显示方向，采样与坐标保持原始排列。
        self.analyzer.timeline_bar.switch_flip.setChecked(True)
        render_on = self.analyzer._render_context_for_visual_flip(context)
        np.testing.assert_allclose(render_on["energy_axis"], context["energy_axis"])
        np.testing.assert_allclose(render_on["curves"], context["curves"])
        self.assertTrue(render_on["display_e_flip"])

        snapshot2 = PublicationSnapshot(
            snapshot_id="s", source_page_id=spec.page_id, source_page_title=spec.title,
            page_kind=spec.page_kind, view="waterfall", view_family="1d",
            captured_at="", cmap_name="magma", levels_params=(0.0, 1.0, 1.0),
            locked_range=None, coords=coords,
            coord_sources=dict(self.analyzer.core.coord_sources),
            coord_units=dict(self.analyzer.core.coord_units),
            source_desc="", source_revision="",
        )
        _freeze_waterfall(None, snapshot2, render_on)
        payload_on = dict(snapshot2.payload)

        for payload in (payload_off, payload_on):
            energy = np.asarray(payload["energy_axis"])
            curves = np.asarray(payload["curves"])
            # 出版图与数值导出在同一 E 坐标下给出同一强度：出版图是归一化曲线，
            # 与导出的 normalized_intensity 逐点一致（导出保持原始坐标排列）。
            for index in (0, len(energy) // 2, len(energy) - 1):
                column = int(np.argmin(np.abs(np.asarray(export["E"]) - energy[index])))
                np.testing.assert_allclose(
                    curves[:, index],
                    np.asarray(export["normalized_intensity"])[:, column],
                    rtol=1e-6, atol=1e-4,
                )
                # 同一份采样：出版图的 k 值与导出一致。
            np.testing.assert_allclose(
                np.asarray(payload["k_values"]), np.asarray(export["k"]), rtol=1e-6
            )
        # 翻转开关只改出版图的显示方向：数组与坐标仍然逐点相同。
        np.testing.assert_allclose(payload_on["energy_axis"], payload_off["energy_axis"])
        np.testing.assert_allclose(payload_on["curves"], payload_off["curves"])
        self.assertIsNone(payload_off["flip_axis"])
        self.assertEqual(payload_on["flip_axis"], "y")
        # 数值导出与显示开关无关：同一份原始坐标。
        self.analyzer.timeline_bar.switch_flip.setChecked(False)
        _, _, export_again = self.analyzer._build_export_payload(spec, raw_data, coords)
        np.testing.assert_allclose(export_again["E"], export["E"])
        np.testing.assert_allclose(export_again["intensity"], export["intensity"])

    def test_apply_settings_touches_only_the_current_page_and_keeps_rename(self):
        self.select_other_action("edc_waterfall:create")
        self.analyzer.on_apply_other_integral()
        first = self.derived_specs()[0]
        self.workspace.apply_page_rename(first.page_id, "我的瀑布")

        # 第二页用默认值创建；两页参数互相独立（创建前回到积分来源页）。
        self.workspace.activate_page(SOURCE_PAGE_ID)
        self.select_other_action("edc_waterfall:create")
        self.analyzer.on_apply_other_integral()
        second = self.derived_specs()[1]
        self.assertNotEqual(first.page_id, second.page_id)

        self.workspace.activate_page(first.page_id)
        before_second = dict(second.params[DERIVED_PARAMS_KEY])
        # 「应用当前瀑布图」走插件当前设置（手动 0.3）：只有当前页发生变化。
        record = self.manager.record("edc_waterfall")
        record.instance.restore_settings({"k_step_mode": "manual", "k_step": 0.3})
        ok, message = self.session.apply_settings_to_current_page("edc_waterfall")
        self.assertTrue(ok, message)
        self.assertEqual(first.params[DERIVED_PARAMS_KEY]["k_step"], 0.3)
        self.assertEqual(second.params[DERIVED_PARAMS_KEY], before_second)
        # 用户改名不被自动标题覆盖，但底层自动名已更新。
        self.assertEqual(first.title, "我的瀑布")
        self.assertIn("步长 0.300", first.auto_title or "")
        self.assertTrue(first.title_overridden)
        self.assertGreater(self.refreshes, 0)

    def test_failed_apply_keeps_previous_params_and_result(self):
        self.select_other_action("edc_waterfall:create")
        self.analyzer.on_apply_other_integral()
        spec = self.derived_specs()[0]
        before = dict(spec.params[DERIVED_PARAMS_KEY])
        ok, message = self.session._derived.apply_params_to_current_page(
            "edc_waterfall", {"k_step_mode": "manual", "k_step": 99.0}
        )
        self.assertFalse(ok)
        self.assertIn("步长", message)
        self.assertEqual(spec.params[DERIVED_PARAMS_KEY], before)
        # 原有结果仍可渲染。
        context = self.analyzer._compute_render_context(spec)
        self.assertEqual(context["view"], "waterfall")
        self.assertNotIn("stale_error", context)

    def test_frozen_source_is_independent_of_the_source_page(self):
        self.select_other_action("edc_waterfall:create")
        self.analyzer.on_apply_other_integral()
        spec = self.derived_specs()[0]
        before = self.analyzer._compute_render_context(spec)
        # 改来源页：积分区间与裁剪都变了，但瀑布页仍按冻结来源重算。
        source_page = self.workspace.page_specs[SOURCE_PAGE_ID]
        source_page.params.update({"low": 0, "up": 4, "mid": 2})
        after = self.analyzer._compute_render_context(spec)
        np.testing.assert_allclose(after["raw_curves"], before["raw_curves"])

    def test_time_integral_source_freezes_the_time_range(self):
        spec_page = self.workspace.page_specs[SOURCE_PAGE_ID]
        spec_page.params.update(
            {"source_mode": "time_integral", "source_t_low": 1, "source_t_up": 1}
        )
        self.select_other_action("edc_waterfall:create")
        self.analyzer.on_apply_other_integral()
        spec = self.derived_specs()[0]

        source = spec.params[DERIVED_SOURCE_KEY]
        self.assertEqual(source["source_mode"], "time_integral")
        self.assertEqual((source["time_low"], source["time_up"]), (1, 1))
        self.assertEqual(spec.params["source_mode"], "time_integral")
        # 时间积分的来源页会锁住时间轴控件。
        self.assertTrue(self.analyzer._is_time_locked_page(spec))
        self.assertEqual(self.analyzer._page_axis_source_mode(spec), "time_integral")

        context = self.analyzer._compute_render_context(spec)
        # 时间积分取第 1 帧（帧号 1 的 RAW 切片），口径与源页一致。
        expected_plane = np.sum(RAW[:, :, :, 1][1:4, :, :], axis=0)
        np.testing.assert_allclose(context["raw_curves"][: len(expected_plane), :].sum(), expected_plane.sum(), rtol=1e-6)

    def test_dropdown_state_falls_back_when_plugin_action_is_missing(self):
        page = DataProcessPage()
        page.set_integration_actions([("edc_waterfall:create", "EDC瀑布图", 20.0)])
        page.combo_other.setCurrentIndex(page._other_keys.index("edc_waterfall:create"))
        saved = page.export_state()["combo_other"]
        self.assertEqual(saved["key"], "edc_waterfall:create")

        # 插件未加载：同一份状态恢复时回退到可用内置项，不按旧下标乱选。
        bare = DataProcessPage()
        bare.restore_state({"combo_other": saved})
        self.assertEqual(bare.current_other_key(), "builtin:slice_dos")

        # 旧状态（仅文本）：能按文本恢复；文本也缺失时同样回退。
        legacy = DataProcessPage()
        legacy.restore_state({"combo_other": {"index": 2, "text": "单条 EDC 曲线"}})
        self.assertEqual(legacy.current_other_key(), "builtin:edc_curve")
        stale = DataProcessPage()
        stale.restore_state({"combo_other": {"index": 2, "text": "EDC瀑布图"}})
        self.assertEqual(stale.current_other_key(), "builtin:slice_dos")


if __name__ == "__main__":
    unittest.main()
