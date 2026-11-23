"""Real-window acceptance for automatic scan axes and source-level reassignment.

Usage: .venv/Scripts/python.exe scripts/validation/verify_axis_assignment.py [WSe2.npz]
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

_SANDBOX = tempfile.TemporaryDirectory(prefix="bandscope-axis-assignment-")
os.environ["BANDSCOPE_EXTENSION_ROOT"] = str(Path(_SANDBOX.name) / "extensions")

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QSettings, QTimer
from PyQt5.QtWidgets import QApplication, QDialogButtonBox

QSettings.setDefaultFormat(QSettings.IniFormat)
for _scope in (QSettings.UserScope, QSettings.SystemScope):
    QSettings.setPath(QSettings.IniFormat, _scope, _SANDBOX.name)

import numpy as np

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
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / ".local/outputs/axis-assignment")
    parser.add_argument("--synthetic-only", action="store_true")
    args = parser.parse_args()
    if not args.synthetic_only and not args.data.is_file():
        parser.error(f"数据文件不存在：{args.data}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    import bandscope.app.refactored_app as refactored_app
    from bandscope.exporting.publication_export import capture_snapshot, render_and_save
    from bandscope.exporting.publication_models import OutputOptions, resolve_style
    from bandscope.ui.axis_assignment_dialog import AxisAssignmentDialog

    refactored_app.UpdateController = _NullUpdateController
    app = QApplication.instance() or QApplication(sys.argv)
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.resize(1500, 920)
    window.show()
    messages = []
    window._show_message = lambda title, text, *a, **k: messages.append((title, text))
    state = {"finished": False}
    artifacts = {}

    def capture(name, widget=None):
        target = args.output_dir / name
        if not (widget or window).grab().save(str(target)):
            raise AssertionError(f"截图失败：{target}")
        artifacts[name] = str(target)

    def finish():
        if state["finished"]:
            return
        state["finished"] = True
        report = {
            "data": str(args.data),
            "wse2_executed": not args.synthetic_only,
            "platform": os.environ.get("QT_QPA_PLATFORM"),
            "checks": RESULTS,
            "messages": messages,
            "artifacts": artifacts,
            "passed": bool(RESULTS) and all(item["ok"] for item in RESULTS),
        }
        (args.output_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        window.close()
        app.exit(0 if report["passed"] else 1)

    def guard(callback):
        try:
            callback()
        except Exception:
            record("验收步骤异常", False, traceback.format_exc())
            traceback.print_exc()
            finish()

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
                guard(next_step)

        poll()

    def active_dialog():
        dialog = QApplication.activeModalWidget()
        return dialog if isinstance(dialog, AxisAssignmentDialog) else None

    def step_wse_loaded():
        core = window.core
        record("WSe₂ 自动识别为时间扫描", core.is_time_axis, core.scan_kind)
        record("WSe₂ 时间单位兼容", window.time_space.unit == "fs", window.time_space.unit)
        record("WSe₂ 帧数", window.timeline_bar.slider_time.maximum() == 24,
               window.core.raw_data.shape)
        record("时间数据源卡片摘要", "时间" in window.left_workspace.page_tree.source_meta.text(),
               window.left_workspace.page_tree.source_meta.text())
        capture("wse2-time.png")
        QTimer.singleShot(400, check_wse_dialog)
        window.left_workspace.page_tree.source_card.activated.emit()
        QTimer.singleShot(0, step_temperature_load)

    def check_wse_dialog():
        dialog = active_dialog()
        record("WSe₂ 卡片打开原始轴窗口", dialog is not None)
        if dialog is None:
            return
        record("WSe₂ 窗口显示原始维度", "150 × 150 × 200 × 25" in dialog.shape_label.text(),
               dialog.shape_label.text())
        capture("wse2-axis-dialog.png", dialog)
        dialog.reject()

    def step_temperature_load():
        if not args.synthetic_only:
            record("WSe₂ 取消重设保留来源", len(window.source_manager) == 1 and window.core.is_time_axis)
        raw = np.arange(3 * 2 * 4 * 4, dtype=np.float32).reshape(3, 2, 4, 4)
        path = args.output_dir / "temperature-input.npz"
        np.savez(
            path,
            sample=raw,
            kx=np.arange(4),
            ky=np.arange(4),
            E=np.arange(2),
            temperature=np.asarray([300.0, 180.0, 100.0]),
            temperature_unit=np.asarray(["K"]),
            axis_order=np.asarray(["T", "E", "X", "Y"]),
        )
        window.load_data(str(path))
        wait_for(
            "温度主页渲染",
            lambda: window.core.scan_kind == "temperature"
            and window.current_render_context is not None,
            step_temperature_dialog,
        )

    def step_temperature_dialog():
        record("温度扫描自动识别", window.core.scan_kind == "temperature", window.core.scan_kind)
        record("扫描底条使用实际名称",
               window.timeline_bar.time_interval_control.title_label.text() == "温度",
               window.timeline_bar.time_interval_control.title_label.text())
        record("降序坐标初始帧仍为第 0 帧", window.timeline_bar.slider_time.value() == 0,
               window.core.coords["delay"].tolist())
        record("等长 Kx/Ky 给出待确认说明",
               any("等长" in item for item in window.core.axis_assignment.warnings),
               window.core.axis_assignment.warnings)
        capture("temperature-automatic.png")
        QTimer.singleShot(400, edit_temperature_dialog)
        window.left_workspace.page_tree.source_card.activated.emit()
        QTimer.singleShot(0, step_temperature_reassigned)

    def edit_temperature_dialog():
        dialog = active_dialog()
        record("温度卡片打开重设窗口", dialog is not None)
        if dialog is None:
            return
        dialog._rows["X"]["dimension"].setCurrentIndex(
            dialog._rows["X"]["dimension"].findData(3)
        )
        dialog._rows["Y"]["dimension"].setCurrentIndex(
            dialog._rows["Y"]["dimension"].findData(2)
        )
        dialog.scan_name.setText("样品温度")
        dialog.scan_unit.setText("K")
        record("重设窗口提供扫描轴滑块设置",
               dialog.docking_combo.isEnabled() and dialog.pan_combo.isEnabled()
               and "仅作用于扫描轴" in dialog.drag_scope_label.text())
        dialog.docking_combo.setCurrentIndex(dialog.docking_combo.findData("continuous"))
        dialog.pan_combo.setCurrentIndex(dialog.pan_combo.findData("frames"))
        capture("temperature-axis-dialog.png", dialog)
        dialog.button_box.button(QDialogButtonBox.Ok).click()

    def step_temperature_reassigned():
        wait_for(
            "温度重设渲染",
            lambda: window.core.scan_name == "样品温度"
            and window.current_render_context is not None,
            step_curve,
        )

    def step_curve():
        drag = window.source_manager.active().fourth_axis_drag
        record("轴重设应用扫描滑块设置",
               drag.docking == "continuous" and drag.continuous_pan == "frames",
               drag)
        expected = np.arange(3 * 2 * 4 * 4, dtype=np.float32).reshape(3, 2, 4, 4).transpose(3, 2, 1, 0)
        record("卡片重设交换原始维度", np.array_equal(window.core.raw_data, expected),
               window.core.axis_assignment.summary())
        expected_sources = 1 if args.synthetic_only else 2
        record("只有主页时保留当前来源", len(window.source_manager) == expected_sources, len(window.source_manager))
        home = window.left_workspace.home_spec()
        window.clip_ranges = [0, 3, 0, 3, 0, 1]
        home.params["clip_ranges"] = list(window.clip_ranges)
        page_id = window._on_page_create_requested(home.page_id, "builtin:slice_dos")
        record("温度扫描创建强度曲线", page_id is not None)
        if page_id is not None:
            window.left_workspace.activate_page(page_id)
        wait_for(
            "温度曲线",
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "1d",
            step_exports,
        )

    def step_exports():
        context = window.current_render_context
        spec = window.left_workspace.current_spec()
        expected = np.arange(3 * 2 * 4 * 4, dtype=np.float32).reshape(3, 2, 4, 4).transpose(3, 2, 1, 0)
        record("曲线使用实际扫描名称和单位", context.get("xlabel") == "样品温度 (K)",
               context.get("xlabel"))
        np.testing.assert_allclose(context["y_data"], expected.sum(axis=(0, 1, 2)))
        _title, _name, payload = window._build_slice_dos_export_payload(
            spec, window.core.raw_data, window.core.coords
        )
        record("数值导出包含扫描元数据",
               payload.get("scan_type", [""])[0] == "temperature"
               and payload.get("scan_name", [""])[0] == "样品温度"
               and payload.get("scan_unit", [""])[0] == "K"
               and "time" not in payload)
        numeric_path = args.output_dir / "temperature-curve.npz"
        window._save_dict_to_path(str(numeric_path), payload)
        with np.load(numeric_path, allow_pickle=False) as exported:
            np.testing.assert_allclose(exported["scan_coordinates"], [300.0, 180.0, 100.0])
            np.testing.assert_allclose(exported["intensity"], expected.sum(axis=(0, 1, 2)))
        artifacts[numeric_path.name] = str(numeric_path)

        snapshot = capture_snapshot(window)
        image_path = args.output_dir / "temperature-curve.png"
        options = OutputOptions(dpi=120)
        render_and_save(snapshot, resolve_style("1d", None), {}, options, str(image_path))
        record("出版图实际写出", image_path.stat().st_size > 0, snapshot.payload.get("xlabel"))
        artifacts[image_path.name] = str(image_path)
        capture("temperature-curve-window.png")
        finish()

    if args.synthetic_only:
        QTimer.singleShot(500, lambda: guard(step_temperature_load))
    else:
        QTimer.singleShot(500, lambda: (window.load_data(str(args.data)), wait_for(
            "WSe₂ 主页渲染",
            lambda: window.current_render_context is not None,
            step_wse_loaded,
        )))
    QTimer.singleShot(240000, lambda: (record("总验收超时", False), finish()))
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
