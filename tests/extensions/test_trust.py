# -*- coding: utf-8 -*-
"""阶段 D：来源验证与安装/加载的信任路径。

全部使用临时生成的测试密钥，不依赖正式 Secret，也不联网。验签、登记来源和
“激活前再核对内容摘要”都在这里覆盖。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bandscope.app_metadata import APP_VERSION
from bandscope.extensions import trust as trust_module
from bandscope.extensions.api import PluginError
from bandscope.extensions.plugin_manager import (
    InstallSource,
    PluginManager,
    install_package,
)
from bandscope.extensions.plugin_store import compute_content_digest
from bandscope.extensions.trust import (
    PURPOSE_CATALOG,
    PURPOSE_PLUGIN,
    SOURCE_INVALID,
    SOURCE_LEGACY,
    SOURCE_LOCAL,
    SOURCE_OFFICIAL,
    SignatureEnvelope,
    TrustError,
    build_signature,
    describe_source,
    encode_public_key,
    inspect_package_source,
    parse_signature,
    sidecar_path,
    signature_payload,
    verify_file,
    verify_signature,
    write_signature_sidecar,
)
from tests.support.plugins import (
    PLUGIN_ID,
    make_plugin_archive,
    manifest_payload,
    read_registry,
    sign_archive,
    test_signing_key,
)


class TrustTestCase(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.root = Path(self._root.name) / "extensions"
        self.key_id, self.private_key, self.trusted = test_signing_key()

    def patch_keys(self):
        patcher = mock.patch.object(trust_module, "TRUSTED_PLUGIN_KEYS", self.trusted)
        patcher.start()
        self.addCleanup(patcher.stop)

    def signed_archive(self, *, plugin_id=PLUGIN_ID, name=None, **manifest_overrides):
        # 每个用例用独立的文件名：签名 sidecar 就写在包旁边，同名会互相干扰。
        archive = make_plugin_archive(
            Path(self._root.name) / (name or f"{plugin_id}.bsplugin"),
            manifest=manifest_payload(plugin_id, **manifest_overrides),
        )
        sign_archive(archive, key_id=self.key_id, private_key=self.private_key)
        return archive

    def unsigned_archive(self, *, plugin_id=PLUGIN_ID, name=None, **manifest_overrides):
        return make_plugin_archive(
            Path(self._root.name) / (name or f"{plugin_id}.bsplugin"),
            manifest=manifest_payload(plugin_id, **manifest_overrides),
        )


class SignaturePrimitiveTests(TrustTestCase):
    def test_valid_signature_verifies(self):
        data = b"payload"
        envelope = build_signature(
            self.private_key, data, purpose=PURPOSE_PLUGIN, key_id=self.key_id
        )
        self.assertEqual(
            verify_signature(
                data, envelope, purpose=PURPOSE_PLUGIN, trusted_keys=self.trusted
            ),
            self.key_id,
        )

    def test_tampered_content_fails(self):
        envelope = build_signature(
            self.private_key, b"payload", purpose=PURPOSE_PLUGIN, key_id=self.key_id
        )
        with self.assertRaises(TrustError) as ctx:
            verify_signature(
                b"payload!",
                envelope,
                purpose=PURPOSE_PLUGIN,
                trusted_keys=self.trusted,
            )
        self.assertIn("签名校验失败", str(ctx.exception))

    def test_purpose_prefix_separates_plugin_and_catalog(self):
        # 目录签名不能搬到插件包上复用。
        envelope = build_signature(
            self.private_key, b"payload", purpose=PURPOSE_CATALOG, key_id=self.key_id
        )
        with self.assertRaises(TrustError):
            verify_signature(
                b"payload",
                envelope,
                purpose=PURPOSE_PLUGIN,
                trusted_keys=self.trusted,
            )

    def test_unknown_key_is_rejected(self):
        envelope = build_signature(
            self.private_key, b"payload", purpose=PURPOSE_PLUGIN, key_id="other-key"
        )
        with self.assertRaises(TrustError) as ctx:
            verify_signature(
                b"payload",
                envelope,
                purpose=PURPOSE_PLUGIN,
                trusted_keys=self.trusted,
            )
        self.assertIn("未知密钥", str(ctx.exception))

    def test_payload_includes_purpose_and_protocol(self):
        self.assertNotEqual(
            signature_payload(PURPOSE_PLUGIN, b"x"),
            signature_payload(PURPOSE_CATALOG, b"x"),
        )
        self.assertNotEqual(
            signature_payload(PURPOSE_PLUGIN, b"x", protocol=1),
            signature_payload(PURPOSE_PLUGIN, b"x", protocol=2),
        )

    def test_bad_sidecars_are_reported_not_guessed(self):
        cases = {
            "not json": b"{",
            "list": b"[]",
            "protocol": json.dumps(
                {"protocol": 99, "key_id": "k", "algorithm": "ed25519", "signature": "AA=="}
            ).encode(),
            "algorithm": json.dumps(
                {"protocol": 1, "key_id": "k", "algorithm": "rsa", "signature": "AA=="}
            ).encode(),
            "key": json.dumps(
                {"protocol": 1, "key_id": "", "algorithm": "ed25519", "signature": "AA=="}
            ).encode(),
            "signature": json.dumps(
                {"protocol": 1, "key_id": "k", "algorithm": "ed25519", "signature": "!!"}
            ).encode(),
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(TrustError):
                    parse_signature(payload)

    def test_envelope_round_trips_through_the_sidecar(self):
        envelope = SignatureEnvelope(1, self.key_id, "ed25519", b"\x01\x02")
        self.assertEqual(
            parse_signature(json.dumps(envelope.to_payload())), envelope
        )


class PackageSourceTests(TrustTestCase):
    def test_unsigned_package_is_local_unverified(self):
        archive = self.unsigned_archive()
        source = inspect_package_source(archive)
        self.assertEqual(source.kind, SOURCE_LOCAL)
        self.assertFalse(source.verified)
        self.assertFalse(source.rejected)

    def test_signed_package_with_builtin_key_is_official(self):
        self.patch_keys()
        archive = self.signed_archive()
        source = inspect_package_source(archive)
        self.assertEqual(source.kind, SOURCE_OFFICIAL)
        self.assertEqual(source.key_id, self.key_id)

    def test_signed_package_without_matching_builtin_key_is_rejected(self):
        # 内置公钥为空（或没有这一把）：必须拒绝，不能降级成未验证包。
        archive = self.signed_archive()
        source = inspect_package_source(archive)
        self.assertEqual(source.kind, SOURCE_INVALID)
        self.assertTrue(source.rejected)
        self.assertIn("未知密钥", source.reason)

    def test_tampered_package_is_rejected(self):
        self.patch_keys()
        archive = self.signed_archive()
        with archive.open("ab") as handle:
            handle.write(b"\x00")
        source = inspect_package_source(archive)
        self.assertEqual(source.kind, SOURCE_INVALID)
        self.assertTrue(source.rejected)

    def test_verify_file_reads_the_sidecar(self):
        self.patch_keys()
        archive = self.signed_archive()
        self.assertTrue(sidecar_path(archive).is_file())
        self.assertEqual(
            verify_file(archive, purpose=PURPOSE_PLUGIN, trusted_keys=self.trusted),
            self.key_id,
        )

    def test_describe_source_distinguishes_three_states(self):
        self.assertIn("官方已验证", describe_source({"kind": SOURCE_OFFICIAL, "verified": True, "key_id": "k"}))
        self.assertIn("本地未验证", describe_source({"kind": SOURCE_LOCAL}))
        self.assertIn("历史未验证", describe_source({"kind": SOURCE_LEGACY}))


class TrustedInstallTests(TrustTestCase):
    def test_unsigned_package_needs_explicit_confirmation(self):
        archive = self.unsigned_archive()
        with self.assertRaises(PluginError) as ctx:
            install_package(archive, root=self.root)
        self.assertIn("没有官方签名", str(ctx.exception))
        # 拒绝时不留任何登记表内容。
        self.assertEqual(read_registry(self.root), {})

    def test_confirmed_unsigned_package_records_local_source(self):
        archive = self.unsigned_archive()
        install_package(
            archive,
            root=self.root,
            source=InstallSource(kind=SOURCE_LOCAL, accepted_unverified=True),
        )
        source = read_registry(self.root)["plugins"][PLUGIN_ID]["source"]
        self.assertEqual(source["kind"], SOURCE_LOCAL)
        self.assertFalse(source["verified"])
        self.assertTrue(source["accepted_unverified"])

    def test_signed_package_records_official_source(self):
        self.patch_keys()
        archive = self.signed_archive()
        install_package(archive, root=self.root, source=InstallSource(kind=SOURCE_OFFICIAL))
        source = read_registry(self.root)["plugins"][PLUGIN_ID]["source"]
        self.assertEqual(source["kind"], SOURCE_OFFICIAL)
        self.assertTrue(source["verified"])
        self.assertEqual(source["key_id"], self.key_id)

    def test_signed_package_cannot_be_installed_as_official_without_builtin_key(self):
        archive = self.signed_archive()
        with self.assertRaises(PluginError):
            install_package(archive, root=self.root, source=InstallSource(kind=SOURCE_OFFICIAL))

    def test_expectations_from_the_catalog_are_enforced(self):
        archive = self.unsigned_archive()
        accepted = InstallSource(kind=SOURCE_LOCAL, accepted_unverified=True)
        with self.assertRaises(PluginError) as ctx:
            install_package(
                archive, root=self.root,
                source=InstallSource(
                    kind=SOURCE_LOCAL, accepted_unverified=True, expected_id="something_else"
                ),
            )
        self.assertIn("id", str(ctx.exception))
        with self.assertRaises(PluginError):
            install_package(
                archive, root=self.root,
                source=InstallSource(
                    kind=SOURCE_LOCAL, accepted_unverified=True, expected_version="9.9.9"
                ),
            )
        with self.assertRaises(PluginError):
            install_package(
                archive, root=self.root,
                source=InstallSource(
                    kind=SOURCE_LOCAL, accepted_unverified=True, expected_sha256="a" * 64
                ),
            )
        # 身份与摘要都对得上时正常安装。
        import hashlib

        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        install_package(
            archive, root=self.root,
            source=InstallSource(
                kind=SOURCE_LOCAL,
                accepted_unverified=True,
                expected_id=PLUGIN_ID,
                expected_sha256=digest,
            ),
        )
        self.assertIn(PLUGIN_ID, read_registry(self.root)["plugins"])

    def test_official_install_cannot_be_silently_replaced_by_an_unsigned_package(self):
        self.patch_keys()
        signed = self.signed_archive()
        install_package(signed, root=self.root, source=InstallSource(kind=SOURCE_OFFICIAL))

        unsigned = self.unsigned_archive(name="unsigned.bsplugin", version="1.0.1")
        with self.assertRaises(PluginError) as ctx:
            install_package(
                unsigned,
                root=self.root,
                source=InstallSource(kind=SOURCE_LOCAL, accepted_unverified=True),
            )
        self.assertIn("官方已验证", str(ctx.exception))

        # 明确确认来源转换后才允许覆盖。
        install_package(
            unsigned,
            root=self.root,
            source=InstallSource(
                kind=SOURCE_LOCAL,
                accepted_unverified=True,
                accepted_downgrade=True,
            ),
        )
        source = read_registry(self.root)["plugins"][PLUGIN_ID]["source"]
        self.assertEqual(source["kind"], SOURCE_LOCAL)


class ActivationIntegrityTests(TrustTestCase):
    def test_content_changed_after_install_is_refused_at_load(self):
        archive = self.unsigned_archive()
        install_package(
            archive,
            root=self.root,
            source=InstallSource(kind=SOURCE_LOCAL, accepted_unverified=True),
        )
        state = read_registry(self.root)
        entry_path = state["plugins"][PLUGIN_ID]["desired"]["path"]
        installed = self.root / entry_path
        self.assertEqual(compute_content_digest(installed), state["plugins"][PLUGIN_ID]["desired"]["digest"])

        # 提交之后、重启加载之前内容被改动：不能再照着旧摘要执行。
        (installed / "entry.py").write_text(
            "from plugin_api import Plugin\n\n\nclass Plugin(Plugin):\n    pass\n",
            encoding="utf-8",
        )
        manager = PluginManager(root=self.root)
        self.addCleanup(manager.shutdown)
        manager.startup()
        record = manager.record(PLUGIN_ID)
        self.assertFalse(record.ready)
        self.assertIn("内容摘要", record.load_error)

    def test_untouched_content_still_loads(self):
        archive = self.unsigned_archive()
        install_package(
            archive,
            root=self.root,
            source=InstallSource(kind=SOURCE_LOCAL, accepted_unverified=True),
        )
        manager = PluginManager(root=self.root)
        self.addCleanup(manager.shutdown)
        manager.startup()
        self.assertTrue(manager.record(PLUGIN_ID).ready)

    def test_migration_marks_legacy_sources(self):
        from tests.support.plugins import stage_plugin_content

        # 直接构造一份 v1 登记表，确认迁移后来源标成历史未验证。
        plugin_dir = self.root / "installed" / PLUGIN_ID / "1.0.0"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps(manifest_payload()), encoding="utf-8"
        )
        (plugin_dir / "entry.py").write_text(
            "from plugin_api import Plugin\n\n\nclass Plugin(Plugin):\n"
            "    def create_panel(self, host):\n        return None\n",
            encoding="utf-8",
        )
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "registry.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "plugins": {
                        PLUGIN_ID: {
                            "name": "平带增强",
                            "version": "1.0.0",
                            "path": f"{PLUGIN_ID}/1.0.0",
                            "enabled": True,
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        store_path = self.root / "registry.json"
        manager = PluginManager(root=self.root)
        self.addCleanup(manager.shutdown)
        manager.startup()
        self.assertEqual(manager.registry_error, "")
        source = read_registry(self.root)["plugins"][PLUGIN_ID]["source"]
        self.assertEqual(source["kind"], SOURCE_LEGACY)
        self.assertFalse(source["verified"])
        self.assertTrue(store_path.is_file())


class SignPluginCliTests(unittest.TestCase):
    """发布脚本的路径处理：PowerShell 与 cmd 不替原生程序展开通配符。

    发布工作流第一次真跑签名步骤时，``release/*.bsplugin`` 被原样当成文件名传进来，
    脚本报“找不到文件”直接失败；这里锁定“模式由脚本自己展开”的行为。
    """

    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.folder = Path(self._root.name)
        self.key_id, self.private_key, _trusted = test_signing_key("cli-key")
        # 就地修改内置公钥表：发布脚本在导入时绑定了同一个字典对象，替换属性影响不到
        # 它（发布链路验收脚本同样用就地修改）。
        patcher = mock.patch.dict(
            trust_module.TRUSTED_PLUGIN_KEYS,
            {self.key_id: encode_public_key(self.private_key.public_key())},
            clear=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        from cryptography.hazmat.primitives import serialization

        self.key_file = self.folder / "key.pem"
        self.key_file.write_bytes(
            self.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    def sign(self, pattern: str) -> int:
        from scripts.release.sign_plugin import main as sign_main

        return sign_main(
            [pattern, "--key-file", str(self.key_file), "--key-id", self.key_id]
        )

    def test_pattern_signs_every_match(self):
        (self.folder / "a.bsplugin").write_bytes(b"a")
        (self.folder / "b.bsplugin").write_bytes(b"b")

        self.assertEqual(self.sign(str(self.folder / "*.bsplugin")), 0)

        self.assertTrue((self.folder / "a.bsplugin.sig").is_file())
        self.assertTrue((self.folder / "b.bsplugin.sig").is_file())

    def test_pattern_without_matches_still_fails(self):
        # 没有匹配时保持原样交给“找不到文件”，不能静默地一个都不签。
        self.assertEqual(self.sign(str(self.folder / "*.bsplugin")), 1)


class TrustModuleBoundaryTests(unittest.TestCase):
    def test_trust_module_does_not_import_graphics(self):
        import subprocess
        import sys

        script = (
            "import sys;"
            "import bandscope.extensions.trust;"
            "print('PyQt5' in sys.modules or 'vtk' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
