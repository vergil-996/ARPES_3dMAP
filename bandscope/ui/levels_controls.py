from __future__ import annotations

import numpy as np
from matplotlib import colormaps
from PyQt5 import sip
from PyQt5.QtCore import QPointF, QRect, QRectF, Qt
from PyQt5.QtGui import QColor, QLinearGradient, QPainter, QPen
from PyQt5.QtWidgets import QLabel, QSlider, QToolTip, QWidget

import bandscope.ui.theme as theme
from bandscope.processing.display_transfer import gamma_for_gray_position, level_info


class _HandleEditor(QWidget):
    def __init__(self, height, parent=None):
        super().__init__(parent)
        self.setFixedHeight(int(height))
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self._active_name = None
        self._dragging = False
        self._handle_fills = {}
        theme.changed.connect(self._on_theme_changed)
        self.destroyed.connect(self._disconnect_theme)

    def _disconnect_theme(self, *_args):
        theme.changed.disconnect(self._on_theme_changed)

    def _on_theme_changed(self, _name):
        if not sip.isdeleted(self):
            self.update()

    def _make_proxy(self, name, maximum, value):
        slider = QSlider(Qt.Horizontal, self)
        slider.setRange(0, int(maximum))
        slider.setValue(int(value))
        slider._value_label = QLabel(str(slider.value()), self)
        slider._value_label.hide()
        slider.hide()
        slider.valueChanged.connect(lambda _value, key=name: self._proxy_changed(key))
        setattr(self, name, slider)
        return slider

    def _proxy_changed(self, name):
        slider = getattr(self, name)
        slider._value_label.setText(str(int(slider.value())))
        self.update()

    def sync_from_proxies(self):
        for name in self._handle_fills:
            slider = getattr(self, name)
            slider._value_label.setText(str(int(slider.value())))
        self.update()

    def _track_rect(self):
        raise NotImplementedError

    def _handle_positions(self):
        raise NotImplementedError

    def _set_fraction(self, name, fraction):
        raise NotImplementedError

    def _step_handle(self, name, step):
        raise NotImplementedError

    def _handle_at(self, position):
        positions = self._handle_positions()
        if not positions:
            return None
        name = min(positions, key=lambda key: abs(position.x() - positions[key]))
        slider = getattr(self, name)
        if not slider.isEnabled() or abs(position.x() - positions[name]) > 12:
            return None
        if abs(position.y() - self._track_rect().center().y()) > 18:
            return None
        return name

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        name = self._handle_at(event.pos())
        if name is None:
            return super().mousePressEvent(event)
        self._active_name = name
        self._dragging = True
        self.setFocus(Qt.MouseFocusReason)
        getattr(self, name).sliderPressed.emit()
        self._set_fraction(name, self._fraction_at(event.pos().x()))
        event.accept()
        self.update()

    def mouseMoveEvent(self, event):
        if self._dragging and self._active_name is not None:
            self._set_fraction(self._active_name, self._fraction_at(event.pos().x()))
            event.accept()
            return
        self._update_hover(event.pos())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.LeftButton:
            self._dragging = False
            getattr(self, self._active_name).sliderReleased.emit()
            event.accept()
            self._update_hover(event.pos())
            return
        super().mouseReleaseEvent(event)

    def _fraction_at(self, x):
        track = self._track_rect()
        return float(np.clip((float(x) - track.left()) / max(track.width(), 1), 0.0, 1.0))

    def keyPressEvent(self, event):
        if self._active_name is None or not getattr(self, self._active_name).isEnabled():
            return super().keyPressEvent(event)
        steps = {
            Qt.Key_Left: -1, Qt.Key_Down: -1,
            Qt.Key_Right: 1, Qt.Key_Up: 1,
            Qt.Key_PageDown: -10, Qt.Key_PageUp: 10,
        }
        if event.key() in steps:
            self._step_handle(self._active_name, steps[event.key()])
            getattr(self, self._active_name).sliderReleased.emit()
            event.accept()
            self.update()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        if self._active_name is None:
            self._active_name = next(iter(self._handle_fills), None)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def leaveEvent(self, event):
        QToolTip.hideText()
        super().leaveEvent(event)

    def _update_hover(self, position):
        name = self._handle_at(position)
        self.setCursor(Qt.PointingHandCursor if name is not None else Qt.ArrowCursor)

    def _draw_handle(self, painter, name, x, y, text):
        fill = QColor(self._handle_fills[name])
        contrast = QColor("#111111") if fill.lightnessF() > 0.55 else QColor("#FFFFFF")
        center = QPointF(float(x), float(y))
        painter.setPen(QPen(QColor(theme.BG_0), 4.0))
        painter.setBrush(fill)
        painter.drawEllipse(center, 7.0, 7.0)
        painter.setPen(QPen(contrast, 1.2))
        painter.drawEllipse(center, 6.0, 6.0)
        if self._active_name == name and self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(theme.ACCENT_HOVER), 1.2))
            painter.drawEllipse(center, 9.0, 9.0)
        painter.setPen(contrast)
        font = painter.font()
        font.setPointSizeF(7.0)
        painter.setFont(font)
        painter.drawText(QRectF(x - 7, y - 7, 14, 14), Qt.AlignCenter, text)


class LevelsEditor(_HandleEditor):
    def __init__(self, parent=None):
        super().__init__(94, parent)
        self._histogram = None
        self._data_range = None
        self._handle_fills = {"black": "#090909", "gray": "#858585", "white": "#FFFFFF"}
        self.black = self._make_proxy("black", 100, 0)
        self.white = self._make_proxy("white", 100, 100)
        self.gray = self._make_proxy("gray", 100, 50)
        self._active_name = "gray"
        self.setAccessibleName("输入色阶：黑、灰、白三滑块")
        self.setToolTip("拖动黑、灰、白点调整输入强度；方向键微调。灰点向左提亮，向右压暗。")

    def set_statistics(self, histogram, data_range=None):
        self._histogram = histogram
        self._data_range = None if data_range is None else tuple(float(v) for v in data_range)
        self.update()

    def _track_rect(self):
        return QRect(10, 61, max(1, self.width() - 20), 9)

    def _histogram_rect(self):
        return QRect(10, 3, max(1, self.width() - 20), 48)

    def _axis_range(self):
        values = []
        if self._data_range is not None:
            values.extend(self._data_range[:2])
        if self._histogram is not None and self._histogram.get("data_range") is not None:
            values.extend(self._histogram["data_range"][:2])
        low, high = (min(values), max(values)) if values else (0.0, 1.0)
        if not np.isfinite([low, high]).all():
            return 0.0, 1.0
        if high <= low:
            margin = max(abs(low) * 0.005, 0.5)
            low, high = low - margin, high + margin
        return float(low), float(high)

    def _info(self):
        levels = tuple(float(getattr(self, name).value()) for name in ("black", "gray", "white"))
        return level_info(self._data_range or self._axis_range(), levels)

    def _x_for_value(self, value):
        low, high = self._axis_range()
        track = self._histogram_rect()
        return track.left() + (float(value) - low) / (high - low) * track.width()

    def _handle_positions(self):
        info = self._info()
        return {name: self._x_for_value(info[f"{name}_value"])
                for name in ("black", "gray", "white")}

    def _set_fraction(self, name, fraction):
        info = self._info()
        low, high = self._axis_range()
        intensity = low + fraction * (high - low)
        value = (intensity - info["data_min"]) / info["span"] * 100.0
        self._set_percent(name, value, info)

    def _set_percent(self, name, value, info=None):
        info = info or self._info()
        if name == "gray":
            gray = float(np.clip(value, info["black_pos"] * 100.0, info["white_pos"] * 100.0))
            gamma = gamma_for_gray_position(
                info["black_pos"] * 100.0, info["white_pos"] * 100.0, gray,
            )
            self.gray.setValue(int(round(gamma)))
            return
        limit = info["gray_pos"] * 100.0
        if name == "black":
            value = min(float(np.clip(value, 0.0, 100.0)), limit)
        else:
            value = max(float(np.clip(value, 0.0, 100.0)), limit)
        getattr(self, name).setValue(int(round(value)))

    def _step_handle(self, name, step):
        info = self._info()
        if name == "gray":
            self._set_percent(name, info["gray_pos"] * 100.0 + step, info)
        else:
            self._set_percent(name, getattr(self, name).value() + step, info)

    def _update_hover(self, position):
        super()._update_hover(position)
        if self._histogram is None or not self._histogram_rect().contains(position):
            return
        thresholds = np.asarray(self._histogram.get("thresholds", ()), dtype=float)
        counts = np.asarray(self._histogram.get("counts", ()), dtype=np.int64)
        if thresholds.size == 0:
            QToolTip.showText(self.mapToGlobal(position), "没有有限强度点", self)
            return
        low, high = self._axis_range()
        value = low + self._fraction_at(position.x()) * (high - low)
        index = int(np.argmin(np.abs(thresholds - value)))
        QToolTip.showText(
            self.mapToGlobal(position),
            f"强度 ≥ {thresholds[index]:.6g}\n累计点数：{int(counts[index])}", self,
        )

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        histogram_rect = self._histogram_rect()
        painter.fillRect(histogram_rect, QColor(theme.BG_2))
        if self._histogram is not None:
            thresholds = np.asarray(self._histogram.get("thresholds", ()), dtype=float)
            counts = np.asarray(self._histogram.get("counts", ()), dtype=float)
            maximum = float(np.max(counts)) if counts.size else 0.0
            if thresholds.size and maximum > 0.0:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(theme.TEXT_2))
                for index, (threshold, count) in enumerate(zip(thresholds, counts)):
                    left = self._x_for_value(threshold)
                    right = (self._x_for_value(thresholds[index + 1])
                             if index + 1 < thresholds.size else histogram_rect.right() + 1)
                    height = (histogram_rect.height() - 3) * count / maximum
                    painter.drawRect(QRectF(left, histogram_rect.bottom() - height,
                                            max(1.0, right - left + 0.2), height))
            elif thresholds.size == 0:
                painter.setPen(QColor(theme.TEXT_3))
                painter.drawText(histogram_rect, Qt.AlignCenter, "无有效强度")
        painter.setPen(QPen(QColor(theme.BORDER_HEX), 1.0))
        painter.drawLine(histogram_rect.bottomLeft(), histogram_rect.bottomRight())
        track = self._track_rect()
        painter.fillRect(track, QColor(theme.BG_4))
        positions = self._handle_positions()
        painter.save()
        painter.setClipRect(track)
        painter.fillRect(QRectF(positions["black"], track.top(),
                                max(0.0, positions["gray"] - positions["black"]), track.height()),
                         QColor("#050505"))
        painter.fillRect(QRectF(positions["gray"], track.top(),
                                max(0.0, positions["white"] - positions["gray"]), track.height()),
                         QColor("#888888"))
        painter.restore()
        painter.setPen(QPen(QColor(theme.BORDER_HEX), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(track)
        for name, text in (("black", "黑"), ("gray", "灰"), ("white", "白")):
            self._draw_handle(painter, name, positions[name], track.center().y(), text)


class ColormapRangeEditor(_HandleEditor):
    def __init__(self, cmap="magma", parent=None):
        super().__init__(54, parent)
        self._cmap = str(cmap)
        self._handle_fills = {"cmap_low": "#090909", "cmap_high": "#FFFFFF"}
        self.cmap_low = self._make_proxy("cmap_low", 1000, 0)
        self.cmap_high = self._make_proxy("cmap_high", 1000, 1000)
        self._active_name = "cmap_low"
        self.setAccessibleName("输出色带：黑、白双滑块")
        self.setToolTip("选择原色带的输出子区间；方向键以 0.1% 微调。")

    def set_colormap(self, cmap):
        self._cmap = str(cmap)
        self.update()

    def get_range(self):
        return self.cmap_low.value() / 1000.0, self.cmap_high.value() / 1000.0

    def _track_rect(self):
        return QRect(10, 7, max(1, self.width() - 20), 18)

    def _handle_positions(self):
        track = self._track_rect()
        low, high = self.get_range()
        return {"cmap_low": track.left() + low * track.width(),
                "cmap_high": track.left() + high * track.width()}

    def _set_fraction(self, name, fraction):
        value = int(round(float(np.clip(fraction, 0.0, 1.0)) * 1000.0))
        if name == "cmap_low":
            value = min(value, self.cmap_high.value())
        else:
            value = max(value, self.cmap_low.value())
        getattr(self, name).setValue(value)

    def _step_handle(self, name, step):
        self._set_fraction(name, (getattr(self, name).value() + step) / 1000.0)

    def _update_hover(self, position):
        super()._update_hover(position)
        name = self._handle_at(position)
        if name is not None:
            value = getattr(self, name).value() / 10.0
            QToolTip.showText(self.mapToGlobal(position), f"原色带位置：{value:.1f}%", self)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = self._track_rect()
        palette = colormaps.get_cmap(self._cmap)
        gradient = QLinearGradient(float(track.left()), 0.0, float(track.right() + 1), 0.0)
        for position, rgba in zip(np.linspace(0.0, 1.0, 64), palette(np.linspace(0.0, 1.0, 64))):
            gradient.setColorAt(float(position), QColor.fromRgbF(*rgba))
        painter.fillRect(track, gradient)
        positions = self._handle_positions()
        shade = QColor(theme.BG_3)
        shade.setAlpha(175)
        painter.fillRect(QRectF(track.left(), track.top(),
                                max(0.0, positions["cmap_low"] - track.left()), track.height()), shade)
        painter.fillRect(QRectF(positions["cmap_high"], track.top(),
                                max(0.0, track.right() + 1 - positions["cmap_high"]), track.height()), shade)
        painter.setPen(QPen(QColor(theme.BORDER_HEX), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(track)
        for name, text in (("cmap_low", "黑"), ("cmap_high", "白")):
            self._draw_handle(painter, name, positions[name], track.bottom() + 1, text)
        painter.setPen(QColor(theme.TEXT_2))
        font = painter.font()
        font.setPointSizeF(8.0)
        painter.setFont(font)
        for name in ("cmap_low", "cmap_high"):
            x = positions[name]
            painter.drawText(QRectF(x - 38, 35, 76, 16), Qt.AlignCenter,
                             f"{getattr(self, name).value() / 10.0:.1f}%")
