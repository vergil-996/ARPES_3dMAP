import math

from PyQt5.QtCore import QPointF, QRectF, Qt, QSignalBlocker, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from bandscope.ui import theme
from bandscope.ui.ui_controls import ActionButton, RotationSpinBox


class RotationDial(QWidget):
    angle_changed = pyqtSignal(float)
    drag_finished = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0.0
        self._dragging = False
        self._drag_angle = 0.0
        self._last_pointer_angle = 0.0
        self.value_editor = None
        self.setFixedSize(172, 172)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("Kx–Ky 平面旋转圆盘")
        self.setToolTip("拖动外圈旋转；跨越顶部时角度连续变化")

    def angle(self):
        return float(self._angle)

    def set_angle(self, angle):
        self._angle = self._clamp(angle)
        self.update()

    def set_value_editor(self, editor):
        self.value_editor = editor
        self._center_value_editor()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._center_value_editor()

    def _center_value_editor(self):
        if self.value_editor is None:
            return
        center = self.rect().center()
        editor = self.value_editor
        editor.move(center.x() - editor.width() // 2, center.y() - editor.height() // 2)

    @staticmethod
    def _clamp(angle):
        return max(-360.0, min(360.0, round(float(angle), 2)))

    def _ring_geometry(self):
        rect = QRectF(self.rect()).adjusted(12, 12, -12, -12)
        return rect, rect.center(), rect.width() / 2.0

    def _pointer_angle(self, position):
        _rect, center, _radius = self._ring_geometry()
        delta = position - center
        return math.degrees(math.atan2(delta.x(), -delta.y()))

    def _inside_outer_ring(self, position):
        _rect, center, radius = self._ring_geometry()
        distance = math.hypot(position.x() - center.x(), position.y() - center.y())
        return radius * 0.64 <= distance <= radius * 1.12

    def mousePressEvent(self, event):
        position = QPointF(event.localPos())
        if event.button() != Qt.LeftButton or not self._inside_outer_ring(position):
            super().mousePressEvent(event)
            return
        self._dragging = True
        self._drag_angle = self._angle
        self._last_pointer_angle = self._pointer_angle(position)
        self.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            super().mouseMoveEvent(event)
            return
        pointer_angle = self._pointer_angle(QPointF(event.localPos()))
        delta = (pointer_angle - self._last_pointer_angle + 180.0) % 360.0 - 180.0
        self._last_pointer_angle = pointer_angle
        next_angle = max(-360.0, min(360.0, self._drag_angle + delta))
        self._drag_angle = next_angle
        displayed = self._clamp(next_angle)
        if displayed != self._angle:
            self.set_angle(displayed)
            self.angle_changed.emit(displayed)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self._dragging:
            super().mouseReleaseEvent(event)
            return
        self._dragging = False
        self.setCursor(Qt.PointingHandCursor)
        self.drag_finished.emit(self._angle)
        event.accept()

    def cancel_drag(self):
        self._dragging = False
        self._drag_angle = self._angle
        self.releaseMouse()
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect, center, radius = self._ring_geometry()

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(theme.BORDER_HEX), 7.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawEllipse(rect)
        painter.setPen(QPen(QColor(theme.BG_4), 3.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawEllipse(rect)

        painter.setPen(QPen(QColor(theme.TEXT_3), 1.0))
        for degrees in range(0, 360, 15):
            radians = math.radians(degrees)
            direction = QPointF(math.sin(radians), -math.cos(radians))
            outer = center + direction * (radius + 7.0)
            inner = center + direction * (radius + (3.0 if degrees % 90 else 1.0))
            painter.drawLine(inner, outer)

        if abs(self._angle) >= 0.005:
            painter.setPen(QPen(QColor(theme.ACCENT), 3.0, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(rect, 90 * 16, -round(self._angle * 16))

        radians = math.radians(self._angle)
        direction = QPointF(math.sin(radians), -math.cos(radians))
        handle = center + direction * radius
        painter.setPen(QPen(QColor(theme.BG_2), 2.0))
        painter.setBrush(QColor(theme.ACCENT if self._dragging or abs(self._angle) >= 0.005 else theme.TEXT_2))
        painter.drawEllipse(handle, 6.0, 6.0)
        painter.setPen(QPen(QColor(theme.TEXT_2), 1.5, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(center + direction * (radius - 13.0), center + direction * (radius - 7.0))


class RotationDialPanel(QFrame):
    rotation_preview = pyqtSignal(float)
    rotation_committed = pyqtSignal(float)
    angle_presented = pyqtSignal(float)
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("rotation_dial_panel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QFrame#rotation_dial_panel {{ background-color: {theme.BG_2};"
            f"border: 1px solid {theme.BORDER_HEX}; border-radius: 8px; }}"
        )
        self._editor_dirty = False
        self._suppress_commit = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        title = QLabel("Kx–Ky 平面旋转 / °", self)
        title.setStyleSheet(theme.field_label_qss())
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        self.dial = RotationDial(self)
        self.edit_rotation = RotationSpinBox(self.dial)
        self.edit_rotation.setAccessibleName("Z 轴旋转角度")
        self.edit_rotation.setRange(-360.0, 360.0)
        self.edit_rotation.setDecimals(2)
        self.edit_rotation.setSingleStep(1.0)
        self.edit_rotation.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.edit_rotation.setAlignment(Qt.AlignCenter)
        self.edit_rotation.setFixedSize(88, 30)
        self.edit_rotation.setToolTip("输入 −360° 到 +360°；上下键每次调整 1°；回车提交")
        self.edit_rotation.setValue(0.0)
        self.dial.set_value_editor(self.edit_rotation)
        layout.addWidget(self.dial, 0, Qt.AlignCenter)

        self.zero_button = ActionButton(self)
        self.zero_button.setText("归零")
        self.zero_button.setAccessibleName("旋转角度归零")
        self.zero_button.setFixedHeight(28)
        self.zero_button.setToolTip("立即恢复 0°，保持相机位置和朝向")
        theme.style_push_button(self.zero_button, "quiet")
        layout.addWidget(self.zero_button)

        self.dial.angle_changed.connect(self._on_dial_angle)
        self.dial.drag_finished.connect(self._on_drag_finished)
        self.edit_rotation.textChanged.connect(self._on_editor_text_changed)
        self.edit_rotation.editingFinished.connect(self._on_editing_finished)
        self.zero_button.clicked.connect(self.zero_angle)
        self.adjustSize()

    def current_angle(self):
        try:
            angle = float(self.edit_rotation.text().strip())
        except (TypeError, ValueError):
            angle = float(self.edit_rotation.value())
        return max(-360.0, min(360.0, angle))

    def set_angle(self, angle):
        value = max(-360.0, min(360.0, round(float(angle), 2)))
        blocker = QSignalBlocker(self.edit_rotation)
        self.edit_rotation.setValue(value)
        del blocker
        self.dial.set_angle(value)
        self._editor_dirty = False
        self.angle_presented.emit(value)

    def _on_dial_angle(self, angle):
        self.set_angle(angle)
        self.rotation_preview.emit(self.current_angle())

    def _on_drag_finished(self, angle):
        self.set_angle(angle)
        self.rotation_committed.emit(self.current_angle())

    def _on_editor_text_changed(self, text):
        try:
            angle = float(str(text).strip())
        except (TypeError, ValueError):
            return
        if not -360.0 <= angle <= 360.0:
            return
        self._editor_dirty = True
        self.dial.set_angle(angle)
        self.angle_presented.emit(angle)
        self.rotation_preview.emit(angle)

    def _on_editing_finished(self):
        if self._suppress_commit:
            return
        self.commit_editor()

    def commit_editor(self):
        value = self.current_angle()
        self.set_angle(value)
        self.rotation_committed.emit(self.current_angle())

    def finish_editing(self):
        if self._editor_dirty:
            self.commit_editor()
        else:
            self.edit_rotation.clearFocus()

    def zero_angle(self):
        self.set_angle(0.0)
        self.rotation_committed.emit(0.0)

    def dismiss(self):
        self.dial.cancel_drag()
        self._editor_dirty = False
        self._suppress_commit = True
        try:
            self.hide()
        finally:
            self._suppress_commit = False

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.dial._center_value_editor()
        self.edit_rotation.setFocus(Qt.ActiveWindowFocusReason)
        self.edit_rotation.selectAll()

    def hideEvent(self, event):
        self.dial.cancel_drag()
        if self._editor_dirty and not self._suppress_commit:
            self.commit_editor()
        super().hideEvent(event)
        self.closed.emit()
