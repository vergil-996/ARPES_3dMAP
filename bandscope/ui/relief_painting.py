from __future__ import annotations

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QBrush, QColor, QLinearGradient, QPainterPath, QPen

from bandscope.ui import theme


def _mixed(first, second, amount):
    amount = max(0.0, min(1.0, float(amount)))
    result = QColor(*(
        round(start + (end - start) * amount)
        for start, end in zip(first.getRgb()[:3], second.getRgb()[:3])
    ))
    result.setAlpha(first.alpha())
    return result


def paint_raised_path(painter, path, fill, border, *, pressed=False, shadow=True):
    path = QPainterPath(path)
    bounds = path.boundingRect()
    if bounds.isEmpty():
        return
    fill = QColor(fill)
    border = QColor(border)
    white = QColor("#FFFFFF")
    black = QColor("#000000")
    top = _mixed(fill, white, 0.28 if fill.lightness() < 150 else 0.52)
    bottom = _mixed(fill, black, 0.24 if fill.lightness() < 150 else 0.18)
    if pressed:
        top, bottom = bottom, top

    painter.save()
    try:
        if shadow:
            shadow_color = QColor(0, 0, 0, 30 if pressed else 62)
            painter.setPen(Qt.NoPen)
            painter.setBrush(shadow_color)
            painter.drawPath(path.translated(0.0, 0.55 if pressed else 1.15))

        gradient = QLinearGradient(bounds.topLeft(), bounds.bottomLeft())
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(0.44, fill)
        gradient.setColorAt(0.56, fill)
        gradient.setColorAt(1.0, bottom)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(path)

        painter.save()
        try:
            painter.setClipPath(path)
            highlight = QColor(255, 255, 255, 42 if pressed else 92)
            painter.setPen(QPen(highlight, 0.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path.translated(0.0, 0.7))
        finally:
            painter.restore()

        edge = QLinearGradient(bounds.topLeft(), bounds.bottomLeft())
        edge.setColorAt(0.0, _mixed(border, white, 0.35))
        edge.setColorAt(0.5, border)
        edge.setColorAt(1.0, _mixed(border, black, 0.30))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QBrush(edge), 0.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(path)
    finally:
        painter.restore()


def paint_raised_rect(painter, rect, radius, fill, border, *, pressed=False, shadow=True):
    rect = QRectF(rect)
    radius = max(0.0, min(float(radius), rect.width() / 2.0, rect.height() / 2.0))
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    paint_raised_path(painter, path, fill, border, pressed=pressed, shadow=shadow)


def paint_raised_ellipse(painter, rect, fill, border, *, pressed=False, shadow=True):
    path = QPainterPath()
    path.addEllipse(QRectF(rect))
    paint_raised_path(painter, path, fill, border, pressed=pressed, shadow=shadow)


__all__ = ["paint_raised_ellipse", "paint_raised_path", "paint_raised_rect"]
