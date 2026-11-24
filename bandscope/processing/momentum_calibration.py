"""Two-point calibration of momentum axes in logical index coordinates."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class MomentumCalibrationResult:
    x: np.ndarray
    y: np.ndarray
    source_distance: float
    target_distance: float
    scale: float
    point_a: tuple
    point_b: tuple
    unit: str = ""


def _coordinate_axis(values, name):
    try:
        axis = np.asarray(values, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} coordinates must be a numeric one-dimensional sequence.") from exc
    if not axis.size or not np.isfinite(axis).all():
        raise ValueError(f"{name} coordinates must be non-empty and finite.")
    return np.array(axis, dtype=np.float64, copy=True)


def _logical_point(point, name):
    try:
        values = np.asarray(point, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain two finite logical coordinates.") from exc
    if values.size != 2 or not np.isfinite(values).all():
        raise ValueError(f"{name} must contain two finite logical coordinates.")
    return float(values[0]), float(values[1])


def _physical_point(point, x, y):
    return (
        float(np.interp(point[0], np.arange(x.size, dtype=np.float64), x)),
        float(np.interp(point[1], np.arange(y.size, dtype=np.float64), y)),
    )


def calibrate_momentum_axes(
    x,
    y,
    point_a,
    point_b,
    target_distance,
    *,
    unit="",
):
    """Scale both momentum axes uniformly from a two-point physical distance."""
    x_values = _coordinate_axis(x, "X")
    y_values = _coordinate_axis(y, "Y")
    first = _logical_point(point_a, "point_a")
    second = _logical_point(point_b, "point_b")

    for name, point in (("point_a", first), ("point_b", second)):
        if not 0.0 <= point[0] <= x_values.size - 1:
            raise ValueError(f"{name} x index is outside the X axis.")
        if not 0.0 <= point[1] <= y_values.size - 1:
            raise ValueError(f"{name} y index is outside the Y axis.")
    if first == second:
        raise ValueError("Calibration points must be distinct.")

    try:
        target = float(target_distance)
    except (TypeError, ValueError) as exc:
        raise ValueError("Target distance must be a finite positive number.") from exc
    if not np.isfinite(target) or target <= 0.0:
        raise ValueError("Target distance must be a finite positive number.")

    physical_a = _physical_point(first, x_values, y_values)
    physical_b = _physical_point(second, x_values, y_values)
    with np.errstate(over="ignore", invalid="ignore"):
        source = float(np.hypot(
            physical_b[0] - physical_a[0],
            physical_b[1] - physical_a[1],
        ))
    if not np.isfinite(source):
        raise ValueError("Source distance must be finite.")
    if source == 0.0:
        raise ValueError("Calibration points must have a non-zero physical separation.")

    with np.errstate(over="ignore", invalid="ignore", divide="ignore", under="ignore"):
        scale = target / source
        calibrated_x = x_values * scale
        calibrated_y = y_values * scale
    if not np.isfinite(scale) or scale <= 0.0:
        raise ValueError("Calibration scale must be finite and positive.")
    if not np.isfinite(calibrated_x).all() or not np.isfinite(calibrated_y).all():
        raise ValueError("Calibrated coordinates must be finite.")

    calibrated_x.setflags(write=False)
    calibrated_y.setflags(write=False)
    return MomentumCalibrationResult(
        x=calibrated_x,
        y=calibrated_y,
        source_distance=source,
        target_distance=target,
        scale=float(scale),
        point_a=first,
        point_b=second,
        unit=str(unit or ""),
    )
