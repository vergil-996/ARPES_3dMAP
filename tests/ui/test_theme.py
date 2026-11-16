import unittest

from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QPushButton, QWidget

import bandscope.ui.theme as theme
from bandscope.ui.ui_controls import SyncedSwitch


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        theme.set_theme("dark")
        self.addCleanup(theme.set_theme, "dark")

    def test_only_dark_and_light_palettes_are_available(self):
        self.assertEqual(set(theme.PALETTES), {"dark", "light"})
        self.assertNotEqual(theme.PALETTES["dark"]["ACCENT"], theme.PALETTES["light"]["ACCENT"])
        with self.assertRaises(ValueError):
            theme.set_theme("mint")

    def test_existing_widgets_are_restyled_without_reconstruction(self):
        root = QWidget()
        button = QPushButton("导出", root)
        theme.style_push_button(button, "primary")
        theme.set_button_icon(button, "ic_fluent_arrow_export_regular", on_accent=True)
        label = QLabel("标题", root)
        label.setStyleSheet(f"color: {theme.TEXT_1}; background: {theme.BG_2};")
        switch = SyncedSwitch(root)
        original_style = switch.style_data

        theme.set_theme("light")

        self.assertIn(theme.ACCENT, button.styleSheet())
        self.assertIn(theme.BG_2, label.styleSheet())
        self.assertEqual(switch.style_data, original_style)
        self.assertEqual(switch.style_data.background_color_starting.name(), theme.ACCENT.lower())
        self.assertIn(theme.BG_1, QApplication.instance().styleSheet())
        palette = QApplication.instance().palette()
        self.assertEqual(palette.color(QPalette.Window).name().upper(), theme.BG_0)
        self.assertEqual(palette.color(QPalette.Base).name().upper(), theme.BG_1)
        self.assertEqual(palette.color(QPalette.Button).name().upper(), theme.BG_3)
        theme.set_theme("dark")
        self.assertIn(theme.ACCENT, button.styleSheet())
        self.assertIn(theme.BG_2, label.styleSheet())

    def test_nested_dialog_is_not_restyled_twice(self):
        root = QWidget()
        self.addCleanup(root.deleteLater)
        dialog = QDialog(root)
        combo = QComboBox(dialog)
        combo.addItem("PNG")
        dialog.setStyleSheet(
            f"QComboBox {{ color: {theme.TEXT_1}; background-color: {theme.BG_3}; }}"
        )

        theme.set_theme("light")

        self.assertIn(theme.BG_3, dialog.styleSheet())
        self.assertNotIn(theme.ACCENT_HOVER, dialog.styleSheet())

    def test_bound_listener_does_not_need_a_temporary_method_reference(self):
        class Listener:
            def __init__(self):
                self.names = []

            def on_theme_changed(self, name):
                self.names.append(name)

        listener = Listener()
        theme.changed.connect(listener.on_theme_changed)
        theme.set_theme("light")
        self.assertEqual(listener.names, ["light"])
        theme.changed.disconnect(listener.on_theme_changed)
        theme.set_theme("dark")
        self.assertEqual(listener.names, ["light"])

    def test_light_canvas_is_distinct_from_input_background(self):
        theme.set_theme("light")
        self.assertNotEqual(theme.CANVAS_BG, theme.BG_1)
        self.assertEqual(theme.CANVAS_BG, "#FAFBFB")


if __name__ == "__main__":
    unittest.main()
