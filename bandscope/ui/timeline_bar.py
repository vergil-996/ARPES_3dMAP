# -*- coding: utf-8 -*-
"""画布下方底条：空间区间与扫描区间。

空间端点控制切片位置与厚度；扫描端点合拢时选择单帧，分离后选择当前页的
扫描积分区间。内部沿用 ``time`` 接口兼容页面、配方、插件与导出，显示名称、
单位和物理量由源级轴分配决定。视图按钮和旋转旋钮由 ``CanvasToolOverlay`` 持有。
"""

from PyQt5.QtCore import QObject, QSize, Qt, QSignalBlocker, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QFrame,
    QHBoxLayout,
    QLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import bandscope.ui.theme as theme
from bandscope.ui.control_layout_utils import (
    animate_widget_visibility,
    animate_widget_width,
    set_widget_visibility_instant,
)
from bandscope.core.axis_interval import nearest_index
from bandscope.ui.axis_interval_bar import AxisIntervalControl
from bandscope.ui.ui_controls import RotationSpinBox, SyncedSwitch


class FrameSliderAdapter(QObject):
    """QSlider 风格的当前帧接口，直接投影到底部时间区间。"""

    valueChanged = pyqtSignal(int)
    rangeChanged = pyqtSignal(int, int)
    sliderReleased = pyqtSignal()

    def __init__(self, bar):
        super().__init__(bar)
        self._bar = bar
        self._minimum = 0
        self._maximum = 0
        self._value = 0
        self._enabled = True

    def minimum(self):
        return 0 if self._bar.time_space is not None else self._minimum

    def maximum(self):
        space = self._bar.time_space
        if space is not None:
            return max(int(space.sample_count) - 1, 0)
        return self._maximum

    def value(self):
        bar = self._bar
        if bar.time_space is not None and bar.time_interval is not None:
            return nearest_index(bar.time_space.coords, bar.time_interval.center)
        return self._value

    def setRange(self, minimum, maximum):
        self._minimum = int(minimum)
        self._maximum = max(int(minimum), int(maximum))
        self._value = min(max(self._value, self._minimum), self._maximum)
        self.rangeChanged.emit(self._minimum, self._maximum)

    def setValue(self, value):
        bar = self._bar
        if bar.time_space is None or bar.time_interval is None:
            self._value = min(max(int(value), self._minimum), self._maximum)
            self.valueChanged.emit(self._value)
            return
        index = int(min(max(int(value), 0), self.maximum()))
        was_single = bar.time_interval.locked and bar.time_interval.length == 0.0
        changed = index != self.value() or not was_single
        coords = bar.time_space.coords
        physical = float(coords[index])
        bar.time_interval.set_length(0.0)
        bar.time_interval.set_center(physical)
        bar.time_interval.set_locked(True)
        bar.time_interval_control.sync(bar.time_space, bar.time_interval, self._enabled)
        if changed:
            self.valueChanged.emit(index)

    def setEnabled(self, enabled):
        self._enabled = bool(enabled)
        self._bar.time_interval_control.setEnabled(self._enabled)

    def isEnabled(self):
        return self._enabled

    def setToolTipConvertionFunc(self, _func):
        pass


class TimelineBar(QFrame):
    """位置组与时间组共享底栏宽度，只有空间不足时才分成两行。"""

    HEIGHT = 64
    SECOND_ROW_HEIGHT = 40
    TIMELINE_STRETCH = 1
    SLIDER_STRETCH = 1
    AXIS_STRETCH = 1
    AXIS_SLIDER_MIN_WIDTH = 120
    AXIS_VALUE_BOX_WIDTH = 92
    AXIS_VALUE_BOX_HEIGHT = 30
    ROW_MARGINS = (14, 6, 14, 6)

    layoutChanged = pyqtSignal()

    def __init__(self, parent=None, *, view_controls=None):
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

        self.axis_group = QWidget(self)
        self._axis_layout = QHBoxLayout(self.axis_group)
        self._axis_layout.setContentsMargins(0, 0, 0, 0)
        self._axis_layout.setSpacing(12)

        self.timeline_group = QWidget(self)
        group_layout = QHBoxLayout(self.timeline_group)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(12)
        self._group_layout = group_layout
        self.time_space = None
        self.time_interval = None

        layout.addWidget(self.axis_group, self.AXIS_STRETCH)
        layout.addWidget(self.timeline_group, self.TIMELINE_STRETCH)

        self._timeline_visible_target = True
        self._axis_visible_target = False
        self._wrapped = False
        self._view_controls_owner = view_controls
        self._create_axis_controls()
        self._create_timeline_controls()
        self._bind_view_controls(view_controls)
        set_widget_visibility_instant(self.axis_group, False)

    def _create_axis_controls(self):
        self.interval_control = AxisIntervalControl(self.axis_group)
        self._axis_layout.addWidget(self.interval_control, self.SLIDER_STRETCH)
        self.axis_title_label = self.interval_control.title_label

    def _create_timeline_controls(self):
        self.time_interval_control = AxisIntervalControl(self.timeline_group)
        self._group_layout.addWidget(self.time_interval_control, self.SLIDER_STRETCH)
        self.slider_time = FrameSliderAdapter(self)
        self.time_hint = self.time_interval_control.subtitle_label
        self.total_label = None

    def _bind_view_controls(self, view_controls):
        if view_controls is not None:
            self.switch_axes = view_controls.switch_axes
            self.switch_flip = view_controls.switch_flip
            self.edit_rotation = view_controls.edit_rotation
            return
        self.switch_axes = SyncedSwitch(self)
        self.switch_axes.setFixedSize(30, 18)
        self.switch_axes.setToolTip("显示物理坐标轴刻度")
        self.switch_flip = SyncedSwitch(self)
        self.switch_flip.setFixedSize(30, 18)
        self.switch_flip.setToolTip("翻转图像显示方向")
        self.edit_rotation = RotationSpinBox(self)
        self.edit_rotation.setAccessibleName("Z 轴旋转角度")
        self.edit_rotation.setRange(-360.0, 360.0)
        self.edit_rotation.setDecimals(2)
        self.edit_rotation.setSingleStep(1.0)
        self.edit_rotation.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.edit_rotation.setValue(0.0)
        self.edit_rotation.setFixedSize(88, 30)
        self.edit_rotation.setToolTip("在 Kx–Ky 平面内旋转（−360° 到 +360°）")

    def sync_time_interval(self, space, interval, enabled=True):
        self.time_space = space
        self.time_interval = interval
        self.time_interval_control.sync(space, interval, enabled)

    def _available_row_width(self):
        left, _top, right, _bottom = self.ROW_MARGINS
        return max(int(self.width()) - left - right, 0)

    def _axis_group_min_width(self):
        return int(self.axis_group.minimumSizeHint().width())

    def _timeline_group_min_width(self):
        return int(self.timeline_group.minimumSizeHint().width())

    def _visible_group_min_widths(self):
        widths = []
        if self._axis_visible_target:
            widths.append(self._axis_group_min_width())
        if self._timeline_visible_target:
            widths.append(self._timeline_group_min_width())
        return widths

    def _groups_min_width(self):
        widths = self._visible_group_min_widths()
        if not widths:
            return 0
        width = sum(widths)
        if len(widths) == 2:
            width += self._primary_layout.spacing()
        return width

    def minimumSizeHint(self):
        widths = self._visible_group_min_widths()
        left, _top, right, _bottom = self.ROW_MARGINS
        return QSize((max(widths) if widths else 0) + left + right, self.height())

    def _should_wrap(self):
        if not (self._axis_visible_target and self._timeline_visible_target):
            return False
        available = self._available_row_width()
        return available > 0 and available < self._groups_min_width()

    def _apply_wrap(self, wrapped):
        wrapped = bool(wrapped and self._axis_visible_target and self._timeline_visible_target)
        if self._wrapped == wrapped:
            return
        self._wrapped = wrapped
        self._primary_layout.removeWidget(self.timeline_group)
        self._secondary_layout.removeWidget(self.timeline_group)
        if wrapped:
            self._secondary_layout.addWidget(self.timeline_group, self.TIMELINE_STRETCH)
        else:
            self._primary_layout.addWidget(self.timeline_group, self.TIMELINE_STRETCH)
        self.secondary_row.setVisible(wrapped)
        self.setFixedHeight(self.HEIGHT + (self.SECOND_ROW_HEIGHT if wrapped else 0))
        self.updateGeometry()
        self.layoutChanged.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_wrap(self._should_wrap())

    def _axis_reveal_width(self, *, alone):
        available = self._available_row_width()
        if not alone:
            available = available // 2
        return max(available, self._axis_group_min_width())

    def get_rotation_angle(self):
        if self._view_controls_owner is not None:
            return float(self._view_controls_owner.rotation_angle())
        try:
            return float(self.edit_rotation.text().strip())
        except (TypeError, ValueError):
            return float(self.edit_rotation.value())

    def set_rotation_angle(self, angle):
        if self._view_controls_owner is not None:
            self._view_controls_owner.set_rotation_angle(angle)
        else:
            self.edit_rotation.setValue(float(angle))

    def export_state(self):
        return {
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
                QSignalBlocker(self.switch_axes),
                QSignalBlocker(self.switch_flip),
                QSignalBlocker(self.edit_rotation),
            ]

        try:
            slider_state = state.get("slider_time") or {}
            minimum = slider_state.get("minimum")
            maximum = slider_state.get("maximum")
            if minimum is not None and maximum is not None and self.time_space is None:
                self.slider_time.setRange(int(minimum), int(maximum))
            if "value" in slider_state:
                value = int(slider_state["value"])
                value = max(int(self.slider_time.minimum()),
                            min(int(self.slider_time.maximum()), value))
                self.slider_time.setValue(value)

            if "switch_axes" in state:
                self.switch_axes.setChecked(bool(state["switch_axes"]))
            self.set_rotation_angle(state.get("rotation_angle", 0.0))
            if "switch_flip" in state:
                self.switch_flip.setChecked(bool(state["switch_flip"]))
        finally:
            del blockers

    def set_total_frames(self, count):
        if self.total_label is not None:
            self.total_label.setText(f"/ {int(count)} 帧" if count else "–")

    def set_timeline_visible(self, visible, *, animate=True):
        visible = bool(visible)
        if self._timeline_visible_target == visible:
            self._sync_bar_visibility()
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
        visible = bool(visible)
        if self._axis_visible_target == visible:
            self._sync_bar_visibility()
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
        self._apply_wrap(self._should_wrap())
        self._sync_bar_visibility()

    def _sync_bar_visibility(self):
        self.setVisible(self._axis_visible_target or self._timeline_visible_target)
        self.updateGeometry()

    def _on_group_animation_frame(self):
        self._apply_wrap(self._should_wrap())
        self._sync_bar_visibility()
        self.layoutChanged.emit()

    def set_position_label(self, text):
        self.interval_control.subtitle_label.setText(f"沿 {text} 积分")
        self._apply_wrap(self._should_wrap())
