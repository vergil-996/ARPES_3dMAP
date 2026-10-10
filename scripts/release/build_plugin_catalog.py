# -*- coding: utf-8 -*-
"""生成官方插件目录 ``plugins-index.json``（阶段 E）。

目录跟着主程序 Release 一起发布，记录每个插件版本对应的**不可变** Release 资源
地址、大小与 SHA-256。历史记录用 ``--merge`` 带进来：同一 id/version 一旦发布就
不能换成不同内容，脚本会直接拒绝。

用法::

    # 先打包并签名，再生成目录（目录本身由 sign_plugin.py --purpose catalog 签名）
    python scripts/release/build_plugin_catalog.py \\
        --tag v1.12.4 --output release/plugins-index.json \\
        release/BandScope-flat_band_opacity-1.0.1.bsplugin

    # 聚合上一版目录，保留仍受支持宿主的历史包
    python scripts/release/build_plugin_catalog.py --tag v1.12.4 \\
        --merge previous/plugins-index.json --output release/plugins-index.json release/*.bsplugin
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bandscope.app_metadata import APP_VERSION, GITHUB_REPOSITORY_SLUG  # noqa: E402
from bandscope.extensions.catalog import (  # noqa: E402
    CATALOG_SCHEMA,
    MAX_PLUGIN_PACKAGE_BYTES,
    CatalogError,
    parse_catalog,
)
from bandscope.extensions.compat import SUPPORTED_API_VERSIONS  # noqa: E402
from bandscope.extensions.plugin_manager import inspect_archive  # noqa: E402
from bandscope.extensions.trust import (  # noqa: E402
    PURPOSE_PLUGIN,
    TRUSTED_PLUGIN_KEYS,
    TrustError,
    sidecar_path,
    verify_file,
)


class CatalogBuildError(Exception):
    pass


def asset_url(tag: str, name: str) -> str:
    return f"https://github.com/{GITHUB_REPOSITORY_SLUG}/releases/download/{tag}/{name}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entry_from_package(package: Path, tag: str, *, require_signature: bool) -> dict:
    package = Path(package)
    if not package.is_file():
        raise CatalogBuildError(f"找不到插件包：{package}")
    if package.stat().st_size > MAX_PLUGIN_PACKAGE_BYTES:
        raise CatalogBuildError(f"插件包超出大小上限：{package.name}")

    signature_file = sidecar_path(package)
    if require_signature:
        if not signature_file.is_file():
            raise CatalogBuildError(
                f"{package.name} 没有签名文件；正式目录不允许未签名包。"
            )
        try:
            verify_file(package, purpose=PURPOSE_PLUGIN)
        except TrustError as exc:
            raise CatalogBuildError(f"{package.name} 验签失败：{exc}") from exc
    elif not signature_file.is_file():
        raise CatalogBuildError(f"{package.name} 没有签名文件，无法作为官方包发布。")

    try:
        manifest, _prefix = inspect_archive(package)
    except Exception as exc:  # 清单/包结构问题统一转成构建错误
        raise CatalogBuildError(f"{package.name} 无法解析：{exc}") from exc
    if manifest.plugin_id not in package.name:
        raise CatalogBuildError(
            f"产物文件名与清单 id 不一致：{package.name} / {manifest.plugin_id}"
        )

    return {
        "id": manifest.plugin_id,
        "name": manifest.name,
        "version": manifest.version,
        "requires_app": manifest.requires_app,
        "api_version": int(manifest.api_version),
        "capabilities": list(manifest.capabilities),
        "notes": manifest.description,
        "package": {
            "name": package.name,
            "url": asset_url(tag, package.name),
            "size": package.stat().st_size,
            "sha256": _sha256(package),
        },
        "signature": {
            "name": signature_file.name,
            "url": asset_url(tag, signature_file.name),
            "size": signature_file.stat().st_size,
            "sha256": _sha256(signature_file),
        },
    }


def load_previous(path) -> list:
    """读取上一版目录里的插件记录；有签名就先验签再聚合。"""
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CatalogBuildError(f"无法读取要合并的目录：{exc}") from exc
    if sidecar_path(target).is_file():
        try:
            verify_file(target, purpose=PURPOSE_CATALOG)
        except TrustError as exc:
            raise CatalogBuildError(f"要合并的目录验签失败：{exc}") from exc
    else:
        print(f"注意：{target.name} 没有签名 sidecar，按未验证来源聚合。", file=sys.stderr)
    entries = payload.get("plugins")
    if not isinstance(entries, list):
        raise CatalogBuildError("要合并的目录里 plugins 不是列表。")
    return entries


def merge_entries(new_entries: list, previous: list) -> list:
    """把历史记录带进来；同一 id/version 的内容必须一模一样。"""
    merged = {(item["id"], item["version"]): item for item in new_entries}
    for item in previous:
        key = (item.get("id"), item.get("version"))
        existing = merged.get(key)
        if existing is None:
            merged[key] = item
            continue
        if existing.get("package", {}).get("sha256") != item.get("package", {}).get("sha256"):
            raise CatalogBuildError(
                f"{key[0]} {key[1]} 已在历史目录里，但内容不同；"
                "同一 id/version 发布后不能再改内容，请提升插件版本。"
            )
    return list(merged.values())


def build(
    packages,
    *,
    tag: str,
    output: Path,
    merge=None,
    host_version: str = APP_VERSION,
    revision: int = 0,
) -> Path:
    entries = [
        entry_from_package(Path(package), tag, require_signature=True)
        for package in packages
    ]
    if merge is not None:
        entries = merge_entries(entries, load_previous(merge))
    entries.sort(key=lambda item: (item["id"], item["version"]))

    if not TRUSTED_PLUGIN_KEYS:
        # 目录里会写进官方包，但宿主还没有内置公钥：用户装上去一定验不过。
        raise CatalogBuildError(
            "宿主没有内置任何官方公钥，生成的目录在客户端无法验证；"
            "请先按 docs 配置 TRUSTED_PLUGIN_KEYS。"
        )

    payload = {
        "schema": CATALOG_SCHEMA,
        "revision": int(revision),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": {
            "app_version": str(host_version),
            "api_versions": sorted(int(item) for item in SUPPORTED_API_VERSIONS),
        },
        "plugins": entries,
    }
    # 自己生成的目录也要过一遍解析：结构错误在这里失败，而不是发到用户机器上才炸。
    parse_catalog(json.dumps(payload, ensure_ascii=False))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成官方插件目录")
    parser.add_argument("packages", nargs="+", help="已签名的 .bsplugin 文件")
    parser.add_argument("--tag", required=True, help="本次 Release 的 tag，例如 v1.12.4")
    parser.add_argument("--output", required=True, help="输出 plugins-index.json 的路径")
    parser.add_argument("--merge", default=None, help="上一版 plugins-index.json，用于保留历史记录")
    parser.add_argument("--host-version", default=APP_VERSION, help="目录声明的目标宿主版本")
    parser.add_argument(
        "--revision",
        type=int,
        default=0,
        help="目录修订号；缺省时在合并的历史修订号上 +1",
    )
    args = parser.parse_args(argv)

    revision = int(args.revision)
    if not revision and args.merge:
        try:
            revision = parse_catalog(Path(args.merge).read_bytes()).revision
        except (OSError, CatalogError):
            revision = 0
    if not revision:
        revision = 1

    try:
        output = build(
            args.packages,
            tag=args.tag,
            output=Path(args.output),
            merge=args.merge,
            host_version=args.host_version,
            revision=revision,
        )
    except (CatalogBuildError, OSError, ValueError) as exc:
        print(f"目录生成失败：{exc}", file=sys.stderr)
        return 1
    print(f"已生成 {output}（修订号 {revision}，共 {_entry_count(output)} 条记录）")
    print("下一步：python scripts/release/sign_plugin.py "
          f"{output} --purpose catalog")
    return 0


def _entry_count(path: Path) -> int:
    try:
        return len(json.loads(Path(path).read_text(encoding="utf-8")).get("plugins", []))
    except (OSError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
