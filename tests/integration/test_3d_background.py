import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PyQt5.QtWidgets import QApplication

from tests.support.view_recipe import control_window, synthetic_npz
from bandscope.ui import theme


class View3dBackgroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_initial_3d_background_follows_theme(self):
        window = control_window()
        self.addCleanup(window.close)

        self.assertEqual(window.plotter.background_setting, theme.CANVAS_BG)

    def test_new_settings_default_to_light(self):
        previous = control_window()
        previous.settings.remove("ui/theme")
        previous.close()
        theme.initialize_theme("light")

        window = control_window()
        self.addCleanup(window.close)
        self.addCleanup(window._set_theme, "light")

        self.assertEqual(theme.current_theme(), "light")
        self.assertTrue(window.btn_theme_light.isChecked())
        self.assertFalse(window.btn_theme_dark.isChecked())
        self.assertEqual(window.plotter.background_setting, theme.CANVAS_BG)

    def test_loading_data_restores_current_theme_background(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.npz"
            synthetic_npz(path)
            window = control_window()
            self.addCleanup(window.close)
            window.plotter.set_background("black")

            self.assertTrue(window.load_data(str(path)))

            self.assertEqual(window.plotter.background_setting, theme.CANVAS_BG)

    def test_theme_switch_preserves_parameters_and_is_saved(self):
        window = control_window()
        self.addCleanup(window.close)
        self.addCleanup(window._set_theme, "dark")
        window._set_theme("dark")
        window.page_render.s_gamma.setValue(37)

        window._set_theme("light")

        self.assertEqual(window.settings.value("ui/theme"), "light")
        self.assertEqual(window.page_render.s_gamma.value(), 37)
        self.assertEqual(window.plotter.background_setting, theme.CANVAS_BG)
        self.assertTrue(window.btn_theme_light.isChecked())
        self.assertIn("palette(highlight)", window.btn_main_export.styleSheet())
        self.assertIn("palette(base)", window.styleSheet() + QApplication.instance().styleSheet())

    def test_theme_switch_does_not_request_a_data_render(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.npz"
            synthetic_npz(path)
            window = control_window()
            self.addCleanup(window.close)
            original_theme = theme.current_theme()
            self.addCleanup(window._set_theme, original_theme)
            self.assertTrue(window.load_data(str(path)))
            context = window.current_render_context
            window.request_refresh = Mock()
            window._render_active_page = Mock()
            window.volume_session.update_theme = Mock()
            window.axis_crop_selector = Mock()
            window._enhancement_selector = Mock()

            target = "light" if original_theme == "dark" else "dark"
            window._set_theme(target)

            window.request_refresh.assert_not_called()
            window._render_active_page.assert_not_called()
            window.volume_session.update_theme.assert_called_once_with()
            window.axis_crop_selector.update_theme.assert_called_once_with()
            window._enhancement_selector.update_theme.assert_called_once_with()
            self.assertIs(window.current_render_context, context)
            self.assertEqual(window.plotter.background_setting, theme.CANVAS_BG)

    def test_camera_preset_updates_overlay_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.npz"
            synthetic_npz(path)
            window = control_window()
            self.addCleanup(window.close)
            self.assertTrue(window.load_data(str(path)))

            window.canvas_tool_overlay.preset_buttons["3d"].click()
            self.assertTrue(window.canvas_tool_overlay.preset_buttons["3d"].isChecked())


if __name__ == "__main__":
    unittest.main()
