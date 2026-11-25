from __future__ import annotations

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QBrush, QColor, QLinearGradient, QPainterPath, QPen

from bandscope.ui import theme


def _mixed(first, second, amount):
    amount = max(0.0, min(1.0, float(amount)))
    return QColor(*(
        round(start + (end - start) * amount)
        for start, end in zip(first.getRgb(), second.getRgb())
    ))


def paint_groove(painter, rect, radius=None, *, content=None):
    rect = QRectF(rect)
    if rect.isEmpty():
        return
    if radius is None:
        radius = rect.height() / 2.0
    radius = max(0.0, min(float(radius), rect.width() / 2.0, rect.height() / 2.0))
    dark = theme.current_theme() == "dark"

    painter.save()
    try:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 38 if dark else 24))
        painter.drawRoundedRect(rect.translated(0.0, 1.0), radius, radius)

        fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        fill.setColorAt(0.0, _mixed(
            theme.token_color("BG_4"), theme.token_color("BG_0"), 0.48,
        ))
        fill.setColorAt(1.0, _mixed(
            theme.token_color("BG_4"), theme.token_color("BG_3"), 0.42,
        ))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, radius, radius)

        groove = QPainterPath()
        groove.addRoundedRect(rect, radius, radius)
        painter.save()
        try:
            painter.setClipPath(groove)
            if content is not None:
                painter.save()
                try:
                    content(painter, rect)
                finally:
                    painter.restore()

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
        finally:
            painter.restore()

        edge = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        edge.setColorAt(0.0, QColor(255, 255, 255, 55 if dark else 175))
        edge.setColorAt(0.48, theme.token_color("BORDER_HEX"))
        edge.setColorAt(1.0, QColor(0, 0, 0, 115 if dark else 42))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QBrush(edge), 0.9))
        painter.drawRoundedRect(
            rect.adjusted(0.45, 0.45, -0.45, -0.45),
            max(0.0, radius - 0.45), max(0.0, radius - 0.45),
        )


    finally:
        painter.restore()


__all__ = ["paint_groove"]
