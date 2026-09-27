# -*- coding: utf-8 -*-
"""「平带增强」扩展入口。

职责边界：

* 持有 :class:`effect.EffectState`（总开关、背景保留、可变长度的条目列表），
  面板只是它的一层视图。
* 实现 ``plugin_api.Plugin``：交出面板、按当前能量轴算一维倍率、导出/恢复
  参数、换数据时清空峰位。
* **不碰渲染、不碰 VTK、不改强度数组**：倍率交给宿主去换算到体素并应用。

按页面保存、预设导入导出、裁剪派生页继承，都由宿主按 ``export_state`` /
``restore_state`` 的字典完成，本模块不做任何自己的持久化。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

from PyQt5.QtWidgets import QFileDialog, QWidget

from bandscope.extensions.api import (
    CAPABILITY_OPACITY_MULTIPLIER,
    Plugin,
    PluginContext,
    PluginHost,
)

from .effect import (
    SCHEMA_VERSION,
    EffectState,
    multiplier_for,
    preset_unit_mismatch,
)

#: 预设文件过滤器；扩展名区分主程序自己的工程文件。
PRESET_FILTER = "平带增强预设 (*.json)"

#: 提示文本的级别，交宿主决定呈现方式。
LEVEL_INFO = "info"
LEVEL_WARNING = "warning"


def _axis_changed(previous: Optional[PluginContext], current: PluginContext) -> bool:
    """只有能量轴/视图真的变了才刷新面板。

    宿主每个渲染周期都会通知一次上下文；拖动时间轴、旋转相机时视图与坐标都
    没变，重复做一次控件同步（改文本、重算提示、可能的重新布局）纯属浪费。
    """
    if previous is None:
        return True
    if previous.view != current.view:
        return True
    if previous.display_e_flip != current.display_e_flip:
        return True
    before, after = previous.energy, current.energy
    if (before is None) != (after is None):
        return True
    if before is None:
        return False
    return (
        before.source != after.source
        or before.unit != after.unit
        or before.roi_range != after.roi_range
        or before.values.shape != after.values.shape
    )


class FlatBandOpacityPlugin(Plugin):
    """插件主体。宿主为每个页面各建一个实例。"""

    def __init__(self):
        self._host: Optional[PluginHost] = None
        self._state = EffectState()
        self._panel: Optional[QWidget] = None
        self._context: Optional[PluginContext] = None

    # ------------------------------------------------------------------
    # plugin_api.Plugin
    # ------------------------------------------------------------------
    def create_panel(self, host: PluginHost):
        # 延迟导入：加载插件时不需要 Qt 控件类，只有真正挂载面板才建。
        from .controls import FlatBandPanel

        self._host = host
        # 每次请求面板都给一份新的视图，但共享同一个 EffectState：宿主可以按
        # 需要重建卡片，参数不会因此丢失。
        panel = FlatBandPanel(self._state)
        panel.changed.connect(self._on_panel_changed)
        panel.preset_export_requested.connect(self.export_preset)
        panel.preset_import_requested.connect(self.import_preset)
        self._panel = panel
        if self._context is not None:
            panel.sync_context(self._context)
        return panel

    def on_context_changed(self, context: PluginContext) -> None:
        previous, self._context = self._context, context
        panel = self._panel
        if panel is not None and _axis_changed(previous, context):
            panel.sync_context(context)

    def opacity_multiplier(self, context: PluginContext):
        if not self._state.enabled or context.energy is None:
            return None
        return multiplier_for(
            self._state.bands, context.energy.values, self._state.background
        )

    def export_state(self) -> Dict[str, Any]:
        return self._state.to_state()

    def restore_state(self, state: Mapping[str, Any]) -> None:
        """恢复参数快照。

        调用点包括「导入预设」按钮的 Qt 槽，预设文件是用户可以直接编辑的，
        schema 字段可能是任意 JSON 值；这里必须容错，任何异常都会让进程退出。
        """
        if not state:
            return
        try:
            schema = int(state.get("schema", SCHEMA_VERSION))
        except (TypeError, ValueError):
            schema = SCHEMA_VERSION
        if schema > SCHEMA_VERSION:
            self._notify(
                "页面保存的参数来自更新的插件版本，已按当前版本尽力恢复。",
                LEVEL_WARNING,
            )
        try:
            self._state = EffectState.from_state(state)
        except Exception as exc:
            self._notify(f"参数恢复失败，已保留当前设置：{exc}", LEVEL_WARNING)
            return
        self._refresh_panel()

    def reset_for_new_data(self) -> None:
        """换数据文件时清空峰位：旧数据的峰位不能自动套到新数据上。"""
        self._state = EffectState(enabled=self._state.enabled)
        self._refresh_panel()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _on_panel_changed(self):
        if self._host is not None:
            self._host.request_refresh()

    def _refresh_panel(self):
        panel = self._panel
        if panel is None:
            return
        axis = self._context.energy if self._context is not None else None
        panel.state = self._state
        panel.refresh_from_state(axis)

    def _notify(self, message: str, level: str = LEVEL_INFO):
        if self._host is not None:
            self._host.notify(message, level=level)

    def _current_axis(self):
        return self._context.energy if self._context is not None else None

    # -- 预设 -----------------------------------------------------------
    def export_preset(self):
        if self._panel is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self._panel, "导出平带增强预设", "flat_band_preset.json", PRESET_FILTER
        )
        if not path:
            return
        payload = self._state.to_preset(self._current_axis())
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except OSError as exc:
            self._notify(f"预设导出失败：{exc}", LEVEL_WARNING)
            return
        self._notify(f"已导出 {len(self._state.bands)} 条平带的预设。")

    def import_preset(self):
        if self._panel is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self._panel, "导入平带增强预设", "", PRESET_FILTER
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            self._notify(f"预设读取失败：{exc}", LEVEL_WARNING)
            return
        if not isinstance(payload, Mapping):
            self._notify("预设文件格式不正确。", LEVEL_WARNING)
            return

        # 整个导入过程都包在兜底里：这是按钮的 Qt 槽，未捕获异常会直接结束进程。
        try:
            warned = preset_unit_mismatch(payload, self._current_axis())
            self.restore_state(payload)
        except Exception as exc:
            self._notify(f"预设导入失败：{exc}", LEVEL_WARNING)
            return
        summary = f"已导入 {len(self._state.bands)} 条平带。"
        self._notify(f"{summary} {warned}" if warned else summary,
                     LEVEL_WARNING if warned else LEVEL_INFO)


#: 清单 ``entry_point`` 指向的名字；保留一个显式别名便于阅读。
PluginClass = FlatBandOpacityPlugin

__all__ = ["FlatBandOpacityPlugin", "PluginClass", CAPABILITY_OPACITY_MULTIPLIER]
