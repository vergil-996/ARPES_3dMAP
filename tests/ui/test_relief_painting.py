import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QApplication

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.ui.relief_painting import paint_raised_path

configure_qt_plugin_path()


class ReliefPaintingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def render(pressed=False):
        image = QImage(48, 32, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(12, 6, 20, 14), 5, 5)
        paint_raised_path(
            painter, path, "#858585", "#555B64", pressed=pressed,
        )
        painter.end()
        return image

    def test_handle_has_lit_top_dark_bottom_and_external_shadow(self):
        image = self.render()

        self.assertEqual(image.pixelColor(22, 13).name(), "#858585")
        self.assertGreater(
            image.pixelColor(22, 9).lightness(), image.pixelColor(22, 13).lightness(),
        )
        self.assertLess(
            image.pixelColor(22, 17).lightness(), image.pixelColor(22, 13).lightness(),
        )
        self.assertGreater(QColor.fromRgba(image.pixel(22, 21)).alpha(), 0)

    def test_pressed_handle_reverses_the_relief(self):
        normal = self.render()
        pressed = self.render(pressed=True)

        self.assertLess(
            pressed.pixelColor(22, 9).lightness(), normal.pixelColor(22, 9).lightness(),
        )
        self.assertGreater(
            pressed.pixelColor(22, 17).lightness(), normal.pixelColor(22, 17).lightness(),
        )

    def test_painter_state_is_restored(self):
        image = QImage(48, 32, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setPen(QPen(QColor("#abcdef"), 3))
        painter.setBrush(QColor("#123456"))
        painter.setClipRect(QRectF(0, 0, 35, 20))
        original_pen = painter.pen()
        original_brush = painter.brush()
        original_clip = painter.clipRegion()
        path = QPainterPath()
        path.addEllipse(QRectF(12, 6, 20, 14))

        paint_raised_path(painter, path, "#858585", "#555B64")

        self.assertEqual(painter.pen(), original_pen)
        self.assertEqual(painter.brush(), original_brush)
        self.assertEqual(painter.clipRegion(), original_clip)
        painter.end()


if __name__ == "__main__":
    unittest.main()
