"""Real-window acceptance for live axis slices, rotation references and the footer.

Usage: .venv/Scripts/python.exe scripts/validation/verify_canvas_axis_slices.py [data.npz]
Defaults to .local/data/WSe2_step.npz and writes .local/outputs/canvas-axis-slices/.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "xcb")
_SANDBOX = tempfile.TemporaryDirectory(prefix="bandscope-axis-slices-")
os.environ["BANDSCOPE_EXTENSION_ROOT"] = str(Path(_SANDBOX.name) / "extensions")

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QEvent, QPointF, QSettings, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

QSettings.setDefaultFormat(QSettings.IniFormat)
for _scope in (QSettings.UserScope, QSettings.SystemScope):
    QSettings.setPath(QSettings.IniFormat, _scope, _SANDBOX.name)

import numpy as np

from bandscope.core.crop_model import apply_crop_regions
from bandscope.rendering.refresh_pipeline import RefreshCause, RenderQuality


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def wait_for(predicate, description, timeout=90):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        QTest.qWait(200)
    raise AssertionError(f"等待{description}超时")


def send_dial_mouse(dial, event_type, position, button, buttons):
    event = QMouseEvent(event_type, QPointF(position), button, buttons, Qt.NoModifier)
    QApplication.sendEvent(dial, event)
    QApplication.processEvents()


def ring_point(dial, angle):
    _rect, center, radius = dial._ring_geometry()
    radians = math.radians(angle)
    return QPointF(
        center.x() + math.sin(radians) * radius,
        center.y() - math.cos(radians) * radius,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", nargs="?", type=Path, default=REPO_ROOT / ".local/data/WSe2_step.npz")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / ".local/outputs/canvas-axis-slices")
    parser.add_argument("--hold", action="store_true")
    args = parser.parse_args()
    if not args.data.is_file():
        parser.error(f"数据文件不存在：{args.data}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    import bandscope.app.refactored_app as refactored_app
    from bandscope.core.axis_interval import AxisInterval
    from bandscope.ui import theme

    refactored_app.UpdateController = _NullUpdateController
    app = QApplication.instance() or QApplication(sys.argv)
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    render_frames = []
    original_render_active_page = window._render_active_page

    def tracked_render(quality=RenderQuality.EXACT, cause=None):
        result = original_render_active_page(quality, cause)
        context = window.current_render_context or {}
        render_frames.append((quality, cause, context.get("rotation_angle")))
        return result

    window._render_active_page = tracked_render
    window.showNormal()
    window.resize(1500, 920)
    window.show()
    results = []

    def record(name, ok, detail=""):
        results.append({"item": name, "ok": bool(ok), "detail": str(detail)})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)

    def capture(name, settle_ms=250):
        QApplication.processEvents()
        if settle_ms:
            QTest.qWait(settle_ms)
        path = args.output_dir / name
        if not window.grab().save(str(path)):
            raise AssertionError(f"截图失败：{path}")
        print(f"[capture] {path}", flush=True)

    overlay = window.canvas_tool_overlay
    panel = overlay.rotation_panel

    def context_at_angle(angle):
        context = window.current_render_context or {}
        return abs(float(context.get("rotation_angle", math.inf)) - float(angle)) < 1e-6

    def rotation_diagnostics():
        context = window.current_render_context or {}
        spec = window.left_workspace.current_spec()
        return {
            "window.rotation_angle": getattr(window, "rotation_angle", None),
            "panel.current_angle": panel.current_angle(),
            "context.rotation_angle": context.get("rotation_angle"),
            "_render_exact_ready": getattr(window, "_render_exact_ready", None),
            "_last_refresh_failure": getattr(window, "_last_refresh_failure", None),
            "visible_page_kind": getattr(spec, "page_kind", None),
            "visible_view": context.get("view"),
            "panel_visible": panel.isVisible(),
            "editor_text": panel.edit_rotation.text(),
        }

    def data_changed(before, after):
        return (
            before is not None and after is not None and before.shape == after.shape
            and not np.array_equal(before, after, equal_nan=True)
        )

    def fixed_snapshot(spec):
        return {
            "axis_interval": copy.deepcopy(spec.params.get("axis_interval")),
            "normal_indices": tuple(spec.params.get(key) for key in ("low", "up", "mid")),
            "source_angle": copy.deepcopy((spec.params.get("source_3d") or {}).get("rotation_angle")),
            "crop_regions": copy.deepcopy(spec.params.get("crop_regions") or []),
            "axes": tuple(window.ax_2d.get_xlim()) + tuple(window.ax_2d.get_ylim()),
            "data_bounds": copy.deepcopy((window.current_render_context or {}).get("data_bounds")),
        }

    def expected_axis_data(spec, angle):
        params = spec.params
        axis_index = int(params["axis_index"])
        time_interval = params.get("time_interval")
        if time_interval:
            t_low, t_up = AxisInterval.from_dict(time_interval).to_indices(window.core.coords["delay"])
        else:
            t_low = t_up = 0
        if t_low == t_up:
            frame = np.asarray(window.original_raw_data[..., t_low], dtype=np.float32)
        else:
            frame = np.sum(
                window.original_raw_data[..., t_low:t_up + 1], axis=3, dtype=np.float32,
            )
        volume = window.backend_manager.cpu.rotate_volume(frame, float(angle))
        source = {
            "view": "3d", "data": volume, "coords": window.core.coords,
            "rotation_angle": float(angle),
        }
        source_3d = params.get("source_3d") or {}
        source_regions = ((source_3d.get("params") or {}).get("crop_regions") or [])
        if source_regions:
            source = apply_crop_regions(source, source_regions, rotation_angle=float(angle))
        source_data = np.nan_to_num(
            np.asarray(source["data"], dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0,
        )
        axis_key = ("X", "Y", "E")[axis_index]
        interval = AxisInterval.from_dict(params["axis_interval"])
        low, up = interval.to_indices(window.core.coords[axis_key])
        slices = [slice(None)] * 3
        slices[axis_index] = slice(low, up + 1)
        integrated = np.sum(source_data[tuple(slices)], axis=axis_index, dtype=np.float64)
        plot_keys = {0: ("Y", "E"), 1: ("X", "E"), 2: ("X", "Y")}[axis_index]
        context = {
            "view": "2d",
            "data": integrated,
            "slice_info": {
                "axis": axis_index, "index": int(params["mid"]),
                "mode": "integral", "range": (low, up),
            },
            "coords": window.core.coords,
            "rotation_angle": float(angle),
            "plot_axes": {
                "x_key": plot_keys[0], "y_key": plot_keys[1],
                "x_label": plot_keys[0], "y_label": plot_keys[1],
            },
            "plot_logical_bounds": {
                "x_low": 0, "x_up": integrated.shape[0] - 1,
                "y_low": 0, "y_up": integrated.shape[1] - 1,
            },
        }
        regions = list(params.get("crop_regions") or [])
        if regions:
            return np.asarray(
                apply_crop_regions(context, regions, rotation_angle=float(angle))["data"]
            )
        if spec.page_kind == "axis_integral_crop":
            rect = params.get("crop_base_rect") or {
                "x_low": params.get("crop_k_low"), "x_up": params.get("crop_k_up"),
                "y_low": params.get("crop_e_low"), "y_up": params.get("crop_e_up"),
            }
            if all(value is not None for value in rect.values()):
                return integrated[
                    int(rect["x_low"]):int(rect["x_up"]) + 1,
                    int(rect["y_low"]):int(rect["y_up"]) + 1,
                ]
        return integrated

    def verify_reference(spec, label, angle, quality, previous, fixed):
        actual = np.asarray(window.current_render_context["data"])
        expected = expected_axis_data(spec, angle)
        close = (
            actual.shape == expected.shape
            and np.allclose(actual, expected, rtol=3e-5, atol=3e-5, equal_nan=True)
        )
        if actual.shape == expected.shape and np.isfinite(actual - expected).any():
            max_error = float(np.nanmax(np.abs(actual - expected)))
        else:
            max_error = None
        changed = previous is None or data_changed(previous, actual)
        fixed_ok = fixed_snapshot(spec) == fixed
        record(
            f"{label} {angle:g}° {quality}数组、固定区间与独立参考",
            close and changed and fixed_ok and context_at_angle(angle),
            {
                "shape": list(actual.shape), "reference_shape": list(expected.shape),
                "changed": changed, "fixed": fixed_ok, "max_abs_error": max_error,
                "low": spec.params.get("low"), "up": spec.params.get("up"),
                "source_angle": (spec.params.get("source_3d") or {}).get("rotation_angle"),
            },
        )
        return actual.copy()

    def commit_exact_angle(angle, description):
        if not panel.isVisible():
            overlay.rotation_button.click()
        send_dial_mouse(
            panel.dial, QEvent.MouseButtonPress, ring_point(panel.dial, panel.dial.angle()),
            Qt.LeftButton, Qt.LeftButton,
        )
        send_dial_mouse(
            panel.dial, QEvent.MouseMove, ring_point(panel.dial, angle),
            Qt.NoButton, Qt.LeftButton,
        )
        send_dial_mouse(
            panel.dial, QEvent.MouseButtonRelease, ring_point(panel.dial, angle),
            Qt.LeftButton, Qt.NoButton,
        )
        QApplication.processEvents()
        wait_for(
            lambda: window._render_exact_ready and context_at_angle(angle), description,
        )

    def exercise_slice(spec, label, intermediate_angles=(0.0, -15.0), final_angle=45.0):
        fixed = fixed_snapshot(spec)
        previous = verify_reference(
            spec, label, window.rotation_angle, "创建精确", None, fixed,
        )
        if not panel.isVisible():
            overlay.rotation_button.click()
        send_dial_mouse(
            panel.dial, QEvent.MouseButtonPress, ring_point(panel.dial, panel.dial.angle()),
            Qt.LeftButton, Qt.LeftButton,
        )
        for index, angle in enumerate(intermediate_angles):
            before = previous
            frame_marker = len(render_frames)
            send_dial_mouse(
                panel.dial, QEvent.MouseMove, ring_point(panel.dial, angle),
                Qt.NoButton, Qt.LeftButton,
            )
            wait_for(
                lambda: any(
                    quality == RenderQuality.PREVIEW and cause == RefreshCause.ROTATION
                    and rendered_angle is not None and abs(float(rendered_angle) - angle) < 1e-6
                    for quality, cause, rendered_angle in render_frames[frame_marker:]
                )
                and context_at_angle(angle)
                and data_changed(before, np.asarray((window.current_render_context or {}).get("data", []))),
                f"{label} {angle:g}° 数值中间预览",
            )
            previous = verify_reference(spec, label, angle, "中间预览", before, fixed)
            if index == 0:
                capture(f"{label.lower()}-rotation-preview.png")
        before_final = previous
        final_frame_marker = len(render_frames)
        send_dial_mouse(
            panel.dial, QEvent.MouseMove, ring_point(panel.dial, final_angle),
            Qt.NoButton, Qt.LeftButton,
        )
        send_dial_mouse(
            panel.dial, QEvent.MouseButtonRelease, ring_point(panel.dial, final_angle),
            Qt.LeftButton, Qt.NoButton,
        )
        wait_for(
            lambda: window._render_exact_ready and context_at_angle(final_angle)
            and any(
                quality == RenderQuality.EXACT and cause == RefreshCause.ROTATION
                and rendered_angle is not None and abs(float(rendered_angle) - final_angle) < 1e-6
                for quality, cause, rendered_angle in render_frames[final_frame_marker:]
            ),
            f"{label} {final_angle:g}° 最终精确结果",
        )
        verify_reference(spec, label, final_angle, "最终精确", before_final, fixed)
        capture(f"{label.lower()}-rotation-exact.png")

    def write_report():
        report = {
            "data": str(args.data),
            "platform": os.environ.get("QT_QPA_PLATFORM"),
            "results": results,
            "screenshots": sorted(path.name for path in args.output_dir.glob("*.png")),
            "automatic_screenshots_only": True,
            "not_executed": [
                "维护者人工视觉复核、真实鼠标流畅度与科研出图质量认可",
                "视图保存恢复、导出一致性和冻结派生页回归（不在本脚本执行）",
            ],
        }
        (args.output_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    try:
        window.load_data(str(args.data))
        wait_for(
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "3d"
            and window._render_exact_ready,
            "三维主页",
        )
        source = window.left_workspace.current_spec()
        commit_exact_angle(30.0, "主页 30° 创建角度")
        overlay.close_rotation_panel(commit=False)
        overlay.preset_buttons["xy"].click()
        wait_for(
            lambda: window.left_workspace.current_spec() is not source
            and window.current_render_context is not None
            and window.current_render_context.get("view") == "2d"
            and window._render_exact_ready,
            "XY 切片",
        )
        spec = window.left_workspace.current_spec()
        interval = AxisInterval.from_dict(spec.params["axis_interval"])
        record("XY 新建独立切片", spec.source_page_id == source.page_id, spec.title)
        record(
            "初始四分位端点",
            abs(interval.low - (interval.minimum + interval.span * 0.25)) < 1e-8
            and abs(interval.up - (interval.minimum + interval.span * 0.75)) < 1e-8,
            f"{interval.low:g} … {interval.up:g}",
        )
        record(
            "仅保留外观控制页",
            window.page_container.count() == 1
            and window.page_container.currentWidget() is window.page_render,
        )
        record("双端底栏可见", window.timeline_bar.interval_control.isVisible())
        exercise_slice(spec, "XY")
        overlay.close_rotation_panel(commit=False)

        slider = window.timeline_bar.interval_control.range_slider
        slider._show_popup("up")
        capture("xy-light-popup.png")
        slider.coordinate_popup.grab().save(str(args.output_dir / "coordinate-popup.png"))
        slider.coordinate_popup.hide()

        window.timeline_bar.interval_control.input_length.setValue(0.0)
        window.timeline_bar.interval_control.input_length.editingFinished.emit()
        wait_for(lambda: window._render_exact_ready, "合拢单层")
        record("零长度自动锁定", window.axis_interval.locked)
        capture("xy-light-locked.png")
        slider.lock_badge.click()
        slider.lock_badge._animation.pause()
        slider.lock_badge._animation.setCurrentTime(70)
        QApplication.processEvents()
        record("开锁动画进行帧", 0.0 < slider.lock_badge._open_progress < 1.0,
               f"progress={slider.lock_badge._open_progress:.3f}")
        capture("xy-unlock-animation.png", settle_ms=0)
        slider.lock_badge._animation.resume()
        QTest.qWait(250)
        record("开锁后端点保持", not window.axis_interval.locked and window.axis_interval.length == 0.0)

        window._set_theme("dark")
        capture("xy-dark.png")
        window.resize(1050, 760)
        QTest.qWait(500)
        record(
            "窄窗口底栏两行自适应",
            window.timeline_bar._wrapped
            and window.timeline_bar.secondary_row.isAncestorOf(window.timeline_bar.timeline_group)
            and not hasattr(window.timeline_bar, "view_controls"),
        )
        capture("xy-dark-narrow.png")
        window.resize(1500, 920)
        window._set_theme("light")
        window.canvas_tool_overlay.preset_buttons["3d"].click()
        wait_for(lambda: window.left_workspace.current_spec() is source, "返回三维来源")
        record("3D 返回来源页", True)

        window.canvas_tool_overlay.preset_buttons["xy"].click()
        wait_for(lambda: window._render_exact_ready, "第二张 XY 切片")
        second_spec = window.left_workspace.current_spec()
        record("同方向重复建页", second_spec.page_id != spec.page_id)

        for plane, axis_index in (("XZ", 1), ("YZ", 0)):
            window.left_workspace.activate_page(source.page_id)
            wait_for(
                lambda: window.left_workspace.current_spec() is source
                and window.current_render_context is not None
                and window.current_render_context.get("view") == "3d"
                and window._render_exact_ready,
                f"{plane} 前返回三维来源",
            )
            commit_exact_angle(30.0, f"{plane} 创建前主页 30°")
            overlay.close_rotation_panel(commit=False)
            overlay.preset_buttons[plane.lower()].click()
            wait_for(
                lambda: window.left_workspace.current_spec() is not source
                and window.left_workspace.current_spec().params.get("axis_index") == axis_index
                and window.current_render_context is not None
                and window.current_render_context.get("view") == "2d"
                and window._render_exact_ready,
                f"{plane} 切片",
            )
            exercise_slice(window.left_workspace.current_spec(), plane)

        window.left_workspace.activate_page(second_spec.page_id)
        wait_for(
            lambda: window.left_workspace.current_spec() is second_spec
            and window.current_render_context is not None
            and window.current_render_context.get("view") == "2d"
            and window._render_exact_ready,
            "返回第二张 XY 创建裁剪子页",
        )
        window.btn_tb_crop.click()
        wait_for(lambda: window.crop_controller.popup.isVisible(), "XY 裁剪范围浮窗")
        before_pages = set(window.left_workspace.page_specs)
        creation_angle = float(window.rotation_angle)
        window.crop_controller.popup.apply_button.click()
        wait_for(
            lambda: window.left_workspace.current_spec().page_id not in before_pages
            and window.left_workspace.current_spec().page_kind == "axis_integral_crop"
            and window.current_render_context is not None
            and window._render_exact_ready,
            "axis_integral_crop 真实裁剪子页",
        )
        cropped = window.left_workspace.current_spec()
        committed = copy.deepcopy(cropped.params["crop_regions"][-1])
        record(
            "直接 XY 裁剪子页记录创建角度与逻辑范围",
            abs(float(committed.get("rotation_angle")) - creation_angle) < 1e-6
            and len(committed.get("logical_bounds") or ()) == 4,
            {
                "creation_angle": creation_angle,
                "region_angle": committed.get("rotation_angle"),
                "logical_bounds": committed.get("logical_bounds"),
            },
        )
        exercise_slice(cropped, "XY-CROP")
        record(
            "旋转后已提交裁剪历史创建角度不变",
            cropped.params["crop_regions"][-1] == committed,
            committed,
        )
        overlay.close_rotation_panel(commit=False)
        write_report()
        if not all(item["ok"] for item in results):
            raise AssertionError("存在未通过的验收项")
        if args.hold:
            print("[hold] 验收窗口已保留，关闭窗口退出", flush=True)
            app.exec_()
        window.close()
    except Exception as exc:
        record(
            "验收步骤异常", False,
            {"error": f"{type(exc).__name__}: {exc}", "diagnostics": rotation_diagnostics()},
        )
        write_report()
        window.close()
        raise


if __name__ == "__main__":
    main()
