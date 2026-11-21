"""Real-window acceptance for polyline E–k path slices.

Usage: .venv/Scripts/python.exe scripts/validation/verify_path_slice.py [data.npz]
Defaults to .local/data/WSe2_step.npz and writes .local/outputs/path-slice/.
"""
from __future__ import annotations

import faulthandler
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "xcb")

_SANDBOX = tempfile.TemporaryDirectory(prefix="bandscope-path-slice-")
os.environ["BANDSCOPE_EXTENSION_ROOT"] = str(Path(_SANDBOX.name) / "extensions")

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QPoint, QPointF, QSettings, Qt, QTimer
from PyQt5.QtGui import QContextMenuEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMenu

QSettings.setDefaultFormat(QSettings.IniFormat)
for _scope in (QSettings.UserScope, QSettings.SystemScope):
    QSettings.setPath(QSettings.IniFormat, _scope, _SANDBOX.name)

import numpy as np

from bandscope.core.path_slice import PathNode, draft_nodes, write_draft


faulthandler.enable()

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append({"item": name, "ok": bool(ok), "detail": str(detail)})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", nargs="?", type=Path, default=REPO_ROOT / ".local/data/WSe2_step.npz")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / ".local/outputs/path-slice")
    parser.add_argument("--hold", action="store_true")
    args = parser.parse_args()
    if not args.data.is_file():
        parser.error(f"数据文件不存在：{args.data}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    import bandscope.app.refactored_app as refactored_app
    import bandscope.exporting.publication_export as publication
    from bandscope.exporting.publication_models import OutputOptions, resolve_style
    from bandscope.ui import theme

    refactored_app.UpdateController = _NullUpdateController
    app = QApplication.instance() or QApplication(sys.argv)
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    messages = []
    window._show_message = lambda title, text, *a, **k: messages.append((title, text))
    state = {"timed_out": [], "failed": False}

    def wait_for(description, predicate, next_step, timeout_ms=90000):
        deadline = time.perf_counter() + timeout_ms / 1000.0

        def poll():
            try:
                ready = bool(predicate())
            except Exception:
                ready = False
            if ready:
                guard(next_step)
            elif time.perf_counter() < deadline:
                QTimer.singleShot(250, poll)
            else:
                record(f"等待{description}", False, "超时")
                state["timed_out"].append(description)
                guard(next_step)

        poll()

    def guard(callback):
        try:
            callback()
        except Exception:
            state["failed"] = True
            traceback.print_exc()
            record("验收步骤异常", False, traceback.format_exc())
            finish()

    def canvas_position(logical):
        core = window.core
        physical = (
            core.logical_to_physical("X", logical[0]),
            core.logical_to_physical("Y", logical[1]),
        )
        x, y = window.ax_2d.transData.transform(physical)
        ratio = window.canvas_2d.devicePixelRatioF()
        height = window.canvas_2d.get_width_height(physical=True)[1]
        return QPointF(x / ratio, (height - y) / ratio).toPoint()

    def click_path_node(logical, double=False):
        position = canvas_position(logical)
        if double:
            QTest.mouseDClick(window.canvas_2d, Qt.LeftButton, Qt.NoModifier, position)
        else:
            QTest.mouseClick(window.canvas_2d, Qt.LeftButton, Qt.NoModifier, position)

    def choose_menu_action(text):
        popup = QApplication.activePopupWidget() or QApplication.activeModalWidget()
        if not isinstance(popup, QMenu):
            record(f"右键菜单「{text}」", False, "菜单未出现")
            return
        action = next((item for item in popup.actions() if item.text() == text), None)
        record(f"右键菜单「{text}」", action is not None, popup.title())
        if action is not None:
            action.trigger()
        popup.close()

    def capture(name):
        path = args.output_dir / name
        if not window.grab().save(str(path)):
            raise AssertionError(f"窗口截图失败：{path}")
        print(f"[capture] {path}", flush=True)

    def step_load():
        window.showNormal()
        window.resize(1500, 920)
        window.load_data(str(args.data))
        wait_for(
            "主页渲染",
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "3d"
            and window._render_exact_ready,
            step_integral,
        )

    def step_integral():
        window.canvas_tool_overlay.preset_buttons["xy"].click()
        wait_for(
            "kx–ky 等能面",
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "2d"
            and window.left_workspace.current_spec().params.get("axis_index") == 2
            and window._render_exact_ready,
            step_pick,
        )

    def step_pick():
        source = window.left_workspace.current_spec()
        record("等能面显示折线工具", window.path_slice_controller.button.isVisible())
        window.path_slice_controller.button.click()
        record("点亮后进入选点模式", window.path_slice_controller.active_source_id == source.page_id)
        nx, ny = window.core.raw_data.shape[:2]
        state["points"] = [
            (nx * 0.08, ny * 0.08),
            (nx * 0.90, ny * 0.08),
            (nx * 0.90, ny * 0.90),
            (nx * 0.08, ny * 0.08),
        ]
        for point in state["points"]:
            click_path_node(point)
        QTimer.singleShot(max(1000, 2 * QApplication.doubleClickInterval() + 200), open_rename)

    def open_rename():
        source = window.left_workspace.current_spec()
        nodes = draft_nodes(source.params)
        record(
            "左键连续落点并闭合", len(nodes) == 4,
            {"count": len(nodes), "pending": window.path_slice_controller._pending_single,
             "nodes": [(round(node.x, 3), round(node.y, 3), node.label) for node in nodes]},
        )
        QTimer.singleShot(250, finish_rename)
        click_path_node(state["points"][0], double=True)

    def finish_rename():
        dialog = QApplication.activeModalWidget()
        edit = getattr(dialog, "edit", None)
        if edit is not None:
            edit.setText("Γ")
            dialog._accept_name()
        else:
            record("节点改名对话框", False, "未找到输入框")
        QTimer.singleShot(150, step_generate)

    def step_generate():
        source = window.left_workspace.current_spec()
        nodes = draft_nodes(source.params)
        record("双击改名不追加点", len(nodes) == 4 and nodes[0].label == "Γ", [node.label for node in nodes])
        position = canvas_position(state["points"][1])
        QTimer.singleShot(250, lambda: choose_menu_action("生成路径切片"))
        event = QContextMenuEvent(
            QContextMenuEvent.Mouse, position, window.canvas_2d.mapToGlobal(position),
        )
        QApplication.sendEvent(window.canvas_2d, event)
        wait_for(
            "路径结果页",
            lambda: window.left_workspace.current_spec().page_kind == "path_slice"
            and window.current_render_context is not None
            and window.current_render_context.get("view") == "path_slice"
            and window._render_exact_ready,
            step_segments,
        )

    def step_segments():
        spec = window.left_workspace.current_spec()
        model = window.path_slice_controller.model_for(spec)
        record("创建后进入来源子页", spec.source_page_id is not None and model.segment_count == 3, spec.title)
        record("底部路径条显示", window.path_slice_controller.bar.isVisible())
        record("默认选中第一段", model.selected == [0])
        overview = window.path_slice_controller.bar.overview
        rect = overview._segment_rects()[2]
        QTest.mouseClick(
            overview, Qt.LeftButton, Qt.ShiftModifier,
            QPoint(int(rect.center().x()), int(rect.center().y())),
        )
        selected = window.active_page_spec.params.get("path_selected_segments")
        record("Shift 跳选第三段", selected == [0, 2], selected)
        context = window.current_render_context
        state["before_thickness"] = context["raw_intensity"].copy()
        state["segment_two"] = context["path_sample"].segments[1].intensity.copy()
        spacing = context["path_sample"].calibration.sample_spacing
        window.path_slice_controller.bar.thickness.setValue(2.0 * spacing)
        wait_for(
            "分段增厚重算",
            lambda: window.current_render_context["path_sample"].segments[0].thickness > 0
            and window._render_exact_ready,
            step_thickness,
        )

    def step_thickness():
        spec = window.active_page_spec
        thicknesses = [item["thickness"] for item in spec.params["path_segments"]]
        record("厚度只写选中段", thicknesses[0] > 0 and thicknesses[1] == 0 and thicknesses[2] > 0, thicknesses)
        unchanged = np.allclose(
            window.current_render_context["path_sample"].segments[1].intensity,
            state["segment_two"], equal_nan=True,
        )
        record("未选第二段原始缓存不变", unchanged)
        window.path_slice_controller.on_selection_changed([0])
        wait_for(
            "单选段色条",
            lambda: window.active_page_spec.params["path_selected_segments"] == [0]
            and window._render_exact_ready,
            step_capture_single,
        )

    def step_capture_single():
        capture("path-single-light.png")
        record("单选段色条显示", getattr(window.ax_2d, "_arpes_colorbar", None) is not None)
        window.path_slice_controller.on_selection_changed([0, 2])
        wait_for(
            "恢复多选",
            lambda: window.active_page_spec.params["path_selected_segments"] == [0, 2]
            and window._render_exact_ready,
            step_annotation,
        )

    def step_annotation():
        spec = window.active_page_spec
        window.path_slice_controller.set_annotation(spec, guides=True, value=False)
        wait_for("关闭竖虚线", lambda: not window.active_page_spec.params["path_show_guides"], lambda: None)
        window.path_slice_controller.set_annotation(spec, labels=True, value=False)
        wait_for("关闭点名", lambda: not window.active_page_spec.params["path_show_labels"], step_time)
        record("虚线与点名可独立关闭", True)

    def step_time():
        slider = window.timeline_bar.slider_time
        if slider.maximum() > slider.minimum():
            slider.setValue((slider.minimum() + slider.maximum()) // 2)
        wait_for(
            "路径时间切帧",
            lambda: window.active_page_spec.params.get("time_interval", {}).get("low")
            == window.core.coords["delay"][slider.value()]
            and window._render_exact_ready,
            step_narrow,
        )
        record("切帧保留路径节点", len(window.active_page_spec.params["path_nodes"]) == 4)

    def step_narrow():
        window.resize(980, 700)
        QTimer.singleShot(1200, lambda: guard(step_capture_light))

    def step_capture_light():
        bar = window.path_slice_controller.bar
        record("窄窗口路径独立一行", bar.secondary_row.isVisible())
        record("长路径提供横向滑条", not bar.scrollbar.isHidden())
        capture("path-light.png")
        publication_snapshot = publication.capture_snapshot(window)
        publication.render_and_save(
            publication_snapshot, resolve_style("2d", "2d_boxed"), {},
            OutputOptions(fmt="png", dpi=220), str(args.output_dir / "path-publication.png"),
        )
        _title, _name, payload = window.path_slice_controller.export_payload(
            window.active_page_spec, window.current_render_context,
        )
        np.savez(args.output_dir / "path-data.npz", **payload)
        record("出版图与数值导出", (args.output_dir / "path-publication.png").is_file())
        window._set_theme("dark")
        QTimer.singleShot(1800, lambda: guard(step_capture_dark))

    def step_capture_dark():
        record("深色主题切换", theme.current_theme() == "dark")
        capture("path-dark.png")
        window.resize(1500, 920)
        window._set_theme("light")
        QTimer.singleShot(1200, finish)

    def finish():
        failed = state["failed"] or bool(state["timed_out"]) or any(not item["ok"] for item in RESULTS)
        report = {
            "data": str(args.data),
            "platform": os.environ.get("QT_QPA_PLATFORM"),
            "results": RESULTS,
            "messages": messages,
            "outputs": sorted(path.name for path in args.output_dir.iterdir()),
        }
        (args.output_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[result] {'FAIL' if failed else 'PASS'}", flush=True)
        if args.hold:
            print("[hold] 验收窗口已保留，关闭窗口退出", flush=True)
            return
        for widget in QApplication.topLevelWidgets():
            if widget is not window and widget.isVisible():
                widget.close()
        window.close()
        app.exit(1 if failed else 0)

    def watchdog():
        record("真实窗口验收总时限", False, "超过 300 秒")
        state["failed"] = True
        finish()

    QTimer.singleShot(1200, lambda: guard(step_load))
    QTimer.singleShot(300000, watchdog)
    exit_code = app.exec_()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
