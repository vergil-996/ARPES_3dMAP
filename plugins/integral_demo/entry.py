# -*- coding: utf-8 -*-
"""「二维积分演示」扩展入口。

它演示最小分析插件需要的全部动作，别的一概不做：

* 抓取宿主提供的**只读**二维快照；
* 在后台线程里做纯数值积分（取消是协作式的）；
* 交回一维曲线，由宿主校验、建结果页、负责展示与导出。

插件自己不建页面、不碰主窗口、不写任何文件，也不改动强度数组。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from bandscope.extensions.api import (
    ANALYSIS_CAPABILITIES,
    Plugin,
    PluginContext,
    PluginHostV2,
)

from .panel import IntegralDemoPanel


class IntegralDemoPlugin(Plugin):
    """二维积分演示插件。"""

    def __init__(self):
        self.host: Optional[PluginHostV2] = None
        # 只保留最近一份面板用于回写状态；create_panel 仍然每次都新建控件。
        self._panel: Optional[IntegralDemoPanel] = None
        self._last_axis = "x"

    # ------------------------------------------------------------------ 面板
    def create_panel(self, host):
        panel = IntegralDemoPanel(host)
        panel.combo_axis.setCurrentIndex(
            max(0, panel.combo_axis.findData(getattr(self, "_last_axis", "x")))
        )
        panel.combo_axis.currentIndexChanged.connect(self._remember_axis)
        self.host = host
        self._panel = panel
        return panel

    def _remember_axis(self, _index=None):
        panel = self._panel
        if panel is None:
            return
        try:
            self._last_axis = panel.selected_axis()
        except RuntimeError:
            self._panel = None

    # ------------------------------------------------------------------ 结果回调
    def on_analysis_finished(self, handle, status: str, detail: str = "") -> None:
        """宿主在任务结束后回调；这里只把状态写回面板。"""
        panel = self._panel
        if panel is None:
            return
        try:
            if status == "succeeded":
                panel.on_task_finished("分析完成：结果页已加入左侧页面树。")
            elif status == "cancelled":
                panel.on_task_finished("已取消。")
            else:
                panel.on_task_finished(f"分析失败：{detail or '未知原因'}")
        except RuntimeError:
            # 面板所属的页面已经被销毁：丢开引用即可，不要再碰控件。
            self._panel = None

    # ------------------------------------------------------------------ 参数
    def export_state(self) -> Dict[str, Any]:
        return {"axis": getattr(self, "_last_axis", "x")}

    def restore_state(self, state: Mapping[str, Any]) -> None:
        axis = str((state or {}).get("axis") or "x")
        self._last_axis = axis
        panel = self._panel
        if panel is None:
            return
        try:
            index = panel.combo_axis.findData(axis)
            if index >= 0:
                panel.combo_axis.setCurrentIndex(index)
        except RuntimeError:
            self._panel = None

    def reset_for_new_data(self) -> None:
        self._panel = None

    def release(self) -> None:
        self._panel = None
        self.host = None

    def on_context_changed(self, context: PluginContext) -> None:
        # 演示插件不参与渲染，只需要上下文中的页面信息在抓快照时由宿主给出。
        return None


__all__ = ["IntegralDemoPlugin", "ANALYSIS_CAPABILITIES"]
