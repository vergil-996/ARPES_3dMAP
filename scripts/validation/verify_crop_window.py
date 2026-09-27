# -*- coding: utf-8 -*-
"""裁剪/裁空范围窗口的真实窗口验收：按钮呼出、可拖动、随开关收起。

验证点：
1. 点顶部栏「裁剪」后，范围窗口直接出现（不再是原先只有右键才能呼出），
   落在按钮正下方，是独立浮窗（Qt.Tool + 无边框）而不是模态弹出层；
2. 拖动标题行整窗移动，落点等于鼠标位移，且不被屏幕边界吃掉；
3. 切到「裁空」窗口留在原地，只换标题与动作按钮；两个开关互斥；
4. 右击画布仍能把窗口移到光标处（原入口保留）；
5. 关闭开关时窗口随之关闭，选区与两个开关状态互不串味；
6. 开关打开期间，页面重新渲染后窗口仍在（窗口跟随开关，而不是一次性弹出）。

用法: .venv/Scripts/python.exe scripts/validation/verify_crop_window.py <npz路径>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QEvent, QPoint, Qt, QTimer
from PyQt5.QtGui import QColor, QMouseEvent, QPainter, QPixmap
from PyQt5.QtWidgets import QApplication

from bandscope.ui import theme

NPZ = None
OUT_DIR = None

failures = []
#: 拖动落点：后面几步都要用它判断“窗口记住用户位置”。
state = {}


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}", flush=True)
    if not condition:
        failures.append(label)


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def drag(popup, origin, delta):
    """按真实鼠标事件序列拖动窗口：按下 → 移动 → 松开。"""
    start = popup.mapToGlobal(origin)
    events = (
        QMouseEvent(QEvent.MouseButtonPress, origin, start, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier),
        QMouseEvent(QEvent.MouseMove, origin + delta, start + delta, Qt.NoButton, Qt.LeftButton, Qt.NoModifier),
        QMouseEvent(QEvent.MouseButtonRelease, origin + delta, start + delta, Qt.LeftButton, Qt.NoButton, Qt.NoModifier),
    )
    for event in events:
        QApplication.sendEvent(popup, event)


def right_click_canvas(window, pos):
    target = window.plotter.interactor
    global_pos = target.mapToGlobal(pos)
    event = QMouseEvent(QEvent.MouseButtonPress, pos, global_pos, Qt.RightButton, Qt.RightButton, Qt.NoModifier)
    QApplication.sendEvent(target, event)


def save_shot(window, popup, name):
    """把主窗口与浮窗按真实相对位置拼成一张图：桌面抓图会被别的窗口挡住。

    浮窗是独立顶层窗口，window.grab() 抓不到它，所以要分别抓、按几何位置合成。
    """
    region = window.frameGeometry().united(popup.frameGeometry())
    ratio = window.devicePixelRatioF()
    canvas = QPixmap(int(region.width() * ratio), int(region.height() * ratio))
    canvas.setDevicePixelRatio(ratio)
    canvas.fill(QColor(theme.BG_1))
    painter = QPainter(canvas)
    for widget in (window, popup):
        origin = widget.frameGeometry().topLeft() - region.topLeft()
        painter.drawPixmap(origin, widget.grab())
    painter.end()
    path = os.path.join(OUT_DIR, name)
    canvas.save(path)
    popup.grab().save(os.path.join(OUT_DIR, name.replace(".png", "_popup.png")))
    print(f"[SHOT] {path}", flush=True)


def main():
    global NPZ, OUT_DIR
    from scripts.validation._common import arguments
    args = arguments("crop_window")
    NPZ, OUT_DIR = str(args.data), str(args.output_dir)
    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app
    refactored_app.UpdateController = _NullUpdateController

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()

    popup = window.crop_controller.popup
    crop_button = window.btn_tb_crop
    erase_button = window.btn_tb_erase
    screen_area = QApplication.primaryScreen().availableGeometry()

    def step_loaded():
        check("数据加载后 3D 视图就绪", (window.current_render_context or {}).get("view") == "3d")
        check("加载后范围窗口默认关闭", not popup.isVisible())

        crop_button.click()
        check("点「裁剪」直接呼出范围窗口", popup.isVisible())
        check("窗口是独立浮窗（Qt.Tool + 无边框）",
              bool(popup.isWindow() and popup.windowFlags() & Qt.Tool
                   and popup.windowFlags() & Qt.FramelessWindowHint))
        check("窗口标题与动作按钮为裁剪",
              popup.title_label.text() == "裁剪范围" and popup.apply_button.text() == "裁剪")
        anchor = crop_button.mapToGlobal(QPoint(0, crop_button.height() + 8))
        check(f"窗口落在按钮下方（窗口 {popup.pos()} / 按钮 {anchor}）",
              popup.y() == anchor.y() and popup.x() < anchor.x())
        check("窗口未被屏幕边界裁掉", screen_area.contains(popup.geometry()))
        save_shot(window, popup, "crop_window_opened.png")
        QTimer.singleShot(400, guarded(step_drag))

    def step_drag():
        start = popup.pos()
        drag(popup, QPoint(40, 30), QPoint(120, 90))
        state["dragged"] = popup.pos()
        check(f"拖动标题行整窗移动 {popup.pos() - start}", popup.pos() == start + QPoint(120, 90))
        check("拖动后窗口仍在屏幕内", screen_area.contains(popup.geometry()))
        save_shot(window, popup, "crop_window_dragged.png")
        QTimer.singleShot(300, guarded(step_erase_mode))

    def step_erase_mode():
        erase_button.click()
        check("两个开关互斥", erase_button.isChecked() and not crop_button.isChecked())
        check("切到裁空后窗口留在原地", popup.isVisible() and popup.pos() == state["dragged"])
        check("窗口换成裁空文案",
              popup.title_label.text() == "裁空范围" and popup.apply_button.text() == "裁空")
        save_shot(window, popup, "crop_window_erase_mode.png")

        right_click_canvas(window, QPoint(200, 200))
        expected = window.plotter.interactor.mapToGlobal(QPoint(200, 200))
        check(f"右击画布把窗口移到光标处（原右键入口保留，窗口 {popup.pos()}）",
              popup.isVisible() and popup.pos() == expected)
        QTimer.singleShot(300, guarded(step_toggle_off))

    def step_toggle_off():
        selection = window.crop_controller.selection
        erase_button.click()
        check("关闭开关时窗口关闭", not popup.isVisible())
        check("关闭开关后模式停用、选区保留",
              not window.crop_controller.enabled and window.crop_controller.selection == selection)

        crop_button.click()
        check(f"再次打开开关回到用户拖到的位置 {state['dragged']}",
              popup.isVisible() and popup.pos() == state["dragged"])

        popup.hide()
        window.global_refresh()
        QTimer.singleShot(2500, guarded(step_after_refresh))

    def step_after_refresh():
        check("开关打开期间重新渲染后窗口仍然回来", popup.isVisible())
        check("选区仍然有效", window.crop_controller.selection is not None)
        save_shot(window, popup, "crop_window_after_refresh.png")
        crop_button.click()
        check("最终关闭开关，窗口关闭", not popup.isVisible())
        finish()

    def finish():
        if failures:
            print(f"[RESULT] {len(failures)} 项失败", flush=True)
            app.exit(1)
        else:
            print("[RESULT] 全部通过", flush=True)
            app.exit(0)

    def guarded(step):
        def run():
            try:
                step()
            except Exception as exc:  # 验收脚本把异常算作失败，不挂死
                failures.append(f"{step.__name__}: {exc!r}")
                print(f"[FAIL] {step.__name__} 抛出异常 {exc!r}", flush=True)
                finish()
        return run

    window.load_data(NPZ)
    QTimer.singleShot(6000, guarded(step_loaded))
    QTimer.singleShot(45000, lambda: app.exit(2))  # 兜底防挂死

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
