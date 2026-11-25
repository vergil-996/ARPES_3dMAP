from __future__ import annotations

import math
import weakref

from PyQt5 import sip
from PyQt5.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt5.QtWidgets import QAbstractButton

import bandscope.ui.theme as theme
from bandscope.core.opacity_curve import (
    DEFAULT_OPACITY_INTERPOLATION,
    OPACITY_LINEAR,
    OPACITY_SMOOTH,
    normalize_opacity_interpolation,
)


class OpacityInterpolationSwitch(QAbstractButton):
    interpolationChanged = pyqtSignal(str)

    WIDTH = 72
    HEIGHT = 28
    THUMB_WIDTH = 32.0
    THUMB_MARGIN = 3.0
    DURATION_MS = 240

    def __init__(self, parent=None):
        super().__init__(parent)
        self._interpolation = DEFAULT_OPACITY_INTERPOLATION
        self._progress = self._target_progress(self._interpolation)
        self._hovered = False

        self._animation = QPropertyAnimation(self, b"progress", self)
        self._animation.setDuration(self.DURATION_MS)
        self._animation.setEasingCurve(QEasingCurve.InOutCubic)

        self.setCheckable(True)
        self.setChecked(self._interpolation == OPACITY_LINEAR)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self._update_accessibility()
        self.clicked.connect(self._on_clicked)

        reference = weakref.ref(self)

        def on_theme_changed(_name):
            switch = reference()
            if switch is not None and not sip.isdeleted(switch):
                switch.update()

        def on_destroyed(*_args):
            theme.changed.disconnect(on_theme_changed)

        self._theme_listener = on_theme_changed
        self._theme_destroy_listener = on_destroyed
        theme.changed.connect(self._theme_listener)
        self.destroyed.connect(self._theme_destroy_listener)

    @staticmethod
    def _target_progress(mode):
        return 1.0 if mode == OPACITY_LINEAR else 0.0

    def interpolation(self):
        return self._interpolation

    def set_interpolation(self, value, animate=True):
        mode = normalize_opacity_interpolation(value, DEFAULT_OPACITY_INTERPOLATION)
        target = self._target_progress(mode)
        if mode == self._interpolation:
            self.setChecked(mode == OPACITY_LINEAR)
            if not animate:
                self._animation.stop()
                self._set_progress(target)
            return

        self._animation.stop()
        self._interpolation = mode
        self.setChecked(mode == OPACITY_LINEAR)
        self._update_accessibility()
        if animate:
            self._animation.setStartValue(self._progress)
            self._animation.setEndValue(target)
            self._animation.start()
        else:
            self._set_progress(target)

    def _get_progress(self):
        return self._progress

    def _set_progress(self, value):
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    progress = pyqtProperty(float, fget=_get_progress, fset=_set_progress)

    def _on_clicked(self, checked):
        mode = OPACITY_LINEAR if checked else OPACITY_SMOOTH
        if mode == self._interpolation:
            return
        self.set_interpolation(mode, animate=True)
        self.interpolationChanged.emit(mode)

    def _update_accessibility(self):
        smooth = self._interpolation == OPACITY_SMOOTH
        current = "平滑曲线" if smooth else "直线"
        target = "直线" if smooth else "平滑曲线"
        self.setToolTip(f"当前为{current}插值；点击、按空格或回车切换到{target}插值")
        self.setAccessibleName(f"不透明度插值方式，当前{current}")
        self.setAccessibleDescription(
            f"左侧为平滑曲线，右侧为直线；激活后切换到{target}插值"
        )

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.isEnabled() and not event.isAutoRepeat():
                self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event):
        super().enterEvent(event)
        self._hovered = True
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hovered = False
        self.update()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.EnabledChange:
            self._hovered = False
            self.setCursor(Qt.PointingHandCursor if self.isEnabled() else Qt.ArrowCursor)
            self.update()

    def _track_rect(self):
        return QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)

    def _thumb_rect(self, progress=None):
        progress = self._progress if progress is None else progress
        travel = self.width() - 2.0 * self.THUMB_MARGIN - self.THUMB_WIDTH
        return QRectF(
            self.THUMB_MARGIN + travel * progress,
            self.THUMB_MARGIN,
            self.THUMB_WIDTH,
            self.height() - 2.0 * self.THUMB_MARGIN,
        )

    @staticmethod
    def _mixed(first, second, amount):
        amount = max(0.0, min(1.0, float(amount)))
        return QColor(*(
            round(start + (end - start) * amount)
            for start, end in zip(first.getRgb(), second.getRgb())
        ))

    @staticmethod
    def _with_alpha(color, alpha):
        result = QColor(color)
        result.setAlpha(alpha)
        return result

    @staticmethod
    def _icon_path(mode, center):
        left = center.x() - 7.0
        right = center.x() + 7.0
        top = center.y() - 4.0
        bottom = center.y() + 4.0
        if mode == OPACITY_SMOOTH:
            path = QPainterPath()
            for index in range(33):
                position = index / 32.0
                wave = (
                    3.1 * math.sin(2.0 * math.pi * position)
                    + 0.45 * math.sin(4.0 * math.pi * position)
                )
                point = QPointF(left + 14.0 * position, center.y() - wave)
                if index == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            return path

        vertices = (
            QPointF(left, center.y() + 2.5),
            QPointF(center.x() - 2.4, top),
            QPointF(center.x() + 2.4, bottom),
            QPointF(right, center.y() - 2.5),
        )
        path = QPainterPath(vertices[0])
        for vertex in vertices[1:]:
            path.lineTo(vertex)
        return path

    def _paint_groove(self, painter):
        rect = self._track_rect()
        radius = rect.height() / 2.0
        dark = theme.current_theme() == "dark"

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 38 if dark else 24))
        painter.drawRoundedRect(rect.translated(0.0, 1.0), radius, radius)

        fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        fill.setColorAt(0.0, self._mixed(
            theme.token_color("BG_4"), theme.token_color("BG_0"), 0.48,
        ))
        fill.setColorAt(1.0, self._mixed(
            theme.token_color("BG_4"), theme.token_color("BG_3"), 0.42,
        ))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, radius, radius)

        groove = QPainterPath()
        groove.addRoundedRect(rect, radius, radius)
        painter.save()
        painter.setClipPath(groove)

        top_shadow = QLinearGradient(
            rect.topLeft(), QPointF(rect.left(), rect.top() + 7.0),
        )
        top_shadow.setColorAt(0.0, QColor(0, 0, 0, 115 if dark else 72))
        top_shadow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(rect, top_shadow)

        bottom_light = QLinearGradient(
            QPointF(rect.left(), rect.bottom() - 5.0), rect.bottomLeft(),
        )
        bottom_light.setColorAt(0.0, QColor(255, 255, 255, 0))
        bottom_light.setColorAt(1.0, QColor(255, 255, 255, 28 if dark else 105))
        painter.fillRect(rect, bottom_light)

        side_shadow = QLinearGradient(rect.topLeft(), rect.topRight())
        side_shadow.setColorAt(0.0, QColor(0, 0, 0, 45 if dark else 24))
        side_shadow.setColorAt(0.18, QColor(0, 0, 0, 0))
        side_shadow.setColorAt(0.82, QColor(0, 0, 0, 0))
        side_shadow.setColorAt(1.0, QColor(0, 0, 0, 45 if dark else 24))
        painter.fillRect(rect, side_shadow)
        painter.restore()

        edge = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        edge.setColorAt(0.0, QColor(255, 255, 255, 55 if dark else 175))
        edge.setColorAt(0.48, theme.token_color("BORDER_HEX"))
        edge.setColorAt(1.0, QColor(0, 0, 0, 115 if dark else 42))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QBrush(edge), 0.9))
        painter.drawRoundedRect(rect.adjusted(0.45, 0.45, -0.45, -0.45),
                                radius - 0.45, radius - 0.45)


    def _paint_thumb(self, painter):
        rect = self._thumb_rect()
        radius = rect.height() / 2.0
        accent = theme.token_color("ACCENT")

        if self._hovered and self.isEnabled():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(self._with_alpha(accent, 65), 1.2))
            painter.drawRoundedRect(rect.adjusted(-1.0, -1.0, 1.0, 1.0),
                                    radius + 1.0, radius + 1.0)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 72))
        painter.drawRoundedRect(rect.translated(0.0, 1.2), radius, radius)

        fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        fill.setColorAt(0.0, self._mixed(accent, QColor("#FFFFFF"), 0.14))
        fill.setColorAt(0.52, accent)
        fill.setColorAt(1.0, self._mixed(accent, QColor("#000000"), 0.17))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, radius, radius)

        edge = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        edge.setColorAt(0.0, QColor(255, 255, 255, 105))
        edge.setColorAt(0.45, self._with_alpha(accent, 80))
        edge.setColorAt(1.0, QColor(0, 0, 0, 95))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QBrush(edge), 0.8))
        painter.drawRoundedRect(rect.adjusted(0.4, 0.4, -0.4, -0.4),
                                radius - 0.4, radius - 0.4)

        painter.setPen(QPen(QColor(255, 255, 255, 90), 0.75,
                            Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect.adjusted(2.0, 1.4, -2.0, -1.4), 35 * 16, 110 * 16)

    def _paint_icons(self, painter):
        left_center = self._thumb_rect(0.0).center()
        right_center = self._thumb_rect(1.0).center()
        inactive = theme.token_color("TEXT_2")
        active = theme.token_color("ACCENT_ON")
        for mode, center, amount in (
            (OPACITY_SMOOTH, left_center, 1.0 - self._progress),
            (OPACITY_LINEAR, right_center, self._progress),
        ):
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(
                self._mixed(inactive, active, amount), 1.8,
                Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin,
            ))
            painter.drawPath(self._icon_path(mode, center))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.46)
        self._paint_groove(painter)
        self._paint_thumb(painter)
        self._paint_icons(painter)
        if self.hasFocus() and self.isEnabled():
            rect = self._track_rect().adjusted(-0.35, -0.35, 0.35, 0.35)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(theme.token_color("TEXT_2"), 1.15))
            painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
