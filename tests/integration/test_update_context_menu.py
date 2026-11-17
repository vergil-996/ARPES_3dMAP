import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, QRect
from PyQt5.QtWidgets import QApplication, QMenu, QPushButton, QWidget

from bandscope.app.refactored_app import My3DAnalyzer


class SettingsMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _analyzer(self):
        button = QPushButton()
        self.addCleanup(button.deleteLater)
        analyzer = SimpleNamespace(
            btn_tb_settings=button,
            CONTEXT_MENU_STYLE=My3DAnalyzer.CONTEXT_MENU_STYLE,
            update_controller=SimpleNamespace(open_settings=Mock()),
            open_plugin_manager=Mock(),
            open_denoise_settings=Mock(),
        )
        return analyzer

    def test_gear_menu_contains_only_the_three_settings_entries(self):
        analyzer = self._analyzer()

        menu = My3DAnalyzer._create_settings_menu(analyzer)
        self.addCleanup(menu.deleteLater)
        actions = {action.text(): action for action in menu.actions()}

        self.assertEqual(
            list(actions),
            ["更新设置…", "插件管理…", "去噪参数设置…"],
        )
        self.assertIs(analyzer.plugin_manager_action, actions["插件管理…"])

        actions["更新设置…"].trigger()
        analyzer.update_controller.open_settings.assert_called_once_with()
        actions["插件管理…"].trigger()
        analyzer.open_plugin_manager.assert_called_once_with()
        actions["去噪参数设置…"].trigger()
        analyzer.open_denoise_settings.assert_called_once_with()

    def test_settings_button_opens_menu_below_the_gear(self):
        analyzer = self._analyzer()
        menu = My3DAnalyzer._create_settings_menu(analyzer)
        self.addCleanup(menu.deleteLater)
        analyzer.settings_menu = menu
        opened = []

        with patch.object(QMenu, "exec_", lambda target, pos: opened.append((target, pos))):
            My3DAnalyzer._show_settings_menu(analyzer)

        expected = analyzer.btn_tb_settings.mapToGlobal(
            analyzer.btn_tb_settings.rect().bottomLeft()
        )
        self.assertEqual(opened, [(menu, expected)])

    def test_canvas_menu_no_longer_contains_moved_settings_entries(self):
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        analyzer = SimpleNamespace(CONTEXT_MENU_STYLE=My3DAnalyzer.CONTEXT_MENU_STYLE)

        menu, global_pos = My3DAnalyzer._create_canvas_context_menu(
            analyzer,
            owner,
            QPoint(4, 6),
        )
        self.addCleanup(menu.deleteLater)

        self.assertEqual(global_pos, owner.mapToGlobal(QPoint(4, 6)))
        self.assertEqual(menu.actions(), [])

    def test_denoise_entry_centers_the_existing_popup_on_the_main_window(self):
        popup = SimpleNamespace(
            adjustSize=Mock(),
            rect=Mock(return_value=QRect(0, 0, 500, 400)),
            show_at=Mock(),
        )
        analyzer = SimpleNamespace(
            denoise_popup=popup,
            frameGeometry=Mock(return_value=QRect(100, 100, 800, 600)),
        )

        My3DAnalyzer.open_denoise_settings(analyzer)

        popup.adjustSize.assert_called_once_with()
        popup.show_at.assert_called_once_with(QPoint(250, 200))


if __name__ == "__main__":
    unittest.main()
