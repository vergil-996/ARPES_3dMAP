# -*- coding: utf-8 -*-
"""EDC 瀑布图插件的业务测试：采样、归一化、自动步长、标题与入口。

全部使用合成数据，不依赖本机实验文件。宿主侧只出现公开协议类型
（``DerivedInput2D`` 等），不触碰 Qt 与主窗口。
"""
from __future__ import annotations

import unittest

import numpy as np

from bandscope.extensions.api import (
    DerivedAxis,
    DerivedInput2D,
    DerivedSource2D,
    SOURCE_FILE,
    read_only_array,
)
from plugins.edc_waterfall import edc
from plugins.edc_waterfall.entry import (
    ACTION_CREATE,
    ACTION_LABEL,
    EdcWaterfallPlugin,
)


def axis(key, label, values, unit="", source=SOURCE_FILE):
    return DerivedAxis(key=key, label=label, values=read_only_array(values), unit=unit, source=source)


def make_source(**overrides):
    payload = {
        "plugin_id": "edc_waterfall",
        "action_id": ACTION_CREATE,
        "source_page_id": "page-1",
        "source_page_kind": "axis_integral",
        "axis_index": 0,
        "integral_low": 1,
        "integral_up": 3,
        "integral_mid": 2,
        "source_mode": "frame",
        "frame_index": 0,
        "time_low": 0,
        "time_up": 2,
        "scope_id": "full",
        "crop_rect": None,
        "source_title": "X-Integral",
        "data_generation": 5,
    }
    payload.update(overrides)
    return DerivedSource2D(**payload)


def make_input(*, k=None, data=None, e=None, params=None, source=None, k_full=None,
               e_full=None, integrated=None):
    k_values = np.asarray(k if k is not None else np.linspace(-0.4, 0.4, 5), dtype=np.float64)
    e_values = np.asarray(e if e is not None else np.linspace(-1.0, 1.0, 4), dtype=np.float64)
    payload = np.asarray(
        data if data is not None else np.tile(np.arange(e_values.size), (k_values.size, 1)),
        dtype=np.float64,
    )
    return DerivedInput2D(
        plugin_id="edc_waterfall",
        source=source or make_source(),
        params=dict(params or {}),
        data=read_only_array(payload),
        k_axis=axis("Y", "ky", k_values, unit="1/Å"),
        e_axis=axis("E", "E", e_values, unit="eV"),
        k_axis_full=axis("Y", "ky", k_full if k_full is not None else np.linspace(-0.4, 0.4, 5), unit="1/Å"),
        e_axis_full=axis("E", "E", e_full if e_full is not None else e_values, unit="eV"),
        integrated_axis=axis("X", "X", integrated if integrated is not None else np.linspace(-2.0, 2.0, 5), unit="1/Å"),
        source_title="X-Integral",
        frame_label="帧 0",
        data_generation=5,
    )


class AutoStepTests(unittest.TestCase):
    def test_median_spacing_of_full_axis(self):
        # 三个间距 0.1/0.2/0.3：中位 0.2。
        values = np.array([0.0, 0.1, 0.3, 0.6])
        self.assertAlmostEqual(edc.auto_step(values), 0.2)

    def test_fallback_and_lower_bound(self):
        self.assertEqual(edc.auto_step([0.5]), edc.DEFAULT_STEP)
        self.assertEqual(edc.auto_step([0.0, 0.0]), edc.DEFAULT_STEP)
        self.assertAlmostEqual(edc.auto_step([0.0, 1e-9]), edc.MIN_STEP)

    def test_resolve_step_manual_validation(self):
        full = np.array([0.0, 0.1, 0.2])
        self.assertAlmostEqual(edc.resolve_step({}, full), 0.1)
        self.assertAlmostEqual(
            edc.resolve_step({"k_step_mode": "manual", "k_step": 0.05}, full), 0.05
        )
        for bad in (0.0, -1.0, 11.0, "abc", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    edc.resolve_step({"k_step_mode": "manual", "k_step": bad}, full)


class SamplingTests(unittest.TestCase):
    def test_endpoints_are_always_sampled(self):
        values = np.linspace(0.0, 1.0, 11)
        indices = edc.nearest_axis_indices(values, 0.3)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], 10)

    def test_descending_axis_is_sorted_physically(self):
        values = np.array([0.6, 0.4, 0.2, 0.0, -0.2])
        indices = edc.nearest_axis_indices(values, 0.25)
        physical = values[indices]
        np.testing.assert_allclose(physical, np.sort(physical))
        self.assertEqual(int(indices[-1]), 0)  # 物理最大 k（0.6）排最后

    def test_nearest_sampling_of_non_uniform_axis(self):
        values = np.array([0.0, 0.05, 0.3, 0.9, 0.95])
        indices = edc.nearest_axis_indices(values, 0.3)
        # 落点 0.0/0.3/0.6/0.9(+端点)：0.6 最近的是 0.9 之上的 0.3？不，
        # 0.9 与 0.95 都比 0.6 近于 … 用物理值核对映射即可。
        for index in indices:
            self.assertIn(int(index), (0, 2, 3, 4))
        self.assertEqual(int(indices[0]), 0)
        self.assertEqual(int(indices[-1]), 4)


class NormalizationTests(unittest.TestCase):
    def test_each_curve_is_normalized_to_its_own_maximum(self):
        data = np.array([[1.0, 2.0, 4.0], [0.0, 0.0, 0.0], [-2.0, -1.0, -0.5]])
        result = edc.compute_waterfall(
            make_input(
                k=np.linspace(0.0, 1.0, 3),
                e=np.linspace(0.0, 2.0, 3),
                data=data,
                params={"k_step_mode": "manual", "k_step": 0.5},
            )
        )
        self.assertEqual(result.curves.shape[0], 3)
        np.testing.assert_allclose(result.curves[0], [0.25, 0.5, 1.0])
        # 全零曲线输出全零，不是 NaN。
        np.testing.assert_array_equal(result.curves[1], [0.0, 0.0, 0.0])
        # 逐曲线归一化除以曲线最大值（不是绝对值最大值）：全负曲线会整体反号，
        # 这是迁移前 ``_safe_curve_normalize`` 的既有行为，原样保留。
        np.testing.assert_allclose(result.curves[2], [4.0, 2.0, 1.0])
        np.testing.assert_allclose(result.raw_curves, data)

    def test_single_point_axis_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            edc.compute_waterfall(
                make_input(k=np.array([0.0]), data=np.ones((1, 4)))
            )
        self.assertIn("采样点不足", str(ctx.exception))


class ResultShapeTests(unittest.TestCase):
    def test_sample_identity_uses_full_axis_indices_with_crop_offset(self):
        source = make_source(crop_rect=(2, 6, 0, 3))
        k_full = np.linspace(-0.5, 0.5, 9)
        k_crop = k_full[2:7]
        result = edc.compute_waterfall(
            make_input(
                source=source,
                k=k_crop,
                k_full=k_full,
                e=np.linspace(-1.0, 1.0, 4),
                data=np.arange(20, dtype=float).reshape(5, 4),
                params={"k_step_mode": "manual", "k_step": 0.25},
            )
        )
        self.assertGreaterEqual(result.sample_indices.min(), 2)
        self.assertLessEqual(result.sample_indices.max(), 6)
        np.testing.assert_allclose(result.k, k_full[result.sample_indices])
        self.assertEqual(result.energy.size, 4)
        self.assertEqual(result.curves.shape, (result.k.size, 4))
        self.assertEqual(result.params["k_step"], 0.25)
        self.assertEqual(result.params["sample_count"], int(result.k.size))

    def test_window_title_and_plot_title(self):
        source = make_source(crop_rect=(1, 3, 0, 3))
        k_full = np.array([-0.4, -0.2, 0.0, 0.2, 0.4])
        result = edc.compute_waterfall(
            make_input(
                source=source,
                k=k_full[1:4],
                k_full=k_full,
                e_full=np.array([-1.0, -0.5, 0.0, 0.5, 1.0]),
                e=np.array([-1.0, -0.5, 0.0, 0.5, 1.0]),
                integrated=np.array([0.0, 0.5, 1.0, 1.5, 2.0]),
                data=np.ones((3, 5)),
                params={"k_step_mode": "manual", "k_step": 0.2},
            )
        )
        self.assertTrue(result.title.startswith("EDC瀑布图 [ky, X中心"))
        self.assertIn("步长 0.200", result.title)
        self.assertIn("ky -0.2~0.2", result.title)
        # 裁剪后缀按完整能量轴坐标生成（crop_rect 的 y 上是 0~3）。
        self.assertIn("E -1~0.5", result.title)
        self.assertTrue(result.plot_title.startswith("EDC Waterfall [ky, X center"))
        self.assertIn("step 0.200", result.plot_title)
        self.assertEqual(result.integrated_range, (0.5, 1.5))
        self.assertEqual(result.integrated_center, 1.0)
        self.assertEqual(result.k_label, "ky")
        self.assertEqual(result.integrated_label, "X")
        self.assertEqual(result.ylabel, "Energy (eV)")

    def test_ylabel_falls_back_without_unit_metadata(self):
        payload = make_input()
        payload = DerivedInput2D(
            plugin_id=payload.plugin_id,
            source=payload.source,
            params=payload.params,
            data=payload.data,
            k_axis=payload.k_axis,
            e_axis=axis("E", "E", np.linspace(0, 1, 4), unit="", source="index"),
            k_axis_full=payload.k_axis_full,
            e_axis_full=payload.e_axis_full,
            integrated_axis=payload.integrated_axis,
        )
        self.assertEqual(edc.energy_label(payload), "Energy")


class PluginEntryTests(unittest.TestCase):
    def test_action_registration_and_compute_delegation(self):
        plugin = EdcWaterfallPlugin()
        actions = plugin.integration_actions()
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_id, ACTION_CREATE)
        self.assertEqual(actions[0].label, ACTION_LABEL)
        self.assertEqual(actions[0].key("edc_waterfall"), "edc_waterfall:create")

        result = plugin.compute_derived_result(
            make_input(params={"k_step_mode": "manual", "k_step": 0.1})
        )
        self.assertEqual(result.k.size, result.curves.shape[0])

    def test_settings_state_round_trip(self):
        plugin = EdcWaterfallPlugin()
        self.assertEqual(plugin.export_settings()["k_step_mode"], edc.MODE_AUTO)
        plugin.restore_settings({"k_step_mode": "manual", "k_step": 0.05})
        state = plugin.export_settings()
        self.assertEqual(state["k_step_mode"], "manual")
        self.assertAlmostEqual(float(state["k_step"]), 0.05)
        # 缺失与多余字段都容忍。
        plugin.restore_settings({"extra": 1})
        self.assertEqual(plugin.export_settings()["k_step_mode"], "manual")


if __name__ == "__main__":
    unittest.main()
