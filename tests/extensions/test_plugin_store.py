# -*- coding: utf-8 -*-
"""存储层：schema v2、v1 迁移、内容摘要、损坏保护、锁与租约、安全启动维护。

全部在临时目录里操作；跨进程用例通过子进程验证锁与租约的真实语义。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bandscope.app_metadata import APP_VERSION
from bandscope.extensions.api import PluginError
from bandscope.extensions.plugin_store import (
    PluginStore,
    compute_content_digest,
)
from tests.support.plugins import (
    DEFAULT_ENTRY_SOURCE,
    PLUGIN_ID,
    install_synthetic,
    make_plugin_archive,
    manifest_payload,
    stage_plugin_content,
    v2_entry,
    write_v2_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.root = Path(self._root.name) / "extensions"
        self.store = PluginStore(self.root)

    def registry_payload(self):
        return json.loads((self.root / "registry.json").read_text(encoding="utf-8"))


class RegistryTests(StoreTestCase):
    def test_update_bumps_the_revision_and_leaves_no_temporary_files(self):
        first = self.store.update(lambda payload: payload.setdefault("plugins", {}))
        second = self.store.update(lambda payload: payload.setdefault("plugins", {}))
        self.assertEqual((first, second), (1, 2))
        self.assertEqual(self.registry_payload()["revision"], 2)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.assertEqual(list(self.root.glob("registry.json.*.tmp")), [])

    def test_update_without_changes_does_not_rewrite_the_file(self):
        self.store.update(lambda payload: payload.setdefault("plugins", {}))
        before = (self.root / "registry.json").read_bytes()
        revision = self.store.update(lambda payload: False)
        self.assertEqual(revision, 1)
        self.assertEqual((self.root / "registry.json").read_bytes(), before)

    def test_corrupt_registry_is_preserved_and_writes_are_refused(self):
        self.root.mkdir(parents=True, exist_ok=True)
        registry = self.root / "registry.json"
        registry.write_text("{ definitely not json", encoding="utf-8")

        state = self.store.read_state()
        self.assertTrue(state.error)
        with self.assertRaises(PluginError):
            self.store.update(lambda payload: payload.setdefault("plugins", {}))
        # 禁止以空表覆盖。
        self.assertEqual(registry.read_text(encoding="utf-8"), "{ definitely not json")

    def test_unknown_schema_version_is_preserved(self):
        write_v2_registry(self.root, {})
        payload = json.loads((self.root / "registry.json").read_text(encoding="utf-8"))
        payload["version"] = 99
        (self.root / "registry.json").write_text(json.dumps(payload), encoding="utf-8")

        state = self.store.read_state()
        self.assertIn("99", state.error)
        with self.assertRaises(PluginError):
            self.store.set_desired_enabled("band_beta", True)

    def test_unconfirmed_entry_survives_other_writes_verbatim(self):
        ghost = {"weird": ["structure"]}
        write_v2_registry(self.root, {"ghost_plugin": ghost})
        # 正常安装另一个插件：未确认条目必须原样保留、不参与解析。
        archive = make_plugin_archive(self.root / "a.bsplugin")
        install_synthetic(archive, root=self.root)

        state = self.store.read_state()
        self.assertFalse(state.entries["ghost_plugin"].valid)
        self.assertEqual(self.registry_payload()["plugins"]["ghost_plugin"], ghost)
        with self.assertRaises(PluginError):
            self.store.set_desired_enabled("ghost_plugin", True)

    def test_atomic_write_failure_keeps_the_original_and_cleans_temporaries(self):
        self.store.update(lambda payload: payload.setdefault("plugins", {}))
        before = (self.root / "registry.json").read_bytes()

        real_replace = os.replace

        def flaky(source, destination, *args, **kwargs):
            if str(destination).endswith("registry.json"):
                raise OSError("disk full")
            return real_replace(source, destination, *args, **kwargs)

        with mock.patch(
            "bandscope.extensions.plugin_store.os.replace", side_effect=flaky
        ):
            with self.assertRaises(OSError):
                self.store.update(lambda payload: payload.setdefault("plugins", {}))
        self.assertEqual((self.root / "registry.json").read_bytes(), before)
        self.assertEqual(list(self.root.glob("registry.json.*.tmp")), [])


class DigestTests(StoreTestCase):
    def _write(self, directory, files):
        for name, content in files.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def test_digest_is_independent_of_creation_order(self):
        first = self.root / "a"
        second = self.root / "b"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        files = {"one.py": "x = 1\n", "sub/two.py": "y = 2\n"}
        self._write(first, files)
        self._write(second, dict(reversed(list(files.items()))))
        self.assertEqual(compute_content_digest(first), compute_content_digest(second))

    def test_digest_changes_with_content(self):
        directory = self.root / "c"
        directory.mkdir(parents=True)
        self._write(directory, {"one.py": "x = 1\n"})
        before = compute_content_digest(directory)
        self._write(directory, {"one.py": "x = 2\n"})
        self.assertNotEqual(before, compute_content_digest(directory))

    def test_digest_ignores_bytecode_caches(self):
        directory = self.root / "d"
        directory.mkdir(parents=True)
        self._write(directory, {"one.py": "x = 1\n"})
        before = compute_content_digest(directory)
        self._write(directory, {"__pycache__/one.cpython-312.pyc": "cache"})
        self.assertEqual(before, compute_content_digest(directory))


class MigrationTests(StoreTestCase):
    def _v1_layout(self, *, version="1.0.0", enabled=True, with_marker=False):
        content = self.root / "installed" / PLUGIN_ID / version
        content.mkdir(parents=True)
        (content / "plugin.json").write_text(
            json.dumps(manifest_payload(version=version), ensure_ascii=False),
            encoding="utf-8",
        )
        (content / "entry.py").write_text(DEFAULT_ENTRY_SOURCE, encoding="utf-8")
        # 旧版本会在这里留下字节码缓存：迁移时应清掉且不进入摘要。
        (content / "__pycache__").mkdir()
        (content / "__pycache__" / "entry.cpython-312.pyc").write_bytes(b"old")
        (self.root / "registry.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "plugins": {
                        PLUGIN_ID: {
                            "version": version,
                            "name": "平带增强",
                            "path": f"{PLUGIN_ID}\\{version}",
                            "enabled": enabled,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        if with_marker:
            marker_dir = self.root / "pending_uninstall"
            marker_dir.mkdir()
            (marker_dir / f"{PLUGIN_ID}.json").write_text(
                json.dumps({"plugin_id": PLUGIN_ID}), encoding="utf-8"
            )
        return content

    def test_v1_migrates_with_backup_and_content_relocation(self):
        self._v1_layout()

        self.assertEqual(self.store.ensure_schema(), "")

        payload = self.registry_payload()
        self.assertEqual(payload["version"], 2)
        entry = payload["plugins"][PLUGIN_ID]
        desired = entry["desired"]
        self.assertEqual(desired["version"], "1.0.0")
        self.assertTrue(desired["enabled"])
        digest = desired["digest"]
        self.assertEqual(len(digest), 64)
        self.assertEqual(
            desired["path"], f"installed/{PLUGIN_ID}/1.0.0/{digest}"
        )
        # 内容已搬进摘要子目录，字节码缓存被清掉。
        content = self.root / desired["path"]
        self.assertTrue((content / "plugin.json").is_file())
        self.assertFalse((content / "__pycache__").exists())
        self.assertEqual(digest, compute_content_digest(content))
        # 迁移前的原文件有备份，迁移后旧布局目录消失。
        backup = self.root / "registry.json.v1.bak"
        self.assertTrue(backup.is_file())
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["version"], 1)
        self.assertFalse((self.root / "installed" / PLUGIN_ID / "1.0.0" / "__pycache__").exists())

    def test_migration_preserves_disabled_state_and_clears_markers_after_commit(self):
        self._v1_layout(enabled=False, with_marker=True)

        self.assertEqual(self.store.ensure_schema(), "")

        entry = self.registry_payload()["plugins"][PLUGIN_ID]
        self.assertFalse(entry["desired"]["enabled"])
        self.assertTrue(entry["pending_uninstall"])
        # v2 原子提交成功后才清理旧卸载标记。
        self.assertFalse(
            (self.root / "pending_uninstall" / f"{PLUGIN_ID}.json").is_file()
        )

    def test_v1_entry_with_missing_content_stays_unconfirmed(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "registry.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "plugins": {
                        PLUGIN_ID: {
                            "version": "1.0.0",
                            "name": "平带增强",
                            "path": f"{PLUGIN_ID}\\1.0.0",
                            "enabled": True,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(self.store.ensure_schema(), "")

        # 迁移仍然提交；无法确认的条目原样保留，只读展示，不参与加载。
        state = self.store.read_state()
        self.assertEqual(self.registry_payload()["version"], 2)
        self.assertFalse(state.entries[PLUGIN_ID].valid)
        self.assertIn("version", self.registry_payload()["plugins"][PLUGIN_ID])

    def test_write_failure_leaves_the_v1_file_untouched(self):
        self._v1_layout()
        original = (self.root / "registry.json").read_bytes()

        real_replace = os.replace

        def flaky(source, destination, *args, **kwargs):
            if str(destination).endswith("registry.json"):
                raise OSError("disk full")
            return real_replace(source, destination, *args, **kwargs)

        with mock.patch(
            "bandscope.extensions.plugin_store.os.replace", side_effect=flaky
        ):
            error = self.store.ensure_schema()
        self.assertTrue(error)
        self.assertEqual((self.root / "registry.json").read_bytes(), original)

    def test_recover_relocations_moves_back_a_leftover(self):
        plugin_dir = self.root / "installed" / PLUGIN_ID
        leftover = plugin_dir / ".migrate-1.0.0-deadbeef"
        leftover.mkdir(parents=True)
        (leftover / "plugin.json").write_text(
            json.dumps(manifest_payload()), encoding="utf-8"
        )
        (leftover / "entry.py").write_text(DEFAULT_ENTRY_SOURCE, encoding="utf-8")

        recovered = self.store.recover_relocations()

        self.assertEqual(len(recovered), 1)
        version_dir = plugin_dir / "1.0.0"
        self.assertTrue((version_dir / "plugin.json").is_file())
        self.assertFalse(leftover.exists())

    def test_recover_relocations_discards_a_redundant_leftover(self):
        plugin_dir = self.root / "installed" / PLUGIN_ID
        version_dir = plugin_dir / "1.0.0"
        version_dir.mkdir(parents=True)
        (version_dir / "plugin.json").write_text(
            json.dumps(manifest_payload()), encoding="utf-8"
        )
        leftover = plugin_dir / ".migrate-1.0.0-deadbeef"
        leftover.mkdir()
        (leftover / "plugin.json").write_text("{}", encoding="utf-8")

        self.store.recover_relocations()

        self.assertFalse(leftover.exists())
        self.assertTrue((version_dir / "plugin.json").is_file())

    def test_interrupted_relocation_can_still_migrate(self):
        """上次迁移在搬目录的中途中断：先恢复，再迁移成功。"""
        content = self._v1_layout()
        # 模拟中断：内容在 .migrate-* 里，版本目录缺失。
        leftover = content.parent / f".migrate-1.0.0-abcdef12"
        os.replace(content, leftover)

        self.store.recover_relocations()
        self.assertEqual(self.store.ensure_schema(), "")
        entry = self.registry_payload()["plugins"][PLUGIN_ID]
        self.assertTrue((self.root / entry["desired"]["path"] / "plugin.json").is_file())


class CleanupTests(StoreTestCase):
    def test_cleanup_keeps_desired_last_good_and_previous_good_only(self):
        desired_digest, desired_path = stage_plugin_content(
            self.root, PLUGIN_ID, manifest=manifest_payload(version="1.0.0")
        )
        good_digest, good_path = stage_plugin_content(
            self.root, PLUGIN_ID, manifest=manifest_payload(version="0.9.0")
        )
        stage_plugin_content(
            self.root, PLUGIN_ID, manifest=manifest_payload(version="2.0.0")
        )
        write_v2_registry(
            self.root,
            {
                PLUGIN_ID: v2_entry(
                    manifest_payload(version="1.0.0"),
                    digest=desired_digest,
                    path=desired_path,
                    last_good={
                        "version": "0.9.0",
                        "digest": good_digest,
                        "path": good_path,
                        "revision": 1,
                    },
                )
            },
        )

        removed = self.store.cleanup_unreferenced()

        versions = sorted(
            p.name for p in (self.root / "installed" / PLUGIN_ID).iterdir()
        )
        self.assertEqual(versions, ["0.9.0", "1.0.0"])
        self.assertEqual(len(removed), 1)
        self.assertIn("2.0.0", removed[0])

    def test_cleanup_skips_pending_uninstall_plugins(self):
        digest, path = stage_plugin_content(self.root, PLUGIN_ID)
        write_v2_registry(
            self.root,
            {
                PLUGIN_ID: v2_entry(
                    manifest_payload(), digest=digest, path=path, pending_uninstall=True
                )
            },
        )
        self.assertEqual(self.store.cleanup_unreferenced(), [])
        self.assertTrue((self.root / path / "plugin.json").is_file())

    def test_cleanup_removes_content_without_a_registry_entry(self):
        digest, path = stage_plugin_content(self.root, PLUGIN_ID)
        write_v2_registry(self.root, {})
        removed = self.store.cleanup_unreferenced()
        self.assertEqual(len(removed), 1)
        self.assertFalse((self.root / path).exists())


class UninstallFailureTests(StoreTestCase):
    def test_delete_failure_keeps_the_request_and_retries_next_start(self):
        archive = make_plugin_archive(self.root / "a.bsplugin")
        install_synthetic(archive, root=self.root)
        self.store.request_uninstall(PLUGIN_ID)

        real_rmtree = shutil.rmtree
        calls = {"count": 0}

        def flaky(path, *args, **kwargs):
            if calls["count"] == 0 and Path(path).name == PLUGIN_ID:
                calls["count"] += 1
                raise OSError("file is in use")
            return real_rmtree(path, *args, **kwargs)

        with mock.patch(
            "bandscope.extensions.plugin_store.shutil.rmtree", side_effect=flaky
        ):
            removed, failures = self.store.apply_pending_uninstalls()

        self.assertEqual(removed, [])
        self.assertTrue(failures)
        entry = self.registry_payload()["plugins"][PLUGIN_ID]
        self.assertTrue(entry["pending_uninstall"])
        self.assertEqual(entry["last_error"]["phase"], "uninstall")
        self.assertTrue(
            (self.root / "installed" / PLUGIN_ID).is_dir()
        )

        # 下一次安全启动重试成功后才从列表清除。
        removed, failures = self.store.apply_pending_uninstalls()
        self.assertEqual(removed, [PLUGIN_ID])
        self.assertEqual(failures, [])
        self.assertNotIn(PLUGIN_ID, self.registry_payload()["plugins"])
        self.assertFalse((self.root / "installed" / PLUGIN_ID).exists())


class RealFileLockTests(StoreTestCase):
    """Windows 上真实的“文件被占用”：删除失败必须保留待卸载记录。"""

    @unittest.skipUnless(os.name == "nt", "只在 Windows 上验证真实文件占用")
    def test_uninstall_retries_after_the_file_is_released(self):
        archive = make_plugin_archive(self.root / "a.bsplugin")
        install_synthetic(archive, root=self.root)
        self.store.request_uninstall(PLUGIN_ID)

        entry_path = next(
            (self.root / "installed" / PLUGIN_ID).rglob("plugin.json")
        )
        with open(entry_path, "rb"):
            # CRT 默认不共享删除：句柄未释放时目录删不掉。
            removed, failures = self.store.apply_pending_uninstalls()
            self.assertEqual(removed, [])
            self.assertTrue(failures)
            self.assertTrue(
                self.registry_payload()["plugins"][PLUGIN_ID]["pending_uninstall"]
            )

        removed, failures = self.store.apply_pending_uninstalls()
        self.assertEqual(removed, [PLUGIN_ID])
        self.assertEqual(failures, [])


class RestoreRequestTests(StoreTestCase):
    def _registry_with_restore(self, *, desired, last_good, previous_good=None):
        digest, path = stage_plugin_content(self.root, PLUGIN_ID)
        write_v2_registry(
            self.root,
            {
                PLUGIN_ID: v2_entry(
                    manifest_payload(),
                    digest=digest,
                    path=path,
                    restore_requested=True,
                    last_good=last_good,
                    previous_good=previous_good,
                )
            },
        )
        return digest, path

    def test_restore_prefers_last_good_when_the_candidate_differs(self):
        desired_digest, _path = self._registry_with_restore(
            desired=None,
            last_good={
                "version": "0.9.0",
                "digest": "a" * 64,
                "path": f"installed/{PLUGIN_ID}/0.9.0/{'a' * 64}",
                "revision": 1,
            },
        )
        # 当前候选（1.0.0/desired_digest）与 last_good 不同：目标是 last_good。
        # 目标内容不存在 → 恢复被拒绝并记错误，候选保持不变。
        applied = self.store.apply_restore_requests(APP_VERSION)
        self.assertTrue(applied)
        entry = self.registry_payload()["plugins"][PLUGIN_ID]
        self.assertFalse(entry["restore_requested"])
        self.assertEqual(entry["desired"]["digest"], desired_digest)
        self.assertEqual(entry["last_error"]["phase"], "restore")

    def test_restore_target_must_be_compatible(self):
        _digest, _path = stage_plugin_content(
            self.root, PLUGIN_ID, manifest=manifest_payload(version="0.9.0")
        )
        other_digest, other_path = stage_plugin_content(
            self.root, PLUGIN_ID, manifest=manifest_payload(version="1.0.0")
        )
        write_v2_registry(
            self.root,
            {
                PLUGIN_ID: v2_entry(
                    manifest_payload(version="1.0.0"),
                    digest=other_digest,
                    path=other_path,
                    restore_requested=True,
                    last_good={
                        "version": "0.9.0",
                        "digest": _digest,
                        "path": _path,
                        "revision": 1,
                    },
                )
            },
        )
        # 让 last_good 目标与当前宿主不兼容。
        manifest_path = self.root / _path / "plugin.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["requires_app"] = "0.0.1"
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        applied = self.store.apply_restore_requests(APP_VERSION)

        self.assertTrue(applied)
        entry = self.registry_payload()["plugins"][PLUGIN_ID]
        self.assertFalse(entry["restore_requested"])
        self.assertEqual(entry["desired"]["digest"], other_digest)  # 未被切换
        self.assertIn("未恢复", entry["last_error"]["message"])

    def test_restore_switches_to_previous_good_when_candidate_is_last_good(self):
        good_digest, good_path = stage_plugin_content(
            self.root, PLUGIN_ID, manifest=manifest_payload(version="1.0.0")
        )
        old_digest, old_path = stage_plugin_content(
            self.root, PLUGIN_ID, manifest=manifest_payload(version="0.9.0")
        )
        write_v2_registry(
            self.root,
            {
                PLUGIN_ID: v2_entry(
                    manifest_payload(version="1.0.0"),
                    digest=good_digest,
                    path=good_path,
                    restore_requested=True,
                    last_good={
                        "version": "1.0.0",
                        "digest": good_digest,
                        "path": good_path,
                        "revision": 1,
                    },
                    previous_good={
                        "version": "0.9.0",
                        "digest": old_digest,
                        "path": old_path,
                        "revision": 1,
                    },
                )
            },
        )

        applied = self.store.apply_restore_requests(APP_VERSION)

        self.assertTrue(applied)
        entry = self.registry_payload()["plugins"][PLUGIN_ID]
        # 当前候选已经是 last_good：退回 previous_good。
        self.assertEqual(entry["desired"]["version"], "0.9.0")
        self.assertEqual(entry["desired"]["digest"], old_digest)


class LockAndLeaseTests(StoreTestCase):
    def test_lock_is_reentrant_and_released_after_errors(self):
        with self.store.lock():
            with self.store.lock():
                pass
        try:
            with self.store.lock():
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # 异常后必须能再次拿到锁。
        with self.store.lock():
            pass

    def test_two_processes_do_not_lose_updates(self):
        worker = (
            "import sys\n"
            "sys.path.insert(0, r'{root}')\n"
            "from bandscope.extensions.plugin_store import PluginStore\n"
            "store = PluginStore(r'{ext}')\n"
            "def bump(payload):\n"
            "    payload['counter'] = int(payload.get('counter') or 0) + 1\n"
            "for _ in range(5):\n"
            "    store.update(bump)\n"
        ).format(root=str(REPO_ROOT), ext=str(self.root))
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", worker],
                env=env,
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(3)
        ]
        for process in processes:
            _out, err = process.communicate(timeout=120)
            self.assertEqual(process.returncode, 0, err.decode("utf-8", "replace"))

        payload = self.registry_payload()
        self.assertEqual(payload["counter"], 15)
        self.assertEqual(payload["revision"], 15)

    def test_live_leases_are_detected_and_stale_ones_cleaned(self):
        worker = (
            "import sys, time\n"
            "sys.path.insert(0, r'{root}')\n"
            "from bandscope.extensions.plugin_store import PluginStore\n"
            "lease = PluginStore(r'{ext}').acquire_lease()\n"
            "print(lease.name, flush=True)\n"
            "time.sleep(60)\n"
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
        try:
            name = process.stdout.readline().strip()
            self.assertTrue(name)
            active = self.store.other_active_leases(exclude="")
            self.assertIn(name, active)
        finally:
            process.kill()
            process.communicate(timeout=30)

        # 进程退出后锁由操作系统释放：陈旧租约被识别并清理。
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not self.store.other_active_leases(exclude=""):
                break
            time.sleep(0.2)
        self.assertEqual(self.store.other_active_leases(exclude=""), [])
        self.assertEqual(list((self.root / "leases").glob("*.lock")), [])


if __name__ == "__main__":
    unittest.main()
