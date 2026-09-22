# -*- coding: utf-8 -*-
"""时间轴横条 —— 画布正下方的常驻播放条。

只负责布局与外观：真正的控件（``slider_time`` / ``input_time`` / ``time_hint``）
仍由 ``ImageControlPage`` 创建并持有，所有业务信号（``on_time_slider_changed``、
``flush_time_slider_refresh``、←/→ 逐帧快捷键等）继续指向 page_image 上的
同名属性，横条通过 :meth:`attach_controls` 把它们收进自己的布局。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout

import theme
from control_layout_utils import animate_widget_visibility, set_widget_visibility_instant


class TimelineBar(QFrame):
    """画布下方的固定时间轴横条（标题 + 状态提示 + 滑条 + 帧号 + 总帧数）。"""

    HEIGHT = 56

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("timeline_bar")
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            f"QFrame#timeline_bar {{"
            f"background-color: {theme.BG_2};"
            f"border: 1px solid {theme.BORDER};"
            f"border-radius: {theme.R_M}px;"
            f"}}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(12)

        # 左侧主色竖条 + 标题/提示两行，与结果页头部的设计语言一致。
        self.accent = QFrame(self)
        self.accent.setFixedSize(4, 26)
        self.accent.setStyleSheet(
            f"background-color: {theme.ACCENT}; border: none; border-radius: 2px;"
        )
        layout.addWidget(self.accent, 0, Qt.AlignVCenter)

        self._title_block = QVBoxLayout()
        self._title_block.setContentsMargins(0, 0, 0, 0)
        self._title_block.setSpacing(2)
        self.title_label = QLabel("时间轴", self)
        self.title_label.setStyleSheet(theme.card_title_qss())
        self._title_block.addWidget(self.title_label)
        layout.addLayout(self._title_block)

        layout.addSpacing(6)
        self._layout = layout

        self.slider = None
        self.spin_box = None
        self.hint_label = None
        self.total_label = None

    def attach_controls(self, slider, spin_box, hint_label):
        """把 page_image 创建的时间轴控件收进横条布局（只调用一次）。"""
        self.slider = slider
        self.spin_box = spin_box
        self.hint_label = hint_label

        hint_label.setParent(self)
        hint_label.setStyleSheet(
            f"color: {theme.TEXT_3}; background: transparent; font-size: 11px;"
        )
        self._title_block.addWidget(hint_label)

        slider.setParent(self)
        self._layout.addWidget(slider, 1)

        spin_box.setParent(self)
        self._layout.addWidget(spin_box, 0, Qt.AlignVCenter)

        self.total_label = QLabel(self)
        self.total_label.setToolTip("总帧数")
        self.total_label.setStyleSheet(
            f"color: {theme.TEXT_3}; background: transparent;"
            f"font-size: 12px; font-family: {theme.FONT_MONO};"
        )
        self._layout.addWidget(self.total_label, 0, Qt.AlignVCenter)
        self.total_label.setText("–")

    def set_total_frames(self, count):
        """由加载流程用真实时间维度设置总帧数（滑条范围有 0..1 兜底，不可信）。"""
        if self.total_label is not None:
            self.total_label.setText(f"/ {int(count)} 帧" if count else "–")

    def set_bar_visible(self, visible, *, animate=True):
        """数据含时间轴时平滑展开横条，否则平滑收起（布局随高度动画联动）。"""
        visible = bool(visible)
        # 构造时横条默认可见；目标状态不变则不重复播放动画。
        if getattr(self, "_bar_visible_target", True) == visible:
            return
        self._bar_visible_target = visible
        settle = lambda: self.setFixedHeight(self.HEIGHT)
        if animate:
            animate_widget_visibility(
                self,
                visible,
                target_height=self.HEIGHT,
                settle_show=settle,
            )
        else:
            set_widget_visibility_instant(self, visible, settle_show=settle)
