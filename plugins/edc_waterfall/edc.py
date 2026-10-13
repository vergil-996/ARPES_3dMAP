# -*- coding: utf-8 -*-
"""EDC 瀑布图的纯数值部分：采样、归一化、自动步长与自动标题。

这里是插件业务里唯一有数值逻辑的地方，只依赖 NumPy 与公开协议类型，不碰
Qt、不碰主窗口、不改写输入数据：

- **自动步长**：完整未裁剪动量轴的中位相邻间距，下限 ``1e-4``，回退 ``0.01``；
- **最近邻采样**：在裁剪后的动量轴上按物理位置落点、取最近采样点，端点必然
  入选；结果按物理 k 升序排列（降序轴会被排成升序）；
- **逐曲线归一化**：除以曲线最大值，全零/非有限曲线输出全零；
- **自动标题**：页面标题用中文，绘图与出版用 ASCII（宿主画布不含中文字形时
  不会出方框）；裁剪后缀按完整轴坐标生成。

输入与输出都保持**原始坐标排列**：E 翻转由宿主在显示、出版与导出处统一处理，
插件不参与。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np

from bandscope.extensions.api import DerivedInput2D, DerivedWaterfallResult

#: 自动步长无法计算时的回退值。
DEFAULT_STEP = 0.01
#: 步长允许范围（与迁移前的手动输入范围一致）。
MIN_STEP = 0.0001
MAX_STEP = 10.0
#: 曲线之间的显示偏移步长。
OFFSET_STEP = 1.2
#: 瀑布图横轴标签。
XLABEL = "Intensity (normalized, arb. u.)"

MODE_AUTO = "auto"
MODE_MANUAL = "manual"
MODE_CHOICES = (MODE_AUTO, MODE_MANUAL)


def step_mode_label(mode: str) -> str:
    return "自动（完整 k 轴中位间距）" if str(mode) != MODE_MANUAL else "手动"


def auto_step(full_k_axis) -> float:
    """完整未裁剪动量轴的中位相邻间距；不足以计算时回退 ``0.01``。"""
    values = np.asarray(full_k_axis, dtype=np.float64).reshape(-1)
    if values.size < 2:
        return DEFAULT_STEP
    diffs = np.abs(np.diff(values))
    diffs = diffs[np.isfinite(diffs) & (diffs > 1e-12)]
    if diffs.size == 0:
        return DEFAULT_STEP
    return float(max(float(np.median(diffs)), MIN_STEP))


def resolve_step(params: Mapping[str, Any], full_k_axis) -> float:
    """按逐页参数解析实际使用的 k 步长；手动值越界时抛可展示的错误。"""
    payload = params or {}
    mode = str(payload.get("k_step_mode") or MODE_AUTO)
    if mode == MODE_MANUAL:
        raw = payload.get("k_step")
        try:
            step = float(raw)
        except (TypeError, ValueError):
            raise ValueError("手动 k 步长必须是数值。")
        if not np.isfinite(step) or step < MIN_STEP or step > MAX_STEP:
            raise ValueError(
                f"手动 k 步长需在 {MIN_STEP}–{MAX_STEP} 之间（当前 {raw}）。"
            )
        return step
    return auto_step(full_k_axis)


def nearest_axis_indices(axis_values, step) -> np.ndarray:
    """按物理位置均匀落点，取最近采样点的下标；端点必然入选。

    结果按物理坐标升序排序，因此降序动量轴也会给出升序的采样序列。
    """
    values = np.asarray(axis_values, dtype=np.float64).flatten()
    if values.size == 0:
        return np.array([], dtype=np.int64)
    if values.size == 1:
        return np.array([0], dtype=np.int64)

    safe_step = max(float(step), MIN_STEP)
    axis_min = float(np.min(values))
    axis_max = float(np.max(values))
    sampled_positions = list(np.arange(axis_min, axis_max + safe_step * 0.5, safe_step))
    sampled_positions.extend([axis_min, axis_max])

    indices = {
        int(np.argmin(np.abs(values - position)))
        for position in sampled_positions
    }
    sorted_indices = sorted(indices, key=lambda idx: float(values[idx]))
    return np.asarray(sorted_indices, dtype=np.int64)


def normalize_curve(curve) -> np.ndarray:
    """逐曲线归一化：除以最大值；全零或非有限曲线输出全零。"""
    values = np.asarray(curve, dtype=np.float64)
    max_value = np.max(values) if values.size else 0.0
    if not np.isfinite(max_value) or abs(max_value) < 1e-12:
        return np.zeros_like(values, dtype=np.float64)
    return values / max_value


def format_number(value) -> str:
    """标题里的紧凑数字：保留两位小数、去掉末尾零；接近零按 0 处理。"""
    numeric = float(value)
    if abs(numeric) < 5e-7:
        numeric = 0.0
    text = f"{numeric:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def crop_rect_indices(input2d: DerivedInput2D) -> Optional[Tuple[int, int, int, int]]:
    rect = getattr(input2d.source, "crop_rect", None)
    if rect is None:
        return None
    return tuple(int(v) for v in rect)


def _safe_index(index, size: int) -> int:
    if size <= 0:
        return 0
    return int(np.clip(int(index), 0, size - 1))


def crop_suffix(input2d: DerivedInput2D) -> str:
    """裁剪后缀：``, kx a~b, E c~d``；没有矩形裁剪时为空串。"""
    rect = crop_rect_indices(input2d)
    if rect is None:
        return ""
    k_values = np.asarray(input2d.k_axis_full.values, dtype=np.float64).reshape(-1)
    e_values = np.asarray(input2d.e_axis_full.values, dtype=np.float64).reshape(-1)
    if k_values.size == 0 or e_values.size == 0:
        return ""
    x_low = _safe_index(rect[0], k_values.size)
    x_up = _safe_index(rect[1], k_values.size)
    y_low = _safe_index(rect[2], e_values.size)
    y_up = _safe_index(rect[3], e_values.size)
    k_label = str(input2d.k_axis.label or "k")
    return (
        f", {k_label} {format_number(k_values[x_low])}~{format_number(k_values[x_up])}, "
        f"E {format_number(e_values[y_low])}~{format_number(e_values[y_up])}"
    )


def integrated_bounds(
    input2d: DerivedInput2D,
) -> Tuple[Optional[Tuple[float, float]], Optional[float]]:
    """被积区间的物理范围与中心值；坐标不可用时返回 ``(None, None)``。"""
    values = np.asarray(input2d.integrated_axis.values, dtype=np.float64).reshape(-1)
    if values.size == 0:
        return None, None
    source = input2d.source
    low = _safe_index(source.integral_low, values.size)
    up = _safe_index(source.integral_up, values.size)
    mid = _safe_index(source.integral_mid, values.size)
    return (float(values[low]), float(values[up])), float(values[mid])


def waterfall_title(input2d: DerivedInput2D, step: float, *, ascii_only: bool) -> str:
    """自动标题：``EDC瀑布图 [ky, X中心 0.12, 步长 0.020]`` + 裁剪后缀。"""
    k_label = str(input2d.k_axis.label or "k")
    integrated_label = str(input2d.integrated_axis.label or "")
    _bounds, center = integrated_bounds(input2d)
    center_text = format_number(center if center is not None else 0.0)
    step_text = f"{float(step):.3f}"
    suffix = crop_suffix(input2d)
    if ascii_only:
        return (
            f"EDC Waterfall [{k_label}, {integrated_label} center {center_text}, "
            f"step {step_text}]{suffix}"
        )
    return (
        f"EDC瀑布图 [{k_label}, {integrated_label}中心 {center_text}, "
        f"步长 {step_text}]{suffix}"
    )


def energy_label(input2d: DerivedInput2D) -> str:
    """纵轴标签：有单位就用单位，没有就只用能量名，不猜单位。"""
    axis = input2d.e_axis
    unit = str(axis.unit or "").strip()
    if axis.has_coordinates and unit:
        return f"Energy ({unit})"
    return "Energy"


def compute_waterfall(input2d: DerivedInput2D) -> DerivedWaterfallResult:
    """宿主工作函数：``DerivedInput2D -> DerivedWaterfallResult``。

    只读输入；失败时抛 ``ValueError``，消息可直接展示。参数里的
    ``k_step_mode`` 取 ``auto`` / ``manual``，``k_step`` 是手动值。
    """
    params = dict(input2d.params or {})
    step = resolve_step(params, input2d.k_axis_full.values)
    k_crop = np.asarray(input2d.k_axis.values, dtype=np.float64).reshape(-1)
    data = np.asarray(input2d.data, dtype=np.float64)
    if data.ndim != 2 or data.shape[0] != k_crop.size:
        raise ValueError("输入数据与动量轴长度不一致，无法采样。")
    if data.shape[0] < 2:
        raise ValueError("动量轴上的采样点不足，无法生成 EDC 瀑布图。")

    local_indices = nearest_axis_indices(k_crop, step)
    if local_indices.size < 2:
        raise ValueError("当前 k 步长过大，至少需要两条 EDC 曲线。")

    rect = crop_rect_indices(input2d)
    offset = int(rect[0]) if rect is not None else 0
    k_values = k_crop[local_indices]
    raw_curves = np.asarray(data[local_indices, :], dtype=np.float64)
    curves = np.asarray(
        [normalize_curve(curve) for curve in raw_curves], dtype=np.float64
    )
    sample_indices = (local_indices + offset).astype(np.int64)
    bounds, center = integrated_bounds(input2d)

    resolved_params: Dict[str, Any] = {
        "k_step": float(step),
        "k_step_mode": str(params.get("k_step_mode") or MODE_AUTO),
        "offset_step": OFFSET_STEP,
        "sample_count": int(local_indices.size),
        "data_generation": int(input2d.data_generation),
        "source_page": str(input2d.source.source_page_id),
    }
    return DerivedWaterfallResult(
        k=k_values,
        sample_indices=sample_indices,
        energy=np.asarray(input2d.e_axis.values, dtype=np.float64).reshape(-1),
        raw_curves=raw_curves,
        curves=curves,
        curve_offsets=None,
        offset_step=OFFSET_STEP,
        title=waterfall_title(input2d, step, ascii_only=False),
        plot_title=waterfall_title(input2d, step, ascii_only=True),
        xlabel=XLABEL,
        ylabel=energy_label(input2d),
        k_label=str(input2d.k_axis.label or "k"),
        integrated_label=str(input2d.integrated_axis.label or ""),
        integrated_range=bounds,
        integrated_center=center,
        params=resolved_params,
    )


__all__ = [
    "DEFAULT_STEP",
    "MAX_STEP",
    "MIN_STEP",
    "MODE_AUTO",
    "MODE_CHOICES",
    "MODE_MANUAL",
    "OFFSET_STEP",
    "auto_step",
    "compute_waterfall",
    "crop_suffix",
    "energy_label",
    "format_number",
    "integrated_bounds",
    "nearest_axis_indices",
    "normalize_curve",
    "resolve_step",
    "step_mode_label",
    "waterfall_title",
]
