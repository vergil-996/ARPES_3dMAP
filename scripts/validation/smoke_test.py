# -*- coding: utf-8 -*-
"""真实数据冒烟测试：加载 npz → 渲染 → 控件页切换 → Toast → 截图 → 干净退出。

用法: .venv/Scripts/python.exe scripts/validation/smoke_test.py <npz路径> [--output-dir <目录>] [--dynamic]

注意：全程用 QTimer 链驱动，不在窗口消息调度里手动 processEvents
（否则 Windows 会抛 RPC_E_CANTCALLOUT_ININPUTSYNCCALL 致命错误）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QMessageBox


class _NullUpdateController:
    """冒烟测试不做联网更新检查。"""

    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def log(msg):
    print(msg, flush=True)


def main():
    from scripts.validation._common import arguments
    args = arguments("smoke_test", dynamic=True)
    npz_path = str(args.data)
    out_dir = str(args.output_dir)
    is_dynamic = args.dynamic
    os.makedirs(out_dir, exist_ok=True)

    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app
    refactored_app.UpdateController = _NullUpdateController

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()

    def step_load():
        size_arg = args.size
        if size_arg:
            width, height = map(int, size_arg.split("x"))
            window.showNormal()
            window.resize(width, height)
            window.main_splitter.setSizes([width - 420, 400])
        log(f"[smoke] loading {npz_path}")
        window.load_data(npz_path)
        assert window.core.raw_data is not None, "raw_data 为空，加载失败"
        log(f"[smoke] loaded, shape={window.core.raw_data.shape}")
        QTimer.singleShot(12000, step_after_render)

    def step_after_render():
        log(f"[smoke] render status: {window.render_status_label.text()}")
        p3d = os.path.join(out_dir, "smoke_3d.png")
        window.plotter.screenshot(p3d)
        log(f"[smoke] 3d screenshot -> {p3d}")
        if is_dynamic:
            slider = window.timeline_bar.slider_time
            log(f"[smoke] time slider range: {slider.minimum()}..{slider.maximum()}")
            assert slider.maximum() > slider.minimum(), "动态数据时间轴范围无效"
            slider.setValue(slider.maximum() // 2)
            QTimer.singleShot(4000, step_pages)
        else:
            QTimer.singleShot(500, step_pages)

    def step_pages(page_index=0):
        # 右侧控件页现在只有「渲染控制」「处理分析」两页。
        if page_index < 2:
            window._select_control_page(page_index)
            def capture_page():
                ppage = os.path.join(out_dir, f"smoke_page{page_index}.png")
                window.grab().save(ppage)
                log(f"[smoke] page {page_index} grab -> {ppage}")
                QTimer.singleShot(100, lambda: step_pages(page_index + 1))
            QTimer.singleShot(300, capture_page)
            return
        window._select_control_page(0)
        log("[smoke] control pages switched")
        window._show_message("冒烟测试", "轻提示应显示为 Toast 而非弹窗", QMessageBox.Information)
        window.toast_manager.show("操作完成反馈", level="success", title="成功样式")
        QTimer.singleShot(600, step_grab)

    def step_grab():
        pwin = os.path.join(out_dir, "smoke_window.png")
        window.grab().save(pwin)
        log(f"[smoke] window grab -> {pwin}")
        if args.hold:
            log("[smoke] review window ready; close it to finish")
        else:
            QTimer.singleShot(800, finish)

    def finish():
        window.close()
        app.quit()

    QTimer.singleShot(2000, step_load)
    exit_code = app.exec_()
    log(f"[smoke] PASS (app exit code {exit_code})")


if __name__ == "__main__":
    main()
