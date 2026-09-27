# -*- coding: utf-8 -*-
"""把 ``plugins/<id>`` 打成可导入的扩展包（``.bsplugin``）。

和主程序共用一个仓库、同一个 Release，但产物完全分开：

* 基础安装包（CPU / NVIDIA）**不含** ``plugins/`` 源码；
* 本脚本产出一个 ``.bsplugin`` 供两种基础安装包共用，CPU 与 GPU 版装同一个包；
* 同时写一份 ``.sha256``，与现有发布流程里安装器的校验文件保持一致。

包内只放清单、Python 代码和必要的 UI 资源，不带 NumPy / Qt / VTK / CUDA——
这些由基础包提供。``plugin_manager.install_package`` 会在安装时再核对一次。

用法::

    python scripts/release/build_plugin.py flat_band_opacity
    python scripts/release/build_plugin.py flat_band_opacity --output-dir release
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bandscope.app_metadata import APP_VERSION  # noqa: E402
from bandscope.extensions.api import API_VERSION, PluginError, PluginManifest  # noqa: E402
from bandscope.extensions.plugin_manager import (  # noqa: E402
    FORBIDDEN_LIBRARIES,
    PLUGIN_SUFFIX,
    inspect_archive,
)

PLUGIN_SOURCE_ROOT = REPO_ROOT / "plugins"

#: 打进包里的文件类型白名单。README 带上，方便用户拿到包后也能看到说明。
INCLUDED_SUFFIXES = (".py", ".json", ".md", ".txt", ".png", ".svg", ".qss")

#: 不进入产物的开发期文件。
EXCLUDED_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache"}


class BuildError(Exception):
    pass


def collect_files(package_dir: Path):
    files = []
    for path in sorted(package_dir.rglob("*")):
        if any(part in EXCLUDED_NAMES for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in INCLUDED_SUFFIXES:
            continue
        files.append(path)
    return files


def check_no_bundled_libraries(package_dir: Path, files) -> None:
    for path in files:
        if path.parent == package_dir and path.stem in FORBIDDEN_LIBRARIES:
            raise BuildError(
                f"扩展包不能自带主程序已提供的依赖：{path.name}。"
            )
        if path.parent.name in FORBIDDEN_LIBRARIES:
            raise BuildError(
                f"扩展包不能自带主程序已提供的依赖目录：{path.parent.name}。"
            )


def build(plugin_id: str, output_dir: Path, app_version: str) -> Path:
    package_dir = PLUGIN_SOURCE_ROOT / plugin_id
    manifest_path = package_dir / "plugin.json"
    if not manifest_path.is_file():
        raise BuildError(f"找不到插件清单：{manifest_path}")

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = PluginManifest.from_mapping(payload)

    if manifest.plugin_id != plugin_id:
        raise BuildError(
            f"清单里的 id 是 {manifest.plugin_id}，与目录名 {plugin_id} 不一致。"
        )
    if int(manifest.api_version) != API_VERSION:
        raise BuildError(
            f"{manifest.name} 的 api_version={manifest.api_version}，"
            f"当前宿主接口版本是 {API_VERSION}。"
        )
    expected = manifest.requires_app
    if expected != app_version:
        raise BuildError(
            f"{manifest.name} 要求主程序 {expected}，而当前仓库版本是 {app_version}；"
            "发布前请同步两者。"
        )

    files = collect_files(package_dir)
    check_no_bundled_libraries(package_dir, files)
    names = {path.relative_to(package_dir).as_posix() for path in files}
    entry_module = manifest.entry_module.replace(".", "/") + ".py"
    if entry_module not in names:
        raise BuildError(f"扩展包内找不到入口模块 {entry_module}。")

    output_dir.mkdir(parents=True, exist_ok=True)
    # 产物名用 id 而不是显示名：下载、CI 与校验脚本都要处理这个文件名，中文名在
    # 不同控制台编码下很容易变成乱码。
    archive_name = f"BandScope-{manifest.plugin_id}-{manifest.version}{PLUGIN_SUFFIX}"
    archive_path = output_dir / archive_name
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(package_dir).as_posix())

    verify_archive(archive_path)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {archive_name}\n", encoding="utf-8")
    return archive_path


def verify_archive(archive_path: Path) -> None:
    """重新打开产物核对一遍：清单能被安装流程解析，且没有夹带共享依赖。

    安装流程自己也会校验，但在这里失败能直接指出是哪一步打错了包，而不是等到
    CI 里另一个脚本报错。
    """
    manifest, _prefix = inspect_archive(archive_path)
    if manifest.plugin_id not in archive_path.name:
        raise BuildError(
            f"产物文件名与清单 id 不一致：{archive_path.name} / {manifest.plugin_id}"
        )
    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            head = name.replace("\\", "/").split("/")[0].split(".")[0]
            if head in FORBIDDEN_LIBRARIES:
                raise BuildError(f"产物夹带了主程序已提供的依赖：{name}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="构建 BandScope 扩展包")
    parser.add_argument("plugin_id", help="plugins/ 下的目录名，例如 flat_band_opacity")
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "release"),
        help="产物目录（默认 release/）",
    )
    parser.add_argument(
        "--app-version",
        default=APP_VERSION,
        help="用于核对 requires_app 的主程序版本（默认取 app_metadata）",
    )
    args = parser.parse_args(argv)

    try:
        archive = build(args.plugin_id, Path(args.output_dir), args.app_version)
    except (BuildError, OSError, ValueError, PluginError) as exc:
        print(f"构建失败：{exc}", file=sys.stderr)
        return 1
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(f"已生成 {archive}")
    print(f"  sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
