# -*- coding: utf-8 -*-
"""生命周期：统一释放入口、自定义 root、跨会话推迟、恢复到上一版本。

期望配置与运行实例分离之后，“重启才生效”的语义必须一致：启停/卸载/更新只
写登记表；实例释放恰好一次；有其他会话时卸载与清理推迟到下次安全启动。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bandscope.app_metadata import APP_VERSION
from bandscope.extensions.plugin_host import PluginSession
from bandscope.extensions.plugin_manager import PluginManager
from tests.support.plugins import (
    PLUGIN_ID,
    install_synthetic,
    make_plugin_archive,
    manifest_payload,
    release_logging_entry,
    synthetic_source,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class LifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.root = Path(self._root.name) / "extensions"

    def make_manager(self, root=None):
        manager = PluginManager(app_version=APP_VERSION, root=root or self.root)
        self.addCleanup(manager.shutdown)
        return manager

    def registry(self, root=None):
        path = (root or self.root) / "registry.json"
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))["plugins"]


class CustomRootTests(LifecycleTestCase):
    def test_every_operation_uses_the_manager_root(self):
        """传入自定义 root 后不能再有操作去读环境变量根目录。"""
        with tempfile.TemporaryDirectory() as env_directory:
            previous = os.environ.get("BANDSCOPE_EXTENSION_ROOT")
            os.environ["BANDSCOPE_EXTENSION_ROOT"] = env_directory
            try:
                manager = self.make_manager()
                archive = make_plugin_archive(self.root / "staging" / "pkg.bsplugin")
                manager.install(archive, source=synthetic_source())
                manager.startup()
                record = manager.record(PLUGIN_ID)
                self.assertTrue(record.ready, record.load_error)

                manager.set_enabled(PLUGIN_ID, False)
                manager.set_enabled(PLUGIN_ID, True)
                manager.note_removal(PLUGIN_ID)
                self.assertTrue(self.registry()[PLUGIN_ID]["pending_uninstall"])

                manager.shutdown()
                restarted = self.make_manager()
                restarted.startup()
                self.assertEqual(restarted.pending_uninstalls, [PLUGIN_ID])

                # 环境变量指向的目录自始至终没被碰过。
                self.assertEqual(list(Path(env_directory).iterdir()), [])
            finally:
                if previous is None:
                    os.environ.pop("BANDSCOPE_EXTENSION_ROOT", None)
                else:
                    os.environ["BANDSCOPE_EXTENSION_ROOT"] = previous


class ReleaseOnceTests(LifecycleTestCase):
    def _install_release_logging(self, log_path):
        archive = make_plugin_archive(
            self.root / "staging" / "rel.bsplugin",
            entry_source=release_logging_entry(log_path),
        )
        install_synthetic(archive, root=self.root)

    def _release_count(self, log_path):
        if not Path(log_path).is_file():
            return 0
        return len(Path(log_path).read_text(encoding="utf-8").splitlines())

    def test_shutdown_releases_each_instance_exactly_once(self):
        log = Path(self._root.name) / "release.log"
        self._install_release_logging(log)
        manager = self.make_manager()
        manager.startup()
        record = manager.record(PLUGIN_ID)
        self.assertTrue(record.ready, record.load_error)

        manager.shutdown()
        manager.shutdown()  # 重复 shutdown 不重复释放

        self.assertEqual(self._release_count(log), 1)

    def test_panel_failure_releases_the_created_instance(self):
        from PyQt5.QtWidgets import QApplication

        from bandscope.app.qt_bootstrap import configure_qt_plugin_path

        configure_qt_plugin_path()
        QApplication.instance() or QApplication([])

        log = Path(self._root.name) / "release.log"
        self._install_release_logging(log)
        manager = self.make_manager()
        manager.startup()
        record = manager.record(PLUGIN_ID)
        self.assertTrue(record.ready, record.load_error)

        def broken_panel(host):
            raise RuntimeError("panel exploded")

        record.instance.create_panel = broken_panel
        window = SimpleNamespace(toast_manager=None)
        session = PluginSession(window, manager=manager)
        session.mount_cards(SimpleNamespace(
            mount_extension_card=lambda *a, **k: None,
            set_extension_card_visible=lambda *a, **k: None,
        ))

        # 面板初始化失败：实例立即释放，且只释放一次。
        self.assertFalse(record.ready)
        self.assertEqual(self._release_count(log), 1)
        self.assertIsNotNone(record.failed_candidate)
        self.assertEqual(record.failed_candidate["phase"], "panel")
        self.assertIn("failed", self.registry()[PLUGIN_ID])

        session.shutdown()
        self.assertEqual(self._release_count(log), 1)

    def test_successful_mount_marks_last_good(self):
        from PyQt5.QtWidgets import QApplication

        from bandscope.app.qt_bootstrap import configure_qt_plugin_path

        configure_qt_plugin_path()
        QApplication.instance() or QApplication([])

        archive = make_plugin_archive(self.root / "staging" / "ok.bsplugin")
        install_synthetic(archive, root=self.root)
        manager = self.make_manager()
        manager.startup()
        window = SimpleNamespace(toast_manager=None)
        session = PluginSession(window, manager=manager)
        session.mount_cards(SimpleNamespace(
            mount_extension_card=lambda *a, **k: None,
            set_extension_visible=lambda *a, **k: None,
        ))

        entry = self.registry()[PLUGIN_ID]
        self.assertEqual(entry["last_good"]["version"], "1.0.0")
        self.assertNotIn("failed", entry)
        session.shutdown()


class DeferredMaintenanceTests(LifecycleTestCase):
    def _run_lease_holder(self):
        """子进程持有一个会话租约，模拟另一个还在运行的 BandScope。"""
        worker = (
            "import sys, time\n"
            "sys.path.insert(0, r'{root}')\n"
            "from bandscope.extensions.plugin_store import PluginStore\n"
            "lease = PluginStore(r'{ext}').acquire_lease()\n"
            "print(lease.name, flush=True)\n"
            "time.sleep(120)\n"
        ).format(root=str(REPO_ROOT), ext=str(self.root))
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
        process = subprocess.Popen(
            [sys.executable, "-c", worker],
            env=env,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        name = process.stdout.readline().strip()
        self.assertTrue(name)
        return process

    def test_pending_uninstall_waits_for_other_sessions_to_close(self):
        archive = make_plugin_archive(self.root / "staging" / "a.bsplugin")
        install_synthetic(archive, root=self.root)

        from bandscope.extensions.plugin_store import PluginStore

        PluginStore(self.root).request_uninstall(PLUGIN_ID)

        holder = self._run_lease_holder()
        try:
            manager = self.make_manager()
            manager.startup()
            # 有其他活跃会话：卸载与清理推迟，按请求保留。
            self.assertTrue(manager.other_sessions_active)
            self.assertTrue(manager.cleanup_deferred)
            self.assertEqual(manager.pending_uninstalls, [])
            self.assertTrue((self.root / "installed" / PLUGIN_ID).is_dir())
            self.assertTrue(self.registry()[PLUGIN_ID]["pending_uninstall"])
            self.assertTrue(any("另一个 BandScope" in note for note in manager.notes()))
        finally:
            holder.kill()
            holder.communicate(timeout=30)

        manager.shutdown()
        restarted = self.make_manager()
        restarted.startup()
        self.assertEqual(restarted.pending_uninstalls, [PLUGIN_ID])
        self.assertFalse((self.root / "installed" / PLUGIN_ID).exists())


class RestoreFlowTests(LifecycleTestCase):
    def test_failed_update_can_be_rolled_back_to_last_good(self):
        good = make_plugin_archive(self.root / "staging" / "good.bsplugin")
        install_synthetic(good, root=self.root)
        first = self.make_manager()
        first.startup()
        record = first.record(PLUGIN_ID)
        self.assertTrue(record.ready, record.load_error)
        first.mark_healthy(PLUGIN_ID)
        self.assertEqual(self.registry()[PLUGIN_ID]["last_good"]["version"], "1.0.0")
        first.shutdown()

        # 新版本在导入时直接抛异常；失败候选记入登记表且不再自动重复。
        broken = make_plugin_archive(
            self.root / "staging" / "broken.bsplugin",
            manifest=manifest_payload(version="1.1.0"),
            entry_source="raise RuntimeError('boom')\n",
        )
        install_synthetic(broken, root=self.root)

        second = self.make_manager()
        second.startup()
        record = second.record(PLUGIN_ID)
        self.assertFalse(record.ready)
        self.assertIn("boom", record.load_error)
        self.assertEqual(record.version, "1.1.0")
        self.assertEqual(record.last_good_version, "1.0.0")

        second.request_restore(PLUGIN_ID)
        self.assertTrue(record.restore_pending)
        second.shutdown()

        third = self.make_manager()
        third.startup()
        record = third.record(PLUGIN_ID)
        # 失败候选不同于 last_good：恢复 last_good，并成功加载。
        self.assertTrue(record.ready, record.load_error)
        self.assertEqual(record.running_version, "1.0.0")
        entry = self.registry()[PLUGIN_ID]
        self.assertEqual(entry["desired"]["version"], "1.0.0")
        self.assertNotIn("failed", entry)
        # 失败的 1.1.0 内容属于未引用内容，在安全启动时清理。
        self.assertFalse((self.root / "installed" / PLUGIN_ID / "1.1.0").exists())


if __name__ == "__main__":
    unittest.main()
