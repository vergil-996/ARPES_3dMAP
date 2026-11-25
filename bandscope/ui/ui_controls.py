# -*- coding: utf-8 -*-
"""视觉自同步控件 —— 把 SiUI 控件的程序化状态同步收进控件内部。

SiSlider / SiSwitchRefactor 的绘制状态由独立动画属性驱动，只在用户
交互时更新；程序化 ``setValue()`` / ``setChecked()`` 不会改变视觉位置，
导致恢复页面状态时滑条/开关显示陈旧。以前每个页面的 ``restore_state``
都要记得手动调 ``sync_slider_visual`` / ``sync_switch_visual`` 补丁，
现在由控件自身保证：任何 ``setValue`` / ``setChecked`` 后视觉立即一致。

经核实 SiSwitchRefactor._onClicked 直接驱动动画、不经过 setChecked，
ContinuousFrameSlider 拖动后也会自行覆盖精确进度，因此重写这两个方法
不会破坏用户交互动画。
"""

from PyQt5.QtCore import QEasingCurve, QPropertyAnimation, QPointF, QRectF, Qt, pyqtProperty
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PyQt5.QtWidgets import QComboBox, QDoubleSpinBox, QPushButton
from siui.components.button import SiSwitchRefactor
from siui.components.slider_ import SiSlider

import bandscope.ui.theme as theme
from bandscope.ui.relief_painting import paint_raised_path


class ChevronComboBox(QComboBox):
    """在应用的无边框下拉按钮上绘制随调色板变化的箭头。"""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(self.palette().text().color(), 1.5, Qt.SolidLine,
                            Qt.RoundCap, Qt.RoundJoin))
        x, y = self.width() - 16, self.height() / 2
        painter.drawPolyline(QPolygonF([
            QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2),
        ]))


class ActionButton(QPushButton):
    """Keyboard-accessible button retaining the SiUI text attachment API."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)

    def attachment(self):
        return self


class AnimatedLockButton(ActionButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock_progress = 0.0
        self._lock_animation = QPropertyAnimation(self, b"lockProgress", self)
        self._lock_animation.setDuration(180)
        self._lock_animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.setCheckable(True)
        self.setProperty("compactIconButton", True)
        self.setProperty("raisedButton", True)
        self.toggled.connect(self._animate_lock)

    def _get_lock_progress(self):
        return self._lock_progress

    def _set_lock_progress(self, value):
        self._lock_progress = float(value)
        self.update()

    lockProgress = pyqtProperty(float, fget=_get_lock_progress, fset=_set_lock_progress)

    def setChecked(self, checked):
        signals_blocked = self.signalsBlocked()
        super().setChecked(checked)
        if signals_blocked:
            self._lock_animation.stop()
            self._set_lock_progress(float(self.isChecked()))

    def _animate_lock(self, checked):
        self._lock_animation.stop()
        self._lock_animation.setStartValue(self._lock_progress)
        self._lock_animation.setEndValue(float(checked))
        self._lock_animation.start()

    def apply_theme(self):
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            color = QColor(theme.TEXT_3 if not self.isEnabled() else (
                theme.ACCENT_ON if self.isChecked() else theme.TEXT_2
            ))
            scale = min(self.width() / 22.0, self.height() / 20.0)
            painter.translate(QRectF(self.rect()).center())
            painter.scale(scale, scale)

            shackle = QPainterPath()
            shackle.moveTo(-3.35, -0.5)
            shackle.lineTo(-3.35, -4.5)
            shackle.cubicTo(-3.35, -7.6, 3.35, -7.6, 3.35, -4.5)
            shackle.lineTo(3.35, -0.5 - 3.8 * (1.0 - self._lock_progress))
            painter.save()
            painter.translate(-3.35, -0.5)
            painter.rotate(-22.0 * (1.0 - self._lock_progress))
            painter.translate(3.35, 0.5)
            for layer_color, offset in (
                (QColor(0, 0, 0, 72), 0.55),
                (QColor(255, 255, 255, 88), -0.45),
                (color, 0.0),
            ):
                painter.save()
                painter.translate(0.0, offset)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(
                    layer_color, 1.55, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin,
                ))
                painter.drawPath(shackle)
                painter.restore()
            painter.restore()

            body = QPainterPath()
            body.addRoundedRect(QRectF(-5.5, -0.5, 11.0, 8.5), 1.8, 1.8)
            for layer_color, offset in (
                (QColor(0, 0, 0, 65), 0.55),
                (QColor(255, 255, 255, 82), -0.45),
            ):
                painter.save()
                painter.translate(0.0, offset)
                painter.fillPath(body, layer_color)
                painter.restore()
            paint_raised_path(
                painter, body, color, color, pressed=self.isDown(), shadow=False,
            )
        finally:
            painter.end()


class RotationSpinBox(QDoubleSpinBox):
    """Expose live text edits for the existing rotation preview pipeline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDecimals(10)
        self.setStyleSheet(theme.value_input_qss())

    def textFromValue(self, value):
        text = f"{value:.10f}".rstrip("0").rstrip(".")
        return text if "." in text else text + ".0"


class SyncedSlider(SiSlider):
    """setValue / setRange 后自动同步滑块位置与关联数值标签的 SiSlider。"""

    def setValue(self, value):
        super().setValue(value)
        self.sync_visual()

    def setRange(self, minimum, maximum):
        super().setRange(minimum, maximum)
        self.sync_visual()

    def sync_visual(self):
        minimum = int(self.minimum())
        maximum = int(self.maximum())
        value = int(self.value())
        label = getattr(self, "_value_label", None)
        if label is not None:
            label.setText(str(value))
        progress = 0.0 if maximum == minimum else (value - minimum) / (maximum - minimum)

        try:
            self.setProperty(self.Property.TrackProgress, progress)
        except Exception:
            pass

        progress_ani = getattr(self, "progress_ani", None)
        if progress_ani is not None:
            try:
                progress_ani.fromProperty()
                progress_ani.setCurrentValue(progress)
                progress_ani.setEndValue(progress)
            except Exception:
                pass

        update_tooltip = getattr(self, "_updateToolTip", None)
        if callable(update_tooltip):
            try:
                update_tooltip(flash=False)
            except Exception:
                pass

        self.update()


class SyncedSwitch(SiSwitchRefactor):
    """setChecked 后自动同步拨杆视觉位置的 SiSwitchRefactor。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.apply_theme()

    def apply_theme(self):
        self.style_data.background_color_starting = QColor(theme.ACCENT)
        self.style_data.background_color_ending = QColor(theme.ACCENT)
        self.style_data.frame_color = QColor(theme.BORDER_HEX)
        self.style_data.thumb_color_checked = QColor(theme.ACCENT_ON)
        self.style_data.thumb_color_unchecked = QColor(theme.BG_2)
        self.update()

    def setChecked(self, checked):
        super().setChecked(checked)
        self.sync_visual()

    def sync_visual(self):
        progress = 1.0 if bool(self.isChecked()) else 0.0

        try:
            self.setProperty(self.Property.Progress, progress)
        except Exception:
            pass

        progress_ani = getattr(self, "progress_ani", None)
        if progress_ani is not None:
            try:
                progress_ani.fromProperty()
                progress_ani.setCurrentValue(progress)
                progress_ani.setEndValue(progress)
            except Exception:
                pass

        self.update()
