"""Draggable annotation layers over a publication preview's static background."""
from __future__ import annotations

from dataclasses import dataclass, field
import math

from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtWidgets import QLabel


@dataclass
class _PreviewLayer:
    pixmap: QPixmap = field(default_factory=QPixmap)
    rect: tuple | None = None
    bounds: tuple | None = None
    interactive: bool = True


class OrientationPreviewLabel(QLabel):
    """Keep figure coordinates independent of preview scaling and scrolling.

    The regular QLabel pixmap is the preview without its draggable annotations.
    Transparent layers move over that background without rerendering the volume.
    Figure Y coordinates run upwards, unlike QWidget coordinates.
    """

    orientationMoved = pyqtSignal(float, float)
    orientationDragFinished = pyqtSignal()
    colorbarMoved = pyqtSignal(float, float)
    colorbarDragFinished = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._layers = {name: _PreviewLayer() for name in ("colorbar", "orientation")}
        self._drag = None
        self.setMouseTracking(True)

    def set_orientation_preview(self, overlay: QPixmap, rect: tuple):
        """Install a full-figure transparent layer and its normalized bounds."""
        self._set_layer("orientation", overlay, rect, rect)

    def set_colorbar_preview(self, overlay: QPixmap, rect: tuple, bounds: tuple | None = None):
        """Use the colorbar rect for its center, and bounds for its labels too."""
        self._set_layer("colorbar", overlay, rect, rect if bounds is None else bounds)

    @staticmethod
    def _valid_rect(rect):
        try:
            x, y, width, height = (float(value) for value in rect)
        except (TypeError, ValueError):
            return None
        if (not all(math.isfinite(value) for value in (x, y, width, height))
                or width <= 0. or height <= 0. or width > 1. or height > 1.):
            return None
        return (x, y, width, height)

    def _set_layer(self, name, overlay, rect, bounds):
        rect, bounds = self._valid_rect(rect), self._valid_rect(bounds)
        if overlay.isNull() or rect is None or bounds is None:
            self._clear_layer(name)
            return
        x, y, width, height = bounds
        source = QRectF(x * overlay.width(), (1. - y - height) * overlay.height(),
                        width * overlay.width(), height * overlay.height())
        layer = self._layers[name]
        layer.pixmap = overlay.copy(source.toAlignedRect())
        layer.rect, layer.bounds = rect, bounds
        self._cancel_drag(name)
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def clear_orientation(self):
        self._clear_layer("orientation")

    def clear_colorbar(self):
        self._clear_layer("colorbar")

    def _clear_layer(self, name):
        layer = self._layers[name]
        layer.pixmap = QPixmap()
        layer.rect = layer.bounds = None
        self._cancel_drag(name)
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def set_orientation_interactive(self, enabled: bool):
        self._set_interactive("orientation", enabled)

    def set_colorbar_interactive(self, enabled: bool):
        self._set_interactive("colorbar", enabled)

    def _set_interactive(self, name, enabled):
        self._layers[name].interactive = bool(enabled)
        if not enabled:
            self._cancel_drag(name)
            self.setCursor(Qt.ArrowCursor)

    def orientation_rect(self):
        return self._layers["orientation"].rect

    def colorbar_rect(self):
        return self._layers["colorbar"].rect

    def set_orientation_center(self, cx_percent: float, cy_percent: float):
        self._set_center("orientation", cx_percent, cy_percent)

    def set_colorbar_center(self, cx_percent: float, cy_percent: float):
        self._set_center("colorbar", cx_percent, cy_percent)

    def _set_center(self, name, cx_percent, cy_percent):
        layer = self._layers[name]
        if layer.rect is None:
            return
        x, y, width, height = layer.rect
        bx, by, bw, bh = layer.bounds
        dx = float(cx_percent) / 100. - (x + width / 2.)
        dy = float(cy_percent) / 100. - (y + height / 2.)
        # The core bar can be narrow; clamp the whole title/tick layer instead.
        dx = min(max(dx, -bx), 1. - bx - bw)
        dy = min(max(dy, -by), 1. - by - bh)
        layer.rect = (x + dx, y + dy, width, height)
        layer.bounds = (bx + dx, by + dy, bw, bh)
        self.update()

    def _cancel_drag(self, name=None):
        if self._drag is not None and (name is None or self._drag[0] == name):
            self._drag = None

    def _image_rect(self) -> QRectF:
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return QRectF()
        content = QRectF(self.contentsRect())
        margin = self.margin()
        content.adjust(margin, margin, -margin, -margin)
        if self.hasScaledContents():
            return content
        ratio = pixmap.devicePixelRatioF()
        width, height = pixmap.width() / ratio, pixmap.height() / ratio
        alignment = self.alignment()
        x, y = content.left(), content.top()
        if alignment & Qt.AlignHCenter:
            x += (content.width() - width) / 2.
        elif alignment & Qt.AlignRight:
            x += content.width() - width
        if alignment & Qt.AlignVCenter:
            y += (content.height() - height) / 2.
        elif alignment & Qt.AlignBottom:
            y += content.height() - height
        return QRectF(x, y, width, height)

    def _marker_rect(self) -> QRectF:
        return self._layer_widget_rect("orientation")

    def _layer_widget_rect(self, name) -> QRectF:
        bounds = self._layers[name].bounds
        if bounds is None:
            return QRectF()
        image = self._image_rect()
        x, y, width, height = bounds
        return QRectF(image.left() + x * image.width(),
                      image.top() + (1. - y - height) * image.height(),
                      width * image.width(), height * image.height())

    def _figure_point(self, position):
        image = self._image_rect()
        if image.width() <= 0. or image.height() <= 0.:
            return None
        return QPointF((position.x() - image.left()) / image.width(),
                       1. - (position.y() - image.top()) / image.height())

    def _can_drag(self, name="orientation"):
        layer = self._layers[name]
        return (layer.interactive and self.isEnabled() and self.isVisible()
                and layer.rect is not None and not layer.pixmap.isNull())

    def _hit_layer(self, position):
        # Match drawing order: the orientation marker sits over the colorbar.
        return next((name for name in ("orientation", "colorbar")
                     if self._can_drag(name)
                     and self._layer_widget_rect(name).contains(QPointF(position))), None)

    def _move_layer(self, position):
        point = self._figure_point(position)
        if self._drag is None or point is None:
            return
        name, anchor, cx, cy = self._drag
        previous = self._layers[name].rect
        self._set_center(name, (cx + point.x() - anchor.x()) * 100.,
                         (cy + point.y() - anchor.y()) * 100.)
        rect = self._layers[name].rect
        if not all(math.isclose(before, after, rel_tol=0., abs_tol=1e-12)
                   for before, after in zip(previous, rect)):
            x, y, width, height = rect
            signal = self.orientationMoved if name == "orientation" else self.colorbarMoved
            signal.emit((x + width / 2.) * 100., (y + height / 2.) * 100.)

    def mousePressEvent(self, event):
        name = self._hit_layer(event.pos())
        if event.button() == Qt.LeftButton and name is not None:
            point = self._figure_point(event.pos())
            x, y, width, height = self._layers[name].rect
            self._drag = (name, point, x + width / 2., y + height / 2.)
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag is not None and self._can_drag(self._drag[0]):
            self._move_layer(event.pos())
            event.accept()
            return
        hit = self._hit_layer(event.pos()) is not None
        self.setCursor(Qt.OpenHandCursor if hit else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag is not None:
            name = self._drag[0]
            self._move_layer(event.pos())
            self._drag = None
            hit = self._hit_layer(event.pos()) is not None
            self.setCursor(Qt.OpenHandCursor if hit else Qt.ArrowCursor)
            signal = (self.orientationDragFinished if name == "orientation"
                      else self.colorbarDragFinished)
            signal.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self._drag is None:
            self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(event)

    def hideEvent(self, event):
        self._cancel_drag()
        self.setCursor(Qt.ArrowCursor)
        super().hideEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            for name in ("colorbar", "orientation"):
                layer = self._layers[name]
                target = self._layer_widget_rect(name)
                if not target.isEmpty() and not layer.pixmap.isNull():
                    painter.drawPixmap(target, layer.pixmap, QRectF(layer.pixmap.rect()))
        finally:
            painter.end()
