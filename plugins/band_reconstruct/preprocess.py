# -*- coding: utf-8 -*-
"""体数据预处理：高斯平滑 + MCLAHE 对比度增强。

流程（对 ``[X, Y, E]`` 三维强度体，与算法规格一致）：

1. **清理**：非有限值（NaN/±inf）置 0——擦除区域留下的 NaN 不是 0 强度，但后续
   平滑会把单点 NaN 扩散成一片，必须在入口一次处理干净；
2. **高斯平滑**：``σ = (0.8, 0.8, 1.0)`` 像素（kx, ky, E）；
3. **归一化**：按最大值缩放到 ``[0, 1]``，让 ``threshold`` / 损失下限这类相对
   参数在不同数据集上含义一致（对损失的极值位置没有影响）；
4. **MCLAHE**：限制对比度的自适应直方图均衡。

MCLAHE 实现说明（与 fuller/mclahe 的差异见 ``README.md`` 与交接文档）：

- 采用**分块 CLAHE**：每轴分成 ``tiles`` 段（默认 8，对应「核为各轴长度的
  1/8」），每块独立统计直方图、按 ``clip_limit`` 限幅并均匀重分配，再用块的
  CDF 做三次线性插值（8 邻块加权），避免块边界出现假轮廓；
- ``threshold`` 以下的体素不进入直方图，输出直接置 0（背景压制）；
- 统计与映射都按 E 轴切片分块进行，峰值内存与体数据总大小无关。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np
from scipy import ndimage

__all__ = [
    "DEFAULT_MCLAHE_PARAMS",
    "DEFAULT_SIGMA",
    "PreprocessResult",
    "gaussian_smooth",
    "mclahe",
    "normalize_volume",
    "preprocess_volume",
    "sanitize_volume",
]

#: 默认高斯平滑参数（kx, ky, E 三个方向的像素 σ）。
DEFAULT_SIGMA: Tuple[float, float, float] = (0.8, 0.8, 1.0)

#: 默认 MCLAHE 参数；``n_bins`` / ``clip_limit`` / ``threshold`` 取自 fuller 默认值。
DEFAULT_MCLAHE_PARAMS: Dict[str, Any] = {
    "n_bins": 128,
    "clip_limit": 0.01,
    "threshold": 1e-6,
    "tiles": 8,
}

#: 单个分块处理的体素上限，用于约束分块映射的峰值内存。
_CHUNK_VOXELS = 2_000_000


@dataclass(frozen=True)
class PreprocessResult:
    """预处理结果：处理后的体数据 + 各步骤的参数与统计。"""

    volume: np.ndarray
    steps: Tuple[str, ...] = ()
    stats: Dict[str, Any] = field(default_factory=dict)

    @property
    def shape(self) -> Tuple[int, int, int]:
        return tuple(int(size) for size in self.volume.shape)  # type: ignore[return-value]


def sanitize_volume(volume) -> np.ndarray:
    """非有限值置 0，返回 float32 副本；形状保持。"""
    values = np.asarray(volume, dtype=np.float32)
    if values.ndim != 3:
        raise ValueError("体数据必须是三维数组 [X, Y, E]。")
    if values.size == 0:
        raise ValueError("体数据为空。")
    if not np.all(np.isfinite(values)):
        values = np.where(np.isfinite(values), values, np.float32(0.0))
    return values


def gaussian_smooth(volume, sigma: Sequence[float] = DEFAULT_SIGMA) -> np.ndarray:
    """各向异性高斯平滑；``sigma`` 顺序与体数据轴一致（kx, ky, E）。"""
    values = np.asarray(volume, dtype=np.float32)
    sigmas = tuple(float(item) for item in sigma)
    if len(sigmas) != values.ndim:
        raise ValueError(f"sigma 长度 {len(sigmas)} 与体数据维度 {values.ndim} 不一致。")
    if all(item <= 0 for item in sigmas):
        return values.copy()
    return ndimage.gaussian_filter(values, sigma=sigmas, mode="nearest")


def normalize_volume(volume) -> Tuple[np.ndarray, float]:
    """按最大值缩放到 ``[0, 1]``；负值截到 0。返回 ``(归一化体, 缩放因子)``。"""
    values = np.asarray(volume, dtype=np.float32)
    values = np.maximum(values, np.float32(0.0))
    peak = float(np.max(values)) if values.size else 0.0
    if not np.isfinite(peak) or peak <= 0.0:
        return values.copy(), 1.0
    return (values / np.float32(peak)).astype(np.float32, copy=False), peak


def _tile_layout(size: int, tiles: int) -> Tuple[np.ndarray, np.ndarray]:
    """把一维长度 ``size`` 分成至多 ``tiles`` 段。

    返回 ``(edges, centers)``：段边界（含首尾）与段中心的体素坐标。``size`` 小于
    ``tiles`` 时自动减少段数，保证每段非空。
    """
    count = int(max(1, min(int(tiles), int(size))))
    edges = np.linspace(0, int(size), count + 1).round().astype(np.int64)
    # 长度极小时 round 可能产生重复边界，也会出现首端不为 0 的情况：先夹再挤出空段。
    edges = np.clip(edges, 0, int(size))
    edges[0] = 0
    edges[-1] = int(size)
    if np.any(np.diff(edges) <= 0):
        edges = np.unique(edges)
        edges[0] = 0
        edges[-1] = int(size)
    centers = (edges[:-1] + edges[1:] - 1) / 2.0
    return edges, centers


def _tile_coordinates(length: int, edges: np.ndarray, centers: np.ndarray):
    """每个体素索引对应的 (低段号, 高段号, 向高段的权重)。

    段号以段中心为基准线性取值并夹到边界：位于段 k 中心与 k+1 中心之间的体素在
    两段之间插值，最外侧的体素落在边缘段（等价于边缘复制）。
    """
    index = np.arange(int(length), dtype=np.float64)
    low = np.searchsorted(edges, index, side="right") - 1
    np.clip(low, 0, centers.size - 1, out=low)
    high = np.minimum(low + 1, centers.size - 1)
    span = np.where(high > low, centers[high] - centers[low], 1.0)
    with np.errstate(invalid="ignore"):
        weight = np.where(high > low, (index - centers[low]) / span, 0.0)
    return low.astype(np.int64), high.astype(np.int64), np.clip(weight, 0.0, 1.0)


def mclahe(
    volume,
    *,
    n_bins: int = 128,
    clip_limit: float = 0.01,
    threshold: float = 1e-6,
    tiles: int = 8,
    chunk_slices: Optional[int] = None,
) -> np.ndarray:
    """分块限制对比度自适应直方图均衡（三维）。

    参数语义：

    - ``n_bins``：直方图分箱数；
    - ``clip_limit``：每块每箱计数的上限，取该块**有效体素数的比例**（与
      scikit-image 的语义一致；超过上限的部分均匀重分配到各箱）；
    - ``threshold``：不进入直方图、输出置 0 的背景阈值（作用于归一化后的强度）；
    - ``tiles``：每轴分块数，默认 8（核 ≈ 轴长的 1/8）。

    输出与输入同形、float32；输入中的 NaN 在输出中保持 NaN。
    """
    values = np.asarray(volume, dtype=np.float32)
    if values.ndim != 3:
        raise ValueError("MCLAHE 只处理三维体数据 [X, Y, E]。")
    bins = int(max(2, n_bins))
    shape = tuple(int(size) for size in values.shape)
    out = np.zeros(shape, dtype=np.float32)
    if values.size == 0:
        return out

    finite = np.isfinite(values)
    if not np.any(finite):
        out[~finite] = np.nan
        return out

    floor = float(threshold)
    peak = float(np.max(values[finite]))
    if not np.isfinite(peak) or peak <= floor:
        out[~finite] = np.nan
        return out

    scale = np.float32(bins) / np.float32(peak)
    x_edges, x_centers = _tile_layout(shape[0], tiles)
    y_edges, y_centers = _tile_layout(shape[1], tiles)
    z_edges, z_centers = _tile_layout(shape[2], tiles)
    count_y, count_z = int(y_centers.size), int(z_centers.size)
    n_tiles = int(x_centers.size * count_y * count_z)

    low_x, high_x, w_x = _tile_coordinates(shape[0], x_edges, x_centers)
    low_y, high_y, w_y = _tile_coordinates(shape[1], y_edges, y_centers)
    low_z, high_z, w_z = _tile_coordinates(shape[2], z_edges, z_centers)

    step = int(chunk_slices) if chunk_slices else max(
        1, _CHUNK_VOXELS // max(1, shape[0] * shape[1])
    )
    ranges = [
        (start, min(start + step, shape[2])) for start in range(0, shape[2], step)
    ]

    def chunk_tables(start: int, stop: int):
        """分块内有效体素的展平坐标与分箱位置（含低箱号与箱内小数部分）。"""
        block = values[:, :, start:stop]
        local_valid = finite[:, :, start:stop] & (block > floor)
        i_idx, j_idx, k_idx = np.nonzero(local_valid)
        flat_block = block[i_idx, j_idx, k_idx]
        position = np.clip(flat_block * scale, 0.0, bins - 1.0)
        low_bin = position.astype(np.int64)
        # CDF 在箱内做线性插值：只按整箱取值会把弱特征量化成台阶，台阶上的
        # 极值位置会发生偏移（弱带比强带更容易被这一步影响）。
        frac = position - low_bin
        high_bin = np.minimum(low_bin + 1, bins - 1)
        return block, local_valid, (i_idx, j_idx, k_idx), (low_bin, high_bin, frac)

    # -- 第一遍：分块直方图 ------------------------------------------------
    hist = np.zeros((n_tiles, bins), dtype=np.float64)
    for start, stop in ranges:
        _, _, (i_idx, j_idx, k_idx), (low_bin, _, _) = chunk_tables(start, stop)
        if i_idx.size == 0:
            continue
        tile = (low_x[i_idx] * count_y + low_y[j_idx]) * count_z + low_z[start + k_idx]
        hist += np.bincount(
            (tile * bins) + low_bin, minlength=n_tiles * bins
        ).reshape(n_tiles, bins)

    # -- 限幅 + 均匀重分配 + CDF ------------------------------------------
    totals = hist.sum(axis=1, keepdims=True)
    limit = np.maximum(1.0, float(clip_limit) * totals)
    clipped = np.minimum(hist, limit)
    excess = (hist - clipped).sum(axis=1, keepdims=True)
    counts = np.where(totals > 0.0, totals, 1.0)
    cdf = np.cumsum(clipped + excess / float(bins), axis=1) / counts
    mapping = (cdf * float(peak)).astype(np.float32)

    # -- 第二遍：三次线性插值映射 ------------------------------------------
    for start, stop in ranges:
        block, local_valid, (i_idx, j_idx, k_idx), (low_bin, high_bin, frac) = (
            chunk_tables(start, stop)
        )
        if i_idx.size == 0:
            continue
        mapped = np.zeros(i_idx.size, dtype=np.float64)
        axis_x = (
            (low_x[i_idx], 1.0 - w_x[i_idx]),
            (high_x[i_idx], w_x[i_idx]),
        )
        axis_y = (
            (low_y[j_idx], 1.0 - w_y[j_idx]),
            (high_y[j_idx], w_y[j_idx]),
        )
        axis_z = (
            (low_z[start + k_idx], 1.0 - w_z[start + k_idx]),
            (high_z[start + k_idx], w_z[start + k_idx]),
        )
        for tile_x, weight_x in axis_x:
            for tile_y, weight_y in axis_y:
                for tile_z, weight_z in axis_z:
                    tile = (tile_x * count_y + tile_y) * count_z + tile_z
                    lookup = mapping[tile, low_bin] + frac * (
                        mapping[tile, high_bin] - mapping[tile, low_bin]
                    )
                    mapped += weight_x * weight_y * weight_z * lookup
        chunk = np.zeros(block.shape, dtype=np.float32)
        chunk[i_idx, j_idx, k_idx] = mapped.astype(np.float32)
        chunk[~finite[:, :, start:stop]] = np.nan
        out[:, :, start:stop] = chunk
    return out


def preprocess_volume(
    volume,
    *,
    smooth: bool = True,
    clahe: bool = True,
    sigma: Sequence[float] = DEFAULT_SIGMA,
    mclahe_params: Optional[Dict[str, Any]] = None,
) -> PreprocessResult:
    """完整预处理链：清理 → 高斯平滑 → 归一化 → MCLAHE。

    返回 :class:`PreprocessResult`；``stats`` 记录各步骤开关、缩放因子与规模信息，
    供面板与结果元数据引用。
    """
    params = dict(DEFAULT_MCLAHE_PARAMS)
    if mclahe_params:
        params.update({key: value for key, value in mclahe_params.items() if value is not None})
    steps = ["sanitize"]
    stats: Dict[str, Any] = {
        "shape": tuple(int(size) for size in np.shape(volume)),
        "smooth": bool(smooth),
        "clahe": bool(clahe),
        "sigma": tuple(float(item) for item in sigma),
        "mclahe": {key: params[key] for key in DEFAULT_MCLAHE_PARAMS},
    }

    values = sanitize_volume(volume)
    stats["input_peak"] = float(np.max(values))

    if smooth:
        values = gaussian_smooth(values, sigma)
        steps.append("gaussian")
    values, scale = normalize_volume(values)
    stats["normalize_scale"] = scale
    if clahe:
        values = mclahe(values, **{key: params[key] for key in DEFAULT_MCLAHE_PARAMS})
        steps.append("mclahe")

    stats["output_peak"] = float(np.max(values))
    return PreprocessResult(volume=values, steps=tuple(steps), stats=stats)
