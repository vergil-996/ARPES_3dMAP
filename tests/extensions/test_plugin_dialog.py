# -*- coding: utf-8 -*-
"""扩展管理面板的按钮状态与列表刷新（离屏，临时扩展根）。

回归：打开面板时没有任何选中行，按钮按「未选中」置灰；此前只有在
``refresh()`` 里更新按钮，用户点击某一行不会重新求值，卸载/启停按钮就一直
是灰的，必须先切走窗口再切回来才亮。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.app_metadata import APP_VERSION
from bandscope.extensions.api import API_VERSION
from bandscope.extensions.plugin_dialog import PluginManagerDialog
from bandscope.extensions.plugin_manager import PluginManager

configure_qt_plugin_path()

PLUGIN_ID = "flat_band_opacity"


def _manifest(**overrides):
    payload = {
        "id": PLUGIN_ID,
        "name": "平带增强",
        "version": "1.0.0",
        "api_version": API_VERSION,
        "requires_app": APP_VERSION,
        "entry_point": "entry:Plugin",
        "capabilities": ["opacity_multiplier"],
    }
    payload.update(overrides)
    return payload


class PluginDialogSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.root = Path(self._root.name) / "extensions"
        self._install(PLUGIN_ID, _manifest())

    def _install(self, plugin_id, manifest):
        target = self.root / "installed" / plugin_id / manifest["version"]
        target.mkdir(parents=True, exist_ok=True)
        (target / "plugin.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "registry.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "plugins": {
                        plugin_id: {
                            "version": manifest["version"],
                            "name": manifest["name"],
                            "path": f"{plugin_id}\\{manifest['version']}",
                            "enabled": True,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def _dialog(self):
        manager = PluginManager(root=self.root)
        manager.scan()
        return PluginManagerDialog(SimpleNamespace(), SimpleNamespace(manager=manager))

    def test_buttons_start_disabled_without_a_selection(self):
        dialog = self._dialog()
        self.assertIsNone(dialog.selected_record())
        self.assertFalse(dialog.btn_uninstall.isEnabled())
        self.assertFalse(dialog.btn_toggle.isEnabled())

    def test_selecting_a_row_enables_uninstall_and_toggle(self):
        dialog = self._dialog()

        dialog.table.selectRow(0)

        self.assertIsNotNone(dialog.selected_record())
        self.assertTrue(dialog.btn_uninstall.isEnabled())
        self.assertTrue(dialog.btn_toggle.isEnabled())

    def test_clearing_the_selection_disables_the_buttons_again(self):
        dialog = self._dialog()
        dialog.table.selectRow(0)
        self.assertTrue(dialog.btn_uninstall.isEnabled())

        dialog.table.clearSelection()
        dialog.table.setCurrentCell(-1, -1)

        self.assertFalse(dialog.btn_uninstall.isEnabled())

    def test_refresh_after_selecting_keeps_the_buttons_enabled(self):
        dialog = self._dialog()
        dialog.table.selectRow(0)

        dialog.refresh()

        self.assertTrue(dialog.btn_uninstall.isEnabled())

    def test_empty_manager_keeps_the_buttons_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PluginManager(root=Path(directory) / "extensions")
            manager.scan()
            dialog = PluginManagerDialog(
                SimpleNamespace(), SimpleNamespace(manager=manager)
            )

        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertFalse(dialog.btn_uninstall.isEnabled())
        self.assertIn("尚未安装任何扩展", dialog.hint.text())

    def test_status_column_marks_an_incompatible_record(self):
        self._install(PLUGIN_ID, _manifest(requires_app="1.0.0"))
        dialog = self._dialog()

        status = dialog.table.item(0, 2).text()

        self.assertIn("不兼容", status)
        # 不兼容也要能卸载：选中后按钮必须可用。
        dialog.table.selectRow(0)
        self.assertTrue(dialog.btn_uninstall.isEnabled())


if __name__ == "__main__":
    unittest.main()
