# -*- coding: utf-8 -*-
"""图片导出：快照捕获与导出协调。

- ``capture_snapshot(window)`` 在 GUI 线程冻结当前页的完整科学状态：
  复用 ``_compute_render_context`` + ``_render_context_for_visual_flip``
  得到与屏幕一致的显示语义，然后复制所需数组；不从 worker 线程读取
  Qt 控件，不触碰 VisualEngine 类级共享色阶状态。
- ``render_and_save`` 用同一 renderer 生成预览与正式文件；原子写入。
- 主视图状态（相机、色阶锁定、页面、数据）在导出前后保持逐位一致。
"""
from __future__ import annotations

import datetime
import os
import re
import uuid
from typing import Any, Dict, Optional, Tuple

import numpy as np
from crop_model import waterfall_offsets

from publication_models import (
    DEFAULT_STYLE_ID,
    FAMILY_FORMATS,
    OutputOptions,
    PublicationSnapshot,
    axis_label,
    intensity_label_for,
    load_output_options,
    load_overrides,
    load_style_id,
    resolve_style,
    save_output_options,
    save_overrides,
    save_style_id,
    validate_overrides,
    view_family_for,
)
from publication_renderers import (
    RenderError,
    compute_level_info,
    render_snapshot,
    save_figure,
)


class ExportError(Exception):
    """导出前置条件不满足（无数据/配置页/非均匀网格等）。"""


# ---------------------------------------------------------------------------
# 坐标与标签
# ---------------------------------------------------------------------------

_UNIFORM_RTOL = 1e-5


def _check_uniform(coords: np.ndarray, axis_key: str) -> None:
    """均匀网格校验（plan §5）：非均匀网格首版明确报不支持。"""
    values = np.asarray(coords, dtype=np.float64).ravel()
    if values.size < 3:
        return
    diffs = np.diff(values)
    scale = max(1e-12, float(np.max(np.abs(diffs))))
    if float(np.max(np.abs(diffs - np.median(diffs)))) > _UNIFORM_RTOL * scale + 1e-12:
        raise ExportError(
            f"{axis_key} 轴坐标为非均匀网格，首版科研导出暂不支持该网格；"
            "请在主视图中确认坐标来源。"
        )


def _coord_meta(window) -> Tuple[Dict[str, np.ndarray], Dict[str, str], Dict[str, Optional[str]]]:
    core = window.core
    coords = {}
    for key in ("X", "Y", "E", "delay"):
        values = core.coords.get(key)
        if values is None:
            values = np.arange(core.raw_data.shape[{"X": 0, "Y": 1, "E": 2, "delay": 3}[key]])
        coords[key] = np.asarray(values, dtype=np.float64).ravel().copy()
    sources = dict(getattr(core, "coord_sources", {}) or {})
    units = dict(getattr(core, "coord_units", {}) or {})
    for key in coords:
        sources.setdefault(key, "index")
        units.setdefault(key, None)
    return coords, sources, units


def _context_xlabel_axis_key(xlabel: str) -> Optional[str]:
    text = str(xlabel or "").lower()
    if "energy" in text or text.startswith("e"):
        return "E"
    if "delay" in text or "time" in text:
        return "delay"
    if "kx" in text:
        return "X"
    if "ky" in text:
        return "Y"
    return None


def _resolve_xlabel(xlabel: str, coords, sources, units) -> str:
    """1D 语境的横轴标签：能量/延迟轴按坐标来源重建，其余保留原样。"""
    key = _context_xlabel_axis_key(xlabel)
    if key is None:
        return str(xlabel or "")
    return axis_label(key, sources.get(key, "index"), units.get(key))


# ---------------------------------------------------------------------------
# 快照捕获（GUI 线程）
# ---------------------------------------------------------------------------


def capture_snapshot(window) -> PublicationSnapshot:
    """冻结当前页的完整科学显示状态。必须在 GUI 线程调用。"""
    spec = window.left_workspace.current_spec() or window.left_workspace.home_spec()
    if spec is None or window.core.raw_data is None:
        raise ExportError("尚未加载数据，无法导出图片。")
    if spec.page_kind == "control_panel":
        raise ExportError("配置页不是科学结果，不支持图片导出。")

    context = window._compute_render_context(spec)
    if context is None:
        raise ExportError("当前页没有可导出的完整结果。")
    if context.get("crop_empty"):
        raise ExportError("当前裁剪范围内没有有效数据。")
    render_context = window._render_context_for_visual_flip(context)
    if render_context is None:
        raise ExportError("当前页没有可导出的完整结果。")

    view = str(render_context.get("view"))
    family = view_family_for(view)
    if family is None or view == "config":
        raise ExportError("配置页不是科学结果，不支持图片导出。")

    coords, sources, units = _coord_meta(window)
    levels = tuple(float(v) for v in window._get_display_levels())
    cmap_name = str(window.page_render.get_selected_cmap())
    locked = None
    try:
        from render_core import VisualEngine

        locked_range = VisualEngine.locked_data_range()
        locked = (
            (float(locked_range[0]), float(locked_range[1]))
            if locked_range is not None
            else None
        )
    except Exception:
        locked = None

    snapshot = PublicationSnapshot(
        snapshot_id=uuid.uuid4().hex,
        source_page_id=spec.page_id,
        source_page_title=str(spec.title),
        page_kind=str(spec.page_kind),
        view=view,
        view_family=family,
        captured_at=datetime.datetime.now().isoformat(timespec="seconds"),
        cmap_name=cmap_name,
        levels_params=levels,
        locked_range=locked,
        coords=coords,
        coord_sources=sources,
        coord_units=units,
        source_desc=_build_source_desc(window, spec, render_context),
    )
    if spec.page_kind == "home" and window.core.has_time_axis:
        try:
            snapshot.home_frame_index = int(window.timeline_bar.slider_time.value())
        except Exception:
            snapshot.home_frame_index = None

    if view == "3d":
        _freeze_3d(window, snapshot, render_context)
    elif view == "2d":
        _freeze_2d(window, snapshot, render_context)
    elif view == "1d":
        _freeze_1d(window, snapshot, render_context)
    elif view == "1d_comparison":
        _freeze_1d_comparison(window, snapshot, render_context)
    elif view == "waterfall":
        _freeze_waterfall(window, snapshot, render_context)
    else:
        raise ExportError(f"暂不支持导出的视图类型：{view}")
    return snapshot


def _build_source_desc(window, spec, render_context) -> str:
    """面板顶部展示的来源信息（位于预览图片之外）。"""
    parts = [str(spec.title)]
    view = render_context.get("view")
    if spec.page_kind == "home" and window.core.has_time_axis:
        t_idx = int(window.timeline_bar.slider_time.value())
        delay_coords = window.core.coords.get("delay")
        if delay_coords is None:
            delay_coords = []
        delays = np.asarray(delay_coords, dtype=np.float64).ravel()
        if delays.size > t_idx:
            parts.append(f"帧 {t_idx}（delay {delays[t_idx]:.4g}）")
        else:
            parts.append(f"帧 {t_idx}")
    elif spec.page_kind == "time_integral":
        params = spec.params
        parts.append(f"时间积分 {int(params.get('t_low', 0))}~{int(params.get('t_up', 0))}")
    elif view == "waterfall":
        params = spec.params
        if params.get("source_mode") == "time_integral":
            parts.append("时间积分来源")
    return " · ".join(parts)


def _freeze_3d(window, snapshot: PublicationSnapshot, render_context) -> None:
    data = np.array(render_context["data"], dtype=np.float32, copy=True)
    if data.ndim != 3:
        raise ExportError("3D 导出需要三维体数据。")
    full_shape = tuple(int(v) for v in render_context.get("full_shape", data.shape))
    data_bounds = render_context.get("data_bounds")
    if data_bounds is None:
        data_bounds = (0, data.shape[0] - 1, 0, data.shape[1] - 1, 0, data.shape[2] - 1)
    data_bounds = tuple(int(v) for v in data_bounds)

    clip_ranges = render_context.get("clip_ranges")
    clip_render = None
    if clip_ranges is not None:
        clip_shape = render_context.get("full_shape", data.shape)
        clip_render = window.core.logical_to_render_bounds(clip_ranges, clip_shape)
        clip_render = [float(v) for v in clip_render]

    include_zero = bool(render_context.get("include_zero", False))
    level_info = compute_level_info(
        data, snapshot.levels_params, include_zero=include_zero,
        locked_range=snapshot.locked_range,
    )

    for key in ("X", "Y", "E"):
        _check_uniform(snapshot.coords[key], key)

    camera = _capture_camera(window)
    plotter = window.plotter
    viewport_aspect = max(plotter.width(), 1) / max(plotter.height(), 1)

    snapshot.camera = camera
    snapshot.payload = {
        "volume": data,
        "data_bounds": data_bounds,
        "full_shape": full_shape,
        "spacing": tuple(
            200.0 / (full_shape[axis] - 1) if full_shape[axis] > 1 else 1.0
            for axis in range(3)
        ),
        "clip_render_bounds": clip_render,
        "include_zero": include_zero,
        "opacity_mode": str(window.page_render.combo_map.currentText()),
        "show_axes": bool(window.timeline_bar.switch_axes.isChecked()),
        "viewport_aspect": float(viewport_aspect),
        "level_info": level_info,
        "intensity_label": intensity_label_for(snapshot.page_kind),
        "axis_titles": [snapshot.axis_label("X"), snapshot.axis_label("Y"), snapshot.axis_label("E")],
    }


def _capture_camera(window) -> Dict[str, Any]:
    """复制相机完整状态（position/focal/view-up/投影/裁剪范围）。"""
    camera = window.plotter.camera
    payload: Dict[str, Any] = {
        "position": tuple(float(v) for v in camera.position),
        "focal_point": tuple(float(v) for v in camera.focal_point),
        # pyvista 0.47 的 Camera 没有 view_up 属性（VTK 侧才是 GetViewUp），
        # 误用会抛 PyVistaAttributeError；在 Qt 槽里未捕获异常会 qFatal 崩溃。
        "view_up": tuple(float(v) for v in camera.up),
        "parallel_projection": False,
    }
    try:
        payload["parallel_projection"] = bool(camera.GetParallelProjection())
        payload["parallel_scale"] = float(camera.GetParallelScale())
        payload["view_angle"] = float(camera.GetViewAngle())
        payload["clipping_range"] = tuple(float(v) for v in camera.clipping_range)
    except Exception:
        pass
    return payload


def _freeze_2d(window, snapshot: PublicationSnapshot, render_context) -> None:
    """冻结 2D 显示图：精确复刻 render_2d_slice 的显示变换（一次且仅一次）。"""
    data = np.asarray(render_context["data"], dtype=np.float64)
    slice_info = dict(render_context["slice_info"])
    coords = snapshot.coords
    xp, yp, zp = coords["X"], coords["Y"], coords["E"]

    idx = int(slice_info["axis"])
    axis_views = {
        0: ("X", "Y", "E", [yp[0], yp[-1], zp[0], zp[-1]]),
        1: ("Y", "X", "E", [xp[0], xp[-1], zp[0], zp[-1]]),
        2: ("E", "X", "Y", [xp[0], xp[-1], yp[0], yp[-1]]),
    }
    sliced_label, x_key, y_key, ext = axis_views.get(idx, axis_views[2])

    img = np.array(data.T, dtype=np.float64, copy=True)
    # E 轴翻转是数据显示方向修正（已由 _render_context_for_visual_flip 标记）
    if slice_info.get("display_e_flip") and idx in (0, 1):
        img = np.flip(img, axis=0)

    mode = slice_info.get("mode")
    if mode == "integral":
        low, up = slice_info["range"]
        title = f"{sliced_label}-Integral ({low}~{up})"
    else:
        title = f"{sliced_label}-Slice ({slice_info.get('index')})"
    title = slice_info.get("title_override", title)
    ext = slice_info.get("extent_override", ext)
    ext = [float(value) for value in ext]
    # 与主视图一致：降序 extent 归一化并同步翻转像素
    if ext[0] > ext[1]:
        img = np.flip(img, axis=1)
        ext[0], ext[1] = ext[1], ext[0]
    if ext[2] > ext[3]:
        img = np.flip(img, axis=0)
        ext[2], ext[3] = ext[3], ext[2]

    plot_axes = render_context.get("plot_axes")
    if plot_axes:
        x_key = plot_axes.get("x_key", x_key)
        y_key = plot_axes.get("y_key", y_key)

    _check_uniform(snapshot.coords[x_key], x_key)
    _check_uniform(snapshot.coords[y_key], y_key)

    level_info = compute_level_info(
        img, snapshot.levels_params, locked_range=snapshot.locked_range
    )
    snapshot.payload = {
        "image": img,
        "extent": ext,
        "title": str(title),
        "xlabel": axis_label(
            x_key, snapshot.coord_sources.get(x_key, "index"),
            snapshot.coord_units.get(x_key),
        ),
        "ylabel": axis_label(
            y_key, snapshot.coord_sources.get(y_key, "index"),
            snapshot.coord_units.get(y_key),
        ),
        "level_info": level_info,
        "intensity_label": intensity_label_for(snapshot.page_kind),
    }


def _ascending_xy(x_data, y_data):
    """与主视图 _ascending_curve_data 一致：降序横轴翻为升序显示。"""
    x = np.asarray(x_data, dtype=np.float64).ravel().copy()
    y = np.asarray(y_data, dtype=np.float64).ravel().copy()
    if x.size > 1 and x.size == y.size and x[0] > x[-1]:
        x = np.flip(x, axis=0)
        y = np.flip(y, axis=0)
    return x, y


def _freeze_1d(window, snapshot: PublicationSnapshot, render_context) -> None:
    x, y = _ascending_xy(render_context["x_data"], render_context["y_data"])
    if x.size == 0 or not np.any(np.isfinite(y)):
        raise ExportError("曲线数据为空或全部无效，不能生成科学图片。")
    xlabel = _resolve_xlabel(
        render_context.get("xlabel", ""), snapshot.coords,
        snapshot.coord_sources, snapshot.coord_units,
    )
    snapshot.payload = {
        "curve": {"x": x, "y": y, "label": None},
        "title": str(render_context.get("title") or snapshot.source_page_title),
        "xlabel": xlabel,
        "ylabel": str(render_context.get("ylabel") or "Intensity (a.u.)"),
    }


def _freeze_1d_comparison(window, snapshot: PublicationSnapshot, render_context) -> None:
    curves = []
    for curve in render_context.get("curves", []):
        x, y = _ascending_xy(curve.get("x_data"), curve.get("y_data"))
        if x.size == 0:
            continue
        curves.append(
            {
                "x": x,
                "y": y,
                "label": str(curve.get("label") or curve.get("source_title") or f"Curve {len(curves) + 1}"),
            }
        )
    if not curves:
        raise ExportError("比较页没有可导出的曲线。")
    xlabel = _resolve_xlabel(
        render_context.get("xlabel", ""), snapshot.coords,
        snapshot.coord_sources, snapshot.coord_units,
    )
    snapshot.payload = {
        "curves": curves,
        "title": str(render_context.get("title") or snapshot.source_page_title),
        "xlabel": xlabel,
        "ylabel": str(render_context.get("ylabel") or "Intensity (a.u.)"),
        "comparison_kind": render_context.get("comparison_kind"),
    }


def _freeze_waterfall(window, snapshot: PublicationSnapshot, render_context) -> None:
    energy = np.asarray(render_context["energy_axis"], dtype=np.float64).ravel().copy()
    curves = np.array(render_context["curves"], dtype=np.float64, copy=True)
    if curves.ndim != 2 or curves.size == 0:
        raise ExportError("瀑布图没有可导出的曲线。")
    # 与 _render_waterfall_plot 一致：能量轴升序显示
    if energy.size > 1 and energy[0] > energy[-1]:
        energy = np.flip(energy, axis=0)
        curves = np.flip(curves, axis=1)
    k_values = np.asarray(render_context["k_values"], dtype=np.float64).ravel().copy()

    ylabel = axis_label(
        "E", snapshot.coord_sources.get("E", "index"), snapshot.coord_units.get("E")
    )
    snapshot.payload = {
        "energy_axis": energy,
        "curves": curves,
        "k_values": k_values,
        "offset_step": float(render_context.get("offset_step", 1.2)),
        "curve_offsets": waterfall_offsets(render_context).copy(),
        "title": str(render_context.get("title") or snapshot.source_page_title),
        "xlabel": str(render_context.get("xlabel") or "Intensity (normalized, arb. u.)"),
        "ylabel": ylabel,
    }


# ---------------------------------------------------------------------------
# 样式/输出偏好解析
# ---------------------------------------------------------------------------


def committed_style_for(settings, family: str):
    style_id = load_style_id(settings, family) or DEFAULT_STYLE_ID[family]
    style = resolve_style(family, style_id)
    overrides = load_overrides(settings, family, style.style_id)
    return style, overrides


def commit_style(settings, family: str, style_id: str, overrides) -> None:
    save_style_id(settings, family, style_id)
    save_overrides(settings, family, style_id, validate_overrides(family, overrides))


def output_options_for(settings, family: str) -> OutputOptions:
    options = load_output_options(settings)
    if options.fmt not in FAMILY_FORMATS.get(family, ("png",)):
        options = options.with_updates(fmt="png")
    return options


def persist_output_options(settings, options: OutputOptions) -> None:
    save_output_options(settings, options)


# ---------------------------------------------------------------------------
# 渲染与保存
# ---------------------------------------------------------------------------


def safe_filename(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", str(text or "").strip())
    return cleaned.strip("_") or "figure"


def default_filename(snapshot, style, options: OutputOptions) -> str:
    return f"{safe_filename(snapshot.source_page_title)}_{style.style_id}.{options.fmt}"


def render_and_save(snapshot, style, overrides, options: OutputOptions, path: str) -> None:
    """同一 renderer 生成正式文件；原子写入；失败不损坏旧文件。"""
    if options.fmt not in FAMILY_FORMATS.get(snapshot.view_family, ("png",)):
        raise ExportError(f"当前视图族首版不支持 {options.fmt.upper()} 输出。")
    fig = render_snapshot(snapshot, style, overrides, options, dpi=int(options.dpi))
    try:
        save_figure(fig, path, options)
    finally:
        fig.clear()
