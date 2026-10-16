"""把一个 BandScope 正式版发布到 MatElab 更新源。

用法：

    python scripts/release/publish_matelab.py --version 1.14.0 \
        --installer CPU=dist/BandScope-1.14.0-Windows-x64-CPU-Setup.exe \
        --installer NVIDIA=dist/BandScope-1.14.0-Windows-x64-NVIDIA-Setup.exe \
        --notes docs/release-notes.md

凭证来自命令行 --username/--password、环境变量 MATELAB_USER/MATELAB_PASSWORD，
或交互输入（密码不回显）。

前置条件（一次性手工准备）：

1. 在 MatElab 上创建发布记录本（默认「BandScope发布」，可用 --notebook 改）；
2. 在该记录本里建一个模板（默认「BandScope Release」，可用 --template 改），
   包含三个模块：「发布说明」（富文本）、「manifest」（富文本）、「安装包」（文件集）。

发布内容：每个版本一条记录，关键词含 bandscope-release；manifest 模块写入
JSON {"version", "notes", "published_at"}；安装包通过官方分片上传接口上传后
以 #file{name} 引用挂到「安装包」模块。客户端（MatelabReleaseClient）按此约定
发现版本、取 SHA-256 并构造下载地址。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bandscope.app_metadata import (
    APP_NAME,
    MATELAB_API_BASE,
    MATELAB_RELEASE_KEYWORD,
    MATELAB_RELEASE_NOTEBOOK,
    installer_asset_name,
)
from bandscope.updates.matelab_source import (
    MatelabCredentials,
    MatelabSession,
    upload_file,
)
from bandscope.updates.update_service import SemanticVersion

DEFAULT_TEMPLATE = "BandScope Release"


def _resolve_credentials(args) -> MatelabCredentials:
    username = args.username or os.environ.get("MATELAB_USER", "").strip()
    password = args.password or os.environ.get("MATELAB_PASSWORD", "")
    if not username:
        username = input("MatElab 用户名（中文姓名/电子邮箱）: ").strip()
    if not password:
        password = getpass.getpass("MatElab 密码: ")
    return MatelabCredentials(username=username, password=password)


def _find_notebook(session: MatelabSession, notebook: str) -> dict:
    payload = session.api("eln_api/elns", {})
    candidates = list(payload.get("my") or ()) + list(payload.get("share") or ())
    for entry in candidates:
        if str(entry.get("showtext") or "") == notebook:
            return entry
    raise SystemExit(f"MatElab 上没有找到发布记录本「{notebook}」，请先创建。")


def _upload_installers(session, notebook, version, installers) -> list:
    """上传全部安装包，返回 [{'flavor', 'name', 'filename', 'size', 'hash'}]。"""
    uploaded = []
    for flavor, installer_path in installers:
        path = Path(installer_path)
        expected_name = installer_asset_name(version, flavor)
        if path.name != expected_name:
            raise SystemExit(f"{flavor} 安装包文件名应为 {expected_name}，实际为 {path.name}")
        size = path.stat().st_size
        name = f"bs-{version.lower()}-{flavor.lower()}-{os.urandom(4).hex()}"
        print(f"上传 {flavor} 安装包 {path.name}（{size / (1024 ** 2):.1f} MiB）…")

        def _progress(sent, total, _flavor=flavor):
            print(f"\r  {_flavor}: {sent / (1024 ** 2):.0f}/{total / (1024 ** 2):.0f} MiB", end="")

        result = upload_file(session, path, name=name, eln=notebook, progress=_progress)
        print()
        uploaded.append(
            {
                "flavor": flavor,
                "name": name,
                "filename": path.name,
                "size": result.get("size", size),
                "hash": result.get("hash", ""),
            }
        )
    return uploaded


def _create_release_record(session, notebook, template, version, notes, uploaded) -> dict:
    manifest = {
        "version": version,
        "notes": notes,
        "published_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    notes_html = "".join(f"<p>{line}</p>" for line in notes.splitlines() if line.strip())
    files = [
        {"file": "#file{%s}" % item["name"], "text": "%s 安装包" % item["flavor"]}
        for item in uploaded
    ]
    payload = {
        "eln": notebook,
        "template": template,
        "dataset": [
            {
                "uid": f"bandscope-release-{version}",
                "title": f"{APP_NAME} v{version}",
                "keyword": f"{MATELAB_RELEASE_KEYWORD};v{version}",
                "data": {
                    "发布说明": notes_html or "<p>本次发布未填写更新说明。</p>",
                    "manifest": json.dumps(manifest, ensure_ascii=False),
                    "安装包": files,
                },
            }
        ],
    }
    result = session.api("eln_api/import", payload)
    print(f"发布记录已创建：{json.dumps(result, ensure_ascii=False)}")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="发布 BandScope 正式版到 MatElab 更新源。")
    parser.add_argument("--version", required=True, help="语义版本号，如 1.14.0")
    parser.add_argument(
        "--installer",
        action="append",
        default=[],
        metavar="FLAVOR=PATH",
        help="安装包，FLAVOR 为 CPU 或 NVIDIA；可重复。",
    )
    parser.add_argument("--notes", default="", help="更新说明文本文件（可选）")
    parser.add_argument("--notebook", default=MATELAB_RELEASE_NOTEBOOK)
    parser.add_argument("--template", default=DEFAULT_TEMPLATE)
    parser.add_argument("--base-url", default=MATELAB_API_BASE)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    args = parser.parse_args(argv)

    version = str(SemanticVersion.parse(args.version))
    installers = []
    for spec in args.installer:
        flavor, separator, path = spec.partition("=")
        if not separator or flavor.strip().upper() not in ("CPU", "NVIDIA"):
            raise SystemExit(f"--installer 需要 FLAVOR=PATH 形式（FLAVOR 为 CPU/NVIDIA）：{spec!r}")
        installers.append((flavor.strip().upper(), path))
    if not installers:
        raise SystemExit("至少需要提供一个 --installer。")

    notes = ""
    if args.notes:
        notes = Path(args.notes).read_text(encoding="utf-8").strip()

    session = MatelabSession(_resolve_credentials(args), args.base_url, timeout=60, opener=None)
    session.login()
    print("MatElab 登录成功。")
    _find_notebook(session, args.notebook)
    uploaded = _upload_installers(session, args.notebook, version, installers)
    _create_release_record(session, args.notebook, args.template, version, notes, uploaded)
    print(f"BandScope v{version} 已发布到 MatElab 记录本「{args.notebook}」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
