from __future__ import annotations

import math
import weakref

import numpy as np
from PyQt5 import sip
from PyQt5.QtCore import QLineF, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PyQt5.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QSlider,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

import bandscope.ui.theme as theme
from bandscope.core.display_style import INDEPENDENT_MODE
from bandscope.processing.display_transfer import independent_opacity_values, level_info


PARAMETER_SPECS = (
    ("threshold", "黑场 / %", 0.0, 100.0, 3, 1000, 0.0, "阈值及以下完全透明"),
    ("transition", "过渡宽度 / %", 0.0, 100.0, 1, 10, 0.0, "过渡终点减去黑场阈值"),
    ("gamma", "透明度 Gamma", 0.1, 4.0, 2, 100, 1.4, "向左拖动手柄提亮弱信号，向右压低"),
    ("density", "整体密度", 0.0, 100.0, 1, 10, 12.0, "上下拖动密度手柄或曲线"),
)


class OpacityCurveCanvas(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setFixedHeight(178)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._active_name = "gamma"
        self._dragging = False
        self._direct_curve = False
        self.setAccessibleName("独立不透明度曲线")
        self.setToolTip("拖动黑场、Gamma、密度和过渡终点；直接上下拖曲线调整密度。")
        reference = weakref.ref(self)

        def on_theme_changed(_name):
            canvas = reference()
            if canvas is not None and not sip.isdeleted(canvas):
                canvas.update()

        self._theme_listener = on_theme_changed
        theme.changed.connect(self._theme_listener)
        self.destroyed.connect(self._disconnect_theme)

    def _disconnect_theme(self, *_args):
        theme.changed.disconnect(self._theme_listener)

    def _plot_rect(self):
        return QRect(32, 10, max(1, self.width() - 44), 118)

    def _axis_maximum(self):
        values = self.editor.values()
        return max(100.0, values["threshold"] + values["transition"])

    def _x(self, intensity):
        plot = self._plot_rect()
        return plot.left() + float(intensity) / self._axis_maximum() * plot.width()

    def _y(self, alpha):
        plot = self._plot_rect()
        return plot.bottom() - float(np.clip(alpha, 0.0, 1.0)) * plot.height()

    def _curve_values(self, intensities):
        values = self.editor.values()
        style = {"display_mode": INDEPENDENT_MODE, "opacity": values}
        info = level_info((0.0, 100.0), (0.0, 50.0, 100.0))
        return independent_opacity_values(np.asarray(intensities, dtype=float), info, style)

    def _gamma_intensity(self):
        values = self.editor.values()
        threshold = values["threshold"]
        if threshold >= 100.0:
            return 100.0
        return threshold + (100.0 - threshold) * 0.5 ** (1.0 / values["gamma"])

    def _handle_positions(self):
        values = self.editor.values()
        plot = self._plot_rect()
        gamma_intensity = self._gamma_intensity()
        return {
            "threshold": QPointF(self._x(values["threshold"]), plot.bottom() + 13),
            "gamma": QPointF(
                self._x(gamma_intensity), self._y(self._curve_values([gamma_intensity])[0]),
            ),
            "density": QPointF(self._x(100.0), self._y(self._curve_values([100.0])[0])),
            "transition": QPointF(
                self._x(values["threshold"] + values["transition"]), plot.bottom() + 31,
            ),
        }

    @staticmethod
    def _distance(position, center):
        return math.hypot(position.x() - center.x(), position.y() - center.y())

    def _handle_at(self, position):
        positions = self._handle_positions()
        values = self.editor.values()
        candidates = []
        for name, radius in (("transition", 9), ("threshold", 9), ("gamma", 10), ("density", 11)):
            if name == "gamma" and values["threshold"] >= 100.0:
                continue
            distance = self._distance(position, positions[name])
            if distance <= radius:
                candidates.append((distance / radius, name))
        return min(candidates)[1] if candidates else None

    def _density_at_y(self, y):
        plot = self._plot_rect()
        alpha = float(np.clip((plot.bottom() - float(y)) / max(plot.height(), 1), 0.0, 0.9999))
        values = self.editor.values()
        threshold = values["threshold"] / 100.0
        transition = values["transition"] / 100.0
        signal = max(1.0 - threshold, 0.0)
        if transition > 0.0:
            position = float(np.clip((1.0 - threshold) / transition, 0.0, 1.0))
            gate = position * position * (3.0 - 2.0 * position)
        else:
            gate = 1.0
        coefficient = signal ** values["gamma"] * gate / 200.0
        density = -math.log1p(-alpha) / coefficient if coefficient > 1.0e-12 else alpha * 100.0
        return float(np.clip(density, 0.0, 100.0))

    def _set_from_position(self, name, position):
        values = self.editor.values()
        plot = self._plot_rect()
        intensity = ((float(position.x()) - plot.left()) / max(plot.width(), 1)
                     * self._axis_maximum())
        if name == "density":
            self.editor.set_parameter("density", self._density_at_y(position.y()))
            return
        if name == "threshold":
            self.editor.set_parameter("threshold", float(np.clip(intensity, 0.0, 100.0)))
            return
        if name == "transition":
            endpoint = float(np.clip(
                intensity, values["threshold"], values["threshold"] + 100.0,
            ))
            self.editor.set_parameter("transition", endpoint - values["threshold"])
            return
        if name == "gamma" and values["threshold"] < 100.0:
            intensity = float(np.clip(intensity, values["threshold"], 100.0))
            fraction = (intensity - values["threshold"]) / (100.0 - values["threshold"])
            fraction = float(np.clip(fraction, 1.0e-9, 1.0 - 1.0e-9))
            gamma = math.log(0.5) / math.log(fraction)
            self.editor.set_parameter("gamma", float(np.clip(gamma, 0.1, 4.0)))

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        name = self._handle_at(event.pos())
        self._direct_curve = False
        if name is None and self._plot_rect().adjusted(-3, -3, 3, 3).contains(event.pos()):
            name = "density"
            self._direct_curve = True
        if name is None:
            return super().mousePressEvent(event)
        self._active_name = name
        self._dragging = True
        self.setFocus(Qt.MouseFocusReason)
        self.editor.display_parameters[name][0].sliderPressed.emit()
        self._set_from_position(name, event.pos())
        event.accept()
        self.update()

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._set_from_position(self._active_name, event.pos())
            event.accept()
            return
        self._update_hover(event.pos())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.LeftButton:
            self._dragging = False
            self.editor.display_parameters[self._active_name][0].sliderReleased.emit()
            self._direct_curve = False
            event.accept()
            self._update_hover(event.pos())
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        steps = {
            Qt.Key_Left: -1, Qt.Key_Down: -1,
            Qt.Key_Right: 1, Qt.Key_Up: 1,
            Qt.Key_PageDown: -10, Qt.Key_PageUp: 10,
        }
        if event.key() not in steps or self._active_name is None:
            return super().keyPressEvent(event)
        name = self._active_name
        slider, _spin, scale = self.editor.display_parameters[name]
        if name == "gamma" and self.editor.values()["threshold"] >= 100.0:
            return super().keyPressEvent(event)
        slider.setValue(int(np.clip(
            slider.value() + steps[event.key()], slider.minimum(), slider.maximum(),
        )))
        slider.sliderReleased.emit()
        event.accept()
        self.update()

    def leaveEvent(self, event):
        QToolTip.hideText()
        super().leaveEvent(event)

    def _update_hover(self, position):
        name = self._handle_at(position)
        self.setCursor(Qt.PointingHandCursor if name is not None else Qt.ArrowCursor)
        if name is None:
            return
        values = self.editor.values()
        labels = {
            "threshold": f"黑场阈值：{values['threshold']:.3f}%",
            "gamma": f"Gamma：{values['gamma']:.2f}；中点：{self._gamma_intensity():.3f}%",
            "density": f"整体密度：{values['density']:.1f}",
            "transition": (
                f"过渡终点：{values['threshold'] + values['transition']:.1f}%\n"
                f"宽度：{values['transition']:.1f}%"
            ),
        }
        QToolTip.showText(self.mapToGlobal(position), labels[name], self)

    def _draw_handle(self, painter, name, center, text):
        accents = {
            "threshold": "#202020", "gamma": theme.ACCENT,
            "density": "#D97706", "transition": theme.TEXT_2,
        }
        fill = QColor(accents[name])
        contrast = QColor("#111111") if fill.lightnessF() > 0.55 else QColor("#FFFFFF")
        painter.setPen(QPen(QColor(theme.BG_0), 3.5))
        painter.setBrush(fill)
        if name == "transition":
            polygon = QPolygonF([
                QPointF(center.x(), center.y() - 7),
                QPointF(center.x() - 7, center.y() + 5),
                QPointF(center.x() + 7, center.y() + 5),
            ])
            painter.drawPolygon(polygon)
        else:
            painter.drawEllipse(center, 7.0, 7.0)
        painter.setPen(QPen(contrast, 1.1))
        painter.setBrush(Qt.NoBrush)
        if name != "transition":
            painter.drawEllipse(center, 5.8, 5.8)
        if self._active_name == name and self.hasFocus():
            painter.setPen(QPen(QColor(theme.ACCENT_HOVER), 1.2))
            painter.drawEllipse(center, 9.0, 9.0)
        painter.setPen(contrast)
        font = painter.font()
        font.setPointSizeF(7.0)
        painter.setFont(font)
        painter.drawText(QRectF(center.x() - 7, center.y() - 7, 14, 14), Qt.AlignCenter, text)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        plot = self._plot_rect()
        painter.fillRect(plot, QColor(theme.BG_2))
        font = painter.font()
        font.setPointSizeF(7.5)
        painter.setFont(font)
        for fraction in (0.0, 0.5, 1.0):
            y = self._y(fraction)
            painter.setPen(QPen(QColor(theme.BORDER_HEX), 0.7, Qt.DotLine))
            painter.drawLine(QLineF(plot.left(), y, plot.right(), y))
            painter.setPen(QColor(theme.TEXT_3))
            painter.drawText(QRectF(0, y - 7, 27, 14), Qt.AlignRight | Qt.AlignVCenter,
                             f"{fraction:.1f}")
        axis_maximum = self._axis_maximum()
        for intensity in (0.0, 50.0, 100.0):
            x = self._x(intensity)
            painter.setPen(QPen(QColor(theme.BORDER_HEX), 0.7, Qt.DotLine))
            painter.drawLine(QLineF(x, plot.top(), x, plot.bottom()))
            painter.setPen(QColor(theme.TEXT_3))
            painter.drawText(QRectF(x - 24, plot.bottom() - 16, 48, 13), Qt.AlignCenter,
                             f"{intensity:.0f}%")
        if axis_maximum > 100.0:
            painter.setPen(QColor(theme.TEXT_3))
            painter.drawText(QRectF(plot.right() - 47, plot.top() + 2, 47, 13),
                             Qt.AlignRight, f"→ {axis_maximum:.1f}%")

        values = self.editor.values()
        threshold_x = self._x(values["threshold"])
        painter.setPen(QPen(QColor(theme.TEXT_3), 0.9, Qt.DashLine))
        painter.drawLine(QLineF(threshold_x, plot.top(), threshold_x, plot.bottom() + 12))
        positions = self._handle_positions()
        transition = positions["transition"]
        painter.drawLine(QLineF(transition.x(), plot.bottom(), transition.x(), transition.y() - 6))

        samples = np.linspace(0.0, axis_maximum, 320)
        alphas = self._curve_values(samples)
        path = QPainterPath(QPointF(self._x(samples[0]), self._y(alphas[0])))
        for intensity, alpha in zip(samples[1:], alphas[1:]):
            path.lineTo(self._x(intensity), self._y(alpha))
        painter.setPen(QPen(QColor(theme.ACCENT), 2.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.setPen(QPen(QColor(theme.BORDER_HEX), 1.0))
        painter.drawRect(plot)
        for name, text in (("threshold", "黑"), ("gamma", "γ"),
                           ("density", "密"), ("transition", "终")):
            self._draw_handle(painter, name, positions[name], text)


class OpacityCurveEditor(QWidget):
    valueChanged = pyqtSignal()
    valueCommitted = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.display_parameters = {}
        self.parameter_labels = {}
        for name, label, minimum, maximum, decimals, scale, value, tooltip in PARAMETER_SPECS:
            slider = QSlider(Qt.Horizontal, self)
            slider.setRange(int(round(minimum * scale)), int(round(maximum * scale)))
            slider.setValue(int(round(value * scale)))
            slider.setToolTip(tooltip)
            slider.hide()
            spin = QDoubleSpinBox(self)
            spin.setRange(minimum, maximum)
            spin.setDecimals(decimals)
            spin.setSingleStep(1.0 / scale)
            spin.setValue(value)
            spin.setKeyboardTracking(False)
            spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
            spin.setAlignment(Qt.AlignCenter)
            spin.setStyleSheet(theme.value_input_qss())
            spin.setToolTip(tooltip)
            spin.setAccessibleName(label)
            self.display_parameters[name] = (slider, spin, scale)
            self.parameter_labels[name] = label
            slider.valueChanged.connect(lambda raw, key=name: self._from_slider(key, raw))
            spin.valueChanged.connect(lambda raw, key=name: self._from_spin(key, raw))
            slider.sliderReleased.connect(self.valueCommitted)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.curve = OpacityCurveCanvas(self)
        layout.addWidget(self.curve)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)
        for index, (name, label, *_rest) in enumerate(PARAMETER_SPECS):
            cell = QWidget(self)
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(2)
            caption = QLabel(label, cell)
            caption.setStyleSheet(theme.field_label_qss())
            cell_layout.addWidget(caption)
            cell_layout.addWidget(self.display_parameters[name][1])
            grid.addWidget(cell, index // 2, index % 2)
        layout.addLayout(grid)
        self.setMinimumHeight(290)

    def values(self):
        return {
            name: slider.value() / float(scale)
            for name, (slider, _spin, scale) in self.display_parameters.items()
        }

    def set_parameter(self, name, value):
        slider, _spin, scale = self.display_parameters[name]
        slider.setValue(int(round(float(value) * scale)))

    def set_values(self, values):
        for name, (slider, spin, scale) in self.display_parameters.items():
            slider.setValue(int(round(float(values[name]) * scale)))
            spin.setValue(float(values[name]))
        self.curve.update()

    def _from_slider(self, name, raw):
        _slider, spin, scale = self.display_parameters[name]
        spin.blockSignals(True)
        spin.setValue(raw / float(scale))
        spin.blockSignals(False)
        self.curve.update()
        self.valueChanged.emit()

    def _from_spin(self, name, raw):
        slider, _spin, scale = self.display_parameters[name]
        slider.blockSignals(True)
        slider.setValue(int(round(raw * scale)))
        slider.blockSignals(False)
        self.curve.update()
        self.valueChanged.emit()

    def sync_from_proxies(self):
        self.curve.update()
