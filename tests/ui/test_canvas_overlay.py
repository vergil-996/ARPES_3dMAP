import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QColor, QImage, QTransform
from PyQt5.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from bandscope.app.qt_bootstrap import configure_qt_plugin_path

configure_qt_plugin_path()

from bandscope.ui import theme
from bandscope.ui.canvas_overlay import CanvasToolOverlay


class CanvasToolOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_overlay(self):
        parent = QWidget()
        parent.resize(800, 600)
        buttons = [QPushButton(name) for name in ("裁剪", "裁空", "导出结果")]
        overlay = CanvasToolOverlay(parent, *buttons)
        self.addCleanup(parent.deleteLater)
        return parent, overlay, buttons

    def test_existing_actions_move_into_the_canvas_tool_group(self):
        _parent, overlay, buttons = self.make_overlay()
        for button, name in zip(buttons, ("裁剪", "裁空", "导出结果")):
            self.assertIs(button.parentWidget(), overlay)
            self.assertEqual(button.text(), "")
            self.assertEqual(button.accessibleName(), name)
            self.assertEqual(button.size().width(), 28)
            self.assertEqual(button.size().height(), 26)

    def test_overlay_uses_a_vertical_tool_group(self):
        _parent, overlay, _buttons = self.make_overlay()
        overlay.set_3d_active(True)
        self.assertIsInstance(overlay.layout(), QVBoxLayout)
        self.assertIsInstance(overlay.preset_container.layout(), QVBoxLayout)
        self.assertGreater(overlay.height(), overlay.width())

    def test_overlay_stays_inside_the_right_edge_after_resize(self):
        parent, overlay, _buttons = self.make_overlay()
        overlay.set_3d_active(True)
        self.assertEqual(overlay.x() + overlay.width(), parent.width() - 12)
        self.assertEqual(overlay.y(), 12)
        parent.resize(420, 360)
        overlay.reposition()
        self.assertEqual(overlay.x() + overlay.width(), parent.width() - 12)
        parent.resize(640, 360)
        overlay.reposition()
        self.assertFalse(overlay.preset_container.isHidden())
        self.assertEqual(overlay.x() + overlay.width(), parent.width() - 12)

    def test_rounded_corners_are_transparent(self):
        _parent, overlay, _buttons = self.make_overlay()
        image = QImage(overlay.size(), QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        visible = overlay.rect().adjusted(2, 2, -2, -2)
        # QWidget.render 将可见区域的左上角放在 targetOffset。
        overlay.render(image, visible.topLeft())
        self.assertEqual(overlay.mask().boundingRect(), visible)
        corners = (visible.topLeft(), visible.topRight(), visible.bottomLeft(), visible.bottomRight())
        for point in corners:
            self.assertEqual(image.pixelColor(point).alpha(), 0)
            self.assertFalse(overlay.mask().contains(point))
        border = QPoint(visible.left(), visible.center().y())
        self.assertEqual(image.pixelColor(border), QColor(theme.BORDER_HEX))
        self.assertEqual(image.pixelColor(border - QPoint(1, 0)).alpha(), 0)

    def test_pixels_inside_the_window_mask_are_opaque(self):
        parent, overlay, _buttons = self.make_overlay()
        parent.show()
        for scale in (1.0, 1.25):
            with self.subTest(scale=scale):
                image = QImage(overlay.size() * scale, QImage.Format_ARGB32)
                image.setDevicePixelRatio(scale)
                image.fill(Qt.transparent)
                overlay.render(image, overlay.mask().boundingRect().topLeft())
                mask = QTransform.fromScale(scale, scale).map(overlay.mask())
                self.assertFalse(mask.isEmpty())
                for y in range(image.height()):
                    for x in range(image.width()):
                        if mask.contains(QPoint(x, y)):
                            self.assertEqual(image.pixelColor(x, y).alpha(), 255, (x, y))

    def test_native_window_clips_the_overlay_after_show_and_resize(self):
        parent, overlay, _buttons = self.make_overlay()
        parent.show()
        widget_mask = overlay.mask()
        self.assertFalse(widget_mask.isEmpty())
        overlay.hide()
        overlay.setAttribute(Qt.WA_NativeWindow)
        overlay.show()
        window = overlay.windowHandle()
        self.assertIsNotNone(window)
        self.assertTrue(overlay.mask().isEmpty())
        self.assertEqual(window.mask(), widget_mask)

        overlay.resize(overlay.width() + 12, overlay.height() + 24)
        visible = overlay.rect().adjusted(2, 2, -2, -2)
        self.assertTrue(overlay.mask().isEmpty())
        self.assertEqual(window.mask().boundingRect(), visible)
        self.assertFalse(window.mask().contains(visible.bottomRight()))
        self.assertTrue(window.mask().contains(QPoint(visible.right(), visible.center().y())))

    def test_plane_buttons_create_slices_and_3d_returns_to_source(self):
        _parent, overlay, _buttons = self.make_overlay()
        overlay.set_3d_active(True)
        slices = []
        cameras = []
        returned = []
        overlay.slice_requested.connect(slices.append)
        overlay.view_preset_requested.connect(cameras.append)
        overlay.return_3d_requested.connect(lambda: returned.append(True))
        overlay.preset_buttons["xy"].click()
        self.assertEqual(slices, ["xy"])
        self.assertEqual(cameras, [])
        self.assertTrue(overlay.preset_buttons["3d"].isChecked())

        overlay.set_3d_active(False)
        overlay.set_return_active(True)
        overlay.preset_buttons["3d"].click()
        self.assertEqual(returned, [True])

    def test_focus_mode_updates_the_existing_button_and_3d_state(self):
        _parent, overlay, _buttons = self.make_overlay()
        overlay.set_3d_active(False)
        self.assertFalse(overlay.reset_button.isEnabled())
        overlay.set_3d_active(True)
        self.assertTrue(overlay.reset_button.isEnabled())
        overlay.set_focus_mode(True)
        self.assertTrue(overlay.focus_button.isChecked())
        self.assertIn("恢复", overlay.focus_button.toolTip())
        overlay.set_focus_mode(False)
        self.assertFalse(overlay.focus_button.isChecked())


if __name__ == "__main__":
    unittest.main()
