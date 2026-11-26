"""Real-window acceptance for two-point Kx/Ky momentum calibration.

Usage: .venv/Scripts/python.exe scripts/validation/verify_momentum_calibration.py [data.npz]
Defaults to .local/data/WSe2_step.npz and writes .local/outputs/momentum-calibration/.
Use --synthetic-only to run without the local experimental dataset.
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
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "xcb")

_SANDBOX = tempfile.TemporaryDirectory(prefix="bandscope-momentum-calibration-")
os.environ["BANDSCOPE_EXTENSION_ROOT"] = str(Path(_SANDBOX.name) / "extensions")

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QPointF, QSettings, Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDoubleSpinBox, QFileDialog, QLineEdit, QPushButton

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


def _write_synthetic(path):
    x = np.asarray([-0.50, -0.20, 0.05, 0.30, 0.90], dtype=np.float64)
    y = np.asarray([0.80, 0.35, 0.10, -0.05, -0.40, -0.90], dtype=np.float64)
    energy = np.asarray([-1.0, -0.4, 0.0, 0.2], dtype=np.float64)
    delay = np.asarray([0.0, 200.0, 500.0], dtype=np.float64)
    xx, yy, ee, tt = np.meshgrid(x, y, energy, delay, indexing="ij")
    raw = (
        np.exp(-((xx - 0.12) ** 2 + (yy + 0.08) ** 2) / 0.08)
        * (1.0 + 0.15 * np.cos(ee * np.pi) + 0.002 * tt)
    ).astype(np.float32)
    np.savez(
        path,
        sample=raw,
        kx=x,
        ky=y,
        E=energy,
        time=delay,
        kx_unit=np.asarray("Å⁻¹"),
        ky_unit=np.asarray("Å⁻¹"),
        E_unit=np.asarray("eV"),
        time_unit=np.asarray("fs"),
        axis_order=np.asarray(["X", "Y", "E", "T"]),
    )


def _physical_point(core, point):
    return (
        float(np.interp(point[0], np.arange(core.coords["X"].size), core.coords["X"])),
        float(np.interp(point[1], np.arange(core.coords["Y"].size), core.coords["Y"])),
    )


def _source_distance(core, point_a, point_b):
    physical_a = _physical_point(core, point_a)
    physical_b = _physical_point(core, point_b)
    return float(np.hypot(
        physical_b[0] - physical_a[0],
        physical_b[1] - physical_a[1],
    ))


def _named_widget(dialog, names, widget_type):
    for name in names:
        widget = getattr(dialog, name, None)
        if isinstance(widget, widget_type):
            return widget
    return None


def _find_button(dialog, names, text):
    button = _named_widget(dialog, names, QPushButton)
    if button is not None:
        return button
    for candidate in dialog.findChildren(QPushButton):
        label = f"{candidate.text()} {candidate.accessibleName()}"
        if text in label:
            return candidate
    raise AssertionError(f"未找到「{text}」按钮")


def _find_edit(dialog, names, keywords, excluded=()):
    edit = _named_widget(dialog, names, QLineEdit)
    if edit is not None:
        return edit
    for candidate in dialog.findChildren(QLineEdit):
        label = f"{candidate.accessibleName()} {candidate.placeholderText()}"
        if any(word in label for word in keywords) and not any(
            word in label for word in excluded
        ):
            return candidate
    raise AssertionError(f"未找到输入框：{keywords}")


def _find_canvas(dialog):
    canvas = _named_widget(dialog, ("canvas", "plot_canvas"), object)
    if canvas is None:
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg

        canvases = [item for item in dialog.findChildren(FigureCanvasQTAgg) if item.figure.axes]
        if not canvases:
            raise AssertionError("定标窗口未显示 XY 画布")
        canvas = canvases[0]
    axes = getattr(dialog, "ax", None) or getattr(dialog, "axes", None)
    if axes is None:
        axes = canvas.figure.axes[0]
    return canvas, axes


def main():
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", nargs="?", type=Path, default=REPO_ROOT / ".local/data/WSe2_step.npz")
    parser.add_argument(
        "--output-dir", type=Path, default=REPO_ROOT / ".local/outputs/momentum-calibration"
    )
    parser.add_argument("--synthetic-only", action="store_true")
    args = parser.parse_args()
    if not args.synthetic_only and not args.data.is_file():
        parser.error(f"数据文件不存在：{args.data}；可用 --synthetic-only 运行合成数据验收")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    data_path = args.data
    if args.synthetic_only:
        data_path = args.output_dir / "synthetic-input.npz"
        _write_synthetic(data_path)

    import bandscope.app.refactored_app as refactored_app
    from bandscope.core.analyzer_core import AnalyzerCore
    from bandscope.ui import theme
    from bandscope.ui.axis_assignment_dialog import AxisAssignmentDialog
    from bandscope.ui.axis_interval_bar import DualRangeSlider
    from bandscope.ui.momentum_calibration_dialog import (
        CalibrationSliceSlider,
        MomentumCalibrationDialog,
    )

    refactored_app.UpdateController = _NullUpdateController
    app = QApplication.instance() or QApplication(sys.argv)
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.resize(1500, 920)
    window.show()
    messages = []
    window._show_message = lambda title, text, *a, **k: messages.append((title, text))
    artifacts = {}
    executed = []
    not_executed = [
        {"item": "截图人工视觉复核", "reason": "脚本只生成截图，不代替人工检查中性深浅主题与布局。"},
    ]
    state = {
        "finished": False,
        "roundtrip": False,
        "apply_signals": [],
        "save_requests": 0,
    }

    def capture(name, widget=None):
        target = args.output_dir / name
        app.processEvents()
        if not (widget or window).grab().save(str(target)):
            raise AssertionError(f"截图失败：{target}")
        artifacts[name] = str(target)
        print(f"[capture] {target}", flush=True)

    def finish():
        if state["finished"]:
            return
        state["finished"] = True
        if not state["roundtrip"]:
            not_executed.append({
                "item": "完整 NPZ/MAT 保存回读",
                "reason": "流程在完成前结束；详见失败检查与 messages。",
            })
        report = {
            "data": str(data_path),
            "synthetic_only": bool(args.synthetic_only),
            "platform": os.environ.get("QT_QPA_PLATFORM"),
            "executed": executed,
            "not_executed": not_executed,
            "checks": RESULTS,
            "messages": messages,
            "artifacts": artifacts,
            "passed": bool(RESULTS) and all(item["ok"] for item in RESULTS) and state["roundtrip"],
        }
        (args.output_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[result] {'PASS' if report['passed'] else 'FAIL'}", flush=True)
        for widget in QApplication.topLevelWidgets():
            if widget.isVisible():
                widget.close()
        app.exit(0 if report["passed"] else 1)

    def guard(callback):
        try:
            callback()
        except Exception:
            record("验收步骤异常", False, traceback.format_exc())
            traceback.print_exc()
            finish()

    def wait_for(description, predicate, next_step, timeout_ms=30000):
        deadline = time.perf_counter() + timeout_ms / 1000.0

        def poll():
            if state["finished"]:
                return
            try:
                ready = bool(predicate())
            except Exception:
                ready = False
            if ready:
                guard(next_step)
            elif time.perf_counter() < deadline:
                QTimer.singleShot(200, poll)
            else:
                record(f"等待{description}", False, "超时")
                finish()

        poll()

    def step_loaded():
        core = window.core
        record("数据加载为 canonical X/Y/E/T", core.raw_data.ndim == 4, core.raw_data.shape)
        state["original_core"] = core
        state["original_raw"] = core.raw_data.copy()
        state["original_source"] = core.source_array.copy()
        state["original_x"] = core.coords["X"].copy()
        state["original_y"] = core.coords["Y"].copy()
        state["original_e"] = core.coords["E"].copy()
        state["original_t"] = core.coords["delay"].copy()
        state["original_role_order"] = list(core.axis_assignment.role_order)
        executed.append("加载数据并进入三维主页")
        card = window.left_workspace.page_tree.source_card
        QTimer.singleShot(400, lambda: guard(step_axis_dialog))
        QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier, card.rect().center())

    def step_axis_dialog():
        dialog = QApplication.activeModalWidget()
        record("数据体卡片打开真实轴分配窗口", isinstance(dialog, AxisAssignmentDialog))
        if not isinstance(dialog, AxisAssignmentDialog):
            raise AssertionError("轴分配窗口未出现")
        calibration_button = _find_button(
            dialog, ("calibration_button", "calibrate_button"), "定标"
        )
        capture("axis-assignment.png", dialog)
        executed.append("AxisAssignmentDialog 定标入口")
        QTimer.singleShot(500, lambda: guard(step_calibration_dialog))
        calibration_button.click()

    def step_calibration_dialog():
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, MomentumCalibrationDialog):
            dialog = next((
                item for item in QApplication.topLevelWidgets()
                if isinstance(item, MomentumCalibrationDialog) and item.isVisible()
            ), None)
        record("定标入口打开真实次级窗口", isinstance(dialog, MomentumCalibrationDialog))
        if dialog is None:
            raise AssertionError("定标窗口未出现")
        state["dialog"] = dialog
        dialog.apply_requested.connect(
            lambda a, b, distance, unit: state["apply_signals"].append((a, b, distance, unit))
        )
        dialog.save_requested.connect(lambda: state.__setitem__("save_requests", state["save_requests"] + 1))
        state["apply_button"] = _find_button(dialog, ("apply_button",), "应用")
        state["save_button"] = _find_button(dialog, ("save_button",), "保存")
        state["energy_slider"] = dialog.energy_slider
        state["scan_slider"] = dialog.scan_slider
        sliders = (state["energy_slider"], state["scan_slider"])
        record(
            "能量与扫描切片均为共享双端滑条且无长度输入框",
            all(
                isinstance(slider, CalibrationSliceSlider)
                and isinstance(slider, DualRangeSlider)
                and not hasattr(slider, "input_length")
                and not slider.findChildren(QDoubleSpinBox)
                for slider in sliders
            ),
        )
        record(
            "定标 figure 只有一个 axes 且没有 colorbar",
            len(dialog.figure.axes) == 1
            and dialog.axes is dialog.figure.axes[0]
            and not hasattr(dialog, "colorbar"),
        )
        distance_input = _named_widget(dialog, ("distance_input",), QDoubleSpinBox)
        if distance_input is None:
            raise AssertionError("未找到两点实际间距输入框")
        unit_edit = _find_edit(dialog, ("unit_edit",), ("单位", "unit"))
        record("应用前禁止独立保存", not state["save_button"].isEnabled())

        slider = state["energy_slider"]
        if slider.maximum() > slider.minimum():
            slider.setValue((slider.minimum() + slider.maximum()) // 2)
        core = state["original_core"]
        nx, ny = core.raw_data.shape[:2]
        point_a = (0.20 * (nx - 1), 0.30 * (ny - 1))
        point_b = (0.80 * (nx - 1), 0.70 * (ny - 1))
        state["target_distance"] = 1.6 * _source_distance(core, point_a, point_b)
        distance_input.setValue(state["target_distance"])
        unit_edit.setText("Å⁻¹")
        canvas, axes = _find_canvas(dialog)
        for point in (point_a, point_b):
            physical = _physical_point(core, point)
            x, y = axes.transData.transform(physical)
            ratio = canvas.devicePixelRatioF()
            height = canvas.get_width_height(physical=True)[1]
            position = QPointF(x / ratio, (height - y) / ratio).toPoint()
            QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, position)
        app.processEvents()
        record("能量切片滑条可调整", slider.value() >= slider.minimum(), slider.value())
        executed.append("XY 切片选两个逻辑点并输入实际间距与单位")
        state["apply_button"].click()
        wait_for(
            "定标应用",
            lambda: len(state["apply_signals"]) == 1 and state["save_button"].isEnabled(),
            step_applied,
        )

    def step_applied():
        point_a, point_b, target, unit = state["apply_signals"][0]
        point_a = tuple(float(value) for value in point_a)
        point_b = tuple(float(value) for value in point_b)
        record("应用信号包含两个零基逻辑点", len(point_a) == len(point_b) == 2 and point_a != point_b,
               {"point_a": point_a, "point_b": point_b})
        record("应用信号包含目标距离和单位",
               np.isclose(target, state["target_distance"]) and unit == "Å⁻¹", (target, unit))
        core = window.core
        original = state["original_core"]
        record("宿主替换为新 core", core is not original)
        source = _source_distance(original, point_a, point_b)
        scale = target / source
        expected_x = state["original_x"] * scale
        expected_y = state["original_y"] * scale
        np.testing.assert_allclose(core.coords["X"], expected_x, rtol=1e-11, atol=1e-12)
        np.testing.assert_allclose(core.coords["Y"], expected_y, rtol=1e-11, atol=1e-12)
        np.testing.assert_allclose(core.coordinate_candidates["kx"], expected_x, rtol=1e-11, atol=1e-12)
        np.testing.assert_allclose(core.coordinate_candidates["ky"], expected_y, rtol=1e-11, atol=1e-12)
        record("两点距离统一缩放 Kx/Ky 并更新 candidates", True,
               {"source_distance": source, "target_distance": target, "scale": scale})
        np.testing.assert_array_equal(core.raw_data, state["original_raw"])
        np.testing.assert_array_equal(core.source_array, state["original_source"])
        np.testing.assert_allclose(core.coords["E"], state["original_e"])
        np.testing.assert_allclose(core.coords["delay"], state["original_t"])
        record("强度数组、E/T 与原始维度顺序保持",
               list(core.axis_assignment.role_order) == state["original_role_order"])
        np.testing.assert_allclose(original.coords["X"], state["original_x"])
        np.testing.assert_allclose(original.coords["Y"], state["original_y"])
        record("原 core 坐标不被修改", True)
        record("定标坐标来源与单位",
               core.axis_assignment.coord_keys["X"] == "kx"
               and core.axis_assignment.coord_keys["Y"] == "ky"
               and core.coord_sources["X"] == core.coord_sources["Y"] == "file"
               and core.coord_units["X"] == core.coord_units["Y"] == "Å⁻¹")
        record("成功应用后启用保存", state["save_button"].isEnabled())
        capture("calibration-light.png", state["dialog"])
        capture("main-light.png", window)
        executed.append("应用定标并核对 core 数值契约")
        window._set_theme("dark")
        QTimer.singleShot(1200, lambda: guard(step_dark))

    def step_dark():
        record("主窗口切换深色主题", theme.current_theme() == "dark", theme.current_theme())
        capture("calibration-dark.png", state["dialog"])
        capture("main-dark.png", window)
        window._set_theme("light")
        QTimer.singleShot(1200, lambda: guard(step_save_npz))

    def save_with_dialog(output_path, selected_filter, request_count, next_step):
        with patch.object(
            QFileDialog, "getSaveFileName",
            return_value=(str(output_path), selected_filter),
        ):
            state["save_button"].click()
        wait_for(
            f"独立保存 {output_path.suffix}",
            lambda: state["save_requests"] == request_count
            and output_path.is_file() and output_path.stat().st_size > 0,
            next_step,
        )

    def verify_roundtrip(output_path, label):
        artifacts[output_path.name] = str(output_path)
        loaded = AnalyzerCore()
        success, info = loaded.load_npz(str(output_path))
        record(f"{label} 可由 AnalyzerCore 加载", success, info)
        if not success:
            raise AssertionError(info)
        state[f"loaded_core_{label}"] = loaded
        expected = window.core
        np.testing.assert_array_equal(loaded.source_array, expected.source_array)
        np.testing.assert_array_equal(loaded.raw_data, expected.raw_data)
        for role in ("X", "Y", "E"):
            np.testing.assert_allclose(loaded.coords[role], expected.coords[role])
        np.testing.assert_allclose(loaded.coords["delay"], expected.coords["delay"])
        np.testing.assert_allclose(loaded.coordinate_candidates["kx"], expected.coords["X"])
        np.testing.assert_allclose(loaded.coordinate_candidates["ky"], expected.coords["Y"])
        record(f"{label} 往返保留完整数据体与 Kx/Ky/E/T 坐标", True)
        loaded_coordinates = dict(loaded.source_layout)
        expected_coordinates = dict(expected.source_layout)
        loaded_layout = dict(loaded_coordinates)
        expected_layout = dict(expected_coordinates)
        for name in ("manual", "coord_keys", "coord_sources", "coord_units"):
            loaded_layout.pop(name, None)
            expected_layout.pop(name, None)
        missing = object()
        layout_differences = [
            {
                "field": key,
                "host": expected_layout.get(key, "<缺失>"),
                "loaded": loaded_layout.get(key, "<缺失>"),
            }
            for key in sorted(set(expected_layout) | set(loaded_layout))
            if expected_layout.get(key, missing) != loaded_layout.get(key, missing)
        ]
        for name in ("scan_kind", "scan_name", "scan_unit"):
            host_value = getattr(expected, name)
            loaded_value = getattr(loaded, name)
            if loaded_value != host_value:
                layout_differences.append({
                    "field": name, "host": host_value, "loaded": loaded_value,
                })
        record(
            f"{label} 往返保留轴分配与扫描信息",
            not layout_differences,
            {"differences": layout_differences} if layout_differences else "非坐标字段逐项一致",
        )

        coordinate_differences = []

        def check_coordinate(field, role, expected_value, *, requirement=None):
            loaded_value = (loaded_coordinates.get(field) or {}).get(role)
            if requirement == "nonempty":
                valid = bool(loaded_value and str(loaded_value).strip())
            else:
                valid = loaded_value == expected_value
            if not valid:
                coordinate_differences.append({
                    "field": f"{field}.{role}",
                    "expected": "非空值" if requirement == "nonempty" else expected_value,
                    "loaded": loaded_value,
                })

        for role in ("X", "Y", "T"):
            check_coordinate(
                "coord_keys", role, (expected_coordinates.get("coord_keys") or {}).get(role)
            )
        for role in ("X", "Y", "delay"):
            check_coordinate(
                "coord_sources", role,
                (expected_coordinates.get("coord_sources") or {}).get(role),
            )
        for role in ("X", "Y"):
            check_coordinate("coord_units", role, "Å⁻¹")
        check_coordinate(
            "coord_units", "delay",
            (expected_coordinates.get("coord_units") or {}).get("delay"),
        )
        check_coordinate("coord_keys", "E", None, requirement="nonempty")
        check_coordinate("coord_sources", "E", "file")
        check_coordinate(
            "coord_units", "E", (expected_coordinates.get("coord_units") or {}).get("E")
        )
        record(
            f"{label} 往返坐标键、来源与单位符合定标契约",
            not coordinate_differences,
            {"differences": coordinate_differences}
            if coordinate_differences else "坐标元数据逐项符合 Kx/Ky 定标契约",
        )

    def step_save_npz():
        output_path = args.output_dir / "calibrated-source.npz"
        save_with_dialog(output_path, "NumPy Files (*.npz)", 1, step_npz_saved)

    def step_npz_saved():
        verify_roundtrip(args.output_dir / "calibrated-source.npz", "NPZ")
        record("NPZ 保存后仍可使用同一保存按钮", state["save_button"].isEnabled())
        executed.append("独立保存完整 NPZ 并直接回读核对")
        guard(step_save_mat)

    def step_save_mat():
        output_path = args.output_dir / "calibrated-source.mat"
        save_with_dialog(output_path, "MATLAB Files (*.mat)", 2, step_mat_saved)

    def step_mat_saved():
        from bandscope.core.data_trans import convert

        mat_path = args.output_dir / "calibrated-source.mat"
        artifacts[mat_path.name] = str(mat_path)
        converted_path = args.output_dir / "calibrated-source-from-mat.npz"
        convert(mat_path, converted_path)
        record("MAT 经 data_trans.convert 转换", converted_path.is_file(), converted_path)
        verify_roundtrip(converted_path, "MAT 转换后")
        state["roundtrip"] = True
        executed.append("同一保存按钮导出 MAT，经转换后回读核对")
        capture("calibration-saved.png", state["dialog"])
        slider = state["energy_slider"]
        if slider.maximum() > slider.minimum():
            slider.setValue(slider.minimum() if slider.value() != slider.minimum() else slider.maximum())
            app.processEvents()
            record("成功后切片变化使保存失效", not state["save_button"].isEnabled())
        state["dialog"].close()
        QTimer.singleShot(500, lambda: guard(step_closed))

    def step_closed():
        record("关闭定标后旧轴分配不覆盖定标",
               window.core is not state["original_core"] and not np.allclose(
                   window.core.coords["X"], state["original_x"]
               ))
        capture("main-final.png", window)
        executed.append("关闭定标与轴分配窗口并复核主窗口")
        finish()

    QTimer.singleShot(800, lambda: guard(lambda: (
        window.load_data(str(data_path)),
        wait_for(
            "主页渲染",
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "3d"
            and window._render_exact_ready,
            step_loaded,
            timeout_ms=90000,
        ),
    )))
    QTimer.singleShot(240000, lambda: (record("总验收超时", False), finish()))
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
