import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.support.view_recipe import control_window


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.app.refactored_app import My3DAnalyzer


class SettingsWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_navigation_and_global_preferences(self):
        window = control_window()
        self.addCleanup(window.close)
        for key in ("rendering/quality", "compute_backend"):
            self.addCleanup(window.settings.remove, key)
        window.request_refresh = Mock()
        with patch.object(window, "open_plugin_manager") as plugins:
            window.btn_tb_settings.click()
            dialog = window.settings_dialog
            nav = dialog.navigation
            self.assertEqual(nav.currentItem().text(0), "计算后端")
            self.assertFalse(hasattr(window.page_render, "combo_backend"))
            self.assertFalse(hasattr(window.page_render, "grp_quality"))
            if not window.backend_manager.gpu_available:
                self.assertFalse(dialog.backend.model().item(2).isEnabled())
                self.assertIn("CUDA", dialog.backend.itemData(2, Qt.ToolTipRole))
            dialog.backend.setCurrentText("CPU")
            self.assertEqual(window.backend_manager.mode, "CPU")
            nav.setCurrentItem(nav.topLevelItem(2).child(0))
            dialog.plugins.click()
            plugins.assert_called_once_with()
            nav.setCurrentItem(nav.topLevelItem(2).child(1))
            dialog.updates.click()
            window.update_controller.open_settings.assert_called_once_with()

        nav.setCurrentItem(nav.topLevelItem(0).child(1))
        self.assertEqual(nav.currentItem().text(0), "3D渲染质量")
        dialog.mode.setCurrentIndex(dialog.mode.findData("legacy"))
        self.assertFalse(dialog.interpolation.isEnabled())
        self.assertFalse(dialog.resting.isEnabled())
        dialog.mode.setCurrentIndex(dialog.mode.findData("explicit"))
        dialog.resting.setCurrentIndex(dialog.resting.findData("fine"))
        window.request_refresh.assert_not_called()
        expected = dict(window.render_quality)
        self.assertEqual(expected["resting"], "fine")
        dialog.close()
        self.assertIs(window.open_settings(), dialog)
        self.assertEqual(nav.currentItem().text(0), "3D渲染质量")
        other = control_window()
        self.addCleanup(other.close)
        self.assertEqual(other.render_quality, expected)
        self.assertEqual(other.backend_manager.mode, "CPU")
        window.current_render_context = {"view": "2d"}
        dialog.resting.setCurrentIndex(dialog.resting.findData("standard"))
        window.request_refresh.assert_not_called()
        window.current_render_context = {"view": "3d"}
        dialog.resting.setCurrentIndex(dialog.resting.findData("fine"))
        from bandscope.rendering.refresh_pipeline import RefreshCause, RenderQuality
        window.request_refresh.assert_called_once_with(
            RefreshCause.TRANSFER_FUNCTION, RenderQuality.EXACT, immediate=True
        )

    def test_denoise_details_follow_source_and_edits(self):
        window = control_window()
        self.addCleanup(window.close)
        dialog = window.open_settings()
        nav = dialog.navigation
        nav.setCurrentItem(nav.topLevelItem(1).child(0))
        detail = window.page_control_blank
        saved = detail.export_state()
        detail.wavelet_level_box.setValue(4)
        dialog.denoise.wavelet_level_box.setValue(2)
        dialog.denoise.wavelet_level_box.editingFinished.emit()
        self.assertEqual(detail.get_wavelet_params()["level"], 2)
        dialog.denoise.wavelet_strength_box.setValue(0.987654321)
        dialog.denoise.wavelet_strength_box.editingFinished.emit()
        self.assertEqual(detail.get_wavelet_params()["strength"], 0.987654321)
        detail.restore_state(saved)
        self.assertEqual(dialog.denoise.wavelet_level_box.value(), detail.wavelet_level_box.value())

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

if __name__ == "__main__":
    unittest.main()
