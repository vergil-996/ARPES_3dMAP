import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication, QWidget

from app_metadata import APP_VERSION
from refactored_app import My3DAnalyzer


class UpdateContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_menu_exposes_manual_update_check(self):
        owner = QWidget()
        controller = SimpleNamespace(check_for_updates=Mock())
        analyzer = SimpleNamespace(
            CONTEXT_MENU_STYLE=My3DAnalyzer.CONTEXT_MENU_STYLE,
            denoise_popup=SimpleNamespace(show_at=Mock()),
            waterfall_popup=SimpleNamespace(show_at=Mock()),
            update_controller=controller,
        )

        menu, _global_pos = My3DAnalyzer._create_settings_context_menu(
            analyzer,
            owner,
            QPoint(4, 6),
        )
        update_action = next(
            action
            for action in menu.actions()
            if action.text() == f"检查更新（当前 v{APP_VERSION}）"
        )

        update_action.trigger()

        controller.check_for_updates.assert_called_once_with(manual=True)
        menu.deleteLater()
        owner.deleteLater()


if __name__ == "__main__":
    unittest.main()
