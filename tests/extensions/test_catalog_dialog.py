# -*- coding: utf-8 -*-
"""阶段 E：插件管理窗口的「官方插件」页。

目录获取与包下载都用替身客户端驱动；签名用临时密钥，官方来源走的仍是真实的
验签与安装事务，不绕过任何校验。
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PyQt5.QtWidgets import QApplication, QMessageBox

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.extensions import plugin_dialog as plugin_dialog_module
from bandscope.extensions import trust as trust_module
from bandscope.extensions.catalog import (
    STATUS_INCOMPATIBLE,
    STATUS_INSTALLABLE,
    STATUS_UPDATE_AVAILABLE,
    CatalogClient,
    DownloadedPackage,
    FetchedCatalog,
    parse_catalog,
)
from bandscope.extensions.plugin_dialog import PluginManagerDialog
from bandscope.extensions.plugin_manager import InstallSource, PluginManager
from bandscope.extensions.trust import SOURCE_OFFICIAL, build_signature, write_signature_sidecar
from tests.support.plugins import (
    PLUGIN_ID,
    install_fake,
    make_plugin_archive,
    manifest_payload,
    read_registry,
    test_signing_key,
)

configure_qt_plugin_path()

URL_ROOT = "https://github.com/vergil-996/ARPES_3dMAP/releases/download/v1.12.0"


class _FakeMessageBox:
    """与 test_plugin_dialog 同款替身：标准按钮用 next_result，自定义按钮用序号。"""

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
    next_clicked_index = 0
    last = None

    def __init__(self, *_args, **_kwargs):
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

    def setDetailedText(self, text):
        pass

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

    def exec_(self):
        return type(self).next_result


class _WindowStub(SimpleNamespace):
    def __init__(self):
        super().__init__()
        self.messages = []

    def _show_message(self, title, text, icon=None):
        self.messages.append((title, text))


class _StubClient:
    """替身目录客户端：不联网，但仍然交出真实的签名包路径。"""

    def __init__(self, catalog=None, *, fetch_error="", package=None):
        self.catalog = catalog
        self.fetch_error = fetch_error
        self.package = package
        self.install_source = CatalogClient.install_source.__get__(self)

    def fetch_catalog(self, destination_dir=None):
        if self.fetch_error:
            raise RuntimeError(self.fetch_error)
        return FetchedCatalog(self.catalog, b"payload", b"signature", "2026-10-06T00:00:00+00:00")

    def download_package(self, entry, destination_dir, *, progress=None, cancelled=None):
        if progress is not None:
            progress(10, 10)
        return self.package


def make_catalog(entries, *, host_version="1.12.0", api_versions=(1,), revision=2):
    return parse_catalog(
        json.dumps(
            {
                "schema": 1,
                "revision": revision,
                "generated_at": "2026-10-06T00:00:00+00:00",
                "host": {"app_version": host_version, "api_versions": list(api_versions)},
                "plugins": entries,
            },
            ensure_ascii=False,
        )
    )


def catalog_entry(version, *, sha, size, name=None, requires_app=">=1.10,<1.13",
                  api_version=1, capabilities=("opacity_multiplier",), signature=True):
    package_name = name or f"BandScope-{PLUGIN_ID}-{version}.bsplugin"
    entry = {
        "id": PLUGIN_ID,
        "name": "平带增强",
        "version": version,
        "requires_app": requires_app,
        "api_version": api_version,
        "capabilities": list(capabilities),
        "notes": "让平带更醒目。",
        "package": {
            "name": package_name,
            "url": f"{URL_ROOT}/{package_name}",
            "size": size,
            "sha256": sha,
        },
    }
    if signature:
        entry["signature"] = {
            "name": package_name + ".sig",
            "url": f"{URL_ROOT}/{package_name}.sig",
            "size": 200,
            "sha256": "b" * 64,
        }
    return entry


class CatalogDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.root = Path(self._root.name) / "extensions"
        self.key_id, self.private_key, self.trusted = test_signing_key()
        patcher = mock.patch.object(trust_module, "TRUSTED_PLUGIN_KEYS", self.trusted)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 目录缓存跟着扩展根走；这里再钉一次，免得读到真实用户目录。
        cache = mock.patch.object(
            plugin_dialog_module, "read_cached_catalog", return_value=None
        )
        cache.start()
        self.addCleanup(cache.stop)

    def make_manager(self, *, installed=True, version="1.0.0"):
        if installed:
            install_fake(self.root, PLUGIN_ID, version=version)
        manager = PluginManager(root=self.root)
        self.addCleanup(manager.shutdown)
        manager.startup()
        return manager

    def make_dialog(self, manager, client):
        return PluginManagerDialog(
            _WindowStub(), SimpleNamespace(manager=manager), catalog_client=client
        )

    def signed_package(self, version="1.0.1", archive_name=None):
        archive = make_plugin_archive(
            Path(self._root.name) / (archive_name or f"pkg-{version}.bsplugin"),
            manifest=manifest_payload(version=version),
        )
        envelope = build_signature(
            self.private_key, archive.read_bytes(), purpose="plugin", key_id=self.key_id
        )
        write_signature_sidecar(archive, envelope)
        return archive

    # -- 表格渲染 --------------------------------------------------------
    def test_rows_show_installable_update_and_incompatible(self):
        manager = self.make_manager(installed=False)
        catalog = make_catalog([
            catalog_entry("1.0.0", sha="c" * 64, size=10),
            catalog_entry("2.0.0", sha="d" * 64, size=10, requires_app=">=99.0"),
        ])
        dialog = self.make_dialog(manager, _StubClient(catalog))
        dialog._catalog = catalog
        dialog._render_catalog()

        self.assertEqual(dialog.catalog_table.rowCount(), 1)
        self.assertEqual(dialog.catalog_table.item(0, 1).text(), "1.0.0")
        self.assertIn("可安装", dialog.catalog_table.item(0, 2).text())

    def test_incompatible_only_catalog_reports_the_reason(self):
        manager = self.make_manager()
        catalog = make_catalog([
            catalog_entry("2.0.0", sha="d" * 64, size=10, requires_app=">=99.0"),
        ])
        dialog = self.make_dialog(manager, _StubClient(catalog))
        dialog._catalog = catalog
        dialog._render_catalog()

        self.assertIn("不兼容", dialog.catalog_table.item(0, 2).text())
        dialog.catalog_table.selectRow(0)
        self.assertFalse(dialog.btn_catalog_install.isEnabled())

    def test_update_row_is_selected_and_install_enabled(self):
        manager = self.make_manager()
        catalog = make_catalog([catalog_entry("1.0.1", sha="e" * 64, size=10)])
        dialog = self.make_dialog(manager, _StubClient(catalog))
        dialog._catalog = catalog
        dialog._render_catalog()
        dialog.catalog_table.selectRow(0)

        self.assertIn("可更新", dialog.catalog_table.item(0, 2).text())
        self.assertTrue(dialog.btn_catalog_install.isEnabled())
        self.assertIn("平带增强", dialog.catalog_details.text())

    def test_no_catalog_disables_install(self):
        manager = self.make_manager()
        dialog = self.make_dialog(manager, _StubClient(None))
        self.assertFalse(dialog.btn_catalog_install.isEnabled())
        self.assertIn("尚未获取", dialog.catalog_status.text())

    def test_fetch_failure_without_cache_disables_install(self):
        manager = self.make_manager()
        dialog = self.make_dialog(manager, _StubClient(None, fetch_error="断网"))
        dialog._on_catalog_failed("断网")
        self.assertIn("断网", dialog.catalog_status.text())
        self.assertFalse(dialog.btn_catalog_install.isEnabled())

    # -- 安装流程 --------------------------------------------------------
    def test_official_install_verifies_and_records_the_source(self):
        manager = self.make_manager()
        archive = self.signed_package("1.0.1")
        payload = archive.read_bytes()
        entry = catalog_entry(
            "1.0.1", sha=hashlib.sha256(payload).hexdigest(), size=len(payload)
        )
        catalog = make_catalog([entry])
        client = _StubClient(
            catalog,
            package=DownloadedPackage(
                entry=catalog.entries[0], path=archive,
                sha256=hashlib.sha256(payload).hexdigest(), verified=True,
            ),
        )
        window = _WindowStub()
        dialog = PluginManagerDialog(window, SimpleNamespace(manager=manager), catalog_client=client)
        dialog._catalog = catalog
        dialog._render_catalog()
        dialog.catalog_table.selectRow(0)

        with mock.patch.object(plugin_dialog_module, "QMessageBox", _FakeMessageBox):
            _FakeMessageBox.next_clicked_index = 0
            dialog.on_catalog_install()
            thread = dialog._download_thread
            if thread is not None:
                thread.wait(5000)
            self.app.processEvents()

        registry = read_registry(self.root)
        source = registry["plugins"][PLUGIN_ID]["source"]
        self.assertEqual(source["kind"], SOURCE_OFFICIAL)
        self.assertEqual(source["key_id"], self.key_id)
        self.assertEqual(registry["plugins"][PLUGIN_ID]["desired"]["version"], "1.0.1")
        self.assertTrue(any("安装完成" in title for title, _text in window.messages))

    def test_cancelled_confirmation_downloads_nothing(self):
        manager = self.make_manager()
        archive = self.signed_package("1.0.1")
        payload = archive.read_bytes()
        catalog = make_catalog([
            catalog_entry("1.0.1", sha=hashlib.sha256(payload).hexdigest(), size=len(payload))
        ])
        client = _StubClient(catalog)
        dialog = self.make_dialog(manager, client)
        dialog._catalog = catalog
        dialog._render_catalog()
        dialog.catalog_table.selectRow(0)

        with mock.patch.object(plugin_dialog_module, "QMessageBox", _FakeMessageBox):
            _FakeMessageBox.next_clicked_index = 1  # 取消
            dialog.on_catalog_install()

        self.assertIsNone(dialog._download_thread)
        self.assertEqual(read_registry(self.root)["plugins"][PLUGIN_ID]["desired"]["version"], "1.0.0")

    def test_unsigned_catalog_entry_requires_confirmation(self):
        manager = self.make_manager()
        archive = make_plugin_archive(
            Path(self._root.name) / "unsigned.bsplugin", manifest=manifest_payload(version="1.0.2")
        )
        payload = archive.read_bytes()
        # 目录声明里没有签名：安装事务会按“用户已确认的未验证来源”处理。
        catalog = make_catalog([
            catalog_entry("1.0.2", sha=hashlib.sha256(payload).hexdigest(),
                          size=len(payload), signature=False)
        ])
        entry = catalog.entries[0]
        client = _StubClient(
            catalog,
            package=DownloadedPackage(
                entry=entry, path=archive,
                sha256=hashlib.sha256(payload).hexdigest(), verified=False,
            ),
        )
        dialog = self.make_dialog(manager, client)
        dialog._catalog = catalog
        dialog._render_catalog()
        dialog.catalog_table.selectRow(0)

        with mock.patch.object(plugin_dialog_module, "QMessageBox", _FakeMessageBox):
            _FakeMessageBox.next_clicked_index = 0
            dialog.on_catalog_install()
            self.assertIn("未签名", _FakeMessageBox.last.informative)
            thread = dialog._download_thread
            if thread is not None:
                thread.wait(5000)
            self.app.processEvents()

        registry = read_registry(self.root)
        self.assertEqual(registry["plugins"][PLUGIN_ID]["desired"]["version"], "1.0.2")
        self.assertEqual(registry["plugins"][PLUGIN_ID]["source"]["kind"], "local")

    def test_install_source_uses_the_catalog_identity(self):
        class _Client(CatalogClient):
            pass

        catalog = make_catalog([catalog_entry("1.0.1", sha="f" * 64, size=10)])
        entry = catalog.entries[0]
        client = CatalogClient.__new__(CatalogClient)
        source = CatalogClient.install_source(client, entry, verified=True)
        self.assertIsInstance(source, InstallSource)
        self.assertEqual(source.kind, SOURCE_OFFICIAL)
        self.assertEqual(source.expected_sha256, "f" * 64)

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        self.app.processEvents()
        return predicate()

    def test_opening_the_official_tab_fetches_once(self):
        manager = self.make_manager()
        catalog = make_catalog([catalog_entry("1.0.1", sha="f" * 64, size=10)])
        client = _StubClient(catalog)
        dialog = self.make_dialog(manager, client)

        dialog.tabs.setCurrentIndex(dialog._catalog_tab_index)
        self.assertTrue(self.wait_for(lambda: dialog._catalog is not None))
        self.assertEqual(dialog.catalog_table.rowCount(), 1)

        # 再来回切一次：本会话已经取过目录，不再重复请求。
        calls = []
        client.fetch_catalog = lambda destination_dir=None: calls.append(1) or FetchedCatalog(
            catalog, b"p", b"s", "2026-10-06T00:00:00+00:00"
        )
        dialog.tabs.setCurrentIndex(0)
        dialog.tabs.setCurrentIndex(dialog._catalog_tab_index)
        self.assertEqual(calls, [])

    def test_selection_survives_a_catalog_render(self):
        manager = self.make_manager()
        catalog = make_catalog([catalog_entry("1.0.1", sha="f" * 64, size=10)])
        dialog = self.make_dialog(manager, _StubClient(catalog))
        dialog._catalog = catalog
        dialog._render_catalog()
        dialog.catalog_table.selectRow(0)
        self.assertEqual(dialog.selected_catalog_row()[0], PLUGIN_ID)

        dialog._render_catalog()
        self.assertEqual(dialog.selected_catalog_row()[0], PLUGIN_ID)
        self.assertTrue(dialog.btn_catalog_install.isEnabled())

    def test_status_text_mentions_cached_catalog(self):
        manager = self.make_manager()
        catalog = make_catalog([catalog_entry("1.0.1", sha="f" * 64, size=10)])
        dialog = self.make_dialog(manager, _StubClient(catalog))
        dialog._catalog = catalog
        dialog._catalog_source = plugin_dialog_module.CATALOG_SOURCE_CACHE
        dialog._catalog_verified_at = "2026-10-06T00:00:00+00:00"
        dialog._update_catalog_status()
        self.assertIn("离线", dialog.catalog_status.text())
        self.assertIn("1.12.0", dialog.catalog_status.text())


if __name__ == "__main__":
    unittest.main()
