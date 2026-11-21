import unittest

import numpy as np
from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.core.display_style import new_display_style
from bandscope.processing.local_enhancement import cumulative_histogram
from bandscope.ui import theme
from bandscope.ui.levels_controls import ColormapRangeEditor, LevelsEditor
from bandscope.ui.page_render_control import RenderControlPage


def drag_to(widget, position):
    event = QMouseEvent(
        QEvent.MouseMove, QPointF(position), Qt.NoButton, Qt.LeftButton, Qt.NoModifier,
    )
    QApplication.sendEvent(widget, event)


class LevelsEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_editor(self):
        editor = LevelsEditor()
        editor.resize(300, editor.height())
        editor.set_statistics(cumulative_histogram(np.array([1.0, 2.0, 2.0, 5.0])), (1.0, 5.0))
        editor.show()
        self.addCleanup(editor.close)
        self.addCleanup(editor.deleteLater)
        return editor

    def test_black_white_drag_preserves_gamma_and_gray_keyboard_direction(self):
        editor = self.make_editor()
        positions = editor._handle_positions()
        self.assertLess(positions["black"], positions["gray"])
        self.assertLess(positions["gray"], positions["white"])

        start = positions["black"]
        original_gamma = editor.gray.value()
        target_x = int(round(start + 12))
        track_y = editor._track_rect().center().y()
        QTest.mousePress(editor, Qt.LeftButton, pos=QPoint(int(round(start)), track_y))
        drag_to(editor, QPoint(target_x, track_y))
        QTest.mouseRelease(editor, Qt.LeftButton, pos=QPoint(target_x, track_y))
        self.assertEqual(editor.gray.value(), original_gamma)
        self.assertGreater(editor.black.value(), 0)
        positions = editor._handle_positions()
        self.assertLessEqual(positions["black"], positions["gray"])
        self.assertLessEqual(positions["gray"], positions["white"])

        editor._active_name = "gray"
        before = editor._info()["gray_pos"]
        QTest.keyClick(editor, Qt.Key_Left)
        self.assertLess(editor._info()["gray_pos"], before)
        self.assertGreater(editor.gray.value(), original_gamma)

        gamma = editor.gray.value()
        white_x = int(round(editor._handle_positions()["white"]))
        QTest.mousePress(editor, Qt.LeftButton, pos=QPoint(white_x, track_y))
        drag_to(editor, QPoint(white_x - 12, track_y))
        QTest.mouseRelease(editor, Qt.LeftButton, pos=QPoint(white_x - 12, track_y))
        self.assertEqual(editor.gray.value(), gamma)

    def test_output_range_survives_palette_switch_and_orders_mouse_drag(self):
        editor = ColormapRangeEditor("gray")
        editor.resize(300, editor.height())
        editor.show()
        self.addCleanup(editor.close)
        self.addCleanup(editor.deleteLater)
        editor.cmap_low.setValue(200)
        editor.cmap_high.setValue(800)
        editor.set_colormap("viridis")
        self.assertEqual(editor.get_range(), (0.2, 0.8))

        low_x = int(round(editor._handle_positions()["cmap_low"]))
        y = editor._track_rect().center().y()
        QTest.mousePress(editor, Qt.LeftButton, pos=QPoint(low_x, y))
        drag_to(editor, QPoint(editor.width() - 2, y))
        QTest.mouseRelease(editor, Qt.LeftButton, pos=QPoint(editor.width() - 2, y))
        self.assertLessEqual(editor.cmap_low.value(), editor.cmap_high.value())
        self.assertEqual(editor.cmap_low.value(), 800)
        editor._active_name = "cmap_high"
        QTest.keyClick(editor, Qt.Key_Right)
        self.assertEqual(editor.cmap_high.value(), 801)

    def test_deleted_editor_does_not_receive_theme_callbacks(self):
        editor = LevelsEditor()
        editor.deleteLater()
        self.app.processEvents()
        original = theme.current_theme()
        try:
            theme.set_theme("dark" if original == "light" else "light")
        finally:
            theme.set_theme(original)

    def test_page_restores_range_and_old_recipe_defaults_to_full_palette(self):
        page = RenderControlPage()
        self.addCleanup(page.close)
        self.addCleanup(page.deleteLater)
        style = new_display_style((10, 60, 90), "线性", (0.2, 0.8))
        page.set_display_style(style)
        self.assertEqual(page.get_display_style()["colormap_range"], (0.2, 0.8))
        saved = page.export_state()
        page.restore_state(saved)
        self.assertEqual(page.get_display_style()["colormap_range"], (0.2, 0.8))
        page.restore_state({
            "s_low": {"value": 7}, "s_gamma": {"value": 61}, "s_up": {"value": 88},
        })
        self.assertEqual(page.get_display_style()["colormap_range"], (0.0, 1.0))


if __name__ == "__main__":
    unittest.main()
