# -*- coding: utf-8 -*-
"""平带增强的数学部分：高斯倍率、参数校验与预设序列化。

透明度模型（计划 §3）：

.. code-block:: text

    G_i(E) = exp[-4 ln(2) ((E - E_i) / w_i)^2]
    m(E)   = b + max_i a_i G_i(E)

``w_i`` 是高斯半高全宽 FWHM，``b`` 是背景保留比例，``a_i`` 是第 i 条的增强量。
用最大值而不是求和：重叠的条目不会因为相加而在交叠处异常增厚。没有任何条目
启用时 :func:`multiplier_for` 返回 ``None``，宿主据此走原有渲染路径，不单独
淡化整幅背景。

本模块只用 NumPy，不导入 Qt / VTK，便于在无图形环境下单独验收。
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

#: 参数 schema 版本；预设 JSON 里记录，导入时据此判断是否需要迁移。
SCHEMA_VERSION = 1

#: 建议起始值：背景保留 20%，各条额外增强量 0.8（分离良好的峰中心倍率 = 1）。
DEFAULT_BACKGROUND = 0.20
DEFAULT_GAIN = 0.80

#: 第一版增强量范围；视觉验收时再决定是否需要放宽。
MIN_GAIN = 0.0
MAX_GAIN = 5.0

#: 厚度用高斯 FWHM 表示，与能量中心同单位。下限按采样间距给，避免出现比一个
#: 采样点还窄、屏幕上不可见的高斯。
MIN_FWHM_SAMPLES = 0.5

#: 新增条目的厚度初值 = 采样间距 × 该倍数。这只是可修改的建议，不是测得的线宽。
SUGGESTED_FWHM_SAMPLES = 4.0

#: 条目数量上限：够用又不至于让面板和计算量失控。
MAX_BANDS = 32

#: 倍率的硬上限，防止越界参数把宿主的不透明度推到无意义的大数。
MAX_MULTIPLIER = 1.0 + MAX_GAIN

_FWHM_FACTOR = 4.0 * math.log(2.0)


def gaussian(values, center: float, fwhm: float):
    """FWHM 参数化的单位高斯；中心处为 1，``center ± fwhm/2`` 处为 0.5。

    退化输入（宽度为零/负数/非有限、中心非有限）一律返回全零，而不是抛异常或
    产生 NaN：调用点包括 Qt 槽，任何异常都会变成崩溃。
    """
    axis = np.asarray(values, dtype=np.float64)
    width = _finite(fwhm)
    origin = _finite(center)
    if width is None or width <= 0.0 or origin is None:
        return np.zeros(axis.shape, dtype=np.float64)
    return np.exp(-_FWHM_FACTOR * ((axis - origin) / width) ** 2)


def _finite(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass
class FlatBand:
    """一条用户添加的平带。参数为空表示还没填写，此时不参与计算。"""

    band_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    enabled: bool = True
    center: Optional[float] = None
    fwhm: Optional[float] = None
    gain: float = DEFAULT_GAIN

    # -- 校验 -----------------------------------------------------------
    def invalid_reason(self) -> Optional[str]:
        """返回不能参与计算的原因；``None`` 表示参数有效。"""
        if not self.enabled:
            return "未启用"
        if _finite(self.center) is None:
            return "请填写有效的能量中心"
        width = _finite(self.fwhm)
        if width is None or width <= 0.0:
            return "厚度必须是正数"
        gain = _finite(self.gain)
        if gain is None or gain < 0.0:
            return "增强量不能为负数"
        return None

    @property
    def active(self) -> bool:
        return self.invalid_reason() is None

    @property
    def normalized_gain(self) -> float:
        gain = _finite(self.gain)
        if gain is None:
            return 0.0
        return max(MIN_GAIN, min(MAX_GAIN, gain))

    # -- 序列化 ---------------------------------------------------------
    def to_state(self) -> Dict[str, Any]:
        return {
            "id": str(self.band_id),
            "enabled": bool(self.enabled),
            "center": None if self.center is None else float(self.center),
            "fwhm": None if self.fwhm is None else float(self.fwhm),
            "gain": float(self.normalized_gain),
        }

    @classmethod
    def from_state(cls, payload: Mapping[str, Any]) -> "FlatBand":
        payload = payload or {}
        raw_id = str(payload.get("id") or "").strip()
        band = cls(band_id=raw_id or uuid.uuid4().hex[:12])
        band.enabled = bool(payload.get("enabled", True))
        band.center = _finite(payload.get("center"))
        band.fwhm = _finite(payload.get("fwhm"))
        gain = _finite(payload.get("gain"))
        band.gain = DEFAULT_GAIN if gain is None else max(MIN_GAIN, min(MAX_GAIN, gain))
        return band


def suggested_fwhm(axis) -> float:
    """按能量采样间距给出的厚度初值（可修改，不当作测量到的线宽）。"""
    spacing = float(axis.sample_spacing()) if axis is not None else 0.0
    if not math.isfinite(spacing) or spacing <= 0.0:
        return 0.0
    return spacing * SUGGESTED_FWHM_SAMPLES


def minimum_fwhm(axis) -> float:
    spacing = float(axis.sample_spacing()) if axis is not None else 0.0
    if not math.isfinite(spacing) or spacing <= 0.0:
        return 0.0
    return spacing * MIN_FWHM_SAMPLES


def multiplier_for(
    bands: Iterable[FlatBand],
    values,
    background: float = DEFAULT_BACKGROUND,
):
    """按能量轴算出一维倍率；没有任何有效条目时返回 ``None``。"""
    axis = np.asarray(values, dtype=np.float64)
    level = _finite(background)
    level = 0.0 if level is None else max(0.0, min(1.0, level))

    result = np.full(axis.shape, level, dtype=np.float64)
    touched = False
    for band in bands or ():
        if not band.active:
            continue
        touched = True
        gain = band.normalized_gain
        if gain <= 0.0:
            # 增强量为 0 的条目只保留背景水平，但依然算“参与了效果”。
            continue
        profile = level + gain * gaussian(axis, band.center, band.fwhm)
        np.maximum(result, profile, out=result)
    if not touched:
        return None
    np.clip(result, 0.0, MAX_MULTIPLIER, out=result)
    return result


@dataclass
class EffectState:
    """面板的完整参数快照：总开关 + 背景 + 可变长度的条目列表。"""

    enabled: bool = True
    background: float = DEFAULT_BACKGROUND
    bands: List[FlatBand] = field(default_factory=list)
    schema: int = SCHEMA_VERSION

    # -- 序列化 ---------------------------------------------------------
    def to_state(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "enabled": bool(self.enabled),
            "background": float(self.background),
            "bands": [band.to_state() for band in self.bands],
        }

    @classmethod
    def from_state(cls, payload: Optional[Mapping[str, Any]]) -> "EffectState":
        payload = payload or {}
        state = cls()
        state.enabled = bool(payload.get("enabled", True))
        level = _finite(payload.get("background"))
        state.background = (
            DEFAULT_BACKGROUND
            if level is None
            else max(0.0, min(1.0, level))
        )
        raw_bands = payload.get("bands") or []
        if isinstance(raw_bands, (list, tuple)):
            state.bands = [
                FlatBand.from_state(item)
                for item in raw_bands[:MAX_BANDS]
                if isinstance(item, Mapping)
            ]
        return state

    def to_preset(self, axis=None) -> Dict[str, Any]:
        """带单位信息的预设，供导入导出；单位只记录，不做换算。"""
        payload = self.to_state()
        payload["plugin"] = "flat_band_opacity"
        payload["energy"] = {
            "source": getattr(axis, "source", "index") if axis else "index",
            "unit": getattr(axis, "unit", None) if axis else None,
        }
        return payload

    def reset_display_parameters(self, axis=None) -> None:
        """恢复背景、增强量与厚度的建议值；保留数量与峰位。"""
        self.background = DEFAULT_BACKGROUND
        suggested = suggested_fwhm(axis)
        for band in self.bands:
            band.gain = DEFAULT_GAIN
            band.fwhm = suggested if suggested > 0.0 else band.fwhm

    def new_band(self, axis=None) -> FlatBand:
        band = FlatBand()
        suggested = suggested_fwhm(axis)
        band.fwhm = suggested if suggested > 0.0 else None
        band.gain = DEFAULT_GAIN
        return band


def preset_unit_mismatch(state_preset: Mapping[str, Any], axis) -> Optional[str]:
    """导入预设时比较单位与坐标来源；返回提示文本，一致时返回 ``None``。

    预设里没有记录坐标信息时不提示——没有可比对的依据，不是不一致。
    """
    recorded = (state_preset or {}).get("energy")
    if not isinstance(recorded, Mapping):
        return None
    current_source = getattr(axis, "source", "index") if axis else "index"
    current_unit = getattr(axis, "unit", None) if axis else None
    recorded_source = recorded.get("source")
    recorded_unit = recorded.get("unit")

    if recorded_source and recorded_source != current_source:
        return (
            f"预设记录的是 {recorded_source} 坐标，当前显示的是 {current_source}；"
            "峰位按原样保留，不做换算。"
        )
    if (recorded_unit or None) != (current_unit or None):
        left = recorded_unit or "无单位"
        right = current_unit or "无单位"
        return f"预设记录的单位是 {left}，当前是 {right}；峰位按原样保留，不做换算。"
    return None
