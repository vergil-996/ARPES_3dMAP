"""修饰键翻页已移除：Shift / Ctrl 单击不再切换页面。

原先的实现是「按下并松开修饰键且中间没按别的键 = 翻页」，它不知道中间发生过
鼠标拖动，于是视图里的 Ctrl + 左键拖动一松手就翻页。这里锁住移除结果。
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.app.refactored_app import My3DAnalyzer

REMOVED_HELPERS = (
    "_handle_page_shortcut_key_press",
    "_handle_page_shortcut_key_release",
    "_step_page_under_cursor",
    "_step_control_page",
    "_step_left_workspace_page",
    "_reset_page_keyboard_shortcut",
)


class ModifierPageShortcutRemovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _analyzer(self):
        # __new__ 不构造 QWidget 部分：只填 eventFilter 在本次路径上会读到的属性。
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.left_display_stack = None
        analyzer.left_workspace = SimpleNamespace(
            activate_page=Mock(), visible_page_ids=lambda: ["a", "b"], current_page_id="a"
        )
        analyzer._select_control_page = Mock()
        analyzer._page_keyboard_shortcuts_enabled = lambda: True
        return analyzer

    def test_modifier_key_press_never_switches_pages(self):
        for key in (Qt.Key_Shift, Qt.Key_Control):
            analyzer = self._analyzer()
            watched = QWidget()
            self.assertFalse(
                analyzer.eventFilter(watched, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))
            )
            analyzer.left_workspace.activate_page.assert_not_called()
            analyzer._select_control_page.assert_not_called()
            watched.deleteLater()

    def test_page_switch_helpers_are_gone(self):
        for name in REMOVED_HELPERS:
            self.assertFalse(hasattr(My3DAnalyzer, name), name)


if __name__ == "__main__":
    unittest.main()
