import os
import sys

from qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from app_metadata import APP_NAME, APP_PUBLISHER, APP_VERSION

import theme


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
    background-color: %(ACCENT)s;
    color: %(ACC_ON)s;
    font-weight: 600;
    border-radius: 4px;
    padding: 5px 15px;
    min-width: 72px;
}
QMessageBox QPushButton:hover {
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
    background-color: %(ACCENT)s;
    color: %(ACC_ON)s;
    font-weight: 600;
    border-radius: 4px;
    padding: 5px 15px;
    min-width: 72px;
}
QProgressDialog QPushButton:hover {
    background-color: %(ACCENT_H)s;
}
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


def resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, relative_path)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_PUBLISHER)
    app.setStyleSheet(APP_FEEDBACK_STYLE)

    # SiliconUI creates font-backed widgets while refactored_app is imported,
    # so import it only after QApplication exists.
    import refactored_app

    icon_path = resource_path("app.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = refactored_app.My3DAnalyzer()
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))

    window.showMaximized()
    sys.exit(app.exec_())
