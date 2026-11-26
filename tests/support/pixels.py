"""Device-independent widget rendering helpers for pixel assertions."""

from PyQt5.QtCore import QPoint
from PyQt5.QtGui import QImage, QPainter


def render_unit_image(widget):
    """Render a widget at device ratio 1 so pixels map 1:1 to logical coordinates."""

    image = QImage(widget.size(), QImage.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(1.0)
    image.fill(0)
    painter = QPainter(image)
    try:
        widget.render(painter, QPoint(0, 0))
    finally:
        painter.end()
    return image


__all__ = ["render_unit_image"]
