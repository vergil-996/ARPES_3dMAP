# -*- coding: utf-8 -*-
"""复现「先裁剪、后裁空」闪退：真实窗口 + 真实鼠标拖动。

对照实验（同一份数据，两个进程分别跑）：
- ``--mode default``：不拖动，直接用默认（全范围）裁剪框「裁剪」再「裁空」。
- ``--mode drag``：先在 3D 交互盒的 +E 面上按下并向盒内拖动，把裁剪框拖小，
  再「裁剪」；然后切到「裁空」，同样真实拖动后提交。

脚本不吞异常：Qt 槽里抛出的异常会记录完整 traceback（PyQt 默认会直接终止
进程，即用户看到的闪退），硬崩溃由 faulthandler 记录 C 栈。两种都写日志。

用法: .venv/Scripts/python.exe scripts/diagnostics/repro_crop_erase.py \
          .local/data/NiHITP_calibrated_2.npz --mode drag
"""
import argparse
import faulthandler
import os
import sys
import time
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--mode", choices=("default", "drag"), default="drag")
_pre.add_argument("--page", choices=("home", "axis2d"), default="home",
                  help="home=3D 原始视图；axis2d=Z 轴积分得到的 kx–ky 二维页")
_pre.add_argument("--axis", type=int, default=2, help="拖动哪个轴的盒面（0=X,1=Y,2=E）")
_pre.add_argument("--keep", choices=("upper", "lower"), default="upper",
                  help="裁剪后保留哪一段：upper 让结果 data_bounds 下界非零")
_pre.add_argument("--shrink", type=float, default=0.25, help="向盒内拖动的比例")
_pre.add_argument("--settle-ms", type=int, default=1200)
known, remaining = _pre.parse_known_args()
sys.argv = [sys.argv[0]] + remaining

from scripts.validation._common import arguments  # noqa: E402

args = arguments("crop_erase")
MODE, PAGE = known.mode, known.page
AXIS, SHRINK, SETTLE = known.axis, known.shrink, known.settle_ms / 1000.0
KEEP = known.keep
LOG_PATH = REPO_ROOT + f"/.local/logs/crop_erase_{PAGE}_{MODE}.log"
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
_log_file = open(LOG_PATH, "w", encoding="utf-8", buffering=1)
faulthandler.enable(_log_file)

from PyQt5.QtCore import QEvent, QPoint, Qt, QTimer  # noqa: E402
from PyQt5.QtGui import QMouseEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

import bandscope.ui.theme as theme  # noqa: E402

FAILURES = []
NOTES = []
CRASHES = []


def log(message):
    print(message, flush=True)
    _log_file.write(message + "\n")


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    log(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


# ----------------------------------------------------------------------------
# 异常捕获：Qt 槽里的未处理异常本来会让 PyQt 直接终止进程（闪退），
# 这里记录 traceback 后继续，便于看清整条流程。
# ----------------------------------------------------------------------------
def _hook(exc_type, exc_value, exc_tb):
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    CRASHES.append(text)
    log("[EXCEPTION] 未处理异常（PyQt 默认行为是直接退出）:")
    log(text)


sys.excepthook = _hook


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def send_mouse(widget, kind, pos, button, buttons):
    global_pos = widget.mapToGlobal(pos)
    event = QMouseEvent(kind, pos, global_pos, button, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, event)


def world_to_widget(window, point):
    """世界（渲染）坐标 -> 交互窗口控件坐标（考虑 devicePixelRatio 与 Y 翻转）。"""
    coordinate = vtk_coordinate()
    coordinate.SetCoordinateSystemToWorld()
    coordinate.SetValue(float(point[0]), float(point[1]), float(point[2]))
    renderer = window.plotter.renderer
    display = coordinate.GetComputedDisplayValue(renderer)
    widget = window.plotter.interactor
    ratio = widget.devicePixelRatioF() or 1.0
    width, height = window.plotter.render_window.GetSize()
    x = display[0] / ratio
    y = (height - display[1]) / ratio
    return QPoint(round(x), round(y))


def vtk_coordinate():
    import vtk

    return vtk.vtkCoordinate()


def box_face_targets(window, axis, shrink, face):
    """返回盒面上真实拖动的起止屏幕点；宽高为零时返回 None。

    ``face="upper"`` 拖动 +轴 面内收（保留下段，上界变小）；
    ``face="lower"`` 拖动 -轴 面内收（保留上段，下界变大，data_bounds 下界非零）。
    """
    bounds = window._get_render_bounds_for_box()
    if bounds is None:
        return None
    extent = float(bounds[2 * axis + 1]) - float(bounds[2 * axis])
    if extent <= 0:
        return None
    center = [(float(bounds[2 * i]) + float(bounds[2 * i + 1])) / 2.0 for i in range(3)]
    start_world = list(center)
    start_world[axis] = float(bounds[2 * axis + (0 if face == "lower" else 1)])
    end_world = list(start_world)
    step = extent * shrink * (1.0 if face == "lower" else -1.0)
    end_world[axis] = start_world[axis] + step
    return world_to_widget(window, start_world), world_to_widget(window, end_world)


def canvas_display_point(canvas, axes, fraction):
    """坐标轴相对位置 -> Qt 画布控件坐标（处理 mpl 显示空间与控件像素的换算）。"""
    x_display, y_display = axes.transAxes.transform(fraction)
    width_display, height_display = canvas.get_width_height()
    x = x_display * canvas.width() / width_display
    y = (height_display - y_display) * canvas.height() / height_display
    return QPoint(round(x), round(y))


def perform_canvas_drag(window, start_fraction, end_fraction, done):
    """二维画布上的真实矩形框选：按下 -> 多步移动 -> 松开。"""
    canvas, axes = window.canvas_2d, window.ax_2d
    start = canvas_display_point(canvas, axes, start_fraction)
    end = canvas_display_point(canvas, axes, end_fraction)
    log(f"[DRAG] 画布上从 {start.x()},{start.y()} 拖到 {end.x()},{end.y()}")

    send_mouse(canvas, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
    steps = 8

    def move(index):
        if index > steps:
            send_mouse(canvas, QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)
            QTimer.singleShot(int(SETTLE * 1000), done)
            return
        fraction = index / steps
        point = QPoint(
            round(start.x() + (end.x() - start.x()) * fraction),
            round(start.y() + (end.y() - start.y()) * fraction),
        )
        send_mouse(canvas, QEvent.MouseMove, point, Qt.NoButton, Qt.LeftButton)
        QTimer.singleShot(25, lambda: move(index + 1))

    QTimer.singleShot(25, lambda: move(1))


def perform_drag(window, axis, shrink, done, face=None):
    """真实鼠标拖动：按下 -> 多步移动 -> 松开，事件走 Qt 到 VTK 的完整链路。"""
    face = face or ("lower" if KEEP == "upper" else "upper")
    targets = box_face_targets(window, axis, shrink, face)
    if targets is None:
        NOTES.append("交互盒范围不可拖动（跨度为 0 或没有盒子）")
        done()
        return
    start, end = targets
    widget = window.plotter.interactor
    log(f"[DRAG] 轴 {'XYZ'[axis]} {face} 面，屏上从 {start.x()},{start.y()} 拖到 {end.x()},{end.y()}")

    send_mouse(widget, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
    steps = 8

    def move(index):
        if index > steps:
            send_mouse(widget, QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)
            QTimer.singleShot(int(SETTLE * 1000), done)
            return
        fraction = index / steps
        point = QPoint(
            round(start.x() + (end.x() - start.x()) * fraction),
            round(start.y() + (end.y() - start.y()) * fraction),
        )
        send_mouse(widget, QEvent.MouseMove, point, Qt.NoButton, Qt.LeftButton)
        QTimer.singleShot(25, lambda: move(index + 1))

    QTimer.singleShot(25, lambda: move(1))


# ----------------------------------------------------------------------------
# 步骤机
# ----------------------------------------------------------------------------
WINDOW = None
STEPS = []
state = {"index": 0, "busy": False, "started": None, "page": None}


def step(name, ready=None, timeout=90.0):
    def decorate(function):
        STEPS.append({"name": name, "ready": ready or (lambda: True),
                      "fn": function, "timeout": timeout})
        return function

    return decorate


def render_ready():
    return bool(WINDOW is not None and WINDOW.core.raw_data is not None
                and WINDOW.current_render_context is not None
                and WINDOW._render_exact_ready)


def page_id():
    spec = WINDOW.left_workspace.current_spec()
    return None if spec is None else spec.page_id


def expected_view():
    return "2d" if PAGE == "axis2d" else "3d"


def crop_mode_ready():
    if not render_ready():
        return False
    context = WINDOW.current_render_context or {}
    return (context.get("view") == expected_view()
            and WINDOW.left_display_stack.currentIndex() == (1 if PAGE == "axis2d" else 0))


def drag_and_report(label):
    """真实拖动当前裁剪框（3D 用交互盒面，2D 用画布矩形框选）并核对选区变化。"""
    app = QApplication.instance()
    before = WINDOW.crop_controller.selection.bounds
    finished = {"done": False}

    def after():
        finished["done"] = True
        describe_context(f"{label}后")
        now = WINDOW.crop_controller.selection.bounds
        check(f"{label}改变了裁剪框 {before} -> {now}", before != now)

    def done():
        state["busy"] = False
        after()

    state["busy"] = True
    if WINDOW.left_display_stack.currentIndex() == 1:
        perform_canvas_drag(WINDOW, (0.12, 0.12), (0.78, 0.72), done)
    else:
        perform_drag(WINDOW, AXIS, SHRINK, done)
    while not finished["done"]:
        app.processEvents()
        time.sleep(0.01)


def save_shot(name):
    """窗口截图 + VTK 自身截图：Qt 的 grab 抓不到原生 GL 区域，会是全黑。"""
    out_dir = os.path.join(REPO_ROOT, ".local", "outputs", "crop_erase")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{PAGE}-{MODE}-{name}.png")
    WINDOW.grab().save(path)
    log(f"[SHOT] {path}")
    try:
        gl_path = os.path.join(out_dir, f"{PAGE}-{MODE}-{name}-gl.png")
        WINDOW.plotter.screenshot(gl_path)
        log(f"[SHOT] {gl_path}")
    except Exception as exc:
        log(f"[SHOT] VTK 截图失败: {exc!r}")


def volume_world_bounds():
    """当前 3D 体积在渲染坐标里的包围盒；用于核对裁剪/裁空是否改变几何。"""
    session = WINDOW.__dict__.get("volume_session")
    volume = getattr(session, "volume", None)
    if volume is None:
        return None
    return tuple(round(float(value), 6) for value in volume.GetBounds())


def describe_context(label):
    context = WINDOW.current_render_context or {}
    data = context.get("data")
    shape = getattr(data, "shape", None)
    log(f"[CTX] {label}: view={context.get('view')} crop_empty={context.get('crop_empty')} "
        f"shape={shape} data_bounds={context.get('data_bounds')} full_shape={context.get('full_shape')}")
    selection = WINDOW.crop_controller.selection
    log(f"[SEL] {label}: {None if selection is None else selection.bounds} "
        f"op={None if selection is None else selection.operation} view={None if selection is None else selection.view}")
    log(f"[BOX] {label}: logical={WINDOW.precise_logical_bounds} "
        f"render={WINDOW._get_render_bounds_for_box()}")


def pump():
    if state["busy"]:
        return
    if state["index"] >= len(STEPS):
        finish()
        return
    current = STEPS[state["index"]]
    if state["started"] is None:
        state["started"] = time.monotonic()
    if time.monotonic() - state["started"] > current["timeout"]:
        FAILURES.append(f"{current['name']} 等待超时")
        log(f"[FAIL] {current['name']} 等待超时，跳过")
        state["index"] += 1
        state["started"] = None
        return
    try:
        ready = current["ready"]()
    except Exception:
        log("[EXCEPTION] 前置判断出错:\n" + traceback.format_exc())
        ready = False
    if not ready:
        return
    state["index"] += 1
    state["started"] = None
    log(f"=== STEP {current['name']}")
    try:
        current["fn"]()
    except Exception:
        CRASHES.append(traceback.format_exc())
        log("[EXCEPTION] 步骤 %s 抛出:\n%s" % (current["name"], traceback.format_exc()))


def deferred(function):
    def run():
        state["busy"] = True
        try:
            function()
        finally:
            state["busy"] = False

    return run


def finish():
    log("")
    if CRASHES:
        log(f"[RESULT] 捕获 {len(CRASHES)} 处异常")
        FAILURES.append("出现未处理异常")
    if FAILURES:
        log(f"[RESULT] {len(FAILURES)} 项失败: {FAILURES}")
        QApplication.instance().exit(1)
    else:
        log("[RESULT] 全部通过（未复现闪退）")
        QApplication.instance().exit(0)


def main():
    global WINDOW
    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app

    refactored_app.UpdateController = _NullUpdateController

    window = refactored_app.My3DAnalyzer()
    WINDOW = window
    window._save_splitter_sizes = lambda *_args: None
    window.show()

    crop_button = window.btn_tb_crop
    erase_button = window.btn_tb_erase
    popup = window.crop_controller.popup

    # ---------------------------------------------------------------- 各步骤
    @step("准备二维页", ready=render_ready)
    def _prepare_2d():
        if PAGE != "axis2d":
            log("[SKIP] home 模式：直接用加载后的原始 3D 视图")
            return
        window.left_workspace.activate_page(window.left_workspace.home_page_id)
        window.page_data.combo_ax.setCurrentIndex(2)  # Z 轴（能量）积分 -> kx–ky
        window.page_data.btn_ax_apply.click()

    @step("进入裁剪模式", ready=crop_mode_ready)
    def _enter_crop():
        crop_button.click()
        check("裁剪开关打开", crop_button.isChecked() and window.crop_controller.enabled)
        check("范围窗口可见", popup.isVisible())
        describe_context("裁剪模式初始")

    @step("拖动裁剪框", ready=lambda: True)
    def _drag_crop():
        if MODE != "drag":
            log("[SKIP] default 模式：使用默认裁剪框，不拖动")
            return
        drag_and_report("拖动")

    @step("提交裁剪")
    def _apply_crop():
        state["page"] = page_id()
        popup.apply_button.click()
        describe_context("裁剪后")

    @step("裁剪结果页就绪",
          ready=lambda: page_id() != state["page"] and render_ready())
    def _crop_page_ready():
        spec = window.left_workspace.current_spec()
        check("裁剪新增了结果页", spec is not None and spec.page_id != state["page"])
        check("裁剪页没有空数据", not (window.current_render_context or {}).get("crop_empty"))
        describe_context(f"裁剪页 {spec.page_kind}")
        log(f"[PARAMS] {spec.params.get('crop_regions')}")
        bounds = (window.current_render_context or {}).get("data_bounds")
        if bounds is not None:
            # 崩溃前提条件：拖动保留下段会让 data_bounds 下界非零。默认框对照
            # 组（下界为 0）本来就不该满足它，只作信息记录。
            message = f"裁剪页 data_bounds 下界非零（崩溃前提条件）: {bounds}"
            if PAGE == "home" and MODE == "drag" and KEEP == "upper":
                check(message, any(bounds[2 * i] > 0 for i in range(3)))
            else:
                log(f"[INFO] {message} -> {any(bounds[2 * i] > 0 for i in range(3))}")
        state["crop_bounds"] = volume_world_bounds()
        log(f"[BOUNDS] 裁剪页体积世界坐标 {state['crop_bounds']}")
        if window.left_display_stack.currentIndex() == 0:
            save_shot("crop-page")

    @step("切到裁空模式")
    def _enter_erase():
        erase_button.click()
        check("裁空开关打开", erase_button.isChecked() and window.crop_controller.enabled)
        check("裁剪开关互斥关闭", not crop_button.isChecked())
        check("窗口换成裁空文案", popup.apply_button.text() == "裁空")
        describe_context("裁空模式")
        state["page"] = page_id()

    @step("拖动裁空框")
    def _drag_erase():
        if MODE != "drag":
            log("[SKIP] default 模式：使用默认裁剪框，不拖动")
            return
        drag_and_report("裁空拖动")

    @step("提交裁空")
    def _apply_erase():
        state["page"] = page_id()
        popup.apply_button.click()

    @step("裁空结果页就绪",
          ready=lambda: page_id() != state["page"] and render_ready())
    def _erase_page_ready():
        spec = window.left_workspace.current_spec()
        check("裁空新增了结果页", spec is not None and spec.page_id != state["page"])
        describe_context(f"裁空页 {spec.page_kind}")
        log(f"[PARAMS] {spec.params.get('crop_regions')}")
        check("窗口仍然存活（未闪退）", window.isVisible())
        log(f"[RESULT] 空页标记 crop_empty={(window.current_render_context or {}).get('crop_empty')}")
        erase_bounds = volume_world_bounds()
        log(f"[BOUNDS] 裁空页体积世界坐标 {erase_bounds}")
        if state.get("crop_bounds") and erase_bounds:
            check(f"裁空没有移动/缩放体积 {state['crop_bounds']} -> {erase_bounds}",
                  all(abs(a - b) < 1e-3 for a, b in zip(state["crop_bounds"], erase_bounds)))
        if window.left_display_stack.currentIndex() == 0:
            save_shot("erase-page")

    window.load_data(str(args.data))
    timer = QTimer()
    timer.timeout.connect(pump)
    timer.start(250)
    QTimer.singleShot(180000, lambda: (log("[FAIL] 全局超时"), app.exit(2)))

    return app.exec_()


if __name__ == "__main__":
    code = main()
    log(f"[EXIT] {code}")
    sys.exit(code)
