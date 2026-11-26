"""Index-space XY geometry for replaying selections on exact rotated grids."""
from __future__ import annotations

import numpy as np


def _xy_shape(shape) -> tuple[int, int]:
    values = tuple(shape)
    if len(values) != 2:
        raise ValueError("XY rotation geometry requires exactly two dimensions.")
    result = tuple(int(size) for size in values)
    if any(size <= 0 or size != original for size, original in zip(result, values)):
        raise ValueError("XY rotation geometry requires positive integer dimensions.")
    return result


def _angle(value) -> float:
    result = float(value or 0.0)
    if not np.isfinite(result):
        raise ValueError("Rotation angles must be finite.")
    return result


def creation_xy_coordinates(shape, creation_angle, current_angle) -> tuple[np.ndarray, np.ndarray]:
    """Map current-grid samples to the grid on which a selection was created."""
    nx, ny = _xy_shape(shape)
    x, y = np.meshgrid(np.arange(nx, dtype=float), np.arange(ny, dtype=float), indexing="ij")
    delta = np.deg2rad(_angle(creation_angle) - _angle(current_angle))
    if abs(delta) < 1e-12:
        return x, y
    cx, cy = (nx - 1) / 2.0, (ny - 1) / 2.0
    x, y = x - cx, y - cy
    cosine, sine = np.cos(delta), np.sin(delta)
    return cx + cosine * x - sine * y, cy + sine * x + cosine * y


def xy_canvas_mask(shape, rotation_angle=0.0, *, source_bounds=None) -> np.ndarray:
    """Locate samples whose centers map inside the unrotated XY source canvas."""
    nx, ny = _xy_shape(shape)
    if source_bounds is None:
        bounds = (0, nx - 1, 0, ny - 1)
    else:
        values = tuple(source_bounds)
        if len(values) != 4:
            raise ValueError("XY source bounds must contain four inclusive indices.")
        bounds = tuple(int(value) for value in values)
        if any(value != original for value, original in zip(bounds, values)):
            raise ValueError("XY source bounds must contain integer indices.")
        if bounds[0] > bounds[1] or bounds[2] > bounds[3]:
            raise ValueError("XY source bounds must be ordered.")
    x, y = np.meshgrid(np.arange(nx, dtype=float), np.arange(ny, dtype=float), indexing="ij")
    angle = np.deg2rad(_angle(rotation_angle))
    if abs(angle) >= 1e-12:
        cx, cy = (nx - 1) / 2.0, (ny - 1) / 2.0
        centered_x, centered_y = x - cx, y - cy
        cosine, sine = np.cos(angle), np.sin(angle)
        x = cx + cosine * centered_x + sine * centered_y
        y = cy - sine * centered_x + cosine * centered_y
    tolerance = 1e-9
    return ((x >= bounds[0] - tolerance) & (x <= bounds[1] + tolerance)
            & (y >= bounds[2] - tolerance) & (y <= bounds[3] + tolerance))
