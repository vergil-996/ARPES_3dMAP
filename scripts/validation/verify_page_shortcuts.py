# -*- coding: utf-8 -*-
"""翻页快捷键的真实窗口验收：修饰键翻页已移除，剩下的翻页方式仍然可用。

验证点：
1. 光标在左侧视图上，单击 `Shift` / `Ctrl` 不再切换结果页；
2. 光标在右侧控件面板上，单击 `Shift` / `Ctrl` 不再切换控件页；
3. 按住 `Ctrl` 在视图里拖动（原冲突场景）后松开，页面不跳；
4. 数字键 `1`–`4` 仍按光标所在区域翻页（左侧结果页 / 右侧控件页）；
5. `Shift + ←` / `Shift + →` 的十帧步进不受影响（动态数据）。

用法: .venv/Scripts/python.exe scripts/validation/verify_page_shortcuts.py <npz路径> [--dynamic]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from dataclasses import replace

from PyQt5.QtCore import QEvent, QPoint, Qt, QTimer
from PyQt5.QtGui import QCursor, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

NPZ = None
is_dynamic = False
failures = []


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


def park_cursor(widget):
    """把真实光标停到控件中央：快捷键按光标所在区域生效。"""
    QCursor.setPos(widget.mapToGlobal(widget.rect().center()))


def drag_view(window, delta):
    """在 3D 视图里用左键拖动（VTK 交互），模拟 Ctrl + 左键拖动视图。"""
    target = window.plotter.interactor
    origin = QPoint(target.width() // 2, target.height() // 2)
    start = target.mapToGlobal(origin)
    for event in (
        QMouseEvent(QEvent.MouseButtonPress, origin, start, Qt.LeftButton, Qt.LeftButton, Qt.ControlModifier),
        QMouseEvent(QEvent.MouseMove, origin + delta, start + delta, Qt.NoButton, Qt.LeftButton, Qt.ControlModifier),
        QMouseEvent(QEvent.MouseButtonRelease, origin + delta, start + delta, Qt.LeftButton, Qt.NoButton, Qt.ControlModifier),
    ):
        QApplication.sendEvent(target, event)


def main():
    global NPZ, is_dynamic
    from scripts.validation._common import arguments
    args = arguments("page_shortcuts", dynamic=True)
    NPZ, is_dynamic = str(args.data), args.dynamic
    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app
    refactored_app.UpdateController = _NullUpdateController

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    window.raise_()
    window.activateWindow()

    def add_second_page():
        """用薄 E 层的裁剪生成第二个结果页，翻页才有可观察的差异。"""
        selection = window.crop_controller.selection
        bounds = list(selection.bounds)
        bounds[4] = bounds[5] = (bounds[4] + bounds[5]) / 2.0
        window.crop_controller.set_selection(replace(selection, bounds=tuple(bounds)))
        window.on_cut()

    def left_page_ids():
        # 数字切页按页面树的可见行顺序，验收读同一份顺序。
        return window.left_workspace.visible_page_ids()

    def step_loaded():
        check("窗口是活动窗口（快捷键前提）", window._page_keyboard_shortcuts_enabled())
        add_second_page()
        QTimer.singleShot(3000, guarded(step_pages_ready))

    def step_pages_ready():
        check(f"裁剪生成第二个结果页（现有 {len(left_page_ids())} 页）", len(left_page_ids()) == 2)
        QTimer.singleShot(300, guarded(step_modifier_keys))

    def step_modifier_keys():
        # —— 光标在左侧视图：修饰键单击不应翻页 ——
        park_cursor(window.left_workspace)
        left_before = window.left_workspace.current_page_id
        for key in (Qt.Key_Shift, Qt.Key_Control):
            QTest.keyClick(window, key)
        check("光标在左侧视图时 Shift / Ctrl 单击不翻页",
              window.left_workspace.current_page_id == left_before)

        # —— 光标在右侧控件面板：同样不应翻页 ——
        park_cursor(window.page_container)
        control_before = window.page_container.currentIndex()
        for key in (Qt.Key_Shift, Qt.Key_Control):
            QTest.keyClick(window, key)
        check("光标在右侧面板时 Shift / Ctrl 单击不切控件页",
              window.page_container.currentIndex() == control_before)

        # —— 原冲突场景：按住 Ctrl 拖视图后松开 ——
        park_cursor(window.left_workspace)
        QTest.keyPress(window, Qt.Key_Control)
        drag_view(window, QPoint(60, 40))
        QTest.keyRelease(window, Qt.Key_Control)
        check("按住 Ctrl 拖动视图后松开不翻页",
              window.left_workspace.current_page_id == left_before)
        QTimer.singleShot(400, guarded(step_number_keys))

    def step_number_keys():
        page_ids = left_page_ids()
        park_cursor(window.left_workspace)
        QTest.keyClick(window, Qt.Key_1)
        check(f"数字键 1 仍切换到左侧第 1 页 {page_ids[0]}",
              window.left_workspace.current_page_id == page_ids[0])
        QTest.keyClick(window, Qt.Key_2)
        check(f"数字键 2 仍切换到左侧第 2 页 {page_ids[1]}",
              window.left_workspace.current_page_id == page_ids[1])

        park_cursor(window.page_container)
        QTest.keyClick(window, Qt.Key_2)
        check("数字键 2 在右侧面板切到「处理分析」",
              window.page_container.currentIndex() == 1)
        QTest.keyClick(window, Qt.Key_1)
        check("数字键 1 在右侧面板切回「渲染控制」",
              window.page_container.currentIndex() == 0)
        QTimer.singleShot(300, guarded(step_shift_arrows))

    def step_shift_arrows():
        if not is_dynamic:
            print("[SKIP] 静态数据没有时间轴，跳过 Shift+方向键步进检查", flush=True)
            finish()
            return
        slider = window.timeline_bar.slider_time
        park_cursor(window.left_workspace)
        if slider.maximum() <= slider.minimum():
            failures.append("动态数据时间轴范围无效")
            finish()
            return
        slider.setValue(slider.minimum())
        QTest.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
        check(f"Shift + → 仍前进十帧（{slider.value()}）",
              slider.value() == min(slider.minimum() + 10, slider.maximum()))
        QTest.keyClick(window, Qt.Key_Left, Qt.ShiftModifier)
        check(f"Shift + ← 仍后退十帧（{slider.value()}）", slider.value() == slider.minimum())
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
    QTimer.singleShot(60000, lambda: app.exit(2))  # 兜底防挂死

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
