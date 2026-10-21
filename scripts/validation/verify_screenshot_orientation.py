# -*- coding: utf-8 -*-
"""真实窗口验收三轴方向标、色条拖动；默认使用 WSe2_step.npz。

运行：.venv/Scripts/python.exe scripts/validation/verify_screenshot_orientation.py
输出：.local/outputs/screenshot_orientation/，不写用户设置或已安装插件。
全程通过 QTimer 等待窗口加载，不手动调用 processEvents。
"""
import copy
import faulthandler
import hashlib
import os
from pathlib import Path
import platform
import sys
import time
import traceback

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.validation._common import REPO_ROOT, arguments


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def _digest(values):
    return hashlib.sha256(memoryview(np.ascontiguousarray(values)).cast("B")).hexdigest()


def main():
    if sys.platform == "win32":
        os.environ["QT_QPA_PLATFORM"] = "windows"
    args = arguments(
        "screenshot_orientation",
        default_data=REPO_ROOT / ".local" / "data" / "WSe2_step.npz",
    )
    fault_log = (args.output_dir / "faulthandler.log").open("w", encoding="utf-8")
    faulthandler.enable(fault_log)

    from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

    configure_qt_high_dpi()
    configure_qt_plugin_path()
    from PyQt5.QtCore import QEvent, QPointF, QT_VERSION_STR, Qt, QTimer
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    from bandscope.exporting.publication_dialog import PublicationExportDialog
    from bandscope.exporting.publication_export import capture_snapshot
    from bandscope.exporting.publication_models import OutputOptions, resolve_style
    from bandscope.exporting.publication_renderers import (
        _orientation_axis_order, colorbar_preview_geometry, render_snapshot, save_figure,
    )
    from matplotlib.patches import Circle
    from bandscope.rendering.render_core import VisualEngine
    import bandscope.app.refactored_app as refactored_app

    refactored_app.UpdateController = _NullUpdateController
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.resize(1440, 1000)
    window.show()
    results = []
    dialog_holder = []
    finished = False

    def record(name, passed, detail=""):
        status = "PASS" if passed else "FAIL"
        results.append((name, status, detail))
        print(f"[{status}] {name} {detail}", flush=True)

    def camera_state():
        camera = window.plotter.camera
        return (
            tuple(camera.position), tuple(camera.focal_point), tuple(camera.up),
            bool(camera.GetParallelProjection()), float(camera.GetParallelScale()),
            float(camera.GetViewAngle()), tuple(camera.clipping_range),
        )

    def science_state():
        current = window.left_workspace.current_spec()
        return {
            "camera": camera_state(),
            "raw_id": id(window.core.raw_data),
            "raw_digest": _digest(window.core.raw_data),
            "levels": tuple(window._get_display_levels()),
            "last_range": VisualEngine._last_data_range,
            "locked_range": VisualEngine._locked_data_range,
            "page_id": current.page_id if current is not None else None,
            "clip_ranges": copy.deepcopy(window.clip_ranges),
            "show_axes": window.timeline_bar.switch_axes.isChecked(),
            "e_flip": window.timeline_bar.switch_flip.isChecked(),
        }

    def finish():
        nonlocal finished
        if finished:
            return
        finished = True
        import matplotlib
        import pyvista
        import vtk

        lines = [
            "# 截图三轴方向标与色条拖动真实窗口验证", "",
            f"- 数据：`{args.data.resolve()}`",
            f"- 数据形状：`{getattr(window.core.raw_data, 'shape', None)}`",
            f"- 环境：Python {platform.python_version()} / Qt {QT_VERSION_STR} / "
            f"Matplotlib {matplotlib.__version__} / PyVista {pyvista.__version__} / VTK {vtk.VTK_VERSION}",
            "- Qt 平台：" + os.environ.get("QT_QPA_PLATFORM", "默认"),
            "- 方向标保持相机投影倾角；只选择横向朝右半边、竖向朝上半边、纵深朝观察者的一端。",
            "- 通过控件 API 和 Qt 鼠标事件验证位置输入与拖动；未执行人工鼠标手测。",
            "- 2D 色条使用真实数据的中间能量切片快照，在真实面板中走 worker 预览路径。",
            "- PNG 已生成，科研排版观感与大数据体遮挡仍需人工确认。", "",
            "| 检查 | 结果 | 说明 |", "| --- | --- | --- |",
        ]
        lines.extend(f"| {name} | {status} | {detail.replace('|', '/')} |"
                     for name, status, detail in results)
        body_results = {name: (status, detail) for name, status, detail in results
                        if name in ("body_250_marker_off", "body_250")}
        if (len(body_results) == 2
                and all(status == "FAIL" for status, _ in body_results.values())
                and body_results["body_250_marker_off"][1] == body_results["body_250"][1]):
            lines.extend([
                "", "已知布局边界：`body_size=250` 且绘制带刻度坐标轴时，方向标关闭/开启"
                "均报告相同画布尺寸错误；此对照表明失败与方向标开关无关。"
                "`body_250_axes_off` 单独验证关闭带刻度坐标轴后的大数据体叠加。",
            ])
        failed = sum(status == "FAIL" for _, status, _ in results)
        lines.extend(["", f"汇总：{len(results) - failed} 通过，{failed} 失败。", ""])
        (args.output_dir / "validation_report.md").write_text("\n".join(lines), encoding="utf-8")
        for dialog in dialog_holder:
            dialog._debounce.stop()
            dialog.close()
        window.close()
        print(f"[RESULT] {len(results) - failed} passed / {failed} failed; {args.output_dir}", flush=True)
        app.exit(1 if failed else 0)

    def guarded(step):
        def call():
            try:
                step()
            except Exception as exc:
                traceback.print_exc()
                record(step.__name__, False, f"{type(exc).__name__}: {exc}")
                finish()
        return call

    def check_marker(fig, expected, camera):
        axes = [axis for axis in fig.axes if axis.get_label() == "orientation"]
        assert len(axes) == int(expected), "方向标开关与绘制结果不符"
        if not expected:
            return
        axis = axes[0]
        names = ("orientation-horizontal", "orientation-vertical", "orientation-depth")
        arrows = {patch.get_gid(): patch for patch in axis.patches if patch.get_gid() in names}
        assert len(arrows) == 3, "方向标必须包含三个轴方向"
        # 独立从冻结相机求投影，不用渲染器提供的方向向量作为预期值。
        outward = np.asarray(camera["position"]) - np.asarray(camera["focal_point"])
        outward = outward / np.linalg.norm(outward)
        right = np.cross(np.asarray(camera["view_up"]), outward)
        right = right / np.linalg.norm(right)
        screen_up = np.cross(outward, right)
        basis = np.asarray((right, screen_up, outward))
        directions = []
        scale_ratios = []
        for slot, (name, world_axis) in enumerate(zip(names, _orientation_axis_order(camera))):
            expected_vector = basis[:, world_axis].copy()
            if expected_vector[slot] < 0.0:
                expected_vector = -expected_vector
            arrow = arrows[name]
            projected_length = np.linalg.norm(expected_vector[:2])
            if projected_length < 1e-9:
                assert slot == 2 and isinstance(arrow, Circle), "正视纵深轴须画圈点，不能伪造斜角"
                assert any(line.get_gid() == "orientation-depth-dot" for line in axis.lines)
                directions.append(np.zeros(2))
                continue
            assert not isinstance(arrow, Circle), "非正视方向应保留投影箭头"
            start, end = axis.transData.transform(arrow._posA_posB)
            delta = end - start
            length = np.linalg.norm(delta)
            assert length > 0.0, name
            direction = delta / length
            np.testing.assert_allclose(direction, expected_vector[:2] / projected_length,
                                       atol=1e-7, err_msg=f"{name} 未保留相机投影倾角")
            assert expected_vector[slot] >= -1e-12, f"{name} 箭头未朝指定半平面"
            directions.append(direction)
            scale_ratios.append(length / projected_length)
        np.testing.assert_allclose(scale_ratios, scale_ratios[0], rtol=1e-7,
                                   err_msg="三轴投影缩短比例被单独拉伸")
        assert len(axis.texts) == 3, "当前数据的三条轴均应有标签"
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        bounds = fig.bbox
        for text in axis.texts:
            box = text.get_window_extent(renderer)
            assert (box.x0 >= bounds.x0 - 1 and box.y0 >= bounds.y0 - 1
                    and box.x1 <= bounds.x1 + 1 and box.y1 <= bounds.y1 + 1), "标签超出画布"
        return np.asarray(directions)

    def run_exports():
        snapshot = capture_snapshot(window)
        assert snapshot.view_family == "3d"
        baseline_state = science_state()
        base_camera = copy.deepcopy(snapshot.camera)
        base_digest = _digest(snapshot.payload["volume"])
        scenarios = [
            ("default_off", "3d_minimal", {}, False, False, False),
            ("minimal_on", "3d_minimal", {"show_orientation": True}, False, False, False),
            ("boxed_on", "3d_boxed", {"show_orientation": True}, False, False, False),
            ("horizontal_on", "3d_horizontal", {"show_orientation": True}, False, False, False),
            ("axes_off_marker_on", "3d_minimal", {"show_orientation": True}, True, False, False),
            ("reverse_camera", "3d_minimal", {"show_orientation": True}, False, True, False),
            ("oblique_camera", "3d_minimal", {"show_orientation": True}, False, False, False),
            ("roll_camera", "3d_minimal", {"show_orientation": True}, False, False, False),
            ("depth_facing", "3d_minimal", {"show_orientation": True}, False, False, False),
            ("transparent", "3d_minimal", {"show_orientation": True}, False, False, True),
            ("body_250_axes_off", "3d_minimal", {"show_orientation": True, "body_size": 250}, True, False, False),
            ("body_250_marker_off", "3d_minimal", {"body_size": 250}, False, False, False),
            ("body_250", "3d_minimal", {"show_orientation": True, "body_size": 250}, False, False, False),
        ]
        pending = iter(scenarios)
        marker_directions = {}

        def next_export():
            try:
                name, style_id, overrides, axes_off, reverse, transparent = next(pending)
            except StopIteration:
                for name in ("oblique_camera", "roll_camera"):
                    base = marker_directions.get("minimal_on")
                    changed = marker_directions.get(name)
                    passed = (base is not None and changed is not None
                              and np.max(np.abs(base[:, 0] * changed[:, 1]
                                                - base[:, 1] * changed[:, 0])) > 0.05)
                    record(f"projection_changes_{name}", passed, "不同视角的实际箭头倾角随相机改变")
                open_dialog(snapshot, baseline_state)
                return
            snap = copy.copy(snapshot)
            snap.camera = copy.deepcopy(snapshot.camera)
            snap.payload = dict(snapshot.payload)
            if name != "default_off":
                # 只修改导出快照，覆盖原坐标轴与方向标共存及独立开启两种情况。
                snap.payload["show_axes"] = not axes_off
            if reverse:
                focal = np.asarray(snap.camera["focal_point"])
                position = np.asarray(snap.camera["position"])
                snap.camera["position"] = tuple(2.0 * focal - position)
            if name in ("oblique_camera", "roll_camera", "depth_facing"):
                focal = np.asarray(snap.camera["focal_point"])
                position = np.asarray(snap.camera["position"])
                distance = np.linalg.norm(position - focal)
                if name == "oblique_camera":
                    direction = np.asarray((1.7, -2.4, 1.2))
                    snap.camera["position"] = tuple(focal + distance * direction / np.linalg.norm(direction))
                    snap.camera["view_up"] = (0.0, 0.0, 1.0)
                elif name == "depth_facing":
                    snap.camera["position"] = tuple(focal + np.asarray((0.0, -distance, 0.0)))
                    snap.camera["view_up"] = (0.0, 0.0, 1.0)
                else:
                    outward = (position - focal) / distance
                    up = np.asarray(snap.camera["view_up"])
                    angle = np.deg2rad(27.0)
                    snap.camera["view_up"] = tuple(
                        up * np.cos(angle) + np.cross(outward, up) * np.sin(angle)
                        + outward * np.dot(outward, up) * (1.0 - np.cos(angle))
                    )
            saved_camera = copy.deepcopy(snap.camera)
            options = OutputOptions(width_mm=89.0, dpi=180, fmt="png", transparent=transparent)
            started = time.perf_counter()
            output_path = args.output_dir / f"{name}.png"
            output_path.unlink(missing_ok=True)
            fig = None
            try:
                fig = render_snapshot(snap, resolve_style("3d", style_id), overrides, options)
                directions = check_marker(fig, bool(overrides.get("show_orientation", False)), snap.camera)
                if directions is not None:
                    marker_directions[name] = directions
                save_figure(fig, str(output_path), options)
                assert snap.camera == saved_camera, "导出改变了快照相机"
                assert snapshot.camera == base_camera, "导出改变了原始快照相机"
                assert _digest(snapshot.payload["volume"]) == base_digest, "导出改变了快照数据"
                assert science_state() == baseline_state, "导出改变了主窗口科学状态"
                if transparent:
                    from PIL import Image
                    with Image.open(args.output_dir / f"{name}.png") as image:
                        alpha = np.asarray(image)[..., 3]
                        assert np.any(alpha == 0), "透明输出没有透明像素"
                detail = f"{time.perf_counter() - started:.2f}s；相机投影、半平面、快照和主窗口状态断言通过"
                if name in ("minimal_on", "oblique_camera", "roll_camera"):
                    angles = np.degrees(np.arctan2(directions[:, 1], directions[:, 0])).round(1).tolist()
                    detail += f"；水平/竖直/纵深投影角度={angles}°"
                record(name, True, detail)
            except Exception as exc:
                traceback.print_exc()
                record(name, False, f"{type(exc).__name__}: {exc}")
            finally:
                if fig is not None:
                    fig.clear()
            QTimer.singleShot(150, guarded(next_export))

        QTimer.singleShot(150, guarded(next_export))

    def open_dialog(snapshot, baseline_state):
        dialog = PublicationExportDialog(window)
        dialog_holder.append(dialog)
        dialog._adopt_snapshot(snapshot)
        dialog.resize(1120, 1100)
        dialog.show()
        dialog.tune_toggle.setChecked(True)
        assert dialog.chk_orientation.isVisible(), "三轴方向标入口不可见"
        dialog.chk_orientation.setChecked(True)
        assert dialog._collect_overrides_from_ui().get("show_orientation") is True

        def assert_science_unchanged():
            assert science_state() == baseline_state, "位置交互改变了主窗口科学状态"

        def marker_center():
            rect = dialog.preview_label.orientation_rect()
            assert rect is not None, "预览缺少可拖动方向标"
            x, y, width, height = rect
            return np.asarray((100.0 * (x + width / 2.0),
                               100.0 * (y + height / 2.0)))

        def check_center_inputs():
            center = marker_center()
            values = np.asarray((dialog.spin_orientation_cx.value(),
                                 dialog.spin_orientation_cy.value()))
            np.testing.assert_allclose(values, center, atol=0.02,
                                       err_msg="方向标位置输入与实际预览不一致")
            return center

        def wait_preview(next_step, *, marker=True):
            deadline = time.perf_counter() + 30.0

            def poll():
                rect = dialog.preview_label.orientation_rect()
                ready = (dialog._big_ready and not dialog._busy_3d
                         and (rect is not None) == marker)
                if ready:
                    QTimer.singleShot(150, guarded(next_step))
                elif time.perf_counter() < deadline:
                    QTimer.singleShot(100, guarded(poll))
                else:
                    raise AssertionError("等待位置预览刷新超时")

            QTimer.singleShot(150, guarded(poll))

        def export_position(name):
            center = check_center_inputs()
            options = dialog._collect_output_options().with_updates(dpi=180)
            overrides = dialog._collect_overrides_from_ui()
            output_path = args.output_dir / f"{name}.png"
            output_path.unlink(missing_ok=True)
            fig = render_snapshot(snapshot, dialog._current_style(), overrides, options)
            try:
                check_marker(fig, True, snapshot.camera)
                axes = next(axis for axis in fig.axes if axis.get_label() == "orientation")
                rect = axes.get_position()
                actual = np.asarray((100.0 * (rect.x0 + rect.width / 2.0),
                                     100.0 * (rect.y0 + rect.height / 2.0)))
                np.testing.assert_allclose(actual, center, atol=0.02,
                                           err_msg="正式导出位置与预览不一致")
                save_figure(fig, str(output_path), options)
            finally:
                fig.clear()
            assert_science_unchanged()
            record(name, True, f"预览/坐标框/导出中心一致：{center.round(2).tolist()}%")
            return center

        def drag_marker(delta_percent):
            label = dialog.preview_label
            rect = label.orientation_rect()
            assert rect is not None
            pixmap = label.pixmap()
            ratio = pixmap.devicePixelRatio()
            width, height = pixmap.width() / ratio, pixmap.height() / ratio
            contents = label.contentsRect()
            left = contents.x() + (contents.width() - width) / 2.0
            top = contents.y() + (contents.height() - height) / 2.0
            # 从图标内部偏离中心的位置按下，检查拖动不会把按下点吸附到中心。
            start = QPointF(left + (rect[0] + rect[2] * 0.43) * width,
                            top + (1.0 - rect[1] - rect[3] * 0.61) * height)
            start = QPointF(start.toPoint())
            delta = QPointF(float(delta_percent[0]) / 100.0 * width,
                            -float(delta_percent[1]) / 100.0 * height)
            delta = QPointF((start + delta).toPoint()) - start
            before = marker_center()

            def send(kind, point, button, buttons):
                event = QMouseEvent(kind, point, button, buttons, Qt.NoModifier)
                QApplication.sendEvent(label, event)

            send(QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
            send(QEvent.MouseMove, start + delta / 2.0, Qt.NoButton, Qt.LeftButton)
            halfway = check_center_inputs()
            assert np.linalg.norm(halfway - before) > 0.5, "拖动过程中图标未实时移动"
            send(QEvent.MouseMove, start + delta, Qt.NoButton, Qt.LeftButton)
            moved = check_center_inputs()
            rect = label.orientation_rect()
            half_size = np.asarray((rect[2], rect[3])) * 50.0
            effective_delta = np.asarray((delta.x() / width, -delta.y() / height)) * 100.0
            expected = np.clip(before + effective_delta, half_size, 100.0 - half_size)
            np.testing.assert_allclose(moved, expected, atol=0.02,
                                       err_msg="拖动位移/按下偏移与百分比不一致")
            send(QEvent.MouseButtonRelease, start + delta, Qt.LeftButton, Qt.NoButton)
            assert_science_unchanged()

        def check_manual_position():
            center = export_position("position_inputs")
            np.testing.assert_allclose(center, (70.0, 30.0), atol=0.02)
            drag_marker((12.0, 8.0))
            wait_preview(check_fit_drag)

        def check_fit_drag():
            export_position("position_drag_fit")
            dialog._toggle_fit()
            assert dialog._fit_to_window is False
            QTimer.singleShot(200, guarded(drag_one_to_one))

        def drag_one_to_one():
            drag_marker((-16.0, 12.0))
            wait_preview(check_one_to_one_drag)

        preserved_position = []

        def check_one_to_one_drag():
            preserved_position.append(export_position("position_drag_one_to_one"))
            dialog.chk_orientation.setChecked(False)
            wait_preview(check_hidden_position, marker=False)

        def check_hidden_position():
            values = np.asarray((dialog.spin_orientation_cx.value(),
                                 dialog.spin_orientation_cy.value()))
            np.testing.assert_allclose(values, preserved_position[0], atol=0.02,
                                       err_msg="关闭方向标丢失已设置位置")
            dialog.chk_orientation.setChecked(True)
            wait_preview(check_reopened_position)

        def check_reopened_position():
            np.testing.assert_allclose(check_center_inputs(), preserved_position[0], atol=0.02)
            record("position_toggle_preserves", True, "关闭/重新开启方向标保留位置")
            dialog.spin_orientation_cx.setValue(0.0)
            dialog.spin_orientation_cy.setValue(100.0)
            wait_preview(check_boundary_position)

        def check_boundary_position():
            export_position("position_boundary_inputs")
            rect = dialog.preview_label.orientation_rect()
            assert rect[0] >= -1e-8 and rect[1] >= -1e-8
            assert rect[0] + rect[2] <= 1.0 + 1e-8 and rect[1] + rect[3] <= 1.0 + 1e-8
            drag_marker((300.0, -300.0))
            wait_preview(check_boundary_drag)

        def check_boundary_drag():
            export_position("position_boundary_drag")
            dialog._toggle_fit()
            QTimer.singleShot(200, guarded(grab_dialog))

        def grab_dialog():
            assert dialog.chk_orientation.isChecked()
            assert_science_unchanged()
            assert dialog.grab().save(str(args.output_dir / "dialog_orientation.png"))
            record("dialog_entry", True, "实际面板位置输入、fit/100%鼠标事件拖动及图幅边界通过；保存控件截图")
            run_colorbar_checks(dialog, snapshot, baseline_state)

        def set_manual_position():
            dialog.spin_orientation_cx.setValue(70.0)
            dialog.spin_orientation_cy.setValue(30.0)
            wait_preview(check_manual_position)

        wait_preview(set_manual_position)

    def run_colorbar_checks(dialog, snapshot, baseline_state):
        """在同一真实面板验证色条与方向标独立拖动及 2D worker 路径。"""
        baseline_main = {}
        orientation_before = []

        def assert_science_unchanged():
            assert science_state() == baseline_state, "色条交互改变了主窗口科学状态"

        def wait_colorbar(next_step, *, visible=True):
            deadline = time.perf_counter() + 30.0

            def poll():
                rect = dialog.preview_label.colorbar_rect()
                ready = (dialog._big_ready and not dialog._busy_3d
                         and (rect is not None) == visible)
                if ready:
                    QTimer.singleShot(150, guarded(next_step))
                elif time.perf_counter() < deadline:
                    QTimer.singleShot(100, guarded(poll))
                else:
                    raise AssertionError("等待色条预览刷新超时")

            QTimer.singleShot(150, guarded(poll))

        def center_of(rect):
            return np.asarray((100.0 * (rect[0] + rect[2] / 2.0),
                               100.0 * (rect[1] + rect[3] / 2.0)))

        def check_inputs():
            rect = dialog.preview_label.colorbar_rect()
            assert rect is not None, "预览缺少可拖动色条"
            center = center_of(rect)
            values = (dialog.spin_cbar_cx.value(), dialog.spin_cbar_cy.value())
            np.testing.assert_allclose(values, center, atol=0.02,
                                       err_msg="色条中心输入与预览不一致")
            return center

        def export_bar(name, *, compare_main=False):
            center = check_inputs()
            options = dialog._collect_output_options().with_updates(dpi=180)
            overrides = dialog._collect_overrides_from_ui()
            path = args.output_dir / f"{name}.png"
            path.unlink(missing_ok=True)
            fig = render_snapshot(dialog.snapshot, dialog._current_style(), overrides, options)
            try:
                geometry = colorbar_preview_geometry(fig)
                assert geometry is not None, "导出没有色条几何信息"
                rect, bounds = geometry
                np.testing.assert_allclose(center_of(rect), center, atol=0.03,
                                           err_msg="正式导出色条位置与预览不一致")
                assert (bounds[0] >= -0.003 and bounds[1] >= -0.003
                        and bounds[0] + bounds[2] <= 1.003
                        and bounds[1] + bounds[3] <= 1.003), "色条或刻度/标题超出图幅"
                main = tuple(float(v) for v in fig.axes[0].get_position().bounds)
                family = dialog.snapshot.view_family
                if compare_main:
                    np.testing.assert_allclose(main, baseline_main[family], atol=1e-6,
                                               err_msg="自由移动色条重新排布了主图")
                else:
                    baseline_main[family] = main
                if family == "3d" and orientation_before:
                    np.testing.assert_allclose(dialog.preview_label.orientation_rect(),
                                               orientation_before[0], atol=1e-7,
                                               err_msg="拖动色条串扰方向标位置")
                save_figure(fig, str(path), options)
            finally:
                fig.clear()
            assert_science_unchanged()
            record(name, True, f"色条/刻度/标题同层移动，预览/控件/导出中心={center.round(2).tolist()}%")
            return center

        def drag_bar(delta_percent, *, boundary=False):
            label = dialog.preview_label
            rect = label.colorbar_rect()
            assert rect is not None
            pixmap = label.pixmap()
            width = pixmap.width() / pixmap.devicePixelRatio()
            height = pixmap.height() / pixmap.devicePixelRatio()
            content = label.contentsRect()
            left = content.x() + (content.width() - width) / 2.0
            top = content.y() + (content.height() - height) / 2.0
            start = QPointF(left + (rect[0] + rect[2] * 0.43) * width,
                            top + (1.0 - rect[1] - rect[3] * 0.61) * height)
            start = QPointF(start.toPoint())
            delta = QPointF(float(delta_percent[0]) / 100.0 * width,
                            -float(delta_percent[1]) / 100.0 * height)
            delta = QPointF((start + delta).toPoint()) - start
            before = check_inputs()
            marker_before = label.orientation_rect()

            def send(kind, point, button, buttons):
                QApplication.sendEvent(label, QMouseEvent(kind, point, button, buttons, Qt.NoModifier))

            send(QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
            send(QEvent.MouseMove, start + delta / 2.0, Qt.NoButton, Qt.LeftButton)
            halfway = check_inputs()
            assert np.linalg.norm(halfway - before) > 0.5, "拖动过程中色条没有实时移动"
            send(QEvent.MouseMove, start + delta, Qt.NoButton, Qt.LeftButton)
            moved = check_inputs()
            if not boundary:
                effective = np.asarray((delta.x() / width, -delta.y() / height)) * 100.0
                np.testing.assert_allclose(moved, before + effective, atol=0.02,
                                           err_msg="色条拖动位移/抓取偏移不正确")
            if marker_before is not None:
                np.testing.assert_allclose(label.orientation_rect(), marker_before, atol=1e-8,
                                           err_msg="色条拖动同时移动了方向标")
            send(QEvent.MouseButtonRelease, start + delta, Qt.LeftButton, Qt.NoButton)
            assert_science_unchanged()

        def setup_vertical():
            assert not dialog.chk_orientation.toolTip(), "方向标勾选框仍有气泡提示"
            for widget in (dialog.spin_orientation_cx, dialog.spin_orientation_cy,
                           dialog.btn_orientation_auto):
                assert not widget.toolTip(), "方向标附近控件仍有气泡提示"
            record("orientation_tooltips_removed", True, "方向标勾选框及同行控件不再设置气泡提示")
            if not dialog._fit_to_window:
                dialog._toggle_fit()
            dialog.spin_cbar_len.setValue(60)
            dialog.spin_orientation_cx.setValue(20.0)
            dialog.spin_orientation_cy.setValue(20.0)
            wait_colorbar(vertical_ready)

        def vertical_ready():
            orientation_before.append(dialog.preview_label.orientation_rect())
            export_bar("colorbar_vertical_baseline")
            dialog.spin_cbar_cx.setValue(55.0)
            dialog.spin_cbar_cy.setValue(50.0)
            wait_colorbar(vertical_inputs)

        def vertical_inputs():
            np.testing.assert_allclose(export_bar("colorbar_vertical_inputs", compare_main=True),
                                       (55.0, 50.0), atol=0.03)
            drag_bar((-9.0, 5.0))
            wait_colorbar(vertical_fit)

        def vertical_fit():
            export_bar("colorbar_vertical_fit", compare_main=True)
            dialog._toggle_fit()
            assert not dialog._fit_to_window
            QTimer.singleShot(150, guarded(vertical_one_to_one))

        def vertical_one_to_one():
            drag_bar((8.0, -7.0))
            wait_colorbar(vertical_one_to_one_result)

        def vertical_one_to_one_result():
            export_bar("colorbar_vertical_one_to_one", compare_main=True)
            drag_bar((300.0, -300.0), boundary=True)
            wait_colorbar(vertical_boundary)

        def vertical_boundary():
            export_bar("colorbar_vertical_boundary", compare_main=True)
            if not dialog._fit_to_window:
                dialog._toggle_fit()
            dialog.combo_cbar_pos.setCurrentIndex(3)  # bottom
            dialog.spin_cbar_len.setValue(60)
            wait_colorbar(horizontal_ready)

        def horizontal_ready():
            export_bar("colorbar_horizontal_baseline")
            drag_bar((-7.0, 18.0))
            wait_colorbar(horizontal_dragged)

        def horizontal_dragged():
            export_bar("colorbar_horizontal_drag", compare_main=True)
            dialog.chk_cbar.setChecked(False)
            wait_colorbar(hidden_bar, visible=False)

        def hidden_bar():
            assert dialog.preview_label.colorbar_rect() is None
            record("colorbar_hidden_not_draggable", True, "关闭色条后移除预览拖动命中层")
            dialog.chk_cbar.setChecked(True)
            wait_colorbar(reopened_bar)

        def reopened_bar():
            check_inputs()
            assert dialog.grab().save(str(args.output_dir / "dialog_colorbar_drag.png"))
            record("colorbar_3d_dialog", True, "真实3D面板完成竖条/横条、fit/100%、边界和独立拖动")
            two_d = copy.copy(snapshot)
            volume = np.asarray(snapshot.payload["volume"])
            image = volume[:, :, volume.shape[2] // 2].T.copy()
            x, y = (np.asarray(snapshot.coords[key]) for key in ("X", "Y"))
            two_d.view = two_d.view_family = "2d"
            two_d.snapshot_id = snapshot.snapshot_id + "-colorbar-2d"
            two_d.source_page_title = "真实数据中间能量切片"
            two_d.payload = {
                "image": image,
                "extent": (float(x.min()), float(x.max()), float(y.min()), float(y.max())),
                "xlabel": snapshot.axis_label("X"), "ylabel": snapshot.axis_label("Y"),
                "title": "2D colorbar validation",
                "level_info": snapshot.payload["level_info"],
                "intensity_label": snapshot.payload["intensity_label"],
            }
            dialog._adopt_snapshot(two_d)
            dialog.spin_cbar_len.setValue(60)
            wait_colorbar(two_d_ready)

        def two_d_ready():
            assert dialog.preview_label.orientation_rect() is None
            export_bar("colorbar_2d_baseline")
            drag_bar((-12.0, 6.0))
            wait_colorbar(two_d_dragged)

        def two_d_dragged():
            export_bar("colorbar_2d_drag", compare_main=True)
            dialog.chk_transparent.setChecked(True)
            wait_colorbar(two_d_transparent)

        def two_d_transparent():
            export_bar("colorbar_2d_transparent", compare_main=True)
            assert dialog.grab().save(str(args.output_dir / "dialog_colorbar_2d.png"))
            record("colorbar_2d_worker", True, "真实数据切片走2D worker预览，色条拖动/导出/透明背景通过")
            finish()

        QTimer.singleShot(150, guarded(setup_vertical))

    deadline = time.perf_counter() + 75.0

    def await_load():
        context = window.current_render_context
        if window.core.raw_data is not None and isinstance(context, dict) and context.get("view") == "3d":
            QTimer.singleShot(1000, guarded(run_exports))
        elif time.perf_counter() < deadline:
            QTimer.singleShot(250, guarded(await_load))
        else:
            record("load_3d", False, "等待真实数据三维视图超时")
            finish()

    def load():
        window.load_data(str(args.data))
        QTimer.singleShot(250, guarded(await_load))

    QTimer.singleShot(500, guarded(load))
    QTimer.singleShot(240000, lambda: (record("watchdog", False, "总验证超时"), finish()))
    code = app.exec_()
    fault_log.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
