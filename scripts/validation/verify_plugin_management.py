# -*- coding: utf-8 -*-
"""齿轮设置菜单、插件管理窗口与布局的真实窗口验收。

验证点：
1. 顶部不显示计算硬件指示灯、旧扩展按钮和旧版本按钮；28×26 齿轮按钮
   位于导出按钮右侧，菜单包含更新设置、插件管理和去噪参数设置；
2. 1550×950、1280×800 及可用最窄宽度下齿轮按钮不被挤压，底部版本号保留；
3. 菜单动作打开（复用）非模态管理窗口；未加载数据、开启裁剪时也能进入；
4. 管理窗口显示已安装插件、状态与操作按钮；重开复用同一窗口；
5. 画布不显示计算状态浮层，底部状态栏仍接收计算状态更新；
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
    check("顶部不再有硬件指示灯 backend_badge", not hasattr(window, "backend_badge"))
    check("旧扩展按钮已移除", not hasattr(window, "btn_tb_plugins"))
    check("旧版本按钮已移除", not hasattr(window, "btn_tb_version"))
    button = window.btn_tb_settings
    actions = {action.text(): action for action in window.settings_menu.actions()}
    plugin_action = actions["插件管理…"]
    check("齿轮按钮存在且可用", button.isEnabled())
    check("齿轮按钮提示正确", button.toolTip() == "设置")
    check(
        f"齿轮按钮尺寸为 28×26（实际 {button.width()}×{button.height()}）",
        (button.width(), button.height()) == (28, 26),
    )
    check(
        "设置菜单包含三个集中入口",
        list(actions) == ["更新设置…", "插件管理…", "去噪参数设置…"],
    )
    check("插件管理动作可用", plugin_action.isEnabled())
    check("插件管理动作提示正确", plugin_action.toolTip() == "安装、卸载及启用/停用插件")
    check("底部保留版本号", window.status_version.text().startswith("v"))

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
        export_rect = window.btn_main_export.geometry()
        gap = button_rect.left() - export_rect.right()
        check(f"{width}×{height}: 齿轮在导出按钮右侧且不重叠", export_rect.right() < button_rect.left())
        check(f"{width}×{height}: 间距合理（0 < 间隙 < 40px）", 0 <= gap < 40)
        check(
            f"{width}×{height}: 两个按钮都在工具栏内",
            export_rect.right() <= toolbar.width() and button_rect.right() <= toolbar.width(),
        )
        window.grab().save(str(OUT_DIR / f"main_{width}x{height}.png"))

    window.resize(window.minimumWidth() if window.minimumWidth() > 600 else 1100, 800)
    app.processEvents()
    check("最窄宽度: 齿轮仍在导出按钮右侧", window.btn_main_export.geometry().right() < button.geometry().left())
    window.grab().save(str(OUT_DIR / "main_narrow.png"))

    window.resize(1550, 950)
    app.processEvents()

    # 计算状态只保留底部状态栏，不再创建画布浮层。
    window._update_render_status("complete", "CPU")
    app.processEvents()
    check("画布计算状态浮层已移除", not hasattr(window, "render_status_label"))
    check("底部状态栏显示就绪", "就绪" in window.status_state.text())

    # 未加载数据即可通过菜单打开；开启裁剪后也能进入。
    plugin_action.trigger()
    dialog = window.__dict__.get("_plugin_dialog")
    check("管理窗口打开", dialog is not None and dialog.isVisible())
    check("窗口标题为「插件管理」", dialog.windowTitle() == "插件管理")
    check("窗口非模态", not dialog.isModal())
    check("重复打开复用同一窗口", window.open_plugin_manager() is dialog)
    check("列表显示已安装插件", dialog.table.rowCount() == 1)
    check("状态列显示运行中", "运行中" in dialog.table.item(0, 2).text())
    check("安装按钮文案", dialog.btn_install.text() == "安装插件…")
    dialog.grab().save(str(OUT_DIR / "manager_window.png"))
    dialog.close()

    actions["去噪参数设置…"].trigger()
    check("去噪参数窗口由设置菜单打开", window.denoise_popup.isVisible())
    window.denoise_popup.grab().save(str(OUT_DIR / "denoise_settings.png"))
    window.denoise_popup.close()

    from bandscope.updates.update_settings_dialog import UpdateSettingsDialog

    update_dialog = UpdateSettingsDialog(window)
    update_dialog.show()
    app.processEvents()
    check(
        "更新设置包含检查更新按钮",
        update_dialog.check_updates_button.isVisible()
        and update_dialog.check_updates_button.text() == "检查更新",
    )
    update_dialog.grab().save(str(OUT_DIR / "update_settings.png"))
    update_dialog.close()
    update_dialog.deleteLater()

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
