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
    QBrush,
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
        self._paint_inset(painter, capsule, capsule_rect, night_mix)

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

        self._draw_halos(
            painter, center, radius, QColor(255, 236, 178), day_mix
        )
        self._draw_halos(
            painter, center, radius, QColor(208, 229, 255), night_mix
        )

        self._draw_thumb_shadow(painter, center, radius, night_mix)
        self._draw_sun(painter, center, radius)
        if night_mix > 0.0:
            painter.save()
            painter.setOpacity(night_mix)
            self._draw_moon(painter, center, radius)
            painter.restore()
        self._draw_foreground_clouds(painter, progress, day_mix)

        if not self.isEnabled():
            painter.fillRect(capsule_rect, QColor(225, 230, 237, 100))
        painter.restore()

        painter.setBrush(Qt.NoBrush)
        if self.isEnabled():
            edge = QLinearGradient(capsule_rect.topLeft(), capsule_rect.bottomLeft())
            edge.setColorAt(0.0, QColor(255, 255, 255, round(175 - 120 * night_mix)))
            edge.setColorAt(0.48, QColor(255, 255, 255, round(70 - 35 * night_mix)))
            edge.setColorAt(1.0, QColor(0, 0, 0, round(42 + 73 * night_mix)))
            border_pen = QPen(QBrush(edge), 1.2)
        else:
            border_pen = QPen(QColor(112, 122, 137, 180), 1.4)
        painter.setPen(border_pen)
        painter.drawPath(capsule)

    @staticmethod
    def _paint_inset(painter, capsule, rect, night_mix):
        painter.save()
        painter.setClipPath(capsule)

        top_shadow = QLinearGradient(
            rect.topLeft(), QPointF(rect.left(), rect.top() + 9.0)
        )
        top_shadow.setColorAt(0.0, QColor(0, 0, 0, round(72 + 43 * night_mix)))
        top_shadow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(rect, top_shadow)

        bottom_light = QLinearGradient(
            QPointF(rect.left(), rect.bottom() - 5.0), rect.bottomLeft()
        )
        bottom_light.setColorAt(0.0, QColor(255, 255, 255, 0))
        bottom_light.setColorAt(1.0, QColor(255, 255, 255, round(105 - 50 * night_mix)))
        painter.fillRect(rect, bottom_light)

        side_shadow = QLinearGradient(rect.topLeft(), rect.topRight())
        side_alpha = round(24 + 21 * night_mix)
        side_shadow.setColorAt(0.0, QColor(0, 0, 0, side_alpha))
        side_shadow.setColorAt(0.18, QColor(0, 0, 0, 0))
        side_shadow.setColorAt(0.82, QColor(0, 0, 0, 0))
        side_shadow.setColorAt(1.0, QColor(0, 0, 0, side_alpha))
        painter.fillRect(rect, side_shadow)
        painter.restore()

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

        painter.setBrush(QColor(176, 202, 239, 105))
        painter.drawEllipse(QPointF(70.0, 46.0), 1.2, 1.2)
        painter.drawEllipse(QPointF(135.0, 33.0), 1.0, 1.0)

        sorted_stars = sorted(self.STAR_FIELD, key=lambda star: star[2])
        for x, y, radius, brightness in sorted_stars:
            depth = max(0.0, min(1.0, (radius - 0.7) / 1.1))
            center = QPointF(x, y)
            if radius >= 1.15:
                glow_radius = 3.6 + radius * 2.3
                glow = QRadialGradient(center, glow_radius)
                glow_alpha = round(brightness * (0.28 + 0.20 * depth))
                glow.setColorAt(0.0, QColor(190, 219, 255, glow_alpha))
                glow.setColorAt(
                    0.35, QColor(158, 196, 248, round(brightness * 0.16))
                )
                glow.setColorAt(1.0, QColor(133, 177, 239, 0))
                painter.setBrush(glow)
                painter.drawEllipse(center, glow_radius, glow_radius)

            star = QPainterPath()
            star.moveTo(x, y - radius * 2.0)
            star.quadTo(x + radius * 0.35, y - radius * 0.35, x + radius * 2.0, y)
            star.quadTo(x + radius * 0.35, y + radius * 0.35, x, y + radius * 2.0)
            star.quadTo(x - radius * 0.35, y + radius * 0.35, x - radius * 2.0, y)
            star.quadTo(x - radius * 0.35, y - radius * 0.35, x, y - radius * 2.0)
            star.closeSubpath()

            if depth > 0.45:
                painter.save()
                painter.translate(0.6, 0.9)
                painter.fillPath(star, QColor(0, 5, 20, round(85 * depth)))
                painter.restore()

            star_fill = QRadialGradient(
                QPointF(x - radius * 0.35, y - radius * 0.4), radius * 2.4
            )
            star_fill.setColorAt(
                0.0, QColor(255, 255, 255, min(255, brightness + 35))
            )
            star_fill.setColorAt(0.55, QColor(226, 238, 255, brightness))
            edge_alpha = round(brightness * (0.55 + 0.35 * depth))
            star_fill.setColorAt(1.0, QColor(154, 190, 238, edge_alpha))
            painter.fillPath(star, star_fill)

            if radius >= 1.5:
                painter.setBrush(QColor(255, 255, 255, 235))
                painter.drawEllipse(
                    QPointF(x - 0.15, y - 0.15), radius * 0.28, radius * 0.28
                )
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
        back_cloud.translate(9.0, -7.0)
        back_fill = QLinearGradient(QPointF(0.0, 24.0), QPointF(0.0, 88.0))
        back_fill.setColorAt(0.0, QColor(215, 235, 248))
        back_fill.setColorAt(1.0, QColor(127, 174, 207))
        painter.setOpacity(opacity * 0.48)
        painter.fillPath(back_cloud, back_fill)

        middle_cloud = QPainterPath(cloud)
        middle_cloud.translate(-5.0, 5.0)
        middle_fill = QLinearGradient(QPointF(0.0, 28.0), QPointF(0.0, 90.0))
        middle_fill.setColorAt(0.0, QColor(239, 248, 253))
        middle_fill.setColorAt(1.0, QColor(151, 193, 220))
        painter.setOpacity(opacity * 0.68)
        painter.fillPath(middle_cloud, middle_fill)

        cloud_shadow = QPainterPath(cloud)
        cloud_shadow.translate(0.0, 3.0)
        painter.setOpacity(opacity * 0.65)
        painter.fillPath(cloud_shadow, QColor(35, 78, 112, 100))

        cloud_fill = QLinearGradient(QPointF(0.0, 28.0), QPointF(0.0, 88.0))
        cloud_fill.setColorAt(0.0, QColor(255, 255, 255))
        cloud_fill.setColorAt(0.55, QColor(250, 252, 254))
        cloud_fill.setColorAt(1.0, QColor(178, 208, 228))
        painter.setOpacity(opacity)
        painter.fillPath(cloud, cloud_fill)
        painter.restore()

    @staticmethod
    def _foreground_cloud_path():
        cloud = QPainterPath()
        cloud.setFillRule(Qt.WindingFill)
        cloud.addEllipse(QRectF(-24.0, 58.0, 58.0, 34.0))
        cloud.addEllipse(QRectF(12.0, 56.0, 58.0, 36.0))
        cloud.addEllipse(QRectF(55.0, 59.0, 56.0, 34.0))
        cloud.addEllipse(QRectF(97.0, 56.0, 58.0, 36.0))
        cloud.addEllipse(QRectF(143.0, 58.0, 58.0, 34.0))
        cloud.addRoundedRect(QRectF(-24.0, 66.0, 230.0, 26.0), 13.0, 13.0)
        return cloud

    def _draw_foreground_clouds(self, painter, progress, opacity):
        if opacity <= 0.0:
            return
        painter.save()
        painter.translate(-10.0 * progress, 36.0 * progress)
        painter.setPen(Qt.NoPen)
        cloud = self._foreground_cloud_path()

        cloud_shadow = QPainterPath(cloud)
        cloud_shadow.translate(0.0, 2.4)
        painter.setOpacity(opacity * 0.72)
        painter.fillPath(cloud_shadow, QColor(25, 65, 98, 115))

        cloud_fill = QLinearGradient(QPointF(0.0, 52.0), QPointF(0.0, 90.0))
        cloud_fill.setColorAt(0.0, QColor(255, 255, 255))
        cloud_fill.setColorAt(0.45, QColor(238, 247, 252))
        cloud_fill.setColorAt(1.0, QColor(157, 195, 221))
        painter.setOpacity(opacity * 0.98)
        painter.fillPath(cloud, cloud_fill)
        painter.restore()

    @staticmethod
    def _draw_thumb_shadow(painter, center, radius, night_mix):
        painter.save()
        painter.setPen(Qt.NoPen)
        shadow_center = QPointF(center.x() + 1.2, center.y() + 3.2)
        shadow_radius = radius * 1.18
        shadow = QRadialGradient(shadow_center, shadow_radius)
        shadow.setColorAt(0.0, QColor(3, 10, 24, round(100 + 35 * night_mix)))
        shadow.setColorAt(0.68, QColor(3, 10, 24, round(62 + 25 * night_mix)))
        shadow.setColorAt(1.0, QColor(3, 10, 24, 0))
        painter.setBrush(shadow)
        painter.drawEllipse(shadow_center, shadow_radius, shadow_radius)
        painter.setBrush(QColor(2, 8, 20, round(50 + 20 * night_mix)))
        painter.drawEllipse(QPointF(center.x() + 0.8, center.y() + 2.2), radius, radius)
        painter.restore()

    @staticmethod
    def _draw_sun(painter, center, radius):
        gradient = QRadialGradient(
            QPointF(center.x() - radius * 0.30, center.y() - radius * 0.34),
            radius * 1.45,
        )
        gradient.setColorAt(0.0, QColor(255, 252, 205))
        gradient.setColorAt(0.32, QColor(255, 226, 115))
        gradient.setColorAt(0.70, QColor(250, 184, 52))
        gradient.setColorAt(1.0, QColor(213, 111, 20))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(center, radius, radius)

        sun_rect = QRectF(
            center.x() - radius + 1.4,
            center.y() - radius + 1.4,
            radius * 2.0 - 2.8,
            radius * 2.0 - 2.8,
        )
        painter.setBrush(Qt.NoBrush)
        highlight_pen = QPen(
            QColor(255, 251, 211, 205), 2.2, Qt.SolidLine, Qt.RoundCap
        )
        painter.setPen(highlight_pen)
        painter.drawArc(sun_rect, 48 * 16, 128 * 16)
        shade_pen = QPen(
            QColor(157, 75, 12, 135), 2.5, Qt.SolidLine, Qt.RoundCap
        )
        painter.setPen(shade_pen)
        painter.drawArc(sun_rect.adjusted(0.0, 0.8, 0.0, 0.8), 200 * 16, 140 * 16)
        painter.setPen(QPen(QColor(255, 250, 204, 185), 1.6, Qt.SolidLine, Qt.RoundCap))
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
        gradient.setColorAt(0.35, QColor(239, 243, 246))
        gradient.setColorAt(0.72, QColor(192, 202, 216))
        gradient.setColorAt(1.0, QColor(121, 137, 161))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(center, radius, radius)

        moon_rect = QRectF(
            center.x() - radius + 1.3,
            center.y() - radius + 1.3,
            radius * 2.0 - 2.6,
            radius * 2.0 - 2.6,
        )
        painter.setBrush(Qt.NoBrush)
        highlight_pen = QPen(
            QColor(255, 255, 255, 195), 2.0, Qt.SolidLine, Qt.RoundCap
        )
        painter.setPen(highlight_pen)
        painter.drawArc(moon_rect, 45 * 16, 130 * 16)
        shade_pen = QPen(
            QColor(61, 79, 106, 120), 2.4, Qt.SolidLine, Qt.RoundCap
        )
        painter.setPen(shade_pen)
        painter.drawArc(moon_rect.adjusted(0.0, 0.8, 0.0, 0.8), 200 * 16, 140 * 16)

        craters = (
            (-9.0, -7.0, 5.3),
            (8.0, -12.0, 3.5),
            (10.0, 8.0, 6.0),
            (-10.0, 12.0, 3.2),
            (0.0, 1.0, 2.4),
        )
        for dx, dy, crater_radius in craters:
            crater_center = QPointF(center.x() + dx, center.y() + dy)
            crater_rect = QRectF(
                crater_center.x() - crater_radius,
                crater_center.y() - crater_radius,
                crater_radius * 2.0,
                crater_radius * 2.0,
            )
            crater_fill = QRadialGradient(
                QPointF(
                    crater_center.x() - crater_radius * 0.28,
                    crater_center.y() - crater_radius * 0.32,
                ),
                crater_radius * 1.25,
            )
            crater_fill.setColorAt(0.0, QColor(72, 88, 112, 155))
            crater_fill.setColorAt(0.55, QColor(117, 134, 157, 125))
            crater_fill.setColorAt(1.0, QColor(198, 207, 219, 90))
            painter.setBrush(crater_fill)
            painter.setPen(QPen(QColor(61, 79, 106, 125), 0.9))
            painter.drawEllipse(crater_center, crater_radius, crater_radius)

            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(46, 63, 89, 135), 1.1, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(crater_rect.adjusted(0.5, 0.5, -0.5, -0.5), 35 * 16, 130 * 16)
            painter.setPen(QPen(QColor(255, 255, 255, 160), 1.2, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(crater_rect.adjusted(0.7, 0.7, -0.7, -0.7), 200 * 16, 130 * 16)
