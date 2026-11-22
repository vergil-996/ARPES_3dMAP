"""Canvas footer control for a physical, two-ended axis interval."""

from __future__ import annotations

from PyQt5.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QPropertyAnimation,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from bandscope.ui import theme


class EndpointSpinBox(QDoubleSpinBox):
    cancelled = pyqtSignal()

    def focusInEvent(self, event):
        self._value_before_edit = self.value()
        super().focusInEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.setValue(getattr(self, "_value_before_edit", self.value()))
            self.cancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class CoordinatePopup(QFrame):
    submitted = pyqtSignal(float)
    cancelled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setObjectName("axis_coordinate_popup")
        self.setStyleSheet(
            f"QFrame#axis_coordinate_popup {{ background: {theme.BG_3};"
            f"border: 1px solid {theme.BORDER_HEX}; border-radius: 7px; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        self.endpoint_marker = QLabel(self)
        self.endpoint_marker.setFixedSize(7, 7)
        layout.addWidget(self.endpoint_marker, 0, Qt.AlignVCenter)
        self.value_edit = EndpointSpinBox(self)
        self.value_edit.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.value_edit.setKeyboardTracking(False)
        self.value_edit.setAlignment(Qt.AlignCenter)
        self.value_edit.setFixedSize(112, 28)
        self.value_edit.setStyleSheet(theme.value_input_qss())
        layout.addWidget(self.value_edit)
        self.value_edit.editingFinished.connect(self._submit)
        self.value_edit.cancelled.connect(self._cancel)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(240)
        self._hide_timer.timeout.connect(self.hide)

    def present(self, slider, endpoint, global_center):
        space = slider._space
        interval = slider._interval
        if space is None or interval is None:
            return
        value = interval.low if endpoint == "low" else interval.up
        unit = "" if space.describes_index else space.unit
        self.value_edit.blockSignals(True)
        self.value_edit.setDecimals(space.decimals)
        self.value_edit.setRange(space.minimum, space.maximum)
        self.value_edit.setSingleStep(space.step)
        self.value_edit.setSuffix(f" {unit}" if unit else "")
        self.value_edit.setValue(value)
        self.value_edit.blockSignals(False)
        color = (theme.RANGE_HANDLE_DARK_FILL if endpoint == "low"
                 else theme.RANGE_HANDLE_LIGHT_FILL)
        border = (theme.RANGE_HANDLE_DARK_BORDER if endpoint == "low"
                  else theme.RANGE_HANDLE_LIGHT_BORDER)
        self.endpoint_marker.setStyleSheet(
            f"background: {color}; border: 1px solid {border}; border-radius: 3px;"
        )
        self.value_edit.setAccessibleName(
            f"{space.display_label} {'下限' if endpoint == 'low' else '上限'}"
        )
        self.adjustSize()
        anchor = QPoint(global_center) - QPoint(self.width() // 2, self.height() + 12)
        screen = QApplication.screenAt(global_center) or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            anchor.setX(min(max(anchor.x(), available.left() + 4),
                            available.right() - self.width() - 4))
            if anchor.y() < available.top() + 4:
                anchor.setY(global_center.y() + 24)
        self.move(anchor)
        self._hide_timer.stop()
        self.show()
        self.raise_()

    def schedule_hide(self):
        self._hide_timer.start()

    def enterEvent(self, event):
        self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.schedule_hide()
        super().leaveEvent(event)

    def _submit(self):
        if not self.isVisible():
            return
        value = float(self.value_edit.value())
        self.hide()
        self.submitted.emit(value)

    def _cancel(self):
        self.hide()
        self.cancelled.emit()


class LockBadge(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._open_progress = 0.0
        self.setFixedSize(24, 16)
        self.setStyleSheet("QToolButton { min-width: 0; min-height: 0; padding: 0; }")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("解锁区间；随后向左拖黑端、向右拖白端")
        self.setAccessibleName("解锁切片区间")
        self._animation = QPropertyAnimation(self, b"openProgress", self)
        self._animation.setDuration(180)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.finished.connect(self._after_unlock)

    def _get_open_progress(self):
        return self._open_progress

    def _set_open_progress(self, value):
        self._open_progress = float(value)
        self.update()

    openProgress = pyqtProperty(float, _get_open_progress, _set_open_progress)

    def show_locked(self):
        self._animation.stop()
        self._open_progress = 0.0
        self.show()
        self.update()

    def animate_unlock(self):
        self._animation.stop()
        self._animation.setStartValue(self._open_progress)
        self._animation.setEndValue(1.0)
        self._animation.start()

    def _after_unlock(self):
        if self._open_progress >= 1.0:
            self.hide()
            self._open_progress = 0.0

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        background = QPainterPath()
        background.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        painter.fillPath(background, QColor(theme.BG_3))
        painter.setPen(QPen(QColor(theme.BORDER_HEX), 1))
        painter.drawPath(background)

        pen = QPen(QColor(theme.TEXT_1), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        body = QRectF(7.0, 7.0, 10.0, 6.5)
        painter.drawRoundedRect(body, 2, 2)
        lift = 3.0 * self._open_progress
        shackle = QPainterPath()
        shackle.moveTo(9.0, 7.0)
        shackle.lineTo(9.0, 4.5 - lift)
        shackle.cubicTo(9.0, 0.5 - lift, 15.0, 0.5 - lift, 15.0, 4.5 - lift)
        if self._open_progress < 0.55:
            shackle.lineTo(15.0, 7.0)
        painter.drawPath(shackle)
        painter.setBrush(QColor(theme.TEXT_1))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(12.0, 10.0), 1.0, 1.0)


class DualRangeSlider(QWidget):
    endpointPreview = pyqtSignal(str, float)
    endpointCommitted = pyqtSignal(str, float)
    centerPreview = pyqtSignal(float)
    interactionFinished = pyqtSignal()
    lockRequested = pyqtSignal(bool)

    TRACK_Y = 30
    HANDLE_WIDTH = 10
    HANDLE_HEIGHT = 16
    SIDE_MARGIN = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._space = None
        self._interval = None
        self._drag_mode = None
        self._drag_origin = 0.0
        self._drag_center = 0.0
        self._hovered = None
        self._badge_locked = False
        self.setMinimumSize(170, 60)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.lock_badge = LockBadge(self)
        self.lock_badge.clicked.connect(lambda: self.lockRequested.emit(False))
        self.lock_badge.hide()
        self.coordinate_popup = CoordinatePopup()
        self.coordinate_popup.submitted.connect(self._commit_popup)
        self._popup_endpoint = "low"

    def sync(self, space, interval, enabled=True):
        was_locked = self._badge_locked
        self._space = space
        self._interval = interval
        self._badge_locked = bool(interval is not None and interval.locked)
        self.setEnabled(bool(enabled))
        if interval is not None and interval.locked:
            self.lock_badge.show_locked()
        elif was_locked:
            self.lock_badge.animate_unlock()
        else:
            self.lock_badge.hide()
        self._position_lock_badge()
        self.update()

    def _track_left(self):
        return self.SIDE_MARGIN + self.HANDLE_WIDTH / 2

    def _track_right(self):
        return max(self._track_left(), self.width() - self.SIDE_MARGIN - self.HANDLE_WIDTH / 2)

    def _x_for(self, value):
        if self._space is None or self._space.physical_span <= 0:
            return self._track_left()
        ratio = (float(value) - self._space.minimum) / self._space.physical_span
        ratio = min(max(ratio, 0.0), 1.0)
        return self._track_left() + ratio * (self._track_right() - self._track_left())

    def _value_for(self, x):
        if self._space is None:
            return 0.0
        width = max(self._track_right() - self._track_left(), 1.0)
        ratio = min(max((x - self._track_left()) / width, 0.0), 1.0)
        return self._space.minimum + ratio * self._space.physical_span

    def handle_center(self, endpoint):
        if self._interval is None:
            return 0.0
        return self._x_for(self._interval.low if endpoint == "low" else self._interval.up)

    def _endpoint_at(self, x):
        if self._interval is None:
            return None
        low_x = self.handle_center("low")
        up_x = self.handle_center("up")
        if abs(low_x - up_x) < 1.0:
            if abs(x - low_x) > 18:
                return None
            return "low" if x < low_x else "up"
        candidates = [(abs(x - low_x), "low"), (abs(x - up_x), "up")]
        distance, endpoint = min(candidates)
        return endpoint if distance <= 15 else None

    def mousePressEvent(self, event):
        if not self.isEnabled() or self._interval is None or event.button() != Qt.LeftButton:
            return
        endpoint = self._endpoint_at(event.pos().x())
        if self._interval.locked and endpoint is not None:
            self._drag_mode = "center"
            self._popup_endpoint = "low"
            self._show_popup("low")
        elif endpoint is not None:
            self._drag_mode = endpoint
            self._popup_endpoint = endpoint
            self._show_popup(endpoint)
        else:
            low_x = self.handle_center("low")
            up_x = self.handle_center("up")
            if not (min(low_x, up_x) <= event.pos().x() <= max(low_x, up_x)):
                return
            self._drag_mode = "center"
        self._drag_origin = self._value_for(event.pos().x())
        self._drag_center = self._interval.center
        event.accept()

    def mouseMoveEvent(self, event):
        if self._interval is None:
            return
        if self._drag_mode is not None:
            value = self._value_for(event.pos().x())
            if self._drag_mode == "center":
                self.centerPreview.emit(self._drag_center + value - self._drag_origin)
                # 合拢/锁定后拖的是整体平移，浮框也要跟着手柄走。以前这条
                # 分支只发预览，浮框停在按下时的位置和坐标，看起来像“跟不上”。
                if self._interval.locked or self.coordinate_popup.isVisible():
                    self._show_popup("low" if self._interval.locked else self._popup_endpoint)
            else:
                self.endpointPreview.emit(self._drag_mode, value)
                self._show_popup(self._drag_mode)
            event.accept()
            return
        endpoint = self._endpoint_at(event.pos().x())
        if endpoint != self._hovered:
            self._hovered = endpoint
            if endpoint is not None:
                self._popup_endpoint = "low" if self._interval.locked else endpoint
                self._show_popup(self._popup_endpoint)
            else:
                self.coordinate_popup.schedule_hide()
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag_mode is None or event.button() != Qt.LeftButton:
            return
        self._drag_mode = None
        self.interactionFinished.emit()
        event.accept()

    def leaveEvent(self, event):
        self._hovered = None
        if self._drag_mode is None:
            self.coordinate_popup.schedule_hide()
        super().leaveEvent(event)

    def resizeEvent(self, event):
        self.coordinate_popup.hide()
        super().resizeEvent(event)
        self._position_lock_badge()

    def _position_lock_badge(self):
        if self._interval is None:
            return
        x = int(self.handle_center("low") - self.lock_badge.width() / 2)
        self.lock_badge.move(min(max(x, 0), max(self.width() - self.lock_badge.width(), 0)), 0)

    def _show_popup(self, endpoint):
        if self._space is None or self._interval is None:
            return
        self._popup_endpoint = endpoint
        x = self.handle_center("low" if self._interval.locked else endpoint)
        global_center = self.mapToGlobal(QPoint(int(x), 4))
        self.coordinate_popup.present(self, endpoint, global_center)

    def _commit_popup(self, value):
        self.endpointCommitted.emit(self._popup_endpoint, float(value))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        left = self._track_left()
        right = self._track_right()
        track = QRectF(left, self.TRACK_Y - 2.5, right - left, 5.0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BG_4))
        painter.drawRoundedRect(track, 2.5, 2.5)
        if self._interval is None:
            return
        low_x = self.handle_center("low")
        up_x = self.handle_center("up")
        selected = QRectF(low_x, self.TRACK_Y - 2.5, max(up_x - low_x, 0.0), 5.0)
        painter.setBrush(theme.token_color("ACCENT_DIM"))
        if selected.width() > 0:
            painter.drawRoundedRect(selected, 2.5, 2.5)

        collapsed = self._interval.locked and self._interval.length == 0.0
        if collapsed:
            self._draw_handle(painter, low_x, "up", collapsed=True)
            self._draw_handle(painter, low_x, "low")
        else:
            self._draw_handle(painter, low_x, "low")
            self._draw_handle(painter, up_x, "up")
        self._position_lock_badge()

    def _draw_handle(self, painter, x, endpoint, collapsed=False):
        outline = 4 if collapsed else 0
        outer = QRectF(
            x - (self.HANDLE_WIDTH + outline) / 2,
            self.TRACK_Y - (self.HANDLE_HEIGHT + outline) / 2,
            self.HANDLE_WIDTH + outline,
            self.HANDLE_HEIGHT + outline,
        )
        white = endpoint == "up"
        fill = QColor(theme.RANGE_HANDLE_LIGHT_FILL if white
                      else theme.RANGE_HANDLE_DARK_FILL)
        border = QColor(theme.RANGE_HANDLE_LIGHT_BORDER if white
                        else theme.RANGE_HANDLE_DARK_BORDER)
        if self._hovered == endpoint or self._drag_mode == endpoint:
            border = QColor(theme.TEXT_1)
        painter.setBrush(fill)
        painter.setPen(QPen(border, 1))
        radius = outer.width() / 2
        painter.drawRoundedRect(outer, radius, radius)


class AxisIntervalControl(QWidget):
    endpointPreview = pyqtSignal(str, float)
    endpointCommitted = pyqtSignal(str, float)
    centerPreview = pyqtSignal(float)
    interactionFinished = pyqtSignal()
    lockRequested = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.accent = QFrame(self)
        self.accent.setFixedSize(3, 30)
        self.accent.setStyleSheet(
            f"background: {theme.ACCENT}; border: none; border-radius: 1px;"
        )
        layout.addWidget(self.accent, 0, Qt.AlignVCenter)

        self.title_label = QLabel("二维切片", self)
        self.title_label.setStyleSheet(theme.card_title_qss())
        layout.addWidget(self.title_label, 0, Qt.AlignVCenter)

        self.range_slider = DualRangeSlider(self)
        layout.addWidget(self.range_slider, 1)
        self.range_slider.endpointPreview.connect(self.endpointPreview)
        self.range_slider.endpointCommitted.connect(self.endpointCommitted)
        self.range_slider.centerPreview.connect(self.centerPreview)
        self.range_slider.interactionFinished.connect(self.interactionFinished)
        self.range_slider.lockRequested.connect(self.lockRequested)

        self.input_length = QDoubleSpinBox(self)
        self.input_length.setFixedSize(112, 29)
        self.input_length.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.input_length.setKeyboardTracking(False)
        self.input_length.setAlignment(Qt.AlignCenter)
        self.input_length.setStyleSheet(theme.value_input_qss())
        self.input_length.setToolTip("区间长度 = 上限 − 下限；以中心扩缩，输入 0 合拢锁定")
        layout.addWidget(self.input_length, 0, Qt.AlignVCenter)

    def sync(self, space, interval, enabled=True):
        if space is None or interval is None:
            return
        if space.key == "delay":
            self.title_label.setText("时间延迟" if space.label == "时间" else space.label)
            self.input_length.setAccessibleName("积分长度")
        else:
            plane = {"X": "YZ", "Y": "XZ", "E": "XY"}.get(space.key, "二维")
            self.title_label.setText(f"{plane} 切片")
            self.input_length.setAccessibleName("区间长度")
        unit = "" if space.describes_index else space.unit
        self.input_length.setSuffix(f" {unit}" if unit else "")
        self.range_slider.sync(space, interval, enabled)

    def minimumSizeHint(self):
        return QSize(370, 60)


__all__ = ["AxisIntervalControl", "DualRangeSlider"]
