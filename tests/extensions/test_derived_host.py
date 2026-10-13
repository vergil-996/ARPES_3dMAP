# -*- coding: utf-8 -*-
"""API 3 宿主侧协调器：冻结来源、只读输入、缓存回退、参数提交与默认值。

用测试替身提供窗口的数据准备原语（真实主窗口的接入由
``tests/integration/test_plugin_derived_flow.py`` 覆盖），这里只验证契约：
冻结之后页面不依赖来源页、失败保留上次结果、提交前先验证、其他页不受影响。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np

from bandscope.extensions.api import (
    DerivedSourceUnavailable,
    DerivedWaterfallResult,
)
from bandscope.extensions.derived_host import (
    DERIVED_ACTION_KEY,
    DERIVED_PARAMS_KEY,
    DERIVED_PLUGIN_KEY,
    DERIVED_SOURCE_KEY,
    DerivedPageController,
    PluginDefaultsStore,
)

K_FULL = np.linspace(-0.5, 0.5, 11)
E_FULL = np.linspace(-1.0, 1.0, 6)
INTEGRATED = np.linspace(0.0, 2.0, 5)
DATA_2D = np.arange(11 * 6, dtype=float).reshape(11, 6)


class _Workspace:
    def __init__(self, specs):
        self.page_specs = {spec.page_id: spec for spec in specs}
        self.current = specs[0] if specs else None
        self.added = []
        self.updates = []

    def current_spec(self):
        return self.current

    def home_spec(self):
        return next(iter(self.page_specs.values()), None)

    def page_by_id(self, page_id):
        return self.page_specs.get(page_id)

    def add_page(self, spec):
        self.page_specs[spec.page_id] = spec
        self.current = spec
        self.added.append(spec)
        return spec

    def update_page(self, page_id, *, title=None, params=None, source_page_id=None):
        spec = self.page_specs.get(page_id)
        if spec is None:
            return
        if params is not None:
            spec.params = dict(params)
        if title is not None:
            spec.title = title
        self.updates.append((page_id, title))


class _Plugin:
    def __init__(self):
        self.fail = None
        self.calls = []
        self.action = SimpleNamespace(action_id="create", label="EDC瀑布图", order=20.0)

    def integration_actions(self):
        return (self.action,)

    def compute_derived_result(self, input2d):
        self.calls.append(input2d)
        if self.fail is not None:
            raise ValueError(self.fail)
        k = np.asarray(input2d.k_axis.values)
        curves = np.asarray(input2d.data)
        return DerivedWaterfallResult(
            k=k,
            sample_indices=np.arange(k.size),
            energy=np.asarray(input2d.e_axis.values),
            raw_curves=curves,
            curves=curves / np.maximum(np.max(curves, axis=1, keepdims=True), 1e-12),
            title=f"EDC瀑布图 [测试 {len(self.calls)}]",
            plot_title="EDC Waterfall [test]",
            integrated_range=(0.0, 2.0),
            integrated_center=1.0,
            params={"k_step": 0.1},
        )


class _Manager:
    def __init__(self, plugin):
        self.plugin = plugin

    def ready_plugins(self):
        return [SimpleNamespace(plugin_id="edc_test", instance=self.plugin, manifest=SimpleNamespace(capabilities=("integration_action", "derived_result", "plugin_settings"), name="测试派生"))]

    def record(self, plugin_id):
        if str(plugin_id) != "edc_test":
            return None
        return SimpleNamespace(instance=self.plugin, manifest=SimpleNamespace(capabilities=("integration_action", "derived_result", "plugin_settings")))


class _Window:
    def __init__(self, specs):
        self.workspace = _Workspace(specs)
        self.left_workspace = self.workspace
        self.messages = []
        self.refreshes = 0
        self.shared_denoise_version = 1
        self.rotation_angle = 0.0
        self.settings = None
        self.core = SimpleNamespace(
            coord_sources={"X": "file", "Y": "file", "E": "file"},
            coord_units={"X": "1/Å", "Y": "1/Å", "E": "eV"},
        )

    # -- 宿主数据准备原语（替身） --------------------------------------
    def _resolved_axis_integral_params(self, spec):
        params = spec.params
        low = int(params.get("low", 0))
        up = int(params.get("up", low))
        return {
            "axis_index": int(params.get("axis_index", 0)),
            "low": low,
            "up": up,
            "mid": int(params.get("mid", round((low + up) / 2))),
            "source_mode": str(params.get("source_mode", "frame")),
            "source_t_index": int(params.get("source_t_index", 0)),
            "source_t_low": int(params.get("source_t_low", 0)),
            "source_t_up": int(params.get("source_t_up", 0)),
        }

    def _axis_crop_rect_from_params(self, params):
        if "crop_k_low" not in params:
            return None
        return {
            "x_low": int(params["crop_k_low"]),
            "x_up": int(params["crop_k_up"]),
            "y_low": int(params["crop_e_low"]),
            "y_up": int(params["crop_e_up"]),
        }

    def _get_display_state_for_spec(self, spec):
        return SimpleNamespace(shape=(len(K_FULL), len(E_FULL), 3, 1)), {
            "X": INTEGRATED,
            "Y": K_FULL,
            "E": E_FULL,
            "delay": np.array([0.0]),
        }

    def _build_axis_integral_base_context(self, spec, raw_data, coords):
        return {
            "view": "2d",
            "data": DATA_2D,
            "slice_info": {"axis": 0, "mode": "integral", "range": (0, 1)},
            "coords": coords,
            "plot_axes": {"x_key": "Y", "y_key": "E", "x_label": "ky", "y_label": "E"},
            "plot_logical_bounds": {"x_low": 0, "x_up": 10, "y_low": 0, "y_up": 5},
        }

    def _apply_axis_crop_to_context(self, context, rect):
        rect = dict(rect)
        updated = dict(context)
        updated["data"] = context["data"][rect["x_low"]:rect["x_up"] + 1, rect["y_low"]:rect["y_up"] + 1]
        updated["plot_logical_bounds"] = rect
        return updated

    def _current_delay_text(self, index):
        return f"{index} ps"

    def _show_message(self, title, text, icon=None):
        self.messages.append((title, text))

    def add_plugin_derived_page(self, *, title, plugin_id, action_id, source, params):
        from bandscope.ui.result_workspace import AnalysisPageSpec

        spec = AnalysisPageSpec(
            page_id="derived-1",
            title=title,
            page_kind="plugin_derived",
            source_module="plugin_derived",
            source_page_id=source.source_page_id,
            data_scope_id=source.scope_id,
            params={
                "source_mode": source.source_mode,
                DERIVED_SOURCE_KEY: source.to_mapping(),
                DERIVED_PLUGIN_KEY: plugin_id,
                DERIVED_ACTION_KEY: action_id,
                DERIVED_PARAMS_KEY: dict(params),
            },
        )
        self.left_workspace.add_page(spec)
        return spec.page_id

    def commit_derived_page_params(self, spec, *, params, title):
        spec.params[DERIVED_PARAMS_KEY] = dict(params)
        spec.title = str(title)
        self.left_workspace.update_page(spec.page_id, title=title, params=spec.params)
        self.refreshes += 1

    def global_refresh(self):
        self.refreshes += 1


def source_page(**overrides):
    from bandscope.ui.result_workspace import AnalysisPageSpec

    payload = {
        "page_id": "page-1",
        "title": "X-Integral",
        "page_kind": "axis_integral",
        "source_module": "data_process",
        "params": {
            "axis_index": 0,
            "low": 0,
            "up": 1,
            "mid": 0,
            "source_mode": "frame",
            "source_t_index": 0,
            "source_t_low": 0,
            "source_t_up": 0,
        },
    }
    payload["params"].update(overrides.pop("params", {}))
    payload.update(overrides)
    return AnalysisPageSpec(**payload)


def make_controller(specs=None, plugin=None):
    window = _Window(specs if specs is not None else [source_page()])
    plugin = plugin or _Plugin()
    session = SimpleNamespace(window=window, manager=_Manager(plugin), notify=lambda *a, **k: None)
    return window, plugin, DerivedPageController(session)


class FreezeSourceTests(unittest.TestCase):
    def test_freeze_captures_frozen_fields(self):
        window, _plugin, controller = make_controller()
        window.left_workspace.current.params["crop_k_low"] = 2
        window.left_workspace.current.params["crop_k_up"] = 8
        window.left_workspace.current.params["crop_e_low"] = 1
        window.left_workspace.current.params["crop_e_up"] = 4
        source = controller.freeze_source(
            window.left_workspace.current, "edc_test", "create"
        )
        self.assertEqual(source.source_page_id, "page-1")
        self.assertEqual(source.axis_index, 0)
        self.assertEqual((source.integral_low, source.integral_up, source.integral_mid), (0, 1, 0))
        self.assertEqual(source.crop_rect, (2, 8, 1, 4))
        self.assertEqual(source.source_mode, "frame")
        self.assertEqual(source.scope_id, "full")
        self.assertEqual(source.data_generation, 1)

    def test_freeze_rejects_wrong_page_and_erase_regions(self):
        window, _plugin, controller = make_controller(
            [source_page(page_kind="time_integral", params={"axis_index": 0})]
        )
        with self.assertRaises(DerivedSourceUnavailable):
            controller.freeze_source(window.left_workspace.current, "edc_test", "create")

        erase_spec = source_page(
            params={"crop_regions": [{"operation": "erase", "view": "2d"}]}
        )
        window.left_workspace.page_specs["page-1"] = erase_spec
        window.left_workspace.current = erase_spec
        with self.assertRaises(DerivedSourceUnavailable) as ctx:
            controller.freeze_source(erase_spec, "edc_test", "create")
        self.assertIn("擦除", str(ctx.exception))

    def test_z_integral_source_is_rejected(self):
        spec = source_page(params={"axis_index": 2})
        window, _plugin, controller = make_controller([spec])
        with self.assertRaises(DerivedSourceUnavailable):
            controller.freeze_source(spec, "edc_test", "create")


class BuildInputTests(unittest.TestCase):
    def test_input_axes_units_and_readonly(self):
        window, _plugin, controller = make_controller()
        source = controller.freeze_source(window.left_workspace.current, "edc_test", "create")
        input2d = controller.build_input(source, {"k_step_mode": "auto"}, "edc_test")
        np.testing.assert_allclose(input2d.data, DATA_2D)
        np.testing.assert_allclose(input2d.k_axis.values, K_FULL)
        np.testing.assert_allclose(input2d.e_axis.values, E_FULL)
        np.testing.assert_allclose(input2d.integrated_axis.values, INTEGRATED)
        self.assertEqual(input2d.k_axis.label, "ky")
        self.assertEqual(input2d.integrated_axis.label, "X")
        self.assertEqual(input2d.e_axis.unit, "eV")
        self.assertFalse(input2d.data.flags.writeable)
        self.assertEqual(input2d.data_generation, 1)

    def test_crop_limits_the_input_window(self):
        window, _plugin, controller = make_controller()
        spec = window.left_workspace.current
        spec.params.update({"crop_k_low": 2, "crop_k_up": 6, "crop_e_low": 1, "crop_e_up": 3})
        source = controller.freeze_source(spec, "edc_test", "create")
        input2d = controller.build_input(source, {}, "edc_test")
        self.assertEqual(input2d.shape, (5, 3))
        np.testing.assert_allclose(input2d.k_axis.values, K_FULL[2:7])
        np.testing.assert_allclose(input2d.e_axis.values, E_FULL[1:4])
        # 完整轴保持未裁剪，供自动步长与裁剪后缀使用。
        np.testing.assert_allclose(input2d.k_axis_full.values, K_FULL)
        np.testing.assert_allclose(input2d.e_axis_full.values, E_FULL)


class ContextTests(unittest.TestCase):
    def test_context_matches_the_result_and_is_cached(self):
        window, plugin, controller = make_controller()
        self.assertEqual(
            controller.invoke_action("edc_test", "create"), "derived-1"
        )
        spec = window.left_workspace.current
        self.assertEqual(spec.page_kind, "plugin_derived")
        self.assertEqual(spec.params[DERIVED_PLUGIN_KEY], "edc_test")
        self.assertEqual(len(plugin.calls), 1)

        # 创建时算过的结果直接进缓存：首次渲染不重复调用插件。
        context = controller.derived_context(spec)
        self.assertEqual(len(plugin.calls), 1)
        self.assertEqual(context["view"], "waterfall")
        self.assertEqual(context["title"], "EDC Waterfall [test]")
        np.testing.assert_allclose(context["k_values"], K_FULL)
        self.assertEqual(int(context["sample_indices"][0]), 0)
        self.assertEqual(context["integrated_range"], (0.0, 2.0))
        self.assertEqual(float(context["k_step"]), 0.1)

        # 同一份来源与参数命中缓存，不再重复调用插件。
        again = controller.derived_context(spec)
        self.assertEqual(len(plugin.calls), 1)
        self.assertIsNot(again, context)

        # 参数变化失效缓存。
        spec.params[DERIVED_PARAMS_KEY] = {"k_step_mode": "manual", "k_step": 0.2}
        controller.derived_context(spec)
        self.assertEqual(len(plugin.calls), 2)

    def test_failed_recompute_keeps_last_success_with_marker(self):
        window, plugin, controller = make_controller()
        controller.invoke_action("edc_test", "create")
        spec = window.left_workspace.current
        controller.derived_context(spec)

        plugin.fail = "步长无效"
        spec.params[DERIVED_PARAMS_KEY] = {"k_step_mode": "manual", "k_step": 99.0}
        stale = controller.derived_context(spec)
        self.assertEqual(stale["stale_error"], "步长无效")
        self.assertEqual(stale["title"], "EDC Waterfall [test]")

    def test_first_failure_creates_no_page_and_reports_the_reason(self):
        window, plugin, controller = make_controller()
        plugin.fail = "无法计算"
        self.assertIsNone(controller.invoke_action("edc_test", "create"))
        self.assertIn("无法计算", window.messages[0][1])
        self.assertEqual(window.messages[0][0], "无法创建")
        self.assertFalse(window.left_workspace.added)

    def test_missing_plugin_keeps_last_result(self):
        window, plugin, controller = make_controller()
        controller.invoke_action("edc_test", "create")
        spec = window.left_workspace.current
        controller.derived_context(spec)
        controller.session.manager.plugin = None
        controller.session.manager.ready_plugins = lambda: []
        controller.session.manager.record = lambda plugin_id: None
        spec.params[DERIVED_PARAMS_KEY] = {"k_step_mode": "manual", "k_step": 0.3}
        stale = controller.derived_context(spec)
        self.assertIn("没有加载", stale["stale_error"])


class ApplyParamsTests(unittest.TestCase):
    def test_apply_validates_then_commits_only_the_current_page(self):
        window, plugin, controller = make_controller()
        controller.invoke_action("edc_test", "create")
        spec = window.left_workspace.current
        other = source_page(page_id="page-2", title="Y-Integral")
        window.left_workspace.page_specs["page-2"] = other
        before = dict(other.params)

        ok, message = controller.apply_params_to_current_page(
            "edc_test", {"k_step_mode": "manual", "k_step": 0.25}
        )
        self.assertTrue(ok)
        self.assertIn("已应用", message)
        self.assertEqual(spec.params[DERIVED_PARAMS_KEY]["k_step"], 0.25)
        self.assertEqual(other.params, before)
        self.assertEqual(window.refreshes, 1)

    def test_apply_failure_keeps_previous_params(self):
        window, plugin, controller = make_controller()
        controller.invoke_action("edc_test", "create")
        spec = window.left_workspace.current
        old = dict(spec.params[DERIVED_PARAMS_KEY])
        plugin.fail = "步长越界"
        ok, message = controller.apply_params_to_current_page(
            "edc_test", {"k_step_mode": "manual", "k_step": 99.0}
        )
        self.assertFalse(ok)
        self.assertIn("步长越界", message)
        self.assertEqual(spec.params[DERIVED_PARAMS_KEY], old)

    def test_apply_rejects_foreign_pages(self):
        window, plugin, controller = make_controller()
        ok, message = controller.apply_params_to_current_page("edc_test", {})
        self.assertFalse(ok)
        self.assertIn("不是该插件的派生结果页", message)

    def test_current_derived_page_query(self):
        window, _plugin, controller = make_controller()
        controller.invoke_action("edc_test", "create")
        info = controller.current_derived_page("edc_test")
        self.assertIsNotNone(info)
        self.assertEqual(info.page_id, "derived-1")
        self.assertEqual(info.action_id, "create")
        self.assertEqual(info.source.source_page_id, "page-1")
        self.assertIsNone(controller.current_derived_page("other_plugin"))


class DefaultsStoreTests(unittest.TestCase):
    def test_round_trip_through_qsettings(self):
        from PyQt5.QtCore import QSettings

        store = PluginDefaultsStore(QSettings("ARPES", "ARPES_3dMAP"))
        store.save("edc_test", {"k_step_mode": "manual", "k_step": 0.05, "文本": "值"})
        loaded = store.load("edc_test")
        self.assertEqual(loaded["k_step_mode"], "manual")
        self.assertEqual(loaded["文本"], "值")
        self.assertEqual(store.load("missing"), {})

    def test_store_without_settings_is_a_noop(self):
        store = PluginDefaultsStore(None)
        store.save("edc_test", {"k_step": 1.0})
        self.assertEqual(store.load("edc_test"), {})


class ActionRegistrationTests(unittest.TestCase):
    def test_actions_are_keyed_and_sorted(self):
        window, plugin, controller = make_controller()
        plugin.action = SimpleNamespace(action_id="create", label="EDC瀑布图", order=20.0)
        actions = controller.integration_actions()
        self.assertEqual(actions, [("edc_test:create", "EDC瀑布图", 20.0)])
        self.assertTrue(controller.has_action("edc_test:create"))
        self.assertFalse(controller.has_action("edc_test:other"))

    def test_action_with_invalid_id_is_skipped(self):
        window, plugin, controller = make_controller()
        plugin.action = SimpleNamespace(action_id="bad:id", label="坏动作", order=1.0)
        self.assertEqual(controller.integration_actions(), [])


if __name__ == "__main__":
    unittest.main()
