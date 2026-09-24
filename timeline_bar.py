# -*- coding: utf-8 -*-
"""画布下方底条 —— 左侧时间轴（按数据显隐）+ 右侧画布视图控件（常驻右对齐）。

底条自己创建并持有这两组控件（原「图像控制」页已删除）：

- 左侧时间轴：滑条 + 帧号输入框 + 提示 + 总帧数；
- 右侧视图控件：显示坐标 / E轴翻转 开关与 Z 轴旋转输入框，常驻右对齐。

主窗口的所有信号、快捷键和按结果页保存的控件状态都指向这里的同名属性
（``slider_time`` / ``input_time`` / ``time_hint`` / ``switch_axes`` /
``switch_flip`` / ``edit_rotation``），:meth:`export_state` /
:meth:`restore_state` 保持与迁移前完全相同的字典结构。

没有时间轴的数据只收放左侧时间轴分组，右侧视图控件与时间轴无关，始终保留。
"""

from PyQt5.QtCore import Qt, QSignalBlocker
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QSpinBox, QVBoxLayout, QWidget
from siui.components.widgets import SiLabel

import theme
from control_layout_utils import animate_widget_visibility, set_widget_visibility_instant
from ui_controls import RotationSpinBox, SyncedSlider, SyncedSwitch


class ContinuousFrameSlider(SyncedSlider):
    """A discrete frame slider whose thumb still follows the mouse smoothly."""

    def _onValueChanged(self, value):
        if self._is_dragging:
            self._updateToolTip(flash=False)
            return
        super()._onValueChanged(value)

    def _onRangeChanged(self, minimum, maximum):
        if maximum == minimum:
            self.progress_ani.stop()
            self.setProperty(self.Property.TrackProgress, 0.0)
            self.progress_ani.setCurrentValue(0.0)
            self.progress_ani.setEndValue(0.0)
            return
        super()._onRangeChanged(minimum, maximum)

    def _setValueToMousePos(self, pos):
        thumb_width = self.style_data.thumb_width
        if self.orientation() == Qt.Horizontal:
            available = max(self.width() - thumb_width, 1)
            progress = min(1.0, max((pos.x() - thumb_width / 2) / available, 0.0))
        else:
            available = max(self.height() - thumb_width, 1)
            progress = min(1.0, max(1.0 - (pos.y() - thumb_width / 2) / available, 0.0))

        region = self.maximum() - self.minimum()
        # SyncedSlider.setValue 会把滑块吸附到整数帧进度；随后立即用精确的
        # 鼠标进度覆盖，保证 2~3 帧的数据集也能在整个轨道上拖动。
        self.setValue(int(self.minimum() + region * progress + 0.5))

        self.progress_ani.stop()
        self.setProperty(self.Property.TrackProgress, progress)
        self.progress_ani.setCurrentValue(progress)
        self.progress_ani.setEndValue(progress)
        self.update()


class TimelineBar(QFrame):
    """画布正下方的常驻底条：时间轴分组 + 右对齐的画布视图控件。"""

    HEIGHT = 56
    # 滑条长度下限：窄窗口下仍能拖动。滑条没有长度上限——分组吸满富余宽度，
    # 滑条一直顶到右侧视图控件前，只留一处与行内一致的间隙（layout spacing）。
    SLIDER_MIN_WIDTH = 160
    # 分组与空隙的弹性比例：任何正数都能让富余宽度全归分组，
    # 空隙只在分组收起后才起作用（那时它是唯一的 Expanding 项）。
    TIMELINE_STRETCH = 1
    SLIDER_STRETCH = 1
    # 时间轴控件尺寸
    TIME_VALUE_BOX_WIDTH = 72
    TIME_VALUE_BOX_HEIGHT = 30
    ROTATION_BOX_WIDTH = 108
    ROTATION_BOX_HEIGHT = 32
    SWITCH_WIDTH = 40
    SWITCH_HEIGHT = 20
    LABEL_CONTROL_SPACING = 6
    VIEW_CONTROL_SPACING = 16

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

        # —— 左：时间轴分组（整体显隐）——
        # 主色竖条 + 标题/提示两行，与结果页头部的设计语言一致。
        self.timeline_group = QWidget(self)
        group_layout = QHBoxLayout(self.timeline_group)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(12)

        self.accent = QFrame(self.timeline_group)
        self.accent.setFixedSize(4, 26)
        self.accent.setStyleSheet(
            f"background-color: {theme.ACCENT}; border: none; border-radius: 2px;"
        )
        group_layout.addWidget(self.accent, 0, Qt.AlignVCenter)

        self._title_block = QVBoxLayout()
        self._title_block.setContentsMargins(0, 0, 0, 0)
        self._title_block.setSpacing(2)
        self.title_label = QLabel("时间轴", self.timeline_group)
        self.title_label.setStyleSheet(theme.card_title_qss())
        # 文字标签固定宽度：分组里的富余宽度只留给滑条，标签不被拉长。
        self.title_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._title_block.addWidget(self.title_label)
        group_layout.addLayout(self._title_block)

        group_layout.addSpacing(self.LABEL_CONTROL_SPACING)
        self._group_layout = group_layout

        layout.addWidget(self.timeline_group, self.TIMELINE_STRETCH)
        # 空隙自身不带拉伸因子：分组可见时富余宽度全归分组（加长滑条）；
        # 分组收起后这个 Expanding 空隙才撑住整行，右侧控件不会左移。
        layout.addStretch()

        # —— 右：画布视图控件（显示坐标 / E轴翻转 / Z轴旋转），常驻右对齐 ——
        self.view_controls = QWidget(self)
        self.view_controls.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._view_layout = QHBoxLayout(self.view_controls)
        self._view_layout.setContentsMargins(0, 0, 0, 0)
        self._view_layout.setSpacing(0)
        layout.addWidget(self.view_controls, 0, Qt.AlignVCenter)

        self._timeline_visible_target = True
        self._create_timeline_controls()
        self._create_view_controls()

    # ------------------------------------------------------------------
    # 控件创建
    # ------------------------------------------------------------------
    def _create_timeline_controls(self):
        self.time_hint = QLabel("当前帧 · ← → 逐帧切换", self.timeline_group)
        self.time_hint.setStyleSheet(theme.field_label_qss())
        self.time_hint.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._title_block.addWidget(self.time_hint)

        self.slider_time = ContinuousFrameSlider(self.timeline_group)
        self.slider_time.setFixedHeight(32)
        self.slider_time.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.slider_time.setMinimumWidth(self.SLIDER_MIN_WIDTH)
        theme.style_accent_slider(self.slider_time)
        self._group_layout.addWidget(self.slider_time, self.SLIDER_STRETCH)

        self.input_time = QSpinBox(self.timeline_group)
        self.input_time.setFixedSize(self.TIME_VALUE_BOX_WIDTH, self.TIME_VALUE_BOX_HEIGHT)
        self.input_time.setRange(0, 99)
        self.input_time.setSingleStep(1)
        self.input_time.setKeyboardTracking(False)
        self.input_time.setAlignment(Qt.AlignCenter)
        self.input_time.setFocusPolicy(Qt.StrongFocus)
        self.input_time.setToolTip("当前帧")
        self.input_time.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.input_time.setStyleSheet(theme.value_input_qss())
        self._group_layout.addWidget(self.input_time, 0, Qt.AlignVCenter)

        self.total_label = QLabel(self.timeline_group)
        self.total_label.setToolTip("总帧数")
        self.total_label.setStyleSheet(
            f"color: {theme.TEXT_3}; background: transparent;"
            f"font-size: 12px; font-family: {theme.FONT_MONO};"
        )
        self.total_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._group_layout.addWidget(self.total_label, 0, Qt.AlignVCenter)
        self.total_label.setText("–")

        # 滑条与帧号输入框双向同步；范围变化同样跟随。
        self.slider_time.valueChanged.connect(self.input_time.setValue)
        self.slider_time.rangeChanged.connect(self.input_time.setRange)
        self.input_time.valueChanged.connect(self.slider_time.setValue)

    def _create_view_controls(self):
        self.switch_axes = SyncedSwitch(self.view_controls)
        self.switch_axes.setFixedSize(self.SWITCH_WIDTH, self.SWITCH_HEIGHT)
        self.switch_axes.setToolTip("显示物理坐标轴刻度")

        self.switch_flip = SyncedSwitch(self.view_controls)
        self.switch_flip.setFixedSize(self.SWITCH_WIDTH, self.SWITCH_HEIGHT)
        self.switch_flip.setToolTip("翻转 E 轴（能量）显示方向")

        self.edit_rotation = RotationSpinBox(self.view_controls)
        self.edit_rotation.setAccessibleName("Z轴旋转角度")
        self.edit_rotation.setMinimum(-360.0)
        self.edit_rotation.setMaximum(360.0)
        self.edit_rotation.setSingleStep(1.0)
        self.edit_rotation.setValue(0.0)
        self.edit_rotation.setFixedSize(self.ROTATION_BOX_WIDTH, self.ROTATION_BOX_HEIGHT)
        self.edit_rotation.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.edit_rotation.setToolTip("在 Kx-Ky 平面内旋转 3D 数据体（-360° ~ 360°）")

        for switch, text in ((self.switch_axes, "显示坐标"), (self.switch_flip, "E轴翻转")):
            label = SiLabel(text, self.view_controls)
            label.setStyleSheet(theme.field_label_qss())
            self._view_layout.addWidget(label, 0, Qt.AlignVCenter)
            self._view_layout.addSpacing(self.LABEL_CONTROL_SPACING)
            self._view_layout.addWidget(switch, 0, Qt.AlignVCenter)
            self._view_layout.addSpacing(self.VIEW_CONTROL_SPACING)

        rotation_label = QLabel("Z 轴旋转 / °", self.view_controls)
        rotation_label.setStyleSheet(theme.field_label_qss())
        self._view_layout.addWidget(rotation_label, 0, Qt.AlignVCenter)
        self._view_layout.addSpacing(self.LABEL_CONTROL_SPACING)
        self._view_layout.addWidget(self.edit_rotation, 0, Qt.AlignVCenter)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def get_rotation_angle(self):
        # Read the visible text so live typing and delayed exact refresh share
        # the same angle snapshot, including before editingFinished.
        text = self.edit_rotation.text().strip()
        try:
            return float(text)
        except (TypeError, ValueError):
            return float(self.edit_rotation.value())

    def set_rotation_angle(self, angle):
        self.edit_rotation.setValue(float(angle))

    def export_state(self):
        return {
            "slider_time": {
                "minimum": int(self.slider_time.minimum()),
                "maximum": int(self.slider_time.maximum()),
                "value": int(self.slider_time.value()),
            },
            "switch_axes": bool(self.switch_axes.isChecked()),
            "switch_flip": bool(self.switch_flip.isChecked()),
            "rotation_angle": self.get_rotation_angle(),
        }

    def restore_state(self, state, *, block_signals=True):
        state = state or {}
        blockers = []
        if block_signals:
            blockers = [
                QSignalBlocker(self.slider_time),
                QSignalBlocker(self.input_time),
                QSignalBlocker(self.switch_axes),
                QSignalBlocker(self.switch_flip),
            ]

        try:
            slider_state = state.get("slider_time") or {}
            minimum = slider_state.get("minimum")
            maximum = slider_state.get("maximum")
            if minimum is not None and maximum is not None:
                self.slider_time.setRange(int(minimum), int(maximum))
                self.input_time.setRange(int(minimum), int(maximum))
            if "value" in slider_state:
                value = int(slider_state["value"])
                value = max(int(self.slider_time.minimum()),
                            min(int(self.slider_time.maximum()), value))
                self.slider_time.setValue(value)
                self.input_time.setValue(value)

            if "switch_axes" in state:
                self.switch_axes.setChecked(bool(state["switch_axes"]))
            rotation_angle = state.get("rotation_angle")
            if rotation_angle is not None:
                self.set_rotation_angle(str(rotation_angle))
            else:
                self.set_rotation_angle(0.0)

            if "switch_flip" in state:
                self.switch_flip.setChecked(bool(state["switch_flip"]))
        finally:
            del blockers

    def set_total_frames(self, count):
        """由加载流程用真实时间维度设置总帧数（滑条范围有 0..1 兜底，不可信）。"""
        if self.total_label is not None:
            self.total_label.setText(f"/ {int(count)} 帧" if count else "–")

    def set_timeline_visible(self, visible, *, animate=True):
        """按数据是否含时间轴平滑收放左侧时间轴分组（底条本身常驻可见）。"""
        visible = bool(visible)
        # 构造时时间轴分组默认可见；目标状态不变则不重复播放动画。
        if self._timeline_visible_target == visible:
            return
        self._timeline_visible_target = visible
        if animate:
            animate_widget_visibility(self.timeline_group, visible)
        else:
            set_widget_visibility_instant(self.timeline_group, visible)
