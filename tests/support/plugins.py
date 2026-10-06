# -*- coding: utf-8 -*-
"""合成插件包与登记表构造器（供多个插件测试模块复用）。

只使用临时目录里的合成数据：不依赖本机已安装插件、实验文件或用户注册表。
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from bandscope.app_metadata import APP_VERSION
from bandscope.extensions.api import API_VERSION
from bandscope.extensions.plugin_store import compute_content_digest
from bandscope.extensions.trust import SOURCE_LOCAL

PLUGIN_ID = "flat_band_opacity"
SECOND_PLUGIN_ID = "band_beta"

#: 最小合法插件入口：能导入、能实例化、不创建面板。
DEFAULT_ENTRY_SOURCE = (
    "from plugin_api import Plugin\n"
    "\n"
    "\n"
    "class Plugin(Plugin):\n"
    "    def create_panel(self, host):\n"
    "        return None\n"
)


def manifest_payload(plugin_id: str = PLUGIN_ID, **overrides) -> dict:
    payload = {
        "id": plugin_id,
        "name": "平带增强" if plugin_id == PLUGIN_ID else plugin_id,
        "version": "1.0.0",
        "api_version": API_VERSION,
        "requires_app": APP_VERSION,
        "entry_point": "entry:Plugin",
        "capabilities": ["opacity_multiplier"],
    }
    payload.update(overrides)
    return payload


def make_plugin_archive(
    path,
    *,
    manifest: Optional[dict] = None,
    files: Optional[dict] = None,
    prefix: str = "",
    entry_source: Optional[str] = None,
) -> Path:
    """把合成插件打成 ``.bsplugin``（真实 ZIP 容器）。"""
    entries = {
        "plugin.json": json.dumps(manifest or manifest_payload(), ensure_ascii=False),
        "entry.py": entry_source if entry_source is not None else DEFAULT_ENTRY_SOURCE,
    }
    entries.update(files or {})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(prefix + name, content)
    return path


def release_logging_entry(log_path) -> str:
    """入口源码：每次 ``release()`` 向日志追加一行，用于核对“恰好一次”。"""
    return (
        "from pathlib import Path\n"
        "\n"
        "from plugin_api import Plugin\n"
        "\n"
        f"LOG = Path(r'{log_path}')\n"
        "\n"
        "\n"
        "class Plugin(Plugin):\n"
        "    def create_panel(self, host):\n"
        "        return None\n"
        "\n"
        "    def release(self):\n"
        "        with open(LOG, 'a', encoding='utf-8') as handle:\n"
        "            handle.write('release\\n')\n"
    )


def stage_plugin_content(
    root: Path,
    plugin_id: str = PLUGIN_ID,
    *,
    manifest: Optional[dict] = None,
    entry_source: Optional[str] = None,
) -> tuple[str, str]:
    """按 v2 布局写入一份内容目录，返回 ``(摘要, 相对路径)``。

    摘要由真实内容计算，目录名与摘要一致；不经过登记表。
    """
    manifest = manifest or manifest_payload(plugin_id)
    root = Path(root)
    staging = root / "staging" / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (staging / "entry.py").write_text(
        entry_source if entry_source is not None else DEFAULT_ENTRY_SOURCE,
        encoding="utf-8",
    )
    digest = compute_content_digest(staging)
    target = root / "installed" / plugin_id / manifest["version"] / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(staging, ignore_errors=True)
    else:
        os.replace(staging, target)
    shutil.rmtree(staging.parent, ignore_errors=True)
    return digest, f"installed/{plugin_id}/{manifest['version']}/{digest}"


def v2_entry(
    manifest: Optional[dict] = None,
    *,
    plugin_id: str = PLUGIN_ID,
    digest: Optional[str] = None,
    path: Optional[str] = None,
    enabled: bool = True,
    revision: int = 1,
    pending_uninstall: bool = False,
    restore_requested: bool = False,
    failed: Optional[dict] = None,
    last_good: Optional[dict] = None,
    previous_good: Optional[dict] = None,
) -> dict:
    """构造登记表 schema v2 的一条记录。"""
    manifest = manifest or manifest_payload(plugin_id)
    version = manifest["version"]
    digest = digest or "e" * 64
    path = path or f"installed/{plugin_id}/{version}/{digest}"
    return {
        "name": manifest["name"],
        "desired": {
            "version": version,
            "digest": digest,
            "path": path,
            "enabled": enabled,
            "revision": revision,
        },
        "last_good": last_good,
        "previous_good": previous_good,
        "pending_uninstall": pending_uninstall,
        "restore_requested": restore_requested,
        "failed": failed,
        "source": {"kind": "local", "verified": False, "package_sha256": None},
    }


def write_v2_registry(root: Path, entries: dict, *, revision: int = 1) -> Path:
    """直接落一份 v2 登记表文件。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    payload = {"version": 2, "revision": revision, "plugins": dict(entries)}
    path = root / "registry.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def synthetic_source():
    """合成包默认的来源：未签名，但等价于界面里点过「仍要安装」。

    真实安装路径拒绝未经确认的未签名包；测试里绝大多数用例只关心安装事务本身，
    用这个来源把“用户已确认”这一步显式表达出来。要测信任规则本身，直接调
    ``install_package`` 并传入自己的 ``InstallSource``。
    """
    from bandscope.extensions.plugin_manager import InstallSource

    return InstallSource(kind=SOURCE_LOCAL, accepted_unverified=True)


def install_synthetic(archive, **kwargs):
    """安装一个合成包（未签名但已确认的本地来源）。"""
    from bandscope.extensions.plugin_manager import install_package

    kwargs.setdefault("source", synthetic_source())
    return install_package(archive, **kwargs)


def test_signing_key(key_id: str = "test-key"):
    """临时生成的测试密钥；不依赖正式 Secret。返回 ``(key_id, 私钥, 可信公钥表)``。"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from bandscope.extensions.trust import encode_public_key

    private_key = Ed25519PrivateKey.generate()
    return key_id, private_key, {key_id: encode_public_key(private_key.public_key())}


def sign_archive(path, *, key_id, private_key, purpose=None) -> Path:
    """为合成包写一份真实签名 sidecar；返回 sidecar 路径。"""
    from bandscope.extensions.trust import (
        PURPOSE_PLUGIN,
        build_signature,
        write_signature_sidecar,
    )

    target = Path(path)
    envelope = build_signature(
        private_key,
        target.read_bytes(),
        purpose=purpose or PURPOSE_PLUGIN,
        key_id=key_id,
    )
    return write_signature_sidecar(target, envelope)


def read_registry(root: Path) -> dict:
    path = Path(root) / "registry.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def install_fake(
    root: Path,
    plugin_id: str = PLUGIN_ID,
    *,
    source=None,
    **manifest_overrides,
):
    """用真实安装事务装一个合成插件；返回 ``InstallOutcome``。

    合成包没有签名，默认按“用户已确认的本地未验证来源”安装——这与真实界面
    里点过确认之后的状态一致；要测未确认或官方来源的路径，显式传入 ``source``。
    """
    from bandscope.extensions.plugin_manager import InstallSource, install_package

    manifest = manifest_payload(plugin_id, **manifest_overrides)
    archive = make_plugin_archive(
        Path(root) / "staging" / "fixtures" / f"{plugin_id}-{manifest['version']}.bsplugin",
        manifest=manifest,
    )
    if source is None:
        source = InstallSource(kind=SOURCE_LOCAL, accepted_unverified=True)
    return install_package(archive, root=Path(root), source=source)
