"""Bilinear sampling and per-segment caching for momentum-path slices."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from bandscope.core.path_slice import PathNode, PathSliceModel


@dataclass(frozen=True)
class MomentumCalibration:
    x: np.ndarray
    y: np.ndarray
    calibrated: bool
    unit: str = ""

    @classmethod
    def create(
        cls,
        x: Sequence[float],
        y: Sequence[float],
        *,
        x_source: str = "index",
        y_source: str = "index",
        x_unit: Optional[str] = None,
        y_unit: Optional[str] = None,
    ) -> "MomentumCalibration":
        x_values = np.asarray(x, dtype=np.float64).reshape(-1)
        y_values = np.asarray(y, dtype=np.float64).reshape(-1)
        monotonic = all(
            values.size < 2
            or np.all(np.diff(values) > 0)
            or np.all(np.diff(values) < 0)
            for values in (x_values, y_values)
        )
        units_match = not (x_unit and y_unit) or str(x_unit) == str(y_unit)
        calibrated = bool(
            x_source == "file"
            and y_source == "file"
            and monotonic
            and units_match
            and np.isfinite(x_values).all()
            and np.isfinite(y_values).all()
        )
        unit = str(x_unit or y_unit or "") if calibrated else ""
        return cls(x_values, y_values, calibrated, unit)

    @property
    def label(self) -> str:
        return f"k ({self.unit})" if self.unit else ("k" if self.calibrated else "k (index)")

    @property
    def thickness_unit(self) -> str:
        return self.unit if self.calibrated and self.unit else ("index" if not self.calibrated else "")

    @staticmethod
    def _forward(values: np.ndarray, indices: np.ndarray) -> np.ndarray:
        if values.size == 1:
            return np.full(np.shape(indices), values[0], dtype=np.float64)
        return np.interp(indices, np.arange(values.size, dtype=np.float64), values)

    @staticmethod
    def _inverse(values: np.ndarray, coordinates: np.ndarray) -> np.ndarray:
        if values.size == 1:
            return np.zeros(np.shape(coordinates), dtype=np.float64)
        indices = np.arange(values.size, dtype=np.float64)
        if values[0] > values[-1]:
            values = values[::-1]
            indices = indices[::-1]
        coordinates = np.asarray(coordinates, dtype=np.float64)
        result = np.interp(coordinates, values, indices)
        outside = (coordinates < values[0]) | (coordinates > values[-1])
        return np.where(outside, np.nan, result)

    def to_physical(self, x: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        if not self.calibrated:
            return np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
        return self._forward(self.x, x), self._forward(self.y, y)

    def to_logical(self, x: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        if not self.calibrated:
            return np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
        return self._inverse(self.x, x), self._inverse(self.y, y)

    def node_physical(self, node: PathNode) -> Tuple[float, float]:
        x, y = self.to_physical(node.x, node.y)
        return float(x), float(y)

    def segment_length(self, start: PathNode, end: PathNode) -> float:
        x0, y0 = self.node_physical(start)
        x1, y1 = self.node_physical(end)
        return float(np.hypot(x1 - x0, y1 - y0))

    @property
    def sample_spacing(self) -> float:
        if not self.calibrated:
            return 1.0
        steps = []
        for values in (self.x, self.y):
            differences = np.abs(np.diff(values))
            differences = differences[np.isfinite(differences) & (differences > 0)]
            if differences.size:
                steps.append(float(np.median(differences)))
        return min(steps) if steps else 1.0


@dataclass
class SegmentSample:
    index: int
    intensity: np.ndarray
    column_slice: slice
    start_k: float
    end_k: float
    thickness: float


@dataclass
class PathSample:
    k: np.ndarray
    energy: np.ndarray
    intensity: np.ndarray
    node_k: np.ndarray
    segments: List[SegmentSample]
    calibration: MomentumCalibration

    @property
    def segment_ranges(self) -> List[Tuple[int, int]]:
        return [(item.column_slice.start, item.column_slice.stop) for item in self.segments]


def _bilinear(volume: np.ndarray, x: np.ndarray, y: np.ndarray, valid_mask=None) -> np.ndarray:
    """Sample (point, energy), excluding invalid corners from the weights."""
    source = np.asarray(volume, dtype=np.float64)
    if source.ndim != 3:
        raise ValueError("路径切片需要 [kx, ky, E] 三维数据。")
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    y = np.asarray(y, dtype=np.float64).reshape(-1)
    nx, ny = source.shape[:2]
    inside = np.isfinite(x) & np.isfinite(y) & (x >= 0) & (y >= 0) & (x <= nx - 1) & (y <= ny - 1)
    safe_x = np.clip(np.nan_to_num(x, nan=0.0), 0, max(nx - 1, 0))
    safe_y = np.clip(np.nan_to_num(y, nan=0.0), 0, max(ny - 1, 0))
    x0 = np.floor(safe_x).astype(np.int64)
    y0 = np.floor(safe_y).astype(np.int64)
    x1 = np.minimum(x0 + 1, nx - 1)
    y1 = np.minimum(y0 + 1, ny - 1)
    fx = safe_x - x0
    fy = safe_y - y0
    corners = (
        (x0, y0, (1.0 - fx) * (1.0 - fy)),
        (x1, y0, fx * (1.0 - fy)),
        (x0, y1, (1.0 - fx) * fy),
        (x1, y1, fx * fy),
    )
    numerator = np.zeros((x.size, source.shape[2]), dtype=np.float64)
    weights_sum = np.zeros_like(numerator)
    for xi, yi, weight in corners:
        values = source[xi, yi, :]
        valid = np.broadcast_to(inside[:, None], values.shape).copy() & np.isfinite(values)
        if valid_mask is not None:
            mask = np.asarray(valid_mask, dtype=bool)
            valid &= mask[xi, yi, None] if mask.ndim == 2 else mask[xi, yi, :]
        weighted = weight[:, None] * valid
        numerator += np.where(valid, values, 0.0) * weighted
        weights_sum += weighted
    with np.errstate(invalid="ignore", divide="ignore"):
        result = numerator / weights_sum
    result[weights_sum <= 0] = np.nan
    return result


def sample_segment(
    volume: np.ndarray,
    start: PathNode,
    end: PathNode,
    calibration: MomentumCalibration,
    *,
    thickness: float = 0.0,
    index: int = 0,
    valid_mask=None,
) -> SegmentSample:
    length = calibration.segment_length(start, end)
    if length <= 1e-12:
        raise ValueError("相邻路径节点必须位于不同位置。")
    spacing = max(float(calibration.sample_spacing), 1e-12)
    count = max(2, int(np.ceil(length / spacing)) + 1)
    t = np.linspace(0.0, 1.0, count)
    p0 = np.asarray(calibration.node_physical(start), dtype=np.float64)
    p1 = np.asarray(calibration.node_physical(end), dtype=np.float64)
    physical = p0[None, :] + t[:, None] * (p1 - p0)[None, :]
    center_x, center_y = calibration.to_logical(physical[:, 0], physical[:, 1])
    thickness = max(0.0, float(thickness))
    offset_count = max(1, int(np.ceil(thickness / spacing)) + 1)
    offsets = np.linspace(-thickness / 2.0, thickness / 2.0, offset_count)
    direction = (p1 - p0) / length
    normal = np.asarray([-direction[1], direction[0]])
    samples = []
    for offset in offsets:
        shifted = physical + float(offset) * normal[None, :]
        x, y = calibration.to_logical(shifted[:, 0], shifted[:, 1])
        samples.append(_bilinear(volume, x, y, valid_mask))
    stacked = np.stack(samples, axis=0)
    valid = np.isfinite(stacked)
    count_valid = valid.sum(axis=0)
    intensity = np.divide(
        np.where(valid, stacked, 0.0).sum(axis=0),
        count_valid,
        out=np.full(stacked.shape[1:], np.nan, dtype=np.float64),
        where=count_valid > 0,
    )
    return SegmentSample(
        index=int(index),
        intensity=intensity,
        column_slice=slice(0, count),
        start_k=0.0,
        end_k=length,
        thickness=thickness,
    )


def _geometry_key(model: PathSliceModel, calibration: MomentumCalibration) -> tuple:
    return (
        tuple((node.x, node.y, node.label) for node in model.nodes),
        calibration.calibrated,
        calibration.unit,
        tuple(np.asarray(calibration.x, dtype=float)),
        tuple(np.asarray(calibration.y, dtype=float)),
    )


class PathSliceSampler:
    """Raw segment cache; display colors and level changes never invalidate it."""

    CACHE_LIMIT = 96

    def __init__(self):
        self._segments: Dict[tuple, SegmentSample] = {}

    def clear(self) -> None:
        self._segments.clear()

    def sample(
        self,
        volume: np.ndarray,
        energy: Sequence[float],
        model: PathSliceModel,
        calibration: MomentumCalibration,
        *,
        source_key: Any = None,
        valid_mask=None,
        only_segments: Optional[Sequence[int]] = None,
    ) -> PathSample:
        model.validate()
        source = np.asarray(volume, dtype=np.float64)
        energy_values = np.asarray(energy, dtype=np.float64).reshape(-1)
        if source.shape[2] != energy_values.size:
            raise ValueError("能量坐标与来源体尺寸不一致。")
        geometry = _geometry_key(model, calibration)
        sampled: List[SegmentSample] = []
        cumulative = 0.0
        output_columns = 0
        requested = set(only_segments) if only_segments is not None else None
        for index, (start, end) in enumerate(zip(model.nodes, model.nodes[1:])):
            key = (
                repr(source_key),
                geometry,
                index,
                float(model.segments[index].thickness),
            )
            segment = self._segments.pop(key, None)
            if segment is not None:
                self._segments[key] = segment
            if segment is None:
                if requested is not None and index not in requested:
                    raise ValueError(f"路径第 {index + 1} 段尚未取样。")
                segment = sample_segment(
                    source,
                    start,
                    end,
                    calibration,
                    thickness=model.segments[index].thickness,
                    index=index,
                    valid_mask=valid_mask,
                )
                self._segments[key] = segment
                while len(self._segments) > self.CACHE_LIMIT:
                    self._segments.pop(next(iter(self._segments)))
            count = segment.intensity.shape[0]
            length = calibration.segment_length(start, end)
            start_column = output_columns if index == 0 else output_columns - 1
            stop_column = start_column + count
            item = SegmentSample(
                index=index,
                intensity=segment.intensity,
                column_slice=slice(start_column, stop_column),
                start_k=cumulative,
                end_k=cumulative + length,
                thickness=segment.thickness,
            )
            sampled.append(item)
            cumulative += length
            output_columns = stop_column
        arrays = []
        distances = []
        for item in sampled:
            values = item.intensity
            k = np.linspace(item.start_k, item.end_k, values.shape[0])
            if item.index:
                values = values[1:]
                k = k[1:]
            arrays.append(values)
            distances.append(k)
        return PathSample(
            k=np.concatenate(distances),
            energy=energy_values.copy(),
            intensity=np.concatenate(arrays, axis=0),
            node_k=np.asarray([0.0] + [item.end_k for item in sampled], dtype=np.float64),
            segments=sampled,
            calibration=calibration,
        )
