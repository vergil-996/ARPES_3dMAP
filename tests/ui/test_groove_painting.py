import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QRect, QRectF, Qt
from PyQt5.QtGui import QColor, QImage, QPainter, QPen
from PyQt5.QtWidgets import QApplication

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.ui import theme
from bandscope.ui.groove_painting import paint_groove

configure_qt_plugin_path()


class GroovePaintingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_theme = theme.current_theme()
        self.addCleanup(theme.set_theme, self.previous_theme)

    def test_content_is_clipped_to_the_groove_and_receives_inner_shading(self):
        for name in ("light", "dark"):
            with self.subTest(theme=name):
                theme.set_theme(name)
                image = QImage(60, 20, QImage.Format_ARGB32)
                image.fill(Qt.transparent)
                painter = QPainter(image)
                painter.setRenderHint(QPainter.Antialiasing)
                rect = QRect(8, 5, 44, 10)
                received = []

                def content(content_painter, content_rect):
                    received.append(QRectF(content_rect))
                    content_painter.fillRect(image.rect(), QColor("#d43b3b"))

                paint_groove(painter, rect, content=content)
                painter.end()

                self.assertEqual(received, [QRectF(rect)])
                self.assertEqual(QColor.fromRgba(image.pixel(0, 0)).alpha(), 0)
                above = QColor(image.pixel(30, 7))
                below = QColor(image.pixel(30, 13))
                self.assertLess(above.lightness(), below.lightness())

    def test_qrect_input_and_content_changes_do_not_leak_painter_state(self):
        image = QImage(40, 20, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setPen(QPen(QColor("#abcdef"), 3))
        painter.setBrush(QColor("#123456"))
        painter.setClipRect(QRect(0, 0, 35, 20))
        original_pen = painter.pen()
        original_brush = painter.brush()
        original_clip = painter.clipRegion()

        def content(content_painter, _rect):
            content_painter.setPen(QPen(QColor("#ffffff"), 1))
            content_painter.setClipRect(QRect(0, 0, 1, 1))

        paint_groove(painter, QRect(5, 5, 30, 10), radius=3, content=content)

        self.assertEqual(painter.pen(), original_pen)
        self.assertEqual(painter.brush(), original_brush)
        self.assertEqual(painter.clipRegion(), original_clip)
        painter.end()


if __name__ == "__main__":
    unittest.main()
