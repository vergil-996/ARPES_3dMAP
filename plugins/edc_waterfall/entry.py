# -*- coding: utf-8 -*-
"""「EDC 瀑布图」扩展入口（API 3）。

插件只做四件事，别的都归宿主：

1. 向「其他积分」下拉注册一个动作（稳定键 ``create``）；
2. 冻结来源后计算派生结果（:mod:`edc` 的纯数值 + 自动标题）；
3. 在插件管理里提供设置界面（自动 / 手动 k 步长）；
4. 维护一份设置状态，供宿主「保存为新建默认值」与「应用到当前瀑布图」。

插件不建页面、不绘图、不碰 VTK、不写文件，也不改动任何强度数组。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from bandscope.extensions.api import (
    IntegrationAction,
    Plugin,
    PluginContext,
)

from .edc import MODE_AUTO, compute_waterfall
from .settings_panel import EdcSettingsPanel

#: 下拉动作：排序权重 20 让 EDC 瀑布图落在迁移前的位置（能级态密度之后）。
ACTION_CREATE = "create"
ACTION_LABEL = "EDC瀑布图"
ACTION_ORDER = 20.0


class EdcWaterfallPlugin(Plugin):
    """EDC 瀑布图插件。"""

    def __init__(self):
        #: 当前设置状态；面板存活时以面板为准，不在时沿用这里保存的副本。
        self._settings: Dict[str, Any] = {"k_step_mode": MODE_AUTO, "k_step": 0.01}
        self._panel: Optional[EdcSettingsPanel] = None
        self.host = None

    # ------------------------------------------------------------------ 面板
    def create_panel(self, host):
        """本插件不参与 3D 渲染，没有渲染控制卡片；设置界面另见
        :meth:`create_settings_panel`（挂在插件管理里）。"""
        return None

    # ------------------------------------------------------------------ 动作
    def integration_actions(self) -> Sequence[IntegrationAction]:
        return (IntegrationAction(ACTION_CREATE, ACTION_LABEL, ACTION_ORDER),)

    # ------------------------------------------------------------------ 计算
    def compute_derived_result(self, source):
        return compute_waterfall(source)

    # ------------------------------------------------------------------ 设置
    def restore_settings(self, state: Mapping[str, Any]) -> None:
        """恢复保存的默认值；容忍缺失与多余字段。"""
        payload = dict(state or {})
        if payload:
            self._settings = {
                "k_step_mode": str(payload.get("k_step_mode") or self._settings["k_step_mode"]),
                "k_step": payload.get("k_step", self._settings["k_step"]),
            }
        panel = self._settings_panel()
        if panel is not None:
            try:
                panel.restore(self._settings)
            except RuntimeError:
                self._panel = None

    def export_settings(self) -> Mapping[str, Any]:
        """当前设置状态：优先读面板，面板不在时用内部副本。"""
        panel = self._settings_panel()
        if panel is not None:
            try:
                self._settings = dict(panel.export_state())
            except RuntimeError:
                self._panel = None
        return dict(self._settings)

    def create_settings_panel(self, host):
        """新建设置面板；宿主负责挂载与生命周期。"""
        self.host = host
        panel = EdcSettingsPanel(self._settings, on_changed=self._remember_settings)
        self._panel = panel
        return panel

    def _remember_settings(self, state: Dict[str, Any]) -> None:
        self._settings = dict(state)

    def _settings_panel(self) -> Optional[EdcSettingsPanel]:
        panel = self._panel
        if panel is None:
            return None
        try:
            panel.isVisible()
        except RuntimeError:
            # 面板所属的窗口已经销毁：丢开引用即可。
            self._panel = None
            return None
        return panel

    # ------------------------------------------------------------------ 其他钩子
    def on_context_changed(self, context: PluginContext) -> None:
        # 本插件不参与渲染，只在动作与设置里工作。
        return None

    def reset_for_new_data(self) -> None:
        # 冻结来源随页面走，插件本身没有与旧数据绑定的状态。
        return None

    def release(self) -> None:
        self._panel = None
        self.host = None


__all__ = ["EdcWaterfallPlugin", "ACTION_CREATE", "ACTION_LABEL"]
