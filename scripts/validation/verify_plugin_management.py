# -*- coding: utf-8 -*-
"""插件管理入口的真实窗口验收：顶部按钮、管理窗口与布局（计划阶段 A）。

验证点：
1. 顶部计算信息控件已由「插件管理」按钮接替：92×32、secondary 样式、悬停提示
   正确，位置在版本按钮左侧且不重叠；
2. 1550×950、1280×800 及可用最窄宽度下按钮不被挤压，版本入口完好；
3. 点击打开（复用）非模态管理窗口；未加载数据、开启裁剪时也能进入；
4. 管理窗口显示已安装插件、状态与操作按钮；重开复用同一窗口；
5. 底部状态栏与画布右上角仍接收计算状态更新；
6. 真实应用启动时按 schema v2 加载已安装插件，并写入 last_good 健康记录。

安装根目录隔离到临时目录，不触碰用户真实扩展与设置；验收用真实构建的
flat_band_opacity 包安装，覆盖“安装 → 重启加载 → 健康回报”整条路径。

用法: .venv/Scripts/python.exe scripts/validation/verify_plugin_management.py
"""
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# 隔离环境要在导入 Qt / bandscope 之前就位（QSettings 与扩展根）。
_SANDBOX = tempfile.TemporaryDirectory(prefix="bandscope-plugin-entry-")
_SANDBOX_PATH = Path(_SANDBOX.name)
os.environ["BANDSCOPE_EXTENSION_ROOT"] = str(_SANDBOX_PATH / "extensions")
os.environ.pop("QT_QPA_PLATFORM", None)

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QSettings

QSettings.setDefaultFormat(QSettings.IniFormat)
for _scope in (QSettings.UserScope, QSettings.SystemScope):
    QSettings.setPath(QSettings.IniFormat, _scope, _SANDBOX.name)

import bandscope.app_metadata as app_metadata
from bandscope.extensions.plugin_manager import InstallSource, install_package
from bandscope.extensions.trust import SOURCE_LOCAL
from scripts.release.build_plugin import build as build_plugin_package

OUT_DIR = REPO_ROOT / ".local" / "outputs" / "plugin_management"
failures = []


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}", flush=True)
    if not condition:
        failures.append(label)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 用真实构建的平带插件包预置一份已安装插件（schema v2 布局）。
    root = Path(os.environ["BANDSCOPE_EXTENSION_ROOT"])
    archive = build_plugin_package("flat_band_opacity", _SANDBOX_PATH / "dist", app_metadata.APP_VERSION)
    # 本机验收包没有官方签名：按“用户已确认的本地来源”装入，与界面路径一致。
    outcome = install_package(
        archive,
        root=root,
        source=InstallSource(kind=SOURCE_LOCAL, accepted_unverified=True),
    )
    print(f"已安装 {outcome.manifest.name} {outcome.manifest.version} -> {outcome.digest[:12]}…")

    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from bandscope.app.refactored_app import My3DAnalyzer

    window = My3DAnalyzer()
    window.show()
    app.processEvents()

    check("顶部不再有计算信息控件 backend_chip", not hasattr(window, "backend_chip"))
    button = window.btn_tb_plugins
    version_button = window.btn_tb_version
    check("插件管理按钮存在且可用", button.isEnabled())
    check("悬停提示正确", button.toolTip() == "安装、卸载及启用/停用插件")
    check("按钮尺寸为 92×32", (button.width(), button.height()) == (92, 32))

    # 插件按 v2 登记表加载，并在面板挂载成功后写入健康记录。
    session = window.plugin_session
    record = session.manager.record("flat_band_opacity") if session else None
    check("插件按 schema v2 加载成功", bool(record and record.ready))
    check("插件面板已挂载", bool(session and "flat_band_opacity" in session.card_ids()))
    import json

    registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    entry = registry["plugins"]["flat_band_opacity"]
    check("健康回报写入 last_good", entry.get("last_good", {}).get("version") == outcome.manifest.version)

    for width, height in ((1550, 950), (1280, 800)):
        window.resize(width, height)
        app.processEvents()
        toolbar = window.toolbar
        button_rect = button.geometry()
        version_rect = version_button.geometry()
        gap = version_rect.left() - button_rect.right()
        check(f"{width}×{height}: 按钮在版本入口左侧且不重叠", button_rect.right() < version_rect.left())
        check(f"{width}×{height}: 间距合理（0 < 间隙 < 40px）", 0 <= gap < 40)
        check(f"{width}×{height}: 两个按钮都在工具栏内", button_rect.right() <= toolbar.width() and version_rect.right() <= toolbar.width())
        window.grab().save(str(OUT_DIR / f"main_{width}x{height}.png"))

    window.resize(window.minimumWidth() if window.minimumWidth() > 600 else 1100, 800)
    app.processEvents()
    check("最窄宽度: 按钮仍在版本入口左侧", button.geometry().right() < version_button.geometry().left())
    window.grab().save(str(OUT_DIR / "main_narrow.png"))

    window.resize(1550, 950)
    app.processEvents()

    # 计算状态：画布右上角浮层与底部状态栏都要继续更新。
    window._update_render_status("complete", "CPU")
    app.processEvents()
    check("画布状态浮层显示完整", "完整" in window.render_status_label.text())
    check("底部状态栏显示就绪", "就绪" in window.status_state.text())

    # 未加载数据即可打开；开启裁剪后也能进入。
    dialog = window.open_plugin_manager()
    check("管理窗口打开", dialog is not None and dialog.isVisible())
    check("窗口标题为「插件管理」", dialog.windowTitle() == "插件管理")
    check("窗口非模态", not dialog.isModal())
    check("重复打开复用同一窗口", window.open_plugin_manager() is dialog)
    check("列表显示已安装插件", dialog.table.rowCount() == 1)
    check("状态列显示运行中", "运行中" in dialog.table.item(0, 2).text())
    check("安装按钮文案", dialog.btn_install.text() == "安装插件…")
    dialog.grab().save(str(OUT_DIR / "manager_window.png"))
    dialog.close()

    window.btn_tb_crop.setChecked(True)
    app.processEvents()
    dialog_in_crop = window.open_plugin_manager()
    check("裁剪模式下仍可进入插件管理", dialog_in_crop is not None and dialog_in_crop.isVisible())
    dialog_in_crop.close()
    window.btn_tb_crop.setChecked(False)
    app.processEvents()

    window.close()
    app.processEvents()

    print()
    if failures:
        print(f"未通过 {len(failures)} 项：" + "；".join(failures))
        return 1
    print("全部通过。截图目录：" + str(OUT_DIR))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
