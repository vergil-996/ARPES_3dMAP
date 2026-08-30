"""Application identity and release-channel metadata for BandScope."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "BandScope"
APP_VERSION = "1.2.0"
APP_PUBLISHER = "vergil-996"

GITHUB_OWNER = "vergil-996"
GITHUB_REPOSITORY = "ARPES_3dMAP"
GITHUB_REPOSITORY_SLUG = f"{GITHUB_OWNER}/{GITHUB_REPOSITORY}"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPOSITORY_SLUG}/releases"

CPU_BUILD = "CPU"
NVIDIA_BUILD = "NVIDIA"
SUPPORTED_BUILD_FLAVORS = (CPU_BUILD, NVIDIA_BUILD)

# Once a Windows code-signing certificate is available, put its SHA-256
# certificate fingerprint here (uppercase, without separators).  The updater
# will then require every downloaded installer to be signed by that exact
# certificate in addition to checking the GitHub-provided SHA-256 file digest.
TRUSTED_SIGNER_CERT_SHA256 = ""


def is_frozen_build() -> bool:
    """Return whether the application is running from a PyInstaller bundle."""

    return bool(getattr(sys, "frozen", False))


def current_build_flavor(executable: str | os.PathLike[str] | None = None) -> str:
    """Infer the release flavor while allowing an explicit test/build override."""

    override = os.environ.get("BANDSCOPE_BUILD_FLAVOR", "").strip().upper()
    if override in SUPPORTED_BUILD_FLAVORS:
        return override

    executable_name = Path(executable or sys.executable).stem.upper()
    if "NVIDIA" in executable_name:
        return NVIDIA_BUILD
    return CPU_BUILD


def installer_asset_name(version: str, flavor: str | None = None) -> str:
    normalized_flavor = (flavor or current_build_flavor()).strip().upper()
    if normalized_flavor not in SUPPORTED_BUILD_FLAVORS:
        raise ValueError(f"Unsupported BandScope build flavor: {flavor!r}")
    return f"{APP_NAME}-{version}-Windows-x64-{normalized_flavor}-Setup.exe"


def display_version(flavor: str | None = None) -> str:
    return f"{APP_NAME} v{APP_VERSION} ({flavor or current_build_flavor()})"
