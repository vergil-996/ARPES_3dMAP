import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app_metadata import installer_asset_name
from update_service import (
    DownloadIntegrityError,
    GitHubReleaseClient,
    ReleaseAsset,
    ReleaseAssetNotFound,
    ReleaseFormatError,
    SemanticVersion,
    cleanup_update_cache,
    verify_installer,
)


class FakeResponse(io.BytesIO):
    def __init__(self, payload, url):
        super().__init__(payload)
        self._url = url

    def geturl(self):
        return self._url


class UpdateServiceTests(unittest.TestCase):
    def test_semantic_version_ordering(self):
        self.assertLess(SemanticVersion.parse("1.1.1"), SemanticVersion.parse("1.2.0"))
        self.assertLess(SemanticVersion.parse("v2.0.0-rc.1"), SemanticVersion.parse("2.0.0"))
        self.assertLess(SemanticVersion.parse("2.0.0-beta.2"), SemanticVersion.parse("2.0.0-beta.11"))
        self.assertEqual(str(SemanticVersion.parse("v1.2.3-rc.1+build.9")), "1.2.3-rc.1")

    def test_semantic_version_rejects_invalid_values(self):
        for value in ("1.2", "01.2.3", "1.2.3-01", "release-1.2.3"):
            with self.subTest(value=value), self.assertRaises(ReleaseFormatError):
                SemanticVersion.parse(value)

    def test_installer_asset_names_are_flavor_specific(self):
        self.assertEqual(
            installer_asset_name("1.1.1", "CPU"),
            "BandScope-1.1.1-Windows-x64-CPU-Setup.exe",
        )
        self.assertEqual(
            installer_asset_name("1.1.1", "NVIDIA"),
            "BandScope-1.1.1-Windows-x64-NVIDIA-Setup.exe",
        )

    def test_fetch_and_select_latest_release(self):
        installer = b"installer bytes"
        digest = hashlib.sha256(installer).hexdigest()
        asset_name = installer_asset_name("1.2.0", "CPU")
        payload = {
            "tag_name": "v1.2.0",
            "name": "BandScope v1.2.0",
            "body": "New analysis tools",
            "html_url": "https://github.com/vergil-996/ARPES_3dMAP/releases/tag/v1.2.0",
            "published_at": "2026-08-02T00:00:00Z",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": asset_name,
                    "browser_download_url": "https://github.com/vergil-996/ARPES_3dMAP/releases/download/v1.2.0/" + asset_name,
                    "size": len(installer),
                    "digest": "sha256:" + digest,
                }
            ],
        }

        def opener(request, timeout):
            del timeout
            return FakeResponse(json.dumps(payload).encode("utf-8"), request.full_url)

        result = GitHubReleaseClient(opener=opener).check_for_update("1.1.1", "CPU")
        self.assertTrue(result.update_available)
        self.assertEqual(result.installer.name, asset_name)
        self.assertEqual(result.installer.sha256, digest)

    def test_new_release_without_matching_flavor_is_rejected(self):
        payload = {
            "tag_name": "v1.2.0",
            "html_url": "https://github.com/vergil-996/ARPES_3dMAP/releases/tag/v1.2.0",
            "draft": False,
            "prerelease": False,
            "assets": [],
        }

        def opener(request, timeout):
            del timeout
            return FakeResponse(json.dumps(payload).encode("utf-8"), request.full_url)

        with self.assertRaises(ReleaseAssetNotFound):
            GitHubReleaseClient(opener=opener).check_for_update("1.1.1", "NVIDIA")

    def test_download_checks_size_and_sha256(self):
        content = b"a trusted installer payload"
        asset = ReleaseAsset(
            name=installer_asset_name("1.2.0", "CPU"),
            download_url="https://github.com/example/repo/releases/download/v1.2.0/setup.exe",
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

        def opener(request, timeout):
            del timeout
            return FakeResponse(content, request.full_url)

        progress = []
        with tempfile.TemporaryDirectory() as directory:
            path, digest = GitHubReleaseClient(opener=opener).download_installer(
                asset,
                directory,
                progress=lambda received, total: progress.append((received, total)),
            )
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(digest, hashlib.sha256(content).hexdigest())
            self.assertEqual(progress[-1], (len(content), len(content)))

    def test_bad_download_digest_removes_partial_file(self):
        content = b"corrupted"
        asset = ReleaseAsset(
            name=installer_asset_name("1.2.0", "CPU"),
            download_url="https://github.com/example/repo/releases/download/v1.2.0/setup.exe",
            size=len(content),
            sha256="0" * 64,
        )

        def opener(request, timeout):
            del timeout
            return FakeResponse(content, request.full_url)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(DownloadIntegrityError):
                GitHubReleaseClient(opener=opener).download_installer(asset, directory)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_installer_verification_can_pin_signer(self):
        content = b"signed installer"
        expected = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "setup.exe"
            path.write_bytes(content)
            with mock.patch(
                "update_service._powershell_authenticode",
                return_value=("Valid", "ABCD"),
            ):
                verification = verify_installer(path, expected, trusted_signer_sha256="AB:CD")
        self.assertTrue(verification.signature_valid)
        self.assertEqual(verification.signer_cert_sha256, "ABCD")

    def test_cleanup_removes_current_and_older_cached_installers(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            old_path = cache / installer_asset_name("1.0.0", "CPU")
            current_path = cache / installer_asset_name("1.1.1", "CPU")
            future_path = cache / installer_asset_name("1.2.0", "NVIDIA")
            partial_path = cache / (installer_asset_name("1.2.0", "CPU") + ".part")
            unrelated_path = cache / "user-data.txt"
            for path in (old_path, current_path, future_path, partial_path, unrelated_path):
                path.write_bytes(b"x")

            cleanup_update_cache("1.1.1", cache)

            self.assertFalse(old_path.exists())
            self.assertFalse(current_path.exists())
            self.assertFalse(partial_path.exists())
            self.assertTrue(future_path.exists())
            self.assertTrue(unrelated_path.exists())


if __name__ == "__main__":
    unittest.main()
