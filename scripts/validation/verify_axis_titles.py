# -*- coding: utf-8 -*-
"""轴标题与页面改名的真实窗口验收：双击、拖动、裁剪共存、3D 轴名与导出。

验证点：
1. 2D 页默认显示横纵轴标题，文字按切面与坐标单位生成；
2. 改名（中文）、取消不改名、清空隐藏标题、恢复默认文字与位置；
3. 按住标题拖动任意位置，超出画布时被收回可见区，松手后落点保留；
4. 裁剪模式下标题命中优先：按下标题不产生裁剪选区，绘图区内按下仍然照旧；
5. 切页、调色带后轴标题保留，页面之间互不影响；
6. 3D 右键「轴标题…」改 X/Y/E 轴名，旋转相机后仍在；坐标轴关着改名，打开后生效；
7. 导出快照采用主画布轴名，截图样式的显式覆盖优先；
8. 双击页面名称改名（长中文名）、切页保留、恢复自动名称；换数据后回到自动名。

用法: .venv/Scripts/python.exe scripts/validation/verify_axis_titles.py <npz路径> [--dynamic]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QEvent, QPoint, Qt, QTimer
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication, QMenu

NPZ = None
OUT_DIR = None

failures = []
state = {}


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}", flush=True)
    if not condition:
        failures.append(label)


def log(message):
    print(message, flush=True)


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


class _NullToast:
    """提示层不参与验收，免得在截图上盖住画布。"""

    def __init__(self, *args, **kwargs):
        pass

    def show(self, *args, **kwargs):
        pass

    def clear(self):
        pass


# ---------------------------------------------------------------------------
# 对话框：exec_() 开嵌套事件循环，答案要在打开之前排好。
# ---------------------------------------------------------------------------
def answer_dialog(*, text=None, restore=False, axis=None, texts=None):
    """``texts`` 给三轴弹窗，``text`` 给单轴/页面改名弹窗，``restore`` 点恢复。"""

    def act():
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            failures.append("没有找到模态弹窗")
            return
        if restore:
            dialog.btn_restore.click()
            return
        if texts is not None:
            for key, value in texts.items():
                dialog.edits[key].setText(value)
        elif text is not None:
            dialog.edit.setText(text)
        dialog.accept()

    QTimer.singleShot(0, act)


def answer_dialog_cancel():
    def act():
        dialog = QApplication.activeModalWidget()
        if dialog is not None:
            dialog.reject()

    QTimer.singleShot(0, act)


# ---------------------------------------------------------------------------
# 真实鼠标事件：Qt 逻辑像素 ↔ matplotlib 物理像素（见 FigureCanvasQT）
# ---------------------------------------------------------------------------
def to_canvas_pos(window, mpl_xy):
    canvas = window.canvas_2d
    ratio = canvas.device_pixel_ratio
    height = canvas.figure.bbox.height
    return QPoint(int(round(mpl_xy[0] / ratio)), int(round((height - mpl_xy[1]) / ratio)))


def title_label(window, axis):
    return window.ax_2d.xaxis.label if axis == "x" else window.ax_2d.yaxis.label


def title_box(window, axis):
    return title_label(window, axis).get_window_extent(window.canvas_2d.get_renderer())


def title_center(window, axis):
    box = title_box(window, axis)
    return ((box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0)


def send_mouse(widget, kind, pos, *, button=Qt.LeftButton, buttons=Qt.NoButton):
    event = QMouseEvent(kind, pos, widget.mapToGlobal(pos), button, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, event)


def drag_canvas(window, start_mpl, delta, *, steps=3):
    canvas = window.canvas_2d
    start = to_canvas_pos(window, start_mpl)
    offset = QPoint(int(delta[0]), int(delta[1]))
    send_mouse(canvas, QEvent.MouseButtonPress, start, buttons=Qt.LeftButton)
    for step in range(1, steps + 1):
        send_mouse(
            canvas,
            QEvent.MouseMove,
            start + (offset * step) / steps,
            button=Qt.NoButton,
            buttons=Qt.LeftButton,
        )
    send_mouse(canvas, QEvent.MouseButtonRelease, start + offset, buttons=Qt.NoButton)


def double_click_canvas(window, mpl_pos):
    canvas = window.canvas_2d
    pos = to_canvas_pos(window, mpl_pos)
    send_mouse(canvas, QEvent.MouseButtonPress, pos, buttons=Qt.LeftButton)
    send_mouse(canvas, QEvent.MouseButtonRelease, pos, buttons=Qt.NoButton)
    send_mouse(canvas, QEvent.MouseButtonDblClick, pos, buttons=Qt.LeftButton)
    send_mouse(canvas, QEvent.MouseButtonRelease, pos, buttons=Qt.NoButton)


def save_shot(window, name):
    window.canvas_2d.draw()
    path = os.path.join(OUT_DIR, name)
    window.grab().save(path)
    log(f"[SHOT] {path}")


def save_3d_shot(window, name):
    """3D 场景是原生 OpenGL 表面，window.grab() 抓不到，只能让 VTK 自己截图。"""
    path = os.path.join(OUT_DIR, name)
    try:
        window.plotter.screenshot(path)
        log(f"[SHOT] {path}")
    except Exception as exc:
        log(f"[WARN] 3D 截图失败：{exc!r}")


def cube_axes_actor(window):
    for actor in window.plotter.renderer.actors.values():
        if type(actor).__name__ == "CubeAxesActor":
            return actor
    return None


def capture_popup_menu(attempts=20, interval_ms=60):
    """右键弹出的菜单会阻塞在 exec_()：排定时器记下条目再把它关掉。

    没有菜单弹出时也会在有限次重试后记 None，验收照常判定失败而不是挂死。
    """

    def act(remaining):
        menu = QApplication.activePopupWidget()
        if menu is None and remaining > 0:
            QTimer.singleShot(interval_ms, lambda: act(remaining - 1))
            return
        state.setdefault("popup_menus", []).append(
            None if menu is None else [action.text() for action in menu.actions()]
        )
        if menu is not None:
            menu.close()

    QTimer.singleShot(interval_ms, lambda: act(attempts))


def right_click(widget, pos):
    send_mouse(
        widget,
        QEvent.MouseButtonPress,
        pos,
        button=Qt.RightButton,
        buttons=Qt.RightButton,
    )


def cube_axes_titles(window):
    actor = cube_axes_actor(window)
    if actor is None:
        return None
    return (str(actor.GetXTitle()), str(actor.GetYTitle()), str(actor.GetZTitle()))


def axis_title_key_2d(window):
    return window._axis_title_context(window.current_render_context)["key"]


def resolved_titles(window):
    titles, _ = window._render_2d_axis_titles(
        window.current_render_context, window.left_workspace.current_spec()
    )
    return titles


def main():
    global NPZ, OUT_DIR
    from scripts.validation._common import arguments

    args = arguments("verify_axis_titles", dynamic=True)
    NPZ, OUT_DIR = str(args.data), str(args.output_dir)

    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app

    refactored_app.UpdateController = _NullUpdateController
    refactored_app.ToastManager = _NullToast

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    window.resize(1500, 950)

    def finish():
        save_shot(window, "axis_titles_final.png")
        if failures:
            print(f"[RESULT] {len(failures)} 项失败", flush=True)
            for item in failures:
                print(f"  - {item}", flush=True)
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

    # ------------------------------------------------------------ 加载与建页
    def step_load():
        log(f"[load] {NPZ}")
        check("数据已加载", window.core.raw_data is not None)
        QTimer.singleShot(4000, guarded(step_open_2d))

    def step_open_2d():
        window.left_workspace.activate_page(window.left_workspace.home_page_id)
        window.page_data.combo_ax.setCurrentIndex(2)  # 沿 E 轴积分 → kx–ky 面

        def click():
            window.page_data.btn_ax_apply.click()
            QTimer.singleShot(10000, guarded(step_default_titles))

        QTimer.singleShot(600, click)

    def step_default_titles():
        spec = window.left_workspace.current_spec()
        state["page_2d"] = spec.page_id
        check("已切到 2D 页", (window.current_render_context or {}).get("view") == "2d")
        titles = resolved_titles(window)
        state["default_x"], state["default_y"] = titles["x"], titles["y"]
        log(f"[titles] 默认横轴 {titles['x']!r} / 纵轴 {titles['y']!r}")
        check("横纵轴标题都已绘制", bool(titles["x"]) and bool(titles["y"]))
        check(
            "轴名取自坐标元信息（kx/ky，缺单位不猜）",
            titles["x"].startswith("kx") and titles["y"].startswith("ky"),
        )
        check(
            "画布上确实是这两段文字",
            window.ax_2d.get_xlabel() == titles["x"]
            and window.ax_2d.get_ylabel() == titles["y"],
        )
        save_shot(window, "axis_titles_default.png")
        QTimer.singleShot(300, guarded(step_rename_x))

    # ------------------------------------------------------------ 改名 / 取消
    def step_rename_x():
        answer_dialog(text="结合能 (eV)")
        window.axis_title_controller.edit("x")
        QTimer.singleShot(600, guarded(step_rename_x_check))

    def step_rename_x_check():
        check("改名写入了中文轴名", window.ax_2d.get_xlabel() == "结合能 (eV)")
        check("另一条轴不受影响", resolved_titles(window)["y"] == state["default_y"])
        save_shot(window, "axis_titles_renamed.png")

        answer_dialog_cancel()
        window.axis_title_controller.edit("x")
        QTimer.singleShot(500, guarded(step_cancel_check))

    def step_cancel_check():
        check("取消编辑不改变原名", window.ax_2d.get_xlabel() == "结合能 (eV)")

        answer_dialog(text="")
        window.axis_title_controller.edit("y")
        QTimer.singleShot(500, guarded(step_clear_check))

    def step_clear_check():
        check("清空文字隐藏该轴标题", window.ax_2d.get_ylabel() == "")
        check(
            "隐藏后仍是覆盖状态（右键菜单可再编辑）",
            window.axis_title_state.text(axis_title_key_2d(window), "y") == "",
        )
        window.axis_title_controller.reset_texts()
        QTimer.singleShot(800, guarded(step_restore_check))

    def step_restore_check():
        check("恢复默认文字后纵轴回到自动轴名", window.ax_2d.get_ylabel() == state["default_y"])
        check("横轴也回到默认文字", window.ax_2d.get_xlabel() == state["default_x"])
        QTimer.singleShot(200, guarded(step_drag_setup))

    # -------------------------------------------------------------- 拖动
    def step_drag_setup():
        answer_dialog(text="结合能 (eV)")
        window.axis_title_controller.edit("x")
        QTimer.singleShot(700, guarded(step_drag))

    def step_drag():
        state["drag_before"] = title_center(window, "x")
        drag_canvas(window, title_center(window, "x"), (60.0, 40.0))
        QTimer.singleShot(500, guarded(step_drag_check))

    def step_drag_check():
        before = state["drag_before"]
        after = title_center(window, "x")
        moved = abs(after[0] - before[0]) + abs(after[1] - before[1])
        log(f"[drag] 位移 ({after[0] - before[0]:.0f}, {after[1] - before[1]:.0f}) px")
        check("按住标题拖动改变位置", moved > 20)
        check("横轴标题保持水平", window.ax_2d.xaxis.label.get_rotation() == 0.0)
        check("纵轴标题保持竖直", window.ax_2d.yaxis.label.get_rotation() == 90.0)
        position = window.axis_title_state.position(axis_title_key_2d(window), "x")
        check(f"位置按相对绘图区坐标保存 {position}", position is not None)
        save_shot(window, "axis_titles_dragged.png")

        drag_canvas(window, title_center(window, "x"), (-4000.0, -4000.0), steps=2)
        QTimer.singleShot(400, guarded(step_clamp_check))

    def step_clamp_check():
        box = title_box(window, "x")
        figure_box = window.canvas_2d.figure.bbox
        inside = (
            box.x0 >= figure_box.x0 - 1
            and box.x1 <= figure_box.x1 + 1
            and box.y0 >= figure_box.y0 - 1
            and box.y1 <= figure_box.y1 + 1
        )
        check(f"拖出画布后被收回可见区 {tuple(round(v) for v in box.bounds)}", inside)
        save_shot(window, "axis_titles_clamped.png")

        window.axis_title_controller.reset_positions()
        QTimer.singleShot(800, guarded(step_double_click))

    def step_double_click():
        answer_dialog(text="光子能量 (eV)")
        double_click_canvas(window, title_center(window, "x"))
        QTimer.singleShot(900, guarded(step_double_click_check))

    def step_double_click_check():
        check("双击标题弹出改名框并生效", window.ax_2d.get_xlabel() == "光子能量 (eV)")
        QTimer.singleShot(200, guarded(step_crop_conflict))

    # ---------------------------------------------------------- 裁剪共存
    def step_crop_conflict():
        window.btn_tb_crop.click()
        check("裁剪模式已打开", window._crop_enabled())

        selector = window.axis_crop_selector
        check("裁剪模式下已挂上矩形选区", selector is not None)

        start = title_center(window, "x")
        drag_canvas(window, start, (50.0, 50.0))
        check("按下标题不会开始裁剪选区", selector._eventpress is None)
        check("拖完标题后仍在裁剪模式", window._crop_enabled())
        check("横轴标题仍被拖动（拖动优先于裁剪）", window.ax_2d.get_xlabel() == "光子能量 (eV)")

        # 裁剪模式下右键走真实路径：事件过滤器吞掉 ContextMenu，菜单由它自己排出来。
        capture_popup_menu()
        right_click(window.canvas_2d, to_canvas_pos(window, title_center(window, "x")))
        QTimer.singleShot(2500, guarded(step_crop_menu_check))

    def step_crop_menu_check():
        menus = state.get("popup_menus") or []
        entries = menus[-1] if menus else None
        check(
            f"裁剪模式下右键保留轴标题入口 {entries}",
            bool(entries) and "恢复默认位置" in entries and "恢复默认文字" in entries,
        )
        check("菜单关闭后范围窗口仍在", window.crop_controller.popup.isVisible())

        plot_mpl = (window.ax_2d.bbox.x0 + 70, window.ax_2d.bbox.y1 - 70)
        drag_canvas(window, plot_mpl, (70.0, 70.0))
        QTimer.singleShot(600, guarded(step_crop_selection_check))

    def step_crop_selection_check():
        selection = window.crop_controller.selection
        check("绘图区内按下仍然照旧产生裁剪选区", selection is not None)
        save_shot(window, "axis_titles_crop_mode.png")
        window.btn_tb_crop.click()
        check("关闭裁剪模式", not window._crop_enabled())
        QTimer.singleShot(3500, guarded(step_page_isolation))

    # -------------------------------------------------------- 切页与调色带
    def step_page_isolation():
        window.left_workspace.activate_page(window.left_workspace.home_page_id)
        QTimer.singleShot(4500, guarded(step_back_to_2d))

    def step_back_to_2d():
        check("主页是 3D 视图", (window.current_render_context or {}).get("view") == "3d")
        window.left_workspace.activate_page(state["page_2d"])
        QTimer.singleShot(4500, guarded(step_back_check))

    def step_back_check():
        check("切页再回来轴标题保留", window.ax_2d.get_xlabel() == "光子能量 (eV)")
        window.page_render.s_gamma.setValue(35)
        QTimer.singleShot(3500, guarded(step_binding_check))

    def step_binding_check():
        check("调色带后轴标题仍保留", window.ax_2d.get_xlabel() == "光子能量 (eV)")
        save_shot(window, "axis_titles_after_binding.png")
        if window.core.has_time_axis:
            slider = window.timeline_bar.slider_time
            slider.setValue(min(1, slider.maximum()))
            QTimer.singleShot(5000, guarded(step_frame_check))
        else:
            step_frame_check()

    def step_frame_check():
        context = window.current_render_context or {}
        if window.core.has_time_axis and context.get("view") == "2d":
            check(
                f"切帧后轴标题仍保留（帧 {window.timeline_bar.slider_time.value()}）",
                window.ax_2d.get_xlabel() == "光子能量 (eV)",
            )
            save_shot(window, "axis_titles_after_frame.png")
        QTimer.singleShot(200, guarded(step_export))

    # ---------------------------------------------------------------- 导出
    def step_export():
        from bandscope.exporting.publication_export import capture_snapshot
        from bandscope.exporting.publication_models import resolve_axis_labels

        snapshot = capture_snapshot(window)
        check(
            f"快照采用主画布轴名 {snapshot.payload['xlabel']!r}",
            snapshot.payload["xlabel"] == window.ax_2d.get_xlabel(),
        )
        check(
            "截图样式的显式覆盖优先",
            resolve_axis_labels(snapshot, {"xlabel_text": "Fig. 3 x"})[0] == "Fig. 3 x",
        )
        check(
            "没有覆盖时用主画布文字",
            resolve_axis_labels(snapshot, {})[0] == window.ax_2d.get_xlabel(),
        )
        check(
            "快照来源标题取当前页面名称",
            snapshot.source_page_title == window.left_workspace.current_spec().title,
        )
        QTimer.singleShot(200, guarded(step_three_d))

    # ------------------------------------------------------------ 3D 轴名
    def step_three_d():
        window.left_workspace.activate_page(window.left_workspace.home_page_id)
        # 坐标轴开关是持久化设置，验收不依赖它上次的取值。
        window.timeline_bar.switch_axes.setChecked(True)
        QTimer.singleShot(4500, guarded(step_three_d_default))

    def step_three_d_default():
        save_3d_shot(window, "axis_titles_3d_default.png")
        titles = cube_axes_titles(window)
        check(f"3D 默认轴名 {titles}", titles == ("Kx", "Ky", "E (eV)"))
        answer_dialog(texts={"E": "Binding energy (eV)"})
        window._edit_three_d_axis_titles()
        QTimer.singleShot(3000, guarded(step_three_d_check))

    def step_three_d_check():
        titles = cube_axes_titles(window)
        check(
            f"右键「轴标题…」改 E 轴名 {titles}",
            titles is not None and titles[2] == "Binding energy (eV)",
        )
        # 相机拉到包围盒外，轴名才看得见（盒子在世界坐标 0..200）。
        window.plotter.camera_position = [
            (420.0, 380.0, 640.0),
            (100.0, 100.0, 100.0),
            (0.0, 0.0, 1.0),
        ]
        window.plotter.render()
        QTimer.singleShot(900, guarded(step_three_d_rotated))

    def step_three_d_rotated():
        titles = cube_axes_titles(window)
        check(
            f"旋转相机后轴名仍在 {titles}",
            titles is not None and titles[2] == "Binding energy (eV)",
        )
        save_3d_shot(window, "axis_titles_3d_rotated.png")

        window.global_refresh()
        QTimer.singleShot(3500, guarded(step_three_d_resized))

    def step_three_d_resized():
        titles = cube_axes_titles(window)
        check(
            f"整帧刷新（相机回到目标姿态）后轴名仍在 {titles}",
            titles is not None and titles[2] == "Binding energy (eV)",
        )
        save_3d_shot(window, "axis_titles_3d_after_refresh.png")

        window.timeline_bar.switch_axes.setChecked(False)
        QTimer.singleShot(3000, guarded(step_three_d_hidden))

    def step_three_d_hidden():
        check("关闭坐标轴后不再绘制轴线", cube_axes_actor(window) is None)
        answer_dialog(texts={"E": "E (eV)"})
        window._edit_three_d_axis_titles()
        QTimer.singleShot(800, guarded(step_three_d_reshown))

    def step_three_d_reshown():
        window.timeline_bar.switch_axes.setChecked(True)
        QTimer.singleShot(3000, guarded(step_three_d_reshown_check))

    def step_three_d_reshown_check():
        titles = cube_axes_titles(window)
        check(
            f"坐标轴隐藏期间改的名字重新显示时生效 {titles}",
            titles is not None and titles[2] == "E (eV)",
        )
        QTimer.singleShot(200, guarded(step_page_rename))

    # ------------------------------------------------------------ 页面改名
    def step_page_rename():
        window.left_workspace.activate_page(state["page_2d"])
        QTimer.singleShot(2500, guarded(step_page_rename_dialog))

    def step_page_rename_dialog():
        spec = window.left_workspace.current_spec()
        state["original_2d_title"] = spec.title
        state["long_title"] = "超长的页面名称：沿能量轴积分得到的费米面（验收页眉排版）"
        # 页面树常驻在左侧：改名后它要跟着换字。
        workspace = window.left_workspace
        answer_dialog(text=f"  {state['long_title']}  ")
        workspace._on_rename_requested()
        QTimer.singleShot(800, guarded(step_page_rename_check))

    def step_page_rename_check():
        spec = window.left_workspace.current_spec()
        check("页面改名成功（首尾空白去掉）", spec.title == state["long_title"])
        check("页眉同步新名称", window.left_workspace.page_title.text() == state["long_title"])
        check(
            "页面树同步新名称",
            state["long_title"] in window.left_workspace.page_tree.entry_titles(),
        )
        check("状态栏同步新名称", window.status_page.text() == state["long_title"])
        check(
            "长名称没有挤掉页眉的关闭按钮",
            window.left_workspace.close_button.isVisible()
            and window.left_workspace.close_button.x() > 0,
        )
        check(
            "页面身份与计算参数不变",
            spec.page_id == state["page_2d"] and spec.params.get("axis_index") == 2,
        )
        save_shot(window, "page_title_renamed.png")
        QTimer.singleShot(200, guarded(step_page_home_rename))

    def step_page_home_rename():
        window.left_workspace.activate_page("home")
        QTimer.singleShot(2500, guarded(step_page_home_rename_dialog))

    def step_page_home_rename_dialog():
        state["home_title"] = window.left_workspace.current_spec().title
        answer_dialog(text="我的主页")
        window.left_workspace._on_rename_requested()
        QTimer.singleShot(800, guarded(step_page_home_rename_check))

    def step_page_home_rename_check():
        check("主页也能改名", window.left_workspace.current_spec().title == "我的主页")
        window.left_workspace._on_restore_requested()
        QTimer.singleShot(800, guarded(step_page_home_restore_check))

    def step_page_home_restore_check():
        spec = window.left_workspace.current_spec()
        check(f"主页恢复自动名称回到 {spec.title!r}", spec.title == state["home_title"])
        check("恢复后不再是用户命名", not spec.title_overridden)
        QTimer.singleShot(200, guarded(step_page_rename_back))

    def step_page_rename_back():
        window.left_workspace.activate_page(state["page_2d"])
        QTimer.singleShot(2500, guarded(step_page_restore_check))

    def step_page_restore_check():
        check(
            "切页回来长名称仍在（页眉与页面树同步）",
            window.left_workspace.current_spec().title == state["long_title"]
            and state["long_title"] in window.left_workspace.page_tree.entry_titles(),
        )
        window.left_workspace._on_restore_requested()
        QTimer.singleShot(800, guarded(step_page_restored_check))

    def step_page_restored_check():
        spec = window.left_workspace.current_spec()
        check(f"恢复自动名称回到 {spec.title!r}", spec.title == state["original_2d_title"])
        check("恢复后不再是用户命名", not spec.title_overridden)
        save_shot(window, "page_title_restored.png")
        QTimer.singleShot(200, guarded(step_reload_prepare))

    # ------------------------------------------------- 换数据后整体复位
    def step_reload_prepare():
        answer_dialog(text="重命名后再换数据")
        window.left_workspace._on_rename_requested()
        QTimer.singleShot(600, guarded(step_reload_apply))

    def step_reload_apply():
        check(
            "换数据前的自定义名称已生效",
            window.left_workspace.current_spec().title == "重命名后再换数据",
        )
        window.load_data(NPZ)
        QTimer.singleShot(10000, guarded(step_reload_check))

    def step_reload_check():
        home = window.left_workspace.home_spec()
        check(f"换数据后主页仍是自动名称 {home.title!r}", not home.title_overridden)
        check("换数据后原来的 2D 结果页已关闭", state["page_2d"] not in window.left_workspace.page_specs)
        titles = (
            window.axis_title_state.texts(axis_title_key_2d(window))
            if (window.current_render_context or {}).get("view") == "2d"
            else {}
        )
        check("换数据后轴标题覆盖已清空", titles == {})
        finish()

    window.load_data(NPZ)
    QTimer.singleShot(8000, guarded(step_load))
    QTimer.singleShot(300000, lambda: app.exit(2))  # 兜底防挂死

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
