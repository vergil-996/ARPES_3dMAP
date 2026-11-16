import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QPushButton, QWidget

from bandscope.app.qt_bootstrap import configure_qt_plugin_path

configure_qt_plugin_path()

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

    def test_overlay_stays_inside_the_left_edge_after_resize(self):
        parent, overlay, _buttons = self.make_overlay()
        overlay.reposition()
        self.assertEqual(overlay.x(), 12)
        self.assertEqual(overlay.y(), 12)
        self.assertLessEqual(overlay.x() + overlay.width(), parent.width())
        parent.resize(420, 360)
        overlay.reposition()
        self.assertEqual(overlay.x(), 12)
        self.assertLessEqual(overlay.x() + overlay.width(), parent.width())
        parent.resize(640, 360)
        overlay.set_3d_active(True)
        self.assertFalse(overlay.preset_container.isHidden())
        self.assertLessEqual(overlay.x() + overlay.width(), parent.width())

    def test_view_presets_emit_the_selected_camera(self):
        _parent, overlay, _buttons = self.make_overlay()
        overlay.set_3d_active(True)
        received = []
        overlay.view_preset_requested.connect(received.append)
        overlay.preset_buttons["xy"].click()
        self.assertEqual(received, ["xy"])
        self.assertTrue(overlay.preset_buttons["xy"].isChecked())
        self.assertFalse(overlay.preset_buttons["3d"].isChecked())

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
