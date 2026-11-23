from PyQt5.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QPointF,
    QRectF,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt5.QtWidgets import QAbstractButton


class DayNightSwitch(QAbstractButton):
    theme_requested = pyqtSignal(str)

    WIDTH = 96
    HEIGHT = 38
    SCENE_WIDTH = 180.0
    SCENE_HEIGHT = 70.0
    DURATION_MS = 560
    HALO_LAYERS = (
        (1.36, 105, 72),
        (1.95, 48, 26),
    )
    STAR_FIELD = (
        (16.0, 17.0, 0.85, 135),
        (29.0, 12.0, 1.25, 190),
        (43.0, 28.0, 1.55, 215),
        (57.0, 14.0, 0.9, 150),
        (65.0, 43.0, 1.2, 180),
        (25.0, 47.0, 0.75, 120),
        (77.0, 18.0, 1.8, 220),
        (99.0, 12.0, 1.3, 180),
        (119.0, 21.0, 1.6, 215),
        (145.0, 13.0, 1.2, 170),
        (93.0, 39.0, 1.1, 155),
        (159.0, 37.0, 1.4, 190),
        (116.0, 51.0, 1.0, 145),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme_name = "light"
        self._progress = 0.0
        self._hover_progress = 0.0

        self._theme_animation = QPropertyAnimation(self, b"progress", self)
        self._theme_animation.setDuration(self.DURATION_MS)
        self._theme_animation.setEasingCurve(QEasingCurve.InOutCubic)
        self._hover_animation = QPropertyAnimation(self, b"hoverProgress", self)
        self._hover_animation.setEasingCurve(QEasingCurve.OutCubic)

        self.setCheckable(True)
        self.setChecked(False)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self._update_accessibility()
        self.clicked.connect(self._on_clicked)

    def theme_name(self):
        return self._theme_name

    def set_theme(self, name, animate=True):
        if name not in ("light", "dark"):
            raise ValueError("theme name must be 'light' or 'dark'")

        target = 1.0 if name == "dark" else 0.0
        if name == self._theme_name:
            self.setChecked(name == "dark")
            if not animate:
                self._theme_animation.stop()
                self._set_progress(target)
            return

        self._theme_animation.stop()
        self._theme_name = name
        self.setChecked(name == "dark")
        self._update_accessibility()
        if animate:
            self._theme_animation.setStartValue(self._progress)
            self._theme_animation.setEndValue(target)
            self._theme_animation.start()
        else:
            self._set_progress(target)

    def _get_progress(self):
        return self._progress

    def _set_progress(self, value):
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    progress = pyqtProperty(float, fget=_get_progress, fset=_set_progress)

    def _get_hover_progress(self):
        return self._hover_progress

    def _set_hover_progress(self, value):
        self._hover_progress = float(value)
        self.update()

    hoverProgress = pyqtProperty(
        float, fget=_get_hover_progress, fset=_set_hover_progress
    )

    def _on_clicked(self, checked):
        name = "dark" if checked else "light"
        self.set_theme(name, animate=True)
        self.theme_requested.emit(name)

    def _update_accessibility(self):
        current = "深色" if self._theme_name == "dark" else "浅色"
        target = "浅色" if self._theme_name == "dark" else "深色"
        self.setToolTip(f"当前为{current}主题；点击或按空格切换到{target}主题")
        self.setAccessibleName(f"昼夜主题切换，当前{current}主题")
        self.setAccessibleDescription(f"激活后切换到{target}主题")

    def _animate_hover(self, target, leaving=False):
        self._hover_animation.stop()
        self._hover_animation.setDuration(240 if leaving else 150)
        self._hover_animation.setEasingCurve(
            QEasingCurve.OutBack if leaving else QEasingCurve.OutCubic
        )
        self._hover_animation.setStartValue(self._hover_progress)
        self._hover_animation.setEndValue(target)
        self._hover_animation.start()

    def enterEvent(self, event):
        super().enterEvent(event)
        if self.isEnabled():
            self._animate_hover(1.0)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self.isEnabled():
            self._animate_hover(0.0, leaving=True)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.EnabledChange:
            self._hover_animation.stop()
            self._set_hover_progress(0.0)
            self.setCursor(
                Qt.PointingHandCursor if self.isEnabled() else Qt.ArrowCursor
            )

    @staticmethod
    def _smoothstep(value):
        value = max(0.0, min(1.0, value))
        return value * value * (3.0 - 2.0 * value)

    @staticmethod
    def _mixed_color(day, night, amount):
        return QColor(*(
            round(start + (end - start) * amount)
            for start, end in zip(day, night)
        ))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        scale = min(
            self.width() / self.SCENE_WIDTH,
            self.height() / self.SCENE_HEIGHT,
        )
        painter.translate(
            (self.width() - self.SCENE_WIDTH * scale) / 2.0,
            (self.height() - self.SCENE_HEIGHT * scale) / 2.0,
        )
        painter.scale(scale, scale)

        progress = self._progress
        night_mix = self._smoothstep(progress)
        day_mix = 1.0 - night_mix
        capsule = QPainterPath()
        capsule_rect = QRectF(0.8, 0.8, 178.4, 68.4)
        capsule.addRoundedRect(capsule_rect, 34.2, 34.2)

        sky = QLinearGradient(capsule_rect.topLeft(), capsule_rect.bottomLeft())
        sky.setColorAt(
            0.0,
            self._mixed_color((76, 174, 237), (6, 16, 46), progress),
        )
        sky.setColorAt(
            1.0,
            self._mixed_color((174, 224, 249), (30, 53, 91), progress),
        )
        painter.fillPath(capsule, sky)

        painter.save()
        painter.setClipPath(capsule)
        self._draw_stars(painter, night_mix)
        self._draw_clouds(painter, progress, day_mix)

        hover_direction = -1.0 if self._theme_name == "dark" else 1.0
        thumb_x = (
            35.0
            + 110.0 * progress
            + hover_direction * 3.0 * self._hover_progress
        )
        center = QPointF(thumb_x, 35.0)
        radius = 29.0
        thumb_rect = QRectF(
            center.x() - radius,
            center.y() - radius,
            radius * 2.0,
            radius * 2.0,
        )

        self._draw_halos(
            painter, center, radius, QColor(255, 236, 178), day_mix
        )
        self._draw_halos(
            painter, center, radius, QColor(208, 229, 255), night_mix
        )

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(5, 15, 31, 38))
        painter.drawEllipse(thumb_rect.translated(0.0, 2.2))
        self._draw_sun(painter, center, radius)
        if night_mix > 0.0:
            painter.save()
            painter.setOpacity(night_mix)
            self._draw_moon(painter, center, radius)
            painter.restore()

        if not self.isEnabled():
            painter.fillRect(capsule_rect, QColor(225, 230, 237, 100))
        painter.restore()

        painter.setBrush(Qt.NoBrush)
        border = QColor(255, 255, 255, 105) if self.isEnabled() else QColor(112, 122, 137, 180)
        painter.setPen(QPen(border, 1.4))
        painter.drawPath(capsule)

        if self.hasFocus() and self.isEnabled():
            focus_rect = capsule_rect.adjusted(1.3, 1.3, -1.3, -1.3)
            painter.setPen(QPen(QColor(248, 251, 255, 235), 3.6))
            painter.drawRoundedRect(focus_rect, 32.9, 32.9)
            painter.setPen(QPen(QColor(33, 101, 164, 235), 1.5))
            painter.drawRoundedRect(focus_rect, 32.9, 32.9)

    def _draw_halos(self, painter, center, radius, color, opacity):
        if opacity <= 0.0:
            return
        painter.save()
        painter.setOpacity(opacity)
        painter.setPen(Qt.NoPen)
        for radius_scale, center_alpha, middle_alpha in reversed(self.HALO_LAYERS):
            halo_radius = radius * radius_scale
            halo = QRadialGradient(center, halo_radius)
            center_color = QColor(color)
            center_color.setAlpha(center_alpha)
            middle_color = QColor(color)
            middle_color.setAlpha(middle_alpha)
            edge_color = QColor(color)
            edge_color.setAlpha(0)
            halo.setColorAt(0.0, center_color)
            halo.setColorAt(0.72, middle_color)
            halo.setColorAt(1.0, edge_color)
            painter.setBrush(halo)
            painter.drawEllipse(center, halo_radius, halo_radius)
        painter.restore()

    def _draw_stars(self, painter, opacity):
        if opacity <= 0.0:
            return
        painter.save()
        painter.setOpacity(opacity)
        painter.translate(-9.0 * (1.0 - opacity), 10.0 * (1.0 - opacity))
        painter.setPen(Qt.NoPen)
        for x, y, radius, brightness in self.STAR_FIELD:
            painter.setBrush(QColor(241, 246, 255, brightness))
            star = QPainterPath()
            star.moveTo(x, y - radius * 2.0)
            star.quadTo(x + radius * 0.35, y - radius * 0.35, x + radius * 2.0, y)
            star.quadTo(x + radius * 0.35, y + radius * 0.35, x, y + radius * 2.0)
            star.quadTo(x - radius * 0.35, y + radius * 0.35, x - radius * 2.0, y)
            star.quadTo(x - radius * 0.35, y - radius * 0.35, x, y - radius * 2.0)
            star.closeSubpath()
            painter.drawPath(star)
        painter.setBrush(QColor(210, 226, 255, 180))
        painter.drawEllipse(QPointF(70.0, 46.0), 1.2, 1.2)
        painter.drawEllipse(QPointF(135.0, 33.0), 1.0, 1.0)
        painter.restore()

    @staticmethod
    def _cloud_band_path():
        cloud = QPainterPath()
        cloud.setFillRule(Qt.WindingFill)
        cloud.addEllipse(QRectF(-24.0, 47.0, 48.0, 48.0))
        cloud.addEllipse(QRectF(6.0, 43.0, 48.0, 48.0))
        cloud.addEllipse(QRectF(37.0, 48.0, 46.0, 46.0))
        cloud.addEllipse(QRectF(66.0, 43.0, 48.0, 48.0))
        cloud.addEllipse(QRectF(91.0, 33.0, 46.0, 46.0))
        cloud.addEllipse(QRectF(117.0, 40.0, 48.0, 48.0))
        cloud.addEllipse(QRectF(145.0, 42.0, 45.0, 45.0))
        cloud.addEllipse(QRectF(156.0, 24.0, 34.0, 34.0))
        cloud.addEllipse(QRectF(177.0, 31.0, 31.0, 31.0))
        cloud.addRoundedRect(QRectF(-24.0, 55.0, 230.0, 30.0), 15.0, 15.0)
        return cloud

    def _draw_clouds(self, painter, progress, opacity):
        if opacity <= 0.0:
            return
        painter.save()
        painter.translate(-10.0 * progress, 36.0 * progress)
        painter.setPen(Qt.NoPen)

        cloud = self._cloud_band_path()
        back_cloud = QPainterPath(cloud)
        back_cloud.translate(9.0, -6.0)
        painter.setOpacity(opacity * 0.38)
        painter.fillPath(back_cloud, QColor(224, 240, 250))

        painter.setOpacity(opacity)
        painter.fillPath(cloud, QColor(252, 253, 255))
        painter.restore()

    @staticmethod
    def _draw_sun(painter, center, radius):
        gradient = QRadialGradient(
            QPointF(center.x() - radius * 0.30, center.y() - radius * 0.34),
            radius * 1.45,
        )
        gradient.setColorAt(0.0, QColor(255, 248, 183))
        gradient.setColorAt(0.42, QColor(255, 215, 91))
        gradient.setColorAt(1.0, QColor(242, 157, 39))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(center, radius, radius)
        painter.setPen(QPen(QColor(255, 250, 204, 145), 1.6, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(
            QRectF(center.x() - 21.0, center.y() - 21.0, 42.0, 42.0),
            65 * 16,
            105 * 16,
        )

    @staticmethod
    def _draw_moon(painter, center, radius):
        gradient = QRadialGradient(
            QPointF(center.x() - radius * 0.32, center.y() - radius * 0.36),
            radius * 1.45,
        )
        gradient.setColorAt(0.0, QColor(255, 255, 250))
        gradient.setColorAt(0.48, QColor(229, 234, 240))
        gradient.setColorAt(1.0, QColor(167, 179, 195))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(center, radius, radius)

        craters = (
            (-9.0, -7.0, 5.3),
            (8.0, -12.0, 3.5),
            (10.0, 8.0, 6.0),
            (-10.0, 12.0, 3.2),
            (0.0, 1.0, 2.4),
        )
        for dx, dy, crater_radius in craters:
            crater_center = QPointF(center.x() + dx, center.y() + dy)
            painter.setBrush(QColor(121, 137, 157, 75))
            painter.setPen(QPen(QColor(111, 128, 149, 70), 0.8))
            painter.drawEllipse(crater_center, crater_radius, crater_radius)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 105), 0.9))
            painter.drawArc(
                QRectF(
                    crater_center.x() - crater_radius + 0.8,
                    crater_center.y() - crater_radius + 0.8,
                    crater_radius * 2.0 - 1.6,
                    crater_radius * 2.0 - 1.6,
                ),
                35 * 16,
                130 * 16,
            )
