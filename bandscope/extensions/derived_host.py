# -*- coding: utf-8 -*-
"""派生结果页（``plugin_derived``）的宿主协调器。

API 3 的宿主侧实现，与 ``analysis_host`` 各管一类任务：

- **冻结来源**：用户执行下拉动作时，把当前 X/Y 积分页及其矩形裁剪结果冻结成
  :class:`DerivedSource2D`，连同数据域、时间帧或时间积分区间一起存进结果页
  参数；此后来源页怎么变都不影响已有结果。
- **同步计算**：体数据准备仍走既有后台刷新管线，二维采样与归一化在数据就绪后
  同步执行；结果按（来源、数据修订、旋转、逐页参数）缓存，其他页激活后回来
  重算。
- **建页与更新**：创建与参数更新都先验证计算结果再提交页面状态；失败时保留
  上次成功结果并在标题上标注。自动标题只更新底层默认值，不覆盖用户改名。
- **默认值**：按插件 ID 存进宿主 QSettings，与逐页参数分离；保存立即对之后
  新建的结果页生效。

本模块不导入主窗口；窗口只提供既有的数据准备原语与三个小接口（建页、提交
参数、冻结当前来源所需的页面状态读取）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np

from bandscope.extensions.api import (
    DERIVED_CAPABILITIES,
    DerivedAxis,
    DerivedInput2D,
    DerivedPage,
    DerivedSource2D,
    DerivedSourceUnavailable,
    PluginError,
    SOURCE_FILE,
    read_only_array,
    validate_derived_waterfall,
)
from bandscope.core.axis_units import resolve_axis_unit

#: 派生结果页的占位页面类型；宿主按能力处理，不识别具体插件 ID。
DERIVED_PAGE_KIND = "plugin_derived"

#: 结果页参数键。
DERIVED_SOURCE_KEY = "derived_source"
DERIVED_PLUGIN_KEY = "derived_plugin_id"
DERIVED_ACTION_KEY = "derived_action_id"
DERIVED_PARAMS_KEY = "derived_params"

#: 重建二维基础上下文时使用的合成来源页 ID。宿主既有的动量前缀缓存是单槽位
#: 且按页记录，所有派生页共用这一个名字，避免和真实页面互相覆盖；该名字不会
#: 出现在页面树里，也不会被 ``_is_current_page`` 当成真实页面。
DERIVED_SOURCE_PAGE_ID = "__derived_source__"

#: 默认值在 QSettings 里的分组。
DEFAULTS_GROUP = "plugin_defaults"


def _region_operation(region) -> str:
    if isinstance(region, Mapping):
        return str(region.get("operation") or "")
    return str(getattr(region, "operation", "") or "")


def window_attr(window, name, default=None):
    """读主窗口属性；先查实例 ``__dict__``，避开测试替身的 sip 属性陷阱。

    部分测试用 ``My3DAnalyzer.__new__`` 绕过 QWidget 构造，此时任何
    ``getattr(window, ...)`` 都会抛 ``RuntimeError``；实例字典查询安全。
    """
    payload = getattr(window, "__dict__", None)
    if isinstance(payload, dict) and name in payload:
        return payload[name]
    try:
        return getattr(window, name, default)
    except Exception:
        return default


def _displayable_error(exc: Exception) -> str:
    """插件错误的可展示文本；未知异常也说明类型，方便排查。"""
    message = str(exc).strip()
    if isinstance(exc, (PluginError, ValueError)) and message:
        return message
    name = type(exc).__name__
    return f"{name}: {message}" if message else name


class PluginDefaultsStore:
    """按插件 ID 读写「新建默认值」（宿主 QSettings，JSON 文本存储）。"""

    def __init__(self, settings=None):
        self._settings = settings

    @staticmethod
    def _key(plugin_id: str) -> str:
        return f"{DEFAULTS_GROUP}/{str(plugin_id)}"

    def load(self, plugin_id: str) -> Dict[str, Any]:
        settings = self._settings
        if settings is None:
            return {}
        raw = settings.value(self._key(plugin_id), "", type=str)
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    def save(self, plugin_id: str, values: Mapping[str, Any]) -> None:
        settings = self._settings
        if settings is None:
            return
        settings.setValue(
            self._key(plugin_id),
            json.dumps(dict(values or {}), ensure_ascii=False, sort_keys=True),
        )


class _SourceSpec:
    """把冻结来源伪装成一个 axis_integral 页，复用宿主既有的二维基础上下文。

    页面 ID 固定为 :data:`DERIVED_SOURCE_PAGE_ID`，保证它既不会命中真实页面，
    也让 ``_axis_prefix_cache_key`` 在准备路径与渲染路径之间保持一致。
    """

    def __init__(self, source: DerivedSource2D):
        self.page_id = DERIVED_SOURCE_PAGE_ID
        self.page_kind = "axis_integral"
        self.title = source.source_title or DERIVED_SOURCE_PAGE_ID
        self.source_page_id = None
        self.data_scope_id = str(source.scope_id or "full")
        self.params = {
            "axis_index": int(source.axis_index),
            "low": int(source.integral_low),
            "up": int(source.integral_up),
            "mid": int(source.integral_mid),
            "source_mode": str(source.source_mode or "frame"),
            "source_t_index": int(source.frame_index),
            "source_t_low": int(source.time_low),
            "source_t_up": int(source.time_up),
        }

def synthetic_source_spec_from_params(params: Mapping[str, Any]) -> Optional[_SourceSpec]:
    """从结果页参数构造合成来源规格；缺冻结来源时返回 None。"""
    payload = params.get(DERIVED_SOURCE_KEY)
    if not isinstance(payload, Mapping) or not payload:
        return None
    try:
        source = DerivedSource2D.from_mapping(payload)
    except (TypeError, ValueError):
        return None
    return _SourceSpec(source)


class DerivedPageController:
    """一个主窗口对应一个控制器：注册动作、冻结来源、计算与建页。"""

    def __init__(self, session):
        self.session = session
        self.window = session.window
        settings = window_attr(self.window, "settings", None)
        if settings is None:
            settings = self._fallback_settings()
        self.defaults_store = PluginDefaultsStore(settings)
        #: page_id → {"key": 缓存键, "context": 渲染上下文, "notified": 是否已提示失败}
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._actions_cache: Optional[List[Tuple[str, str, float]]] = None

    @staticmethod
    def _fallback_settings():
        """测试替身没有 ``settings`` 时退回一个同组织/应用的 QSettings。"""
        try:
            from PyQt5.QtCore import QSettings

            return QSettings("ARPES", "ARPES_3dMAP")
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 能力判定
    # ------------------------------------------------------------------
    @staticmethod
    def is_derived_plugin(record) -> bool:
        manifest = getattr(record, "manifest", None)
        capabilities = set(getattr(manifest, "capabilities", ()) or ())
        return set(DERIVED_CAPABILITIES).issubset(capabilities)

    @staticmethod
    def is_settings_plugin(record) -> bool:
        manifest = getattr(record, "manifest", None)
        capabilities = set(getattr(manifest, "capabilities", ()) or ())
        return "plugin_settings" in capabilities

    def _instance(self, plugin_id: str):
        record = self.session.manager.record(str(plugin_id))
        instance = getattr(record, "instance", None) if record is not None else None
        if instance is None:
            raise DerivedSourceUnavailable(f"插件「{plugin_id}」当前没有加载，无法创建或重算派生结果。")
        return instance

    # ------------------------------------------------------------------
    # 「其他积分」下拉动作
    # ------------------------------------------------------------------
    def integration_actions(self) -> List[Tuple[str, str, float]]:
        """收集已加载插件注册的动作：``(稳定键, 展示文本, 排序权重)``。"""
        actions: List[Tuple[str, str, float]] = []
        for record in self.session.manager.ready_plugins():
            if not self.is_derived_plugin(record):
                continue
            try:
                declared = list(record.instance.integration_actions() or ())
            except Exception:
                continue
            for action in declared:
                action_id = str(getattr(action, "action_id", "") or "").strip()
                label = str(getattr(action, "label", "") or "").strip()
                if not action_id or not label or ":" in action_id:
                    continue
                try:
                    order = float(getattr(action, "order", 50.0))
                except (TypeError, ValueError):
                    order = 50.0
                actions.append((f"{record.plugin_id}:{action_id}", label, order))
        actions.sort(key=lambda item: (item[2], item[1]))
        self._actions_cache = list(actions)
        return list(actions)

    def has_action(self, key: str) -> bool:
        if self._actions_cache is None:
            self.integration_actions()
        return any(item[0] == str(key) for item in self._actions_cache or ())

    def invoke_action(self, plugin_id: str, action_id: str) -> Optional[str]:
        """执行一次下拉动作：冻结来源 → 插件计算 → 宿主建页（失败不建页）。"""
        window = self.window
        plugin_id = str(plugin_id)
        action_id = str(action_id)
        source_spec = window.left_workspace.current_spec()
        try:
            instance = self._instance(plugin_id)
            source = self.freeze_source(source_spec, plugin_id, action_id)
            params = dict(self.defaults_store.load(plugin_id))
            input2d = self.build_input(source, params, plugin_id)
            validated = validate_derived_waterfall(
                instance.compute_derived_result(input2d), input2d
            )
        except Exception as exc:
            _show_error_message(window, "无法创建", _displayable_error(exc))
            return None
        title = validated.title or f"{source.source_title or '派生结果'}"
        page_id = window.add_plugin_derived_page(
            title=title,
            plugin_id=plugin_id,
            action_id=action_id,
            source=source,
            params=params,
        )
        if page_id:
            # 创建时已经算过一次：把结果放进缓存，首次渲染不再重复计算。
            self._cache[str(page_id)] = {
                "key": self._cache_key(page_id, source, params),
                "context": self._context_from_result(source, input2d, validated),
                "notified": False,
            }
            remember = getattr(window, "_remember_derived_recipe", None)
            if remember is not None:
                remember(window.left_workspace.page_by_id(page_id), self._cache[str(page_id)]["context"])
        return page_id

    # ------------------------------------------------------------------
    # 冻结来源
    # ------------------------------------------------------------------
    def freeze_source(self, spec, plugin_id: str, action_id: str) -> DerivedSource2D:
        window = self.window
        if spec is None:
            raise DerivedSourceUnavailable("当前没有可用的来源页。")
        if spec.page_kind not in {"axis_integral", "axis_integral_crop"}:
            raise DerivedSourceUnavailable(
                "该动作只支持从 X 积分 / Y 积分结果页创建，请先得到二维积分结果。"
            )
        for region in spec.params.get("crop_regions") or ():
            if _region_operation(region) == "erase":
                raise DerivedSourceUnavailable(
                    "带擦除区域的来源不能创建派生结果，请改用矩形裁剪后的二维结果。"
                )
        params = window._resolved_axis_integral_params(spec)
        if params is None:
            raise DerivedSourceUnavailable("无法读取来源页的积分参数。")
        axis_index = int(params["axis_index"])
        if axis_index not in (0, 1):
            raise DerivedSourceUnavailable(
                "该动作只支持从 X 积分 / Y 积分结果页创建。"
            )
        crop_rect = window._axis_crop_rect_from_params(spec.params)
        crop_tuple = None
        if crop_rect is not None:
            crop_tuple = (
                int(crop_rect["x_low"]),
                int(crop_rect["x_up"]),
                int(crop_rect["y_low"]),
                int(crop_rect["y_up"]),
            )
        return DerivedSource2D(
            plugin_id=str(plugin_id),
            action_id=str(action_id),
            source_page_id=str(spec.page_id),
            source_page_kind=str(spec.page_kind),
            axis_index=axis_index,
            integral_low=int(params["low"]),
            integral_up=int(params["up"]),
            integral_mid=int(params["mid"]),
            source_mode=str(params.get("source_mode") or "frame"),
            frame_index=int(params.get("source_t_index", 0)),
            time_low=int(params.get("source_t_low", 0)),
            time_up=int(params.get("source_t_up", 0)),
            scope_id=str(getattr(spec, "data_scope_id", "full") or "full"),
            crop_rect=crop_tuple,
            source_title=str(getattr(spec, "title", "") or ""),
            data_generation=int(window_attr(window, "shared_denoise_version", 0) or 0),
        )

    # ------------------------------------------------------------------
    # 只读输入
    # ------------------------------------------------------------------
    @staticmethod
    def _axis(values, key: str, label: str, window) -> DerivedAxis:
        core = window_attr(window, "core", None)
        sources = dict(getattr(core, "coord_sources", {}) or {})
        units = dict(getattr(core, "coord_units", {}) or {})
        source = str(sources.get(key, "index"))
        # 时间轴缺单位时按 fs 转述；其它轴仍只转述显式元数据。
        unit = resolve_axis_unit(key, units.get(key)) if source == SOURCE_FILE else ""
        return DerivedAxis(
            key=str(key),
            label=str(label),
            values=read_only_array(np.asarray(values, dtype=np.float64).reshape(-1)),
            unit=unit,
            source=source,
        )

    def build_input(
        self,
        source: DerivedSource2D,
        params: Mapping[str, Any],
        plugin_id: str,
    ) -> DerivedInput2D:
        """把冻结来源还原成插件看到的只读输入（原始坐标排列）。"""
        window = self.window
        synth = _SourceSpec(source)
        raw_data, coords = window._get_display_state_for_spec(synth)
        if raw_data is None or coords is None:
            raise DerivedSourceUnavailable("来源数据不可用；数据文件可能已被替换。")
        base = window._build_axis_integral_base_context(synth, raw_data, coords)
        if base is None:
            raise DerivedSourceUnavailable("无法重建来源的二维积分结果。")
        if source.crop_rect is not None:
            rect = {
                "x_low": int(source.crop_rect[0]),
                "x_up": int(source.crop_rect[1]),
                "y_low": int(source.crop_rect[2]),
                "y_up": int(source.crop_rect[3]),
            }
            base = window._apply_axis_crop_to_context(base, rect)
        data = np.asarray(base.get("data"), dtype=np.float64)
        if data.ndim != 2 or data.size == 0:
            raise DerivedSourceUnavailable("来源的裁剪范围内没有有效数据。")

        plot_axes = dict(base.get("plot_axes") or {})
        bounds = dict(base.get("plot_logical_bounds") or {})
        k_key = str(plot_axes.get("x_key") or ("Y" if source.axis_index == 0 else "X"))
        e_key = str(plot_axes.get("y_key") or "E")
        x_low = int(bounds.get("x_low", 0))
        x_up = int(bounds.get("x_up", data.shape[0] - 1))
        y_low = int(bounds.get("y_low", 0))
        y_up = int(bounds.get("y_up", data.shape[1] - 1))
        if x_up < x_low or y_up < y_low:
            raise DerivedSourceUnavailable("来源的裁剪范围内没有有效数据。")
        if data.shape != (x_up - x_low + 1, y_up - y_low + 1):
            raise DerivedSourceUnavailable("来源结果与裁剪范围不一致。")

        k_values = np.asarray(coords[k_key], dtype=np.float64)[x_low : x_up + 1]
        e_values = np.asarray(coords[e_key], dtype=np.float64)[y_low : y_up + 1]
        k_values_full = np.asarray(coords[k_key], dtype=np.float64).reshape(-1)
        e_values_full = np.asarray(coords[e_key], dtype=np.float64).reshape(-1)
        # 被积轴就是积分所沿的那根轴：0 是 X 积分（被积轴 X），1 是 Y 积分。
        integrated_key = "X" if int(source.axis_index) == 0 else "Y"
        integrated_values = np.asarray(coords[integrated_key], dtype=np.float64).reshape(-1)

        k_label = {"X": "kx", "Y": "ky"}.get(k_key, k_key)
        frame_label = ""
        if source.source_mode == "time_integral":
            frame_label = f"时间积分 {int(source.time_low)}~{int(source.time_up)}"
        else:
            try:
                frame_label = "帧 " + str(window._current_delay_text(int(source.frame_index)))
            except Exception:
                frame_label = f"帧 {int(source.frame_index)}"
        slice_info = dict(base.get("slice_info") or {})
        roi_label = str(slice_info.get("title_override") or "")

        return DerivedInput2D(
            plugin_id=str(plugin_id),
            source=source,
            params=dict(params or {}),
            data=read_only_array(data),
            k_axis=self._axis(k_values, k_key, k_label, window),
            e_axis=self._axis(e_values, e_key, "E", window),
            k_axis_full=self._axis(k_values_full, k_key, k_label, window),
            e_axis_full=self._axis(e_values_full, e_key, "E", window),
            integrated_axis=self._axis(
                integrated_values, integrated_key, integrated_key, window
            ),
            source_title=str(source.source_title or ""),
            frame_label=frame_label,
            roi_label=roi_label,
            data_generation=int(window_attr(window, "shared_denoise_version", 0) or 0),
        )

    # ------------------------------------------------------------------
    # 渲染上下文与缓存
    # ------------------------------------------------------------------
    def _cache_key(self, page_id: str, source: DerivedSource2D, params: Mapping[str, Any]) -> tuple:
        window = self.window
        return (
            str(page_id),
            repr(source.to_mapping()),
            int(window_attr(window, "shared_denoise_version", 0) or 0),
            round(float(window_attr(window, "rotation_angle", 0.0) or 0.0), 2),
            repr(dict(params or {})),
        )

    def derived_context(self, spec) -> Optional[dict]:
        """渲染用的 waterfall 上下文；失败时回退上次成功结果并标注。"""
        payload = spec.params.get(DERIVED_SOURCE_KEY)
        if not isinstance(payload, Mapping) or not payload:
            raise ValueError("该派生结果页缺少冻结来源信息，无法重算。")
        plugin_id = str(spec.params.get(DERIVED_PLUGIN_KEY) or "")
        params = dict(spec.params.get(DERIVED_PARAMS_KEY) or {})
        source = DerivedSource2D.from_mapping(payload)
        key = self._cache_key(spec.page_id, source, params)
        entry = self._cache.get(str(spec.page_id))
        if entry is not None and entry.get("key") == key and entry.get("context") is not None:
            return dict(entry["context"])
        try:
            instance = self._instance(plugin_id)
            input2d = self.build_input(source, params, plugin_id)
            validated = validate_derived_waterfall(
                instance.compute_derived_result(input2d), input2d
            )
        except Exception as exc:
            message = _displayable_error(exc)
            if entry is not None and entry.get("context") is not None:
                stale = dict(entry["context"])
                stale["stale_error"] = message
                if not entry.get("notified"):
                    entry["notified"] = True
                    self.session.notify(
                        f"派生结果无法按当前参数重算：{message}。已保留上次成功结果。",
                        level="warning",
                    )
                return stale
            raise ValueError(message) from exc
        context = self._context_from_result(source, input2d, validated)
        self._cache[str(spec.page_id)] = {
            "key": key,
            "context": context,
            "notified": False,
        }
        remember = getattr(self.window, "_remember_derived_recipe", None)
        if remember is not None:
            remember(spec, context)
        return context

    def _context_from_result(self, source, input2d, result) -> dict:
        crop_rect = None
        if source.crop_rect is not None:
            crop_rect = {
                "x_low": int(source.crop_rect[0]),
                "x_up": int(source.crop_rect[1]),
                "y_low": int(source.crop_rect[2]),
                "y_up": int(source.crop_rect[3]),
            }
        energy = np.array(result.energy, dtype=np.float64, copy=True)
        context = {
            "view": "waterfall",
            "title": str(result.plot_title or result.title or f"{source.source_title or '派生结果'}"),
            "energy_axis": energy,
            "k_values": np.array(result.k, dtype=np.float64, copy=True),
            "k_axis_key": str(input2d.k_axis.key),
            "sample_indices": np.array(result.sample_indices, copy=True),
            "curves": np.array(result.curves, dtype=np.float64, copy=True),
            "raw_curves": np.array(result.raw_curves, dtype=np.float64, copy=True),
            "offset_step": float(result.offset_step),
            "xlabel": str(result.xlabel),
            "ylabel": str(result.ylabel),
            "k_axis_label": str(result.k_label),
            "integrated_axis_label": str(result.integrated_label),
            "integrated_range": result.integrated_range,
            "integrated_center": result.integrated_center,
            "crop_rect": crop_rect,
            "energy_range": (float(energy[0]), float(energy[-1])),
            "derived_plugin_id": str(source.plugin_id),
            "derived_action_id": str(source.action_id),
        }
        if result.curve_offsets is not None:
            context["curve_offsets"] = np.array(result.curve_offsets, dtype=np.float64, copy=True)
        resolved = dict(result.params or {})
        if "k_step" in resolved:
            context["k_step"] = float(resolved["k_step"])
        context["derived_params"] = resolved
        return context

    def invalidate_page(self, page_id: str) -> None:
        self._cache.pop(str(page_id), None)

    def clear_cache(self) -> None:
        """换数据文件后所有冻结来源与缓存一律作废。"""
        self._cache.clear()
        self._actions_cache = None

    # ------------------------------------------------------------------
    # 默认值与当前结果页
    # ------------------------------------------------------------------
    def _current_derived_spec(self, plugin_id: str):
        spec = self.window.left_workspace.current_spec()
        if (
            spec is None
            or spec.page_kind != DERIVED_PAGE_KIND
            or str(spec.params.get(DERIVED_PLUGIN_KEY) or "") != str(plugin_id)
        ):
            return None
        if not isinstance(spec.params.get(DERIVED_SOURCE_KEY), Mapping):
            return None
        return spec

    def current_derived_page(self, plugin_id: str) -> Optional[DerivedPage]:
        spec = self._current_derived_spec(plugin_id)
        if spec is None:
            return None
        source = DerivedSource2D.from_mapping(spec.params.get(DERIVED_SOURCE_KEY))
        return DerivedPage(
            plugin_id=str(plugin_id),
            action_id=str(spec.params.get(DERIVED_ACTION_KEY) or ""),
            page_id=str(spec.page_id),
            title=str(spec.title),
            params=dict(spec.params.get(DERIVED_PARAMS_KEY) or {}),
            source=source,
        )

    def apply_params_to_current_page(
        self, plugin_id: str, params: Mapping[str, Any]
    ) -> Tuple[bool, str]:
        """把参数应用到当前派生页：先验证计算结果，通过后提交并刷新。"""
        spec = self._current_derived_spec(plugin_id)
        if spec is None:
            return False, "当前页不是该插件的派生结果页，已保留各页原有参数。"
        if spec.params.get("_restored_result"):
            return False, "此页为保存的结果快照；请从来源页新建派生结果后修改参数。"
        source = DerivedSource2D.from_mapping(spec.params.get(DERIVED_SOURCE_KEY))
        try:
            instance = self._instance(plugin_id)
            input2d = self.build_input(source, params, plugin_id)
            validated = validate_derived_waterfall(
                instance.compute_derived_result(input2d), input2d
            )
        except Exception as exc:
            return False, f"{_displayable_error(exc)}（已保留上次成功结果）"
        self.window.commit_derived_page_params(
            spec, params=dict(params or {}), title=validated.title or str(spec.title)
        )
        return True, "已应用到当前瀑布图；其他页面保留各自参数。"


def _show_error_message(window, title: str, message: str) -> None:
    """用主窗口既有的消息出口展示错误；测试替身只提供两参版本时降级调用。"""
    try:
        from PyQt5.QtWidgets import QMessageBox

        window._show_message(title, message, QMessageBox.Warning)
    except Exception:
        try:
            window._show_message(title, message)
        except Exception:
            pass
