# -*- coding: utf-8 -*-
"""宿主工作函数：一次三维快照 → 一批能带面。

这是**工作线程**里跑的全部内容：只用 NumPy/SciPy 与算法模块，不碰 Qt、不碰主
窗口，也不改写快照（宿主给的 ``volume`` 就是只读缓冲区）。流程：

1. 可选地按能量窗口裁剪快照（面板给的是物理坐标区间）；
2. 预处理（高斯平滑 + MCLAHE，可分别关闭）；
3. 逐带构造初始化面并施加对齐参数（动量缩放、能量平移）；
4. 逐带顺序重构，期间用 ``cancel.report_progress`` 回传进度；
5. 组装 :class:`AnalysisSurface2D` 交回宿主。

进度约定：预处理占 0–15%，重构占 15–100%，按带均分。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from bandscope.extensions.api import (
    AnalysisCancelled,
    AnalysisSurface2D,
    BandSurface,
    CancelToken,
)

from . import init_surface as IS
from .optimize import reconstruct_bands
from .preprocess import DEFAULT_MCLAHE_PARAMS, DEFAULT_SIGMA, preprocess_volume

__all__ = [
    "DEFAULT_BAND_COLORS",
    "band_defaults",
    "build_initial_surface",
    "run_reconstruction",
    "surface_params_schema",
]

#: 逐带默认颜色（阶段 3 的叠加层直接用；面板上只读展示）。
DEFAULT_BAND_COLORS = (
    "#ff8a3d",
    "#4dd2ff",
    "#9d7bff",
    "#ffd166",
    "#5ddc7a",
    "#ff6b9a",
    "#b0b7c3",
    "#7fd1c1",
)

#: 重构的能量窗口默认留白：避免把面推到能量轴边界上。
_PREPROCESS_SHARE = 0.15


def band_defaults(index: int, e_values) -> Dict[str, Any]:
    """第 ``index`` 条带的默认参数（面板初值与缺省补齐共用同一份）。"""
    energy = np.asarray(e_values, dtype=np.float64).reshape(-1)
    center = float(np.mean(energy)) if energy.size else 0.0
    span = float(np.ptp(energy)) if energy.size else 1.0
    return {
        "label": f"Band {index + 1}",
        "init": "parabolic",
        "e0": center,
        "curvature": 0.3 * max(span, 1e-6),
        "slope_x": 0.0,
        "slope_y": 0.0,
        "amplitude": 0.25 * span,
        "sigma_frac": 0.35,
        "momentum_scale": 1.0,
        "energy_shift": 0.0,
        "color": DEFAULT_BAND_COLORS[index % len(DEFAULT_BAND_COLORS)],
    }


def _finite(value, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    return number if np.isfinite(number) else float(fallback)


def build_initial_surface(settings: Mapping[str, Any], x, y, e_values) -> np.ndarray:
    """按一条带的设置构造初始化面（未做动量缩放/平移）。

    ``init`` 取 ``parabolic`` / ``gaussian`` / ``plane``，或带 ``grid`` 的
    ``import``（外部网格，先双线性插值到数据网格）。缺省参数由
    :func:`band_defaults` 补齐，非法值退回默认值而不是抛异常——面板上的输入
    永远不该让整个任务失败。
    """
    defaults = band_defaults(0, e_values)
    kind = str(settings.get("init") or defaults["init"]).lower()
    x_axis = np.asarray(x, dtype=np.float64).reshape(-1)
    y_axis = np.asarray(y, dtype=np.float64).reshape(-1)
    span = float(np.ptp(np.asarray(e_values, dtype=np.float64))) if np.size(e_values) else 1.0
    center = float(np.mean(np.asarray(e_values, dtype=np.float64))) if np.size(e_values) else 0.0

    if kind == "import":
        grid = settings.get("grid")
        if not isinstance(grid, IS.SurfaceGrid):
            raise ValueError("导入式初始化缺少网格数据。")
        return IS.resample_surface(grid.z, grid.x, grid.y, x_axis, y_axis)

    e0 = _finite(settings.get("e0"), center)
    if kind == "plane":
        return IS.plane_surface(
            x_axis,
            y_axis,
            e0=e0,
            slope_x=_finite(settings.get("slope_x"), 0.0),
            slope_y=_finite(settings.get("slope_y"), 0.0),
        )
    if kind == "gaussian":
        sigma = max(abs(_finite(settings.get("sigma_frac"), defaults["sigma_frac"])), 1e-3)
        return IS.gaussian_surface(
            x_axis,
            y_axis,
            e0=e0,
            amplitude=_finite(settings.get("amplitude"), defaults["amplitude"]),
            sigma_x=sigma * max(float(np.ptp(x_axis)) if x_axis.size else 1.0, 1e-6),
            sigma_y=sigma * max(float(np.ptp(y_axis)) if y_axis.size else 1.0, 1e-6),
        )
    if kind != "parabolic":
        raise ValueError(f"未知的初始化面类型：{kind!r}")
    # ``curvature`` 用「中心到动量网格角落的能量抬升」表示（与动量单位无关），
    # 内部再折成抛物系数 a：rise = a·(Δkx² + Δky²)。
    rise = _finite(settings.get("curvature"), defaults["curvature"])
    center_x = float(np.mean(x_axis)) if x_axis.size else 0.0
    center_y = float(np.mean(y_axis)) if y_axis.size else 0.0
    reach_x = float(np.max(np.abs(x_axis - center_x))) if x_axis.size else 1.0
    reach_y = float(np.max(np.abs(y_axis - center_y))) if y_axis.size else 1.0
    denominator = reach_x**2 + reach_y**2
    coefficient = rise / denominator if denominator > 0.0 else 0.0
    return IS.parabolic_surface(x_axis, y_axis, e0=e0, a_x=coefficient, a_y=coefficient)


def _energy_window(snapshot, params: Mapping[str, Any]):
    """按物理能量窗口裁出体数据与能量轴；未给窗口时用整条轴。"""
    energy = np.asarray(snapshot.e, dtype=np.float64).reshape(-1)
    window = params.get("energy_window")
    if not window:
        return np.asarray(snapshot.volume), energy
    low, high = sorted((_finite(window[0], float(energy[0])), _finite(window[1], float(energy[-1]))))
    inside = (energy >= low) & (energy <= high)
    if int(np.count_nonzero(inside)) < 8:
        raise ValueError("能量窗口太窄：至少需要 8 个能量采样点。")
    return np.asarray(snapshot.volume)[:, :, inside], energy[inside]


def run_reconstruction(snapshot, params: Mapping[str, Any], cancel: CancelToken) -> AnalysisSurface2D:
    """宿主工作函数：``(快照, 冻结参数, 取消信号) -> AnalysisSurface2D``。"""
    cancel.raise_if_cancelled()
    settings = [dict(item or {}) for item in (params.get("bands") or ())]
    if not settings:
        raise ValueError("至少需要一条带。")

    volume, energy = _energy_window(snapshot, params)
    x_axis = np.asarray(snapshot.x, dtype=np.float64).reshape(-1)
    y_axis = np.asarray(snapshot.y, dtype=np.float64).reshape(-1)

    smooth = bool(params.get("smooth", True))
    clahe = bool(params.get("clahe", True))
    cancel.report_progress(0.02, "预处理体数据…")
    processed = preprocess_volume(
        volume,
        smooth=smooth,
        clahe=clahe,
        sigma=DEFAULT_SIGMA,
        mclahe_params=dict(DEFAULT_MCLAHE_PARAMS),
    )
    cancel.raise_if_cancelled()
    cancel.report_progress(_PREPROCESS_SHARE, "预处理完成")

    initials: List[np.ndarray] = []
    labels: List[str] = []
    colors: List[str] = []
    for index, item in enumerate(settings):
        surface = build_initial_surface(item, x_axis, y_axis, energy)
        surface = IS.align_surface(
            surface,
            x_axis,
            y_axis,
            momentum_scale=_finite(item.get("momentum_scale"), 1.0),
            energy_shift=_finite(item.get("energy_shift"), 0.0),
        )
        initials.append(surface)
        labels.append(str(item.get("label") or f"Band {index + 1}"))
        colors.append(str(item.get("color") or DEFAULT_BAND_COLORS[index % len(DEFAULT_BAND_COLORS)]))

    eta = _finite(params.get("eta"), 0.1)
    if eta <= 0.0:
        raise ValueError("eta 必须为正数。")
    maxiter = int(_finite(params.get("maxiter"), 200))
    total_bands = len(initials)

    def on_iteration(band_index: int, iteration: int, loss: float) -> None:
        share = _PREPROCESS_SHARE + (1.0 - _PREPROCESS_SHARE) * (
            band_index + _iteration_fraction(iteration, maxiter)
        ) / max(total_bands, 1)
        cancel.report_progress(share, f"{labels[band_index]}：第 {iteration} 次迭代")

    def on_band_start(band_index: int, label: str) -> None:
        cancel.raise_if_cancelled()
        cancel.report_progress(
            _PREPROCESS_SHARE + (1.0 - _PREPROCESS_SHARE) * band_index / max(total_bands, 1),
            f"开始重构 {label}",
        )

    results = reconstruct_bands(
        processed.volume,
        energy,
        initials,
        eta=eta,
        labels=labels,
        maxiter=maxiter,
        cancel=cancel,
        on_band_start=on_band_start,
        on_iteration=on_iteration,
    )
    cancel.report_progress(1.0, "重构完成")

    surfaces = [
        BandSurface(z=np.asarray(result.surface, dtype=np.float64), label=label, color=color)
        for result, label, color in zip(results, labels, colors)
    ]
    return AnalysisSurface2D(
        x=x_axis,
        y=y_axis,
        surfaces=surfaces,
        x_label=str(getattr(snapshot, "x_label", "kx") or "kx"),
        x_unit=str(getattr(snapshot, "x_unit", "") or ""),
        y_label=str(getattr(snapshot, "y_label", "ky") or "ky"),
        y_unit=str(getattr(snapshot, "y_unit", "") or ""),
        z_label=str(getattr(snapshot, "e_label", "E") or "E"),
        z_unit=str(getattr(snapshot, "e_unit", "") or ""),
        title=str(params.get("title") or f"{snapshot.page_title} 能带面"),
        params={
            "eta": eta,
            "maxiter": maxiter,
            "smooth": smooth,
            "clahe": clahe,
            "band_count": len(surfaces),
            "bands": [
                {
                    "label": label,
                    "init": str(settings[index].get("init") or "parabolic"),
                    "momentum_scale": _finite(settings[index].get("momentum_scale"), 1.0),
                    "energy_shift": _finite(settings[index].get("energy_shift"), 0.0),
                    "n_iter": int(results[index].n_iter),
                    "loss": float(results[index].loss),
                    "loss_tail_delta": float(results[index].tail_delta),
                    "seconds": float(results[index].elapsed),
                }
                for index, label in enumerate(labels)
            ],
            "preprocess": dict(processed.stats),
            "source_page": str(snapshot.page_id),
            "source_shape": list(snapshot.shape),
            "source_scope": str(getattr(snapshot, "scope_label", "") or ""),
            "data_generation": int(snapshot.data_generation),
            "energy_window": [float(energy[0]), float(energy[-1])],
        },
    )


def _iteration_fraction(iteration: int, maxiter: int) -> float:
    """把迭代次数折算成该带的完成比例（封顶 0.95，剩下的留给收尾）。"""
    limit = max(int(maxiter), 1)
    return min(0.95, float(iteration) / limit)


def surface_params_schema() -> Dict[str, Any]:
    """参数结构说明（面板、测试与文档共用一份，避免三处各写一遍）。"""
    return {
        "bands": [
            {
                "label": "str",
                "init": "parabolic | gaussian | plane | import",
                "e0": "float（能量单位，中心处能量）",
                "curvature": "float（抛物面从中心到动量网格角落的能量抬升）",
                "slope_x": "float（平面斜率）",
                "slope_y": "float（平面斜率）",
                "amplitude": "float（高斯面幅度）",
                "sigma_frac": "float（高斯面宽度占动量跨度比例）",
                "momentum_scale": "float（>0，绕中心缩放色散）",
                "energy_shift": "float（刚性平移）",
                "color": "str（显示偏好，阶段 3 使用）",
            }
        ],
        "eta": "float > 0（平滑先验强度，单位同能量轴）",
        "maxiter": "int（L-BFGS-B 最大迭代次数）",
        "smooth": "bool（高斯平滑）",
        "clahe": "bool（MCLAHE 对比度增强）",
        "energy_window": "[low, high] 或 None（物理能量窗口）",
        "title": "str（结果页标题）",
    }
