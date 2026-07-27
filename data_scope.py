from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np


Bounds6 = Tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class DataScopeDescriptor:
    """Immutable spatial scope for one [X, Y, E, T] source generation."""

    scope_id: str
    full_shape: Tuple[int, int, int, int]
    bounds: Optional[Bounds6] = None
    generation: int = 0

    def __post_init__(self):
        shape = tuple(int(size) for size in self.full_shape)
        if len(shape) != 4 or any(size <= 0 for size in shape):
            raise ValueError("A data scope requires a non-empty [X, Y, E, T] shape.")
        object.__setattr__(self, "full_shape", shape)

        if self.bounds is None:
            return
        if len(self.bounds) != 6:
            raise ValueError("ROI bounds must contain X/Y/E lower and upper indices.")

        normalized = []
        for axis in range(3):
            low = int(self.bounds[2 * axis])
            up = int(self.bounds[2 * axis + 1])
            if low > up:
                low, up = up, low
            if low < 0 or up >= shape[axis]:
                raise ValueError(
                    f"ROI axis {axis} bounds [{low}, {up}] exceed [0, {shape[axis] - 1}]."
                )
            normalized.extend((low, up))
        object.__setattr__(self, "bounds", tuple(normalized))

    @classmethod
    def full(cls, full_shape: Sequence[int]) -> "DataScopeDescriptor":
        return cls("full", tuple(int(size) for size in full_shape), None, 0)

    @property
    def is_full(self) -> bool:
        return self.bounds is None

    @property
    def label(self) -> str:
        return "完整数据" if self.is_full else f"ROI #{self.generation}"

    @property
    def spatial_slices(self) -> Tuple[slice, slice, slice]:
        if self.bounds is None:
            return tuple(slice(0, self.full_shape[axis]) for axis in range(3))
        return tuple(
            slice(self.bounds[2 * axis], self.bounds[2 * axis + 1] + 1)
            for axis in range(3)
        )

    @property
    def spatial_bounds(self) -> Bounds6:
        if self.bounds is not None:
            return self.bounds
        return (
            0,
            self.full_shape[0] - 1,
            0,
            self.full_shape[1] - 1,
            0,
            self.full_shape[2] - 1,
        )

    @property
    def compact_spatial_shape(self) -> Tuple[int, int, int]:
        bounds = self.spatial_bounds
        return tuple(bounds[2 * axis + 1] - bounds[2 * axis] + 1 for axis in range(3))

    @property
    def compact_shape(self) -> Tuple[int, int, int, int]:
        return self.compact_spatial_shape + (self.full_shape[3],)

    def axis_bounds(self, axis: int) -> Tuple[int, int]:
        axis = int(axis)
        if axis not in (0, 1, 2):
            raise ValueError("Spatial axis must be X, Y, or E.")
        bounds = self.spatial_bounds
        return int(bounds[2 * axis]), int(bounds[2 * axis + 1])

    def local_index(self, axis: int, logical_index: int) -> Optional[int]:
        low, up = self.axis_bounds(axis)
        logical_index = int(logical_index)
        if logical_index < low or logical_index > up:
            return None
        return logical_index - low

    def local_range(self, axis: int, low: int, up: int) -> Optional[Tuple[int, int]]:
        requested_low, requested_up = sorted((int(low), int(up)))
        scope_low, scope_up = self.axis_bounds(axis)
        intersect_low = max(requested_low, scope_low)
        intersect_up = min(requested_up, scope_up)
        if intersect_low > intersect_up:
            return None
        return intersect_low - scope_low, intersect_up - scope_low

    def extract(self, full_data) -> np.ndarray:
        source = np.asarray(full_data, dtype=np.float32)
        if tuple(source.shape) != self.full_shape:
            raise ValueError(
                f"Source shape {source.shape} does not match scope shape {self.full_shape}."
            )
        if self.is_full:
            return source
        x_slice, y_slice, e_slice = self.spatial_slices
        return source[x_slice, y_slice, e_slice, :]

    def physical_bounds(self, coords) -> np.ndarray:
        values = []
        for axis, key in enumerate(("X", "Y", "E")):
            low, up = self.axis_bounds(axis)
            coordinate_values = np.asarray(coords[key])
            values.extend((float(coordinate_values[low]), float(coordinate_values[up])))
        return np.asarray(values, dtype=np.float32)


class ScopedSpatialArray(np.ndarray):
    """A compact 3D array that retains its position in the full data domain."""

    descriptor: DataScopeDescriptor

    def __new__(cls, values, descriptor: DataScopeDescriptor):
        instance = np.asarray(values).view(cls)
        instance.descriptor = descriptor
        return instance

    def __array_finalize__(self, source):
        self.descriptor = getattr(source, "descriptor", None)


class ScopedDataVolume:
    """Compact [X, Y, E, T] values plus full-domain coordinate semantics."""

    def __init__(self, descriptor: DataScopeDescriptor, values):
        source = np.asarray(values, dtype=np.float32)
        if tuple(source.shape) != descriptor.compact_shape:
            raise ValueError(
                f"Compact source shape {source.shape} does not match {descriptor.compact_shape}."
            )
        self.descriptor = descriptor
        self.values = source

    @property
    def shape(self):
        return self.descriptor.full_shape

    @property
    def ndim(self):
        return 4

    @property
    def dtype(self):
        return self.values.dtype

    @property
    def nbytes(self):
        return int(self.values.nbytes)

    @property
    def scope_id(self):
        return self.descriptor.scope_id

    def frame(self, time_index: int) -> ScopedSpatialArray:
        time_index = int(np.clip(int(time_index), 0, self.shape[3] - 1))
        return ScopedSpatialArray(self.values[..., time_index], self.descriptor)

    def time_integral(self, low: int, up: int) -> ScopedSpatialArray:
        low, up = sorted((int(low), int(up)))
        low = int(np.clip(low, 0, self.shape[3] - 1))
        up = int(np.clip(up, 0, self.shape[3] - 1))
        result = np.sum(self.values[..., low : up + 1], axis=3, dtype=np.float32)
        return ScopedSpatialArray(result, self.descriptor)

    def materialize_3d(self, values=None) -> np.ndarray:
        compact = self.frame(0) if values is None else np.asarray(values, dtype=np.float32)
        if self.descriptor.is_full:
            return np.asarray(compact, dtype=np.float32)
        output = np.zeros(self.shape[:3], dtype=np.float32)
        output[self.descriptor.spatial_slices] = compact
        return output

    def extract_bounds(self, bounds: Bounds6) -> Tuple[np.ndarray, Bounds6]:
        intersections = []
        local_slices = []
        for axis in range(3):
            requested_low, requested_up = sorted(
                (int(bounds[2 * axis]), int(bounds[2 * axis + 1]))
            )
            scope_low, scope_up = self.descriptor.axis_bounds(axis)
            low = max(requested_low, scope_low)
            up = min(requested_up, scope_up)
            if low > up:
                empty_shape = list(self.values.shape)
                empty_shape[axis] = 0
                return np.zeros(empty_shape, dtype=np.float32), tuple(
                    value for pair in intersections for value in pair
                )
            intersections.append((low, up))
            local_slices.append(slice(low - scope_low, up - scope_low + 1))
        compact = self.values[local_slices[0], local_slices[1], local_slices[2], :]
        normalized = tuple(value for pair in intersections for value in pair)
        return compact, normalized


def scoped_descriptor_from_array(data) -> Optional[DataScopeDescriptor]:
    if isinstance(data, ScopedDataVolume):
        return data.descriptor
    if isinstance(data, ScopedSpatialArray):
        return data.descriptor
    return None

