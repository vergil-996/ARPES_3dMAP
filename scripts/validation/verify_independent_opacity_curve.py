"""真实窗口验证三维不透明度控制点曲线、删除与各渲染路径一致性。"""
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

import numpy as np
from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QContextMenuEvent, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def main():
    from scripts.validation._common import arguments

    args = arguments(
        "opacity-curve-control-points", dynamic=True,
        default_data=Path(".local/data/WSe2_step.npz"),
    )
    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app
    from bandscope.core.display_style import (
        INDEPENDENT_MODE,
        legacy_display_style,
        new_display_style,
    )
    from bandscope.core.local_enhancement import STATE_KEY
    from bandscope.core.view_recipe import ViewRecipeDocument, digest, state_digest
    from bandscope.processing.display_transfer import (
        OPACITY_MAPS,
        channels,
        legacy_opacity_points,
        opacity_reference_distance,
    )
    from bandscope.processing import gpu_display
    from bandscope.ui.enhancement_controls import SHOW_ALL_REGIONS_ID

    refactored_app.UpdateController = _NullUpdateController
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    checks = []
    screenshots = []

    def wait_ready(timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QTest.qWait(80)
            if (window.__dict__.get("_render_exact_ready")
                    and window.__dict__.get("_display_stats_job") is None
                    and window.__dict__.get("_enhancement_roi_job") is None):
                return
        raise AssertionError("等待精确渲染和后台统计超时")

    def capture(name, widget=None):
        path = args.output_dir / name
        QApplication.processEvents()
        if widget is None and window.current_render_context.get("view") == "3d":
            window.plotter.render()
        QTest.qWait(160)
        target = window if widget is None else widget
        if not target.grab().save(str(path)):
            raise AssertionError(f"截图失败：{path}")
        screenshots.append(path.name)

    def capture_volume(name):
        path = args.output_dir / name
        window.plotter.render()
        window.plotter.screenshot(str(path))
        if not path.is_file() or path.stat().st_size == 0:
            raise AssertionError(f"三维帧缓冲截图失败：{path}")
        screenshots.append(path.name)

    window.load_data(str(args.data))
    wait_ready()
    page = window.page_render
    curve = page.opacity_editor.curve
    context = window.current_render_context
    data = np.asarray(context["data"])
    fields = window._enhancement_cache[1]
    finite = data[np.isfinite(data)]

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
        print(f"[PASS] {name}", flush=True)

    def alpha_now():
        return np.array(window._enhancement_cache[1]["alpha"], copy=True)

    def color_now():
        return np.array(window._enhancement_cache[1]["color"], copy=True)

    def backend_alpha(values, style):
        info = fields["background_level_info"]
        distance = opacity_reference_distance(context.get("full_shape", values.shape))
        _color, alpha = channels(
            values, info, style["opacity_mode"], style=style, unit_distance=distance,
        )
        return np.asarray(alpha)

    def move_to(position):
        event = QMouseEvent(
            QEvent.MouseMove, QPointF(position), Qt.NoButton, Qt.LeftButton, Qt.NoModifier,
        )
        QApplication.sendEvent(curve, event)
        QTest.qWait(40)

    def point_for(intensity, alpha):
        return QPoint(int(round(curve._x(intensity))), int(round(curve._y(alpha))))

    def press_rail(intensity):
        rail = curve._rail_rect()
        position = QPoint(int(round(curve._x(intensity))), rail.center().y())
        QTest.mousePress(curve, Qt.LeftButton, pos=position)
        return position

    def set_points(points, *, flush=True):
        page.opacity_editor.set_points(points)
        if flush:
            window.flush_render_levels_refresh()
            wait_ready()

    check("不透明度卡片在三维页面可见", page.grp_opacity.isVisible())
    check("旧的显示模式与映射控件已移除",
          not hasattr(page, "combo_display_mode") and not hasattr(page, "combo_map")
          and not hasattr(page, "btn_apply_map") and not hasattr(page, "display_parameters"))
    check("新页面默认三个控制点", page.opacity_editor.points() == [[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]])
    check("默认使用含极值的完整强度范围",
          fields["background_range"] == (float(finite.min()), float(finite.max())))

    legacy_style = legacy_display_style((0.0, 50.0, 100.0), "线性")
    legacy_alpha = np.asarray(channels(
        data, fields["background_level_info"], "线性",
        style=legacy_style,
        unit_distance=opacity_reference_distance(context.get("full_shape", data.shape)),
    )[1])
    np.testing.assert_allclose(alpha_now(), legacy_alpha, rtol=0.0, atol=1e-6)
    del legacy_alpha
    checks.append("默认三点曲线与旧兼容线性在默认色阶下逐体素一致")
    capture_volume("volume-default.png")

    before_alpha = alpha_now()
    before_color = color_now()
    page.s_low.setValue(18)
    page.s_gamma.setValue(62)
    window.flush_render_levels_refresh()
    wait_ready()
    check("调整黑、灰、白场只改变颜色", np.array_equal(alpha_now(), before_alpha)
          and not np.array_equal(color_now(), before_color))
    del before_alpha, before_color
    page.s_low.setValue(0)
    page.s_gamma.setValue(50)
    window.flush_render_levels_refresh()
    wait_ready()

    set_points([[0.0, 0.0], [0.2, 0.8], [0.6, 0.8], [1.0, 0.1]])
    plateau = alpha_now()
    info = window._enhancement_cache[1]["background_level_info"]
    q = np.clip((data.astype(np.float64) - info["data_min"]) / max(info["span"], 1e-12), 0, 1)
    expected = np.interp(q, [0.0, 0.2, 0.6, 1.0], [0.0, 0.8, 0.8, 0.1])
    np.testing.assert_allclose(plateau[np.isfinite(data)], expected[np.isfinite(data)],
                               rtol=0.0, atol=1e-6)
    del expected, q
    checks.append("下降段与平台的 α 逐体素等于分段线性曲线")
    capture_volume("volume-plateau.png")

    gpu_ran = False
    if gpu_display.set_enabled(True):
        gpu_alpha = backend_alpha(data, page.get_display_style())
        np.testing.assert_allclose(gpu_alpha[np.isfinite(data)],
                                   plateau[np.isfinite(data)], rtol=0.0, atol=1e-6)
        gpu_display.set_enabled(False)
        gpu_display.reset()
        gpu_ran = True
        del gpu_alpha
        checks.append("CUDA 映射与 CPU 分段线性曲线一致")
    else:
        gpu_display.set_enabled(False)
        print(f"[SKIP] GPU：{gpu_display.failure_reason()}", flush=True)
    del plateau

    set_points([[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]])
    rail_before = curve._rail_point()
    start = point_for(0.5, 0.5)
    end = point_for(0.7, 0.9)
    QTest.mousePress(curve, Qt.LeftButton, pos=start)
    if curve._mode != "point" or curve._selected != 1:
        raise AssertionError("内部控制点未命中")
    for index in range(1, 5):
        move_to(QPoint(int(start.x() + (end.x() - start.x()) * index / 4),
                       int(start.y() + (end.y() - start.y()) * index / 4)))
    check("内部点拖动同时改变强度与 α", page.opacity_editor.points()[1][0] > 0.6
          and page.opacity_editor.points()[1][1] > 0.8)
    QTest.mouseRelease(curve, Qt.LeftButton, pos=end)
    wait_ready()
    check("拖动曲线上的点不带动底轨圆点", curve._rail_point() == rail_before)
    check("松手生成最终精确帧", window._render_exact_ready)

    set_points([[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]])
    below = QPoint(int(round(curve._x(0.6))), curve._rail_rect().center().y() + 6)
    QTest.mousePress(curve, Qt.LeftButton, pos=point_for(0.5, 0.5))
    move_to(QPoint(below.x(), curve._plot_rect().bottom() + 30))
    QTest.mouseRelease(curve, Qt.LeftButton, pos=QPoint(below.x(), below.y()))
    wait_ready()
    points = page.opacity_editor.points()
    check("拖过零线并在底轨松手仍保留控制点", len(points) == 3 and points[1][1] == 0.0)
    persisted = copy.deepcopy(points)
    window._persist_active_page_state()
    page.opacity_editor.set_points([[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]])
    window._restore_page_ui_state(window.active_page_spec)
    window._restore_enhancement_style()
    check("重新打开页面后零线控制点仍在", page.opacity_editor.points() == persisted)

    set_points([[0.0, 0.0], [0.3, 0.4], [0.6, 0.8], [1.0, 1.0]])
    shape_alpha = alpha_now()
    render_count = window.volume_session.render_count
    rail_y = curve._rail_rect().center().y()
    press_rail(0.3)
    move_to(QPoint(int(round(curve._x(0.45))), rail_y))
    check("底轨水平定位只重绘控件", window.volume_session.render_count == render_count)
    QTest.mouseRelease(curve, Qt.LeftButton, pos=QPoint(int(round(curve._x(0.45))), rail_y))

    press_rail(0.3)
    move_to(point_for(0.3, 0.1))
    check("向上拖动中途可继续上推", curve._mode == "insert")
    move_to(QPoint(int(round(curve._x(0.3))), rail_y))
    check("拖回底轨后黑点重新挂回轮廓",
          curve._mode == "rail" and curve.moving_dot_position() is None)
    move_to(QPoint(int(round(curve._x(0.7))), rail_y))
    check("重新挂回后仍可水平拖动底轨", abs(curve._rail_intensity - 0.7) < 0.02)
    QTest.mouseRelease(curve, Qt.LeftButton, pos=QPoint(int(round(curve._x(0.7))), rail_y))
    check("中途往返不生成控制点", len(page.opacity_editor.points()) == 4)

    QTest.mousePress(curve, Qt.LeftButton, pos=QPoint(
        int(round(curve._x(0.45))), curve._rail_rect().center().y()))
    gap_y = curve._plot_rect().bottom() + 12
    move_to(QPoint(int(round(curve._x(0.45))), gap_y))
    gap_dot = curve.moving_dot_position()
    check("黑点连续穿过底轨与横轴之间的间距",
          gap_dot is not None and abs(gap_dot.y() - gap_y) < 2.0
          and len(page.opacity_editor.points()) == 4)
    capture("opacity-detached-gap.png", page.opacity_editor)
    move_to(point_for(0.45, 0.25))
    detached_dot = curve.moving_dot_position()
    check("向上拖动时黑点脱离底轨外轮廓",
          curve._mode == "insert" and detached_dot is not None
          and abs(detached_dot.x() - curve._x(0.45)) < 1.5)
    check("碰线前底轨保持空心且不回填", curve.backfill_progress() is None)
    capture("opacity-detached-dot.png", page.opacity_editor)
    move_to(point_for(0.45, 0.85))
    insert_progress = curve.backfill_progress()
    check("从底轨向上拖到曲线上插入新控制点", len(page.opacity_editor.points()) == 5
          and curve._selected is not None)
    check("插入后底轨原位开始回填动画", insert_progress is not None and insert_progress >= 0.0)
    capture("opacity-backfill.png", page.opacity_editor)
    QTest.mouseRelease(curve, Qt.LeftButton, pos=point_for(0.45, 0.85))
    wait_ready()
    check("插入瞬间曲线形状不变", np.allclose(alpha_now(), shape_alpha, rtol=0.0, atol=1e-6))
    QTest.qWait(260)
    check("回填动画约 200 毫秒结束", curve.backfill_progress() is None)

    press_rail(0.2)
    move_to(QPoint(int(round(curve._x(0.2))), curve._rail_rect().center().y() - 14))
    QTest.mouseRelease(curve, Qt.LeftButton, pos=QPoint(
        int(round(curve._x(0.2))), curve._rail_rect().center().y() - 14))
    before_count = len(page.opacity_editor.points())
    check("碰线前松手不生成控制点", before_count == 5)
    press_rail(0.20)
    QTest.mouseRelease(curve, Qt.LeftButton, pos=QPoint(
        int(round(curve._x(0.20))), curve._rail_rect().center().y()))
    QTest.mousePress(curve, Qt.LeftButton, pos=QPoint(
        int(round(curve._x(0.20))), curve._rail_rect().center().y()))
    move_to(point_for(0.20, 0.7))
    QTest.mouseRelease(curve, Qt.LeftButton, pos=point_for(0.20, 0.7))
    wait_ready()
    check("分两次拖动同样可以加点", len(page.opacity_editor.points()) == 6)

    press_rail(0.6)
    move_to(point_for(0.6, 0.8))
    QTest.mouseRelease(curve, Qt.LeftButton, pos=point_for(0.6, 0.8))
    check("碰到已有点时选中而不重复添加", len(page.opacity_editor.points()) == 6
          and curve._selected is not None)

    set_points([[0.0, 0.0], [0.5, 0.0], [1.0, 1.0]])
    press_rail(0.25)
    move_to(point_for(0.25, 0.4))
    QTest.mouseRelease(curve, Qt.LeftButton, pos=point_for(0.25, 0.4))
    wait_ready()
    zero_point = page.opacity_editor.points()[1]
    check("α＝0 的段落上也能加点", abs(zero_point[0] - 0.25) < 0.03 and abs(zero_point[1]) < 1e-6)

    set_points([[0.0, 0.0], [0.3, 0.4], [0.6, 0.8], [1.0, 1.0]])
    QTest.mousePress(curve, Qt.LeftButton, pos=point_for(0.3, 0.4))
    QTest.mouseRelease(curve, Qt.LeftButton, pos=point_for(0.3, 0.4))
    curve.setFocus(Qt.MouseFocusReason)
    QTest.keyClick(curve, Qt.Key_Delete)
    wait_ready()
    check("Delete 删除选中内部点并连接邻点", len(page.opacity_editor.points()) == 3)
    check("删除后两个端点保留",
          page.opacity_editor.points()[0] == [0.0, 0.0]
          and page.opacity_editor.points()[-1] == [1.0, 1.0])

    set_points([[0.0, 0.0], [0.25, 0.3], [0.5, 0.7], [0.75, 0.5], [1.0, 1.0]])
    QTest.mousePress(curve, Qt.LeftButton, pos=point_for(0.75, 0.5))
    QTest.mouseRelease(curve, Qt.LeftButton, pos=point_for(0.75, 0.5))
    position = point_for(0.75, 0.5)
    event = QContextMenuEvent(QContextMenuEvent.Mouse, position, curve.mapToGlobal(position))
    from unittest.mock import patch

    with patch("bandscope.ui.opacity_controls.QMenu") as menu_class:
        menu = menu_class.return_value
        action = menu.addAction.return_value
        curve.contextMenuEvent(event)
        check("右键菜单提供删除控制点", menu.addAction.call_args[0][0] == "删除控制点")
        action.triggered.connect.call_args[0][0]()
    wait_ready()
    check("右键菜单删除内部点", len(page.opacity_editor.points()) == 4)

    QTest.mousePress(curve, Qt.LeftButton, pos=point_for(0.0, 0.0))
    QTest.mouseRelease(curve, Qt.LeftButton, pos=point_for(0.0, 0.0))
    count_endpoint = len(page.opacity_editor.points())
    QTest.keyClick(curve, Qt.Key_Delete)
    check("两个端点受保护，Delete 不删除", len(page.opacity_editor.points()) == count_endpoint)
    page.s_low.setFocus(Qt.OtherFocusReason)
    snapshot_points = page.opacity_editor.points()
    QTest.keyClick(page.s_low, Qt.Key_Delete)
    check("焦点离开曲线后 Delete 不影响曲线", page.opacity_editor.points() == snapshot_points)

    window._set_theme("dark")
    QTest.qWait(300)
    capture("opacity-dark.png", page.opacity_editor)
    window._set_theme("light")
    window.resize(1000, 720)
    QTest.qWait(300)
    capture("opacity-narrow-light.png", page.opacity_editor)
    window._set_theme("dark")
    QTest.qWait(300)
    capture("opacity-narrow-dark.png", page.opacity_editor)
    window._set_theme("light")
    window.resize(1280, 800)
    QTest.qWait(300)

    from bandscope.core.display_style import new_display_style

    style = new_display_style(
        (0.0, 50.0, 100.0), "线性", (0.0, 1.0),
        [[0.0, 0.0], [0.2, 0.75], [0.55, 0.75], [0.8, 0.2], [1.0, 0.9]],
    )
    window._select_enhancement_target("")
    window.page_render.set_display_style(style, view="3d")
    window.flush_render_levels_refresh()
    wait_ready()
    state = window._enhancement_state()
    background = copy.deepcopy(state["background"])
    window._add_enhancement_region()
    wait_ready()
    selected = window._enhancement_selected()
    check("局部区域使用同一分段线性曲线", selected["style"] == background)
    set_points([[0.0, 0.0], [0.4, 0.1], [0.7, 0.9], [1.0, 1.0]])
    region_style = window._enhancement_selected()["style"]
    check("局部区域只修改当前目标", state["background"] == background
          and region_style["opacity"]["points"] == [[0.0, 0.0], [0.4, 0.1], [0.7, 0.9], [1.0, 1.0]])
    capture("region-opacity-curve.png")
    index = page.region_combo.findData(SHOW_ALL_REGIONS_ID)
    page.region_combo.setCurrentIndex(index)
    wait_ready()
    batch = [[0.0, 0.0], [0.3, 0.85], [1.0, 0.5]]
    set_points(batch)
    check("全部显示同步调整背景与区域",
          all(style["opacity"]["points"] == batch for style in (
              state["background"], *[region["style"] for region in state["regions"]])))
    capture_volume("volume-weak-band.png")
    capture("window-final-light.png")

    from bandscope.exporting.blender_project import BlenderProjectError  # noqa: F401
    from bandscope.exporting.blender_scene import ALPHA_CAP, prepare_blender_scene
    from bandscope.exporting.publication_export import capture_snapshot, render_and_save
    from bandscope.exporting.publication_models import OutputOptions, resolve_style

    snapshot = capture_snapshot(window)
    frozen = snapshot.payload["enhancement"]
    live = window._enhancement_display(window.current_render_context)
    np.testing.assert_array_equal(frozen["alpha"], live["alpha"])
    info = frozen["background_level_info"]
    expected_alpha = np.asarray(channels(
        data, info, snapshot.payload["opacity_mode"],
        style=frozen["background_style"],
        unit_distance=opacity_reference_distance(snapshot.payload["full_shape"]),
    )[1])
    multiplier = snapshot.payload.get("opacity_multiplier")
    if multiplier is not None:
        expected_alpha = np.clip(
            expected_alpha * np.asarray(multiplier, dtype=np.float32)[None, None, :], 0.0, 1.0,
        )
    from bandscope.core.local_enhancement import region_slices

    background_mask = np.ones(data.shape, dtype=bool)
    for region in live["regions"]:
        background_mask[region_slices(context, region)] = False
    np.testing.assert_allclose(
        frozen["alpha"][background_mask], expected_alpha[background_mask],
        rtol=0.0, atol=1e-6,
    )
    del expected_alpha, background_mask
    checks.append("出版导出冻结的 α 与共享曲线一致")
    render_and_save(
        snapshot, resolve_style("3d", None), {},
        OutputOptions(width_mm=90, height_mm=90, dpi=90),
        str(args.output_dir / "publication-curve.png"),
    )
    check("出版导出图已生成", (args.output_dir / "publication-curve.png").is_file())
    scene = prepare_blender_scene(snapshot)
    np.testing.assert_array_equal(scene.alpha, frozen["alpha"])
    expected_density = -np.log1p(-np.minimum(scene.alpha, ALPHA_CAP)) / np.float32(
        scene.unit_distance_blender)
    np.testing.assert_allclose(scene.density, expected_density, rtol=1e-6, atol=1e-7)
    check("Blender 密度由同一 α 与参考距离换算且保持有限",
          np.all(np.isfinite(scene.density)))
    del expected_density
    metadata = scene.metadata()
    check("新版 Blender 工程保存点列并使用 schema 12",
          metadata["schema_version"] == 12
          and metadata["bandscope_edit"]["display_style"]["opacity"]["points"] == batch)
    del scene, metadata

    reference = opacity_reference_distance(context.get("full_shape", data.shape))
    legacy_points = np.asarray(legacy_opacity_points(
        {"threshold": 21.739, "transition": 15.0, "gamma": 1.3, "density": 40.0}, reference,
    ), dtype=float)
    worst = 0.0
    for intensity in np.linspace(0.0, 1.0, 1025):
        threshold = 0.21739
        signal = max(intensity - threshold, 0.0)
        position = np.clip((intensity - threshold) / 0.15, 0.0, 1.0)
        gate = position * position * (3.0 - 2.0 * position)
        sigma = 0.2 * signal ** 1.3 * gate
        bad = abs(np.interp(intensity, legacy_points[:, 0], legacy_points[:, 1])
                  - (1.0 - np.exp(-sigma * reference)))
        worst = max(worst, bad)
    check("旧四参数曲线迁移误差不超过 1/255", worst <= 1.0 / 255.0)

    document = json.loads(json.dumps(snapshot.view_recipe))
    page_record = next(p for p in document["pages"] if p["page_id"] == snapshot.source_page_id)
    legacy_style = legacy_display_style((0.0, 50.0, 100.0), "幂函数")
    legacy_state = copy.deepcopy(page_record["params"][STATE_KEY])
    legacy_state["background"] = copy.deepcopy(legacy_style)
    for region in legacy_state.get("regions", []):
        region["style"] = copy.deepcopy(legacy_style)
    page_record["params"][STATE_KEY] = legacy_state
    old_key = page_record["recipe_id"]
    node = document["recipes"].pop(old_key)
    node["params"] = copy.deepcopy(page_record["params"])
    new_key = digest(node)
    document["recipes"][new_key] = node
    page_record["recipe_id"] = new_key
    document["view_state"]["display"]["background_style"] = copy.deepcopy(legacy_style)
    document["schema_version"] = 4
    document["state_digest"] = state_digest(document)
    migrated = ViewRecipeDocument(document).validate().data
    migrated_style = migrated["pages"][0]["params"][STATE_KEY]["background"]
    check("旧复现文件升级为可编辑点列", migrated["schema_version"] == 5
          and migrated_style["display_mode"] == INDEPENDENT_MODE
          and "points" in migrated_style["opacity"])
    points = np.asarray(migrated_style["opacity"]["points"], dtype=float)
    low, high = 0.0, 1.0
    ramp = OPACITY_MAPS["幂函数"]
    worst = 0.0
    for intensity in np.linspace(0.0, 1.0, 1025):
        mapped = np.clip((intensity - low) / max(high - low, 1e-9), 0.0, 1.0)
        expected_alpha = np.interp(mapped, np.linspace(0.0, 1.0, len(ramp)), ramp)
        worst = max(worst, abs(np.interp(intensity, points[:, 0], points[:, 1]) - expected_alpha))
    check("旧兼容曲线迁移误差不超过 1/255", worst <= 1.0 / 255.0)

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "curve.bsview.json"
        saved = ViewRecipeDocument(migrated).dumps()
        path.write_text(saved, encoding="utf-8")
        restored = ViewRecipeDocument.load(path).data
        restored_points = restored["pages"][0]["params"][STATE_KEY]["background"]["opacity"]["points"]
        check("新曲线保存后能准确还原", restored_points == migrated_style["opacity"]["points"])

    check("科学数据未被修改",
          np.array_equal(data, window.current_render_context["data"], equal_nan=True))
    report = {
        "data": str(args.data),
        "frame": int(window.timeline_bar.slider_time.value()),
        "points": page.opacity_editor.points(),
        "checks": checks,
        "screenshots": screenshots,
        "gpu_executed": gpu_ran,
        "status": "passed",
    }
    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if args.hold:
        app.exec_()
    else:
        window.close()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
