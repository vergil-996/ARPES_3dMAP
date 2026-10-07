# -*- coding: utf-8 -*-
"""美化审计：截取插件管理 / 设置弹窗 / 导出对话框的实际观感。

只读界面，不做任何安装或修改操作。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QApplication


def log(msg):
    print(msg, flush=True)


def main():
    from scripts.validation._common import arguments
    args = arguments("beauty_audit_dialogs", data=False)
    out_dir = str(args.output_dir)

    app = QApplication(sys.argv)

    # 与生产启动保持一致：全局消息框/滚动条样式来自 startup.APP_FEEDBACK_STYLE。
    from bandscope.app.startup import APP_FEEDBACK_STYLE
    app.setStyleSheet(APP_FEEDBACK_STYLE)

    import bandscope.app.refactored_app as refactored_app

    class _NullUpdateController:
        def __init__(self, *a, **k):
            pass

        def check_automatically(self):
            pass

        def shutdown(self, **kwargs):
            pass

    refactored_app.UpdateController = _NullUpdateController
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    window.resize(1550, 950)

    dialogs = []

    def step_plugin_dialog():
        log("[audit] open plugin manager dialog")
        window.open_plugin_manager()
        dlg = getattr(window, "_plugin_dialog", None)
        if dlg is None:
            # 找顶层窗口里的插件管理
            from PyQt5.QtWidgets import QApplication as _q
            for w in _q.topLevelWidgets():
                if w.windowTitle() == "插件管理" and w.isVisible():
                    dlg = w
                    break
        dialogs.append(dlg)
        if dlg is not None:
            dlg.raise_()
            dlg.activateWindow()
        QTimer.singleShot(800, lambda: grab(dlg, "dialog_plugin_installed", step_catalog_tab))

    def step_catalog_tab():
        dlg = dialogs[-1]
        if dlg is not None and hasattr(dlg, "tabs"):
            dlg.tabs.setCurrentIndex(1)
        QTimer.singleShot(600, lambda: grab(dialogs[-1], "dialog_plugin_catalog", step_denoise_popup))

    def step_denoise_popup():
        for d in dialogs:
            if d is not None:
                d.close()
        log("[audit] open denoise settings popup")
        try:
            popup = window.denoise_popup
            from PyQt5.QtCore import QPoint
            popup.show_at(window.mapToGlobal(QPoint(200, 120)))
            dialogs.append(popup)
        except Exception as exc:
            log(f"[audit] denoise popup failed: {exc}")
            dialogs.append(None)
        QTimer.singleShot(800, lambda: grab(dialogs[-1], "popup_denoise", step_update_box))

    def step_update_box():
        for d in dialogs:
            if d is not None:
                # 只关不删：插件管理对话框的后台线程可能还在跑，deleteLater
                # 会让 QThread 在运行中被析构，直接崩进程。脚本很快退出，
                # 交给进程收尾即可。
                d.close()
        log("[audit] open a QMessageBox sample (update prompt style)")
        from PyQt5.QtWidgets import QMessageBox
        box = QMessageBox(window)
        box.setWindowTitle("BandScope 更新")
        box.setIcon(QMessageBox.Information)
        box.setText("发现 BandScope v9.9.9\n当前版本 v1.12.3")
        box.setInformativeText("发布说明：\n- 示例条目\n\n是否下载并安装？")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.button(QMessageBox.Yes).setText("下载更新")
        box.button(QMessageBox.No).setText("稍后提醒")
        box.setDefaultButton(QMessageBox.Yes)
        box.show()
        dialogs.append(box)
        QTimer.singleShot(800, lambda: grab(box, "dialog_messagebox", step_menu_and_scroll))

    def step_menu_and_scroll():
        log("[audit] context menu + native scrollbar demo")
        from PyQt5.QtCore import QPoint
        from PyQt5.QtWidgets import QMenu, QTableWidget, QTableWidgetItem
        import bandscope.ui.theme as theme

        menu = QMenu(window)
        menu.setStyleSheet(theme.context_menu_qss())
        menu.addAction("重命名")
        menu.addAction("恢复自动名称")
        menu.addSeparator()
        disabled = menu.addAction("删除页面（禁用态）")
        disabled.setEnabled(False)
        menu.popup(window.mapToGlobal(QPoint(360, 240)))
        dialogs.append(menu)

        table = QTableWidget(30, 3, window)
        table.setHorizontalHeaderLabels(["名称", "版本", "状态"])
        for row in range(30):
            table.setItem(row, 0, QTableWidgetItem(f"示例插件 {row + 1}"))
            table.setItem(row, 1, QTableWidgetItem("1.0.0"))
            table.setItem(row, 2, QTableWidgetItem("运行中"))
        table.setStyleSheet(
            f"QTableWidget {{ color: {theme.TEXT_1}; background-color: {theme.BG_2};"
            f" gridline-color: {theme.BORDER_HEX}; border: 1px solid {theme.BORDER_HEX};"
            f" border-radius: 6px; }}"
            f"QTableWidget::item:selected {{ background-color: {theme.ACCENT_SOFT};"
            f" color: {theme.TEXT_1}; }}"
            f"QHeaderView::section {{ color: {theme.TEXT_2}; background-color: {theme.BG_3};"
            f" border: none; padding: 6px; }}"
        )
        table.setWindowFlags(Qt.Window)
        table.resize(420, 300)
        table.move(window.mapToGlobal(QPoint(760, 240)))
        table.selectRow(3)
        table.show()
        dialogs.append(table)

        def grab_both():
            menu.grab().save(os.path.join(out_dir, "context_menu.png"))
            table.grab().save(os.path.join(out_dir, "table_scrollbar.png"))
            log("[audit] context_menu.png / table_scrollbar.png saved")
            QTimer.singleShot(200, finish)

        QTimer.singleShot(700, grab_both)

    def grab(widget, name, next_step):
        if widget is not None:
            path = os.path.join(out_dir, f"{name}.png")
            widget.grab().save(path)
            log(f"[audit] grab -> {path}")
        else:
            log(f"[audit] {name}: widget missing")
        QTimer.singleShot(200, next_step)

    def finish():
        for d in dialogs:
            if d is not None:
                d.close()
        window.close()
        app.quit()

    QTimer.singleShot(1500, step_plugin_dialog)
    exit_code = app.exec_()
    log(f"[audit] DONE (exit {exit_code})")


if __name__ == "__main__":
    main()
