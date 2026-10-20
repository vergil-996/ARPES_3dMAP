"""Read-only post-publish verification of the MatElab release record.

2026-10-20 起按协作约定，MatElab 上传完成、发布记录创建成功即视为发出，**不再要求**
本脚本的回传下载复验；保留此脚本用于排查网络/内容问题或按需抽查。

步骤：
1. 从上一个补丁版本检查更新，两个 flavor 都应发现当前版本，且文件名/大小/SHA-256 与 CI 发布值一致；
2. CPU 安装包经客户端 download_installer 完整下载并校验；
3. NVIDIA 安装包做头/中/尾三段 4 MiB 区间抽检，与 CI 版逐字节一致。

用法：python scripts/validation/verify_matelab_release.py {check|download-cpu|sample-nvidia}
CI 产物需先下载到 release/（含 .sha256 边车）。
"""
import base64
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bandscope.app_metadata import APP_VERSION

CI_DIR = ROOT / "release"
OUT_DIR = ROOT / ".local" / "outputs" / f"matelab-verify-{APP_VERSION}"

_parts = APP_VERSION.split(".")
PREVIOUS_VERSION = os.environ.get("BANDSCOPE_PREVIOUS_VERSION", "").strip() or ".".join(
    [_parts[0], _parts[1], str(int(_parts[2]) - 1)]
)

EXPECTED = {}
for flavor in ("CPU", "NVIDIA"):
    name = f"BandScope-{APP_VERSION}-Windows-x64-{flavor}-Setup.exe"
    digest = (CI_DIR / f"{name}.sha256").read_text().split()[0].strip().lower()
    EXPECTED[flavor] = {"name": name, "size": (CI_DIR / name).stat().st_size, "sha256": digest}


def credentials():
    from PyQt5.QtCore import QSettings

    from bandscope.updates.matelab_source import MatelabCredentials

    settings = QSettings("ARPES", "ARPES_3dMAP")
    username = str(settings.value("updates/matelab_username", "") or "")
    encoded = str(settings.value("updates/matelab_password_b64", "") or "")
    password = base64.b64decode(encoded).decode("utf-8") if encoded else ""
    return MatelabCredentials(username=username, password=password)


def client():
    from bandscope.updates.matelab_source import MATELAB_API_BASE, MATELAB_RELEASE_NOTEBOOK, MatelabReleaseClient

    return MatelabReleaseClient(credentials, MATELAB_RELEASE_NOTEBOOK, MATELAB_API_BASE, timeout=60, opener=None)


def step_check():
    cli = client()
    failures = []
    for flavor in ("CPU", "NVIDIA"):
        result = cli.check_for_update(PREVIOUS_VERSION, flavor)
        asset = result.installer
        expected = EXPECTED[flavor]
        ok = (
            asset is not None
            and str(result.release.version) == APP_VERSION
            and asset.name == expected["name"]
            and int(asset.size) == expected["size"]
            and str(asset.sha256).strip().lower() == expected["sha256"]
        )
        print(f"[{'PASS' if ok else 'FAIL'}] {flavor}: version={result.release.version} "
              f"name={asset.name if asset else None} size={asset.size if asset else None}", flush=True)
        if not ok:
            failures.append(flavor)
    if failures:
        raise SystemExit(f"检查更新验证失败：{failures}")


def step_download_cpu():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cli = client()
    result = cli.check_for_update(PREVIOUS_VERSION, "CPU")

    def progress(sent, total):
        print(f"\r  CPU 下载: {sent / (1024 ** 2):.0f}/{total / (1024 ** 2):.0f} MiB", end="", flush=True)

    path, digest = cli.download_installer(result.installer, OUT_DIR, progress=progress)
    print(flush=True)
    ok = digest.lower() == EXPECTED["CPU"]["sha256"]
    print(f"[{'PASS' if ok else 'FAIL'}] CPU 完整下载校验 sha256={digest[:16]}…", flush=True)
    if not ok:
        raise SystemExit("CPU 下载校验失败")


def _fetch_range(url, start, end):
    request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def step_sample_nvidia():
    cli = client()
    result = cli.check_for_update(PREVIOUS_VERSION, "NVIDIA")
    asset = result.installer
    url = asset.download_url if hasattr(asset, "download_url") else None
    if not url:
        url = getattr(asset, "url", None)
    if not url:
        raise SystemExit(f"无法取得 NVIDIA 资产下载地址：{asset!r}")
    local = CI_DIR / EXPECTED["NVIDIA"]["name"]
    size = EXPECTED["NVIDIA"]["size"]
    width = 4 * 1024 * 1024
    spans = [(0, width), ((size - width) // 2, width), (size - width, width)]
    with local.open("rb") as stream:
        for i, (offset, length) in enumerate(spans, 1):
            remote = _fetch_range(url, offset, offset + length - 1)
            stream.seek(offset)
            expected = stream.read(length)
            ok = remote == expected
            print(f"[{'PASS' if ok else 'FAIL'}] NVIDIA 区间抽检 {i}/3 offset={offset}", flush=True)
            if not ok:
                raise SystemExit(f"区间 {i} 不一致")


if __name__ == "__main__":
    {"check": step_check, "download-cpu": step_download_cpu, "sample-nvidia": step_sample_nvidia}[sys.argv[1]]()
