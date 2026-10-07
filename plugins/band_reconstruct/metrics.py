# -*- coding: utf-8 -*-
"""验收指标（论文 eq. 9 / 10）。

- ``eta_avg``：``sqrt(mean((E_recon − E_truth)²))``，单位与能量轴相同（测试里用
  eV，报告里换算成 meV）；
- ``eta_rel``：``‖E_recon − E_truth‖₂ / ‖E_truth‖₂``，无量纲。

约定：任一侧为 NaN 的点表示「无解/无真值」，默认从统计中剔除（不算 0）；真正
参与统计的点数通过 :func:`compare_surfaces` 一并返回，避免"剔除后只剩两个点"
还被当成有效结论。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import numpy as np

__all__ = [
    "SurfaceComparison",
    "compare_surfaces",
    "eta_avg",
    "eta_rel",
]


def _pair(recon, truth, mask=None):
    """对齐两个面并返回 (残差, 有效掩膜)。"""
    recon_values = np.asarray(recon, dtype=np.float64)
    truth_values = np.asarray(truth, dtype=np.float64)
    if recon_values.shape != truth_values.shape:
        raise ValueError(
            f"重构面 {recon_values.shape} 与真值面 {truth_values.shape} 形状不一致。"
        )
    residual = recon_values - truth_values
    valid = np.isfinite(residual)
    if mask is not None:
        mask_values = np.asarray(mask, dtype=bool)
        if mask_values.shape != recon_values.shape:
            raise ValueError(
                f"掩膜 {mask_values.shape} 与面 {recon_values.shape} 形状不一致。"
            )
        valid &= mask_values
    return residual, valid


def eta_avg(recon, truth, *, mask=None) -> float:
    """均方根误差；没有有效点时返回 ``nan``。"""
    residual, valid = _pair(recon, truth, mask)
    if not np.any(valid):
        return float("nan")
    return float(np.sqrt(np.mean(residual[valid] ** 2)))


def eta_rel(recon, truth, *, mask=None) -> float:
    """相对 L2 误差；真值范数为 0 或没有有效点时返回 ``nan``。"""
    residual, valid = _pair(recon, truth, mask)
    if not np.any(valid):
        return float("nan")
    truth_norm = float(np.linalg.norm(np.asarray(truth, dtype=np.float64)[valid]))
    if truth_norm == 0.0:
        return float("nan")
    return float(np.linalg.norm(residual[valid]) / truth_norm)


@dataclass(frozen=True)
class SurfaceComparison:
    """一次重构面与真值面的对比结果。"""

    eta_avg: float
    eta_rel: float
    max_abs_error: float
    mean_error: float
    valid_points: int
    total_points: int

    @property
    def coverage(self) -> float:
        """参与统计的点占比；低于 1 说明存在 NaN（无解或无真值）。"""
        if self.total_points <= 0:
            return 0.0
        return self.valid_points / float(self.total_points)

    def to_dict(self, *, unit_scale: float = 1.0, unit: str = "") -> Dict[str, Any]:
        """可序列化摘要；``unit_scale`` 用于换算到报告单位（如 eV→meV 取 1000）。"""
        return {
            "eta_avg": self.eta_avg * unit_scale,
            "eta_rel": self.eta_rel,
            "max_abs_error": self.max_abs_error * unit_scale,
            "mean_error": self.mean_error * unit_scale,
            "valid_points": self.valid_points,
            "total_points": self.total_points,
            "coverage": self.coverage,
            "unit": unit,
        }


def compare_surfaces(recon, truth, *, mask=None) -> SurfaceComparison:
    """一次性算出全部误差指标；NaN 点剔除、无有效点时各指标为 NaN。"""
    residual, valid = _pair(recon, truth, mask)
    total = int(residual.size)
    count = int(np.count_nonzero(valid))
    if count == 0:
        return SurfaceComparison(
            eta_avg=float("nan"),
            eta_rel=float("nan"),
            max_abs_error=float("nan"),
            mean_error=float("nan"),
            valid_points=0,
            total_points=total,
        )
    values = residual[valid]
    truth_values = np.asarray(truth, dtype=np.float64)[valid]
    truth_norm = float(np.linalg.norm(truth_values))
    return SurfaceComparison(
        eta_avg=float(np.sqrt(np.mean(values**2))),
        eta_rel=float(np.linalg.norm(values) / truth_norm) if truth_norm else float("nan"),
        max_abs_error=float(np.max(np.abs(values))),
        mean_error=float(np.mean(values)),
        valid_points=count,
        total_points=total,
    )
