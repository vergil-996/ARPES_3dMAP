# -*- coding: utf-8 -*-
"""Blender 数据体外观契约与可独立验证的体积系数数值参考。"""
from __future__ import annotations

import math
from typing import Mapping

import numpy as np

VOLUME_MODE_EMISSION = "emission"
VOLUME_MODE_HYBRID_LIT = "hybrid_lit"
VOLUME_MODES = (VOLUME_MODE_EMISSION, VOLUME_MODE_HYBRID_LIT)
VOLUME_MODE_LABELS = {
    VOLUME_MODE_EMISSION: "自发光",
    VOLUME_MODE_HYBRID_LIT: "柔和受光",
}
VOLUME_PARAMETER_DEFAULTS = {
    VOLUME_MODE_EMISSION: {
        "density_multiplier": 1.0,
        "color_adjustment": (1.0, 1.0, 1.0),
        "scattering_amount": 0.0,
        "emission_compensation": 1.0,
        "anisotropy": 0.0,
    },
    VOLUME_MODE_HYBRID_LIT: {
        "density_multiplier": 1.0,
        "color_adjustment": (1.0, 1.0, 1.0),
        "scattering_amount": 0.65,
        "emission_compensation": 0.15,
        "anisotropy": 0.0,
    },
}


class BlenderVolumeError(ValueError):
    pass


def resolve_volume_mode(value) -> str:
    mode = str(value or VOLUME_MODE_EMISSION)
    if mode not in VOLUME_MODES:
        raise BlenderVolumeError(f"未知数据体外观模式：{mode!r}。")
    return mode


def resolve_volume_shading(mode=VOLUME_MODE_EMISSION, parameters: Mapping | None = None):
    mode = resolve_volume_mode(mode)
    requested = dict(parameters or {})
    known = set(VOLUME_PARAMETER_DEFAULTS[mode])
    unknown = set(requested).difference(known)
    if unknown:
        raise BlenderVolumeError(f"数据体外观含未知参数：{sorted(unknown)}")
    resolved = dict(VOLUME_PARAMETER_DEFAULTS[mode])
    resolved.update(requested)
    for name in ("density_multiplier", "scattering_amount", "emission_compensation", "anisotropy"):
        try:
            value = float(resolved[name])
        except (TypeError, ValueError) as exc:
            raise BlenderVolumeError(f"数据体参数 {name} 必须是有限数值。") from exc
        if not math.isfinite(value):
            raise BlenderVolumeError(f"数据体参数 {name} 必须是有限数值。")
        resolved[name] = value
    if resolved["density_multiplier"] < 0.0:
        raise BlenderVolumeError("Density multiplier 不能为负数。")
    if not 0.0 <= resolved["scattering_amount"] <= 1.0:
        raise BlenderVolumeError("Scattering amount 必须在 0–1 范围内。")
    if resolved["emission_compensation"] < 0.0:
        raise BlenderVolumeError("Emission compensation 不能为负数。")
    if not -1.0 <= resolved["anisotropy"] <= 1.0:
        raise BlenderVolumeError("Anisotropy 必须在 -1–1 范围内。")
    color = resolved["color_adjustment"]
    if isinstance(color, (str, bytes)):
        raise BlenderVolumeError("Color adjustment 必须是三个非负有限数值。")
    try:
        values = tuple(float(value) for value in color)
    except (TypeError, ValueError) as exc:
        raise BlenderVolumeError("Color adjustment 必须是三个非负有限数值。") from exc
    if len(values) != 3 or not all(math.isfinite(value) and value >= 0.0 for value in values):
        raise BlenderVolumeError("Color adjustment 必须是三个非负有限数值。")
    resolved["color_adjustment"] = values
    return {
        "mode": mode,
        "label": VOLUME_MODE_LABELS[mode],
        "parameters": resolved,
    }


def volume_shading_coefficients(density, color, mode=VOLUME_MODE_EMISSION, parameters=None):
    """返回吸收、散射、发射系数；混合模式逐通道保持总消光不变。"""
    shading = resolve_volume_shading(mode, parameters)
    settings = shading["parameters"]
    sigma = np.asarray(density, dtype=np.float64)
    rgb = np.asarray(color, dtype=np.float64)
    if rgb.shape != sigma.shape + (3,):
        raise BlenderVolumeError("颜色数组必须是密度数组形状再加三个 RGB 通道。")
    sigma = sigma * settings["density_multiplier"]
    adjusted = rgb * np.asarray(settings["color_adjustment"], dtype=np.float64)
    if shading["mode"] == VOLUME_MODE_HYBRID_LIT:
        omega = settings["scattering_amount"] * np.clip(adjusted, 0.0, 1.0)
        scatter = sigma[..., None] * omega
        absorption = sigma[..., None] * (1.0 - omega)
    else:
        scatter = np.zeros(sigma.shape + (3,), dtype=np.float64)
        absorption = np.broadcast_to(sigma[..., None], sigma.shape + (3,)).copy()
    emission = (
        settings["emission_compensation"] * sigma[..., None] * adjusted
    )
    return {
        "sigma": sigma,
        "absorption": absorption,
        "scatter": scatter,
        "emission": emission,
    }
