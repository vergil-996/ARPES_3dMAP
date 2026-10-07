import os
import sys
from pathlib import Path

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from bandscope.app_metadata import APP_NAME, APP_PUBLISHER, APP_VERSION

import bandscope.ui.theme as theme


APP_FEEDBACK_STYLE = """
QMessageBox {
    background-color: %(BG2)s;
}
QMessageBox QLabel {
    color: %(T1)s;
    font-family: %(FONT)s;
    font-size: 14px;
}
QMessageBox QPushButton {
    background-color: %(BG4)s;
    color: %(T1)s;
    border: 1px solid %(BORD)s;
    border-radius: 4px;
    padding: 5px 15px;
    min-width: 72px;
}
QMessageBox QPushButton:hover {
    border-color: %(BORD_S)s;
}
QMessageBox QPushButton:default {
    background-color: %(ACCENT)s;
    color: %(ACC_ON)s;
    border-color: %(ACCENT)s;
    font-weight: 600;
}
QMessageBox QPushButton:default:hover {
    background-color: %(ACCENT_H)s;
}
QProgressDialog {
    background-color: %(BG2)s;
}
QProgressDialog QLabel {
    color: %(T1)s;
    font-family: %(FONT)s;
    font-size: 14px;
}
QProgressDialog QPushButton {
    background-color: %(BG4)s;
    color: %(T1)s;
    border: 1px solid %(BORD)s;
    border-radius: 4px;
    padding: 5px 15px;
    min-width: 72px;
}
QProgressDialog QPushButton:hover {
    border-color: %(BORD_S)s;
}
/* 进度框只有「取消」一颗按钮，且 Qt 把它设为默认按钮；这里刻意不给
   :default 上主色，否则取消键会变成粉底主操作，主次反而颠倒。 */
QProgressDialog QProgressBar {
    background-color: %(BG1)s;
    border: 1px solid %(BORD)s;
    border-radius: 4px;
    text-align: center;
    color: %(T1)s;
}
QProgressDialog QProgressBar::chunk {
    background-color: %(ACCENT)s;
    border-radius: 3px;
}
QToolTip {
    color: %(T1)s;
    background-color: %(BG2)s;
    border: 1px solid %(ACC_DIM)s;
}
""" % theme.QSS_TOKENS

#: 原生 Qt 容器（表格 / 树 / 滚动区）的深色滚动条；SiUI 自绘滚动条不受影响。
APP_FEEDBACK_STYLE += theme.scrollbar_qss()


def resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", str(Path(__file__).resolve().parents[2]))
    return os.path.join(base_path, relative_path)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_PUBLISHER)
    app.setStyleSheet(APP_FEEDBACK_STYLE)

    # SiliconUI creates font-backed widgets while refactored_app is imported,
    # so import it only after QApplication exists.
    import bandscope.app.refactored_app as refactored_app

    icon_path = resource_path("assets/app.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = refactored_app.My3DAnalyzer()
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))

    window.showMaximized()
    return app.exec_()
