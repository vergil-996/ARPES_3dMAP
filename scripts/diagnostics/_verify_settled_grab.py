# -*- coding: utf-8 -*-
"""一次性验证：动态数据加载并等动画落定后，抓取外观页与时间区间底条。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def main():
    from scripts.validation._common import arguments
    args = arguments("settled_grab")
    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app
    refactored_app.UpdateController = _NullUpdateController

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()

    def step_load():
        window.load_data(str(args.data))

    def step_grab():
        window.page_container.setCurrentWidget(window.page_render)

    def step_shot():
        group = window.timeline_bar.timeline_group
        print(f"[info] time interval height={group.height()} hidden={group.isHidden()}",
              flush=True)
        out = str(args.output_dir / "verify_appearance_settled.png")
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
