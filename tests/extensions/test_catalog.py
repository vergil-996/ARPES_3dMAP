# -*- coding: utf-8 -*-
"""阶段 E：官方目录的解析、验签、缓存、版本选择与下载。

不联网：下载用假的 opener，签名用临时生成的测试密钥。
"""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bandscope.app_metadata import APP_VERSION
from bandscope.extensions import catalog as catalog_module
from bandscope.extensions import trust as trust_module
from bandscope.extensions.catalog import (
    CATALOG_FILENAME,
    MAX_PLUGIN_PACKAGE_BYTES,
    STATUS_INCOMPATIBLE,
    STATUS_INSTALLABLE,
    STATUS_NOT_IN_CATALOG,
    STATUS_UPDATE_AVAILABLE,
    STATUS_UP_TO_DATE,
    CatalogClient,
    CatalogError,
    load_catalog,
    match_catalog_entry,
    parse_catalog,
    read_cached_catalog,
    store_cached_catalog,
)
from bandscope.extensions.trust import (
    PURPOSE_CATALOG,
    SignatureEnvelope,
    build_signature,
    sidecar_path,
    write_signature_sidecar,
)
from tests.support.plugins import test_signing_key

URL = "https://github.com/vergil-996/ARPES_3dMAP/releases/download/v1.11.2/x.bsplugin"


def package_asset(name="BandScope-flat_band_opacity-1.0.1.bsplugin", sha=None, size=1024):
    return {
        "name": name,
        "url": URL.replace("x.bsplugin", name),
        "size": size,
        "sha256": sha or hashlib.sha256(name.encode()).hexdigest(),
    }


def signature_asset(name="BandScope-flat_band_opacity-1.0.1.bsplugin.sig"):
    return {
        "name": name,
        "url": URL.replace("x.bsplugin", name),
        "size": 200,
        "sha256": hashlib.sha256(name.encode()).hexdigest(),
    }


def entry_payload(version="1.0.1", *, requires_app=APP_VERSION, api_version=1,
                  capabilities=("opacity_multiplier",), plugin_id="flat_band_opacity",
                  signature=True, package=None, name="平带增强"):
    payload = {
        "id": plugin_id,
        "name": name,
        "version": version,
        "requires_app": requires_app,
        "api_version": api_version,
        "capabilities": list(capabilities),
        "notes": "说明",
        "package": package or package_asset(f"BandScope-{plugin_id}-{version}.bsplugin"),
    }
    if signature:
        payload["signature"] = signature_asset(f"BandScope-{plugin_id}-{version}.bsplugin.sig")
    return payload


def catalog_payload(entries=None, *, revision=3, host_version=APP_VERSION, api_versions=(1,), schema=1):
    return json.dumps(
        {
            "schema": schema,
            "revision": revision,
            "generated_at": "2026-10-06T00:00:00+00:00",
            "host": {"app_version": host_version, "api_versions": list(api_versions)},
            "plugins": entries if entries is not None else [entry_payload()],
        },
        ensure_ascii=False,
    )


class CatalogParseTests(unittest.TestCase):
    def test_parses_a_well_formed_catalog(self):
        catalog = parse_catalog(catalog_payload())
        self.assertEqual(catalog.revision, 3)
        self.assertEqual(catalog.host_api_versions, (1,))
        self.assertEqual(len(catalog.entries), 1)
        entry = catalog.entries[0]
        self.assertEqual(entry.plugin_id, "flat_band_opacity")
        self.assertTrue(entry.signed)

    def test_unknown_schema_is_refused(self):
        with self.assertRaises(CatalogError) as ctx:
            parse_catalog(catalog_payload(schema=99))
        self.assertIn("schema", str(ctx.exception))

    def test_untrusted_download_host_is_refused(self):
        entry = entry_payload()
        entry["package"]["url"] = "https://evil.example.com/x.bsplugin"
        with self.assertRaises(CatalogError):
            parse_catalog(catalog_payload([entry]))

    def test_oversized_package_is_refused(self):
        entry = entry_payload()
        entry["package"]["size"] = MAX_PLUGIN_PACKAGE_BYTES + 1
        with self.assertRaises(CatalogError):
            parse_catalog(catalog_payload([entry]))

    def test_missing_digest_is_refused(self):
        entry = entry_payload()
        entry["package"]["sha256"] = ""
        with self.assertRaises(CatalogError):
            parse_catalog(catalog_payload([entry]))

    def test_missing_host_declaration_is_refused(self):
        payload = json.loads(catalog_payload())
        payload["host"] = {"app_version": "", "api_versions": [1]}
        with self.assertRaises(CatalogError):
            parse_catalog(json.dumps(payload))

    def test_same_version_with_different_content_is_refused(self):
        first = entry_payload("1.0.1")
        second = entry_payload("1.0.1")
        second["package"]["sha256"] = "a" * 64
        with self.assertRaises(CatalogError) as ctx:
            parse_catalog(catalog_payload([first, second]))
        self.assertIn("不同内容", str(ctx.exception))

    def test_history_without_signature_stays_unverified(self):
        catalog = parse_catalog(catalog_payload([entry_payload(signature=False)]))
        self.assertFalse(catalog.entries[0].signed)


class CatalogSignatureTests(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.folder = Path(self._root.name)
        self.key_id, self.private_key, self.trusted = test_signing_key()

    def write_catalog(self, payload, *, sign=True, keys=None):
        path = self.folder / CATALOG_FILENAME
        data = payload.encode("utf-8")
        path.write_bytes(data)
        if sign:
            envelope = build_signature(
                self.private_key, data, purpose=PURPOSE_CATALOG, key_id=self.key_id
            )
            write_signature_sidecar(path, envelope)
        return path

    def test_signed_catalog_loads(self):
        path = self.write_catalog(catalog_payload())
        catalog = load_catalog(path, trusted_keys=self.trusted)
        self.assertEqual(catalog.revision, 3)

    def test_missing_signature_is_refused(self):
        path = self.write_catalog(catalog_payload(), sign=False)
        with self.assertRaises(CatalogError) as ctx:
            load_catalog(path, trusted_keys=self.trusted)
        with self.assertRaises(CatalogError):
            load_catalog(path, trusted_keys=self.trusted)

    def test_tampered_catalog_is_refused(self):
        path = self.write_catalog(catalog_payload())
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["revision"] = 999
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(CatalogError):
            load_catalog(path, trusted_keys=self.trusted)

    def test_unknown_key_is_refused(self):
        path = self.write_catalog(catalog_payload())
        with self.assertRaises(CatalogError):
            load_catalog(path, trusted_keys={"other": "AAAA"})


class CatalogSelectionTests(unittest.TestCase):
    def match(self, entries, *, installed="", app_version=APP_VERSION, api_versions=(1,)):
        catalog = parse_catalog(
            catalog_payload(entries, host_version=app_version, api_versions=api_versions)
        )
        return match_catalog_entry(
            catalog,
            "flat_band_opacity",
            installed_version=installed,
            app_version=app_version,
            supported_apis=api_versions,
        )

    def test_not_in_catalog(self):
        result = self.match([], installed="1.0.0")
        self.assertEqual(result.status, STATUS_NOT_IN_CATALOG)
        self.assertIsNone(result.entry)

    def test_installable_when_not_installed(self):
        result = self.match([entry_payload("1.0.1")])
        self.assertEqual(result.status, STATUS_INSTALLABLE)
        self.assertEqual(result.entry.version, "1.0.1")
        self.assertTrue(result.can_install)

    def test_update_available_only_for_a_higher_version(self):
        result = self.match([entry_payload("1.0.1")], installed="1.0.0")
        self.assertEqual(result.status, STATUS_UPDATE_AVAILABLE)
        self.assertEqual(result.entry.version, "1.0.1")

    def test_no_downgrade_and_no_same_version_update(self):
        result = self.match([entry_payload("1.0.1")], installed="1.0.1")
        self.assertEqual(result.status, STATUS_UP_TO_DATE)
        result = self.match([entry_payload("1.0.0")], installed="1.0.1")
        self.assertEqual(result.status, STATUS_UP_TO_DATE)

    def test_highest_compatible_stable_version_wins(self):
        entries = [entry_payload("1.0.1"), entry_payload("1.0.3"), entry_payload("1.2.0")]
        result = self.match(entries, installed="1.0.0")
        self.assertEqual(result.entry.version, "1.2.0")

    def test_prerelease_is_not_recommended(self):
        entries = [entry_payload("1.0.1"), entry_payload("1.1.0rc1")]
        result = self.match(entries, installed="1.0.0")
        self.assertEqual(result.entry.version, "1.0.1")

    def test_incompatible_reports_the_reason(self):
        entries = [entry_payload("1.0.1", requires_app=">=9.0,<10.0")]
        result = self.match(entries, installed="1.0.0")
        self.assertEqual(result.status, STATUS_INCOMPATIBLE)
        self.assertIn(">=9.0,<10.0", result.reason)

    def test_future_host_api_set_is_respected(self):
        # 目标宿主只支持接口 2 时，声明接口 1 的包不能算兼容。
        entries = [entry_payload("1.0.1", api_version=1), entry_payload("1.0.2", api_version=2)]
        result = self.match(entries, installed="1.0.0", api_versions=(2,))
        self.assertEqual(result.status, STATUS_UPDATE_AVAILABLE)
        self.assertEqual(result.entry.version, "1.0.2")

    def test_legacy_local_version_is_not_compared(self):
        # 本地是非标准版本：不参与“最新版本”比较，但可以显式换成官方包。
        result = self.match([entry_payload("1.0.1")], installed="beta-2")
        self.assertEqual(result.status, STATUS_INSTALLABLE)
        self.assertEqual(result.entry.version, "1.0.1")
        self.assertIn("非标准版本", result.reason)

    def test_missing_capability_is_incompatible(self):
        entries = [entry_payload("1.0.1", capabilities=("time_travel",))]
        result = self.match(entries, installed="1.0.0")
        self.assertEqual(result.status, STATUS_INCOMPATIBLE)
        self.assertIn("time_travel", result.reason)


class CatalogCacheTests(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.folder = Path(self._root.name)
        self.key_id, self.private_key, self.trusted = test_signing_key()

    def store(self, catalog, payload):
        envelope = build_signature(
            self.private_key, payload, purpose=PURPOSE_CATALOG, key_id=self.key_id
        )
        return store_cached_catalog(
            catalog, payload, json.dumps(envelope.to_payload()).encode("utf-8"),
            directory=self.folder, verified_at="2026-10-06T00:00:00+00:00",
        )

    def test_round_trip(self):
        payload = catalog_payload(revision=4).encode("utf-8")
        catalog = parse_catalog(payload)
        patcher = mock.patch.object(trust_module, "TRUSTED_PLUGIN_KEYS", self.trusted)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertTrue(self.store(catalog, payload))

        cached = read_cached_catalog(self.folder)
        self.assertIsNotNone(cached)
        self.assertEqual(cached.revision, 4)
        self.assertEqual(catalog_module.cached_verified_at(self.folder), "2026-10-06T00:00:00+00:00")

    def test_lower_revision_does_not_replace_the_cache(self):
        patcher = mock.patch.object(trust_module, "TRUSTED_PLUGIN_KEYS", self.trusted)
        patcher.start()
        self.addCleanup(patcher.stop)
        newer = catalog_payload(revision=4).encode("utf-8")
        self.store(parse_catalog(newer), newer)
        older = catalog_payload(revision=2).encode("utf-8")
        self.assertFalse(self.store(parse_catalog(older), older))
        self.assertEqual(read_cached_catalog(self.folder).revision, 4)

    def test_corrupted_cache_reads_as_empty(self):
        (self.folder / CATALOG_FILENAME).write_text("{", encoding="utf-8")
        self.assertIsNone(read_cached_catalog(self.folder))


class _FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, url: str = ""):
        super().__init__(payload)
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False

    def geturl(self):
        return self._url


class _FakeOpener:
    """按地址返回预置字节的假 opener。"""

    def __init__(self, mapping):
        self.mapping = dict(mapping)

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if url not in self.mapping:
            raise AssertionError(f"unexpected url {url}")
        return _FakeResponse(self.mapping[url], url)


class CatalogDownloadTests(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.folder = Path(self._root.name)
        self.key_id, self.private_key, self.trusted = test_signing_key()
        patcher = mock.patch.object(trust_module, "TRUSTED_PLUGIN_KEYS", self.trusted)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_entry(self, package_bytes: bytes, *, signed=True):
        name = "BandScope-flat_band_opacity-1.0.1.bsplugin"
        package = {
            "name": name,
            "url": URL.replace("x.bsplugin", name),
            "size": len(package_bytes),
            "sha256": hashlib.sha256(package_bytes).hexdigest(),
        }
        entry = entry_payload("1.0.1", package=package, signature=signed)
        if signed:
            envelope = build_signature(
                self.private_key, package_bytes, purpose="plugin", key_id=self.key_id
            )
            sig_bytes = json.dumps(envelope.to_payload()).encode("utf-8")
            entry["signature"] = {
                "name": name + ".sig",
                "url": URL.replace("x.bsplugin", name + ".sig"),
                "size": len(sig_bytes),
                "sha256": hashlib.sha256(sig_bytes).hexdigest(),
            }
        else:
            sig_bytes = b""
        catalog = parse_catalog(catalog_payload([entry]))
        return catalog.entries[0], sig_bytes

    def client(self, mapping):
        return CatalogClient(opener=_FakeOpener(mapping), timeout=5)

    def test_signed_package_downloads_and_verifies(self):
        payload = b"package-bytes"
        entry, sig_bytes = self.make_entry(payload)
        mapping = {
            entry.package.url: payload,
            entry.signature.url: sig_bytes,
        }
        downloaded = self.client(mapping).download_package(entry, self.folder)
        self.assertTrue(downloaded.verified)
        self.assertEqual(downloaded.path.read_bytes(), payload)
        self.assertTrue(sidecar_path(downloaded.path).is_file())

    def test_digest_mismatch_leaves_nothing_behind(self):
        payload = b"package-bytes"
        entry, sig_bytes = self.make_entry(payload)
        mapping = {entry.package.url: b"other-bytes", entry.signature.url: sig_bytes}
        with self.assertRaises(Exception):
            self.client(mapping).download_package(entry, self.folder)
        leftovers = list(self.folder.iterdir())
        self.assertEqual(leftovers, [], leftovers)

    def test_tampered_signature_is_refused(self):
        payload = b"package-bytes"
        name = "BandScope-flat_band_opacity-1.0.1.bsplugin"
        package = {
            "name": name,
            "url": URL.replace("x.bsplugin", name),
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        # 摘要/大小都对得上、但签名本身被改：必须落到验签失败，而不是别的校验。
        good = build_signature(
            self.private_key, payload, purpose="plugin", key_id=self.key_id
        )
        raw = bytearray(good.signature)
        raw[0] ^= 0xFF
        bad_bytes = json.dumps(
            SignatureEnvelope(good.protocol, good.key_id, good.algorithm, bytes(raw)).to_payload()
        ).encode("utf-8")
        entry_payload_dict = entry_payload("1.0.1", package=package, signature=False)
        entry_payload_dict["signature"] = {
            "name": name + ".sig",
            "url": URL.replace("x.bsplugin", name + ".sig"),
            "size": len(bad_bytes),
            "sha256": hashlib.sha256(bad_bytes).hexdigest(),
        }
        entry = parse_catalog(catalog_payload([entry_payload_dict])).entries[0]
        mapping = {entry.package.url: payload, entry.signature.url: bad_bytes}
        with self.assertRaises(CatalogError) as ctx:
            self.client(mapping).download_package(entry, self.folder)
        self.assertIn("验签失败", str(ctx.exception))

    def test_install_source_carries_the_catalog_identity(self):
        payload = b"package-bytes"
        entry, _sig = self.make_entry(payload)
        source = self.client({}).install_source(entry, verified=True)
        self.assertEqual(source.expected_id, entry.plugin_id)
        self.assertEqual(source.expected_version, entry.version)
        self.assertEqual(source.expected_sha256, entry.package.sha256)


if __name__ == "__main__":
    unittest.main()
