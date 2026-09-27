"""扩展包的安装、校验、加载与卸载（计划 §6、§8「安装和分发」）。

所有检查都在临时目录里做：``BANDSCOPE_EXTENSION_ROOT`` 指向 ``TemporaryDirectory``，
不会碰到用户真实的扩展安装目录。
"""
import json
import os
import shutil
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
    inspect_archive,
    install_package,
    request_uninstall,
    set_enabled,
)
from scripts.release.build_plugin import build as build_package

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ID = "flat_band_opacity"


def manifest_payload(**overrides):
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


def make_archive(path: Path, *, manifest=None, files=None, prefix=""):
    entries = {
        "plugin.json": json.dumps(manifest or manifest_payload(), ensure_ascii=False),
        "entry.py": "from plugin_api import Plugin\n\n\nclass Plugin(Plugin):\n"
                    "    def create_panel(self, host):\n        return None\n",
    }
    entries.update(files or {})
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(prefix + name, content)
    return path


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

    def installed_files(self, plugin_id=PLUGIN_ID):
        base = self.root / "installed" / plugin_id
        if not base.is_dir():
            return []
        return sorted(p.name for p in base.rglob("*") if p.is_file())


class InstallTests(ManagerTestCase):
    def test_valid_package_installs_and_registers(self):
        archive = make_archive(self.root / f"good{PLUGIN_SUFFIX}")
        manifest = install_package(archive, app_version=APP_VERSION)
        self.assertEqual(manifest.plugin_id, PLUGIN_ID)
        self.assertEqual(self.registry()[PLUGIN_ID]["version"], "1.0.0")
        self.assertIn("plugin.json", self.installed_files())
        self.assertIn("entry.py", self.installed_files())

    def test_reinstall_overwrites_the_previous_version(self):
        first = make_archive(self.root / "a.bsplugin", manifest=manifest_payload(version="1.0.0"))
        install_package(first)
        second = make_archive(self.root / "b.bsplugin", manifest=manifest_payload(version="1.1.0"))
        install_package(second)
        self.assertEqual(self.registry()[PLUGIN_ID]["version"], "1.1.0")
        versions = sorted(p.name for p in (self.root / "installed" / PLUGIN_ID).iterdir())
        self.assertEqual(versions, ["1.1.0"])

    def test_incompatible_app_version_is_refused_before_anything_is_written(self):
        archive = make_archive(
            self.root / "old.bsplugin", manifest=manifest_payload(requires_app="1.0.0")
        )
        with self.assertRaises(PluginError):
            install_package(archive, app_version=APP_VERSION)
        self.assertEqual(self.registry(), {})
        self.assertEqual(self.installed_files(), [])

    def test_incompatible_api_version_is_refused(self):
        archive = make_archive(
            self.root / "api.bsplugin", manifest=manifest_payload(api_version=API_VERSION + 1)
        )
        with self.assertRaises(PluginError):
            install_package(archive, app_version=APP_VERSION)
        self.assertEqual(self.installed_files(), [])

    def test_corrupt_container_is_refused(self):
        broken = self.root / "broken.bsplugin"
        broken.write_bytes(b"definitely not a zip")
        with self.assertRaises(PluginArchiveError):
            install_package(broken)
        self.assertEqual(self.installed_files(), [])

    def test_missing_manifest_is_refused(self):
        path = self.root / "nomanifest.bsplugin"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("entry.py", "")
        with self.assertRaises(PluginArchiveError):
            install_package(path)

    def test_manifest_missing_required_fields_is_refused(self):
        payload = manifest_payload()
        payload.pop("capabilities")
        archive = make_archive(self.root / "partial.bsplugin", manifest=payload)
        with self.assertRaises(PluginError):
            install_package(archive)

    def test_path_traversal_is_refused(self):
        archive = make_archive(
            self.root / "escape.bsplugin", files={"../evil.py": "print('x')"}
        )
        with self.assertRaises(PluginArchiveError):
            install_package(archive)
        self.assertEqual(self.installed_files(), [])

    def test_absolute_path_is_refused(self):
        archive = self.root / "abs.bsplugin"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("plugin.json", json.dumps(manifest_payload()))
            handle.writestr("C:/windows/system32/evil.py", "")
        with self.assertRaises(PluginArchiveError):
            install_package(archive)

    def test_bundled_shared_library_is_refused(self):
        archive = make_archive(
            self.root / "fat.bsplugin", files={"numpy.py": "print('x')"}
        )
        with self.assertRaises(PluginArchiveError):
            install_package(archive)

    def test_oversized_package_is_refused(self):
        archive = self.root / "huge.bsplugin"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
            handle.writestr("plugin.json", json.dumps(manifest_payload()))
            handle.writestr("payload.bin", b"\0" * (80 * 1024 * 1024))
        with self.assertRaises(PluginArchiveError):
            install_package(archive)

    def test_missing_entry_module_is_refused(self):
        archive = self.root / "noentry.bsplugin"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("plugin.json", json.dumps(manifest_payload()))
        with self.assertRaises(PluginArchiveError):
            install_package(archive)

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
            archive = make_archive(
                self.root / "escape.bsplugin", manifest=manifest_payload(version=version)
            )
            with self.assertRaises(PluginError, msg=version):
                install_package(archive)
            with self.subTest(version=version):
                self.assertTrue((victim / "keep.txt").is_file())
        self.assertEqual(self.installed_files(), [])

    def test_drive_letter_path_after_prefix_strip_is_refused(self):
        """`myplugin/C:/...` 原样看没问题，剥掉清单前缀就是绝对路径。"""
        archive = make_archive(
            self.root / "drive.bsplugin",
            files={"C:/Users/Public/evil.py": "x = 1\n"},
            prefix="myplugin/",
        )
        with self.assertRaises(PluginArchiveError):
            install_package(archive)
        self.assertEqual(self.installed_files(), [])

    def test_colon_in_a_middle_segment_is_refused(self):
        """`a/b:C/x` 剥前缀后仍不是盘符，但 Windows 上一样是非法文件名。"""
        archive = make_archive(
            self.root / "colon.bsplugin", files={"b:C/x.py": "x = 1\n"}
        )
        with self.assertRaises(PluginArchiveError):
            install_package(archive)
        self.assertEqual(self.installed_files(), [])

    def test_trailing_dot_or_space_is_refused(self):
        for name in ("evil. ", "dir /x.py", "dir./x.py"):
            archive = make_archive(self.root / "trail.bsplugin", files={name: "x\n"})
            with self.assertRaises(PluginArchiveError, msg=name):
                install_package(archive)

    def test_empty_path_segment_is_refused(self):
        archive = make_archive(
            self.root / "seg.bsplugin", files={"sub//evil.py": "x = 1\n"}
        )
        with self.assertRaises(PluginArchiveError):
            install_package(archive)

    def test_truncated_member_becomes_a_presentable_error(self):
        """中断的下载会留下 CRC 不匹配的成员，不能让它抛成未捕获异常。"""
        archive = make_archive(
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
            install_package(archive)
        self.assertEqual(self.installed_files(), [])

    def test_encrypted_package_becomes_a_presentable_error(self):
        archive = make_archive(self.root / "enc.bsplugin")
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
            install_package(archive)

    def test_failed_install_keeps_the_previous_version(self):
        good = make_archive(self.root / "good.bsplugin")
        install_package(good)
        broken = make_archive(
            self.root / "bad.bsplugin", manifest=manifest_payload(version="2.0.0", api_version=99)
        )
        with self.assertRaises(PluginError):
            install_package(broken)
        self.assertEqual(self.registry()[PLUGIN_ID]["version"], "1.0.0")
        self.assertIn("entry.py", self.installed_files())
        self.assertEqual(sorted(p.name for p in (self.root / "staging").glob("*")) if (self.root / "staging").exists() else [], [])


class RealPackageTests(ManagerTestCase):
    """用真实构建出来的扩展包走一遍安装 → 加载。"""

    def build_real_package(self):
        output = self.root / "dist"
        return build_package(PLUGIN_ID, output, APP_VERSION)

    def test_real_package_installs_and_loads(self):
        archive = self.build_real_package()
        manifest = install_package(archive, app_version=APP_VERSION)
        self.assertEqual(manifest.plugin_id, PLUGIN_ID)

        manager = PluginManager(app_version=APP_VERSION, root=self.root)
        manager.startup()
        record = manager.record(PLUGIN_ID)
        self.assertIsNotNone(record)
        self.assertTrue(record.ready, record.load_error)
        self.assertIsNotNone(record.instance)
        manager.shutdown()

    def test_loaded_plugin_returns_a_multiplier_for_a_context(self):
        archive = self.build_real_package()
        install_package(archive, app_version=APP_VERSION)
        manager = PluginManager(app_version=APP_VERSION, root=self.root)
        manager.startup()
        instance = manager.record(PLUGIN_ID).instance
        try:
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
        finally:
            manager.shutdown()

    def test_plugin_that_fails_to_load_does_not_break_the_manager(self):
        payload = manifest_payload(entry_point="entry:Missing")
        bad = self.root / "bad.bsplugin"
        with zipfile.ZipFile(bad, "w") as handle:
            handle.writestr("plugin.json", json.dumps(payload))
            handle.writestr("entry.py", "value = 1\n")
        install_package(bad)
        manager = PluginManager(app_version=APP_VERSION, root=self.root)
        manager.startup()
        record = manager.record(PLUGIN_ID)
        self.assertFalse(record.ready)
        self.assertTrue(record.load_error)
        self.assertEqual(manager.ready_plugins(), [])
        manager.shutdown()

    def test_plugin_that_raises_on_import_is_contained(self):
        payload = manifest_payload()
        bad = self.root / "boom.bsplugin"
        with zipfile.ZipFile(bad, "w") as handle:
            handle.writestr("plugin.json", json.dumps(payload))
            handle.writestr("entry.py", "raise RuntimeError('boom')\n")
        install_package(bad)
        manager = PluginManager(app_version=APP_VERSION, root=self.root)
        manager.startup()
        record = manager.record(PLUGIN_ID)
        self.assertFalse(record.ready)
        self.assertIn("boom", record.load_error)
        manager.shutdown()

    def test_source_tree_is_never_loaded_as_an_installed_plugin(self):
        manager = PluginManager(app_version=APP_VERSION, root=self.root)
        manager.startup()
        self.assertEqual(manager.records, {})
        self.assertTrue((REPO_ROOT / "plugins" / PLUGIN_ID / "entry.py").is_file())
        manager.shutdown()


class EnableToggleTests(ManagerTestCase):
    """启用/停用要在登记表与注册表两边同步。

    登记表只在启动时扫描一次；不同步的话界面会一直显示旧状态，再点一次会把同
    一个值写回去，等于改不回来。
    """

    def test_toggle_writes_through_and_is_reversible_before_restart(self):
        archive = make_archive(self.root / "a.bsplugin")
        install_package(archive)
        manager = PluginManager(app_version=APP_VERSION, root=self.root)
        manager.startup()
        self.addCleanup(manager.shutdown)
        record = manager.record(PLUGIN_ID)
        self.assertTrue(record.enabled)

        manager.set_enabled(PLUGIN_ID, False)
        self.assertFalse(record.enabled)
        self.assertFalse(self.registry()[PLUGIN_ID]["enabled"])

        # 重启之前反悔：再点一次必须真的写回去。
        manager.set_enabled(PLUGIN_ID, True)
        self.assertTrue(record.enabled)
        self.assertTrue(self.registry()[PLUGIN_ID]["enabled"])

    def test_disabled_record_reports_why_it_is_not_ready(self):
        archive = make_archive(self.root / "a.bsplugin")
        install_package(archive)
        manager = PluginManager(app_version=APP_VERSION, root=self.root)
        manager.startup()
        self.addCleanup(manager.shutdown)
        manager.set_enabled(PLUGIN_ID, False)
        record = manager.record(PLUGIN_ID)
        self.assertFalse(record.ready)
        self.assertTrue(record.load_error)

    def test_removal_is_marked_immediately(self):
        archive = make_archive(self.root / "a.bsplugin")
        install_package(archive)
        manager = PluginManager(app_version=APP_VERSION, root=self.root)
        manager.startup()
        self.addCleanup(manager.shutdown)
        manager.note_removal(PLUGIN_ID)
        record = manager.record(PLUGIN_ID)
        self.assertTrue(record.pending_removal)
        self.assertFalse(record.enabled)
        self.assertFalse(self.registry()[PLUGIN_ID]["enabled"])

    def test_register_installed_does_not_load_the_plugin(self):
        """刚装好的扩展要等重启，这里只登记、不实例化。"""
        manager = PluginManager(app_version=APP_VERSION, root=self.root)
        manager.startup()
        self.addCleanup(manager.shutdown)
        manifest, _prefix = inspect_archive(make_archive(self.root / "a.bsplugin"))
        record = manager.register_installed(manifest)
        self.assertIsNone(record.instance)
        self.assertFalse(record.ready)
        self.assertIn("重启", record.load_error)
        self.assertEqual(manager.ready_plugins(), [])


class UninstallTests(ManagerTestCase):
    def test_uninstall_takes_effect_on_the_next_startup(self):
        archive = make_archive(self.root / "a.bsplugin")
        install_package(archive)
        manager = PluginManager(app_version=APP_VERSION, root=self.root)
        manager.startup()
        self.assertTrue(manager.record(PLUGIN_ID).ready)
        manager.shutdown()

        request_uninstall(PLUGIN_ID)
        # 请求只是排队：磁盘上的文件要等下次启动才删。
        self.assertIn("entry.py", self.installed_files())

        restarted = PluginManager(app_version=APP_VERSION, root=self.root)
        restarted.startup()
        self.assertEqual(restarted.pending_uninstalls, [PLUGIN_ID])
        self.assertIsNone(restarted.record(PLUGIN_ID))
        self.assertEqual(self.installed_files(), [])
        self.assertNotIn(PLUGIN_ID, self.registry())
        restarted.shutdown()

    def test_disable_keeps_the_files_but_stops_loading(self):
        archive = make_archive(self.root / "a.bsplugin")
        install_package(archive)
        set_enabled(PLUGIN_ID, False)
        manager = PluginManager(app_version=APP_VERSION, root=self.root)
        manager.startup()
        record = manager.record(PLUGIN_ID)
        self.assertIsNotNone(record)
        self.assertFalse(record.ready)
        self.assertIn("entry.py", self.installed_files())
        manager.shutdown()

    def test_reinstall_clears_a_pending_uninstall(self):
        archive = make_archive(self.root / "a.bsplugin")
        install_package(archive)
        request_uninstall(PLUGIN_ID)
        install_package(archive)
        manager = PluginManager(app_version=APP_VERSION, root=self.root)
        manager.startup()
        self.assertEqual(manager.pending_uninstalls, [])
        self.assertTrue(manager.record(PLUGIN_ID).ready)
        manager.shutdown()

    def test_uninstall_target_is_resolved_from_the_id_not_the_package(self):
        """包给自己起什么路径都不能影响删除位置。"""
        archive = make_archive(self.root / "a.bsplugin", prefix="nested/")
        install_package(archive)
        elsewhere = self.root / "installed" / "something_else"
        elsewhere.mkdir(parents=True, exist_ok=True)
        (elsewhere / "keep.txt").write_text("keep", encoding="utf-8")

        request_uninstall(PLUGIN_ID)
        manager = PluginManager(app_version=APP_VERSION, root=self.root)
        manager.startup()
        self.assertTrue((elsewhere / "keep.txt").is_file())
        manager.shutdown()


if __name__ == "__main__":
    unittest.main()
