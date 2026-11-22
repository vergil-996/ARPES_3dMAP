"""Validate Fluent icons in the real main and secondary windows."""
import json
import os
from pathlib import Path
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, QSize, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication


def main():
    from scripts.validation._common import arguments
    from scripts.validation.verify_ui_themes import _NullUpdateController

    args = arguments(
        "icon-dpi-audit/windows",
        dynamic=True,
        default_data=Path(".local/data/WSe2_step.npz"),
    )
    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app
    from bandscope.ui import theme

    refactored_app.UpdateController = _NullUpdateController
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    initial_theme = theme.current_theme()
    captures = []
    checks = []
    failure = None

    def capture(widget, filename):
        widget.raise_()
        widget.activateWindow()
        app.processEvents()
        QTest.qWait(250)
        path = args.output_dir / filename
        if not widget.grab().save(str(path)) or not path.is_file():
            raise AssertionError(f"截图失败：{path}")
        captures.append(filename)
        print(f"[capture] {path}", flush=True)

    def guard(callback):
        nonlocal failure
        try:
            callback()
        except Exception as error:
            failure = error
            traceback.print_exc()
            window.close()
            app.exit(1)

    def load():
        if args.size:
            width, height = map(int, args.size.split("x"))
            window.showNormal()
            window.resize(width, height)
        if not window.load_data(str(args.data)):
            raise AssertionError("真实数据加载失败")
        QTimer.singleShot(12000, lambda: guard(lambda: run_theme(0)))

    def run_theme(index):
        if index >= 2:
            finish()
            return
        name = ("dark", "light")[index]
        window._set_theme(name)
        QTimer.singleShot(2200, lambda: guard(lambda: capture_main(name, index)))

    def capture_main(name, index):
        assert window.btn_theme_dark.iconSize() == QSize(14, 14)
        assert window.btn_theme_light.iconSize() == QSize(14, 14)
        assert window.btn_tb_settings.iconSize() == QSize(18, 18)
        assert not window.left_workspace.page_tree.source_icon.icon().isNull()
        assert not window.left_workspace.view_icon.icon().isNull()
        capture(window, f"main-{name}.png")
        overlay = window.canvas_tool_overlay
        overlay.focus_button.click()
        assert overlay.focus_button.property("fluentIconName") == "ic_fluent_full_screen_minimize_regular"
        QTimer.singleShot(500, lambda: guard(lambda: capture_focus(name, index)))

    def capture_focus(name, index):
        overlay = window.canvas_tool_overlay
        capture(window, f"main-focus-{name}.png")
        overlay.focus_button.click()
        assert overlay.focus_button.property("fluentIconName") == "ic_fluent_full_screen_maximize_regular"
        checks.append(f"主窗口 {name}：顶栏、画布、数据源、页面标题与专注模式")
        window.open_publication_dialog()
        QTimer.singleShot(6500, lambda: guard(lambda: capture_publication(name, index)))

    def capture_publication(name, index):
        dialog = window.__dict__.get("_publication_dialog")
        assert dialog is not None and dialog.isVisible()
        assert dialog.btn_update.iconSize() == QSize(15, 15)
        assert dialog.btn_export.iconSize() == QSize(15, 15)
        capture(dialog, f"publication-{name}.png")
        dialog.close()
        plugin = window.open_plugin_manager()
        assert plugin is not None and plugin.isVisible()
        QTimer.singleShot(1200, lambda: guard(lambda: capture_plugins(name, index)))

    def capture_plugins(name, index):
        settings = window.settings_dialog
        assert settings is not None and settings.isVisible()
        capture(settings, f"plugins-{name}.png")
        settings.close()
        assert window.open_blender_project_dialog()
        QTimer.singleShot(1200, lambda: guard(lambda: capture_blender(name, index)))

    def capture_blender(name, index):
        dialog = window.__dict__.get("_blender_project_dialog")
        assert dialog is not None and dialog.isVisible()
        assert dialog.title_icon.size() == QSize(32, 32)
        assert not dialog.title_icon.icon().isNull()
        capture(dialog, f"blender-{name}.png")
        dialog.reject()
        checks.append(f"次级窗口 {name}：出版按钮、插件管理与 Blender 标题")
        QTimer.singleShot(300, lambda: guard(lambda: run_theme(index + 1)))

    def finish():
        screens = [
            {
                "name": screen.name(),
                "dpr": screen.devicePixelRatio(),
                "geometry": [
                    screen.geometry().x(),
                    screen.geometry().y(),
                    screen.geometry().width(),
                    screen.geometry().height(),
                ],
            }
            for screen in app.screens()
        ]
        report = {
            "status": "PASS",
            "data": str(args.data),
            "qt_version": QT_VERSION_STR,
            "pyqt_version": PYQT_VERSION_STR,
            "platform": app.platformName(),
            "screens": screens,
            "cross_screen_move": "not_run_single_screen" if len(screens) < 2 else "not_run_manual",
            "checks": checks,
            "captures": captures,
        }
        (args.output_dir / "fluent-icon-window-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        window._set_theme(initial_theme)
        window.close()
        app.quit()

    QTimer.singleShot(1600, lambda: guard(load))
    exit_code = app.exec_()
    if failure is not None:
        print(f"[result] FAIL: {failure}", flush=True)
    else:
        print("[result] PASS", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
