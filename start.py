import os
import sys

from qt_bootstrap import configure_qt_plugin_path

configure_qt_plugin_path()

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

import refactored_app


APP_FEEDBACK_STYLE = """
QMessageBox {
    background-color: #2A2A3A;
}
QMessageBox QLabel {
    color: #FFFFFF;
    font-family: "Segoe UI";
    font-size: 14px;
}
QMessageBox QPushButton {
    background-color: #E81123;
    color: #FFFFFF;
    border-radius: 4px;
    padding: 5px 15px;
    min-width: 72px;
}
QMessageBox QPushButton:hover {
    background-color: #F33A4A;
}
QToolTip {
    color: #FFFFFF;
    background-color: #2A2A3A;
    border: 1px solid #FF69B4;
}
"""


def resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, relative_path)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_FEEDBACK_STYLE)

    icon_path = resource_path("app.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = refactored_app.My3DAnalyzer()
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))

    window.showMaximized()
    sys.exit(app.exec_())
