# -*- coding: utf-8 -*-
"""合成数据生成：已知色散面 → 三维强度体。

生成模型（与算法规格一致）：

.. math::

    I(kx, ky, E) = b(E) + \\sum_b A_b \\exp\\left(-\\frac{(E - E_b(kx, ky))^2}{2\\sigma_E^2}\\right)

- ``b(E)``：常数背景 + 线性斜坡（``background`` / ``background_slope``），模拟
  真实的非弹性背景；
- 每条带一个高斯 EDC 峰，峰位沿该带的真值色散面；``amplitude`` 可以制造强度
  失衡（弱带）；
- 噪声可选泊松（光子计数，``noise="poisson"``，``noise_level`` 为满强度处的
  光子数）或高斯（``noise="gaussian"``，``noise_level`` 为满强度的比例），
  随机数固定种子，结果可复现。

能量间隔默认 18 meV（与论文验收条件一致），也允许直接给能量轴。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "DEFAULT_ENERGY_STEP",
    "SyntheticBand",
    "SyntheticDataset",
    "gaussian_edc_volume",
]

#: 默认能量间隔（eV），与论文的 18 meV 一致。
DEFAULT_ENERGY_STEP = 0.018

#: 噪声类型。
NOISE_NONE = "none"
NOISE_POISSON = "poisson"
NOISE_GAUSSIAN = "gaussian"
NOISE_CHOICES = (NOISE_NONE, NOISE_POISSON, NOISE_GAUSSIAN)


@dataclass(frozen=True)
class SyntheticBand:
    """一条合成带：真值色散面 + 强度。"""

    label: str
    surface: np.ndarray
    amplitude: float = 1.0

    @property
    def shape(self) -> Tuple[int, int]:
        return (int(self.surface.shape[0]), int(self.surface.shape[1]))


@dataclass
class SyntheticDataset:
    """合成体数据 + 全部真值与生成参数。"""

    volume: np.ndarray
    x: np.ndarray
    y: np.ndarray
    e: np.ndarray
    bands: Tuple[SyntheticBand, ...]
    clean: np.ndarray
    sigma_e: float
    background: float
    background_slope: float
    noise: str
    noise_level: float
    seed: Optional[int]
    params: Dict[str, Any] = field(default_factory=dict)

    @property
    def shape(self) -> Tuple[int, int, int]:
        return (
            int(self.volume.shape[0]),
            int(self.volume.shape[1]),
            int(self.volume.shape[2]),
        )

    def truth(self, index: int) -> np.ndarray:
        """第 ``index`` 条带的真值色散面（副本）。"""
        return np.array(self.bands[index].surface, dtype=np.float64)

    def truth_stack(self) -> np.ndarray:
        """全部真值面堆叠成 ``[band, i, j]``。"""
        return np.stack([band.surface for band in self.bands], axis=0)

    def describe(self) -> str:
        labels = "、".join(band.label for band in self.bands)
        parts = [
            f"{self.shape[0]}×{self.shape[1]}×{self.shape[2]}",
            f"{len(self.bands)} 带（{labels}）",
            f"σ_E={self.sigma_e * 1000:.0f} meV",
            f"背景={self.background:g}",
            f"噪声={self.noise}",
        ]
        if self.seed is not None:
            parts.append(f"seed={self.seed}")
        return " · ".join(parts)


def gaussian_edc_volume(
    x,
    y,
    e,
    bands: Sequence[SyntheticBand],
    *,
    sigma_e: float = 0.03,
    background: float = 1e-3,
    background_slope: float = 0.0,
    noise: str = NOISE_POISSON,
    noise_level: Optional[float] = None,
    seed: Optional[int] = 0,
) -> SyntheticDataset:
    """按高斯 EDC 模型生成 ``[X, Y, E]`` 合成体数据。

    :param x, y: 两个动量轴的物理坐标（严格单调）
    :param e: 能量轴物理坐标（严格单调）；也可以只给 ``(起点, 步长, 点数)`` 三元组
    :param bands: :class:`SyntheticBand` 序列，面的形状必须是 ``(len(x), len(y))``
    :param sigma_e: EDC 高斯宽度（eV）
    :param background: 常数背景强度
    :param background_slope: 背景随能量的线性斜率（每 eV）
    :param noise: ``"none"`` / ``"poisson"`` / ``"gaussian"``
    :param noise_level: 泊松模型下是满强度处的光子数（默认 1e4）；高斯模型下是
        噪声标准差占满强度的比例（默认 0.01）
    :param seed: 随机种子；``None`` 表示不固定
    """
    x_axis = np.asarray(x, dtype=np.float64).reshape(-1)
    y_axis = np.asarray(y, dtype=np.float64).reshape(-1)
    if x_axis.size < 2 or y_axis.size < 2:
        raise ValueError("两个动量轴都至少需要两个采样点。")
    e_axis = _energy_axis(e)
    if sigma_e <= 0.0:
        raise ValueError("sigma_e 必须为正数。")
    kind = str(noise or NOISE_NONE).lower()
    if kind not in NOISE_CHOICES:
        raise ValueError(f"未知的噪声类型：{noise!r}（可选 {NOISE_CHOICES}）。")
    if not bands:
        raise ValueError("至少需要一条带。")

    levels = []
    for position, band in enumerate(bands):
        surface = np.asarray(band.surface, dtype=np.float64)
        if surface.shape != (x_axis.size, y_axis.size):
            raise ValueError(
                f"第 {position + 1} 条带的真值面形状 {surface.shape} 与动量网格 "
                f"{(x_axis.size, y_axis.size)} 不一致。"
            )
        levels.append(np.asarray(band.amplitude, dtype=np.float64) * np.exp(
            -((e_axis[:, None, None] - surface[None, :, :]) ** 2) / (2.0 * sigma_e**2)
        ))

    clean = np.zeros((x_axis.size, y_axis.size, e_axis.size), dtype=np.float64)
    for level in levels:
        clean += np.transpose(level, (1, 2, 0))
    slope = float(background_slope) * (e_axis - float(e_axis[0]))
    baseline = np.maximum(float(background) + slope, 0.0)
    clean += baseline[None, None, :]

    if kind == NOISE_NONE:
        volume = clean.copy()
        level_value = 0.0
    else:
        rng = np.random.default_rng(seed)
        if kind == NOISE_POISSON:
            level_value = float(noise_level) if noise_level is not None else 1e4
            if level_value <= 0.0:
                raise ValueError("泊松噪声的光子数必须为正数。")
            counts = rng.poisson(clean * level_value)
            volume = counts / level_value
        else:
            level_value = float(noise_level) if noise_level is not None else 0.01
            peak = float(np.max(clean)) if clean.size else 0.0
            volume = np.maximum(
                clean + rng.normal(0.0, level_value * peak, size=clean.shape), 0.0
            )

    return SyntheticDataset(
        volume=np.asarray(volume, dtype=np.float32),
        clean=np.asarray(clean, dtype=np.float32),
        x=x_axis.copy(),
        y=y_axis.copy(),
        e=e_axis.copy(),
        bands=tuple(
            SyntheticBand(label=str(band.label or f"Band {index + 1}"), surface=np.array(band.surface, dtype=np.float64), amplitude=float(band.amplitude))
            for index, band in enumerate(bands)
        ),
        sigma_e=float(sigma_e),
        background=float(background),
        background_slope=float(background_slope),
        noise=kind,
        noise_level=float(level_value),
        seed=seed,
        params={
            "energy_step": float(np.median(np.abs(np.diff(e_axis)))) if e_axis.size > 1 else 0.0,
            "amplitudes": [float(band.amplitude) for band in bands],
            "labels": [str(band.label or f"Band {index + 1}") for index, band in enumerate(bands)],
        },
    )


def _energy_axis(e) -> np.ndarray:
    """能量轴：一维坐标数组，或 ``(起点, 步长, 点数)`` 三元组（**元组**才按三元组解释）。"""
    if isinstance(e, tuple) and len(e) == 3:
        start, step, count = (float(e[0]), float(e[1]), int(e[2]))
        if count < 2 or step == 0.0:
            raise ValueError("能量轴三元组需要 (起点, 非零步长, ≥2 的点数)。")
        return start + step * np.arange(count, dtype=np.float64)
    axis = np.asarray(e, dtype=np.float64).reshape(-1)
    if axis.size < 2:
        raise ValueError("能量轴至少需要两个采样点。")
    if not np.all(np.isfinite(axis)):
        raise ValueError("能量轴含非有限值。")
    if not (np.all(np.diff(axis) > 0) or np.all(np.diff(axis) < 0)):
        raise ValueError("能量轴必须严格单调。")
    return axis
