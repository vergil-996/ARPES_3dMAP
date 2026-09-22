# -*- coding: utf-8 -*-
"""一次性验证：动态数据加载并等动画落定后，抓取“处理分析”页截图。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def main():
    app = QApplication(sys.argv)

    import refactored_app
    refactored_app.UpdateController = _NullUpdateController

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()

    def step_load():
        window.load_data(os.path.join(ROOT, "smoke_data", "scan07_dynamic.npz"))

    def step_grab():
        window._select_control_page(2)

    def step_shot():
        grp = window.page_data.grp_t
        print(f"[info] grp_t height={grp.height()} sizeHint={grp.sizeHint().height()} "
              f"min={grp.minimumHeight()} max={grp.maximumHeight()} hidden={grp.isHidden()}",
              flush=True)
        out = os.path.join(ROOT, "smoke_output", "verify_page2_settled.png")
        window.grab().save(out)
        print(f"[info] saved {out}", flush=True)
        app.exit(0)

    QTimer.singleShot(300, step_load)
    QTimer.singleShot(3000, step_grab)
    QTimer.singleShot(5000, step_shot)
    QTimer.singleShot(15000, lambda: app.exit(2))
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
