"""真实数据下检查深色 / 浅色主题与主要次级窗口。"""
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def log(message):
    print(message, flush=True)


def main():
    from scripts.validation._common import arguments
    args = arguments(
        "ui-themes",
        dynamic=True,
        default_data=Path(".local/data/WSe2_step.npz"),
    )
    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app
    from bandscope.ui import theme
    from bandscope.updates.update_settings_dialog import UpdateSettingsDialog

    refactored_app.UpdateController = _NullUpdateController
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    initial_theme = theme.current_theme()
    assert initial_theme == "light", f"新环境默认主题应为浅色，实际为 {initial_theme}"
    results = []

    def capture(widget, path):
        widget.raise_()
        widget.activateWindow()
        QApplication.processEvents()
        QTest.qWait(250)
        if not widget.grab().save(str(path)):
            raise AssertionError(f"控件截图失败：{path}")
        if not Path(path).is_file() or Path(path).stat().st_size == 0:
            raise AssertionError(f"截图未生成：{path}")
        log(f"[capture] {path}")

    def guard(callback):
        try:
            callback()
        except Exception:
            traceback.print_exc()
            window.close()
            app.exit(1)

    def step_load():
        if args.size:
            width, height = map(int, args.size.split("x"))
            window.showNormal()
            window.resize(width, height)
        log(f"[data] {args.data}")
        window.load_data(str(args.data))
        assert window.core.raw_data is not None, "真实数据加载失败"
        QTimer.singleShot(12000, lambda: guard(lambda: run_theme(0)))

    def run_theme(index):
        names = ("dark", "light")
        if index >= len(names):
            window._set_theme(initial_theme)
            if args.hold:
                log("[hold] 验收窗口已保留，关闭窗口退出")
            else:
                QTimer.singleShot(300, finish)
            return
        name = names[index]
        window._set_theme(name)
        window.page_container.setCurrentWidget(window.page_render)
        QTimer.singleShot(2200, lambda: guard(lambda: capture_main(index, name)))

    def capture_main(index, name):
        assert theme.current_theme() == name
        actual_background = tuple(round(float(value), 3) for value in window.plotter.background_color[:3])
        expected_background = tuple(round(value, 3) for value in QColor(theme.CANVAS_BG).getRgbF()[:3])
        assert actual_background == expected_background, (actual_background, expected_background)
        assert window.page_render.s_gamma.value() == 50
        assert window.btn_theme_dark.isChecked() == (name == "dark")
        assert window.btn_theme_light.isChecked() == (name == "light")
        assert "palette(highlight)" in window.btn_main_export.styleSheet()
        actual_accent = QApplication.instance().palette().color(QPalette.Highlight).name().upper()
        assert actual_accent == theme.ACCENT, (actual_accent, theme.ACCENT)
        capture(window, args.output_dir / f"main-{name}.png")
        window.plotter.screenshot(str(args.output_dir / f"volume-{name}.png"))
        QTimer.singleShot(200, lambda: guard(check_camera_presets))
        results.append(f"主窗口 {name}：背景、按钮、参数保持")

    def check_camera_presets():
        window._set_camera_view_preset("xy")
        assert window.canvas_tool_overlay.preset_buttons["xy"].isChecked()
        window._set_camera_view_preset("3d")
        assert window.canvas_tool_overlay.preset_buttons["3d"].isChecked()
        window.open_publication_dialog()
        QTimer.singleShot(6500, lambda: guard(capture_publication))

    def capture_publication():
        dialog = window.__dict__.get("_publication_dialog")
        assert dialog is not None and dialog.isVisible(), "出版导出窗口未打开"
        assert dialog.options_scroll is not None
        assert dialog.output_frame is not None
        capture(dialog, args.output_dir / f"publication-{theme.current_theme()}.png")
        dialog.close()
        plugin = window.open_plugin_manager()
        assert plugin is not None, "扩展管理窗口未创建"
        plugin.show()
        QTimer.singleShot(1200, lambda: guard(lambda: capture_plugin(plugin)))

    def capture_plugin(plugin):
        assert plugin.isVisible(), "扩展管理窗口未显示"
        capture(plugin, args.output_dir / f"plugins-{theme.current_theme()}.png")
        plugin.close()
        assert window.open_blender_project_dialog(), "Blender 工程窗口未打开"
        QTimer.singleShot(1200, lambda: guard(capture_blender))

    def capture_blender():
        dialog = window.__dict__.get("_blender_project_dialog")
        assert dialog is not None and dialog.isVisible(), "Blender 工程窗口未显示"
        capture(dialog, args.output_dir / f"blender-{theme.current_theme()}.png")
        dialog.reject()
        settings = UpdateSettingsDialog(window)
        settings.show()
        QTimer.singleShot(600, lambda: guard(lambda: capture_settings(settings)))

    def capture_settings(settings):
        capture(settings, args.output_dir / f"settings-{theme.current_theme()}.png")
        settings.reject()
        settings.deleteLater()
        results.append(f"次级窗口 {theme.current_theme()}：出版、扩展、Blender、更新设置")
        index = 1 if theme.current_theme() == "dark" else 2
        QTimer.singleShot(300, lambda: guard(lambda: run_theme(index)))

    def finish():
        report = args.output_dir / "report.txt"
        report.write_text("\n".join(results) + "\nPASS\n", encoding="utf-8")
        log(f"[report] {report}")
        window.close()
        app.quit()

    QTimer.singleShot(1600, lambda: guard(step_load))
    exit_code = app.exec_()
    log(f"[result] {'PASS' if exit_code == 0 else 'FAIL'} ({exit_code})")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
