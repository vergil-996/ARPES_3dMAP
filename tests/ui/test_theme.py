import unittest
from unittest.mock import patch

from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QPushButton, QSlider, QWidget
from siui.components.combobox_ import SiCapsuleComboBox

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

        self.assertIn("palette(highlight)", button.styleSheet())
        self.assertIn("palette(alternate-base)", label.styleSheet())
        self.assertEqual(switch.style_data, original_style)
        self.assertEqual(switch.style_data.background_color_starting.name(), theme.ACCENT.lower())
        self.assertIn("palette(base)", QApplication.instance().styleSheet())
        self.assertEqual(
            QApplication.instance().palette().color(QPalette.Highlight).name().upper(),
            theme.ACCENT,
        )
        palette = QApplication.instance().palette()
        self.assertEqual(palette.color(QPalette.Window).name().upper(), theme.BG_0)
        self.assertEqual(palette.color(QPalette.Base).name().upper(), theme.BG_1)
        self.assertEqual(palette.color(QPalette.Button).name().upper(), theme.BG_3)
        theme.set_theme("dark")
        self.assertIn("palette(highlight)", button.styleSheet())
        self.assertIn("palette(alternate-base)", label.styleSheet())

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

        self.assertIn("palette(button)", dialog.styleSheet())
        self.assertNotIn("palette(bright-text)", dialog.styleSheet())

    def test_light_combo_text_is_black_after_menu_rebuild(self):
        theme.set_theme("light")
        combo = SiCapsuleComboBox()
        self.addCleanup(combo.deleteLater)
        combo.addItems(["线性", "对数"])
        theme.raise_well_on_card(combo)

        def menu_labels():
            return [item._name_label for item in combo._menu._widgets.values()]

        combo.sizeHint()
        combo._menu.aboutToShow.emit()

        self.assertIn("color: #000000;", combo.lineEdit().styleSheet())
        self.assertIn("QComboBox { color: palette(text);", self.app.styleSheet())
        self.assertEqual(self.app.palette().color(QPalette.Text).name(), "#000000")
        self.assertEqual(len(menu_labels()), 2)
        for label in menu_labels():
            self.assertIn("color: #000000;", label.styleSheet())

        combo.addItem("幂函数")
        combo.sizeHint()
        combo._menu.aboutToShow.emit()

        self.assertEqual(len(menu_labels()), 3)
        for label in menu_labels():
            self.assertIn("color: #000000;", label.styleSheet())

        theme.set_theme("dark")
        self.assertIn("color: palette(window-text);", combo.lineEdit().styleSheet())
        for label in menu_labels():
            self.assertIn("color: palette(window-text);", label.styleSheet())

    def test_combo_theme_can_preserve_existing_layout(self):
        theme.set_theme("light")
        combo = SiCapsuleComboBox()
        self.addCleanup(combo.deleteLater)
        combo.addItem("BayesShrink")
        original_margins = combo.lineEdit()._title_text_margins

        theme.raise_well_on_card(combo, adjust_layout=False)
        theme.set_theme("dark")
        theme.set_theme("light")

        self.assertEqual(combo.lineEdit()._title_text_margins, original_margins)
        self.assertIn("color: #000000;", combo.lineEdit().styleSheet())

    def test_repeated_switch_does_not_reload_application_or_palette_styles(self):
        root = QWidget()
        self.addCleanup(root.deleteLater)
        label = QLabel("标题", root)
        label.setStyleSheet(f"color: {theme.TEXT_1}; background: {theme.BG_2};")
        theme.set_theme("light")
        application_style = self.app.styleSheet()
        label_style = label.styleSheet()

        with patch.object(self.app, "setStyleSheet", wraps=self.app.setStyleSheet) as set_style:
            theme.set_theme("dark")

        set_style.assert_not_called()
        self.assertEqual(self.app.styleSheet(), application_style)
        self.assertEqual(label.styleSheet(), label_style)
        self.assertIn("palette(alternate-base)", label_style)
        self.assertEqual(self.app.palette().color(QPalette.AlternateBase).name().upper(), theme.BG_2)
        accent_soft = self.app.palette().color(QPalette.LinkVisited)
        self.assertEqual(accent_soft.getRgb()[:3], (255, 255, 255))
        self.assertAlmostEqual(accent_soft.alphaF(), 0.075, places=3)

    def test_widget_with_explicit_palette_follows_theme(self):
        root = QWidget()
        self.addCleanup(root.deleteLater)
        root.setPalette(QPalette(root.palette()))
        root.setStyleSheet("background: palette(window);")
        root.resize(32, 32)
        root.show()

        theme.set_theme("light")
        self.app.processEvents()

        self.assertEqual(root.palette().color(QPalette.Window).name().upper(), theme.BG_0)
        self.assertEqual(root.palette().color(QPalette.AlternateBase).name().upper(), theme.BG_2)
        self.assertEqual(root.grab().toImage().pixelColor(8, 8).name().upper(), theme.BG_0)

    def test_style_attribute_shadowing_does_not_break_theme_refresh(self):
        root = QWidget()
        self.addCleanup(root.deleteLater)
        root.style = object()

        theme.set_theme("light")

        self.assertEqual(root.palette().color(QPalette.Window).name().upper(), theme.BG_0)

    def test_marked_native_slider_without_style_data_is_ignored(self):
        slider = QSlider()
        self.addCleanup(slider.deleteLater)
        slider.setProperty("neutralSlider", True)

        theme.set_theme("light")

        self.assertTrue(slider.property("neutralSlider"))
        self.assertFalse(hasattr(slider, "style_data"))

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
