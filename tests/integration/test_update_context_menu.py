import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.support.view_recipe import control_window


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialogButtonBox, QWidget

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.extensions.plugin_dialog import PluginManagerPanel
from bandscope.ui.settings_dialog import SettingsDialog
from bandscope.updates.sources import SOURCE_GITHUB, SOURCE_MATELAB
from bandscope.updates.update_settings_dialog import UpdateSettingsPanel


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
        window.btn_tb_settings.click()
        dialog = window.settings_dialog
        self.assertIsInstance(dialog, SettingsDialog)
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
        plugins = dialog.plugins
        self.assertIsInstance(plugins, PluginManagerPanel)
        self.assertTrue(plugins.isVisible())
        self.assertFalse(plugins.isWindow())
        self.assertIs(plugins.window(), dialog)
        self.assertNotIn(plugins, QApplication.topLevelWidgets())
        self.assertEqual(plugins.tabs.count(), 3)
        self.assertEqual(
            [plugins.tabs.tabText(index) for index in range(plugins.tabs.count())],
            ["已安装", "官方插件", "插件设置"],
        )
        self.assertTrue(plugins.btn_install.isVisible())
        self.assertIs(window.open_plugin_manager(), plugins)
        self.assertIs(window.open_plugin_manager(), plugins)
        self.assertIs(dialog.plugins, plugins)
        self.assertTrue(plugins.isVisible())

        nav.setCurrentItem(nav.topLevelItem(2).child(1))
        updates = dialog.updates
        self.assertIsInstance(updates, UpdateSettingsPanel)
        self.assertTrue(updates.isVisible())
        self.assertTrue(updates.is_active())
        self.assertFalse(updates.isWindow())
        self.assertIs(updates.window(), dialog)
        self.assertNotIn(updates, QApplication.topLevelWidgets())
        self.assertIs(window.update_controller.create_settings_panel(), updates)
        self.assertTrue(updates.source_combo.isVisible())
        self.assertTrue(updates.version_label.isVisible())
        self.assertTrue(updates.check_updates_button.isVisible())
        self.assertTrue(updates.buttons.button(QDialogButtonBox.Save).isVisible())
        self.assertTrue(updates.buttons.button(QDialogButtonBox.Cancel).isVisible())

        nav.setCurrentItem(nav.topLevelItem(0).child(1))
        self.assertFalse(updates.is_active())
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

    def test_reject_and_escape_cleanup_panels_and_restore_state_on_reopen(self):
        window = control_window()
        self.addCleanup(window.close)
        dialog = window.open_settings()
        nav = dialog.navigation
        updates_item = nav.topLevelItem(2).child(1)
        quality_item = nav.topLevelItem(0).child(1)
        nav.setCurrentItem(updates_item)
        plugins = dialog.plugins
        updates = dialog.updates
        saved_source = updates.selected_source()
        draft_source = SOURCE_MATELAB if saved_source != SOURCE_MATELAB else SOURCE_GITHUB

        with patch.object(plugins, "shutdown", wraps=plugins.shutdown) as shutdown:
            updates.source_combo.setCurrentIndex(updates.source_combo.findData(draft_source))
            self.assertNotEqual(updates.selected_source(), saved_source)
            dialog.reject()
            self.app.processEvents()

            self.assertFalse(dialog.isVisible())
            self.assertEqual(updates.selected_source(), saved_source)
            self.assertFalse(updates.is_active())
            shutdown.assert_called_once_with()
            dialog.reject()
            shutdown.assert_called_once_with()

            self.assertIs(window.open_settings(), dialog)
            self.app.processEvents()
            self.assertTrue(dialog.isVisible())
            self.assertIs(dialog.plugins, plugins)
            self.assertIs(dialog.updates, updates)
            self.assertIs(nav.currentItem(), updates_item)
            self.assertTrue(updates.is_active())
            self.assertEqual(updates.selected_source(), saved_source)

            nav.setCurrentItem(quality_item)
            self.assertFalse(updates.is_active())
            updates.source_combo.setCurrentIndex(updates.source_combo.findData(draft_source))
            QTest.keyClick(dialog, Qt.Key_Escape)
            self.app.processEvents()

            self.assertFalse(dialog.isVisible())
            self.assertEqual(updates.selected_source(), saved_source)
            self.assertFalse(updates.is_active())
            self.assertEqual(shutdown.call_count, 2)

            self.assertIs(window.open_settings(), dialog)
            self.app.processEvents()
            self.assertIs(dialog.plugins, plugins)
            self.assertIs(dialog.updates, updates)
            self.assertIs(nav.currentItem(), quality_item)
            self.assertFalse(updates.is_active())
            self.assertEqual(updates.selected_source(), saved_source)
            dialog.select_page("plugins")
            self.assertTrue(plugins.isVisible())
            self.assertFalse(updates.is_active())
            dialog.select_page("updates")
            self.assertTrue(updates.is_active())

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
