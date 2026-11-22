from PyQt5.QtCore import QRect, QRectF, QSize, Qt
from PyQt5.QtGui import QIcon, QIconEngine, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QApplication, QLabel, QStyleOption


class FluentIconEngine(QIconEngine):
    def __init__(self, svg_bytes):
        super().__init__()
        self._svg_bytes = bytes(svg_bytes)

    def clone(self):
        return FluentIconEngine(self._svg_bytes)

    def actualSize(self, size, mode, state):
        return QSize(size)

    @staticmethod
    def _render(svg_bytes, painter, bounds=None):
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        renderer = QSvgRenderer(svg_bytes)
        if bounds is None:
            renderer.render(painter)
        else:
            renderer.render(painter, bounds)

    def _normal_pixmap(self, size):
        if size.width() <= 0 or size.height() <= 0:
            return QPixmap()
        pixmap = QPixmap(size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        self._render(self._svg_bytes, painter)
        painter.end()
        return pixmap

    def pixmap(self, size, mode, state):
        pixmap = self._normal_pixmap(QSize(size))
        if pixmap.isNull() or mode == QIcon.Normal:
            return pixmap
        app = QApplication.instance()
        if app is None:
            return pixmap
        return app.style().generatedIconPixmap(mode, pixmap, QStyleOption())

    @staticmethod
    def _physical_extent(logical_extent, scale):
        if logical_extent <= 0 or scale == 0:
            return 0
        return max(1, int(logical_extent * abs(scale) + 0.5))

    def paint(self, painter, rect, mode, state):
        painter.save()
        try:
            if mode == QIcon.Normal:
                self._render(self._svg_bytes, painter, QRectF(rect))
                return
            transform = painter.deviceTransform()
            scale = abs(transform.m11())
            pixel_size = QSize(
                self._physical_extent(rect.width(), transform.m11()),
                self._physical_extent(rect.height(), transform.m22()),
            )
            pixmap = self.pixmap(pixel_size, mode, state)
            if pixmap.isNull():
                return
            if scale > 0:
                pixmap.setDevicePixelRatio(scale)
            painter.drawPixmap(rect, pixmap)
        finally:
            painter.restore()


class FluentIconLabel(QLabel):
    def __init__(self, parent=None, name="", size=16, color=None):
        super().__init__(parent)
        self._icon_name = ""
        self._icon_size = int(size)
        self._icon_color = color
        self._icon = QIcon()
        self.setAlignment(Qt.AlignCenter)
        if name:
            self.set_fluent_icon(name, size, color)

    def sizeHint(self):
        if self._icon.isNull():
            return super().sizeHint()
        margins = self.contentsMargins()
        return QSize(
            self._icon_size + margins.left() + margins.right(),
            self._icon_size + margins.top() + margins.bottom(),
        )

    def icon(self):
        return self._icon

    def set_fluent_icon(self, name, size=None, color=None):
        self._icon_name = str(name or "")
        if size is not None:
            self._icon_size = int(size)
        self._icon_color = color
        super().clear()
        self.apply_theme()
        self.updateGeometry()

    def clear(self):
        self._icon_name = ""
        self._icon = QIcon()
        super().clear()
        self.updateGeometry()

    def apply_theme(self):
        from bandscope.ui import theme

        self._icon = (
            theme.fluent_icon(self._icon_name, self._icon_size, self._icon_color)
            if self._icon_name else QIcon()
        )
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._icon.isNull():
            return
        rect = QRect(0, 0, self._icon_size, self._icon_size)
        rect.moveCenter(self.rect().center())
        painter = QPainter(self)
        self._icon.paint(painter, rect)
