# -*- coding: utf-8 -*-
"""排查：3D 主画布改轴标题后，画布上是否真的换了文字。

单次真实窗口运行，尽量按用户路径走：右键菜单 → 三轴弹窗 → 确定；随后依次触发
各种「刷新」（整帧重绘、相机旋转、切页往返、切帧、坐标轴开关），每一步同时记录
会话状态解析结果与真实 CubeAxesActor 上的文字，用来区分「状态没存住」与
「状态存住了但渲染没应用」。

用法: .venv/Scripts/python.exe scripts/diagnostics/repro_3d_axis_titles.py <npz路径>
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QEvent, QPoint, Qt, QTimer
from PyQt5.QtGui import QContextMenuEvent
from PyQt5.QtWidgets import QApplication

from tests.support.environment import isolate_runtime

NEW_TITLES = {"X": "AXIS-X", "Y": "AXIS-Y", "E": "Binding energy (eV)"}
NEW_TITLES_2 = {"X": "Kx (Å⁻¹)", "Y": "Ky (Å⁻¹)", "E": "E - EF (eV)"}
EXPECTED = tuple(NEW_TITLES[axis] for axis in ("X", "Y", "E"))
EXPECTED_2 = tuple(NEW_TITLES_2[axis] for axis in ("X", "Y", "E"))
state = {}
failures = []


_START = time.time()


def log(message):
    print(f"[{time.time() - _START:6.1f}s] {message}", flush=True)


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}", flush=True)
    if not condition:
        failures.append(label)


def save_shot(name):
    """3D 场景是原生 OpenGL 表面，只能让 VTK 自己截图。"""
    window = state.get("window")
    if window is None:
        return
    path = os.path.join(ROOT, ".local", "outputs", name)
    try:
        window.plotter.screenshot(path)
        log(f"[SHOT] {path}")
    except Exception as exc:
        log(f"[WARN] 截图失败 {exc!r}")


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


class _NullToast:
    def __init__(self, *args, **kwargs):
        pass

    def show(self, *args, **kwargs):
        pass

    def clear(self):
        pass


def right_click(widget, pos):
    """真实 ContextMenu 事件：plotter 是 Qt.CustomContextMenu，靠它发 customContextMenuRequested。"""
    event = QContextMenuEvent(
        QContextMenuEvent.Mouse, pos, widget.mapToGlobal(pos)
    )
    QApplication.sendEvent(widget, event)


def cube_axes_actor(window):
    for actor in window.plotter.renderer.actors.values():
        if type(actor).__name__ == "CubeAxesActor":
            return actor
    return None


def cube_axes_titles(window):
    actor = cube_axes_actor(window)
    if actor is None:
        return None
    return (str(actor.GetXTitle()), str(actor.GetYTitle()), str(actor.GetZTitle()))


def answer_dialog(texts):
    """弹窗要等它真出来（菜单条目触发后才发现），轮询后再填字接受。"""

    def act(remaining):
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            if remaining > 0:
                QTimer.singleShot(100, lambda: act(remaining - 1))
            else:
                failures.append("没有找到模态弹窗")
            return
        for key, value in texts.items():
            dialog.edits[key].setText(value)
        dialog.accept()

    QTimer.singleShot(200, lambda: act(40))


def trigger_menu_action(title):
    """等菜单弹出来后点掉指定条目（exec_ 阻塞，只能在定时器里做）。"""

    def act(remaining):
        menu = QApplication.activePopupWidget()
        if menu is None and remaining > 0:
            QTimer.singleShot(120, lambda: act(remaining - 1))
            return
        if menu is None:
            failures.append("右键菜单没有弹出")
            return
        state["menu_entries"] = [action.text() for action in menu.actions()]
        log(f"[menu] {state['menu_entries']}")
        for action in menu.actions():
            if action.text() == title:
                action.trigger()
                menu.close()
                return
        failures.append(f"菜单里没有 {title}")
        menu.close()

    QTimer.singleShot(150, lambda: act(20))


def main():
    npz = sys.argv[1]
    isolate_runtime()

    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app

    refactored_app.UpdateController = _NullUpdateController
    refactored_app.ToastManager = _NullToast

    window = refactored_app.My3DAnalyzer()
    state["window"] = window
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    window.resize(1500, 950)
    window.timeline_bar.switch_axes.setChecked(True)

    steps = []
    step_names = []

    def step(name):
        def register(func):
            steps.append(func)
            step_names.append(name)
            return func

        return register

    def report(tag):
        spec = window.left_workspace.current_spec()
        key = window._three_d_axis_title_key(spec)
        context = window.current_render_context or {}
        log(
            f"[{tag}] page={getattr(spec, 'page_id', None)!r} view={context.get('view')!r} "
            f"axes_switch={window.timeline_bar.switch_axes.isChecked()} "
            f"state_texts={window.axis_title_state.texts(key) if key else None!r}"
        )
        resolved = window._three_d_axis_titles(spec)
        drawn = cube_axes_titles(window)
        log(f"[{tag}] resolved={resolved!r} drawn={drawn!r}")
        return resolved, drawn

    # ---------------------------------------------------------------- 步骤
    @step("等待首帧")
    def ready(attempt=0):
        report("ready")
        check("主页是 3D 视图", (window.current_render_context or {}).get("view") == "3d")
        if cube_axes_actor(window) is None:
            # 坐标轴开关是持久化设置：页面恢复会覆盖启动时的取值，这里再打开一次。
            if attempt < 6:
                if not window.timeline_bar.switch_axes.isChecked():
                    window.timeline_bar.switch_axes.setChecked(True)
                else:
                    window.global_refresh()
                QTimer.singleShot(3500, lambda: ready(attempt + 1))
                return
            check("默认轴名已绘制", False)
            QTimer.singleShot(200, run_next)
            return
        check(
            "默认轴名已绘制",
            cube_axes_titles(window) == ("Kx", "Ky", "E (eV)"),
        )
        QTimer.singleShot(200, run_next)

    @step("右键菜单改名")
    def rename_via_menu():
        answer_dialog(NEW_TITLES)
        trigger_menu_action("轴标题…")
        right_click(window.plotter, QPoint(400, 300))
        QTimer.singleShot(4000, run_next)

    @step("检查改名结果")
    def after_menu_rename():
        resolved, drawn = report("after-menu-rename")
        check(f"状态里存住了三条新轴名 {resolved}", resolved == EXPECTED)
        check(f"画布上的三条文字都换了 {drawn}", drawn == EXPECTED)
        save_shot("repro_3d_titles_renamed.png")
        QTimer.singleShot(150, run_next)

    @step("裁剪模式下改名")
    def rename_in_crop_mode():
        window.btn_tb_crop.click()
        log("[crop] 裁剪模式已打开，先探一次普通刷新")
        window.global_refresh()
        log("[crop] 普通刷新返回")
        report("after-crop-plain-refresh")
        answer_dialog(NEW_TITLES_2)
        trigger_menu_action("轴标题…")
        right_click(window.plotter, QPoint(500, 400))
        log("[crop] 菜单/弹窗返回")
        QTimer.singleShot(4000, run_next)

    @step("检查裁剪模式改名")
    def after_crop_rename():
        resolved, drawn = report("after-crop-rename")
        check(f"裁剪模式下改的名也存住了 {resolved}", resolved == EXPECTED_2)
        check(f"裁剪模式下画布文字也换了 {drawn}", drawn == EXPECTED_2)
        save_shot("repro_3d_titles_crop_renamed.png")
        window.btn_tb_crop.click()
        QTimer.singleShot(3000, run_next)

    @step("整帧重绘")
    def global_refresh():
        window.global_refresh()
        QTimer.singleShot(5000, run_next)

    @step("检查整帧重绘")
    def after_refresh():
        _, drawn = report("after-global-refresh")
        check(f"整帧刷新后轴名保留 {drawn}",
              drawn == EXPECTED_2)
        window.plotter.camera_position = [
            (420.0, 380.0, 640.0),
            (100.0, 100.0, 100.0),
            (0.0, 0.0, 1.0),
        ]
        window.plotter.render()
        QTimer.singleShot(1200, run_next)

    @step("检查旋转后")
    def after_rotate():
        _, drawn = report("after-rotate")
        check(f"旋转相机后轴名保留 {drawn}",
              drawn == EXPECTED_2)
        window.left_workspace.activate_page(window.left_workspace.home_page_id)
        window.global_refresh()
        QTimer.singleShot(5000, run_next)

    @step("检查切页往返")
    def after_page_roundtrip():
        _, drawn = report("after-page-roundtrip")
        check(f"切页往返后轴名保留 {drawn}",
              drawn == EXPECTED_2)
        window.on_image_time_changed(3)
        QTimer.singleShot(6000, run_next)

    @step("检查切帧")
    def after_frame():
        _, drawn = report("after-frame")
        check(f"切帧后轴名保留 {drawn}",
              drawn == EXPECTED_2)
        window.timeline_bar.switch_axes.setChecked(False)
        QTimer.singleShot(3000, run_next)

    @step("关轴再开")
    def toggle_axes_off():
        log(f"[axes-off] actor={cube_axes_actor(window)}")
        window.timeline_bar.switch_axes.setChecked(True)
        QTimer.singleShot(4000, run_next)

    @step("检查关轴再开")
    def after_axes_back():
        _, drawn = report("after-axes-back")
        check(f"坐标轴重新打开后轴名保留 {drawn}",
              drawn == EXPECTED_2)
        QTimer.singleShot(100, finish)

    def run_next():
        if not steps:
            finish()
            return
        name = step_names.pop(0)
        func = steps.pop(0)
        log(f"[step] {name}")
        try:
            func()
        except Exception as exc:  # 排查脚本不挂死
            failures.append(f"{name}: {exc!r}")
            log(f"[FAIL] {name} 抛出异常 {exc!r}")
            finish()

    def finish():
        if state.get("finished"):
            return
        state["finished"] = True
        try:
            window.plotter.screenshot(
                os.path.join(ROOT, ".local", "outputs", "repro_3d_axis_titles.png")
            )
        except Exception as exc:
            log(f"[WARN] 截图失败 {exc!r}")
        if failures:
            print(f"[RESULT] {len(failures)} 项失败", flush=True)
            for item in failures:
                print(f"  - {item}", flush=True)
            app.exit(1)
        else:
            print("[RESULT] 全部通过", flush=True)
            app.exit(0)

    def wait_for_first_frame(attempt=0):
        """大文件加载慢：等到 3D 首帧和坐标轴都画出来再开始。"""
        ready = (
            (window.current_render_context or {}).get("view") == "3d"
            and cube_axes_actor(window) is not None
        )
        if ready or attempt >= 45:
            QTimer.singleShot(500, run_next)
            return
        QTimer.singleShot(2000, lambda: wait_for_first_frame(attempt + 1))

    log(f"[load] {npz}")
    window.load_data(npz)
    QTimer.singleShot(2000, wait_for_first_frame)
    QTimer.singleShot(420000, lambda: app.exit(2))

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
