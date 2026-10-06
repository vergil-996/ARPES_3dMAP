# -*- coding: utf-8 -*-
"""插件管理面板：选中状态、按钮文案、安装选择、恢复操作与只读模式。

回归：打开面板时没有任何选中行，按钮按「未选中」置灰；用户点击某一行后按钮
必须立即重新求值（此前只有 ``refresh()`` 更新按钮，必须先切走窗口再切回来）。
此外覆盖阶段 A/B 的新行为：按插件 id 保持选择、启用/停用文案切换、待卸载限制、
安装后选中新插件、重试/恢复按钮与登记表只读模式。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QMessageBox

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.extensions import plugin_dialog as plugin_dialog_module
from bandscope.extensions.plugin_dialog import (
    PluginManagerDialog,
    open_plugin_manager,
    status_text,
)
from bandscope.extensions.plugin_manager import InstallSource, PluginManager
from bandscope.extensions.trust import SOURCE_LOCAL
from tests.support.plugins import (
    PLUGIN_ID,
    SECOND_PLUGIN_ID,
    install_fake,
    manifest_payload,
    make_plugin_archive,
    read_registry,
    stage_plugin_content,
    v2_entry,
    write_v2_registry,
)

configure_qt_plugin_path()

THIRD_PLUGIN_ID = "band_alpha"
FOURTH_PLUGIN_ID = "band_gamma"
#: 模拟一次主程序升级：已装插件的兼容声明不覆盖这个版本。
FUTURE_APP_VERSION = "2.0.0"


class _FakeMessageBox:
    """替掉模态框：记录最后一次确认并返回脚本给定结果。

    标准按钮（``setStandardButtons``）用 ``next_result`` 脚本，``exec_()`` 直接
    返回按钮常量；自定义按钮（``addButton``）用 ``next_clicked_index`` 脚本，
    ``clickedButton()`` 返回对应那颗按钮的令牌，与真实 Qt 的用法一致。
    """

    Yes = QMessageBox.Yes
    No = QMessageBox.No
    Question = QMessageBox.Question
    Warning = QMessageBox.Warning
    Critical = QMessageBox.Critical
    Information = QMessageBox.Information
    DestructiveRole = QMessageBox.DestructiveRole
    RejectRole = QMessageBox.RejectRole
    AcceptRole = QMessageBox.AcceptRole
    ActionRole = QMessageBox.ActionRole
    next_result = QMessageBox.No
    #: 第几颗 addButton 按钮被“点击”；None 表示没有点击自定义按钮。
    next_clicked_index = None
    last = None

    def __init__(self, *args, **kwargs):
        type(self).last = self
        self.text = ""
        self.informative = ""
        self._buttons = []

    def setIcon(self, *_args):
        pass

    def setWindowTitle(self, *_args):
        pass

    def setText(self, text):
        self.text = text

    def setInformativeText(self, text):
        self.informative = text

    def setStandardButtons(self, *_args):
        pass

    def setDefaultButton(self, *_args):
        pass

    def setEscapeButton(self, *_args):
        pass

    def addButton(self, text, _role=None):
        token = object()
        self._buttons.append((str(text), token))
        return token

    def clickedButton(self):
        index = type(self).next_clicked_index
        if index is None or not 0 <= index < len(self._buttons):
            return None
        return self._buttons[index][1]

    def button_texts(self):
        return [text for text, _token in self._buttons]

    def exec_(self):
        return type(self).next_result


class _WindowStub(SimpleNamespace):
    def __init__(self):
        super().__init__()
        self.messages = []

    def _show_message(self, title, text, icon=None):
        self.messages.append((title, text))


class PluginDialogSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.root = Path(self._root.name) / "extensions"
        install_fake(self.root, SECOND_PLUGIN_ID)
        install_fake(self.root, PLUGIN_ID)

    def make_manager(self, app_version=None):
        kwargs = {"root": self.root}
        if app_version is not None:
            kwargs["app_version"] = app_version
        manager = PluginManager(**kwargs)
        self.addCleanup(manager.shutdown)
        manager.startup()
        return manager

    def _dialog(self, manager=None):
        manager = manager or self.make_manager()
        return PluginManagerDialog(_WindowStub(), SimpleNamespace(manager=manager))

    def test_buttons_start_disabled_without_a_selection(self):
        dialog = self._dialog()
        self.assertIsNone(dialog.selected_record())
        self.assertFalse(dialog.btn_uninstall.isEnabled())
        self.assertFalse(dialog.btn_toggle.isEnabled())
        self.assertFalse(dialog.btn_retry.isEnabled())
        self.assertFalse(dialog.btn_restore.isEnabled())

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

    def test_selection_survives_a_reorder_by_plugin_id(self):
        """列表按 id 排序；插入排在前面的插件后，选中仍落在同一个插件上。"""
        dialog = self._dialog()
        # 当前顺序：band_beta, flat_band_opacity；选中第二个。
        dialog.table.selectRow(1)
        self.assertEqual(dialog.selected_record().plugin_id, PLUGIN_ID)

        dialog.manager.install(
            make_plugin_archive(self.root / "new.bsplugin", manifest=manifest_payload(THIRD_PLUGIN_ID)),
            source=InstallSource(kind=SOURCE_LOCAL, accepted_unverified=True),
        )
        dialog.refresh()

        # 新顺序：band_alpha, band_beta, flat_band_opacity；选择跟着 id 移动。
        self.assertEqual(dialog.selected_record().plugin_id, PLUGIN_ID)
        self.assertEqual(dialog.table.currentRow(), 2)

    def test_empty_manager_keeps_the_buttons_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PluginManager(root=Path(directory) / "extensions")
            manager.scan()
            dialog = PluginManagerDialog(_WindowStub(), SimpleNamespace(manager=manager))

        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertFalse(dialog.btn_uninstall.isEnabled())
        self.assertIn("尚未安装任何插件", dialog.hint.text())

    def test_status_column_marks_an_incompatible_record(self):
        # 模拟主程序升级后插件不再兼容：插件内容不动，换一个宿主版本加载。
        # （不能再改磁盘上的清单——内容摘要会先一步判定为被改动而拒绝加载。）
        reloaded = self.make_manager(app_version=FUTURE_APP_VERSION)
        dialog = self._dialog(reloaded)

        row = next(
            i for i, r in enumerate(reloaded.plugins()) if r.plugin_id == PLUGIN_ID
        )
        status = dialog.table.item(row, 2).text()
        self.assertIn("不兼容", status)
        # 不兼容也要能卸载：选中后按钮必须可用。
        dialog.table.selectRow(row)
        self.assertTrue(dialog.btn_uninstall.isEnabled())

    def test_toggle_button_text_follows_the_state(self):
        dialog = self._dialog()
        dialog.table.selectRow(0)
        self.assertEqual(dialog.btn_toggle.text(), "停用")

        dialog.on_toggle()

        self.assertEqual(dialog.btn_toggle.text(), "启用")
        record = dialog.selected_record()
        self.assertFalse(record.enabled)
        self.assertFalse(read_registry(self.root)["plugins"][record.plugin_id]["desired"]["enabled"])
        # 当前会话效果保持：实例仍在运行。
        self.assertTrue(record.ready)

    def test_pending_uninstall_disables_the_operations(self):
        manager = self.make_manager()
        dialog = self._dialog(manager)
        record = manager.record(SECOND_PLUGIN_ID)
        manager.note_removal(SECOND_PLUGIN_ID)
        dialog.refresh()
        row = next(i for i, r in enumerate(manager.plugins()) if r.plugin_id == SECOND_PLUGIN_ID)
        dialog.table.selectRow(row)

        self.assertFalse(dialog.btn_toggle.isEnabled())
        self.assertFalse(dialog.btn_uninstall.isEnabled())
        self.assertFalse(dialog.btn_retry.isEnabled())
        self.assertFalse(dialog.btn_restore.isEnabled())
        self.assertIn("待重启卸载", dialog.table.item(row, 2).text())

        # 直接调用也要提前返回，不能把请求再写一遍或改坏状态。
        with mock.patch.object(plugin_dialog_module, "QMessageBox", _FakeMessageBox):
            dialog.on_toggle()
            dialog.on_uninstall()
        self.assertTrue(record.pending_removal)
        self.assertTrue(read_registry(self.root)["plugins"][SECOND_PLUGIN_ID]["pending_uninstall"])

    def test_uninstall_confirm_and_cancel(self):
        manager = self.make_manager()
        dialog = self._dialog(manager)
        row = next(i for i, r in enumerate(manager.plugins()) if r.plugin_id == SECOND_PLUGIN_ID)
        dialog.table.selectRow(row)

        with mock.patch.object(plugin_dialog_module, "QMessageBox", _FakeMessageBox):
            _FakeMessageBox.next_result = _FakeMessageBox.No
            dialog.on_uninstall()
            self.assertFalse(manager.record(SECOND_PLUGIN_ID).pending_removal)

            _FakeMessageBox.next_result = _FakeMessageBox.Yes
            dialog.on_uninstall()

        record = manager.record(SECOND_PLUGIN_ID)
        self.assertTrue(record.pending_removal)
        # 确认文字只说明“下次启动时执行”，不再给过强保证。
        self.assertIn("下次启动", _FakeMessageBox.last.informative)
        self.assertNotIn("不影响当前会话", _FakeMessageBox.last.informative)

    def test_install_selects_the_new_plugin(self):
        manager = self.make_manager()
        window = _WindowStub()
        dialog = PluginManagerDialog(window, SimpleNamespace(manager=manager))
        archive = make_plugin_archive(
            self.root / "gamma.bsplugin", manifest=manifest_payload(FOURTH_PLUGIN_ID)
        )

        with mock.patch.object(plugin_dialog_module, "QMessageBox", _FakeMessageBox):
            # 合成包没有签名：确认框的第一颗按钮是「仍要安装」。
            _FakeMessageBox.next_clicked_index = 0
            with mock.patch.object(
                plugin_dialog_module.QFileDialog,
                "getOpenFileName",
                return_value=(str(archive), ""),
            ):
                dialog.on_install()

        self.assertEqual(dialog.selected_record().plugin_id, FOURTH_PLUGIN_ID)
        self.assertTrue(
            any("已安装" in text for _title, text in window.messages),
            window.messages,
        )
        # 只登记候选，不加载。
        self.assertFalse(manager.record(FOURTH_PLUGIN_ID).running)

    def test_install_asks_before_cancelling_a_pending_uninstall(self):
        manager = self.make_manager()
        window = _WindowStub()
        dialog = PluginManagerDialog(window, SimpleNamespace(manager=manager))
        manager.note_removal(SECOND_PLUGIN_ID)
        dialog.refresh()
        archive = make_plugin_archive(
            self.root / "again.bsplugin", manifest=manifest_payload(SECOND_PLUGIN_ID)
        )

        with mock.patch.object(plugin_dialog_module, "QMessageBox", _FakeMessageBox):
            _FakeMessageBox.next_result = _FakeMessageBox.No
            _FakeMessageBox.next_clicked_index = 0
            with mock.patch.object(
                plugin_dialog_module.QFileDialog, "getOpenFileName", return_value=(str(archive), "")
            ):
                dialog.on_install()
            self.assertTrue(manager.record(SECOND_PLUGIN_ID).pending_removal)

            _FakeMessageBox.next_result = _FakeMessageBox.Yes
            with mock.patch.object(
                plugin_dialog_module.QFileDialog, "getOpenFileName", return_value=(str(archive), "")
            ):
                dialog.on_install()

        self.assertFalse(manager.record(SECOND_PLUGIN_ID).pending_removal)
        self.assertFalse(
            read_registry(self.root)["plugins"][SECOND_PLUGIN_ID]["pending_uninstall"]
        )

    def test_retry_and_restore_are_available_for_a_failed_candidate(self):
        digest, path = stage_plugin_content(self.root, PLUGIN_ID)
        write_v2_registry(
            self.root,
            {
                PLUGIN_ID: v2_entry(
                    manifest_payload(),
                    digest=digest,
                    path=path,
                    failed={
                        "version": "1.1.0",
                        "digest": "f" * 64,
                        "phase": "load",
                        "message": "boom",
                        "at": "2026-10-01T00:00:00+00:00",
                    },
                    last_good={
                        "version": "0.9.0",
                        "digest": "f" * 64,
                        "path": f"installed/{PLUGIN_ID}/0.9.0/{'f' * 64}",
                        "revision": 1,
                    },
                )
            },
        )
        manager = PluginManager(root=self.root)
        self.addCleanup(manager.shutdown)
        manager.scan()
        dialog = self._dialog(manager)
        dialog.table.selectRow(0)

        self.assertTrue(dialog.btn_retry.isEnabled())
        self.assertTrue(dialog.btn_restore.isEnabled())
        self.assertIn("上次加载失败", dialog.table.item(0, 2).text())

        dialog.on_retry()
        self.assertNotIn("failed", read_registry(self.root)["plugins"][PLUGIN_ID])

    def test_registry_error_switches_the_dialog_to_read_only(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "registry.json").write_text("{ this is not json", encoding="utf-8")
        manager = PluginManager(root=self.root)
        self.addCleanup(manager.shutdown)
        manager.scan()

        dialog = self._dialog(manager)

        self.assertTrue(manager.registry_error)
        self.assertIn("只读", dialog.hint.text())
        self.assertFalse(dialog.btn_install.isEnabled())
        self.assertEqual(dialog.table.rowCount(), 0)

    def test_open_plugin_manager_reuses_the_window(self):
        manager = self.make_manager()
        window = _WindowStub()
        session = SimpleNamespace(manager=manager)

        first = open_plugin_manager(window, session)
        second = open_plugin_manager(window, session)

        self.assertIs(first, second)


class StatusTextTests(unittest.TestCase):
    """状态文案：运行与待生效操作分开说清楚。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _record(self, **overrides):
        from bandscope.extensions.api import PluginRecord

        record = PluginRecord(plugin_id=PLUGIN_ID)
        for key, value in overrides.items():
            setattr(record, key, value)
        return record

    def test_running(self):
        self.assertEqual(status_text(self._record(running=True, version="1.0.0")), "运行中")

    def test_running_with_pending_disable(self):
        text = status_text(self._record(running=True, version="1.0.0", enabled=False))
        self.assertIn("运行中", text)
        self.assertIn("待重启停用", text)

    def test_running_with_pending_update(self):
        text = status_text(
            self._record(running=True, version="1.1.0", running_version="1.0.0")
        )
        self.assertIn("当前 1.0.0", text)
        self.assertIn("待重启更新至 1.1.0", text)

    def test_pending_uninstall(self):
        self.assertEqual(
            status_text(self._record(pending_removal=True, enabled=False)), "待重启卸载"
        )

    def test_waiting_for_restart_to_load(self):
        self.assertEqual(status_text(self._record(enabled=True)), "待重启加载")

    def test_disabled(self):
        self.assertEqual(status_text(self._record(enabled=False)), "已停用")

    def test_failed_load_shows_the_reason(self):
        text = status_text(self._record(enabled=True, load_error="RuntimeError: boom"))
        self.assertIn("未加载", text)
        self.assertIn("boom", text)

    def test_unconfirmed_is_read_only(self):
        text = status_text(self._record(unconfirmed_reason="记录不是 JSON 对象"))
        self.assertIn("条目无法确认（只读）", text)


if __name__ == "__main__":
    unittest.main()
