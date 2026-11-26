# -*- coding: utf-8 -*-
"""真实窗口验收 30° 旋转裁剪、裁空及冻结来源分析页。

默认读取 .local/data/WSe2_step.npz；文件缺失直接失败，不生成合成替代数据。
使用本机 QApplication、VTK、原生旋钮、范围浮窗、工具按钮和右键菜单。自动截图
不等于人工视觉复核。真实数据用于窗口流程和可独立复算的对照，严格合成数值
回归仍由 tests/integration/test_rotated_*.py 负责。

用法: .venv/Scripts/python.exe scripts/validation/verify_rotated_crop_analysis.py [npz路径] [--output-dir DIR]
"""
from __future__ import annotations

import argparse
import faulthandler
import hashlib
import json
import os
import platform
import sys
import time
import traceback
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_DATA = REPO_ROOT / ".local" / "data" / "WSe2_step.npz"
DEFAULT_OUTPUT = REPO_ROOT / ".local" / "outputs" / "rotated-crop-analysis"
ANGLE = 30.0


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path, nargs="?", default=DEFAULT_DATA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not args.data.is_file():
        parser.error(f"真实数据文件不存在，不使用合成数据替代：{args.data}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.environ["QT_QPA_PLATFORM"] = "windows"
    elif sys.platform == "darwin":
        os.environ["QT_QPA_PLATFORM"] = "cocoa"
    else:
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    from tests.support.environment import isolate_runtime

    isolate_runtime()
    return args


class _RenderFailure(RuntimeError):
    pass


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def _physical_bounds(coords, region):
    bounds = []
    for axis, key in enumerate(("X", "Y", "E")):
        low_index, up_index = (int(value) for value in region[2 * axis:2 * axis + 2])
        values = np.asarray(coords[key], dtype=np.float64)
        low = float(values[low_index])
        up = float(values[up_index])
        if low_index + 1 < values.size:
            low += 0.001 * (float(values[low_index + 1]) - low)
        if up_index > 0:
            up += 0.001 * (float(values[up_index - 1]) - up)
        bounds.extend((min(low, up), max(low, up)))
    return tuple(bounds)


def _context_bounds(context):
    from bandscope.core.data_scope import scoped_descriptor_from_array

    descriptor = scoped_descriptor_from_array(context.get("data"))
    if descriptor is not None:
        return tuple(int(value) for value in descriptor.spatial_bounds)
    if context.get("data_bounds") is not None:
        return tuple(int(value) for value in context["data_bounds"])
    return tuple(value for size in np.asarray(context["data"]).shape for value in (0, size - 1))


def _expected_curve(context, selection):
    from bandscope.core.crop_model import capture_selection_geometry, selection_grid_mask

    coords = context["coords"]
    angle = float(context.get("rotation_angle", 0.0) or 0.0)
    captured = capture_selection_geometry(context, selection, rotation_angle=angle)
    shape = tuple(int(np.asarray(coords[key]).size) for key in ("X", "Y", "E"))
    full_mask = selection_grid_mask(
        captured, coords, shape, rotation_angle=angle,
    )
    samples = np.where(full_mask)
    if not samples[0].size:
        raise AssertionError("EDC 选区没有覆盖任何网格点")
    selected_bounds = tuple(
        int(value) for axis in samples for value in (axis.min(), axis.max())
    )
    bounds = _context_bounds(context)
    intersections = tuple(
        value
        for axis in range(3)
        for value in (
            max(selected_bounds[2 * axis], bounds[2 * axis]),
            min(selected_bounds[2 * axis + 1], bounds[2 * axis + 1]),
        )
    )
    if any(intersections[2 * axis] > intersections[2 * axis + 1] for axis in range(3)):
        raise AssertionError("EDC 选区与父页数据域没有交集")
    slices = tuple(
        slice(bounds[2 * axis], bounds[2 * axis + 1] + 1) for axis in range(3)
    )
    mask = full_mask[slices].copy()
    for key in ("spatial_valid_mask", "canvas_mask"):
        valid = context.get(key)
        if valid is not None:
            mask &= np.asarray(valid, dtype=bool)
    data = np.asarray(context["data"])
    selected = np.where(mask, data, np.nan)
    intensity = np.nansum(selected, axis=(0, 1), dtype=np.float64)
    has_value = np.any(~np.isnan(selected), axis=(0, 1))
    intensity = np.where(has_value, intensity, np.nan)
    e0, e1 = intersections[4:6]
    local_e0 = e0 - bounds[4]
    local_e1 = e1 - bounds[4]
    energy = np.asarray(coords["E"], dtype=np.float64)[e0:e1 + 1]
    return energy, intensity[local_e0:local_e1 + 1], captured


def _expected_axis_integral(context, axis_index, low, up):
    data = np.nan_to_num(
        np.asarray(context["data"], dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0,
    )
    bounds = _context_bounds(context)
    remaining = [axis for axis in range(3) if axis != axis_index]
    result = np.zeros(
        tuple(int(np.asarray(context["coords"][("X", "Y", "E")[axis]]).size) for axis in remaining),
        dtype=np.float64,
    )
    normal_low = max(int(low), bounds[2 * axis_index])
    normal_up = min(int(up), bounds[2 * axis_index + 1])
    if normal_low > normal_up:
        return result
    local = [slice(None)] * 3
    local[axis_index] = slice(
        normal_low - bounds[2 * axis_index], normal_up - bounds[2 * axis_index] + 1,
    )
    compact = np.sum(data[tuple(local)], axis=axis_index, dtype=np.float64)
    output_slices = tuple(
        slice(bounds[2 * axis], bounds[2 * axis + 1] + 1) for axis in remaining
    )
    result[output_slices] = compact
    return result


def _second_derivative(data, energy, threshold=0.0):
    source = np.asarray(data)
    compute_dtype = np.float32 if source.dtype.kind == "f" and source.dtype.itemsize <= 4 else np.float64
    source = np.asarray(source, dtype=compute_dtype)
    first = np.gradient(source, np.asarray(energy, dtype=np.float64), axis=1, edge_order=2)
    result = -np.gradient(first, np.asarray(energy, dtype=np.float64), axis=1, edge_order=2)
    result[result < float(threshold)] = 0
    return result


def main():
    args = _arguments()
    faulthandler.enable()

    from PyQt5.QtCore import QPoint, QPointF, Qt, QTimer
    from PyQt5.QtGui import QContextMenuEvent, QImage, QPainter, QPixmap
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication, QMenu

    from bandscope.core.crop_model import CropSelection, apply_crop_regions
    from bandscope.core.data_scope import scoped_descriptor_from_array
    from bandscope.core.path_slice import PathSliceModel, draft_nodes
    from bandscope.processing.path_slice import MomentumCalibration, PathSliceSampler
    from scripts.validation.verify_integration_context_menu import create_from_page_menu

    app = QApplication.instance() or QApplication(sys.argv)
    import bandscope.app.refactored_app as refactored_app

    refactored_app.UpdateController = _NullUpdateController
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.resize(1500, 920)
    window.showNormal()
    window.show()
    messages = []
    window._show_message = lambda title, text, *a, **k: messages.append((title, text))

    results = []
    executed = []
    artifacts = []
    numeric_checks = []
    page_records = []
    spatial_operations = []
    production_issues = []
    axis_source_fallback = False
    pages = {}
    attempted = set()
    required_keys = (
        "crop", "erase", "edc", "slice_dos", "axis_slices", "energy_dos",
        "second_derivative", "path_slice", "zero_frame", "log_curve",
    )
    required = {key: False for key in required_keys}
    state = {"fatal": None}

    def record(item, ok, detail=""):
        entry = {"item": str(item), "ok": bool(ok), "detail": detail}
        results.append(entry)
        rendered = detail if isinstance(detail, str) else json.dumps(
            detail, ensure_ascii=False, default=str,
        )
        print(f"[{'PASS' if ok else 'FAIL'}] {item}  {rendered}", flush=True)
        return bool(ok)

    def current_spec():
        return window.left_workspace.current_spec()

    def current_page_id():
        spec = current_spec()
        return spec.page_id if spec is not None else None

    def page_ready(page_id, view=None):
        context = window.current_render_context
        if current_page_id() != page_id or context is None:
            return False
        if view is not None and context.get("view") != view:
            return False
        if getattr(window, "_render_context_page_id", page_id) != page_id:
            return False
        return bool(getattr(window, "_render_exact_ready", True))

    def diagnostics():
        popup = window.crop_controller.popup
        return {
            "current_page_id": current_page_id(),
            "current_page_kind": getattr(current_spec(), "page_kind", None),
            "context": {
                key: value for key, value in (window.current_render_context or {}).items()
                if key in {"view", "rotation_angle", "data_bounds", "full_shape", "crop_empty", "source_t_index"}
            },
            "exact_ready": getattr(window, "_render_exact_ready", None),
            "rotation_angle": getattr(window, "rotation_angle", None),
            "time_index": window.timeline_bar.slider_time.value(),
            "popup_error": popup.error_label.text(),
            "messages": messages[-4:],
            "last_refresh_failure": getattr(window, "_last_refresh_failure", None),
        }

    def wait_for(description, predicate, timeout=120):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            app.processEvents()
            try:
                ready = bool(predicate())
            except _RenderFailure:
                raise
            except Exception:
                ready = False
            if ready:
                QTest.qWait(150)
                return
            QTest.qWait(120)
        record(description, False, {"reason": "等待超时", **diagnostics()})
        raise AssertionError(f"等待{description}超时")

    def click(button, label):
        if not button.isVisible() or not button.isEnabled():
            raise AssertionError(f"控件不可点击：{label}; visible={button.isVisible()}, enabled={button.isEnabled()}")
        QTest.mouseClick(button, Qt.LeftButton, Qt.NoModifier, button.rect().center())
        app.processEvents()

    def activate(page_id, view=None):
        window.left_workspace.activate_page(page_id)
        wait_for(f"激活页面 {page_id}", lambda: page_ready(page_id, view))

    def wait_new_page(before, kind, view, description, timeout=150):
        message_start = len(messages)

        def ready():
            new_ids = set(window.left_workspace.page_specs) - before
            render_errors = [item for item in messages[message_start:] if item[0] == "Render failed"]
            if len(new_ids) == 1 and current_page_id() in new_ids and render_errors:
                raise _RenderFailure(json.dumps({
                    "description": description,
                    "new_page_id": current_page_id(),
                    "render_errors": render_errors,
                }, ensure_ascii=False))
            if len(new_ids) != 1 or current_page_id() not in new_ids:
                return False
            spec = current_spec()
            return spec.page_kind == kind and page_ready(spec.page_id, view)

        wait_for(description, ready, timeout)
        return current_spec()

    def compare(label, actual, expected, *, rtol=2e-5, atol=2e-5, extra=None, production_issue=False):
        actual = np.asarray(actual)
        expected = np.asarray(expected)
        shape_ok = actual.shape == expected.shape
        ok = shape_ok and bool(np.allclose(
            actual, expected, rtol=rtol, atol=atol, equal_nan=True,
        ))
        detail = {
            "actual_shape": list(actual.shape),
            "expected_shape": list(expected.shape),
            "rtol": rtol,
            "atol": atol,
            "actual_finite": int(np.isfinite(actual).sum()),
            "expected_finite": int(np.isfinite(expected).sum()),
        }
        if shape_ok and actual.size:
            difference = np.abs(actual.astype(np.float64) - expected.astype(np.float64))
            detail["max_abs_error"] = float(np.nanmax(difference)) if np.isfinite(difference).any() else None
            detail["actual_sum"] = float(np.nansum(actual, dtype=np.float64))
            detail["expected_sum"] = float(np.nansum(expected, dtype=np.float64))
        if extra:
            detail.update(extra)
        numeric_checks.append({"label": label, "ok": ok, **detail})
        if not ok and production_issue:
            production_issues.append({
                "stage": label,
                "error": "真实窗口结果与独立数值对照不一致",
                "detail": detail,
            })
        return record(label, ok, detail)

    def rotation_fields(spec):
        params = spec.params or {}
        fields = {}
        for key in ("source_rotation_angle",):
            if params.get(key) is not None:
                fields[key] = params[key]
        for key in ("edc_source", "source_3d", "energy_source", "derivative_source", "path_source"):
            source = params.get(key)
            if isinstance(source, dict) and source.get("rotation_angle") is not None:
                fields[f"{key}.rotation_angle"] = source["rotation_angle"]
        slice_source = params.get("slice_source") or {}
        source_page = slice_source.get("source_page") or {}
        if source_page.get("rotation_angle") is not None:
            fields["slice_source.source_page.rotation_angle"] = source_page["rotation_angle"]
        regions = params.get("crop_regions") or []
        if regions:
            fields["crop_regions.rotation_angle"] = [region.get("rotation_angle") for region in regions]
        return fields

    def register_page(label, spec, parent_id, *, expected_angle=None):
        parent = window.left_workspace.page_specs[parent_id]
        fields = rotation_fields(spec)
        relation_ok = spec.source_page_id == parent_id and spec.data_scope_id == parent.data_scope_id
        angle_ok = True
        if expected_angle is not None and fields:
            angle_values = []
            for value in fields.values():
                angle_values.extend(value if isinstance(value, list) else [value])
            angle_ok = bool(angle_values) and all(
                value is not None and abs(float(value) - expected_angle) < 1e-9 for value in angle_values
            )
        detail = {
            "page_id": spec.page_id,
            "title": spec.title,
            "page_kind": spec.page_kind,
            "source_page_id": spec.source_page_id,
            "expected_parent_id": parent_id,
            "data_scope_id": spec.data_scope_id,
            "parent_data_scope_id": parent.data_scope_id,
            "rotation_fields": fields,
        }
        page_records.append({"label": label, **detail})
        return record(f"{label}：父子关系、data_scope 与冻结角度", relation_ok and angle_ok, detail)

    def capture(name):
        app.processEvents()
        scene_pixels = None
        if (window.current_render_context or {}).get("view") == "3d":
            window.plotter.render()
            app.processEvents()
            scene_path = args.output_dir / name.replace(".png", "-scene.png")
            scene_pixels = np.asarray(window.plotter.screenshot(str(scene_path), return_img=True))
            scene_ok = (
                scene_path.is_file() and scene_path.stat().st_size > 0
                and scene_pixels.size > 0 and float(np.std(scene_pixels[..., :3])) > 1.0
            )
            artifacts.append({
                "name": scene_path.name,
                "path": str(scene_path),
                "saved": bool(scene_ok),
                "automatic_capture": True,
                "sha256": hashlib.sha256(scene_path.read_bytes()).hexdigest() if scene_path.is_file() else None,
                "pixel_std": float(np.std(scene_pixels[..., :3])) if scene_pixels.size else None,
            })
            record(f"自动 VTK 截图 {scene_path.name}", scene_ok, str(scene_path))

        path = args.output_dir / name
        pixmap = window.grab()
        if scene_pixels is not None:
            target = window.plotter.interactor
            scene = QPixmap.fromImage(QImage(str(args.output_dir / name.replace(".png", "-scene.png"))))
            scene.setDevicePixelRatio(target.devicePixelRatioF())
            painter = QPainter(pixmap)
            painter.drawPixmap(target.mapTo(window, QPoint(0, 0)), scene)
            overlay = window.canvas_tool_overlay
            painter.drawPixmap(overlay.mapTo(window, QPoint(0, 0)), overlay.grab())
            if overlay.selection_panel.isVisible():
                painter.drawPixmap(
                    overlay.selection_panel.mapTo(window, QPoint(0, 0)), overlay.selection_panel.grab(),
                )
            painter.end()
        ok = pixmap.save(str(path)) and path.is_file() and path.stat().st_size > 0
        artifacts.append({
            "name": name,
            "path": str(path),
            "saved": bool(ok),
            "automatic_capture": True,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        })
        record(f"自动窗口截图 {name}", ok, str(path))

    def set_rotation(angle):
        overlay = window.canvas_tool_overlay
        if not overlay.rotation_panel.isVisible():
            click(overlay.rotation_button, "Z 轴旋转旋钮")
        panel = overlay.rotation_panel
        if abs(angle) < 1e-12:
            click(panel.zero_button, "旋钮归零")
        else:
            editor = panel.edit_rotation.lineEdit()
            QTest.mouseClick(editor, Qt.LeftButton, Qt.NoModifier, editor.rect().center())
            QTest.keyClick(editor, Qt.Key_A, Qt.ControlModifier)
            QTest.keyClicks(editor, format(float(angle), ".12g"))
            QTest.keyClick(editor, Qt.Key_Return)
            app.processEvents()
        wait_for(
            f"原生旋钮 {angle:g}° 精确结果",
            lambda: abs(float(window.rotation_angle) - angle) < 1e-9
            and page_ready(current_page_id())
            and abs(float((window.current_render_context or {}).get("rotation_angle", 0.0)) - angle) < 1e-9,
            150,
        )
        record(f"真实旋钮设置 {angle:g}° 并等待精确结果", True, {
            "window_angle": window.rotation_angle,
            "dial_angle": panel.dial.angle(),
            "context_angle": window.current_render_context.get("rotation_angle"),
        })
        if panel.isVisible():
            click(overlay.rotation_button, "收起 Z 轴旋转旋钮")

    def set_derived_window_angle(angle):
        window.timeline_bar.set_rotation_angle(float(angle))
        window.on_rotation_angle_changed()
        app.processEvents()
        wait_for(
            f"派生页时间线角度 {angle:g}° 精确结果",
            lambda: abs(float(window.rotation_angle) - angle) < 1e-9 and page_ready(current_page_id()),
            180,
        )
        record("派生页恢复来源角度后，通过时间线接口再次归零", abs(float(window.rotation_angle) - angle) < 1e-9, {
            "page_id": current_page_id(),
            "window_angle": window.rotation_angle,
            "source_angle": rotation_fields(current_spec()),
        })

    def close_selection_tools(*, keep_selection=False):
        overlay = window.canvas_tool_overlay
        popup = window.crop_controller.popup
        if popup.isVisible():
            for button in (window.btn_tb_crop, window.btn_tb_erase):
                if button.isChecked():
                    click(button, "收起范围浮窗")
                    break
        if not keep_selection and overlay.selection_button.isChecked():
            click(overlay.selection_button, "收起框选工具")

    def open_selection(operation):
        overlay = window.canvas_tool_overlay
        if not overlay.selection_button.isChecked():
            click(overlay.selection_button, "框选范围")
        button = window.btn_tb_erase if operation == "erase" else window.btn_tb_crop
        popup = window.crop_controller.popup
        for _attempt in range(3):
            selection = window.crop_controller.selection
            if popup.isVisible() and selection is not None and selection.operation == operation:
                return
            click(button, "裁空" if operation == "erase" else "裁剪")
        raise AssertionError(f"无法打开{operation}范围浮窗")

    def set_popup_bounds(bounds):
        popup = window.crop_controller.popup
        if not popup.isVisible():
            raise AssertionError("范围浮窗未打开")
        expected = tuple(float(format(float(value), ".12g")) for value in bounds)
        popup.raise_()
        popup.activateWindow()
        for edit, value in zip(popup.edits, expected):
            text = format(value, ".12g")
            QTest.mouseClick(edit, Qt.LeftButton, Qt.NoModifier, edit.rect().center())
            edit.setFocus(Qt.ActiveWindowFocusReason)
            edit.selectAll()
            QTest.qWait(30)
            QTest.keyClicks(edit, text)
            QTest.qWait(30)
            if edit.text() != text:
                raise AssertionError(f"范围输入框键入不一致：{edit.text()!r} != {text!r}")
            QTest.keyClick(edit, Qt.Key_Return)
            app.processEvents()
        actual = window.crop_controller.selection.bounds
        if not np.allclose(actual, expected, rtol=0.0, atol=0.0):
            raise AssertionError(f"范围输入不一致：{actual} != {expected}")
        return expected

    def spatial_child(parent_id, operation, bounds, screenshot):
        attempted.add(operation)
        activate(parent_id, "3d")
        source = current_spec()
        before = set(window.left_workspace.page_specs)
        open_selection(operation)
        actual_bounds = set_popup_bounds(bounds)
        click(window.crop_controller.popup.apply_button, "确认裁空" if operation == "erase" else "确认裁剪")
        spec = wait_new_page(
            before, "home", "3d", f"30° {operation} 确认入口建页",
        )
        pages[operation] = spec.page_id
        region = spec.params["crop_regions"][-1]
        context = window.current_render_context
        descriptor = scoped_descriptor_from_array(context["data"])
        operation_detail = {
            "operation": operation,
            "input_bounds": list(actual_bounds),
            "region": region,
            "page_id": spec.page_id,
            "parent_id": parent_id,
            "data_scope_id": spec.data_scope_id,
            "context_data_bounds": context.get("data_bounds"),
            "context_full_shape": context.get("full_shape"),
            "descriptor_scope_id": getattr(descriptor, "scope_id", None),
            "descriptor_bounds": getattr(descriptor, "bounds", None),
        }
        spatial_operations.append(operation_detail)
        ok = (
            region.get("operation") == operation
            and abs(float(region.get("rotation_angle")) - ANGLE) < 1e-9
            and len(region.get("logical_bounds") or ()) == 6
            and abs(float(context.get("rotation_angle")) - ANGLE) < 1e-9
            and not context.get("crop_empty", False)
        )
        record(f"30° {operation} 写入 rotation_angle、逻辑范围与数据域", ok, operation_detail)
        register_page(f"30° {operation}", spec, parent_id, expected_angle=ANGLE)
        data = np.asarray(context["data"])
        content_ok = bool(np.isfinite(data).any())
        if operation == "erase":
            content_ok = content_ok and bool(np.isnan(data).any())
        record(f"30° {operation} 结果具有有效保留/排除内容", content_ok, {
            "shape": list(data.shape),
            "finite_samples": int(np.isfinite(data).sum()),
            "nan_samples": int(np.isnan(data).sum()),
        })
        capture(screenshot)
        executed.append(f"真实范围浮窗在 30° 确认{operation}，截图并记录页面/来源/数据域")
        required[operation] = True
        return spec

    def reference_spatial(spec, time_index, angle=ANGLE):
        rotated = window.backend_manager.cpu.rotate_volume(
            window.original_raw_data[..., int(time_index)], float(angle),
        )
        base = {
            "view": "3d",
            "data": rotated,
            "coords": window.core.coords,
            "rotation_angle": float(angle),
        }
        return apply_crop_regions(
            base, list(spec.params.get("crop_regions") or ()), rotation_angle=float(angle),
        )

    def create_axis_slice(parent_id, plane):
        activate(parent_id, "3d")
        close_selection_tools()
        parent = current_spec()
        parent_context = window.current_render_context
        axis_index = {"xy": 2, "xz": 1, "yz": 0}[plane]
        before = set(window.left_workspace.page_specs)
        click(window.canvas_tool_overlay.preset_buttons[plane], f"{plane.upper()} 切片")
        spec = wait_new_page(before, "axis_integral", "2d", f"{plane.upper()} 切片页")
        pages[f"{plane}_{parent_id}"] = spec.page_id
        register_page(f"{plane.upper()} 切片", spec, parent_id, expected_angle=ANGLE)
        source_3d = spec.params.get("source_3d") or {}
        record(f"{plane.upper()} 切片冻结 30° 三维来源", (
            source_3d.get("page_kind") == "home"
            and abs(float(source_3d.get("rotation_angle")) - ANGLE) < 1e-9
            and source_3d.get("data_scope_id") == parent.data_scope_id
        ), {
            "source_3d_rotation": source_3d.get("rotation_angle"),
            "source_3d_scope": source_3d.get("data_scope_id"),
            "axis_index": axis_index,
            "normal_range": [spec.params.get("low"), spec.params.get("up")],
        })
        expected = _expected_axis_integral(
            parent_context, axis_index, int(spec.params["low"]), int(spec.params["up"]),
        )
        compare(
            f"{plane.upper()} 切片由实际父页独立求和",
            window.current_render_context["data"], expected,
            extra={"parent_page_id": parent_id, "source_rotation_angle": ANGLE},
            production_issue=True,
        )
        executed.append(f"从 {parent.title} 通过画布按钮创建 {plane.upper()} 并独立求和")
        return spec, parent_context

    def create_from_analysis_menu(parent_id, action_key, kind, view):
        activate(parent_id, "2d")
        close_selection_tools()
        before = set(window.left_workspace.page_specs)
        callback = {}

        def completed(report):
            callback.update(report)
            callback["done"] = True

        create_from_page_menu(window, parent_id, action_key, completed)
        wait_for(f"{action_key} 真实右键菜单", lambda: callback.get("done", False), 25)
        record(f"真实视图右键触发 {action_key}", (
            callback.get("opened") and callback.get("create_present")
            and action_key in callback.get("keys", []) and callback.get("triggered")
        ), callback)
        if not callback.get("triggered"):
            raise AssertionError(f"菜单动作未触发：{callback}")
        return wait_new_page(before, kind, view, f"{action_key} 结果页")

    def choose_menu_action(text):
        outcome = {"done": False, "found": False, "labels": []}

        def poll(remaining=100):
            popup = QApplication.activePopupWidget()
            if isinstance(popup, QMenu):
                actions = [action for action in popup.actions() if not action.isSeparator()]
                outcome["labels"] = [action.text() for action in actions]
                target = next((action for action in actions if action.text() == text), None)
                if target is not None and target.isEnabled():
                    outcome["found"] = True
                    outcome["done"] = True
                    QTest.mouseClick(popup, Qt.LeftButton, Qt.NoModifier, popup.actionGeometry(target).center())
                    popup.close()
                    return
            if remaining <= 0:
                outcome["done"] = True
                return
            QTimer.singleShot(50, lambda: poll(remaining - 1))

        QTimer.singleShot(50, poll)
        return outcome

    def canvas_position(logical):
        physical = (
            window.core.logical_to_physical("X", logical[0]),
            window.core.logical_to_physical("Y", logical[1]),
        )
        x, y = window.ax_2d.transData.transform(physical)
        ratio = window.canvas_2d.devicePixelRatioF()
        height = window.canvas_2d.get_width_height(physical=True)[1]
        return QPointF(x / ratio, (height - y) / ratio).toPoint()

    def create_path_slice(parent_id):
        attempted.add("path_slice")
        activate(parent_id, "2d")
        close_selection_tools()
        source = current_spec()
        controller = window.path_slice_controller
        click(controller.button, "折线路径选点")
        nx, ny = window.core.raw_data.shape[:2]
        logical_points = [(0.20 * nx, 0.50 * ny), (0.80 * nx, 0.50 * ny)]
        for logical in logical_points:
            QTest.mouseClick(window.canvas_2d, Qt.LeftButton, Qt.NoModifier, canvas_position(logical))
            app.processEvents()
        QTest.qWait(2 * QApplication.doubleClickInterval() + 350)
        nodes = draft_nodes(source.params)
        record("真实鼠标在裁空 XY 页落下两个路径节点", len(nodes) == 2, {
            "nodes": [(node.x, node.y, node.label) for node in nodes],
            "active_source_id": controller.active_source_id,
            "parent_id": parent_id,
        })
        before = set(window.left_workspace.page_specs)
        outcome = choose_menu_action("生成路径切片")
        position = canvas_position(logical_points[-1])
        event = QContextMenuEvent(
            QContextMenuEvent.Mouse, position, window.canvas_2d.mapToGlobal(position),
        )
        QApplication.sendEvent(window.canvas_2d, event)
        app.processEvents()
        record("路径草稿右键选择生成路径切片", outcome["found"], outcome)
        if not outcome["found"]:
            raise AssertionError(f"未找到路径生成菜单：{outcome}")
        spec = wait_new_page(before, "path_slice", "path_slice", "裁空 XY 路径切片页")
        pages["path_slice"] = spec.page_id
        register_page("裁空 XY 路径切片", spec, parent_id, expected_angle=ANGLE)
        reference = reference_spatial(window.left_workspace.page_specs[pages["erase"]], 0)
        model = PathSliceModel.from_params(spec.params)
        calibration = MomentumCalibration.create(
            window.core.coords["X"], window.core.coords["Y"],
            x_source=window.core.coord_sources.get("X", "index"),
            y_source=window.core.coord_sources.get("Y", "index"),
            x_unit=window.core.coord_units.get("X"),
            y_unit=window.core.coord_units.get("Y"),
        )
        expected = PathSliceSampler().sample(
            np.asarray(reference["data"], dtype=np.float64),
            window.core.coords["E"], model, calibration,
            valid_mask=np.ones(np.asarray(reference["data"]).shape, dtype=bool),
        )
        compare(
            "路径切片由原始帧旋转与裁空结果独立取样",
            window.current_render_context["raw_intensity"], expected.intensity,
            rtol=3e-5, atol=3e-5,
            extra={"path_source_angle": spec.params["path_source"].get("rotation_angle")},
            production_issue=True,
        )
        capture("07-erased-xy-path.png")
        executed.append("裁空 XY 通过真实鼠标选点和右键菜单创建路径切片并独立取样")
        required["path_slice"] = True
        return spec

    def set_scan_frame(target):
        slider = window.timeline_bar.slider_time
        slider.setValue(int(target))
        app.processEvents()
        QTest.qWait(250)
        coords = np.asarray(window.core.coords["delay"], dtype=np.float64)

        def ready():
            spec = current_spec()
            interval = (spec.params or {}).get("time_interval") or {}
            return (
                int(window.timeline_bar.slider_time.value()) == int(target)
                and abs(float(interval.get("low", np.nan)) - float(coords[target])) < 1e-9
                and abs(float(interval.get("up", np.nan)) - float(coords[target])) < 1e-9
                and page_ready(spec.page_id)
            )

        wait_for(f"扫描帧 {target} 精确结果", ready, 180)
        record("通过真实时间控制切换扫描帧", True, {
            "target_index": int(target),
            "target_value": float(coords[target]),
            "window_angle": window.rotation_angle,
            "page_id": current_page_id(),
        })

    def note_production_issue(stage, exc):
        detail = {
            "stage": stage,
            "error": str(exc),
            "reproduction": [
                "加载 .local/data/WSe2_step.npz",
                "原生 Z 轴旋钮设置 30° 并等待精确结果",
                "在中央 X/Y/E 范围确认裁剪或裁空",
                "从结果页点击画布切片或处理分析入口",
            ],
            "diagnostics": diagnostics(),
        }
        production_issues.append({"stage": stage, **detail})
        record(f"生产问题：{stage}", False, detail)

    try:
        window.load_data(str(args.data))
        home_id = window.left_workspace.home_page_id
        wait_for("WSe₂ 真实 VTK 三维主页", lambda: page_ready(home_id, "3d"), 180)
        home = current_spec()
        pages["home"] = home_id
        record("加载默认/显式真实 NPZ，未使用合成回退", window.core.raw_data is not None, {
            "data": str(args.data),
            "shape": list(window.core.raw_data.shape),
            "file_size": args.data.stat().st_size,
        })
        set_rotation(ANGLE)
        nx, ny, ne = (int(value) for value in window.core.raw_data.shape[:3])
        region = (
            max(1, nx // 3), min(nx - 2, 2 * nx // 3),
            max(1, ny // 3), min(ny - 2, 2 * ny // 3),
            max(0, ne // 10), min(ne - 1, max(ne // 10, 9 * ne // 10 - 1)),
        )
        bounds = _physical_bounds(window.current_render_context["coords"], region)
        crop_spec = spatial_child(home_id, "crop", bounds, "01-rotated-crop.png")
        erase_spec = spatial_child(home_id, "erase", bounds, "02-rotated-erase.png")

        attempted.add("edc")
        activate(crop_spec.page_id, "3d")
        open_selection("crop")
        logical = crop_spec.params["crop_regions"][-1]["logical_bounds"]
        inner = (
            int(logical[0] + 0.20 * (logical[1] - logical[0])),
            int(logical[0] + 0.80 * (logical[1] - logical[0])),
            int(logical[2] + 0.20 * (logical[3] - logical[2])),
            int(logical[2] + 0.80 * (logical[3] - logical[2])),
            int(logical[4]), int(logical[5]),
        )
        set_popup_bounds(_physical_bounds(window.current_render_context["coords"], inner))
        expected_energy, expected_edc, captured_edc = _expected_curve(
            window.current_render_context, window.crop_controller.selection,
        )
        close_selection_tools(keep_selection=True)
        before = set(window.left_workspace.page_specs)
        click(window.canvas_tool_overlay.edc_button, "生成 EDC")
        edc_spec = wait_new_page(before, "edc_curve", "1d", "30° 裁剪来源 EDC")
        pages["edc"] = edc_spec.page_id
        register_page("30° 裁剪来源 EDC", edc_spec, crop_spec.page_id, expected_angle=ANGLE)
        metadata = (edc_spec.params.get("curve_metadata") or [{}])[0]
        record("EDC 冻结选区角度与 30° 裁剪历史", (
            abs(float(edc_spec.params.get("source_rotation_angle")) - ANGLE) < 1e-9
            and abs(float((metadata.get("selection") or {}).get("rotation_angle")) - ANGLE) < 1e-9
            and (edc_spec.params.get("edc_source") or {}).get("params", {}).get("crop_regions")
            == crop_spec.params.get("crop_regions")
        ), {
            "source_rotation_angle": edc_spec.params.get("source_rotation_angle"),
            "selection": metadata.get("selection"),
            "captured_selection": captured_edc.to_dict(),
        })
        compare("30° EDC 按选区网格独立逐层求和", edc_spec.params["base_curve"]["y_data"], expected_edc)
        compare("30° EDC 能量坐标", edc_spec.params["base_curve"]["x_data"], expected_energy, rtol=0, atol=0)
        capture("03-rotated-crop-edc.png")
        executed.append("30° 裁剪页通过框选次级按钮创建 EDC，并按 selection_grid_mask 独立求和")
        required["edc"] = True

        attempted.add("slice_dos")
        activate(crop_spec.page_id, "3d")
        parent_sum = float(np.sum(window.current_render_context["data"], dtype=np.float64))
        open_selection("crop")
        close_selection_tools(keep_selection=True)
        before = set(window.left_workspace.page_specs)
        click(window.canvas_tool_overlay.slice_dos_button, "切片内强度积分")
        slice_spec = wait_new_page(before, "slice_dos", "1d", "30° 裁剪来源切片内强度积分", 300)
        pages["slice_dos"] = slice_spec.page_id
        register_page("30° 裁剪来源切片积分", slice_spec, crop_spec.page_id, expected_angle=ANGLE)
        actual_slice = np.asarray(window.current_render_context["y_data"], dtype=np.float64)
        record("切片积分覆盖全部扫描帧且首帧等于父页求和", (
            actual_slice.shape == (window.core.raw_data.shape[3],)
            and np.isclose(actual_slice[0], parent_sum, rtol=2e-5, atol=2e-5)
        ), {
            "frames": int(actual_slice.size),
            "actual_first": float(actual_slice[0]),
            "expected_first": parent_sum,
        })
        capture("04-rotated-crop-slice-integral.png")
        executed.append("30° 裁剪页通过框选次级按钮创建逐扫描帧切片内强度积分")
        required["slice_dos"] = True

        attempted.add("axis_slices")
        try:
            xy_spec, _xy_parent = create_axis_slice(crop_spec.page_id, "xy")
            xz_spec, _xz_source_parent = create_axis_slice(crop_spec.page_id, "xz")
            yz_spec, _yz_parent = create_axis_slice(crop_spec.page_id, "yz")
        except _RenderFailure as exc:
            axis_source_fallback = True
            note_production_issue("30° 紧凑裁剪来源的画布切片渲染", exc)
            xy_spec, _xy_parent = create_axis_slice(erase_spec.page_id, "xy")
            xz_spec, _xz_source_parent = create_axis_slice(erase_spec.page_id, "xz")
            yz_spec, _yz_parent = create_axis_slice(erase_spec.page_id, "yz")
            executed.append("紧凑裁剪切片渲染失败后，改用非紧凑裁空页继续其余切片/派生验收；不替代失败项")
        required["axis_slices"] = True
        capture("05-rotated-source-yz.png")
        activate(xz_spec.page_id, "2d")
        xz_context = window.current_render_context

        attempted.add("energy_dos")
        energy_spec = create_from_analysis_menu(
            xz_spec.page_id, "builtin:energy_dos", "energy_dos", "1d",
        )
        pages["energy_dos"] = energy_spec.page_id
        register_page("XZ Energy DOS", energy_spec, xz_spec.page_id, expected_angle=ANGLE)
        plot_bounds = xz_context["plot_logical_bounds"]
        energy_axis = np.asarray(xz_context["coords"]["E"], dtype=np.float64)[
            int(plot_bounds["y_low"]):int(plot_bounds["y_up"]) + 1
        ]
        expected_dos = np.nansum(xz_context["data"], axis=0, dtype=np.float64)
        compare(
            "Energy DOS 从实际 XZ 父页独立求和", window.current_render_context["y_data"], expected_dos,
            production_issue=True,
        )
        compare("Energy DOS 能量坐标", window.current_render_context["x_data"], energy_axis, rtol=0, atol=0)
        capture("06-rotated-xz-energy-dos.png")
        executed.append("XZ 切片通过真实视图右键菜单创建 Energy DOS 并独立求和")
        required["energy_dos"] = True

        attempted.add("second_derivative")
        derivative_spec = create_from_analysis_menu(
            xz_spec.page_id, "builtin:second_derivative", "second_derivative", "2d",
        )
        pages["second_derivative"] = derivative_spec.page_id
        register_page("XZ 二阶导", derivative_spec, xz_spec.page_id, expected_angle=ANGLE)
        threshold = float((derivative_spec.params.get("second_derivative_params") or {}).get("threshold", 0.0))
        expected_derivative = _second_derivative(xz_context["data"], energy_axis, threshold)
        compare(
            "二阶导从实际 XZ 父页独立计算", window.current_render_context["data"], expected_derivative,
            production_issue=True,
        )
        executed.append("XZ 切片通过真实视图右键菜单创建二阶导并独立微分")
        required["second_derivative"] = True

        erase_xy_spec, _erase_xy_parent = create_axis_slice(erase_spec.page_id, "xy")
        create_path_slice(erase_xy_spec.page_id)

        attempted.add("zero_frame")
        activate(crop_spec.page_id, "3d")
        close_selection_tools()
        set_rotation(0.0)
        target_frame = max(1, window.core.raw_data.shape[3] // 2)
        set_scan_frame(target_frame)
        record("归零后裁剪页数据域与父子关系保持", (
            abs(float(window.rotation_angle)) < 1e-9
            and current_page_id() == crop_spec.page_id
            and current_spec().source_page_id == home_id
            and current_spec().data_scope_id == home.data_scope_id
            and abs(float(window.current_render_context.get("rotation_angle"))) < 1e-9
        ), {
            "page_id": current_page_id(),
            "data_scope_id": current_spec().data_scope_id,
            "data_bounds": window.current_render_context.get("data_bounds"),
            "time_index": window.timeline_bar.slider_time.value(),
        })
        capture("08-crop-zero-scan-frame.png")

        activate(slice_spec.page_id, "1d")
        set_derived_window_angle(0.0)
        reference_slice = reference_spatial(crop_spec, target_frame)
        expected_slice_value = float(np.sum(reference_slice["data"], dtype=np.float64))
        actual_slice = np.asarray(window.current_render_context["y_data"], dtype=np.float64)
        compare(
            "0° 窗口下切片积分指定帧仍按 30° 冻结来源重算",
            actual_slice[target_frame:target_frame + 1], np.asarray([expected_slice_value]),
            extra={"target_frame": target_frame, "window_angle": window.rotation_angle},
        )
        required["zero_frame"] = True
        executed.append("转回 0°、切扫描帧，切片积分从原始目标帧独立旋转重放核对")

        activate(edc_spec.page_id, "1d")
        set_derived_window_angle(0.0)
        set_scan_frame(target_frame)
        reference_edc_source = reference_spatial(crop_spec, target_frame)
        saved_selection = CropSelection.from_dict(edc_spec.params["curve_metadata"][0]["selection"])
        expected_energy_target, expected_edc_target, _saved = _expected_curve(
            reference_edc_source, saved_selection,
        )
        compare("0° 窗口切帧后 EDC 仍按 30° 来源重算", window.current_render_context["y_data"], expected_edc_target)
        compare("0° 窗口切帧后 EDC 能量坐标", window.current_render_context["x_data"], expected_energy_target, rtol=0, atol=0)
        record("EDC 重算恢复当前窗口角度", abs(float(window.rotation_angle)) < 1e-9, {
            "window_angle": window.rotation_angle,
            "source_angle": edc_spec.params.get("source_rotation_angle"),
            "target_frame": target_frame,
        })
        capture("09-edc-zero-scan-frame.png")

        attempted.add("log_curve")
        source_curve = np.asarray(window.current_render_context["y_data"], dtype=np.float64)
        before = set(window.left_workspace.page_specs)
        outcome = choose_menu_action("取对数 (log10)")
        position = QPoint(max(1, window.canvas_2d.width() // 2), max(1, window.canvas_2d.height() // 2))
        window.canvas_2d.customContextMenuRequested.emit(position)
        app.processEvents()
        record("EDC 真实右键选择取对数", outcome["found"], outcome)
        if not outcome["found"]:
            raise AssertionError(f"未找到取对数菜单：{outcome}")
        log_spec = wait_new_page(before, "log_curve", "1d", "EDC log10 派生页")
        pages["log_curve"] = log_spec.page_id
        register_page("EDC log10", log_spec, edc_spec.page_id, expected_angle=ANGLE)
        expected_log = np.full(source_curve.shape, np.nan, dtype=np.float64)
        positive = np.isfinite(source_curve) & (source_curve > 0)
        expected_log[positive] = np.log10(source_curve[positive])
        compare("log10 与当前 EDC 有效正值一致", window.current_render_context["y_data"], expected_log)
        capture("10-edc-log-analysis.png")
        executed.append("0° 切帧后的 EDC 通过真实右键创建 log10 并核对曲线")
        required["log_curve"] = True
    except Exception as exc:
        state["fatal"] = f"{type(exc).__name__}: {exc}"
        record("真实窗口验收步骤异常", False, {
            "error": state["fatal"],
            "traceback": traceback.format_exc(),
            "diagnostics": diagnostics(),
        })
        traceback.print_exc()
    finally:
        not_executed = [
            {
                "item": "人工视觉复核：旋转裁剪、裁空、切片与分析结果图",
                "reason": "本次仅自动操作、数值断言和截图，未由人员逐张确认科研出图质量、选框/手柄可读性及布局。",
            },
            {
                "item": "人工鼠标手感与弹窗遮挡检查",
                "reason": "脚本注入真实 Qt/VTK 事件，但未进行人员手动拖动、手柄命中和遮挡检查。",
            },
            {
                "item": "全量回归及合成数据严格数值测试",
                "reason": "按任务要求不重复另一进程的全量回归；test_rotated_crop、test_rotated_edc、test_rotated_derived_pages、test_rotated_view_recipe 不在本实窗脚本中重复。",
            },
        ]
        if axis_source_fallback:
            not_executed.append({
                "item": "30° 紧凑裁剪来源的 XY/XZ/YZ 全方向切片验收",
                "reason": "XY 已复现生产渲染广播错误；XZ/YZ 不再对同一紧凑来源重复触发。后续切片来自非紧凑裁空页，不能记为紧凑裁剪切片通过。",
            })
        for key in required_keys:
            if key not in attempted:
                not_executed.append({
                    "item": f"流程阶段 {key}",
                    "reason": f"前序步骤终止，未执行；首个异常：{state['fatal'] or '无'}",
                })
        passed = sum(1 for item in results if item["ok"])
        failures = [item for item in results if not item["ok"]]
        success = not failures and all(required.values()) and state["fatal"] is None
        payload = {
            "requested_data": str(args.data),
            "data": str(args.data),
            "data_kind": "default_wse2" if args.data.resolve() == DEFAULT_DATA.resolve() else "explicit_npz",
            "synthetic_fallback": False,
            "data_shape": list(window.core.raw_data.shape) if window.core.raw_data is not None else None,
            "platform": {
                "system": platform.platform(),
                "qt_qpa_platform": os.environ.get("QT_QPA_PLATFORM"),
                "offscreen": False,
                "python": sys.version,
                "backend": window.backend_manager.selected_backend_name(),
            },
            "isolated_runtime": {
                "extension_root": os.environ.get("BANDSCOPE_EXTENSION_ROOT"),
                "settings": "tests.support.environment.isolate_runtime",
            },
            "requested_angle": ANGLE,
            "executed": executed,
            "required_checks": required,
            "pages": pages,
            "page_records": page_records,
            "spatial_operations": spatial_operations,
            "production_issues": production_issues,
            "axis_source_fallback": axis_source_fallback,
            "numeric_checks": numeric_checks,
            "not_executed": not_executed,
            "manual_visual_review": False,
            "automatic_screenshots_only": True,
            "artifacts": artifacts,
            "results": results,
            "failures": failures,
            "fatal_error": state["fatal"],
            "messages": messages,
            "passed": passed,
            "total": len(results),
            "success": bool(success),
        }
        report_path = args.output_dir / "report.json"
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
        )
        print(
            f"[accept] {'PASS' if success else 'FAIL'}；{passed}/{len(results)} 项通过；"
            f"required={required}；输出 {args.output_dir}",
            flush=True,
        )
        for widget in QApplication.topLevelWidgets():
            if widget.isVisible():
                widget.close()
        app.processEvents()
    return 0 if not any(not item["ok"] for item in results) and all(required.values()) and state["fatal"] is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
