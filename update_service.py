"""GitHub Release update discovery, download, and integrity verification.

This module deliberately has no Qt dependency so the network and security
rules can be exercised by small unit tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from app_metadata import (
    APP_NAME,
    APP_VERSION,
    GITHUB_OWNER,
    GITHUB_REPOSITORY,
    TRUSTED_SIGNER_CERT_SHA256,
    installer_asset_name,
)


GITHUB_API_VERSION = "2026-03-10"
DEFAULT_TIMEOUT_SECONDS = 20
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
MAX_INSTALLER_BYTES = 2 * 1024 * 1024 * 1024
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "www.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
SEMVER_PATTERN = re.compile(
    r"^(?:v)?(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class UpdateError(RuntimeError):
    """Base class for user-presentable update failures."""


class ReleaseFormatError(UpdateError):
    pass


class ReleaseAssetNotFound(UpdateError):
    pass


class DownloadIntegrityError(UpdateError):
    pass


@dataclass(frozen=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "SemanticVersion":
        match = SEMVER_PATTERN.fullmatch(str(value).strip())
        if match is None:
            raise ReleaseFormatError(f"无效的语义版本号：{value!r}")
        prerelease = tuple((match.group("prerelease") or "").split("."))
        if prerelease == ("",):
            prerelease = ()
        for identifier in prerelease:
            if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
                raise ReleaseFormatError(f"预发布版本数字不能包含前导零：{value!r}")
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            prerelease,
        )

    def _compare_prerelease(self, other: "SemanticVersion") -> int:
        if not self.prerelease and not other.prerelease:
            return 0
        if not self.prerelease:
            return 1
        if not other.prerelease:
            return -1
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            left_numeric = left.isdigit()
            right_numeric = right.isdigit()
            if left_numeric and right_numeric:
                return -1 if int(left) < int(right) else 1
            if left_numeric != right_numeric:
                return -1 if left_numeric else 1
            return -1 if left < right else 1
        if len(self.prerelease) == len(other.prerelease):
            return 0
        return -1 if len(self.prerelease) < len(other.prerelease) else 1

    def compare(self, other: "SemanticVersion") -> int:
        left_core = (self.major, self.minor, self.patch)
        right_core = (other.major, other.minor, other.patch)
        if left_core != right_core:
            return -1 if left_core < right_core else 1
        return self._compare_prerelease(other)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self.compare(other) < 0

    def __le__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self.compare(other) <= 0

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-{'.'.join(self.prerelease)}" if self.prerelease else base


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ReleaseInfo:
    version: SemanticVersion
    tag_name: str
    title: str
    notes: str
    page_url: str
    published_at: str
    assets: tuple[ReleaseAsset, ...]

    def installer_for(self, flavor: str) -> ReleaseAsset:
        expected = installer_asset_name(str(self.version), flavor)
        for asset in self.assets:
            if asset.name == expected:
                return asset
        raise ReleaseAssetNotFound(
            f"Release {self.tag_name} 中没有找到 {flavor} 安装包：{expected}"
        )


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: SemanticVersion
    release: ReleaseInfo
    installer: ReleaseAsset | None

    @property
    def update_available(self) -> bool:
        return self.current_version < self.release.version


@dataclass(frozen=True)
class InstallerVerification:
    sha256: str
    signature_status: str
    signer_cert_sha256: str = ""

    @property
    def signature_valid(self) -> bool:
        return self.signature_status == "Valid"


def _normalize_sha256(value: str) -> str:
    digest = str(value or "").strip().lower()
    if digest.startswith("sha256:"):
        digest = digest.partition(":")[2]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return ""
    return digest


def _asset_digest(asset: dict) -> str:
    return _normalize_sha256(asset.get("digest", ""))


def _ensure_https_download_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() not in ALLOWED_DOWNLOAD_HOSTS:
        raise ReleaseFormatError(f"Release 资源使用了不受信任的下载地址：{url}")


class GitHubReleaseClient:
    def __init__(
        self,
        owner: str = GITHUB_OWNER,
        repository: str = GITHUB_REPOSITORY,
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        opener: Callable[..., object] | None = None,
    ):
        self.owner = owner
        self.repository = repository
        self.timeout = int(timeout)
        self._opener = opener or urllib.request.urlopen

    @property
    def latest_release_api_url(self) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repository}/releases/latest"

    def _request(self, url: str) -> urllib.request.Request:
        return urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "User-Agent": f"{APP_NAME}/{APP_VERSION}",
            },
        )

    def _open(self, request: urllib.request.Request):
        try:
            return self._opener(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise UpdateError("GitHub 仓库尚未发布正式 Release。") from exc
            raise UpdateError(f"GitHub 更新服务返回 HTTP {exc.code}。") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise UpdateError(f"无法连接 GitHub 更新服务：{reason}") from exc

    def fetch_latest_release(self) -> ReleaseInfo:
        with self._open(self._request(self.latest_release_api_url)) as response:
            try:
                payload = json.load(response)
            except (TypeError, ValueError, UnicodeDecodeError) as exc:
                raise ReleaseFormatError("GitHub Release 返回了无效的 JSON 数据。") from exc

        if payload.get("draft") or payload.get("prerelease"):
            raise ReleaseFormatError("latest Release 不是可用的正式版本。")

        version = SemanticVersion.parse(payload.get("tag_name", ""))
        assets = []
        for item in payload.get("assets") or ():
            name = str(item.get("name") or "")
            url = str(item.get("browser_download_url") or "")
            if not name or not url:
                continue
            _ensure_https_download_url(url)
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            assets.append(
                ReleaseAsset(
                    name=name,
                    download_url=url,
                    size=max(0, size),
                    sha256=_asset_digest(item),
                )
            )

        return ReleaseInfo(
            version=version,
            tag_name=str(payload.get("tag_name") or ""),
            title=str(payload.get("name") or payload.get("tag_name") or ""),
            notes=str(payload.get("body") or ""),
            page_url=str(payload.get("html_url") or ""),
            published_at=str(payload.get("published_at") or ""),
            assets=tuple(assets),
        )

    def check_for_update(self, current_version: str, flavor: str) -> UpdateCheckResult:
        current = SemanticVersion.parse(current_version)
        release = self.fetch_latest_release()
        installer = None
        if current < release.version:
            installer = release.installer_for(flavor)
        return UpdateCheckResult(current, release, installer)

    def download_installer(
        self,
        asset: ReleaseAsset,
        destination_dir: str | os.PathLike[str] | None = None,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> tuple[Path, str]:
        _ensure_https_download_url(asset.download_url)
        if not asset.name.lower().endswith(".exe") or Path(asset.name).name != asset.name:
            raise ReleaseFormatError(f"安装包文件名无效：{asset.name!r}")
        if asset.size > MAX_INSTALLER_BYTES:
            raise UpdateError("安装包超过 2 GiB 安全上限，已拒绝下载。")

        target_dir = Path(destination_dir) if destination_dir else update_cache_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        final_path = target_dir / asset.name
        partial_path = final_path.with_suffix(final_path.suffix + ".part")
        partial_path.unlink(missing_ok=True)

        expected_digest = _normalize_sha256(asset.sha256)
        if not expected_digest:
            raise DownloadIntegrityError(
                "GitHub 尚未提供该安装包的 SHA-256 摘要，已拒绝自动安装。"
            )

        received = 0
        digest = hashlib.sha256()
        try:
            with self._open(self._request(asset.download_url)) as response, partial_path.open("wb") as output:
                final_url = getattr(response, "geturl", lambda: asset.download_url)()
                _ensure_https_download_url(final_url)
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > MAX_INSTALLER_BYTES:
                        raise UpdateError("安装包下载量超过 2 GiB 安全上限。")
                    digest.update(chunk)
                    output.write(chunk)
                    if progress is not None:
                        progress(received, asset.size)
        except Exception:
            partial_path.unlink(missing_ok=True)
            raise

        if asset.size and received != asset.size:
            partial_path.unlink(missing_ok=True)
            raise DownloadIntegrityError(
                f"安装包大小不完整：预期 {asset.size} 字节，实际 {received} 字节。"
            )

        actual_digest = digest.hexdigest()
        if actual_digest != expected_digest:
            partial_path.unlink(missing_ok=True)
            raise DownloadIntegrityError(
                "安装包 SHA-256 校验失败，文件可能损坏或已被篡改。"
            )

        os.replace(partial_path, final_path)
        return final_path, actual_digest


def update_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else ""
    root = Path(base) if base else Path(tempfile.gettempdir())
    return root / APP_NAME / "updates"


def cleanup_update_cache(current_version: str = APP_VERSION, cache_dir: Path | None = None) -> None:
    """Remove incomplete files and installers no newer than this application."""

    directory = Path(cache_dir) if cache_dir else update_cache_dir()
    if not directory.is_dir():
        return
    current = SemanticVersion.parse(current_version)
    pattern = re.compile(
        rf"^{re.escape(APP_NAME)}-(?P<version>[^-]+)-Windows-x64-(?:CPU|NVIDIA)-Setup\.exe$",
        re.IGNORECASE,
    )
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if path.name.endswith(".part"):
            path.unlink(missing_ok=True)
            continue
        match = pattern.fullmatch(path.name)
        if match is None:
            continue
        try:
            cached_version = SemanticVersion.parse(match.group("version"))
        except ReleaseFormatError:
            continue
        if cached_version <= current:
            path.unlink(missing_ok=True)


def _powershell_authenticode(path: Path) -> tuple[str, str]:
    if sys.platform != "win32":
        return "Unavailable", ""
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return "Unavailable", ""
    script = (
        "$s=Get-AuthenticodeSignature -LiteralPath $args[0];"
        "$h='';if($null -ne $s.SignerCertificate){"
        "$h=$s.SignerCertificate.GetCertHashString('SHA256')};"
        "[Console]::Out.Write(($s.Status.ToString())+'|'+$h)"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script, str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return "Unavailable", ""
    status, separator, fingerprint = completed.stdout.strip().partition("|")
    if not separator:
        return "Unavailable", ""
    return status or "Unknown", re.sub(r"[^0-9A-Fa-f]", "", fingerprint).upper()


def verify_installer(
    path: str | os.PathLike[str],
    expected_sha256: str,
    *,
    trusted_signer_sha256: str = TRUSTED_SIGNER_CERT_SHA256,
) -> InstallerVerification:
    installer = Path(path)
    digest = hashlib.sha256()
    with installer.open("rb") as stream:
        for chunk in iter(lambda: stream.read(DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    expected = _normalize_sha256(expected_sha256)
    if not expected or actual != expected:
        raise DownloadIntegrityError("启动安装前的 SHA-256 二次校验失败。")

    signature_status, signer_fingerprint = _powershell_authenticode(installer)
    trusted = re.sub(r"[^0-9A-Fa-f]", "", trusted_signer_sha256 or "").upper()
    if trusted:
        if signature_status != "Valid":
            raise DownloadIntegrityError(
                f"安装包的 Windows 数字签名无效（{signature_status}）。"
            )
        if signer_fingerprint != trusted:
            raise DownloadIntegrityError("安装包签名证书与 BandScope 信任的发布证书不一致。")
    return InstallerVerification(actual, signature_status, signer_fingerprint)


def launch_installer(path: str | os.PathLike[str]) -> None:
    if sys.platform != "win32":
        raise UpdateError("自动安装目前仅支持 Windows。")
    installer = Path(path).resolve()
    if not installer.is_file():
        raise UpdateError("下载的安装包已经不存在。")
    subprocess.Popen(
        [str(installer), "/SP-", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
        cwd=str(installer.parent),
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
