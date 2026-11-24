# -*- coding: utf-8 -*-
"""主框选工具条与裁剪/裁空范围窗口的真实鼠标验收。

验证点：
1. 真实点击主「框选范围」展开次级工具条，再点击次级「裁剪」呼出独立浮窗；
2. 次级工具条位于主按钮左侧且不超出画布，浮窗落在裁剪按钮下方；
3. 拖动标题行整窗移动，切到「裁空」只换模式且保留浮窗位置；
4. 右击画布仍能把窗口移到光标处；重复点击次级按钮只收起浮窗；
5. 重复点击主按钮收起/再展开工具条时保留选区草稿；
6. 开关打开期间，页面重新渲染后浮窗仍在，最终可分别关闭次级与主模式。

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
from PyQt5.QtTest import QTest
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


def click(button):
    QTest.mouseClick(button, Qt.LeftButton, Qt.NoModifier, button.rect().center())
    QApplication.processEvents()


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
    overlay = window.canvas_tool_overlay
    selection_button = overlay.selection_button
    selection_panel = overlay.selection_panel
    crop_button = window.btn_tb_crop
    erase_button = window.btn_tb_erase
    screen_area = QApplication.primaryScreen().availableGeometry()

    def step_loaded():
        check("数据加载后 3D 视图就绪", (window.current_render_context or {}).get("view") == "3d")
        check("加载后主/次级工具与范围窗口默认收起",
              not selection_button.isChecked() and not selection_panel.isVisible() and not popup.isVisible())

        click(selection_button)
        check("真实点击主框选按钮展开次级工具条",
              selection_button.isChecked() and selection_panel.isVisible()
              and crop_button.isVisible() and erase_button.isVisible())
        check("展开主框选按钮启用交互并建立草稿",
              window.crop_controller.enabled and window.crop_controller.selection is not None)
        parent = overlay.parentWidget()
        anchor = selection_button.mapTo(parent, QPoint(0, 0))
        expected_x = max(0, min(anchor.x() - selection_panel.width() - 8,
                                max(0, parent.width() - selection_panel.width())))
        expected_y = max(0, min(anchor.y() + (selection_button.height() - selection_panel.height()) // 2,
                                max(0, parent.height() - selection_panel.height())))
        check(f"次级工具条位置正确（{selection_panel.pos()} / 预期 {(expected_x, expected_y)}）",
              selection_panel.x() == expected_x and selection_panel.y() == expected_y
              and parent.rect().contains(selection_panel.geometry()))

        click(crop_button)
        check("真实点击次级「裁剪」直接呼出范围窗口", crop_button.isChecked() and popup.isVisible())
        check("点击次级按钮不收起主工具条", selection_button.isChecked() and selection_panel.isVisible())
        check("窗口是独立浮窗（Qt.Tool + 无边框）",
              bool(popup.isWindow() and popup.windowFlags() & Qt.Tool
                   and popup.windowFlags() & Qt.FramelessWindowHint))
        check("窗口标题与动作按钮为裁剪",
              popup.title_label.text() == "裁剪范围" and popup.apply_button.text() == "裁剪")
        popup_anchor = crop_button.mapToGlobal(QPoint(0, crop_button.height() + 8))
        check(f"窗口落在按钮下方（窗口 {popup.pos()} / 按钮 {popup_anchor}）",
              popup.y() == popup_anchor.y() and popup.x() < popup_anchor.x())
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
        click(erase_button)
        check("两个次级开关互斥", erase_button.isChecked() and not crop_button.isChecked())
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
        click(erase_button)
        check("重复点击次级裁空按钮只收起浮窗", not popup.isVisible() and not erase_button.isChecked())
        check("次级按钮收起后主模式与草稿保留",
              window.crop_controller.enabled and selection_button.isChecked()
              and selection_panel.isVisible() and window.crop_controller.selection == selection)

        click(selection_button)
        check("真实点击主按钮收起工具条并停用交互",
              not selection_button.isChecked() and not selection_panel.isVisible()
              and not window.crop_controller.enabled)
        check("主工具条收起后选区草稿保留", window.crop_controller.selection == selection)

        click(selection_button)
        check("再次真实点击主按钮展开并恢复同一草稿",
              selection_button.isChecked() and selection_panel.isVisible()
              and window.crop_controller.enabled and window.crop_controller.selection == selection)
        check("仅展开主工具条不会擅自打开裁剪浮窗", not popup.isVisible())

        click(crop_button)
        check(f"再次打开次级裁剪回到用户拖到的位置 {state['dragged']}",
              crop_button.isChecked() and popup.isVisible() and popup.pos() == state["dragged"])

        popup.hide()
        window.global_refresh()
        QTimer.singleShot(2500, guarded(step_after_refresh))

    def step_after_refresh():
        check("主/次级开关打开期间重新渲染后浮窗仍然回来", popup.isVisible())
        check("重新渲染后选区仍然有效", window.crop_controller.selection is not None)
        save_shot(window, popup, "crop_window_after_refresh.png")
        selection = window.crop_controller.selection
        click(crop_button)
        check("最终重复点击次级裁剪只关闭浮窗",
              not popup.isVisible() and window.crop_controller.enabled and selection_panel.isVisible())
        click(selection_button)
        check("最终点击主按钮关闭工具条且保留草稿",
              not selection_panel.isVisible() and not window.crop_controller.enabled
              and window.crop_controller.selection == selection)
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
