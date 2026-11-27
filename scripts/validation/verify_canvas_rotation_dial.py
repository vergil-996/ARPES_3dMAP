"""Real-window acceptance for live slice rotation, fixed spatial tools and the footer."""

from __future__ import annotations

import copy
import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "xcb")
_SANDBOX = tempfile.TemporaryDirectory(prefix="bandscope-rotation-dial-")
os.environ["BANDSCOPE_EXTENSION_ROOT"] = str(Path(_SANDBOX.name) / "extensions")

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

QSettings.setDefaultFormat(QSettings.IniFormat)
for _scope in (QSettings.UserScope, QSettings.SystemScope):
    QSettings.setPath(QSettings.IniFormat, _scope, _SANDBOX.name)

import numpy as np

from bandscope.core.path_slice import PathNode, draft_nodes, write_draft
from bandscope.rendering.refresh_pipeline import RefreshCause, RenderQuality


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def wait_for(predicate, description, timeout=90, settle_ms=250):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        QApplication.processEvents()
        if predicate():
            if settle_ms:
                QTest.qWait(settle_ms)
            return
        QTest.qWait(150)
    raise AssertionError(f"等待{description}超时")


def ring_point(dial, angle):
    _rect, center, radius = dial._ring_geometry()
    radians = math.radians(angle)
    return QPointF(
        center.x() + math.sin(radians) * radius,
        center.y() - math.cos(radians) * radius,
    )


def send_dial_mouse(dial, event_type, position, button, buttons):
    event = QMouseEvent(event_type, QPointF(position), button, buttons, Qt.NoModifier)
    QApplication.sendEvent(dial, event)
    QApplication.processEvents()


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", nargs="?", type=Path, default=REPO_ROOT / ".local/data/WSe2_step.npz")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / ".local/outputs/canvas-rotation-dial")
    parser.add_argument("--hold", action="store_true")
    args = parser.parse_args()
    if not args.data.is_file():
        parser.error(f"数据文件不存在：{args.data}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    import bandscope.app.refactored_app as refactored_app

    refactored_app.UpdateController = _NullUpdateController
    app = QApplication.instance() or QApplication(sys.argv)
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    results = []
    warnings = []
    screenshots = []
    requests = []
    original_request_refresh = window.request_refresh

    def tracked_refresh(cause, quality=RenderQuality.EXACT, **kwargs):
        requests.append((cause, quality, dict(kwargs)))
        return original_request_refresh(cause, quality, **kwargs)

    window.request_refresh = tracked_refresh
    render_frames = []
    original_render_active_page = window._render_active_page

    def tracked_render(quality=RenderQuality.EXACT, cause=None):
        result = original_render_active_page(quality, cause)
        context = window.current_render_context or {}
        render_frames.append((quality, cause, context.get("rotation_angle")))
        return result

    window._render_active_page = tracked_render

    def record(name, ok, detail=""):
        results.append({"item": name, "ok": bool(ok), "detail": str(detail)})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)

    def capture(name, widget=None):
        QApplication.processEvents()
        QTest.qWait(250)
        target = widget or window
        path = args.output_dir / name
        if not target.grab().save(str(path)):
            raise AssertionError(f"截图失败：{path}")
        screenshots.append(path.name)
        print(f"[capture] {path}", flush=True)

    def wait_exact(description):
        wait_for(lambda: window._render_exact_ready, description)

    def capture_composite(name, settle_ms=250):
        QApplication.processEvents()
        if settle_ms:
            QTest.qWait(settle_ms)
        rect = window.frameGeometry()
        for widget in (panel, window.crop_controller.popup):
            if widget is not None and widget.isVisible():
                rect = rect.united(widget.frameGeometry())
        screen = QApplication.screenAt(rect.center()) or QApplication.primaryScreen()
        if screen is None:
            raise AssertionError("找不到桌面截图屏幕")
        path = args.output_dir / name
        pixmap = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
        if not pixmap.save(str(path)):
            raise AssertionError(f"桌面区域截图失败：{path}")
        screenshots.append(path.name)
        print(f"[capture] {path}", flush=True)

    def current_data_copy():
        context = window.current_render_context or {}
        data = context.get("data")
        return None if data is None else np.asarray(data).copy()

    def data_changed(before, after):
        if before is None or after is None or before.shape != after.shape:
            return False
        return not np.array_equal(before, after, equal_nan=True)

    def context_at_angle(angle):
        context = window.current_render_context or {}
        return abs(float(context.get("rotation_angle", math.inf)) - float(angle)) < 1e-6

    def rotation_diagnostics():
        context = window.current_render_context or {}
        spec = window.left_workspace.current_spec()
        rotation_panel = window.canvas_tool_overlay.rotation_panel
        return {
            "window.rotation_angle": getattr(window, "rotation_angle", None),
            "panel.current_angle": rotation_panel.current_angle(),
            "context.rotation_angle": context.get("rotation_angle"),
            "_render_exact_ready": getattr(window, "_render_exact_ready", None),
            "_last_refresh_failure": getattr(window, "_last_refresh_failure", None),
            "visible_page_kind": getattr(spec, "page_kind", None),
            "visible_view": context.get("view"),
            "panel_visible": rotation_panel.isVisible(),
            "editor_text": rotation_panel.edit_rotation.text(),
        }

    def commit_angle(angle, description):
        if not panel.isVisible():
            overlay.rotation_button.click()
        editor = panel.edit_rotation.lineEdit()
        panel.activateWindow()
        panel.raise_()
        QApplication.processEvents()
        QTest.mouseClick(editor, Qt.LeftButton, Qt.NoModifier, editor.rect().center())
        QTest.keyClick(editor, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(editor, format(float(angle), ".12g"))
        QTest.keyClick(editor, Qt.Key_Return)
        QApplication.processEvents()
        wait_exact(description)
        wait_for(lambda: context_at_angle(angle), f"{description}上下文角度")

    def slice_fixed_snapshot(spec, selector=None):
        if callable(selector):
            selector = selector()
        context = window.current_render_context or {}
        return {
            "axis_interval": copy.deepcopy(spec.params.get("axis_interval")),
            "normal_indices": tuple(spec.params.get(key) for key in ("low", "up", "mid")),
            "source_angle": copy.deepcopy((spec.params.get("source_3d") or {}).get("rotation_angle")),
            "axes": tuple(window.ax_2d.get_xlim()) + tuple(window.ax_2d.get_ylim()),
            "selector": None if selector is None else tuple(selector.extents),
            "selection_id": window.crop_controller.active_selection_id,
            "data_bounds": copy.deepcopy(context.get("data_bounds")),
        }

    def drag_slice_rotation(label, intermediate_angles, final_angle, fixed_before, fixed_snapshot, screenshot_prefix=None):
        if not panel.isVisible():
            overlay.rotation_button.click()
        previous = current_data_copy()
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
                f"{label} {angle:g}° 数值预览",
                timeout=150,
            )
            previous = current_data_copy()
            fixed_ok = fixed_snapshot() == fixed_before
            record(
                f"{label} {angle:g}° 中间预览更新数据且几何固定",
                fixed_ok and data_changed(before, previous),
                f"angle={window.rotation_angle:g}, exact_ready={window._render_exact_ready}, fixed={fixed_ok}",
            )
            if screenshot_prefix and index == 0:
                capture_composite(f"{screenshot_prefix}-preview.png")
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
        wait_exact(f"{label} {final_angle:g}° 精确旋转")
        wait_for(
            lambda: context_at_angle(final_angle)
            and any(
                quality == RenderQuality.EXACT and cause == RefreshCause.ROTATION
                and rendered_angle is not None and abs(float(rendered_angle) - final_angle) < 1e-6
                for quality, cause, rendered_angle in render_frames[final_frame_marker:]
            ),
            f"{label} 精确上下文角度",
        )
        final_data = current_data_copy()
        fixed_ok = fixed_snapshot() == fixed_before
        record(
            f"{label} {final_angle:g}° 最终精确数据更新且几何固定",
            data_changed(before_final, final_data) and fixed_ok,
            f"angle={window.rotation_angle:g}, exact_ready={window._render_exact_ready}, fixed={fixed_ok}",
        )
        if screenshot_prefix:
            capture_composite(f"{screenshot_prefix}-exact.png")

    def path_snapshot(spec):
        controller = window.path_slice_controller
        artists = []
        for artist in controller._draft_artists:
            if hasattr(artist, "get_offsets"):
                values = np.asarray(artist.get_offsets()).ravel()
            elif hasattr(artist, "get_xydata"):
                values = np.asarray(artist.get_xydata()).ravel()
            elif hasattr(artist, "get_position"):
                values = np.asarray(artist.get_position()).ravel()
            else:
                values = np.asarray([])
            artists.append((type(artist).__name__, tuple(float(value) for value in values)))
        return {
            "nodes": tuple((node.x, node.y, node.label) for node in draft_nodes(spec.params)),
            "artists": tuple(artists),
            "active": controller.active_source_id == spec.page_id,
        }

    def canvas_position(logical):
        physical = (
            window.core.logical_to_physical("X", logical[0]),
            window.core.logical_to_physical("Y", logical[1]),
        )
        x, y = window.ax_2d.transData.transform(physical)
        ratio = window.canvas_2d.devicePixelRatioF()
        height = window.canvas_2d.get_width_height(physical=True)[1]
        return QPointF(x / ratio, (height - y) / ratio).toPoint()

    def vtk_widget_geometry(widget):
        import vtk

        poly = vtk.vtkPolyData()
        returned = widget.GetPolyData(poly)
        if hasattr(returned, "GetPoints"):
            poly = returned
        points = poly.GetPoints()
        values = [] if points is None else [
            coordinate
            for index in range(points.GetNumberOfPoints())
            for coordinate in points.GetPoint(index)
        ]
        return tuple(float(value) for value in (*poly.GetBounds(), *values))

    def exercise_3d_box(label, widget_getter, intermediate_angle, final_angle, screenshot_prefix):
        if not panel.isVisible():
            overlay.rotation_button.click()
        before_data = current_data_copy()
        before_geometry = vtk_widget_geometry(widget_getter())
        rendered_before = float(window._rendered_rotation_angle)
        current_before = float(window.rotation_angle)
        actor_target_before = window.__dict__.get("_actor_preview_rotation_angle")
        blocked_angles = [rendered_before, current_before]
        if actor_target_before is not None:
            blocked_angles.append(float(actor_target_before))
        requested_intermediate = float(intermediate_angle)
        if any(abs(requested_intermediate - angle) < 1e-6 for angle in blocked_angles):
            for candidate in (
                requested_intermediate + 23.0, requested_intermediate - 23.0,
                rendered_before + 23.0, rendered_before - 23.0,
                current_before + 23.0, current_before - 23.0,
            ):
                if -360.0 <= candidate <= 360.0 and all(
                    abs(candidate - angle) >= 1e-6 for angle in blocked_angles
                ):
                    intermediate_angle = float(candidate)
                    break
            else:
                raise AssertionError(f"{label} 找不到与预览前角度不同的合法中间角")
        preview_before = {
            "rendered_angle": rendered_before,
            "current_angle": current_before,
            "dial_angle": float(panel.dial.angle()),
            "actor_target": actor_target_before,
            "actual_orientation": tuple(window.volume_session.volume.GetOrientation()),
            "requested_intermediate_angle": requested_intermediate,
            "intermediate_angle": float(intermediate_angle),
            "expected_delta": float(intermediate_angle) - rendered_before,
        }
        send_dial_mouse(
            panel.dial, QEvent.MouseButtonPress, ring_point(panel.dial, panel.dial.angle()),
            Qt.LeftButton, Qt.LeftButton,
        )
        send_dial_mouse(
            panel.dial, QEvent.MouseMove, ring_point(panel.dial, intermediate_angle),
            Qt.NoButton, Qt.LeftButton,
        )
        wait_for(
            lambda: window.__dict__.get("_actor_preview_rotation_angle") is not None
            and abs(window._actor_preview_rotation_angle - intermediate_angle) < 1e-6,
            f"{label} 体积 Actor 中间预览",
            settle_ms=0,
        )
        preview_geometry = vtk_widget_geometry(widget_getter())
        orientation = tuple(window.volume_session.volume.GetOrientation())
        preview_after = {
            "rendered_angle": float(window._rendered_rotation_angle),
            "current_angle": float(window.rotation_angle),
            "actor_target": window.__dict__.get("_actor_preview_rotation_angle"),
            "actual_orientation": orientation,
            "exact_ready": bool(window._render_exact_ready),
        }
        fixed_preview = np.allclose(before_geometry, preview_geometry, rtol=0.0, atol=1e-9)
        record(
            f"{label} 中间预览盒框固定且体积转动",
            fixed_preview and abs(orientation[2]) > 1e-6,
            {"preview_before": preview_before, "preview_after": preview_after, "fixed": fixed_preview},
        )
        capture_composite(f"{screenshot_prefix}-preview.png", settle_ms=0)
        send_dial_mouse(
            panel.dial, QEvent.MouseMove, ring_point(panel.dial, final_angle),
            Qt.NoButton, Qt.LeftButton,
        )
        send_dial_mouse(
            panel.dial, QEvent.MouseButtonRelease, ring_point(panel.dial, final_angle),
            Qt.LeftButton, Qt.NoButton,
        )
        wait_exact(f"{label} 最终精确旋转")
        wait_for(lambda: context_at_angle(final_angle), f"{label} 精确上下文角度")
        final_geometry = vtk_widget_geometry(widget_getter())
        final_orientation = tuple(window.volume_session.volume.GetOrientation())
        fixed = np.allclose(before_geometry, final_geometry, rtol=0.0, atol=1e-9)
        record(
            f"{label} 精确刷新盒框固定、数据更新且 Actor 无回弹",
            fixed
            and data_changed(before_data, current_data_copy())
            and np.allclose(final_orientation, (0.0, 0.0, 0.0), atol=1e-9)
            and abs(window._rendered_rotation_angle - final_angle) < 1e-6,
            f"orientation={final_orientation}, rendered={window._rendered_rotation_angle:g}, fixed={fixed}",
        )
        capture_composite(f"{screenshot_prefix}-exact.png")

    try:
        window.showNormal()
        window.resize(1500, 920)
        window.show()
        window.load_data(str(args.data))
        wait_for(
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "3d"
            and window._render_exact_ready,
            "三维主页",
        )
        home_source = window.left_workspace.current_spec()
        overlay = window.canvas_tool_overlay
        panel = overlay.rotation_panel
        record(
            "系统缩放与原生画布工具栏",
            overlay.isVisible() and all(
                button.isVisible() for button in (overlay.switch_axes, overlay.switch_flip, overlay.rotation_button)
            ),
            f"DPR={window.devicePixelRatioF():g}, QT_SCALE_FACTOR={os.environ.get('QT_SCALE_FACTOR', 'auto')}",
        )
        overlay.switch_axes.click()
        overlay.switch_flip.click()
        record(
            "坐标和翻转图标开关",
            overlay.switch_axes.isChecked() and overlay.switch_flip.isChecked(),
        )
        overlay.switch_axes.click()
        overlay.switch_flip.click()
        wait_exact("开关恢复")

        overlay.rotation_button.click()
        record(
            "旋钮弹层位于按钮左侧且使用原生窗口",
            panel.isVisible()
            and panel.windowHandle() is not None
            and panel.x() + panel.width() <= overlay.rotation_button.mapToGlobal(QPoint(0, 0)).x(),
        )
        commit_angle(-7.5, "负小数精确旋转")
        record(
            "负小数同步指针读数和提示",
            abs(panel.dial.angle() + 7.5) < 1e-6
            and abs(window.rotation_angle + 7.5) < 1e-6
            and "−7.5°" in overlay.rotation_button.toolTip(),
            panel.edit_rotation.text(),
        )
        capture("rotation-popup.png", panel)

        requests.clear()
        send_dial_mouse(panel.dial, QEvent.MouseButtonPress, ring_point(panel.dial, -10), Qt.LeftButton, Qt.LeftButton)
        send_dial_mouse(panel.dial, QEvent.MouseMove, ring_point(panel.dial, 0), Qt.NoButton, Qt.LeftButton)
        send_dial_mouse(panel.dial, QEvent.MouseMove, ring_point(panel.dial, 10), Qt.NoButton, Qt.LeftButton)
        record("拖动期间没有精确提交", not any(
            cause == RefreshCause.ROTATION and quality == RenderQuality.EXACT
            and not kwargs.get("interactive", False)
            for cause, quality, kwargs in requests
        ))
        send_dial_mouse(panel.dial, QEvent.MouseButtonRelease, ring_point(panel.dial, 10), Qt.LeftButton, Qt.NoButton)
        wait_exact("拖动松手精确旋转")
        exact_count = sum(
            cause == RefreshCause.ROTATION and quality == RenderQuality.EXACT
            and not kwargs.get("interactive", False)
            for cause, quality, kwargs in requests
        )
        record(
            "跨零拖动只在松手提交一次",
            exact_count == 1 and window.rotation_angle > 0,
            f"angle={window.rotation_angle:g}, exact={exact_count}",
        )

        QTest.mouseClick(
            overlay.rotation_button, Qt.LeftButton, Qt.NoModifier,
            overlay.rotation_button.rect().center(),
        )
        QApplication.processEvents()
        record(
            "非零角度下真实点击收起弹层并熄灭高亮",
            not panel.isVisible() and not overlay.rotation_button.isChecked(),
            f"visible={panel.isVisible()}, checked={overlay.rotation_button.isChecked()}, "
            f"suppressed={overlay._suppress_rotation_click}",
        )
        overlay.rotation_button.click()
        record(
            "重新展开非零角度弹层并恢复高亮",
            panel.isVisible() and overlay.rotation_button.isChecked(),
        )

        camera_before = copy.deepcopy(window._current_3d_camera_position())
        panel.zero_button.click()
        wait_exact("归零精确旋转")
        record(
            "归零保持弹层与高亮且相机不变",
            window.rotation_angle == 0.0
            and panel.isVisible()
            and overlay.rotation_button.isChecked()
            and np.allclose(window._current_3d_camera_position(), camera_before),
            f"angle={window.rotation_angle:g}, visible={panel.isVisible()}, "
            f"checked={overlay.rotation_button.isChecked()}",
        )
        QTest.mouseClick(
            overlay.rotation_button, Qt.LeftButton, Qt.NoModifier,
            overlay.rotation_button.rect().center(),
        )
        QApplication.processEvents()
        record(
            "再次点击 Z 收起弹层并熄灭高亮",
            not panel.isVisible() and not overlay.rotation_button.isChecked(),
            f"visible={panel.isVisible()}, checked={overlay.rotation_button.isChecked()}, "
            f"suppressed={overlay._suppress_rotation_click}",
        )

        commit_angle(30.0, "切片创建前 30° 精确旋转")
        overlay.close_rotation_panel(commit=False)
        overlay.preset_buttons["xy"].click()
        wait_for(
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "2d"
            and window._render_exact_ready,
            "XY 切片",
        )
        axis_source = window.left_workspace.current_spec()
        record(
            "XY 切片继承创建时 30° 并进入实时旋转验收",
            axis_source.page_kind == "axis_integral"
            and abs(window.rotation_angle - 30.0) < 1e-6
            and context_at_angle(30.0),
            f"page={axis_source.page_kind}, angle={window.rotation_angle:g}",
        )

        overlay.selection_button.click()
        wait_for(lambda: window.axis_crop_selector is not None, "切片框选选区")
        selection_id = window.crop_controller.active_selection_id
        selection_fixed = slice_fixed_snapshot(
            axis_source, lambda: window.axis_crop_selectors.get(selection_id)
        )
        overlay.rotation_button.click()
        record(
            "先框选后旋转两者保持可用",
            panel.isVisible()
            and overlay.selection_panel.isVisible()
            and window.crop_controller.enabled
            and window.axis_crop_selector is not None,
        )
        drag_slice_rotation(
            "框选共存", (0.0, -18.0, 12.0), 42.0,
            selection_fixed,
            lambda: slice_fixed_snapshot(
                axis_source, lambda: window.axis_crop_selectors.get(selection_id)
            ),
            "rotation-selection",
        )

        overlay.close_rotation_panel(commit=False)
        overlay.selection_button.click()
        overlay.rotation_button.click()
        overlay.selection_button.click()
        wait_for(lambda: window.axis_crop_selector is not None, "第二顺序框选选区")
        record(
            "先旋转后框选两者保持可用",
            panel.isVisible()
            and overlay.selection_panel.isVisible()
            and window.crop_controller.enabled
            and window.crop_controller.active_selection_id == selection_id,
        )
        capture_composite("rotation-first-selection-second.png")

        for operation, button, angle in (
            ("crop", window.btn_tb_crop, 24.0),
            ("erase", window.btn_tb_erase, -12.0),
        ):
            overlay.close_rotation_panel(commit=False)
            if not button.isChecked():
                button.click()
            wait_for(lambda: window.crop_controller.popup.isVisible(), f"{operation} 范围浮窗")
            overlay.rotation_button.click()
            record(
                f"先{operation}后旋转浮窗与选框共存",
                panel.isVisible()
                and window.crop_controller.popup.isVisible()
                and window.crop_controller.operation == operation
                and window.axis_crop_selector is not None,
            )
            operation_fixed = slice_fixed_snapshot(
                axis_source, lambda: window.axis_crop_selectors.get(selection_id)
            )
            commit_angle(angle, f"{operation} 共存精确旋转")
            record(
                f"{operation} 旋转后范围浮窗和选框几何保持",
                panel.isVisible()
                and window.crop_controller.popup.isVisible()
                and slice_fixed_snapshot(
                    axis_source, lambda: window.axis_crop_selectors.get(selection_id)
                ) == operation_fixed,
            )
            capture_composite(f"rotation-{operation}-tool-first.png")

            overlay.close_rotation_panel(commit=False)
            button.click()
            overlay.rotation_button.click()
            button.click()
            wait_for(lambda: window.crop_controller.popup.isVisible(), f"旋转先开 {operation} 浮窗")
            record(
                f"先旋转后{operation}浮窗与选框共存",
                panel.isVisible()
                and window.crop_controller.popup.isVisible()
                and window.crop_controller.operation == operation
                and window.axis_crop_selector is not None,
            )
            capture_composite(f"rotation-{operation}-rotation-first.png")
            button.click()

        overlay.selection_button.click()
        if not panel.isVisible():
            overlay.rotation_button.click()
        window._add_enhancement_region()
        wait_exact("局部增强区域精确刷新")
        wait_for(lambda: window._enhancement_selector is not None, "局部增强二维框")
        enhancement = window._enhancement_selected()
        record(
            "局部增强与旋转浮窗共存",
            enhancement is not None
            and window._enhancement_selector.get_visible()
            and panel.isVisible(),
        )
        enhancement_fixed = dict(
            slice_fixed_snapshot(axis_source, lambda: window._enhancement_selector),
            enhancement=tuple(enhancement["bounds"]),
            enhancement_id=enhancement["region_id"],
        )

        def current_enhancement_fixed():
            current = window._enhancement_selected()
            return dict(
                slice_fixed_snapshot(axis_source, lambda: window._enhancement_selector),
                enhancement=None if current is None else tuple(current["bounds"]),
                enhancement_id=None if current is None else current["region_id"],
            )

        drag_slice_rotation(
            "局部增强共存", (-6.0,), 26.0,
            enhancement_fixed, current_enhancement_fixed, "rotation-enhancement",
        )
        window._delete_enhancement_region(enhancement["region_id"])
        wait_exact("删除局部增强区域")

        write_draft(axis_source.params, [])
        if not panel.isVisible():
            overlay.rotation_button.click()
        window.path_slice_controller.button.click()
        nx, ny = window.core.raw_data.shape[:2]
        for logical in ((0.20 * nx, 0.35 * ny), (0.80 * nx, 0.35 * ny), (0.65 * nx, 0.75 * ny)):
            QTest.mouseClick(window.canvas_2d, Qt.LeftButton, Qt.NoModifier, canvas_position(logical))
            QApplication.processEvents()
        wait_for(lambda: len(draft_nodes(axis_source.params)) == 3, "三个真实路径节点")
        record(
            "路径选点与旋转浮窗共存",
            window.path_slice_controller.active_source_id == axis_source.page_id
            and panel.isVisible()
            and len(window.path_slice_controller._draft_artists) >= 3,
        )
        path_fixed = {
            "path": path_snapshot(axis_source),
            "slice": slice_fixed_snapshot(axis_source),
        }
        drag_slice_rotation(
            "路径选点共存", (4.0,), 32.0, path_fixed,
            lambda: {"path": path_snapshot(axis_source), "slice": slice_fixed_snapshot(axis_source)},
            "rotation-path-draft",
        )
        record(
            "连续旋转后路径节点和草稿艺术家未重置",
            path_snapshot(axis_source) == path_fixed["path"]
            and window.path_slice_controller.active_source_id == axis_source.page_id,
            f"nodes={len(draft_nodes(axis_source.params))}, artists={len(window.path_slice_controller._draft_artists)}",
        )
        window.path_slice_controller.button.click()

        window.resize(1500, 920)
        QApplication.processEvents()
        QTest.qWait(500)
        bar = window.timeline_bar
        record(
            "宽窗位置与时间等分底栏",
            not bar._wrapped
            and bar.axis_group.isVisible()
            and bar.timeline_group.isVisible()
            and abs(bar.axis_group.width() - bar.timeline_group.width()) <= 2
            and not hasattr(bar, "view_controls"),
            f"position={bar.axis_group.width()}, time={bar.timeline_group.width()}",
        )
        capture("slice-wide.png")

        window.resize(1050, 760)
        QApplication.processEvents()
        QTest.qWait(500)
        if not bar._wrapped:
            narrow = max(900, window.width() - bar.width() + bar._groups_min_width() + 28)
            window.resize(narrow, 760)
            QTest.qWait(500)
        record(
            "窄窗只换成两行",
            bar._wrapped
            and bar.secondary_row.isAncestorOf(bar.timeline_group)
            and bar.height() == bar.HEIGHT + bar.SECOND_ROW_HEIGHT,
            f"window={window.width()}x{window.height()}",
        )
        capture("slice-narrow.png")

        window.resize(1300, 540)
        QApplication.processEvents()
        QTest.qWait(500)
        record(
            "低高度收起视角预设且底部操作可达",
            overlay.preset_container.isHidden()
            and overlay.export_button.isVisible()
            and overlay.focus_button.isVisible()
            and overlay.y() + overlay.height() <= window.left_display_stack.height(),
            f"canvas={window.left_display_stack.width()}x{window.left_display_stack.height()}, "
            f"overlay={overlay.y()}..{overlay.y() + overlay.height()}, "
            f"presets_hidden={overlay.preset_container.isHidden()}, "
            f"export={overlay.export_button.isVisible()}, focus={overlay.focus_button.isVisible()}",
        )
        capture("slice-low-height.png")

        window.resize(1400, 900)
        QApplication.processEvents()
        QTest.qWait(400)
        write_draft(axis_source.params, [
            PathNode(1, 1, "Γ"), PathNode(3, 1, "K"), PathNode(3, 3, "M"),
        ])
        window.path_slice_controller.button.click()
        window.path_slice_controller.redraw_draft()
        page_id = window.path_slice_controller.generate_page()
        if page_id is None:
            raise AssertionError("无法生成路径切片")
        wait_for(
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "path_slice"
            and window.path_slice_controller.bar.isVisible(),
            "路径切片与选段条",
        )
        if not window._render_exact_ready:
            warning = "路径页显示统计任务持续重排；本布局验收不等待全局精确空闲。"
            warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        path_bar = window.path_slice_controller.bar
        record(
            "路径选段条独立于位置时间底栏",
            path_bar.isVisible()
            and window.left_workspace.content_layout.indexOf(path_bar)
            < window.left_workspace.content_layout.indexOf(bar),
        )
        capture("path-segment-bar.png")
        window.left_workspace.activate_page(axis_source.page_id)
        wait_exact("返回 XY 切片")
        record("离开路径页后选段条独立隐藏", path_bar.isHidden() and bar.isVisible())

        window.left_workspace.activate_page(home_source.page_id)
        wait_for(
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "3d"
            and window._render_exact_ready,
            "返回三维盒框共存验收",
        )
        window.btn_tb_crop.click()
        wait_for(lambda: bool(window._crop_box_widgets), "三维裁剪盒")
        crop_box_id = window.crop_controller.active_selection_id
        if not panel.isVisible():
            overlay.rotation_button.click()
        record(
            "三维裁剪盒与旋转浮窗共存",
            crop_box_id in window._crop_box_widgets
            and window.crop_controller.popup.isVisible()
            and panel.isVisible(),
        )
        exercise_3d_box(
            "三维裁剪盒", lambda: window._crop_box_widgets[crop_box_id],
            0.0, 18.0, "rotation-3d-crop-box",
        )

        window.btn_tb_crop.click()
        if not panel.isVisible():
            overlay.rotation_button.click()
        window._add_enhancement_region()
        wait_exact("三维局部增强精确刷新")
        wait_for(lambda: window._enhancement_box_widget is not None, "三维局部增强盒")
        record("三维增强盒与旋转浮窗共存", panel.isVisible())
        exercise_3d_box(
            "三维局部增强盒", lambda: window._enhancement_box_widget,
            -10.0, 8.0, "rotation-3d-enhancement-box",
        )

        overlay.show_rotation_panel()
        capture("rotation-popup-native.png", panel)
        screen = QApplication.screenAt(panel.frameGeometry().center())
        if screen is not None:
            native_path = args.output_dir / "rotation-popup-desktop.png"
            rect = panel.frameGeometry()
            if not screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height()).save(str(native_path)):
                raise AssertionError(f"原生弹层桌面截图失败：{native_path}")
            screenshots.append(native_path.name)
        overlay.close_rotation_panel(commit=False)
        if args.hold:
            app.exec()
    except Exception as exc:
        detail = (
            f"{type(exc).__name__}: {exc}; diagnostics={rotation_diagnostics()}; "
            f"roi_job={window.__dict__.get('_enhancement_roi_job')}; "
            f"stats_job={window.__dict__.get('_display_stats_job')}"
        )
        record("验收步骤异常", False, detail)
        traceback.print_exc()
    finally:
        report = {
            "data": str(args.data),
            "platform": os.environ.get("QT_QPA_PLATFORM"),
            "device_pixel_ratio": window.devicePixelRatioF(),
            "qt_scale_factor": os.environ.get("QT_SCALE_FACTOR"),
            "results": results,
            "warnings": warnings,
            "screenshots": sorted(screenshots),
            "automatic_screenshots_only": True,
            "not_executed": [
                "维护者人工视觉复核（框线可读性、残影、浮窗避让与桌面缩放观感）",
                "维护者真实鼠标拖动手感与流畅度认可",
                "科研出图质量认可",
            ],
        }
        (args.output_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        window.canvas_tool_overlay.close_rotation_panel(commit=False)
        window.close()
        QApplication.processEvents()
    return 1 if any(not item["ok"] for item in results) else 0


if __name__ == "__main__":
    sys.exit(main())
