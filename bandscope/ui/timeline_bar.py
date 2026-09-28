# -*- coding: utf-8 -*-
"""画布下方底条 —— 左侧积分位置 / 时间轴 + 右侧画布视图控件。

底条自己创建并持有这些控件（原「图像控制」页已删除）：

- 左起第一组：当前积分轴向的位置滑条 + 物理值输入框（``slider_axis`` /
  ``input_axis``）。只有需要位置控制的页面才出现；数据没有时间轴坐标时它
  直接占用原时间轴的位置，左侧不留空位。
- 左起第二组：时间轴（滑条 + 帧号输入框 + 提示 + 总帧数），按数据显隐；
  与位置滑条同排时两者共享富余宽度。
- 右侧：显示坐标 / E轴翻转 开关与 Z 轴旋转输入框，常驻右对齐。宽度不够时
  整组下移到第二行，底条高度同步增加。

主窗口的所有信号、快捷键和按结果页保存的控件状态都指向这里的同名属性
（``slider_time`` / ``input_time`` / ``time_hint`` / ``switch_axes`` /
``switch_flip`` / ``edit_rotation``），:meth:`export_state` /
:meth:`restore_state` 保持与迁移前完全相同的字典结构。
"""

from PyQt5.QtCore import QSize, Qt, QSignalBlocker, pyqtSignal
from PyQt5.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from siui.components.widgets import SiLabel

import bandscope.ui.theme as theme
from bandscope.ui.control_layout_utils import (
    animate_widget_visibility,
    animate_widget_width,
    set_widget_visibility_instant,
)
from bandscope.ui.ui_controls import RotationSpinBox, SyncedSlider, SyncedSwitch


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
    """画布正下方的常驻底条：积分位置组 + 时间轴组 + 右对齐的画布视图控件。"""

    #: 底条单行高度；出现第二行（视图控件下移）时再加 ``SECOND_ROW_HEIGHT``。
    HEIGHT = 56
    SECOND_ROW_HEIGHT = 44
    # 滑条长度下限：窄窗口下仍能拖动。滑条没有长度上限——分组吸满富余宽度，
    # 滑条一直顶到相邻分组前，只留一处与行内一致的间隙（layout spacing）。
    SLIDER_MIN_WIDTH = 160
    #: 两条轨道同时显示时的分享比例：富余宽度在位置组与时间轴组之间均分。
    TIMELINE_STRETCH = 1
    SLIDER_STRETCH = 1
    AXIS_STRETCH = 1
    #: 位置轨道最低可拖宽度（px）。
    AXIS_SLIDER_MIN_WIDTH = 120
    # 时间轴控件尺寸
    TIME_VALUE_BOX_WIDTH = 72
    TIME_VALUE_BOX_HEIGHT = 30
    AXIS_VALUE_BOX_WIDTH = 92
    AXIS_VALUE_BOX_HEIGHT = 30
    ROTATION_BOX_WIDTH = 108
    ROTATION_BOX_HEIGHT = 32
    SWITCH_WIDTH = 40
    SWITCH_HEIGHT = 20
    LABEL_CONTROL_SPACING = 6
    VIEW_CONTROL_SPACING = 16
    ROW_MARGINS = (14, 4, 14, 4)

    #: 底条每次重新排版（呼出/收起/换行/恢复）后发出，供宿主合并画布尺寸刷新。
    layoutChanged = pyqtSignal()

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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(*self.ROW_MARGINS)
        outer.setSpacing(0)
        # 默认约束会把单行排布写死成控件的最小宽度，底条就永远缩不到换行阈值
        # 以下。交给 minimumSizeHint() 表达可用最小宽度，换行由 resizeEvent 决定。
        outer.setSizeConstraint(QLayout.SetNoConstraint)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self._primary_layout = layout
        outer.addLayout(layout)

        self.secondary_row = QWidget(self)
        self.secondary_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._secondary_layout = QHBoxLayout(self.secondary_row)
        self._secondary_layout.setContentsMargins(0, 0, 0, 0)
        self._secondary_layout.setSpacing(12)
        self.secondary_row.setVisible(False)
        outer.addWidget(self.secondary_row)

        # —— 左：积分位置组（整体收放）——
        self.axis_group = QWidget(self)
        self._axis_layout = QHBoxLayout(self.axis_group)
        self._axis_layout.setContentsMargins(0, 0, 0, 0)
        self._axis_layout.setSpacing(12)

        # —— 中：时间轴分组（整体显隐）——
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

        layout.addWidget(self.axis_group, self.AXIS_STRETCH)
        layout.addWidget(self.timeline_group, self.TIMELINE_STRETCH)
        # 空隙自身不带拉伸因子：分组可见时富余宽度全归分组（加长滑条）；
        # 分组收起后这个 Expanding 空隙才撑住整行，右侧控件不会左移。
        layout.addStretch()

        # —— 右：画布视图控件（显示坐标 / E轴翻转 / Z轴旋转），默认右对齐 ——
        self.view_controls = QWidget(self)
        self.view_controls.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._view_layout = QHBoxLayout(self.view_controls)
        self._view_layout.setContentsMargins(0, 0, 0, 0)
        self._view_layout.setSpacing(0)
        layout.addWidget(self.view_controls, 0, Qt.AlignVCenter)

        self._timeline_visible_target = True
        self._axis_visible_target = False
        self._wrapped = False
        self._create_axis_controls()
        self._create_timeline_controls()
        self._create_view_controls()
        # 位置组默认隐藏：只有需要位置控制的页面才呼出。
        set_widget_visibility_instant(self.axis_group, False)

    # ------------------------------------------------------------------
    # 控件创建
    # ------------------------------------------------------------------
    def _create_axis_controls(self):
        self.axis_accent = QFrame(self.axis_group)
        self.axis_accent.setFixedSize(4, 26)
        self.axis_accent.setStyleSheet(
            f"background-color: {theme.ACCENT}; border: none; border-radius: 2px;"
        )
        self._axis_layout.addWidget(self.axis_accent, 0, Qt.AlignVCenter)

        self._axis_title_block = QVBoxLayout()
        self._axis_title_block.setContentsMargins(0, 0, 0, 0)
        self._axis_title_block.setSpacing(2)
        self.axis_title_label = QLabel("积分位置", self.axis_group)
        self.axis_title_label.setStyleSheet(theme.card_title_qss())
        self.axis_title_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._axis_title_block.addWidget(self.axis_title_label)
        self._axis_layout.addLayout(self._axis_title_block)
        self._axis_layout.addSpacing(self.LABEL_CONTROL_SPACING)

        self.slider_axis = ContinuousFrameSlider(self.axis_group)
        self.slider_axis.setFixedHeight(32)
        self.slider_axis.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.slider_axis.setMinimumWidth(self.AXIS_SLIDER_MIN_WIDTH)
        theme.style_accent_slider(self.slider_axis)
        self._axis_layout.addWidget(self.slider_axis, self.SLIDER_STRETCH)

        self.input_axis = QDoubleSpinBox(self.axis_group)
        self.input_axis.setFixedSize(self.AXIS_VALUE_BOX_WIDTH, self.AXIS_VALUE_BOX_HEIGHT)
        self.input_axis.setKeyboardTracking(False)
        self.input_axis.setAlignment(Qt.AlignCenter)
        self.input_axis.setFocusPolicy(Qt.StrongFocus)
        self.input_axis.setToolTip("当前积分区间中心（物理坐标，可直接输入）")
        self.input_axis.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.input_axis.setStyleSheet(theme.value_input_qss())
        self._axis_layout.addWidget(self.input_axis, 0, Qt.AlignVCenter)

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
    # 布局：位置组呼出与视图控件换行
    # ------------------------------------------------------------------
    def _available_row_width(self):
        # 用外层布局自己设的边距：QFrame.contentsMargins() 在控件未显示时
        # 还没从布局同步过来，会算错换行阈值。
        left, _top, right, _bottom = self.ROW_MARGINS
        return max(int(self.width()) - left - right, 0)

    def _axis_group_min_width(self):
        return int(self.axis_group.minimumSizeHint().width())

    def _timeline_group_min_width(self):
        return int(self.timeline_group.minimumSizeHint().width())

    def _view_controls_width(self):
        return int(self.view_controls.minimumSizeHint().width())

    def _groups_min_width(self):
        """第一行（位置组 + 时间轴组）在单行里的最小宽度。"""
        width = 0
        if self._axis_visible_target:
            width += self._axis_group_min_width()
        if self._timeline_visible_target:
            width += self._timeline_group_min_width()
        if self._axis_visible_target and self._timeline_visible_target:
            width += self._primary_layout.spacing()
        return width

    def minimumSizeHint(self):
        # 不把「视图控件和两个分组同排」写进最小宽度：那样控件永远缩不到
        # 换行阈值以下，_should_wrap 就永远不会成立。换行后第一行只剩两个
        # 分组，第二行只需要视图控件，最小宽度取两者的较大者。
        left, _top, right, _bottom = self.ROW_MARGINS
        width = max(self._groups_min_width(), self._view_controls_width())
        return QSize(width + left + right, self.height())

    def _should_wrap(self):
        """换行阈值按实际控件最小宽度计算，不用固定像素猜。"""
        available = self._available_row_width()
        if available <= 0:
            return False
        needed = self._groups_min_width() + self._view_controls_width()
        return available < needed

    def _apply_wrap(self, wrapped):
        if self._wrapped == wrapped:
            return
        self._wrapped = wrapped
        self._primary_layout.removeWidget(self.view_controls)

        if wrapped:
            self._secondary_layout.addWidget(self.view_controls, 0, Qt.AlignVCenter)
            self.secondary_row.setVisible(True)
            self.setFixedHeight(self.HEIGHT + self.SECOND_ROW_HEIGHT)
        else:
            self._primary_layout.addWidget(self.view_controls, 0, Qt.AlignVCenter)
            self.secondary_row.setVisible(False)
            self.setFixedHeight(self.HEIGHT)

        self.updateGeometry()
        self.layoutChanged.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_wrap(self._should_wrap())

    def _axis_reveal_width(self, *, alone):
        """呼出动画的目标宽度：与时间轴同排时取可用宽度的一半。"""
        available = self._available_row_width()
        if alone:
            available -= self._view_controls_width()
        else:
            available -= self._view_controls_width() + self._timeline_group_min_width()
        available = max(available // (1 if alone else 2), self._axis_group_min_width())
        return available

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
        self._relayout_for_visibility_change(animate=animate)
        if animate:
            animate_widget_visibility(
                self.timeline_group,
                visible,
                on_update=self._on_group_animation_frame,
                on_settled=self._on_group_animation_frame,
            )
        else:
            set_widget_visibility_instant(self.timeline_group, visible)

    def set_position_visible(self, visible, *, animate=True):
        """按当前页面呼出/收起积分位置组；同一目标状态不重复播放动画。"""
        visible = bool(visible)
        if self._axis_visible_target == visible:
            return
        self._axis_visible_target = visible
        self._relayout_for_visibility_change(animate=animate)
        if animate:
            animate_widget_width(
                self.axis_group,
                visible,
                target_width=self._axis_reveal_width(alone=not self._timeline_visible_target),
                on_update=self._on_group_animation_frame,
                on_settled=self._on_group_animation_frame,
            )
        else:
            set_widget_visibility_instant(self.axis_group, visible)

    def _relayout_for_visibility_change(self, *, animate):
        # 呼出/收起过程中行宽需求在变，换行判定要跟着走；位置组出现时
        # 时间轴不再独占整行，反之亦然。
        self._apply_wrap(self._should_wrap())

    def _on_group_animation_frame(self):
        self._apply_wrap(self._should_wrap())
        self.layoutChanged.emit()

    def set_position_label(self, text):
        self.axis_title_label.setText(str(text))
        self.input_axis.setToolTip(f"{text}：当前积分区间中心（物理坐标，可直接输入）")

    def set_position_enabled(self, enabled):
        enabled = bool(enabled)
        self.slider_axis.setEnabled(enabled)
        self.input_axis.setEnabled(enabled)
