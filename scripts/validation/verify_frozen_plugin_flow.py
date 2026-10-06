# -*- coding: utf-8 -*-
"""冻结包里的插件流程验收：更新、失败候选、恢复上一版本与卸载（计划阶段 B）。

对给定安装包可执行文件连开五次隔离实例（每次用临时扩展根，不触碰用户数据）：

1. 基线：已安装 1.0.0 → 启动加载并写入 last_good；
2. 更新：装入 1.1.0 → 启动后 last_good 切到 1.1.0，旧版本内容在安全启动时清理；
3. 失败候选：装入导入即抛异常的 1.2.0 → 启动后记录 failed、last_good 保持 1.1.0，
   且不自动重复执行；
4. 恢复上一版本：登记恢复 → 启动后 desired 回到 1.1.0、failed 清除、能正常加载；
5. 卸载：登记卸载 → 启动后插件目录与登记条目都被移除。

启用/停用的“重启才切换”语义由统一测试覆盖；磁盘上无法从外部区分“停用未加载”
和“未启动”，因此不在本脚本里重复。

用法: .venv/Scripts/python.exe scripts/validation/verify_frozen_plugin_flow.py <BandScope.exe 路径>
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from bandscope.app_metadata import APP_VERSION
from bandscope.extensions.plugin_manager import InstallSource, install_package
from bandscope.extensions.trust import SOURCE_LOCAL
from bandscope.extensions.plugin_store import PluginStore
from scripts.release.build_plugin import build as build_plugin_package

PLUGIN_ID = "flat_band_opacity"
failures = []


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}", flush=True)
    if not condition:
        failures.append(label)


def launch_until(exe: Path, root: Path, predicate, *, timeout=90):
    """启动冻结程序直到 predicate(registry_entry_or_None) 为真；返回最终条目。"""
    env = dict(os.environ)
    env["BANDSCOPE_EXTENSION_ROOT"] = str(root)
    # 本仓库的 tests 包会把 QT_QPA_PLATFORM 设成 offscreen；冻结程序需要真实
    # 平台才能建 VTK 渲染窗口，这里显式去掉。
    env.pop("QT_QPA_PLATFORM", None)
    process = subprocess.Popen([str(exe)], env=env, cwd=str(REPO_ROOT))
    registry = root / "registry.json"
    entry = None
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            try:
                payload = json.loads(registry.read_text(encoding="utf-8"))
                entry = payload["plugins"].get(PLUGIN_ID)
            except (OSError, ValueError, KeyError):
                entry = None
            if predicate(entry):
                return entry
            time.sleep(1.5)
        return entry
    finally:
        process.kill()
        process.wait(timeout=30)


def make_archive(directory: Path, version: str, *, broken=False) -> Path:
    manifest = {
        "id": PLUGIN_ID,
        "name": "平带增强",
        "version": version,
        "api_version": 1,
        "requires_app": APP_VERSION,
        "entry_point": "entry:Plugin",
        "capabilities": ["opacity_multiplier"],
    }
    entry_source = (
        "raise RuntimeError('frozen check: broken candidate')\n"
        if broken
        else "from plugin_api import Plugin\n\n\nclass Plugin(Plugin):\n"
        "    def create_panel(self, host):\n        return None\n"
    )
    from tests.support.plugins import make_plugin_archive

    return make_plugin_archive(
        directory / f"pkg-{version}.bsplugin",
        manifest=manifest,
        entry_source=entry_source,
    )


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    exe = Path(argv[1])
    if not exe.is_file():
        print(f"找不到可执行文件：{exe}")
        return 2

    sandbox = tempfile.TemporaryDirectory(prefix="frozen-plugin-flow-")
    root = Path(sandbox.name) / "extensions"
    store = PluginStore(root)
    print(f"扩展根（临时）：{root}")

    # 冻结构建里的合成包同样没有签名：显式按已确认的本地来源安装。
    local = InstallSource(kind=SOURCE_LOCAL, accepted_unverified=True)
    install_package(make_archive(Path(sandbox.name), "1.0.0"), root=root, source=local)
    entry = launch_until(exe, root, lambda e: bool(e and e.get("last_good")))
    check("1. 基线：启动加载 1.0.0 并写入 last_good",
          bool(entry and entry.get("last_good", {}).get("version") == "1.0.0"))

    install_package(make_archive(Path(sandbox.name), "1.1.0"), root=root, source=local)
    entry = launch_until(
        exe, root, lambda e: bool(e and e.get("last_good", {}).get("version") == "1.1.0")
    )
    check("2. 更新：last_good 切到 1.1.0",
          bool(entry and entry.get("last_good", {}).get("version") == "1.1.0"))
    check("2. 更新：1.0.0 作为回退版本（previous_good）保留",
          bool(entry and entry.get("previous_good", {}).get("version") == "1.0.0"))
    versions = sorted(p.name for p in (root / "installed" / PLUGIN_ID).iterdir())
    check("2. 更新：磁盘上只保留当前版本与一个回退版本", versions == ["1.0.0", "1.1.0"])

    install_package(make_archive(Path(sandbox.name), "1.2.0", broken=True), root=root, source=local)
    entry = launch_until(exe, root, lambda e: bool(e and e.get("failed")))
    check("3. 失败候选：记录 failed 且不标记成功",
          bool(entry and entry.get("failed")
               and entry.get("last_good", {}).get("version") == "1.1.0"))
    second = launch_until(exe, root, lambda e: False, timeout=25)
    check("3. 失败候选：未经重试不自动重复（desired 仍是 1.2.0 且 failed 仍在）",
          bool(second and second.get("failed")
               and second.get("desired", {}).get("version") == "1.2.0"))

    store.request_restore(PLUGIN_ID)
    entry = launch_until(
        exe, root,
        lambda e: bool(e and e.get("desired", {}).get("version") == "1.1.0" and "failed" not in e
                       and e.get("last_good", {}).get("version") == "1.1.0"),
    )
    check("4. 恢复上一版本：desired 回到 1.1.0、failed 清除并成功加载",
          bool(entry and entry.get("desired", {}).get("version") == "1.1.0"
               and "failed" not in entry))
    check("4. 恢复：失败的 1.2.0 内容作为未引用内容被清理",
          not (root / "installed" / PLUGIN_ID / "1.2.0").exists())

    store.request_uninstall(PLUGIN_ID)
    entry = launch_until(exe, root, lambda e: e is None)
    check("5. 卸载：登记条目已移除", entry is None)
    check("5. 卸载：安装目录已删除", not (root / "installed" / PLUGIN_ID).exists())

    print()
    if failures:
        print(f"未通过 {len(failures)} 项：" + "；".join(failures))
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
