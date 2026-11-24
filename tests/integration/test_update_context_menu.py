import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.support.view_recipe import control_window


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialogButtonBox, QWidget

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.extensions.plugin_dialog import PluginManagerPanel
from bandscope.processing.denoise_config import (
    GAUSSIAN_METHOD_NAME,
    LOW_RANK_METHOD_NAME,
    MEDIAN_METHOD_NAME,
    NLM_METHOD_NAME,
    SG_METHOD_NAME,
    WAVELET_METHOD_NAME,
)
from bandscope.ui.denoise_pipeline import DenoisePipelineWidget
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

        self.assertTrue(dialog.select_page("plugins"))
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

        self.assertTrue(dialog.select_page("updates"))
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
        self.assertTrue(dialog.select_page("updates"))
        updates_item = nav.currentItem()
        quality_item = nav.topLevelItem(0).child(1)
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
        self.assertEqual(
            [nav.topLevelItem(index).text(0) for index in range(nav.topLevelItemCount())],
            ["计算与显示", "应用管理"],
        )
        page_titles = [
            nav.topLevelItem(category).child(page).text(0)
            for category in range(nav.topLevelItemCount())
            for page in range(nav.topLevelItem(category).childCount())
        ]
        self.assertEqual(page_titles, ["计算后端", "3D渲染质量", "插件管理", "更新设置"])
        self.assertFalse(any("去噪" in title for title in page_titles))

        pipeline = window.page_render.denoise_pipeline
        self.assertIsInstance(pipeline, DenoisePipelineWidget)
        node_specs = (
            ("gaussian-first", GAUSSIAN_METHOD_NAME),
            ("gaussian-second", GAUSSIAN_METHOD_NAME),
            ("median", MEDIAN_METHOD_NAME),
            ("sg", SG_METHOD_NAME),
            ("wavelet", WAVELET_METHOD_NAME),
            ("nlmeans", NLM_METHOD_NAME),
            ("low-rank", LOW_RANK_METHOD_NAME),
        )
        pipeline.restore_state({"nodes": [
            {"id": node_id, "label": label} for node_id, label in node_specs
        ]})
        saved = deepcopy(pipeline.export_state())
        dialogs = {
            node_id: pipeline.open_parameter_dialog(node_id)
            for node_id, _label in node_specs
        }
        self.assertIsNot(dialogs["gaussian-first"], dialogs["gaussian-second"])
        for parameter_dialog in dialogs.values():
            self.assertFalse(parameter_dialog.isModal())

        spin_cases = (
            ("gaussian-first", "sigma_kx", 2.25, 0.0, 20.0, 0.1),
            ("gaussian-second", "sigma_kx", 4.5, 0.0, 20.0, 0.1),
            ("gaussian-first", "sigma_ky", 1.75, 0.0, 20.0, 0.1),
            ("gaussian-first", "sigma_e", 1.25, 0.0, 20.0, 0.1),
            ("median", "size_kx", 5, 1, 31, 2),
            ("median", "size_ky", 7, 1, 31, 2),
            ("median", "size_e", 5, 1, 31, 2),
            ("sg", "window_length", 7, 3, 9999, 2),
            ("sg", "polyorder", 1, 0, 4, 1),
            ("wavelet", "level", 2, 1, 6, 1),
            ("wavelet", "strength", 0.987654, 0.0, 10.0, 0.1),
            ("wavelet", "noise_sigma", 0.125, 0.0, 1.0e9, 0.01),
            ("nlmeans", "patch_size", 7, 1, 15, 2),
            ("nlmeans", "patch_distance", 5, 1, 20, 1),
            ("nlmeans", "h_scale", 1.75, 0.01, 100.0, 0.05),
            ("nlmeans", "noise_sigma", 0.125, 0.0, 1.0e9, 0.01),
            ("low-rank", "rank", 2, 1, 9999, 1),
        )
        combo_cases = (
            ("sg", "smoothing_axis", "kx"),
            ("wavelet", "wavelet", "haar"),
            ("wavelet", "threshold_rule", "bayes"),
            ("wavelet", "threshold_mode", "hard"),
        )
        self.assertIsNone(window.original_raw_data)
        window.request_refresh = Mock()
        with patch.object(
            window.refresh_coordinator,
            "submit_compute",
            wraps=window.refresh_coordinator.submit_compute,
        ) as submit_compute:
            for node_id, key, value, minimum, maximum, step in spin_cases:
                with self.subTest(node=node_id, control=key):
                    editor = dialogs[node_id]._controls[key]
                    self.assertEqual(editor.minimum(), minimum)
                    self.assertEqual(editor.maximum(), maximum)
                    self.assertEqual(editor.singleStep(), step)
                    if isinstance(value, float):
                        self.assertEqual(editor.decimals(), 6)
                    before = pipeline.export_state()["nodes"]
                    editor.setValue(value)
                    editor.editingFinished.emit()
                    expected = deepcopy(before)
                    edited_node = next(node for node in expected if node["id"] == node_id)
                    edited_node["params"][key] = value
                    self.assertEqual(pipeline.export_state()["nodes"], expected)

            nodes = pipeline.export_state()["nodes"]
            self.assertEqual(nodes[0]["params"]["sigma_kx"], 2.25)
            self.assertEqual(nodes[1]["params"]["sigma_kx"], 4.5)

            for node_id, key, value in combo_cases:
                with self.subTest(node=node_id, control=key):
                    editor = dialogs[node_id]._controls[key]
                    index = editor.findData(value)
                    self.assertGreaterEqual(index, 0)
                    before = pipeline.export_state()["nodes"]
                    editor.setCurrentIndex(index)
                    expected = deepcopy(before)
                    edited_node = next(node for node in expected if node["id"] == node_id)
                    edited_node["params"][key] = value
                    self.assertEqual(pipeline.export_state()["nodes"], expected)
                    self.assertFalse(dialogs[node_id]._debounce.isActive())

            window_control = dialogs["sg"]._controls["window_length"]
            window_control.setValue(3)
            window_control.editingFinished.emit()
            self.assertEqual(dialogs["sg"]._controls["polyorder"].maximum(), 2)
            sg_node = next(
                node for node in pipeline.export_state()["nodes"] if node["id"] == "sg"
            )
            self.assertEqual(sg_node["params"]["window_length"], 3)

            window.request_refresh.assert_not_called()
            submit_compute.assert_not_called()
            self.assertEqual(pipeline._execution_state, "idle")
            self.assertFalse(pipeline.error_panel.isVisible())

            pipeline.restore_state(saved)
            self.app.processEvents()
            self.assertEqual(pipeline.export_state(), saved)
            window.request_refresh.assert_not_called()
            submit_compute.assert_not_called()

        saved_nodes = {node["id"]: node for node in saved["nodes"]}
        restored_dialogs = {
            node_id: pipeline.open_parameter_dialog(node_id)
            for node_id, _label in node_specs
        }
        for node_id, key, _value, _minimum, _maximum, _step in spin_cases:
            with self.subTest(restored=f"{node_id}.{key}"):
                editor = restored_dialogs[node_id]._controls[key]
                self.assertEqual(editor.value(), saved_nodes[node_id]["params"][key])
        for node_id, key, _value in combo_cases:
            with self.subTest(restored=f"{node_id}.{key}"):
                editor = restored_dialogs[node_id]._controls[key]
                self.assertEqual(editor.currentData(), saved_nodes[node_id]["params"][key])

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
