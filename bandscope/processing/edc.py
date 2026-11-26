"""Coordinate-aware energy distribution curve integration."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bandscope.core.crop_model import CropSelection, SPATIAL_KEYS, index_interval
from bandscope.core.data_scope import scoped_descriptor_from_array


@dataclass(frozen=True)
class EdcCurveData:
    energy: np.ndarray
    intensity: np.ndarray
    index_bounds: tuple[int, int, int, int, int, int]


def _spatial_shape(spatial_shape) -> tuple[int, int, int]:
    try:
        values = tuple(spatial_shape)
        if len(values) != 3:
            raise ValueError
        shape = tuple(int(size) for size in values)
        if any(size <= 0 for size in shape) or any(
            int(size) != size for size in values
        ):
            raise ValueError
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("EDC 需要三个正整数维度的完整空间形状。") from exc
    return shape


def _coordinate_values(coords, key: str, size: int) -> np.ndarray:
    try:
        values = np.asarray(coords[key], dtype=np.float64)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"EDC 缺少有效的 {key} 轴坐标。") from exc
    if values.ndim != 1 or values.size != size or not np.all(np.isfinite(values)):
        raise ValueError(f"{key} 轴坐标必须是长度为 {size} 的有限一维数组。")
    if values.size > 1:
        steps = np.diff(values)
        if not (np.all(steps > 0) or np.all(steps < 0)):
            raise ValueError(f"{key} 轴坐标必须严格单调。")
    return values


def _normal_index_bounds(index_bounds, size: int) -> tuple[int, int]:
    try:
        values = tuple(index_bounds)
        if len(values) != 2:
            raise ValueError
        if any(int(value) != value for value in values):
            raise ValueError
        low, up = sorted(int(value) for value in values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("EDC 法向范围必须是两个 inclusive 整数索引。") from exc
    if low < 0 or up >= size:
        raise ValueError("EDC 法向范围超出完整数据域。")
    return low, up


def selection_index_bounds(
    selection: CropSelection, coords, spatial_shape, *, normal_index_bounds=None
) -> tuple[int, int, int, int, int, int] | None:
    """Convert a spatial selection to absolute inclusive X/Y/E indices."""
    shape = _spatial_shape(spatial_shape)
    axes = tuple(selection.axes)
    normal_key = None
    normal_bounds = None
    if selection.view == "3d":
        if axes != SPATIAL_KEYS:
            raise ValueError("3D EDC 选区必须按 X、Y、E 轴给出范围。")
        axis_ranges = {
            key: selection.bounds[2 * axis:2 * axis + 2]
            for axis, key in enumerate(axes)
        }
    elif selection.view == "2d":
        if (
            len(axes) != 2
            or len(set(axes)) != 2
            or not set(axes).issubset(SPATIAL_KEYS)
        ):
            raise ValueError("2D EDC 选区必须包含两个不同的 X、Y、E 轴。")
        axis_ranges = {
            key: selection.bounds[2 * axis:2 * axis + 2]
            for axis, key in enumerate(axes)
        }
        normal_key = next(key for key in SPATIAL_KEYS if key not in axes)
        normal_axis = SPATIAL_KEYS.index(normal_key)
        if normal_index_bounds is None:
            if set(axes) != {"X", "Y"}:
                raise ValueError("XZ/YZ EDC 选区必须提供法向 inclusive 索引范围。")
            normal_bounds = (0, shape[2] - 1)
        else:
            normal_bounds = _normal_index_bounds(
                normal_index_bounds, shape[normal_axis]
            )
    else:
        raise ValueError("EDC 仅支持 3D 体选区或任意两个空间轴的 2D 选区。")

    bounds = []
    for axis, key in enumerate(SPATIAL_KEYS):
        if selection.view == "2d" and key == normal_key:
            bounds.extend(normal_bounds)
            continue
        values = _coordinate_values(coords, key, shape[axis])
        interval = index_interval(values, *axis_ranges[key])
        if interval is None:
            return None
        bounds.extend(interval)
    return tuple(bounds)


def _index_bounds(index_bounds) -> tuple[int, int, int, int, int, int]:
    try:
        values = tuple(index_bounds)
        if len(values) != 6:
            raise ValueError
        bounds = tuple(int(value) for value in values)
        if any(int(value) != value for value in values):
            raise ValueError
        if any(bounds[2 * axis] > bounds[2 * axis + 1] for axis in range(3)):
            raise ValueError
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("EDC 索引范围必须是六个 inclusive 整数，且下限不大于上限。") from exc
    return bounds


def integrate_edc(
    data_3d, energy_axis, index_bounds, *, ignore_nan=True, valid_mask=None
) -> EdcCurveData | None:
    """Sum X/Y intensities over an absolute ROI and its valid grid samples."""
    source = np.asarray(data_3d)
    if source.ndim != 3:
        raise ValueError("EDC 积分需要已完成时间缩减的 [X, Y, E] 三维数据。")
    requested = _index_bounds(index_bounds)
    descriptor = scoped_descriptor_from_array(data_3d)
    if descriptor is None:
        full_shape = source.shape
        scope_bounds = tuple(
            value for size in source.shape for value in (0, size - 1)
        )
    else:
        full_shape = descriptor.full_shape[:3]
        scope_bounds = descriptor.spatial_bounds
        if source.shape != descriptor.compact_spatial_shape:
            raise ValueError("EDC 局部数据形状与其数据域描述器不一致。")

    energy = np.asarray(energy_axis)
    if energy.ndim != 1 or energy.size != full_shape[2]:
        raise ValueError("能量坐标必须是与完整数据域等长的一维数组。")

    intersections = []
    local_slices = []
    for axis in range(3):
        scope_low, scope_up = scope_bounds[2 * axis:2 * axis + 2]
        low = max(requested[2 * axis], scope_low)
        up = min(requested[2 * axis + 1], scope_up)
        if low > up:
            return None
        intersections.extend((low, up))
        local_slices.append(slice(low - scope_low, up - scope_low + 1))

    selected = source[tuple(local_slices)]
    selected_mask = None
    if valid_mask is not None:
        mask = np.asarray(valid_mask, dtype=bool)
        if mask.ndim != 3:
            raise ValueError("EDC 有效掩膜必须是三维布尔网格。")
        if mask.shape == full_shape:
            mask_slices = tuple(
                slice(intersections[2 * axis], intersections[2 * axis + 1] + 1)
                for axis in range(3)
            )
        elif mask.shape == source.shape:
            mask_slices = tuple(local_slices)
        else:
            raise ValueError("EDC 有效掩膜必须匹配完整数据域或紧凑数据形状。")
        selected_mask = mask[mask_slices]

    if selected_mask is not None:
        selected = np.where(selected_mask, selected, np.nan)
    if ignore_nan:
        intensity = np.nansum(selected, axis=(0, 1), dtype=np.float64)
        has_valid_sample = np.any(~np.isnan(selected), axis=(0, 1))
        intensity = np.where(has_valid_sample, intensity, np.nan)
    else:
        if selected_mask is not None:
            selected = np.where(selected_mask, selected, 0.0)
        intensity = np.sum(selected, axis=(0, 1), dtype=np.float64)
        if selected_mask is not None:
            has_selected_sample = np.any(selected_mask, axis=(0, 1))
            intensity = np.where(has_selected_sample, intensity, np.nan)
    energy_low, energy_up = intersections[4:6]
    return EdcCurveData(
        energy=energy[energy_low:energy_up + 1].copy(),
        intensity=np.asarray(intensity, dtype=np.float64),
        index_bounds=tuple(intersections),
    )
