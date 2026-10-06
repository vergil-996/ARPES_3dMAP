"""扩展包的安装、校验、加载与生命周期（计划阶段 A、B）。

所有检查都在临时目录里做：``BANDSCOPE_EXTENSION_ROOT`` 指向 ``TemporaryDirectory``，
不会碰到用户真实的扩展安装目录。

语义要点（schema v2）：期望配置（desired）与运行实例分离——启停、卸载与更新
只改登记表，当前会话的效果保持到重启；失败候选未经用户重试不自动重复。
"""
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from bandscope.app_metadata import APP_VERSION
from bandscope.extensions.api import API_VERSION, PluginError
from bandscope.extensions.plugin_manager import (
    PLUGIN_SUFFIX,
    PluginArchiveError,
    PluginManager,
    apply_pending_uninstalls,
    inspect_archive,
    request_uninstall,
    set_enabled,
)
from tests.support.plugins import (
    DEFAULT_ENTRY_SOURCE,
    PLUGIN_ID,
    install_synthetic,
    manifest_payload,
    make_plugin_archive,
    synthetic_source,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class ManagerTestCase(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.root = Path(self._root.name)
        self._previous = os.environ.get("BANDSCOPE_EXTENSION_ROOT")
        os.environ["BANDSCOPE_EXTENSION_ROOT"] = str(self.root)
        self.addCleanup(self._restore_root)

    def _restore_root(self):
        if self._previous is None:
            os.environ.pop("BANDSCOPE_EXTENSION_ROOT", None)
        else:
            os.environ["BANDSCOPE_EXTENSION_ROOT"] = self._previous
        self._root.cleanup()

    def registry(self):
        path = self.root / "registry.json"
        return json.loads(path.read_text(encoding="utf-8"))["plugins"] if path.is_file() else {}

    def desired(self, plugin_id=PLUGIN_ID):
        return self.registry()[plugin_id]["desired"]

    def installed_files(self, plugin_id=PLUGIN_ID):
        base = self.root / "installed" / plugin_id
        if not base.is_dir():
            return []
        return sorted(p.name for p in base.rglob("*") if p.is_file())

    def make_manager(self):
        manager = PluginManager(app_version=APP_VERSION, root=self.root)
        self.addCleanup(manager.shutdown)
        return manager


class InstallTests(ManagerTestCase):
    def test_valid_package_installs_and_registers(self):
        archive = make_plugin_archive(self.root / f"good{PLUGIN_SUFFIX}")
        outcome = install_synthetic(archive, app_version=APP_VERSION)
        self.assertEqual(outcome.manifest.plugin_id, PLUGIN_ID)
        desired = self.desired()
        self.assertEqual(desired["version"], "1.0.0")
        # 内容不可变：路径含内容摘要，目录名与摘要一致。
        self.assertEqual(len(desired["digest"]), 64)
        self.assertEqual(
            desired["path"],
            f"installed/{PLUGIN_ID}/1.0.0/{desired['digest']}",
        )
        self.assertTrue((self.root / desired["path"]).is_dir())
        self.assertIn("plugin.json", self.installed_files())
        self.assertIn("entry.py", self.installed_files())
        # 来源记录：本地导入、未验证、保留原包 SHA-256。
        source = self.registry()[PLUGIN_ID]["source"]
        self.assertEqual(source["kind"], "local")
        self.assertFalse(source["verified"])
        self.assertEqual(len(source["package_sha256"]), 64)

    def test_reinstall_with_identical_content_is_idempotent(self):
        archive = make_plugin_archive(self.root / "a.bsplugin")
        first = install_synthetic(archive)
        second = install_synthetic(archive)
        self.assertFalse(first.reused)
        self.assertTrue(second.reused)
        self.assertEqual(first.digest, second.digest)
        versions = sorted(p.name for p in (self.root / "installed" / PLUGIN_ID).iterdir())
        self.assertEqual(versions, ["1.0.0"])
        self.assertEqual(self.desired()["digest"], first.digest)

    def test_same_version_with_different_content_is_refused(self):
        good = make_plugin_archive(self.root / "a.bsplugin")
        first = install_synthetic(good)
        changed = make_plugin_archive(
            self.root / "b.bsplugin",
            entry_source=DEFAULT_ENTRY_SOURCE + "\nEXTRA = 1\n",
        )
        with self.assertRaises(PluginError):
            install_synthetic(changed)
        # 原有内容与登记表都保持原样，绝不被覆盖。
        self.assertEqual(self.desired()["digest"], first.digest)
        self.assertTrue((self.root / self.desired()["path"]).is_dir())

    def test_new_version_switches_desired_and_old_content_cleans_at_next_start(self):
        install_synthetic(make_plugin_archive(self.root / "a.bsplugin"))
        install_synthetic(
            make_plugin_archive(
                self.root / "b.bsplugin", manifest=manifest_payload(version="1.1.0")
            )
        )
        self.assertEqual(self.desired()["version"], "1.1.0")
        # 旧版本内容不在安装事务里删除：先保留，等安全启动清理。
        versions = sorted(p.name for p in (self.root / "installed" / PLUGIN_ID).iterdir())
        self.assertEqual(versions, ["1.0.0", "1.1.0"])

        manager = self.make_manager()
        manager.startup()
        record = manager.record(PLUGIN_ID)
        self.assertTrue(record.ready, record.load_error)
        self.assertEqual(record.running_version, "1.1.0")
        versions = sorted(p.name for p in (self.root / "installed" / PLUGIN_ID).iterdir())
        self.assertEqual(versions, ["1.1.0"])

    def test_incompatible_app_version_is_refused_before_anything_is_written(self):
        archive = make_plugin_archive(
            self.root / "old.bsplugin", manifest=manifest_payload(requires_app="1.0.0")
        )
        with self.assertRaises(PluginError):
            install_synthetic(archive, app_version=APP_VERSION)
        self.assertEqual(self.registry(), {})
        self.assertEqual(self.installed_files(), [])

    def test_incompatible_api_version_is_refused(self):
        archive = make_plugin_archive(
            self.root / "api.bsplugin", manifest=manifest_payload(api_version=API_VERSION + 1)
        )
        with self.assertRaises(PluginError):
            install_synthetic(archive, app_version=APP_VERSION)
        self.assertEqual(self.installed_files(), [])

    def test_corrupt_container_is_refused(self):
        broken = self.root / "broken.bsplugin"
        broken.write_bytes(b"definitely not a zip")
        with self.assertRaises(PluginArchiveError):
            install_synthetic(broken)
        self.assertEqual(self.installed_files(), [])

    def test_missing_manifest_is_refused(self):
        path = self.root / "nomanifest.bsplugin"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("entry.py", "")
        with self.assertRaises(PluginArchiveError):
            install_synthetic(path)

    def test_manifest_missing_required_fields_is_refused(self):
        payload = manifest_payload()
        payload.pop("capabilities")
        archive = make_plugin_archive(self.root / "partial.bsplugin", manifest=payload)
        with self.assertRaises(PluginError):
            install_synthetic(archive)

    def test_path_traversal_is_refused(self):
        archive = make_plugin_archive(
            self.root / "escape.bsplugin", files={"../evil.py": "print('x')"}
        )
        with self.assertRaises(PluginArchiveError):
            install_synthetic(archive)
        self.assertEqual(self.installed_files(), [])

    def test_absolute_path_is_refused(self):
        archive = self.root / "abs.bsplugin"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("plugin.json", json.dumps(manifest_payload()))
            handle.writestr("C:/windows/system32/evil.py", "")
        with self.assertRaises(PluginArchiveError):
            install_synthetic(archive)

    def test_bundled_shared_library_is_refused(self):
        archive = make_plugin_archive(
            self.root / "fat.bsplugin", files={"numpy.py": "print('x')"}
        )
        with self.assertRaises(PluginArchiveError):
            install_synthetic(archive)

    def test_oversized_package_is_refused(self):
        archive = self.root / "huge.bsplugin"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
            handle.writestr("plugin.json", json.dumps(manifest_payload()))
            handle.writestr("payload.bin", b"\0" * (80 * 1024 * 1024))
        with self.assertRaises(PluginArchiveError):
            install_synthetic(archive)

    def test_missing_entry_module_is_refused(self):
        archive = self.root / "noentry.bsplugin"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("plugin.json", json.dumps(manifest_payload()))
        with self.assertRaises(PluginArchiveError):
            install_synthetic(archive)

    def test_bytecode_cache_is_refused(self):
        """字节码不在内容摘要里，加载器也不使用；含字节码的包直接拒绝。"""
        archive = make_plugin_archive(
            self.root / "pyc.bsplugin", files={"__pycache__/entry.cpython-312.pyc": "x"}
        )
        with self.assertRaises(PluginArchiveError):
            install_synthetic(archive)

    def test_version_cannot_escape_the_extension_root(self):
        """版本号会拼进安装路径、旧版本目录还会被递归删除，绝不能含路径片段。"""
        victim = self.root / "victim"
        victim.mkdir()
        (victim / "keep.txt").write_text("keep", encoding="utf-8")

        for version in (
            "../../../victim",
            "..\\..\\victim",
            "C:/Windows",
            "/absolute",
            ".hidden",
            "1.0.0/../../x",
        ):
            archive = make_plugin_archive(
                self.root / "escape.bsplugin", manifest=manifest_payload(version=version)
            )
            with self.assertRaises(PluginError, msg=version):
                install_synthetic(archive)
            with self.subTest(version=version):
                self.assertTrue((victim / "keep.txt").is_file())
        self.assertEqual(self.installed_files(), [])

    def test_drive_letter_path_after_prefix_strip_is_refused(self):
        """`myplugin/C:/...` 原样看没问题，剥掉清单前缀就是绝对路径。"""
        archive = make_plugin_archive(
            self.root / "drive.bsplugin",
            files={"C:/Users/Public/evil.py": "x = 1\n"},
            prefix="myplugin/",
        )
        with self.assertRaises(PluginArchiveError):
            install_synthetic(archive)
        self.assertEqual(self.installed_files(), [])

    def test_colon_in_a_middle_segment_is_refused(self):
        """`a/b:C/x` 剥前缀后仍不是盘符，但 Windows 上一样是非法文件名。"""
        archive = make_plugin_archive(
            self.root / "colon.bsplugin", files={"b:C/x.py": "x = 1\n"}
        )
        with self.assertRaises(PluginArchiveError):
            install_synthetic(archive)
        self.assertEqual(self.installed_files(), [])

    def test_trailing_dot_or_space_is_refused(self):
        for name in ("evil. ", "dir /x.py", "dir./x.py"):
            archive = make_plugin_archive(self.root / "trail.bsplugin", files={name: "x\n"})
            with self.assertRaises(PluginArchiveError, msg=name):
                install_synthetic(archive)

    def test_empty_path_segment_is_refused(self):
        archive = make_plugin_archive(
            self.root / "seg.bsplugin", files={"sub//evil.py": "x = 1\n"}
        )
        with self.assertRaises(PluginArchiveError):
            install_synthetic(archive)

    def test_truncated_member_becomes_a_presentable_error(self):
        """中断的下载会留下 CRC 不匹配的成员，不能让它抛成未捕获异常。"""
        archive = make_plugin_archive(
            self.root / "crc.bsplugin", files={"blob.bin": "A" * 4000}
        )
        raw = bytearray(archive.read_bytes())
        marker = b"A" * 64
        index = raw.find(marker)
        self.assertGreater(index, 0)
        for offset in range(index, index + 64):
            raw[offset] ^= 0xFF
        archive.write_bytes(bytes(raw))

        with self.assertRaises(PluginArchiveError):
            install_synthetic(archive)
        self.assertEqual(self.installed_files(), [])

    def test_encrypted_package_becomes_a_presentable_error(self):
        archive = make_plugin_archive(self.root / "enc.bsplugin")
        raw = bytearray(archive.read_bytes())
        # 置上通用位标记的加密位：本地头与中央目录都要改，zipfile 才会认。
        for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            start = 0
            while True:
                index = raw.find(signature, start)
                if index < 0:
                    break
                raw[index + flag_offset] |= 0x01
                start = index + 4
        archive.write_bytes(bytes(raw))

        with self.assertRaises(PluginArchiveError):
            install_synthetic(archive)

    def test_failed_install_keeps_the_previous_version(self):
        good = make_plugin_archive(self.root / "good.bsplugin")
        install_synthetic(good)
        broken = make_plugin_archive(
            self.root / "bad.bsplugin", manifest=manifest_payload(version="2.0.0", api_version=99)
        )
        with self.assertRaises(PluginError):
            install_synthetic(broken)
        self.assertEqual(self.desired()["version"], "1.0.0")
        self.assertIn("entry.py", self.installed_files())
        staging = self.root / "staging"
        leftovers = [p for p in staging.iterdir()] if staging.exists() else []
        self.assertEqual([p.name for p in leftovers], [])


class RealPackageTests(ManagerTestCase):
    """用仓库里的真实平带插件走一遍安装 → 加载。"""

    def build_real_package(self):
        from scripts.release.build_plugin import build as build_package

        output = self.root / "dist"
        return build_package(PLUGIN_ID, output, APP_VERSION)

    def test_real_package_installs_and_loads(self):
        archive = self.build_real_package()
        outcome = install_synthetic(archive, app_version=APP_VERSION)
        self.assertEqual(outcome.manifest.plugin_id, PLUGIN_ID)

        manager = self.make_manager()
        manager.startup()
        record = manager.record(PLUGIN_ID)
        self.assertIsNotNone(record)
        self.assertTrue(record.running, record.load_error)
        self.assertTrue(record.ready)
        self.assertEqual(record.running_version, outcome.manifest.version)

    def test_loaded_plugin_returns_a_multiplier_for_a_context(self):
        archive = self.build_real_package()
        install_synthetic(archive, app_version=APP_VERSION)
        manager = self.make_manager()
        manager.startup()
        instance = manager.record(PLUGIN_ID).instance
        from bandscope.extensions.api import EnergyAxisSpec, PluginContext

        axis = EnergyAxisSpec(
            values=__import__("numpy").linspace(-1.0, 1.0, 41),
            unit="eV",
            source="coord",
            roi_range=(-1.0, 1.0),
            full_range=(-1.0, 1.0),
        )
        context = PluginContext(page_id="p1", view="3d", energy=axis)
        instance.on_context_changed(context)
        # 空列表：不参与效果，宿主走原有渲染路径。
        self.assertIsNone(instance.opacity_multiplier(context))

        state = instance.export_state()
        state["bands"] = [{"center": 0.0, "fwhm": 0.1, "gain": 0.8, "enabled": True}]
        instance.restore_state(state)
        multiplier = instance.opacity_multiplier(context)
        self.assertEqual(len(multiplier), 41)
        self.assertAlmostEqual(float(max(multiplier)), 1.0, places=3)

    def test_failed_candidate_is_not_retried_until_the_user_asks(self):
        payload = manifest_payload(entry_point="entry:Missing")
        bad = self.root / "bad.bsplugin"
        with zipfile.ZipFile(bad, "w") as handle:
            handle.writestr("plugin.json", json.dumps(payload))
            handle.writestr("entry.py", "value = 1\n")
        install_synthetic(bad)

        manager = self.make_manager()
        manager.startup()
        record = manager.record(PLUGIN_ID)
        self.assertFalse(record.ready)
        self.assertTrue(record.load_error)
        self.assertIsNotNone(record.failed_candidate)
        self.assertEqual(manager.ready_plugins(), [])
        # 失败候选记入登记表：下次启动不再自动重复执行。
        self.assertIn("failed", self.registry()[PLUGIN_ID])
        manager.shutdown()

        second = self.make_manager()
        second.startup()
        record = second.record(PLUGIN_ID)
        self.assertFalse(record.ready)
        self.assertIn("上次加载失败", record.load_error)
        self.assertIn("可重试加载或恢复上一版本", record.load_error)

        # 用户登记重试后才再次尝试。
        second.retry_load(PLUGIN_ID)
        self.assertNotIn("failed", self.registry()[PLUGIN_ID])
        second.shutdown()
        third = self.make_manager()
        third.startup()
        record = third.record(PLUGIN_ID)
        self.assertFalse(record.ready)
        self.assertIn("Missing", record.load_error)
        self.assertNotIn("上次加载失败", record.load_error)

    def test_plugin_that_raises_on_import_is_contained(self):
        payload = manifest_payload()
        bad = self.root / "boom.bsplugin"
        with zipfile.ZipFile(bad, "w") as handle:
            handle.writestr("plugin.json", json.dumps(payload))
            handle.writestr("entry.py", "raise RuntimeError('boom')\n")
        install_synthetic(bad)
        manager = self.make_manager()
        manager.startup()
        record = manager.record(PLUGIN_ID)
        self.assertFalse(record.ready)
        self.assertIn("boom", record.load_error)

    def test_source_tree_is_never_loaded_as_an_installed_plugin(self):
        manager = self.make_manager()
        manager.startup()
        self.assertEqual(manager.records, {})
        self.assertTrue((REPO_ROOT / "plugins" / PLUGIN_ID / "entry.py").is_file())


class DesiredStateTests(ManagerTestCase):
    """启停、卸载、更新只改期望配置；当前会话的效果保持到重启。"""

    def _running_manager(self):
        install_synthetic(make_plugin_archive(self.root / "a.bsplugin"))
        manager = self.make_manager()
        manager.startup()
        record = manager.record(PLUGIN_ID)
        self.assertTrue(record.ready, record.load_error)
        return manager, record

    def test_disable_keeps_the_current_effect_until_restart(self):
        manager, record = self._running_manager()
        instance = record.instance

        manager.set_enabled(PLUGIN_ID, False)

        # 当前会话照常运行，不能通过改 load_error 或换实例立即生效。
        self.assertTrue(record.ready)
        self.assertIs(record.instance, instance)
        self.assertFalse(record.enabled)
        self.assertFalse(self.desired()["enabled"])
        manager.shutdown()

        restarted = self.make_manager()
        restarted.startup()
        record = restarted.record(PLUGIN_ID)
        self.assertIsNotNone(record)
        self.assertFalse(record.running)
        self.assertFalse(record.enabled)

    def test_toggle_writes_through_and_is_reversible_before_restart(self):
        manager, record = self._running_manager()

        manager.set_enabled(PLUGIN_ID, False)
        self.assertFalse(self.desired()["enabled"])

        # 重启之前反悔：再点一次必须真的写回去（保留最后一次选择）。
        manager.set_enabled(PLUGIN_ID, True)
        self.assertTrue(self.desired()["enabled"])
        self.assertTrue(record.enabled)

    def test_removal_keeps_the_current_effect_until_restart(self):
        manager, record = self._running_manager()
        instance = record.instance

        manager.note_removal(PLUGIN_ID)

        self.assertTrue(record.pending_removal)
        self.assertTrue(record.ready)
        self.assertIs(record.instance, instance)
        self.assertTrue(self.registry()[PLUGIN_ID]["pending_uninstall"])
        manager.shutdown()

        restarted = self.make_manager()
        restarted.startup()
        self.assertEqual(restarted.pending_uninstalls, [PLUGIN_ID])
        self.assertIsNone(restarted.record(PLUGIN_ID))
        self.assertEqual(self.installed_files(), [])
        self.assertNotIn(PLUGIN_ID, self.registry())

    def test_runtime_update_keeps_the_instance_and_switches_at_restart(self):
        manager, record = self._running_manager()
        instance = record.instance

        manager.install(
            make_plugin_archive(
                self.root / "b.bsplugin", manifest=manifest_payload(version="1.1.0")
            ),
            source=synthetic_source(),
        )

        # 候选已登记，当前实例不动；页面状态不受影响。
        self.assertTrue(record.ready)
        self.assertIs(record.instance, instance)
        self.assertEqual(record.version, "1.1.0")
        self.assertEqual(record.running_version, "1.0.0")
        manager.shutdown()

        restarted = self.make_manager()
        restarted.startup()
        record = restarted.record(PLUGIN_ID)
        self.assertTrue(record.ready, record.load_error)
        self.assertEqual(record.running_version, "1.1.0")

    def test_install_registers_the_candidate_without_loading_it(self):
        manager = self.make_manager()
        manager.startup()
        manager.install(
            make_plugin_archive(self.root / "a.bsplugin"),
            source=synthetic_source(),
        )

        record = manager.record(PLUGIN_ID)
        self.assertIsNotNone(record)
        self.assertIsNone(record.instance)
        self.assertFalse(record.ready)
        self.assertFalse(record.running)
        self.assertEqual(record.version, "1.0.0")
        self.assertEqual(manager.ready_plugins(), [])

    def test_disabled_plugin_does_not_load_at_startup(self):
        install_synthetic(make_plugin_archive(self.root / "a.bsplugin"))
        set_enabled(PLUGIN_ID, False)
        manager = self.make_manager()
        manager.startup()
        record = manager.record(PLUGIN_ID)
        self.assertIsNotNone(record)
        self.assertFalse(record.running)
        self.assertFalse(record.enabled)
        self.assertIn("entry.py", self.installed_files())


class UninstallTests(ManagerTestCase):
    def test_uninstall_takes_effect_on_the_next_startup(self):
        archive = make_plugin_archive(self.root / "a.bsplugin")
        install_synthetic(archive)
        manager = self.make_manager()
        manager.startup()
        self.assertTrue(manager.record(PLUGIN_ID).ready)
        manager.shutdown()

        request_uninstall(PLUGIN_ID)
        # 请求只是排队：磁盘上的文件要等下次启动才删。
        self.assertIn("entry.py", self.installed_files())

        restarted = self.make_manager()
        restarted.startup()
        self.assertEqual(restarted.pending_uninstalls, [PLUGIN_ID])
        self.assertIsNone(restarted.record(PLUGIN_ID))
        self.assertEqual(self.installed_files(), [])
        self.assertNotIn(PLUGIN_ID, self.registry())

    def test_reinstall_clears_a_pending_uninstall(self):
        archive = make_plugin_archive(self.root / "a.bsplugin")
        install_synthetic(archive)
        request_uninstall(PLUGIN_ID)
        install_synthetic(archive)
        manager = self.make_manager()
        manager.startup()
        self.assertEqual(manager.pending_uninstalls, [])
        self.assertTrue(manager.record(PLUGIN_ID).ready)

    def test_uninstall_target_is_resolved_from_the_id_not_the_package(self):
        """包给自己起什么路径都不能影响删除位置。"""
        archive = make_plugin_archive(self.root / "a.bsplugin", prefix="nested/")
        install_synthetic(archive)
        elsewhere = self.root / "installed" / "something_else"
        elsewhere.mkdir(parents=True, exist_ok=True)
        (elsewhere / "keep.txt").write_text("keep", encoding="utf-8")

        request_uninstall(PLUGIN_ID)
        manager = self.make_manager()
        manager.startup()
        self.assertTrue((elsewhere / "keep.txt").is_file())


if __name__ == "__main__":
    unittest.main()
