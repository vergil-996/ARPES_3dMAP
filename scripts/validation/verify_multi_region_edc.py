# -*- coding: utf-8 -*-
"""真实窗口验收多框选工具条、选区弹窗及 2D/3D EDC／切片积分。

默认读取 .local/data/WSe2_step.npz，也可显式传入 NPZ。仅默认文件缺失时生成
固定合成数据继续可执行验收，并在报告中明确标注；显式路径不存在则报错。
使用本机 Qt 图形平台和真实 VTK 窗口，不以 offscreen 替代。自动截图不等于人工
视觉复核；Z 标号、手柄命中和弹窗布局仍须在桌面逐图检查。

用法: .venv/Scripts/python.exe scripts/validation/verify_multi_region_edc.py [npz路径] [--output-dir DIR]
"""
from __future__ import annotations

import argparse
import copy
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
DEFAULT_OUTPUT = REPO_ROOT / ".local" / "outputs" / "multi-region-edc"


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path, nargs="?", help=f"输入 NPZ（默认 {DEFAULT_DATA}）")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    explicit_data = args.data is not None
    args.requested_data = args.data if explicit_data else DEFAULT_DATA
    args.explicit_data = explicit_data
    if explicit_data and not args.data.is_file():
        parser.error(f"输入文件不存在：{args.data}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.synthetic_fallback = not explicit_data and not DEFAULT_DATA.is_file()
    args.data = args.output_dir / "synthetic-input.npz" if args.synthetic_fallback else args.requested_data
    if args.synthetic_fallback:
        _write_synthetic(args.data)
    if sys.platform == "win32":
        os.environ["QT_QPA_PLATFORM"] = "windows"
    elif sys.platform == "darwin":
        os.environ["QT_QPA_PLATFORM"] = "cocoa"
    else:
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    from tests.support.environment import isolate_runtime

    isolate_runtime()
    return args


def _write_synthetic(path):
    x = np.linspace(-0.8, 0.9, 12, dtype=np.float64) ** 3
    y = np.linspace(0.7, -0.6, 11, dtype=np.float64)
    energy = np.linspace(1.2, -1.0, 14, dtype=np.float64) ** 3
    delay = np.asarray([0.0, 200.0, 500.0], dtype=np.float64)
    xx, yy, ee, tt = np.meshgrid(x, y, energy, delay, indexing="ij")
    raw = (
        np.exp(-((xx - 0.08) ** 2 + (yy + 0.04) ** 2) / 0.12)
        * (1.0 + 0.17 * np.sin(ee * 2.1) + 0.001 * tt)
        + 0.003 * (xx + 1.0) + 0.002 * (yy + 1.0)
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


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def _regions_2d(shape):
    nx, ny, ne = (int(value) for value in shape)
    if min(nx, ny, ne) < 6:
        raise ValueError(f"多选区验收要求 X/Y/E 均至少 6 个采样点，实际 {shape}")
    first = (1, max(2, nx // 3), 1, max(2, ny // 3), 0, ne - 1)
    second = (max(nx // 2, 2 * nx // 3), nx - 2,
              max(ny // 2, 2 * ny // 3), ny - 2, 0, ne - 1)
    return first, second


def _regions_3d(shape):
    nx, ny, ne = (int(value) for value in shape)
    first_2d, second_2d = _regions_2d(shape)
    first = (*first_2d[:4], 1, max(2, ne // 2))
    second = (*second_2d[:2], *first_2d[2:4], max(ne // 2 + 1, ne - 3), ne - 2)
    return first, second


def _physical_bounds(coords, region):
    bounds = []
    for axis, key in enumerate(("X", "Y", "E")):
        low_index, up_index = region[2 * axis:2 * axis + 2]
        values = np.asarray(coords[key], dtype=np.float64)
        low = float(values[low_index])
        up = float(values[up_index])
        if low_index + 1 < values.size:
            low += 0.001 * (float(values[low_index + 1]) - low)
        if up_index > 0:
            up += 0.001 * (float(values[up_index - 1]) - up)
        bounds.extend((min(low, up), max(low, up)))
    return tuple(bounds)


def _expected_curve(source, energy, region):
    """按 X/Y/E 完整索引区间求 EDC；法向积分已包含在 region 中。"""
    x0, x1, y0, y1, e0, e1 = (int(value) for value in region)
    selected = np.asarray(source)[x0:x1 + 1, y0:y1 + 1, e0:e1 + 1]
    return (
        np.asarray(energy, dtype=np.float64)[e0:e1 + 1],
        np.nansum(selected, axis=(0, 1), dtype=np.float64),
    )


def _physical_bounds_for_axes(coords, region, axes):
    bounds = _physical_bounds(coords, region)
    by_axis = {key: bounds[2 * index:2 * index + 2]
               for index, key in enumerate(("X", "Y", "E"))}
    return tuple(value for key in axes for value in by_axis[key])


def _opacity_nodes(function):
    if function is None:
        return []
    nodes = []
    for index in range(function.GetSize()):
        node = [0.0, 0.0, 0.0, 0.0]
        function.GetNodeValue(index, node)
        nodes.append([float(value) for value in node])
    return nodes


def _dimmed_nodes(nodes):
    return [[x, y * 0.15, midpoint, sharpness] for x, y, midpoint, sharpness in nodes]


def _widget_bounds(widget):
    import vtk

    poly = vtk.vtkPolyData()
    widget.GetPolyData(poly)
    return tuple(float(value) for value in poly.GetBounds())


def _visible_renderer_bounds(renderer):
    props = renderer.GetViewProps()
    props.InitTraversal()
    bounds = []
    for _ in range(props.GetNumberOfItems()):
        prop = props.GetNextProp()
        if prop.GetVisibility() and prop.GetBounds() is not None:
            bounds.append(tuple(float(value) for value in prop.GetBounds()))
    return bounds


def _visible_renderer_texts(renderer):
    if renderer is None:
        return []
    props = renderer.GetViewProps()
    props.InitTraversal()
    texts = []
    for _ in range(props.GetNumberOfItems()):
        prop = props.GetNextProp()
        if not prop.GetVisibility():
            continue
        getter = getattr(prop, "GetInput", None)
        if getter is not None:
            texts.append(str(getter()))
    return texts


def main():
    args = _arguments()

    from PyQt5.QtCore import Qt, QTimer, QPoint
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    import bandscope.app.refactored_app as refactored_app

    refactored_app.UpdateController = _NullUpdateController
    app = QApplication(sys.argv)
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.resize(1600, 950)
    window.show()
    messages = []
    window._show_message = lambda title, text, *a, **k: messages.append((title, text))

    results = []
    executed = []
    artifacts = []
    numeric_checks = []
    not_executed = [{
        "item": "人工视觉复核",
        "reason": "本脚本仅自动操作、断言并截图，未由人员逐张复核科研出图质量。",
    }]
    if args.synthetic_fallback:
        not_executed.append({
            "item": "默认 WSe₂ 真实数据验收",
            "reason": f"默认文件不存在：{DEFAULT_DATA}；本次使用固定合成 NPZ。",
        })
    state = {
        "finished": False,
        "2d_comparison": False,
        "erase_edc": False,
        "slice_dos": False,
        "3d_comparison": False,
        "xz_edc": False,
        "yz_edc": False,
        "2d_remove": False,
        "3d_remove": False,
    }

    def record(item, ok, detail=""):
        entry = {"item": str(item), "ok": bool(ok), "detail": detail}
        results.append(entry)
        rendered = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, default=str)
        print(f"[{'PASS' if ok else 'FAIL'}] {item}  {rendered}", flush=True)

    def capture(name):
        from PyQt5.QtCore import QPoint
        from PyQt5.QtGui import QImage, QPainter, QPixmap

        path = args.output_dir / name
        app.processEvents()
        window.plotter.render()
        app.processEvents()
        scene_pixels = None
        scene_path = None
        if (window.current_render_context or {}).get("view") == "3d":
            scene_path = args.output_dir / name.replace(".png", "-scene.png")
            scene_pixels = np.asarray(window.plotter.screenshot(str(scene_path), return_img=True))
            scene_ok = (
                scene_path.is_file() and scene_path.stat().st_size > 0
                and scene_pixels.size > 0 and float(np.std(scene_pixels[..., :3])) > 1.0
            )
            artifacts.append({"name": scene_path.name, "path": str(scene_path),
                              "saved": bool(scene_ok), "automatic_capture": True,
                              "sha256": hashlib.sha256(scene_path.read_bytes()).hexdigest(),
                              "pixel_std": float(np.std(scene_pixels[..., :3]))})
            record(f"自动 VTK 帧缓冲截图 {scene_path.name}", scene_ok, str(scene_path))

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
                painter.drawPixmap(overlay.selection_panel.mapTo(window, QPoint(0, 0)),
                                   overlay.selection_panel.grab())
            painter.end()
        ok = pixmap.save(str(path))
        artifacts.append({"name": name, "path": str(path), "saved": bool(ok), "automatic_capture": True})
        record(f"自动截图 {name}", ok, str(path))
        return {"ok": ok, "scene_pixels": scene_pixels, "scene_path": scene_path}

    def current_page_id():
        spec = window.left_workspace.current_spec()
        return spec.page_id if spec is not None else None

    def page_ready(page_id, view=None):
        if current_page_id() != page_id or window.current_render_context is None:
            return False
        if view is not None and window.current_render_context.get("view") != view:
            return False
        if getattr(window, "_render_context_page_id", page_id) != page_id:
            return False
        return bool(getattr(window, "_render_exact_ready", True))

    def wait_for(desc, predicate, next_step, timeout_ms=90000):
        deadline = time.perf_counter() + timeout_ms / 1000.0

        def poll():
            if state["finished"]:
                return
            try:
                ok = bool(predicate())
            except Exception:
                ok = False
            if ok:
                next_step()
            elif time.perf_counter() < deadline:
                QTimer.singleShot(200, poll)
            else:
                record(desc, False, {
                    "reason": "等待超时",
                    "current_page_id": current_page_id(),
                    "context": window.current_render_context,
                })
                finish()

        poll()

    def guard(step):
        try:
            step()
        except Exception as exc:
            record("验收步骤异常", False, f"{type(exc).__name__}: {exc}")
            messages.append(("traceback", traceback.format_exc()))
            finish()

    def guarded(step):
        return lambda *a, **k: guard(lambda: step(*a, **k))

    def click(button, label=None):
        if not button.isVisible() or not button.isEnabled():
            raise AssertionError(f"按钮不可点击：{label or button.toolTip()}")
        QTest.mouseClick(button, Qt.LeftButton, Qt.NoModifier, button.rect().center())
        app.processEvents()

    def validate_toolbar_placement():
        overlay = window.canvas_tool_overlay
        secondary = [overlay.new_region_button, overlay.edc_button, overlay.slice_dos_button]
        primary_ok = all(
            button.parentWidget() is overlay and overlay.selection_panel.layout().indexOf(button) < 0
            for button in (window.btn_tb_crop, window.btn_tb_erase)
        )
        secondary_ok = all(
            button.parentWidget() is overlay.selection_panel
            and overlay.selection_panel.layout().indexOf(button) >= 0
            for button in secondary
        )
        record("裁剪/裁空位于画布初级栏，新增/EDC/切片积分位于框选次级栏", (
            primary_ok and secondary_ok
            and overlay.slice_dos_button.toolTip() == "切片内强度积分"
        ), {
            "crop_parent": window.btn_tb_crop.parentWidget().objectName(),
            "erase_parent": window.btn_tb_erase.parentWidget().objectName(),
            "secondary": [button.toolTip() for button in secondary],
        })

    def validate_selection_labels(expected_ids, view):
        controller = window.crop_controller
        labels = [controller.selection_label(selection_id) for selection_id in expected_ids]
        expected = [f"Z{selection_id}" for selection_id in expected_ids]
        if view.casefold() == "2d":
            visible = [
                window.axis_crop_selectors[selection_id]._label_artist.get_text()
                for selection_id in expected_ids
                if window.axis_crop_selectors[selection_id]._label_artist.get_visible()
            ]
        else:
            visible = _visible_renderer_texts(getattr(window, "_crop_box_renderer", None))
        record(f"{view} 每个选区显示稳定 Z 标号", (
            labels == expected and len(set(labels)) == len(expected_ids)
            and all(label in visible for label in expected)
        ), {"selection_ids": list(expected_ids), "labels": labels, "visible_texts": visible})

    def capture_popup(name):
        path = args.output_dir / name
        popup = window.crop_controller.popup
        ok = popup.isVisible() and popup.grab().save(str(path))
        artifacts.append({"name": name, "path": str(path), "saved": bool(ok),
                          "automatic_capture": True})
        record(f"自动截图选区弹窗 {name}", ok, str(path))

    def validate_selection_popup(selection_id):
        controller = window.crop_controller
        popup = controller.popup
        selection = dict(controller.selections_for_page()).get(selection_id)
        label = controller.selection_label(selection_id)
        actual = tuple(float(edit.text()) for edit in popup.edits[:len(selection.bounds)])
        ok = (
            popup.isVisible() and controller.active_selection_id == selection_id
            and controller.selection == selection and actual == tuple(selection.bounds)
            and label in popup.title_label.text()
            and popup.remove_button.isVisible() and popup.remove_button.isEnabled()
        )
        record(f"右键 {label} 手柄只编辑对应选区并可移除", ok, {
            "expected_id": selection_id,
            "active_id": controller.active_selection_id,
            "title": popup.title_label.text(),
            "expected_bounds": selection.bounds,
            "actual_bounds": actual,
        })
        if not ok:
            raise AssertionError(f"{label} 手柄右键弹窗不符合选区契约")

    def right_click_2d_selection(selection_id, *, remove=False):
        selector = window.axis_crop_selectors[selection_id]
        x0, x1, y0, y1 = selector.extents
        pixel_x, pixel_y = window.ax_2d.transData.transform(((x0 + x1) / 2, (y0 + y1) / 2))
        ratio = float(window.canvas_2d.devicePixelRatioF())
        position = QPoint(
            int(round(pixel_x / ratio)),
            int(round((window.canvas_2d.figure.bbox.height - pixel_y) / ratio)),
        )
        QTest.mouseClick(window.canvas_2d, Qt.RightButton, Qt.NoModifier, position)
        app.processEvents()
        validate_selection_popup(selection_id)
        capture_popup(f"popup-2d-z{selection_id}.png")
        if remove:
            before = dict(selections())
            click(window.crop_controller.popup.remove_button, f"移除 Z{selection_id}")
            after = dict(selections())
            ok = selection_id not in after and set(after) == set(before) - {selection_id}
            state["2d_remove"] = ok
            record(f"移除 2D Z{selection_id} 不影响其他选区", ok, {
                "before": list(before), "after": list(after),
                "selectors": list(window.axis_crop_selectors),
            })

    def right_click_3d_selection(selection_id, *, remove=False):
        from PyQt5.QtCore import QPoint

        import vtk

        widget = window._crop_box_widgets[selection_id]
        poly = vtk.vtkPolyData()
        returned = widget.GetPolyData(poly)
        if hasattr(returned, "GetPoints"):
            poly = returned
        point = poly.GetPoints().GetPoint(8)
        renderer = widget.GetCurrentRenderer() or window._crop_box_renderer
        renderer.SetWorldPoint(float(point[0]), float(point[1]), float(point[2]), 1.0)
        renderer.WorldToDisplay()
        display = renderer.GetDisplayPoint()
        target = window.plotter.interactor
        position = QPoint(int(round(display[0])), int(round(target.height() - display[1])))
        QTest.mouseClick(target, Qt.RightButton, Qt.NoModifier, position)
        app.processEvents()
        validate_selection_popup(selection_id)
        capture_popup(f"popup-3d-z{selection_id}.png")
        if remove:
            before = dict(selections())
            click(window.crop_controller.popup.remove_button, f"移除 Z{selection_id}")
            after = dict(selections())
            ok = selection_id not in after and set(after) == set(before) - {selection_id}
            state["3d_remove"] = ok
            record(f"移除 3D Z{selection_id} 不影响其他交互盒", ok, {
                "before": list(before), "after": list(after),
                "boxes": list(window._crop_box_widgets),
            })

    def commit_normal_interval(axis_index, low, up):
        key = ("X", "Y", "E")[int(axis_index)]
        coords = np.asarray(window.current_render_context["coords"][key], dtype=np.float64)
        control = window.timeline_bar.interval_control
        initially_locked = bool(window.axis_interval.locked)
        if initially_locked:
            click(control.range_slider.lock_badge, f"{key} 法向区间锁徽章")
        app.processEvents()
        unlocked = not window.axis_interval.locked
        record(f"{key} 法向区间通过真实锁徽章解锁", unlocked, {
            "initially_locked": initially_locked,
            "locked": bool(window.axis_interval.locked),
            "lock_badge_visible": control.range_slider.lock_badge.isVisible(),
        })
        if not unlocked:
            raise AssertionError(f"{key} 法向区间仍锁定，不能分别提交双端")
        target_low, target_up = sorted((float(coords[low]), float(coords[up])))
        if target_low > float(window.axis_interval.up):
            submissions = (("up", target_up), ("low", target_low))
        else:
            submissions = (("low", target_low), ("up", target_up))
        for endpoint, value in submissions:
            control.endpointCommitted.emit(endpoint, value)
        app.processEvents()
        actual = window.axis_interval.to_indices(coords)
        record(f"{key} 法向双端滑条区间提交为 [{low}, {up}]", actual == (low, up), {
            "axis": key, "expected": [low, up], "actual": list(actual),
            "target_physical": [target_low, target_up],
            "submission_order": [endpoint for endpoint, _value in submissions],
            "physical": [window.axis_interval.low, window.axis_interval.up],
        })
        if actual != (low, up):
            raise AssertionError(f"法向区间不一致：{actual} != {(low, up)}")
        return actual

    def prepare_axis_source(parent_id):
        spec = window.left_workspace.page_specs[parent_id]
        raw_data, display_coords = window._get_display_state_for_spec(spec)
        resolved = window._resolved_axis_integral_params(spec)
        axis_params = copy.deepcopy(spec.params)
        axis_params.update(copy.deepcopy(resolved))
        source_spec = window._build_axis_source_spec_from_params(axis_params)
        source_context = window._get_3d_source_context_for_axis(source_spec, raw_data)
        return np.asarray(source_context["data"]), display_coords

    def validate_axis_edc(spec, parent_id, region, source, coords, label):
        curve = spec.params.get("base_curve") or {}
        metadata = (spec.params.get("curve_metadata") or [{}])[0]
        expected_energy, expected_intensity = _expected_curve(source, coords["E"], region)
        actual_energy = np.asarray(curve.get("x_data") or [], dtype=np.float64)
        actual_intensity = np.asarray(curve.get("y_data") or [], dtype=np.float64)
        source_ok = (
            spec.page_kind == "edc_curve" and spec.source_page_id == parent_id
            and not spec.params.get("overlay_curves")
            and spec.params.get("source_view") == "2d"
            and tuple(metadata.get("index_bounds") or ()) == tuple(region)
        )
        energy_ok = actual_energy.shape == expected_energy.shape and np.allclose(
            actual_energy, expected_energy, rtol=1e-12, atol=1e-12
        )
        intensity_ok = actual_intensity.shape == expected_intensity.shape and np.allclose(
            actual_intensity, expected_intensity, rtol=1e-10, atol=1e-9
        )
        record(f"{label}：框选两轴 + 法向区间数值与冻结来源", (
            source_ok and energy_ok and intensity_ok
        ), {
            "expected_index_bounds": list(region),
            "actual_index_bounds": metadata.get("index_bounds"),
            "energy_range": metadata.get("energy_range"),
            "energy_samples": int(actual_energy.size),
            "max_intensity_error": float(np.max(np.abs(actual_intensity - expected_intensity)))
            if actual_intensity.shape == expected_intensity.shape and actual_intensity.size else None,
        })
        numeric_checks.append({"label": label, "source_ok": source_ok,
                               "energy_ok": energy_ok, "intensity_ok": intensity_ok,
                               "index_bounds": metadata.get("index_bounds")})
        return source_ok and energy_ok and intensity_ok

    def validate_slice_dos(spec, parent_id, expected, normal_range):
        context = window.current_render_context or {}
        actual_values = context.get("y_data")
        actual = np.asarray([] if actual_values is None else actual_values, dtype=np.float64)
        source = spec.params.get("slice_source") or {}
        frozen_info = (source.get("context") or {}).get("slice_info") or {}
        frozen_range = tuple(int(value) for value in frozen_info.get("range") or ())
        ok = (
            spec.page_kind == "slice_dos" and spec.source_page_id == parent_id
            and context.get("view") == "1d" and frozen_range == tuple(normal_range)
            and actual.shape == expected.shape
            and np.allclose(actual, expected, rtol=1e-6, atol=1e-5)
        )
        record("2D 切片积分 = 活动框选 + 法向双端区间（逐扫描帧求和）", ok, {
            "source_page_id": spec.source_page_id,
            "frozen_normal_range": frozen_range,
            "expected_normal_range": list(normal_range),
            "samples": int(actual.size),
            "intensity_sum": float(np.nansum(actual)) if actual.size else None,
            "expected_intensity_sum": float(np.nansum(expected)),
            "max_error": float(np.max(np.abs(actual - expected))) if actual.shape == expected.shape and actual.size else None,
        })
        numeric_checks.append({"label": "2D slice DOS", "ok": ok,
                               "normal_range": frozen_range})
        return ok

    def set_popup_bounds(bounds):
        popup = window.crop_controller.popup
        if not popup.isVisible():
            raise AssertionError("范围浮窗未打开，无法输入选区")
        if len(bounds) != 4 and len(bounds) != 6:
            raise AssertionError(f"非法选区维度：{bounds}")
        expected = tuple(float(format(float(value), ".12g")) for value in bounds)
        for edit, value in zip(popup.edits, expected):
            QTest.mouseClick(edit, Qt.LeftButton, Qt.NoModifier, edit.rect().center())
            QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
            QTest.keyClicks(edit, format(value, ".12g"))
            QTest.keyClick(edit, Qt.Key_Return)
            app.processEvents()
        actual = window.crop_controller.selection.bounds
        if not np.allclose(actual, expected, rtol=0.0, atol=0.0):
            raise AssertionError(f"浮窗范围提交不一致：{actual} != {expected}")
        return expected

    def selections():
        return tuple(window.crop_controller.selections_for_page(current_page_id()))

    def volume_opacity():
        volume = window._find_main_volume()
        if volume is None:
            return []
        return _opacity_nodes(volume.GetProperty().GetScalarOpacity())

    def validate_crop_widgets(expected_ids, label):
        widgets = list(window.plotter.box_widgets)
        overlay = getattr(window, "_crop_box_renderer", None)
        widget_bounds = [_widget_bounds(widget) for widget in widgets]
        visible_bounds = _visible_renderer_bounds(overlay) if overlay is not None else []
        match_counts = [
            sum(np.allclose(bounds, visible, rtol=1e-7, atol=1e-7) for visible in visible_bounds)
            for bounds in widget_bounds
        ]
        enabled = [bool(widget.GetEnabled()) for widget in widgets]
        interactive = [bool(widget.GetInteractor()) for widget in widgets]
        current_renderers = [widget.GetCurrentRenderer() for widget in widgets]
        ok = (
            len(widgets) == len(expected_ids) == len(window._crop_box_widgets)
            and set(window._crop_box_widgets) == set(expected_ids)
            and all(enabled) and all(interactive)
            and overlay is not None and overlay.GetInteractive()
            and overlay.GetActiveCamera() == window.plotter.renderer.GetActiveCamera()
            and all(renderer == overlay for renderer in current_renderers)
            and all(count > 0 for count in match_counts)
            and len(set(widget_bounds)) == len(expected_ids)
        )
        detail = {
            "selection_ids": list(expected_ids),
            "plotter_widget_count": len(widgets),
            "owner_widget_count": len(window._crop_box_widgets),
            "enabled": enabled,
            "has_interactor": interactive,
            "overlay_interactive": bool(overlay and overlay.GetInteractive()),
            "overlay_prop_count": len(visible_bounds),
            "visible_prop_matches_per_widget": match_counts,
            "widget_bounds": widget_bounds,
        }
        record(label, ok, detail)
        return ok

    def validate_comparison(spec, parent_id, regions, source, coords, label):
        parent = window.left_workspace.page_specs[parent_id]
        curves = [spec.params.get("base_curve") or {}, *(spec.params.get("overlay_curves") or [])]
        metadata = spec.params.get("curve_metadata") or []
        common_ok = (
            spec.page_kind == "curve_comparison_1d"
            and spec.source_page_id == parent_id
            and spec.params.get("source_page_id") == parent_id
            and spec.params.get("source_page_kind") == parent.page_kind
            and spec.params.get("comparison_kind") == "edc_curve"
            and spec.params.get("x_match_policy") == "per_curve"
            and len(curves) == len(regions) == 2
            and len(metadata) == 2
        )
        record(f"{label}：一个双曲线比较页及冻结来源", common_ok, {
            "page_id": spec.page_id,
            "page_kind": spec.page_kind,
            "source_page_id": spec.source_page_id,
            "source_page_kind": spec.params.get("source_page_kind"),
            "curve_count": len(curves),
        })
        context = window.current_render_context or {}
        record(f"{label}：真实渲染上下文保留两条 EDC", (
            context.get("view") == "1d_comparison"
            and len(context.get("curves") or []) == 2
            and len(window.ax_2d.lines) >= 2
        ), {"view": context.get("view"), "curves": len(context.get("curves") or []),
            "plotted_lines": len(window.ax_2d.lines)})
        legend = window.ax_2d.get_legend()
        legend_labels = [text.get_text() for text in legend.get_texts()] if legend is not None else []
        record(f"{label}：简洁图例仍保留两条曲线", (
            legend_labels == ["基准 - EDC #1", "EDC #2"]
        ), {"legend_labels": legend_labels, "plotted_lines": len(window.ax_2d.lines)})

        energy = np.asarray(coords["E"], dtype=np.float64)
        for index, (curve, meta, region) in enumerate(zip(curves, metadata, regions), start=1):
            expected_energy, expected_intensity = _expected_curve(source, energy, region)
            actual_energy = np.asarray(curve.get("x_data") or [], dtype=np.float64)
            actual_intensity = np.asarray(curve.get("y_data") or [], dtype=np.float64)
            energy_ok = actual_energy.shape == expected_energy.shape and np.allclose(
                actual_energy, expected_energy, rtol=1e-12, atol=1e-12
            )
            intensity_ok = actual_intensity.shape == expected_intensity.shape and np.allclose(
                actual_intensity, expected_intensity, rtol=1e-10, atol=1e-9
            )
            source_ok = (
                curve.get("curve_kind") == "edc_curve"
                and curve.get("source_title") == parent.title
                and meta.get("curve_id") == curve.get("curve_id")
                and meta.get("selection_index") == index
                and tuple(meta.get("index_bounds") or ()) == tuple(region)
            )
            detail = {
                "curve": index,
                "expected_index_bounds": list(region),
                "actual_index_bounds": meta.get("index_bounds"),
                "energy_samples": int(actual_energy.size),
                "energy_endpoints": [float(actual_energy[0]), float(actual_energy[-1])] if actual_energy.size else [],
                "expected_energy_endpoints": [float(expected_energy[0]), float(expected_energy[-1])],
                "intensity_sum": float(np.sum(actual_intensity)) if actual_intensity.size else None,
                "expected_intensity_sum": float(np.sum(expected_intensity)),
                "max_intensity_error": float(np.max(np.abs(actual_intensity - expected_intensity)))
                if actual_intensity.shape == expected_intensity.shape and actual_intensity.size else None,
            }
            record(f"{label}：曲线 {index} 来源、energy 与 X/Y 积分强度", (
                source_ok and energy_ok and intensity_ok
            ), detail)
            numeric_checks.append({"label": label, **detail, "energy_ok": energy_ok,
                                   "intensity_ok": intensity_ok, "source_ok": source_ok})

        expected_view = "2d" if label.startswith("2D") else "3d"
        record(f"{label}：来源视图与选区快照一致", (
            spec.params.get("source_view") == expected_view
            and [item.get("selection_id") for item in metadata]
            == [str(selection_id) for selection_id, _selection in state[f"{expected_view}_selections"]]
        ), {"source_view": spec.params.get("source_view"),
            "selection_ids": [item.get("selection_id") for item in metadata]})

    def step_load():
        window.load_data(str(args.data))
        home_id = window.left_workspace.home_page_id
        wait_for("主页 3D 渲染", lambda: page_ready(home_id, "3d"), step_request_2d)

    def step_request_2d():
        executed.append("加载 NPZ 并完成真实 VTK 3D 主页渲染")
        window.canvas_tool_overlay.preset_buttons["xy"].click()
        wait_for("E(Z) 二维积分页", lambda: (
            window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_kind == "axis_integral"
            and int(window.left_workspace.current_spec().params.get("axis_index", -1)) == 2
            and page_ready(current_page_id(), "2d")
        ), step_master_toggle)

    def step_master_toggle():
        overlay = window.canvas_tool_overlay
        controller = window.crop_controller
        validate_toolbar_placement()
        click(overlay.selection_button, "主框选")
        first = controller.selection
        record("主框选按钮真实点击展开", (
            overlay.selection_button.isChecked() and overlay.selection_panel.isVisible()
            and controller.enabled and first is not None and len(selections()) == 1
        ), {"selections": len(selections()), "selectors": len(window.axis_crop_selectors)})
        click(overlay.selection_button, "主框选")
        record("主框选按钮重复点击收起并保留草稿", (
            not overlay.selection_panel.isVisible() and not controller.enabled
            and controller.selection == first and len(selections()) == 1
        ))
        click(overlay.selection_button, "主框选")
        record("主框选按钮再次展开恢复草稿与二维选框", (
            overlay.selection_panel.isVisible() and controller.enabled
            and controller.selection == first and len(selections()) == 1
            and len(window.axis_crop_selectors) == 1
        ))
        validate_selection_labels([controller.active_selection_id], "2D")
        executed.append("主框选按钮展开/收起/再展开及草稿、Z 标号保留")
        step_2d_ready()

    def step_2d_ready():
        parent_id = current_page_id()
        state["2d_parent"] = parent_id
        overlay = window.canvas_tool_overlay
        if not overlay.selection_button.isChecked():
            click(overlay.selection_button, "主框选")
        click(window.btn_tb_crop, "初级裁剪")
        regions = _regions_2d(window.core.raw_data.shape[:3])
        coords = window.current_render_context["coords"]
        first_bounds = set_popup_bounds(_physical_bounds(coords, regions[0])[:4])
        first_id = window.crop_controller.active_selection_id
        click(overlay.new_region_button, "新增框选范围")
        second_bounds = set_popup_bounds(_physical_bounds(coords, regions[1])[:4])
        second_id = window.crop_controller.active_selection_id
        state["2d_selections"] = selections()
        validate_selection_labels([first_id, second_id], "2D")
        record("2D 次级新增范围得到两个不同选框", (
            len(selections()) == 2 and first_id != second_id
            and first_bounds != second_bounds
            and set(window.axis_crop_selectors) == {first_id, second_id}
        ), {"selection_ids": [first_id, second_id], "regions": regions,
            "selector_count": len(window.axis_crop_selectors)})
        click(window.btn_tb_crop, "初级裁剪")
        capture("02-2d-two-selections.png")

        spec = window.left_workspace.page_specs[parent_id]
        raw_data, display_coords = window._get_display_state_for_spec(spec)
        resolved = window._resolved_axis_integral_params(spec)
        axis_params = copy.deepcopy(spec.params)
        axis_params.update(copy.deepcopy(resolved))
        source_spec = window._build_axis_source_spec_from_params(axis_params)
        source_context = window._get_3d_source_context_for_axis(source_spec, raw_data)
        state["2d_source"] = np.asarray(source_context["data"])
        state["2d_coords"] = display_coords
        state["2d_regions"] = regions
        state["2d_before"] = set(window.left_workspace.page_specs)
        click(overlay.edc_button, "生成 EDC")
        wait_for("2D 多选区 EDC 比较页", lambda: (
            len(set(window.left_workspace.page_specs) - state["2d_before"]) == 1
            and current_page_id() not in state["2d_before"]
            and window.left_workspace.current_spec().page_kind == "curve_comparison_1d"
            and page_ready(current_page_id(), "1d_comparison")
        ), step_2d_created)

    def step_2d_created():
        executed.append("E(Z) 二维积分页通过次级新增范围建立两个选框并点击 EDC")
        new_ids = set(window.left_workspace.page_specs) - state["2d_before"]
        spec = window.left_workspace.current_spec()
        state["2d_comparison"] = spec.page_id in new_ids
        validate_comparison(spec, state["2d_parent"], state["2d_regions"],
                            state["2d_source"], state["2d_coords"], "2D EDC")
        curves = [spec.params["base_curve"], *spec.params["overlay_curves"]]
        energy_size = int(window.core.raw_data.shape[2])
        record("kx-ky EDC 保留完整能量曲线", (
            all(len(curve["x_data"]) == energy_size for curve in curves)
            and all(tuple(item.get("index_bounds") or ())[4:] == (0, energy_size - 1)
                    for item in spec.params["curve_metadata"])
        ), {"full_energy_samples": energy_size,
            "actual_lengths": [len(curve["x_data"]) for curve in curves]})
        capture("03-2d-two-curves.png")
        executed.append("2D 双曲线比较页来源、energy、简洁图例和积分强度数值核对")
        window.left_workspace.activate_page(state["2d_parent"])
        wait_for("返回原 E(Z) 页准备裁空来源", lambda: page_ready(state["2d_parent"], "2d"),
                 step_erase_source_start)

    def step_erase_source_start():
        controller = window.crop_controller
        second_id = state["2d_selections"][-1][0]
        if not controller.activate_selection(second_id):
            raise AssertionError("无法重新激活第二个二维选区")
        state["erase_source_before"] = set(window.left_workspace.page_specs)
        click(window.btn_tb_erase, "初级裁空")
        click(controller.popup.apply_button, "应用裁空")
        wait_for("二维裁空来源页", lambda: (
            len(set(window.left_workspace.page_specs) - state["erase_source_before"]) == 1
            and current_page_id() not in state["erase_source_before"]
            and window.left_workspace.current_spec().source_page_id == state["2d_parent"]
            and window.left_workspace.current_spec().page_kind in {"axis_integral", "axis_integral_crop"}
            and page_ready(current_page_id(), "2d")
        ), step_erase_source_ready)

    def step_erase_source_ready():
        spec = window.left_workspace.current_spec()
        state["erase_source_parent"] = spec.page_id
        regions = list(spec.params.get("crop_regions") or ())
        erased_context_data = (window.current_render_context or {}).get("data")
        erased_data = np.asarray(erased_context_data) if erased_context_data is not None else np.asarray([])
        record("二维裁空来源冻结 erase 区域且视图含 NaN", (
            len(regions) == 1 and regions[0].get("operation") == "erase"
            and bool(np.isnan(erased_data).any())
        ), {"page_id": spec.page_id, "page_kind": spec.page_kind,
            "source_page_id": spec.source_page_id, "crop_regions": regions,
            "nan_count": int(np.isnan(erased_data).sum()) if erased_data.size else 0})

        erased_source = np.array(state["2d_source"], copy=True)
        x0, x1, y0, y1, _e0, _e1 = state["2d_regions"][1]
        erased_source[x0:x1 + 1, y0:y1 + 1, :] = np.nan
        state["erase_expected_source"] = erased_source
        nx, ny, ne = window.core.raw_data.shape[:3]
        state["erase_full_region"] = (0, nx - 1, 0, ny - 1, 0, ne - 1)

        overlay = window.canvas_tool_overlay
        if not overlay.selection_button.isChecked():
            click(overlay.selection_button, "主框选")
        if not window.crop_controller.popup.isVisible():
            if window.btn_tb_erase.isChecked():
                click(window.btn_tb_erase, "初级裁空")
            click(window.btn_tb_erase, "初级裁空")
        coords = window.current_render_context["coords"]
        set_popup_bounds(_physical_bounds(coords, state["erase_full_region"])[:4])
        state["erase_edc_before"] = set(window.left_workspace.page_specs)
        click(overlay.edc_button, "生成 EDC")
        wait_for("二维裁空来源的单条 EDC", lambda: (
            len(set(window.left_workspace.page_specs) - state["erase_edc_before"]) == 1
            and current_page_id() not in state["erase_edc_before"]
            and window.left_workspace.current_spec().page_kind == "edc_curve"
            and page_ready(current_page_id(), "1d")
        ), step_erase_edc_created)

    def step_erase_edc_created():
        spec = window.left_workspace.current_spec()
        curve = spec.params.get("base_curve") or {}
        metadata = (spec.params.get("curve_metadata") or [{}])[0]
        expected_energy, expected_intensity = _expected_curve(
            state["erase_expected_source"], state["2d_coords"]["E"], state["erase_full_region"]
        )
        _original_energy, original_intensity = _expected_curve(
            state["2d_source"], state["2d_coords"]["E"], state["erase_full_region"]
        )
        actual_energy = np.asarray(curve.get("x_data") or [], dtype=np.float64)
        actual_intensity = np.asarray(curve.get("y_data") or [], dtype=np.float64)
        energy_ok = actual_energy.shape == expected_energy.shape and np.allclose(
            actual_energy, expected_energy, rtol=1e-12, atol=1e-12
        )
        intensity_ok = actual_intensity.shape == expected_intensity.shape and np.allclose(
            actual_intensity, expected_intensity, rtol=1e-10, atol=1e-9, equal_nan=True
        )
        excluded = not np.allclose(
            actual_intensity, original_intensity, rtol=1e-10, atol=1e-9, equal_nan=True
        )
        source_ok = (
            spec.source_page_id == state["erase_source_parent"]
            and spec.params.get("source_view") == "2d"
            and tuple(metadata.get("index_bounds") or ()) == state["erase_full_region"]
        )
        state["erase_edc"] = (
            current_page_id() not in state["erase_edc_before"]
            and source_ok and energy_ok and intensity_ok and excluded
        )
        detail = {
            "expected_index_bounds": list(state["erase_full_region"]),
            "actual_index_bounds": metadata.get("index_bounds"),
            "energy_samples": int(actual_energy.size),
            "intensity_sum": float(np.nansum(actual_intensity)) if actual_intensity.size else None,
            "expected_intensity_sum": float(np.nansum(expected_intensity)),
            "uncropped_intensity_sum": float(np.nansum(original_intensity)),
            "excluded_intensity_sum": float(np.nansum(original_intensity - expected_intensity)),
            "max_intensity_error": float(np.nanmax(np.abs(actual_intensity - expected_intensity)))
            if actual_intensity.shape == expected_intensity.shape and actual_intensity.size else None,
        }
        record("二维裁空来源 EDC 不重新包含已裁数据", state["erase_edc"], detail)
        numeric_checks.append({"label": "2D erase-source EDC", **detail,
                               "energy_ok": energy_ok, "intensity_ok": intensity_ok,
                               "source_ok": source_ok, "cropped_data_excluded": excluded})
        capture("03b-2d-erase-source-edc.png")
        executed.append("真实裁空来源页及单条 EDC 独立 NaN 重放数值核对")
        window.left_workspace.activate_page(state["2d_parent"])
        wait_for("返回原 kx-ky 页验收手柄弹窗", lambda: (
            page_ready(state["2d_parent"], "2d") and len(window.axis_crop_selectors) == 2
        ), step_2d_remove)

    def step_2d_remove():
        second_id = state["2d_selections"][-1][0]
        right_click_2d_selection(second_id, remove=True)
        wait_for("2D 选区移除后重绘", lambda: (
            len(selections()) == 1 and len(window.axis_crop_selectors) == 1
        ), step_slice_dos_prepare)

    def step_slice_dos_prepare():
        first_id, _selection = selections()[0]
        if not window.crop_controller.activate_selection(first_id):
            raise AssertionError("无法激活保留的 2D 选区")
        region = state["2d_regions"][0]
        normal_range = (1, max(1, window.core.raw_data.shape[2] // 2))
        state["slice_parent"] = current_page_id()
        state["slice_normal_range"] = normal_range
        commit_normal_interval(2, *normal_range)
        wait_for("切片积分前的法向区间精确刷新", lambda: page_ready(current_page_id(), "2d"),
                 step_slice_dos_create)

    def step_slice_dos_create():
        parent_id = state["slice_parent"]
        parent = window.left_workspace.page_specs[parent_id]
        raw_data, _coords = window._get_display_state_for_spec(parent)
        x0, x1, y0, y1, _e0, _e1 = state["2d_regions"][0]
        e0, e1 = state["slice_normal_range"]
        state["slice_expected"] = np.nansum(
            np.asarray(raw_data)[x0:x1 + 1, y0:y1 + 1, e0:e1 + 1, :],
            axis=(0, 1, 2), dtype=np.float64,
        )
        state["slice_before"] = set(window.left_workspace.page_specs)
        click(window.canvas_tool_overlay.slice_dos_button, "切片内强度积分")
        wait_for("框选次级工具创建切片内强度积分", lambda: (
            len(set(window.left_workspace.page_specs) - state["slice_before"]) == 1
            and current_page_id() not in state["slice_before"]
            and window.left_workspace.current_spec().page_kind == "slice_dos"
            and page_ready(current_page_id(), "1d")
        ), step_slice_dos_created)

    def step_slice_dos_created():
        spec = window.left_workspace.current_spec()
        state["slice_dos"] = validate_slice_dos(
            spec, state["slice_parent"], state["slice_expected"], state["slice_normal_range"]
        )
        capture("03c-2d-selection-slice-dos.png")
        executed.append("右键 2D 手柄核对并移除 Z 选区；次级按钮按活动框选和法向区间创建切片积分")
        window.left_workspace.activate_page(window.left_workspace.home_page_id)
        wait_for("返回 3D home", lambda: (
            current_page_id() == window.left_workspace.home_page_id
            and page_ready(window.left_workspace.home_page_id, "3d")
        ), step_3d_ready)

    def step_3d_ready():
        overlay = window.canvas_tool_overlay
        state["3d_original_opacity"] = (
            _opacity_nodes(window._orig_volume_opacity)
            if window._orig_volume_opacity is not None else volume_opacity()
        )
        if not overlay.selection_button.isChecked():
            click(overlay.selection_button, "主框选")
        if len(selections()) != 1:
            raise AssertionError(f"返回 home 后预期保留首个交互盒，实际 {len(selections())}")
        click(window.btn_tb_crop, "初级裁剪")
        regions = _regions_3d(window.core.raw_data.shape[:3])
        coords = window.current_render_context["coords"]
        first_bounds = set_popup_bounds(_physical_bounds(coords, regions[0]))
        first_id = window.crop_controller.active_selection_id
        validate_selection_labels([first_id], "3D")
        validate_crop_widgets([first_id], "单个 3D box 原生 Enabled 与可见 renderer props")
        first_capture = capture("04-3d-first-box.png")
        click(overlay.new_region_button, "新增框选范围")
        second_bounds = set_popup_bounds(_physical_bounds(coords, regions[1]))
        second_id = window.crop_controller.active_selection_id
        state["3d_selections"] = selections()
        validate_selection_labels([first_id, second_id], "3D")
        record("3D home 新增第二个交互盒并设置不同 X/Y/E", (
            len(selections()) == 2 and first_id != second_id
            and first_bounds[:4] != second_bounds[:4]
            and first_bounds[4:] != second_bounds[4:]
            and set(window._crop_box_widgets) == {first_id, second_id}
        ), {"selection_ids": [first_id, second_id], "regions": regions,
            "first_bounds": first_bounds, "second_bounds": second_bounds,
            "box_count": len(window._crop_box_widgets)})
        validate_crop_widgets([first_id, second_id], "两个 3D box 均 Enabled 且各有可见 renderer props")
        second_capture = capture("05-3d-two-boxes.png")
        first_pixels = first_capture["scene_pixels"]
        second_pixels = second_capture["scene_pixels"]
        same_shape = first_pixels.shape == second_pixels.shape
        changed = np.any(first_pixels != second_pixels, axis=-1) if same_shape else np.asarray([])
        changed_count = int(changed.sum()) if same_shape else 0
        first_sha = hashlib.sha256(first_capture["scene_path"].read_bytes()).hexdigest()
        second_sha = hashlib.sha256(second_capture["scene_path"].read_bytes()).hexdigest()
        if changed_count:
            ys, xs = np.where(changed)
            changed_bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
            changed_area_ratio = float(changed.size and ((xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)) / changed.size)
        else:
            changed_bbox = []
            changed_area_ratio = 0.0
        record("单盒/双盒真实帧缓冲不同且新增盒形成局部像素差", (
            first_capture["ok"] and second_capture["ok"] and same_shape
            and first_sha != second_sha and changed_count >= 20
            and 0.0 < changed_area_ratio < 0.25
        ), {"first_sha256": first_sha, "second_sha256": second_sha,
            "changed_pixels": changed_count, "changed_bbox": changed_bbox,
            "changed_bbox_area_ratio": changed_area_ratio})
        expected_dimmed = _dimmed_nodes(state["3d_original_opacity"])
        record("两个 3D 盒激活时 volume 淡化至 15%", (
            bool(expected_dimmed) and window._orig_volume_opacity is not None
            and np.allclose(volume_opacity(), expected_dimmed, rtol=0.0, atol=1e-12)
        ), {"original": state["3d_original_opacity"], "actual": volume_opacity(),
            "expected": expected_dimmed})
        click(window.btn_tb_crop, "初级裁剪")
        activated = []
        for selection_id in (first_id, second_id):
            active_box = window._crop_box_widgets[selection_id]
            active_box.InvokeEvent("InteractionEvent")
            app.processEvents()
            activated.append(window.crop_controller.active_selection_id)
            active_box.InvokeEvent("EndInteractionEvent")
        record("两个原生 3D widget 均可独立激活", activated == [first_id, second_id], {
            "expected": [first_id, second_id], "actual": activated,
        })
        record("3D 盒交互结束后 volume 仍持续淡化", (
            not window._box_interacting and window._orig_volume_opacity is not None
            and np.allclose(volume_opacity(), expected_dimmed, rtol=0.0, atol=1e-12)
        ), {"box_interacting": bool(window._box_interacting), "actual": volume_opacity()})
        executed.append("逐盒触发真实 VTK 交互事件并保存两个盒的帧缓冲与主窗口合成图")
        click(overlay.selection_button, "主框选")
        record("关闭主框选后 volume 恢复原透明度", (
            window._orig_volume_opacity is None
            and np.allclose(volume_opacity(), state["3d_original_opacity"], rtol=0.0, atol=1e-12)
        ), {"actual": volume_opacity(), "expected": state["3d_original_opacity"]})
        click(overlay.selection_button, "主框选")
        record("重新展开两个 3D 盒后 volume 再次淡化", (
            len(window._crop_box_widgets) == 2 and window._orig_volume_opacity is not None
            and np.allclose(volume_opacity(), expected_dimmed, rtol=0.0, atol=1e-12)
        ), {"box_count": len(window._crop_box_widgets), "actual": volume_opacity()})
        state["3d_parent"] = current_page_id()
        state["3d_regions"] = regions
        state["3d_source"] = np.asarray(window.current_render_context["data"])
        state["3d_coords"] = window.current_render_context["coords"]
        state["3d_before"] = set(window.left_workspace.page_specs)
        click(overlay.edc_button, "生成 EDC")
        wait_for("3D 多盒 EDC 比较页", lambda: (
            len(set(window.left_workspace.page_specs) - state["3d_before"]) == 1
            and current_page_id() not in state["3d_before"]
            and window.left_workspace.current_spec().page_kind == "curve_comparison_1d"
            and page_ready(current_page_id(), "1d_comparison")
        ), step_3d_created)

    def step_3d_created():
        executed.append("返回 3D home 新增第二个交互盒，分别输入 X/Y/E 并点击 EDC")
        new_ids = set(window.left_workspace.page_specs) - state["3d_before"]
        spec = window.left_workspace.current_spec()
        state["3d_comparison"] = spec.page_id in new_ids
        record("切到 EDC 结果页后 volume 恢复原透明度", (
            window._orig_volume_opacity is None
            and np.allclose(volume_opacity(), state["3d_original_opacity"], rtol=0.0, atol=1e-12)
        ), {"actual": volume_opacity(), "expected": state["3d_original_opacity"]})
        validate_comparison(spec, state["3d_parent"], state["3d_regions"],
                            state["3d_source"], state["3d_coords"], "3D EDC")
        curves = [spec.params["base_curve"], *spec.params["overlay_curves"]]
        energy_size = int(window.core.raw_data.shape[2])
        expected_lengths = [region[5] - region[4] + 1 for region in state["3d_regions"]]
        record("3D EDC：两条曲线均按各自盒范围截断 E", (
            [len(curve["x_data"]) for curve in curves] == expected_lengths
            and all(0 < length < energy_size for length in expected_lengths)
            and not np.array_equal(np.asarray(curves[0]["x_data"]), np.asarray(curves[1]["x_data"]))
        ), {"full_energy_samples": energy_size, "expected_lengths": expected_lengths,
            "actual_lengths": [len(curve["x_data"]) for curve in curves],
            "energy_ranges": [item.get("energy_range") for item in spec.params["curve_metadata"]]})
        capture("06-3d-two-curves.png")
        executed.append("3D 直接双曲线页来源、各自 energy、E 截断与积分强度数值核对")
        window.left_workspace.activate_page(state["3d_parent"])
        wait_for("返回 3D 主页验收手柄弹窗", lambda: (
            page_ready(state["3d_parent"], "3d") and len(window._crop_box_widgets) == 2
        ), step_3d_remove)

    def step_3d_remove():
        ids = [selection_id for selection_id, _selection in state["3d_selections"]]
        validate_selection_labels(ids, "3D")
        right_click_3d_selection(ids[-1], remove=True)
        wait_for("3D 选区移除后重绘", lambda: (
            len(selections()) == 1 and len(window._crop_box_widgets) == 1
        ), step_request_xz)

    def step_request_xz():
        window.canvas_tool_overlay.preset_buttons["xz"].click()
        wait_for("XZ（沿 Y 积分）来源页", lambda: (
            window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_kind == "axis_integral"
            and int(window.left_workspace.current_spec().params.get("axis_index", -1)) == 1
            and page_ready(current_page_id(), "2d")
        ), step_xz_ready)

    def step_xz_ready():
        overlay = window.canvas_tool_overlay
        if not overlay.selection_button.isChecked():
            click(overlay.selection_button, "主框选")
        if not window.btn_tb_crop.isChecked():
            click(window.btn_tb_crop, "初级裁剪")
        nx, ny, ne = window.core.raw_data.shape[:3]
        region = (1, max(2, nx // 3), 1, max(2, ny // 2), 1, max(2, ne // 2))
        coords = window.current_render_context["coords"]
        set_popup_bounds(_physical_bounds_for_axes(coords, region, ("X", "E")))
        click(window.btn_tb_crop, "关闭初级裁剪浮窗")
        state["xz_parent"] = current_page_id()
        state["xz_region"] = region
        commit_normal_interval(1, region[2], region[3])
        wait_for("XZ 法向 Y 区间精确刷新", lambda: page_ready(current_page_id(), "2d"),
                 step_xz_create)

    def step_xz_create():
        parent_id = state["xz_parent"]
        state["xz_source"], state["xz_coords"] = prepare_axis_source(parent_id)
        state["xz_before"] = set(window.left_workspace.page_specs)
        click(window.canvas_tool_overlay.edc_button, "XZ 生成 EDC")
        wait_for("XZ 框选 EDC 结果页", lambda: (
            len(set(window.left_workspace.page_specs) - state["xz_before"]) == 1
            and current_page_id() not in state["xz_before"]
            and window.left_workspace.current_spec().page_kind == "edc_curve"
            and page_ready(current_page_id(), "1d")
        ), step_xz_created)

    def step_xz_created():
        spec = window.left_workspace.current_spec()
        state["xz_edc"] = validate_axis_edc(
            spec, state["xz_parent"], state["xz_region"],
            state["xz_source"], state["xz_coords"], "XZ EDC",
        )
        capture("07-xz-selection-edc.png")
        executed.append("XZ 通过框选次级 EDC 建页，核对 X/E 框选及 Y 法向双端区间")
        window.left_workspace.activate_page(window.left_workspace.home_page_id)
        wait_for("XZ 后返回 3D home", lambda: page_ready(window.left_workspace.home_page_id, "3d"),
                 step_request_yz)

    def step_request_yz():
        window.canvas_tool_overlay.preset_buttons["yz"].click()
        wait_for("YZ（沿 X 积分）来源页", lambda: (
            window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_kind == "axis_integral"
            and int(window.left_workspace.current_spec().params.get("axis_index", -1)) == 0
            and page_ready(current_page_id(), "2d")
        ), step_yz_ready)

    def step_yz_ready():
        overlay = window.canvas_tool_overlay
        if not overlay.selection_button.isChecked():
            click(overlay.selection_button, "主框选")
        if not window.btn_tb_crop.isChecked():
            click(window.btn_tb_crop, "初级裁剪")
        nx, ny, ne = window.core.raw_data.shape[:3]
        region = (1, max(2, nx // 2), 1, max(2, ny // 3),
                  max(1, ne // 2), ne - 2)
        coords = window.current_render_context["coords"]
        set_popup_bounds(_physical_bounds_for_axes(coords, region, ("Y", "E")))
        click(window.btn_tb_crop, "关闭初级裁剪浮窗")
        state["yz_parent"] = current_page_id()
        state["yz_region"] = region
        commit_normal_interval(0, region[0], region[1])
        wait_for("YZ 法向 X 区间精确刷新", lambda: page_ready(current_page_id(), "2d"),
                 step_yz_create)

    def step_yz_create():
        parent_id = state["yz_parent"]
        state["yz_source"], state["yz_coords"] = prepare_axis_source(parent_id)
        state["yz_before"] = set(window.left_workspace.page_specs)
        click(window.canvas_tool_overlay.edc_button, "YZ 生成 EDC")
        wait_for("YZ 框选 EDC 结果页", lambda: (
            len(set(window.left_workspace.page_specs) - state["yz_before"]) == 1
            and current_page_id() not in state["yz_before"]
            and window.left_workspace.current_spec().page_kind == "edc_curve"
            and page_ready(current_page_id(), "1d")
        ), step_yz_created)

    def step_yz_created():
        spec = window.left_workspace.current_spec()
        state["yz_edc"] = validate_axis_edc(
            spec, state["yz_parent"], state["yz_region"],
            state["yz_source"], state["yz_coords"], "YZ EDC",
        )
        capture("08-yz-selection-edc.png")
        executed.append("YZ 通过框选次级 EDC 建页，核对 Y/E 框选及 X 法向双端区间")
        finish()

    def finish():
        if state["finished"]:
            return
        state["finished"] = True
        passed = sum(1 for item in results if item["ok"])
        required = (
            "2d_comparison", "erase_edc", "slice_dos", "3d_comparison",
            "xz_edc", "yz_edc", "2d_remove", "3d_remove",
        )
        success = (
            bool(results) and passed == len(results)
            and all(state[key] for key in required)
        )
        payload = {
            "requested_data": str(args.requested_data),
            "data": str(args.data),
            "data_kind": ("synthetic_fallback" if args.synthetic_fallback
                          else "explicit_npz" if args.explicit_data else "default_wse2"),
            "synthetic_fallback": bool(args.synthetic_fallback),
            "data_shape": list(window.core.raw_data.shape) if window.core.raw_data is not None else None,
            "platform": {
                "system": platform.platform(),
                "qt_qpa_platform": os.environ.get("QT_QPA_PLATFORM"),
                "offscreen": False,
                "python": sys.version,
            },
            "isolated_runtime": {
                "extension_root": os.environ.get("BANDSCOPE_EXTENSION_ROOT"),
                "settings": "tests.support.environment.isolate_runtime",
            },
            "executed": executed,
            "required_checks": {key: bool(state[key]) for key in required},
            "not_executed": not_executed,
            "manual_visual_review": False,
            "automatic_screenshots_only": True,
            "artifacts": artifacts,
            "numeric_checks": numeric_checks,
            "results": results,
            "messages": messages,
            "passed": passed,
            "total": len(results),
            "success": bool(success),
        }
        (args.output_dir / "report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        print(f"[accept] {passed}/{len(results)} 项通过；"
              f"required={{{', '.join(f'{key}={state[key]}' for key in required)}}}；"
              f"输出 {args.output_dir}", flush=True)
        for widget in QApplication.topLevelWidgets():
            if widget.isVisible():
                widget.close()
        app.quit()

    QTimer.singleShot(0, guarded(step_load))
    QTimer.singleShot(600000, lambda: (record("真实窗口验收总超时", False), finish()))
    app.exec_()
    passed = sum(1 for item in results if item["ok"])
    return 0 if (
        results and passed == len(results)
        and all(state[key] for key in (
            "2d_comparison", "erase_edc", "slice_dos", "3d_comparison",
            "xz_edc", "yz_edc", "2d_remove", "3d_remove",
        ))
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
