# -*- coding: utf-8 -*-
"""演示插件的参数面板：选积分方向、抓快照、提交后台任务、取消。

面板只做三件事：读用户选择、调宿主接口、显示结果状态。它在二维结果页显示
（宿主按能力控制显隐），拿到的宿主句柄绑定本插件 id，因此队列与限制都是按
插件计的。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from bandscope.extensions.api import AnalysisTaskHandle, AnalysisUnavailable, PluginHostV2
from bandscope.extensions.ui import theme

from .analysis import AXIS_CHOICES, AXIS_X, axis_label, run_integral


class IntegralDemoPanel(QWidget):
    """分析参数与运行状态。"""

    def __init__(self, host: PluginHostV2, parent=None):
        super().__init__(parent)
        self.host = host
        self._handle: AnalysisTaskHandle | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.combo_axis = QComboBox(self)
        self.combo_axis.setFixedHeight(28)
        for axis in AXIS_CHOICES:
            self.combo_axis.addItem(axis_label(axis), axis)
        self.combo_axis.setCurrentIndex(AXIS_CHOICES.index(AXIS_X))
        self.combo_axis.setStyleSheet(
            f"QComboBox {{ color: {theme.TEXT_1}; background-color: {theme.BG_3};"
            f" border: 1px solid {theme.BORDER_HEX}; border-radius: 4px; padding: 2px 8px; }}"
        )
        row.addWidget(self.combo_axis, 1)

        self.btn_run = QPushButton("分析当前二维结果", self)
        self.btn_run.setCursor(Qt.PointingHandCursor)
        self.btn_run.setMinimumHeight(28)
        theme.style_push_button(self.btn_run, "primary")
        self.btn_run.clicked.connect(self.on_run)
        row.addWidget(self.btn_run)

        self.btn_cancel = QPushButton("取消", self)
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.setMinimumHeight(28)
        theme.style_push_button(self.btn_cancel, "secondary")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.on_cancel)
        row.addWidget(self.btn_cancel)
        layout.addLayout(row)

        self.status = QLabel(
            "在二维结果页上点击「分析当前二维结果」：沿所选方向积分，"
            "结果作为新的一维页出现在左侧页面树里。",
            self,
        )
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {theme.TEXT_3}; font-size: 11px;")
        layout.addWidget(self.status)

    # ------------------------------------------------------------------ 操作
    def selected_axis(self) -> str:
        data = self.combo_axis.currentData()
        return str(data or AXIS_X)

    def on_run(self):
        """抓快照并提交任务。

        ``capture_analysis_input`` 在预览中、计算未完成、非二维页或带擦除区域时
        抛出 :class:`AnalysisUnavailable`，消息已经是可以直接展示的原因。
        ``submit_analysis`` 返回 ``None`` 表示忙碌（宿主已提示），这里不再重复提示。
        """
        try:
            snapshot = self.host.capture_analysis_input()
        except AnalysisUnavailable as exc:
            self.status.setText(str(exc))
            return
        axis = self.selected_axis()
        handle = self.host.submit_analysis(
            snapshot,
            run_integral,
            title=f"{snapshot.page_title} 积分",
            params={"axis": axis},
        )
        if handle is None:
            self.status.setText("上一个任务还没结束或队列已满，稍后再试。")
            return
        self._handle = handle
        self._set_running(True)
        self.status.setText(
            f"已提交：{axis_label(axis)}，快照 {snapshot.shape[0]}×{snapshot.shape[1]}。"
        )

    def on_cancel(self):
        if self._handle is None:
            return
        self.host.cancel_analysis(self._handle)
        self.status.setText("已请求取消；任务会在下一个检查点退出。")

    def _set_running(self, running: bool):
        self.btn_run.setEnabled(not running)
        self.btn_cancel.setEnabled(bool(running))

    # ------------------------------------------------------------------ 宿主回调
    def on_task_finished(self, message: str):
        """宿主在结果页建好（或任务失败/取消）之后调一次，恢复按钮状态。"""
        self._handle = None
        self._set_running(False)
        self.status.setText(message)


__all__ = ["IntegralDemoPanel"]
