"""Real-window acceptance for canvas axis slices and the dual-ended footer.

Usage: .venv/Scripts/python.exe scripts/validation/verify_canvas_axis_slices.py [data.npz]
Defaults to .local/data/WSe2_step.npz and writes .local/outputs/canvas-axis-slices/.
"""
from __future__ import annotations

import argparse
import json
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

from PyQt5.QtCore import QSettings
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

QSettings.setDefaultFormat(QSettings.IniFormat)
for _scope in (QSettings.UserScope, QSettings.SystemScope):
    QSettings.setPath(QSettings.IniFormat, _scope, _SANDBOX.name)


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

    try:
        window.load_data(str(args.data))
        wait_for(
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "3d"
            and window._render_exact_ready,
            "三维主页",
        )
        source = window.left_workspace.current_spec()
        window.canvas_tool_overlay.preset_buttons["xy"].click()
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
        record("同方向重复建页", window.left_workspace.current_spec().page_id != spec.page_id)
        report = {
            "data": str(args.data),
            "platform": os.environ.get("QT_QPA_PLATFORM"),
            "results": results,
            "screenshots": sorted(path.name for path in args.output_dir.glob("*.png")),
        }
        (args.output_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        if not all(item["ok"] for item in results):
            raise AssertionError("存在未通过的验收项")
        if args.hold:
            print("[hold] 验收窗口已保留，关闭窗口退出", flush=True)
            app.exec_()
        window.close()
    except Exception:
        report = {"data": str(args.data), "results": results}
        (args.output_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        window.close()
        raise


if __name__ == "__main__":
    main()
