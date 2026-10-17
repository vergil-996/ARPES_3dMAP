import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication, QMenu, QPushButton, QWidget

from bandscope.app_metadata import APP_VERSION
from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.updates.update_controller import UpdateController


class UpdateContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_menu_exposes_manual_update_check(self):
        owner = QWidget()
        controller = SimpleNamespace(check_for_updates=Mock(), open_settings=Mock())
        controller.add_settings_action = lambda menu: UpdateController.add_settings_action(
            controller, menu
        )
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
        settings_action = next(
            action for action in menu.actions() if action.text() == "更新设置…"
        )
        settings_action.trigger()
        controller.open_settings.assert_called_once()
        self.assertFalse(any(action.text() == "更新源" for action in menu.actions()))
        menu.deleteLater()
        owner.deleteLater()

    def test_version_button_menu_uses_the_same_settings_action(self):
        button = QPushButton()
        self.addCleanup(button.deleteLater)
        controller = SimpleNamespace(open_settings=Mock())
        controller.add_settings_action = lambda menu: UpdateController.add_settings_action(
            controller, menu
        )
        analyzer = SimpleNamespace(
            btn_tb_version=button,
            CONTEXT_MENU_STYLE=My3DAnalyzer.CONTEXT_MENU_STYLE,
            update_controller=controller,
        )

        def choose_settings(menu, _pos):
            self.assertEqual([action.text() for action in menu.actions()], ["更新设置…"])
            menu.actions()[0].trigger()

        with patch.object(QMenu, "exec_", choose_settings):
            My3DAnalyzer._show_update_settings_menu(analyzer, QPoint(4, 6))

        controller.open_settings.assert_called_once()


if __name__ == "__main__":
    unittest.main()
