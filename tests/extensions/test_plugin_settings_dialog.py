# -*- coding: utf-8 -*-
"""插件管理「插件设置」页：通用两个操作与默认值持久化。

用真实对话框 + 测试替身会话驱动：面板由插件提供，宿主只负责挂载、默认值
读写与「应用当前瀑布图」的结果页归属校验。
"""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.extensions import plugin_dialog as plugin_dialog_module
from bandscope.extensions.plugin_dialog import PluginManagerDialog

configure_qt_plugin_path()


class _SettingsPlugin:
    """最小设置插件替身：面板一个复选框，状态可读写。"""

    def __init__(self):
        self.state = {"enabled": False}
        self.panel = None
        self.saved = []
        self.applied = []

    def create_settings_panel(self, host):
        self.panel = QWidget()
        return self.panel

    def export_settings(self):
        return dict(self.state)

    def restore_settings(self, state):
        self.state = {"enabled": bool(state.get("enabled", False))}


class _Session:
    def __init__(self, manager, plugin):
        self.manager = manager
        self.plugin = plugin
        self.defaults = {}
        self.errors = lambda: {}

    def settings_plugin_ids(self):
        return ["edc_test"]

    def restore_plugin_settings(self, plugin_id):
        self.plugin.restore_settings(self.defaults.get(plugin_id, {}))

    def create_settings_panel(self, plugin_id):
        return self.plugin.create_settings_panel(self)

    def release_settings_panel(self, plugin_id):
        self.plugin.panel = None

    def plugin_settings_state(self, plugin_id):
        return self.plugin.export_settings()

    def save_derived_defaults(self, plugin_id, values):
        self.defaults[plugin_id] = dict(values)

    def apply_settings_to_current_page(self, plugin_id):
        self.plugin.applied.append(dict(self.plugin.export_settings()))
        return (True, "已应用到当前瀑布图；其他页面保留各自参数。")


class _Manager:
    def __init__(self, plugin):
        self.plugin = plugin
        self.root = "C:/tmp/extensions"
        self.registry_error = ""
        self.cleanup_deferred = False

    def plugins(self):
        return []

    def record(self, plugin_id):
        return SimpleNamespace(
            plugin_id=plugin_id,
            manifest=SimpleNamespace(name="测试派生"),
            version="1.0.0",
        )

    def incompatible(self):
        return []


class _WindowStub(SimpleNamespace):
    def __init__(self):
        super().__init__()
        self.messages = []

    def _show_message(self, title, text, icon=None):
        self.messages.append((title, text))


class PluginSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_dialog(self):
        plugin = _SettingsPlugin()
        window = _WindowStub()
        session = _Session(_Manager(plugin), plugin)
        dialog = PluginManagerDialog(window, session)
        self.addCleanup(dialog.deleteLater)
        return dialog, window, session, plugin

    def test_settings_tab_lists_panels_and_defaults_round_trip(self):
        dialog, window, session, plugin = self.make_dialog()
        dialog.refresh_settings_tab()
        self.assertIn("edc_test", dialog._settings_sections)

        # 面板状态 → 「保存为新建默认值」 → QSettings（此处为会话替身）。
        plugin.state = {"enabled": True}
        dialog.on_settings_save("edc_test")
        self.assertEqual(session.defaults["edc_test"], {"enabled": True})
        self.assertTrue(any("已保存为新建默认值" in text for _, text in window.messages))

        # 重建设置页时新面板按保存的默认值复位。
        plugin.panel = None
        dialog.refresh_settings_tab()
        self.assertTrue(plugin.state["enabled"])

    def test_apply_uses_current_settings_and_reports_result(self):
        dialog, window, session, plugin = self.make_dialog()
        dialog.refresh_settings_tab()
        plugin.state = {"enabled": True}
        dialog.on_settings_apply("edc_test")
        self.assertEqual(plugin.applied, [{"enabled": True}])
        self.assertTrue(any("已应用" in title for title, _ in window.messages))

    def test_failed_apply_reports_the_reason(self):
        dialog, window, session, plugin = self.make_dialog()
        dialog.refresh_settings_tab()
        session.apply_settings_to_current_page = lambda plugin_id: (
            False,
            "当前页不是该插件的派生结果页，已保留各页原有参数。",
        )
        dialog.on_settings_apply("edc_test")
        title, text = window.messages[-1]
        self.assertEqual(title, "未应用")
        self.assertIn("不是该插件的派生结果页", text)


if __name__ == "__main__":
    unittest.main()
