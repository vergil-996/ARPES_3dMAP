# -*- coding: utf-8 -*-
"""在隔离环境中验收齿轮设置窗口、插件管理及真实 WSe₂ 三维质量切换。"""
import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tests.support.environment import isolate_runtime

isolate_runtime()
os.environ.pop("QT_QPA_PLATFORM", None)
_SANDBOX = tempfile.TemporaryDirectory(prefix="bandscope-plugin-entry-")
_SANDBOX_PATH = Path(_SANDBOX.name)

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


def verify_volume_quality(source):
    from PyQt5.QtCore import QSettings
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from bandscope.app.refactored_app import My3DAnalyzer

    QSettings("ARPES", "ARPES_3dMAP").setValue("compute_backend", "CPU")
    window = My3DAnalyzer()
    window.update_controller.automatic_check_due = lambda: False
    window.resize(1020, 670)
    window.show()
    app.processEvents()
    if not source.is_file():
        raise FileNotFoundError(source)
    def fail_on_message(title, detail, *_args):
        raise AssertionError(f"{title} — {detail}")
    window._show_message = fail_on_message
    print(f"加载 {source.name}，计算后端 {window.backend_manager.mode}", flush=True)
    if not window.load_data(str(source)):
        raise RuntimeError(f"无法加载 {source}")

    def wait_for_volume():
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            QTest.qWait(80)
            if window.volume_session.active and window._render_exact_ready:
                return window.volume_session.volume.mapper
        raise RuntimeError("等待 WSe₂ 精确三维帧超时")

    print("等待标准采样帧", flush=True)
    standard = wait_for_volume().GetSampleDistance()
    settings = window.open_settings()
    settings.navigation.setCurrentItem(settings.navigation.topLevelItem(0).child(1))
    settings.resting.setCurrentIndex(settings.resting.findData("fine"))
    fine = wait_for_volume().GetSampleDistance()
    check(f"WSe₂ 精细采样距离为标准的一半（{standard:.4f} → {fine:.4f}）",
          standard > 0 and abs(fine - standard * 0.5) < 1e-5)
    window.plotter.render()
    frame = window.plotter.screenshot(str(OUT_DIR / "wse2_fine.png"), return_img=True)
    check("精细采样后的三维帧含可见内容", frame.max() > frame.min())
    window.close()
    app.processEvents()
    return int(bool(failures))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--volume", nargs="?", type=Path, const=REPO_ROOT / ".local/data/WSe2_step.npz",
                        help="单独验证真实三维采样；默认使用 WSe₂")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.volume:
        return verify_volume_quality(args.volume)

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

    from PyQt5.QtWidgets import QApplication, QScrollArea

    app = QApplication.instance() or QApplication([])
    from bandscope.app.refactored_app import My3DAnalyzer

    window = My3DAnalyzer()
    window.update_controller.automatic_check_due = lambda: False
    window.show()
    app.processEvents()

    check("顶部不再有计算信息控件 backend_chip", not hasattr(window, "backend_chip"))
    check("顶部不再有硬件指示灯 backend_badge", not hasattr(window, "backend_badge"))
    check("旧扩展按钮已移除", not hasattr(window, "btn_tb_plugins"))
    check("旧版本按钮已移除", not hasattr(window, "btn_tb_version"))
    button = window.btn_tb_settings
    button.click()
    settings = window.settings_dialog
    nav = settings.navigation
    plugin_panel = settings.plugins
    check("齿轮按钮存在且可用", button.isEnabled())
    check("齿轮按钮提示正确", button.toolTip() == "设置")
    check(
        f"齿轮按钮尺寸为 28×26（实际 {button.width()}×{button.height()}）",
        (button.width(), button.height()) == (28, 26),
    )
    check(
        "设置窗口的分类及详情页",
        [[nav.topLevelItem(i).child(j).text(0) for j in range(nav.topLevelItem(i).childCount())]
         for i in range(nav.topLevelItemCount())]
        == [["计算后端", "3D渲染质量"], ["去噪参数"], ["插件管理", "更新设置"]],
    )
    check("插件管理页可用", plugin_panel.isEnabled())
    check("插件管理页提示正确", plugin_panel.toolTip() == "安装、卸载及启用/停用插件")
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

    # 未加载数据即可通过导航打开；开启裁剪后也能进入。
    nav.setCurrentItem(nav.topLevelItem(2).child(0))
    app.processEvents()
    check("选择导航直接显示内嵌插件管理", plugin_panel.isVisible() and settings.isVisible())
    check("插件管理内嵌于设置窗口", plugin_panel.window() is settings)
    check("外层窗口标题为「设置」", settings.windowTitle() == "设置")
    check("设置窗口非模态", not settings.isModal())
    check(
        "重复打开复用同一设置窗口和插件面板",
        window.open_plugin_manager() is plugin_panel and window.settings_dialog is settings,
    )
    check(
        "插件管理包含已安装、官方插件和插件设置三页签",
        plugin_panel.tabs.count() == 3
        and [plugin_panel.tabs.tabText(i) for i in range(plugin_panel.tabs.count())]
        == ["已安装", "官方插件", "插件设置"],
    )
    check("列表显示已安装插件", plugin_panel.table.rowCount() == 1)
    check("状态列显示运行中", "运行中" in plugin_panel.table.item(0, 2).text())
    check("安装按钮文案", plugin_panel.btn_install.text() == "安装插件…")
    settings.grab().save(str(OUT_DIR / "manager_window.png"))

    nav.setCurrentItem(nav.topLevelItem(1).child(0))
    check("去噪参数直接显示在设置中", settings.denoise.isVisible())
    settings.grab().save(str(OUT_DIR / "denoise_settings.png"))

    from PyQt5.QtTest import QTest

    nav.setCurrentItem(nav.topLevelItem(2).child(1))
    app.processEvents()
    update_panel = settings.updates
    check("选择导航直接显示更新配置", update_panel.isVisible())
    check("更新设置内嵌于设置窗口", update_panel.window() is settings)
    check("更新设置包含检查更新按钮", update_panel.check_updates_button.isVisible())
    check(
        "更新设置包含保存和取消按钮",
        all(button.isVisible() for button in update_panel.buttons.buttons()),
    )
    settings.grab().save(str(OUT_DIR / "update_settings.png"))

    window.btn_tb_crop.setChecked(True)
    app.processEvents()
    panel_in_crop = window.open_plugin_manager()
    check(
        "裁剪模式下仍可进入插件管理",
        panel_in_crop is plugin_panel and plugin_panel.isVisible() and settings.isVisible(),
    )
    window.btn_tb_crop.setChecked(False)
    app.processEvents()

    settings.resize(646, 400)
    for palette in ("dark", "light"):
        nav.setCurrentItem(nav.topLevelItem(0).child(1))
        window._set_theme(palette)
        QTest.qWait(150)
        check(f"{palette} 主题窄窗下静止质量控件完整可见",
              settings.resting.visibleRegion().boundingRect() == settings.resting.rect())
        settings.grab().save(str(OUT_DIR / f"settings_{palette}.png"))

        settings.select_page("plugins")
        app.processEvents()
        check(f"{palette} 主题窄窗下插件管理页可见", plugin_panel.isVisible())
        plugin_actions = (
            plugin_panel.btn_install,
            plugin_panel.btn_toggle,
            plugin_panel.btn_uninstall,
            plugin_panel.btn_retry,
            plugin_panel.btn_restore,
            plugin_panel.btn_relaunch,
        )
        for action in plugin_actions:
            check(
                f"{palette} 主题窄窗下插件操作「{action.text()}」不裁切",
                action.isVisible() and action.visibleRegion().boundingRect() == action.rect(),
            )
        settings.grab().save(str(OUT_DIR / f"settings_plugins_{palette}.png"))

        settings.select_page("updates")
        app.processEvents()
        check(f"{palette} 主题窄窗下更新设置页可见", update_panel.isVisible())
        update_scroll = update_panel.parentWidget()
        while update_scroll is not None and not isinstance(update_scroll, QScrollArea):
            update_scroll = update_scroll.parentWidget()
        check(f"{palette} 主题窄窗下更新设置页位于滚动区", update_scroll is not None)
        update_actions = [update_panel.check_updates_button] + update_panel.buttons.buttons()
        initial_scroll = update_scroll.verticalScrollBar().value() if update_scroll is not None else 0
        for action in update_actions:
            if update_scroll is not None:
                update_scroll.ensureWidgetVisible(action)
                app.processEvents()
            check(
                f"{palette} 主题窄窗下更新操作「{action.text()}」不裁切",
                update_scroll is not None
                and action.isVisible()
                and action.visibleRegion().boundingRect() == action.rect(),
            )
        if update_scroll is not None:
            update_scroll.verticalScrollBar().setValue(initial_scroll)
            app.processEvents()
        settings.grab().save(str(OUT_DIR / f"settings_updates_{palette}.png"))

    settings.close()
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
