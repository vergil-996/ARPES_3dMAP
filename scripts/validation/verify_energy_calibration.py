"""Real-window acceptance for two-point energy-axis calibration.

Usage: .venv/Scripts/python.exe scripts/validation/verify_energy_calibration.py [data.npz]
Defaults to .local/data/WSe2_step.npz and writes .local/outputs/energy-calibration/.
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

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "xcb")

_SANDBOX = tempfile.TemporaryDirectory(prefix="bandscope-energy-calibration-")
os.environ["BANDSCOPE_EXTENSION_ROOT"] = str(Path(_SANDBOX.name) / "extensions")

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QEvent, QPoint, QSettings, Qt, QTimer
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDoubleSpinBox

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
    energy = np.asarray([-1.0, -0.55, -0.20, 0.05, 0.40, 0.90, 1.50, 2.20])
    delay = np.asarray([0.0, 200.0, 500.0], dtype=np.float64)
    xx, yy, ee, tt = np.meshgrid(x, y, energy, delay, indexing="ij")
    raw = (
        np.exp(-((xx - 0.12) ** 2 + (yy + 0.08) ** 2) / 0.08)
        * (1.0 + 0.15 * np.cos(ee * np.pi) + 0.002 * tt)
        + 0.01 * xx
        + 0.02 * yy
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


def _visible_dialog(dialog_type):
    return next((
        widget for widget in QApplication.topLevelWidgets()
        if isinstance(widget, dialog_type) and widget.isVisible()
    ), None)


def _image_array(dialog):
    return np.asarray(np.ma.getdata(dialog.image.get_array()))


def _canvas_point(dialog, logical_index):
    display_x, display_y = dialog._line_transform.transform(
        (1.045, dialog._physical_energy(logical_index))
    )
    ratio = dialog.canvas.devicePixelRatioF()
    return QPoint(
        int(round(display_x / ratio)),
        int(round(dialog.canvas.height() - display_y / ratio)),
    )


def _drag_outside_handle(dialog, name, target_index):
    current = dialog.reference_index if name == "reference" else dialog.second_index
    start = _canvas_point(dialog, current)
    end = _canvas_point(dialog, target_index)
    press = QMouseEvent(
        QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    )
    move = QMouseEvent(
        QEvent.MouseMove, end, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    )
    release = QMouseEvent(
        QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton, Qt.NoModifier
    )
    QApplication.sendEvent(dialog.canvas, press)
    QApplication.sendEvent(dialog.canvas, move)
    QApplication.sendEvent(dialog.canvas, release)
    QApplication.processEvents()


def main():
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", nargs="?", type=Path, default=REPO_ROOT / ".local/data/WSe2_step.npz")
    parser.add_argument(
        "--output-dir", type=Path, default=REPO_ROOT / ".local/outputs/energy-calibration"
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
    from bandscope.ui import theme
    from bandscope.ui.axis_assignment_dialog import AxisAssignmentDialog
    from bandscope.ui.axis_interval_bar import DualRangeSlider
    from bandscope.ui.energy_calibration_dialog import EnergyCalibrationDialog
    from bandscope.ui.momentum_calibration_dialog import CalibrationSliceSlider

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
        {
            "item": "截图人工视觉复核",
            "reason": "脚本只生成截图并执行自动断言，不代替人工检查深浅主题、手柄和布局。",
        },
        {
            "item": "能量定标 NPZ/MAT 保存回读",
            "reason": "本脚本聚焦真实窗口交互与宿主 core；完整保存链路由现有自动化覆盖。",
        },
    ]
    if args.synthetic_only:
        not_executed.append({
            "item": "WSe₂ 真实数据验收",
            "reason": "本次使用 --synthetic-only，不代表默认实验数据已验收。",
        })
    state = {
        "finished": False,
        "applied": False,
        "closed": False,
        "apply_signals": [],
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
        if not state["applied"]:
            not_executed.append({
                "item": "应用能量定标并核对宿主 core",
                "reason": "流程在完成前结束；详见失败检查与 messages。",
            })
        if not state["closed"]:
            not_executed.append({
                "item": "应用后关闭定标与轴分配窗口",
                "reason": "流程在完成前结束；详见失败检查与 messages。",
            })
        report = {
            "data": str(data_path),
            "synthetic_only": bool(args.synthetic_only),
            "wse2_executed": not args.synthetic_only,
            "platform": os.environ.get("QT_QPA_PLATFORM"),
            "executed": executed,
            "not_executed": not_executed,
            "checks": RESULTS,
            "messages": messages,
            "artifacts": artifacts,
            "passed": bool(RESULTS)
            and all(item["ok"] for item in RESULTS)
            and state["applied"]
            and state["closed"],
        }
        (args.output_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[result] {'PASS' if report['passed'] else 'FAIL'}", flush=True)
        for widget in list(QApplication.topLevelWidgets()):
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
        state["original_energy"] = core.coords["E"].copy()
        state["original_x"] = core.coords["X"].copy()
        state["original_y"] = core.coords["Y"].copy()
        state["original_delay"] = core.coords["delay"].copy()
        executed.append("加载数据并进入三维主页")
        card = window.left_workspace.page_tree.source_card
        QTimer.singleShot(400, lambda: guard(step_axis_dialog))
        QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier, card.rect().center())

    def step_axis_dialog():
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, AxisAssignmentDialog):
            dialog = _visible_dialog(AxisAssignmentDialog)
        record("数据体卡片打开真实轴分配窗口", isinstance(dialog, AxisAssignmentDialog))
        if dialog is None:
            raise AssertionError("轴分配窗口未出现")
        state["axis_dialog"] = dialog
        capture("axis-assignment.png", dialog)
        executed.append("AxisAssignmentDialog 能量定标入口")
        QTimer.singleShot(500, lambda: guard(step_calibration_dialog))
        dialog.energy_calibration_button.click()

    def step_calibration_dialog():
        dialog = _visible_dialog(EnergyCalibrationDialog)
        record("能量定标入口打开真实可见窗口", isinstance(dialog, EnergyCalibrationDialog))
        if dialog is None:
            raise AssertionError("能量定标窗口未出现")
        state["dialog"] = dialog
        dialog.apply_requested.connect(
            lambda *values: state["apply_signals"].append(values)
        )
        dialog.canvas.draw()
        app.processEvents()

        original = state["original_raw"]
        expected = original[:, 0, :, 0].T
        record(
            "默认 XZ 切片使用 canonical 数组",
            dialog.plane == "XZ"
            and dialog.normal_axis == "Y"
            and dialog.normal_index == 0
            and dialog.scan_index == 0
            and np.array_equal(_image_array(dialog), expected),
            {"plane": dialog.plane, "normal": dialog.normal_index, "scan": dialog.scan_index},
        )
        sliders = (dialog.normal_slider, dialog.scan_slider)
        record(
            "法向与扫描均为共享双端滑条且无长度输入框",
            all(
                isinstance(slider, CalibrationSliceSlider)
                and isinstance(slider, DualRangeSlider)
                and not hasattr(slider, "input_length")
                and not slider.findChildren(QDoubleSpinBox)
                for slider in sliders
            ),
        )
        record(
            "能量定标 figure 只有一个 axes 且没有 colorbar",
            len(dialog.figure.axes) == 1
            and dialog.axes is dialog.figure.axes[0]
            and not hasattr(dialog, "colorbar"),
        )
        lines_ok = True
        for line, handle, logical in (
            (dialog.reference_line, dialog.reference_handle, dialog.reference_index),
            (dialog.second_line, dialog.second_handle, dialog.second_index),
        ):
            handle_x, _handle_y = handle.get_transform().transform(
                (1.045, dialog._physical_energy(logical))
            )
            lines_ok = lines_ok and (
                line.get_linestyle() == "--"
                and line.get_xdata()[0] < 0.0
                and line.get_xdata()[-1] > 1.0
                and handle_x > dialog.axes.bbox.x1
            )
        record("两条水平虚线延伸图外且手柄位于 axes 外", lines_ok)
        capture("calibration-default-xz-light.png", dialog)

        dialog.plane_combo.setCurrentText("YZ")
        app.processEvents()
        normal_index = min(1, original.shape[0] - 1)
        scan_index = min(1, original.shape[3] - 1)
        dialog.normal_slider.setValue(normal_index)
        dialog.scan_slider.setValue(scan_index)
        dialog.canvas.draw()
        app.processEvents()
        expected_yz = original[normal_index, :, :, scan_index].T
        record(
            "切换 YZ 后法向与扫描切片数组正确",
            dialog.plane == "YZ"
            and dialog.normal_axis == "X"
            and dialog.normal_index == normal_index
            and dialog.scan_index == scan_index
            and np.array_equal(_image_array(dialog), expected_yz),
            {"normal": normal_index, "scan": scan_index},
        )
        before_flip = _image_array(dialog).copy()
        dialog.flip_checkbox.setChecked(True)
        dialog.canvas.draw()
        app.processEvents()
        record(
            "垂直翻转只镜像显示且不改变切片强度",
            dialog.axes.yaxis_inverted() and np.array_equal(_image_array(dialog), before_flip),
        )

        energy_count = original.shape[2]
        reference_target = 0.20 * (energy_count - 1)
        second_target = 0.80 * (energy_count - 1)
        initial_reference = dialog.reference_index
        initial_second = dialog.second_index
        _drag_outside_handle(dialog, "reference", reference_target)
        _drag_outside_handle(dialog, "second", second_target)
        dialog.canvas.draw()
        app.processEvents()
        tolerance = max(0.08, 1.5 * (energy_count - 1) / max(dialog.axes.bbox.height, 1.0))
        record(
            "翻转后可用鼠标拖动两个图外手柄",
            abs(dialog.reference_index - initial_reference) > 1e-6
            and abs(dialog.second_index - initial_second) > 1e-6
            and abs(dialog.reference_index - reference_target) <= tolerance
            and abs(dialog.second_index - second_target) <= tolerance
            and dialog.reference_index != dialog.second_index,
            {
                "reference_index": dialog.reference_index,
                "second_index": dialog.second_index,
                "target": [reference_target, second_target],
                "tolerance": tolerance,
            },
        )
        state["reference_index"] = float(dialog.reference_index)
        state["second_index"] = float(dialog.second_index)
        state["target_spacing"] = 0.4
        state["unit"] = "meV"
        dialog.spacing_input.setValue(state["target_spacing"])
        dialog.unit_edit.setText(state["unit"])
        capture("calibration-yz-flipped-light.png", dialog)
        scrollbar = dialog.scroll_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        capture("calibration-measurement-light.png", dialog)
        executed.append("真实切换 XZ/YZ、垂直翻转并拖动两个图外能量手柄")
        dialog.apply_button.click()
        wait_for(
            "能量定标应用",
            lambda: len(state["apply_signals"]) == 1
            and window.core is not state["original_core"]
            and dialog.save_button.isEnabled(),
            step_applied,
        )

    def step_applied():
        dialog = state["dialog"]
        reference = state["reference_index"]
        second = state["second_index"]
        target = state["target_spacing"]
        unit = state["unit"]
        record(
            "应用信号包含两个逻辑索引用目标间距与单位",
            len(state["apply_signals"]) == 1
            and np.allclose(state["apply_signals"][0][:2], [reference, second])
            and state["apply_signals"][0][2:] == (target, unit),
            state["apply_signals"],
        )
        core = window.core
        original = state["original_core"]
        energy = np.asarray(core.coords["E"], dtype=np.float64)
        expected = (
            (np.arange(energy.size, dtype=np.float64) - reference)
            * target
            / (second - reference)
        )
        np.testing.assert_allclose(energy, expected, rtol=1e-11, atol=1e-12)
        uniform = bool(np.allclose(np.diff(energy), np.diff(energy)[0], rtol=1e-11, atol=1e-12))
        record("E 坐标按逻辑索引重新生成且均匀", uniform)
        positions = np.arange(energy.size, dtype=np.float64)
        e1 = float(np.interp(reference, positions, energy))
        e2 = float(np.interp(second, positions, energy))
        record(
            "E1 为零且 E2 等于目标间距",
            np.isclose(e1, 0.0, atol=1e-11) and np.isclose(e2, target, rtol=1e-11, atol=1e-12),
            {"E1": e1, "E2": e2, "target_spacing": target},
        )
        np.testing.assert_array_equal(core.raw_data, state["original_raw"])
        np.testing.assert_array_equal(core.source_array, state["original_source"])
        np.testing.assert_array_equal(core.coords["X"], state["original_x"])
        np.testing.assert_array_equal(core.coords["Y"], state["original_y"])
        np.testing.assert_array_equal(core.coords["delay"], state["original_delay"])
        np.testing.assert_array_equal(original.coords["E"], state["original_energy"])
        record("宿主 core 强度、X/Y/扫描坐标及原 core 均保持不变", core is not original)
        np.testing.assert_allclose(core.coordinate_candidates["E"], expected, rtol=1e-11, atol=1e-12)
        record(
            "应用同步单位、坐标候选与定标窗口 core",
            core.coord_units["E"] == unit
            and dialog.core is core
            and dialog.save_button.isEnabled(),
        )
        state["expected_energy"] = expected
        state["applied"] = True
        capture("calibration-applied-light.png", dialog)
        capture("main-light.png", window)
        executed.append("应用目标间距与单位并核对宿主 core 数值契约")
        window._set_theme("dark")
        QTimer.singleShot(1200, lambda: guard(step_dark))

    def step_dark():
        record("主窗口切换深色主题", theme.current_theme() == "dark", theme.current_theme())
        capture("calibration-dark.png", state["dialog"])
        capture("main-dark.png", window)
        window._set_theme("light")
        QTimer.singleShot(700, lambda: guard(step_close))

    def step_close():
        state["dialog"].close_button.click()
        QTimer.singleShot(700, lambda: guard(step_closed))

    def step_closed():
        closed = (
            _visible_dialog(EnergyCalibrationDialog) is None
            and _visible_dialog(AxisAssignmentDialog) is None
        )
        record("应用后正确关闭定标及轴分配窗口", closed)
        np.testing.assert_allclose(window.core.coords["E"], state["expected_energy"])
        record("关闭定标窗口后宿主保持能量定标", True)
        state["closed"] = closed
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

    def timeout():
        if not state["finished"]:
            record("总验收超时", False)
            finish()

    QTimer.singleShot(240000, timeout)
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
