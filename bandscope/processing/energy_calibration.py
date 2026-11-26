"""Two-point energy calibration in logical index coordinates."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class EnergyCalibrationResult:
    energy: np.ndarray
    source_spacing: float
    target_spacing: float
    scale: float
    reference_index: float
    second_index: float
    unit: str = ""


def _coordinate_axis(values):
    message = "Energy coordinates must be a numeric one-dimensional sequence."
    try:
        raw = np.asarray(values)
        if raw.ndim != 1 or np.iscomplexobj(raw):
            raise ValueError(message)
        axis = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(message) from exc
    if not axis.size or not np.isfinite(axis).all():
        raise ValueError("Energy coordinates must be non-empty and finite.")
    return np.array(axis, dtype=np.float64, copy=True)


def _logical_index(value, name):
    message = f"{name} must be a finite scalar logical index."
    try:
        raw = np.asarray(value)
        if raw.ndim != 0 or np.iscomplexobj(raw):
            raise ValueError(message)
        index = float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(message) from exc
    if not np.isfinite(index):
        raise ValueError(message)
    return index


def _target_spacing(value):
    message = "Target spacing must be a finite positive scalar."
    try:
        raw = np.asarray(value)
        if raw.ndim != 0 or np.iscomplexobj(raw):
            raise ValueError(message)
        target = float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(message) from exc
    if not np.isfinite(target) or target <= 0.0:
        raise ValueError(message)
    return target


def calibrate_energy_axis(
    energy,
    reference_index,
    second_index,
    target_spacing,
    *,
    unit="",
):
    """Regenerate a uniform energy axis from two logical sample positions."""
    axis = _coordinate_axis(energy)
    reference = _logical_index(reference_index, "reference_index")
    second = _logical_index(second_index, "second_index")

    for name, index in (("reference_index", reference), ("second_index", second)):
        if not 0.0 <= index <= axis.size - 1:
            raise ValueError(f"{name} is outside the energy axis.")
    if reference == second:
        raise ValueError("Calibration indices must be distinct.")

    target = _target_spacing(target_spacing)
    if unit is None:
        unit = ""
    if not isinstance(unit, str):
        raise ValueError("Energy unit must be a string or None.")

    source = float(second - reference)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore", under="ignore"):
        scale = target / source
        calibrated = (np.arange(axis.size, dtype=np.float64) - reference) * target / source
    if not np.isfinite(scale) or scale == 0.0:
        raise ValueError("Calibration scale must be finite and non-zero.")
    if not np.isfinite(calibrated).all():
        raise ValueError("Calibrated energy coordinates must be finite.")

    calibrated.setflags(write=False)
    return EnergyCalibrationResult(
        energy=calibrated,
        source_spacing=source,
        target_spacing=target,
        scale=float(scale),
        reference_index=reference,
        second_index=second,
        unit=unit,
    )
