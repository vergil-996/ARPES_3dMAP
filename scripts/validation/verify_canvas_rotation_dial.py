"""Real-window acceptance for the canvas view tools, rotation dial and footer."""

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

from bandscope.core.path_slice import PathNode, write_draft
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
            QTest.qWait(250)
            return
        QTest.qWait(150)
    raise AssertionError(f"等待{description}超时")


def ring_point(dial, angle):
    center = dial.rect().center()
    radians = math.radians(angle)
    radius = dial._ring_geometry()[2]
    return QPoint(
        round(center.x() + math.sin(radians) * radius),
        round(center.y() - math.cos(radians) * radius),
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
        panel.edit_rotation.lineEdit().setFocus()
        panel.edit_rotation.lineEdit().selectAll()
        QTest.keyClicks(panel.edit_rotation.lineEdit(), "-7.5")
        QTest.keyClick(panel.edit_rotation.lineEdit(), Qt.Key_Return)
        wait_exact("负小数精确旋转")
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

        camera_before = copy.deepcopy(window._current_3d_camera_position())
        panel.zero_button.click()
        wait_exact("归零精确旋转")
        record(
            "归零熄灭高亮且相机不变",
            window.rotation_angle == 0.0
            and not overlay.rotation_button.isChecked()
            and np.allclose(window._current_3d_camera_position(), camera_before),
        )
        overlay.rotation_button.click()
        record("再次点击 Z 收起弹层", not panel.isVisible())

        overlay.preset_buttons["xy"].click()
        wait_for(
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "2d"
            and window._render_exact_ready,
            "XY 切片",
        )
        axis_source = window.left_workspace.current_spec()
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
        context = window.current_render_context or {}
        spec = window.active_page_spec
        detail = (
            f"{type(exc).__name__}: {exc}; page={getattr(spec, 'page_kind', None)}; "
            f"view={context.get('view')}; failure={window._last_refresh_failure}; "
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
