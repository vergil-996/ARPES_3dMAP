# -*- coding: utf-8 -*-
"""「能带重构」扩展入口。

它演示三维分析插件需要的全部动作，别的一概不做：

* 抓取宿主提供的**只读**三维快照（当前数据域 + 当前帧 + 去噪）；
* 在后台线程里做纯数值重构（预处理 → 逐带 L-BFGS-B），逐带与逐迭代报进度；
* 交回一批能带面，由宿主校验、建结果页、负责展示与导出。

插件自己不建页面、不碰主窗口、不写任何文件，也不改动体数据。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from bandscope.extensions.api import ANALYSIS_3D_CAPABILITIES, Plugin, PluginContext, PluginHostV3

from .panel import BandReconstructPanel


class BandReconstructPlugin(Plugin):
    """MRF 能带重构插件。"""

    def __init__(self):
        self.host: Optional[PluginHostV3] = None
        self._panel: Optional[BandReconstructPanel] = None
        self._state: Dict[str, Any] = {}

    # ------------------------------------------------------------------ 面板
    def create_panel(self, host):
        panel = BandReconstructPanel(host)
        if self._state:
            panel.apply_settings(self._state)
        self.host = host
        self._panel = panel
        return panel

    def _remember_panel_state(self) -> None:
        panel = self._panel
        if panel is None:
            return
        try:
            self._state = panel.settings_snapshot()
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
                panel.on_task_finished("重构完成：结果页已加入左侧结果树。")
            elif status == "cancelled":
                # 取消可能是用户点的，也可能是数据换代/来源页关闭导致的作废：
                # 宿主的原因比笼统的「已取消」有用，原样显示。
                panel.on_task_finished(str(detail or "已取消。"))
            else:
                panel.on_task_finished(f"重构失败：{detail or '未知原因'}")
        except RuntimeError:
            # 面板所属的页面已经被销毁：丢开引用即可，不要再碰控件。
            self._panel = None

    def on_analysis_progress(self, handle, fraction: float, message: str = "") -> None:
        panel = self._panel
        if panel is None:
            return
        try:
            panel.on_progress(fraction, message)
        except RuntimeError:
            self._panel = None

    # ------------------------------------------------------------------ 参数
    def export_state(self) -> Dict[str, Any]:
        self._remember_panel_state()
        return dict(self._state)

    def restore_state(self, state: Mapping[str, Any]) -> None:
        self._state = dict(state or {})
        panel = self._panel
        if panel is None:
            return
        try:
            panel.apply_settings(self._state)
        except RuntimeError:
            self._panel = None

    def reset_for_new_data(self) -> None:
        # 换数据后旧快照与结果都作废；面板保留参数，等待用户在新数据上重跑。
        self._panel = None

    def release(self) -> None:
        self._panel = None
        self.host = None

    def on_context_changed(self, context: PluginContext) -> None:
        # 插件不参与渲染；面板显隐由宿主按能力控制。
        return None


__all__ = ["BandReconstructPlugin", "ANALYSIS_3D_CAPABILITIES"]
