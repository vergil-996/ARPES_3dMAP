# -*- coding: utf-8 -*-
"""探针：观察换帧时 3D level_info 的来源（精确帧数据 vs 预览抽稀）。

用法: .venv/Scripts/python.exe scripts/diagnostics/probe_frame_ranges.py <npz路径>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication


class _NullUpdateController:
    def __init__(self, *a, **k):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **k):
        pass


def log(msg):
    print(msg, flush=True)


def main():
    from scripts.validation._common import arguments
    args = arguments("frame_ranges")
    npz_path = str(args.data)
    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app
    refactored_app.UpdateController = _NullUpdateController
    from bandscope.rendering.render_core import VisualEngine

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    slider = window.timeline_bar.slider_time

    def snap(tag):
        info = window.volume_session.level_info
        bw = None if info is None else (info["data_min"], info["data_max"], info["black_value"], info["white_value"])
        log(f"[probe] {tag}: level_info(min,max,black,white)={bw} "
            f"renders={window.volume_session.render_count} "
            f"data_updates={window.volume_session.data_update_count} "
            f"rebuilds={window.volume_session.rebuild_count} "
            f"shape={window.volume_session.shape} "
            f"last_range={VisualEngine._last_data_range}")

    def step_load():
        window.load_data(npz_path)
        QTimer.singleShot(12000, lambda: (snap("after load"), step_f3()))

    def step_f3():
        slider.setValue(3)
        window.flush_time_slider_refresh()
        QTimer.singleShot(8000, lambda: (snap("frame3"), step_f0()))

    def step_f0():
        slider.setValue(0)
        window.flush_time_slider_refresh()
        QTimer.singleShot(8000, lambda: (snap("frame0 again"), step_f1()))

    def step_f1():
        slider.setValue(1)
        window.flush_time_slider_refresh()
        QTimer.singleShot(8000, lambda: (snap("frame1"), finish()))

    def finish():
        window.close()
        app.quit()

    QTimer.singleShot(2000, step_load)
    code = app.exec_()
    log(f"[probe] done {code}")


if __name__ == "__main__":
    main()
