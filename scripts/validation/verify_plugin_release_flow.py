# -*- coding: utf-8 -*-
"""阶段 C–E 的发布链路端到端验收：打包 → 签名 → 目录 → 验签 → 官方安装。

用临时生成的测试密钥，不依赖正式 Secret，也不联网、不发布任何东西。它验证的是
“发布脚本产出的东西，客户端确实能验、能装、能记成官方来源”。

用法::

    python scripts/validation/verify_plugin_release_flow.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

failures = []


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}" + (f" —— {detail}" if detail and not condition else ""), flush=True)
    if not condition:
        failures.append(label)


def main() -> int:
    # 隔离扩展根：不碰用户的真实安装目录。
    sandbox = tempfile.TemporaryDirectory(prefix="bandscope-release-flow-")
    os.environ["BANDSCOPE_EXTENSION_ROOT"] = str(Path(sandbox.name) / "extensions")

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    import bandscope.app_metadata as app_metadata
    import bandscope.extensions.trust as trust
    from bandscope.extensions.catalog import match_catalog_entry, parse_catalog
    from bandscope.extensions.compat import parse_requires_app
    from bandscope.extensions.plugin_manager import InstallSource, PluginManager, install_package
    from scripts.release.build_plugin import build as build_package
    from scripts.release.build_plugin_catalog import CatalogBuildError, build as build_catalog
    from scripts.release.plugin_signing import check_trusted
    from scripts.release.sign_plugin import main as sign_main

    key_id = "validation-key"
    private_key = Ed25519PrivateKey.generate()
    trust.TRUSTED_PLUGIN_KEYS[key_id] = trust.encode_public_key(private_key.public_key())
    seed = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    import base64

    os.environ["BANDSCOPE_PLUGIN_SIGNING_KEY"] = base64.b64encode(seed).decode("ascii")
    os.environ["BANDSCOPE_PLUGIN_SIGNING_KEY_ID"] = key_id

    out = Path(sandbox.name) / "release"
    tag = f"v{app_metadata.APP_VERSION}"

    # 1. 打包
    package = build_package("flat_band_opacity", out, app_metadata.APP_VERSION)
    check("1. 插件包构建成功", package.is_file(), str(package))
    manifest = json.loads((REPO_ROOT / "plugins" / "flat_band_opacity" / "plugin.json").read_text(encoding="utf-8"))
    check("1. 平带插件已提升版本", manifest["version"] != "1.0.0", manifest["version"])
    # 兼容声明自 v1.12.0 起按范围放宽（维护者 2026-10-06 决定）：这里要求它仍然覆盖
    # 当前宿主版本。打包脚本做同一判定，这条把它显式记录下来，防止声明被改成不覆盖。
    requirement = parse_requires_app(manifest["requires_app"])
    check("1. 兼容声明覆盖当前宿主版本", requirement.accepts(app_metadata.APP_VERSION),
          manifest["requires_app"])

    # 2. 签名（缺密钥必须失败）
    missing = os.environ.pop("BANDSCOPE_PLUGIN_SIGNING_KEY")
    check("2. 缺签名密钥时拒绝签名", sign_main([str(package)]) != 0)
    os.environ["BANDSCOPE_PLUGIN_SIGNING_KEY"] = missing
    check("2. 签名成功", sign_main([str(package)]) == 0)
    check("2. 生成了 .sig sidecar", trust.sidecar_path(package).is_file())

    # 3. 密钥与内置公钥匹配检查
    try:
        check_trusted(private_key, key_id)
        matched = True
    except Exception as exc:
        matched = False
        print(f"      {exc}")
    check("3. 签名密钥与内置公钥匹配", matched)
    other = Ed25519PrivateKey.generate()
    try:
        check_trusted(other, key_id)
        wrong_key_rejected = False
    except Exception:
        wrong_key_rejected = True
    check("3. 不匹配的私钥被拒绝", wrong_key_rejected)

    # 4. 目录
    catalog_path = build_catalog([package], tag=tag, output=out / "plugins-index.json")
    check("4. 目录生成成功", catalog_path.is_file())
    check("4. 目录签名成功", sign_main([str(catalog_path), "--purpose", "catalog"]) == 0)
    catalog = trust.load_signature(trust.sidecar_path(catalog_path))
    check("4. 目录签名使用同一密钥", catalog.key_id == key_id)

    # 5. 客户端解析与选择
    parsed = parse_catalog(catalog_path.read_bytes())
    entry = parsed.entries[0]
    check("5. 目录解析出插件记录", entry.plugin_id == "flat_band_opacity")
    check("5. 目录资源地址指向本次 tag", f"/download/{tag}/" in entry.package.url, entry.package.url)
    match = match_catalog_entry(
        parsed, entry.plugin_id, app_version=app_metadata.APP_VERSION
    )
    check("5. 目录推荐可安装版本", match.can_install, match.reason)
    check("5. 目录声明的包摘要与产物一致",
          entry.package.sha256 == hashlib.sha256(package.read_bytes()).hexdigest())

    # 6. 从目录安装并记录官方来源
    root = Path(os.environ["BANDSCOPE_EXTENSION_ROOT"])
    install_package(
        package,
        root=root,
        source=InstallSource(
            kind="official",
            expected_id=entry.plugin_id,
            expected_version=entry.version,
            expected_sha256=entry.package.sha256,
        ),
    )
    registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    source = registry["plugins"][entry.plugin_id]["source"]
    check("6. 官方来源已登记", source["kind"] == "official" and source["verified"])
    check("6. 登记的密钥标识正确", source["key_id"] == key_id)

    manager = PluginManager(root=root)
    try:
        manager.startup()
        check("6. 重启后加载成功", manager.record(entry.plugin_id).ready,
              manager.record(entry.plugin_id).load_error)
    finally:
        manager.shutdown()

    # 7. 篡改必须被拒绝
    tampered = Path(sandbox.name) / "tampered.bsplugin"
    tampered.write_bytes(package.read_bytes() + b"\x00")
    trust.sidecar_path(tampered).write_bytes(trust.sidecar_path(package).read_bytes())
    from bandscope.extensions.trust import inspect_package_source

    check("7. 篡改过的包被拒绝", inspect_package_source(tampered).rejected)

    sandbox.cleanup()
    print()
    if failures:
        print(f"未通过 {len(failures)} 项：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
