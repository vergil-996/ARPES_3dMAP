# -*- coding: utf-8 -*-
"""MRF 能量项：三线性取样 + 负对数后验与梯度（向量化）。

论文 eq. 4 的负对数后验（每条带一张能量面 Ẽ）：

.. math::

    L = -\\sum_{ij} \\log I(kx_i, ky_j, \\tilde{E}_{ij})
        + \\sum_{(ij),(lm) \\in \\text{4-邻域}} \\frac{(\\tilde{E}_{ij} - \\tilde{E}_{lm})^2}{2\\eta^2}

- 强度项用**三线性插值**在 ``(kx_i, ky_j, Ẽ_ij)`` 处取值：动量两维取体素分数
  索引（初始化面允许在动量方向缩放，所以保留分数索引），能量维按**物理坐标**
  插值，自动适配非均匀、甚至递减的能量轴；
- ``log`` 前把强度截断到 ``floor``（默认 ``1e-6 × 峰值``），保证可微与数值稳定；
  低于 ``floor`` 的体素梯度为 0（该处没有可用的强度信息）；
- 平滑项按 4-邻域成对求和，梯度即邻接差分之和 ``/ η²``，不做近似的拉普拉斯
  边界处理——边界点的邻域对数更少，梯度也相应更小。

所有量都是纯 NumPy，没有状态、没有随机性。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

__all__ = [
    "BandProblem",
    "EnergyTerms",
    "TrilinearSampler",
    "default_floor",
]


def default_floor(volume) -> float:
    """损失下限的默认值：``1e-6 × 体数据峰值``（峰值为 0 时退化为 1e-6）。"""
    values = np.asarray(volume, dtype=np.float64)
    peak = float(np.max(values)) if values.size else 0.0
    if not np.isfinite(peak) or peak <= 0.0:
        return 1e-6
    return 1e-6 * peak


class TrilinearSampler:
    """在 ``[X, Y, E]`` 体数据上做三线性取样并给出能量方向导数。

    - 动量两维（前两维）用**体素分数索引**：``fi`` / ``fj`` 可以取小数（初始化
      面在动量方向缩放时就落在非整点）；越界夹到边缘；
    - 能量维用**物理坐标** ``fe``：内部用 ``searchsorted`` 定位相邻两个能量采样
      点并按各自间距插值，因此支持非均匀轴；递减轴在构造时翻转一次，对外语义
      不变。
    """

    def __init__(self, volume, e_values):
        values = np.asarray(volume, dtype=np.float64)
        if values.ndim != 3:
            raise ValueError("体数据必须是三维数组 [X, Y, E]。")
        energy = np.asarray(e_values, dtype=np.float64).reshape(-1)
        if values.shape[2] != energy.size:
            raise ValueError(
                f"能量轴长度 {energy.size} 与体数据能量维 {values.shape[2]} 不一致。"
            )
        if energy.size < 2:
            raise ValueError("能量轴至少需要两个采样点。")
        if not np.all(np.isfinite(values)):
            raise ValueError("体数据含非有限值；请先做预处理（清理/平滑/MCLAHE）。")
        diffs = np.diff(energy)
        if not (np.all(diffs > 0) or np.all(diffs < 0)):
            raise ValueError("能量轴必须严格单调（全升或全降）。")
        if np.all(diffs < 0):
            energy = energy[::-1].copy()
            values = np.ascontiguousarray(values[:, :, ::-1])
        self.volume = values
        self.e_values = energy
        self.shape = values.shape

    def sample(self, fi, fj, fe) -> Tuple[np.ndarray, np.ndarray]:
        """返回 ``(I, dI/dE)``；``fe`` 超出能量轴时夹到端点的值，导数为端点上的一阶差商。"""
        values = self.volume
        nx, ny, ne = self.shape

        fi = np.asarray(fi, dtype=np.float64)
        fj = np.asarray(fj, dtype=np.float64)
        fe = np.asarray(fe, dtype=np.float64)

        i0 = np.clip(np.floor(fi), 0.0, nx - 1.0).astype(np.intp)
        i1 = np.minimum(i0 + 1, nx - 1)
        wi = np.clip(fi - i0, 0.0, 1.0)

        j0 = np.clip(np.floor(fj), 0.0, ny - 1.0).astype(np.intp)
        j1 = np.minimum(j0 + 1, ny - 1)
        wj = np.clip(fj - j0, 0.0, 1.0)

        energy = self.e_values
        k1 = np.clip(np.searchsorted(energy, fe, side="right"), 1, ne - 1).astype(np.intp)
        k0 = k1 - 1
        span = energy[k1] - energy[k0]
        wk = np.clip((fe - energy[k0]) / span, 0.0, 1.0)

        v000 = values[i0, j0, k0]
        v001 = values[i0, j0, k1]
        v010 = values[i0, j1, k0]
        v011 = values[i0, j1, k1]
        v100 = values[i1, j0, k0]
        v101 = values[i1, j0, k1]
        v110 = values[i1, j1, k0]
        v111 = values[i1, j1, k1]

        low = v000 + wk * (v001 - v000)
        high = v010 + wk * (v011 - v010)
        interp_j = low + wj * (high - low)
        low_i = v100 + wk * (v101 - v100)
        high_i = v110 + wk * (v111 - v110)
        interp_ji = low_i + wj * (high_i - low_i)
        value = interp_j + wi * (interp_ji - interp_j)

        slope00 = (v001 - v000) / span
        slope01 = (v011 - v010) / span
        slope10 = (v101 - v100) / span
        slope11 = (v111 - v110) / span
        slope_j = slope00 + wj * (slope01 - slope00)
        slope_ji = slope10 + wj * (slope11 - slope10)
        slope = slope_j + wi * (slope_ji - slope_j)
        return value, slope


@dataclass(frozen=True)
class EnergyTerms:
    """一次能量评估的分解结果，便于诊断哪一项在起作用。"""

    total: float
    intensity: float
    smoothness: float
    gradient: np.ndarray

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "intensity": self.intensity,
            "smoothness": self.smoothness,
        }


class BandProblem:
    """一条带的重构问题：体数据与网格固定，能量面是唯一变量。

    :param volume: 预处理后的 ``[X, Y, E]`` 强度体（有限、非负）
    :param e_values: 能量轴物理坐标（严格单调）
    :param eta: 平滑先验强度，单位与能量轴相同（fuller 默认 ``0.1``）
    :param floor: 强度截断下限；``None`` 时取 :func:`default_floor`
    """

    def __init__(self, volume, e_values, *, eta: float = 0.1, floor: Optional[float] = None):
        self.sampler = TrilinearSampler(volume, e_values)
        self.eta = float(eta)
        if self.eta <= 0.0:
            raise ValueError("eta 必须为正数。")
        self.floor = float(default_floor(volume) if floor is None else floor)
        if self.floor <= 0.0:
            raise ValueError("floor 必须为正数。")
        self.shape = self.sampler.shape[:2]
        #: 动量维分数索引（整数网格点）；初始化面在动量方向缩放时按同一网格取值。
        self.i_index, self.j_index = np.meshgrid(
            np.arange(self.shape[0], dtype=np.float64),
            np.arange(self.shape[1], dtype=np.float64),
            indexing="ij",
        )

    # -- 能量 ---------------------------------------------------------------
    def sample_intensity(self, surface) -> Tuple[np.ndarray, np.ndarray]:
        """在当前能量面上取 ``(I, ∂I/∂E)``。"""
        values = np.asarray(surface, dtype=np.float64).reshape(self.shape)
        return self.sampler.sample(self.i_index, self.j_index, values)

    def energy_terms(self, surface) -> EnergyTerms:
        """完整的 ``(损失, 梯度)``：强度项 + 平滑项。"""
        values = np.asarray(surface, dtype=np.float64).reshape(self.shape)
        intensity, slope = self.sampler.sample(self.i_index, self.j_index, values)

        clipped = np.maximum(intensity, self.floor)
        loss_intensity = float(-np.sum(np.log(clipped)))
        active = intensity > self.floor
        grad_intensity = np.where(active, -slope / clipped, 0.0)

        diff_x = values[1:, :] - values[:-1, :]
        diff_y = values[:, 1:] - values[:, :-1]
        loss_smooth = float((np.sum(diff_x**2) + np.sum(diff_y**2)) / (2.0 * self.eta**2))

        grad_smooth = np.zeros_like(values)
        contribution = diff_x / self.eta**2
        grad_smooth[1:, :] += contribution
        grad_smooth[:-1, :] -= contribution
        contribution = diff_y / self.eta**2
        grad_smooth[:, 1:] += contribution
        grad_smooth[:, :-1] -= contribution

        total = loss_intensity + loss_smooth
        return EnergyTerms(
            total=total,
            intensity=loss_intensity,
            smoothness=loss_smooth,
            gradient=grad_intensity + grad_smooth,
        )

    def energy(self, surface) -> Tuple[float, np.ndarray]:
        """``(损失, 梯度)`` 快捷入口（供 ``scipy.optimize`` 直接用）。"""
        terms = self.energy_terms(surface)
        return terms.total, terms.gradient

    def energy_bounds(self) -> Tuple[float, float]:
        """能量轴量程，作为优化变量的默认上下界。"""
        energy = self.sampler.e_values
        return float(energy[0]), float(energy[-1])
