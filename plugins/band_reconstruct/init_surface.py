# -*- coding: utf-8 -*-
"""初始化能量面（warm start）：解析函数、外部网格导入与对齐参数。

初始化面决定优化落在哪个局部极小：两条带交叉时，只有把交叉信息正确放进初值，
逐带优化才可能各自收敛到正确的分支。本模块提供：

- 解析面：抛物面 / 高斯面 / 平面（参数在面板上可调）；
- 外部导入：``.npz``（``z/x/y``）或三列文本 ``kx ky E`` 的 DFT 网格，双线性
  插值到数据动量网格；
- 两条对齐参数（论文三超参数之二）：**动量缩放**（绕中心拉伸色散）与**能量刚性
  平移**。

面数组的索引约定与体数据一致：``surface[i, j]`` 对应 ``(x[i], y[j])``。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Union

import numpy as np
from scipy import ndimage

__all__ = [
    "SurfaceGrid",
    "align_surface",
    "fractional_index",
    "gaussian_surface",
    "load_surface_grid",
    "parabolic_surface",
    "plane_surface",
    "resample_surface",
]

PathLike = Union[str, Path]


def _target_axis(values, name: str) -> np.ndarray:
    """目标网格轴：允许只有一个点（对单点求值），其余校验与源轴一致。"""
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if array.size == 1:
        if not np.isfinite(array[0]):
            raise ValueError(f"{name} 含非有限值。")
        return array
    return _axis(array, name)


def _axis(values, name: str) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if array.size < 2:
        raise ValueError(f"{name} 至少需要两个采样点。")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} 含非有限值。")
    if not (np.all(np.diff(array) > 0) or np.all(np.diff(array) < 0)):
        raise ValueError(f"{name} 必须严格单调。")
    return array


def plane_surface(x, y, *, e0: float = 0.0, slope_x: float = 0.0, slope_y: float = 0.0) -> np.ndarray:
    """平面 ``E = e0 + slope_x·kx + slope_y·ky``。"""
    kx, ky = np.meshgrid(_axis(x, "x"), _axis(y, "y"), indexing="ij")
    return np.asarray(e0 + slope_x * kx + slope_y * ky, dtype=np.float64)


def parabolic_surface(
    x,
    y,
    *,
    e0: float = 0.0,
    a_x: float = 1.0,
    a_y: float = 1.0,
    kx0: Optional[float] = None,
    ky0: Optional[float] = None,
) -> np.ndarray:
    """抛物面 ``E = e0 + a_x·(kx−kx0)² + a_y·(ky−ky0)²``；中心默认取坐标中点。"""
    kx_axis = _axis(x, "x")
    ky_axis = _axis(y, "y")
    center_x = float(np.mean(kx_axis)) if kx0 is None else float(kx0)
    center_y = float(np.mean(ky_axis)) if ky0 is None else float(ky0)
    kx, ky = np.meshgrid(kx_axis, ky_axis, indexing="ij")
    return np.asarray(
        e0 + a_x * (kx - center_x) ** 2 + a_y * (ky - center_y) ** 2, dtype=np.float64
    )


def gaussian_surface(
    x,
    y,
    *,
    e0: float = 0.0,
    amplitude: float = 0.1,
    sigma_x: float = 0.3,
    sigma_y: float = 0.3,
    kx0: Optional[float] = None,
    ky0: Optional[float] = None,
) -> np.ndarray:
    """高斯面 ``E = e0 + A·exp(−((kx−kx0)²/2σx² + (ky−ky0)²/2σy²))``。"""
    kx_axis = _axis(x, "x")
    ky_axis = _axis(y, "y")
    center_x = float(np.mean(kx_axis)) if kx0 is None else float(kx0)
    center_y = float(np.mean(ky_axis)) if ky0 is None else float(ky0)
    kx, ky = np.meshgrid(kx_axis, ky_axis, indexing="ij")
    exponent = ((kx - center_x) ** 2) / (2.0 * sigma_x**2) + (
        (ky - center_y) ** 2
    ) / (2.0 * sigma_y**2)
    return np.asarray(e0 + amplitude * np.exp(-exponent), dtype=np.float64)


def fractional_index(values, grid) -> np.ndarray:
    """物理坐标 → 网格分数索引；越界夹到端点。递减网格同样支持。"""
    axis = _axis(grid, "grid")
    targets = np.asarray(values, dtype=np.float64)
    positions = np.arange(axis.size, dtype=np.float64)
    if axis[0] > axis[-1]:
        axis = axis[::-1]
        positions = positions[::-1]
    return np.interp(targets, axis, positions)


def resample_surface(z, x_src, y_src, x_dst, y_dst) -> np.ndarray:
    """把 ``z[i, j]`` 从 ``(x_src, y_src)`` 网格双线性插值到 ``(x_dst, y_dst)``。

    目标网格越界时按边缘复制取值（``mode="nearest"``）；源面的 NaN 会按插值
    扩散到附近的目标点（NaN 表示无解，不做伪造成 0 的处理）。
    """
    values = np.asarray(z, dtype=np.float64)
    source_x = _axis(x_src, "x_src")
    source_y = _axis(y_src, "y_src")
    if values.shape != (source_x.size, source_y.size):
        raise ValueError(
            f"源面形状 {values.shape} 与源网格 {(source_x.size, source_y.size)} 不一致。"
        )
    target_x = _target_axis(x_dst, "x_dst")
    target_y = _target_axis(y_dst, "y_dst")
    rows, columns = np.meshgrid(
        fractional_index(target_x, source_x),
        fractional_index(target_y, source_y),
        indexing="ij",
    )
    return ndimage.map_coordinates(
        values, [rows, columns], order=1, mode="nearest", prefilter=False
    )


def align_surface(
    surface,
    x,
    y,
    *,
    momentum_scale: float = 1.0,
    momentum_center: Optional[Tuple[float, float]] = None,
    energy_shift: float = 0.0,
) -> np.ndarray:
    """对初始化面施加对齐参数。

    - ``momentum_scale=s``：以 ``momentum_center``（默认坐标中点）为中心把色散沿
      动量方向缩放；``s > 1`` 变宽、``s < 1`` 变窄：取值来自原面在
      ``c + (k − c)/s`` 处的双线性插值；
    - ``energy_shift``：整体刚性平移（能量单位）。
    """
    values = np.asarray(surface, dtype=np.float64)
    kx_axis = _axis(x, "x")
    ky_axis = _axis(y, "y")
    if values.shape != (kx_axis.size, ky_axis.size):
        raise ValueError(
            f"面形状 {values.shape} 与坐标网格 {(kx_axis.size, ky_axis.size)} 不一致。"
        )
    scale = float(momentum_scale)
    if not np.isfinite(scale) or scale <= 0.0:
        raise ValueError("momentum_scale 必须为正数。")
    center_x = float(np.mean(kx_axis)) if momentum_center is None else float(momentum_center[0])
    center_y = float(np.mean(ky_axis)) if momentum_center is None else float(momentum_center[1])

    if abs(scale - 1.0) > 1e-12:
        warped_x = center_x + (kx_axis - center_x) / scale
        warped_y = center_y + (ky_axis - center_y) / scale
        values = resample_surface(values, kx_axis, ky_axis, warped_x, warped_y)
    if energy_shift:
        values = values + float(energy_shift)
    return np.asarray(values, dtype=np.float64)


@dataclass(frozen=True)
class SurfaceGrid:
    """外部导入的能量面网格。"""

    z: np.ndarray
    x: np.ndarray
    y: np.ndarray
    label: str = ""
    source: str = ""

    @property
    def shape(self) -> Tuple[int, int]:
        return (int(self.z.shape[0]), int(self.z.shape[1]))

    def describe(self) -> str:
        source = f"（{self.source}）" if self.source else ""
        return f"{self.shape[0]}×{self.shape[1]} 网格{source}"


def _grid_from_points(points: np.ndarray) -> SurfaceGrid:
    """三列 ``(kx, ky, E)`` 散点 → 规则网格（线性插值，凸包外用最近邻填充）。"""
    from scipy.interpolate import griddata

    x_values = np.unique(points[:, 0])
    y_values = np.unique(points[:, 1])
    if x_values.size < 2 or y_values.size < 2:
        raise ValueError("导入的点太少，无法构成二维网格。")
    grid_x, grid_y = np.meshgrid(x_values, y_values, indexing="ij")
    linear = griddata(points[:, :2], points[:, 2], (grid_x, grid_y), method="linear")
    if np.any(~np.isfinite(linear)):
        nearest = griddata(points[:, :2], points[:, 2], (grid_x, grid_y), method="nearest")
        linear = np.where(np.isfinite(linear), linear, nearest)
    return SurfaceGrid(z=linear, x=x_values, y=y_values)


def load_surface_grid(path: PathLike) -> SurfaceGrid:
    """读取外部能量面网格。

    支持两种文件（都是显式输入，不猜列序）：

    - ``.npz``：必须含 ``z``（二维）、``x``、``y``（一维），可选 ``label``；
      也接受 ``E`` / ``kx`` / ``ky`` 作为别名；
    - 文本（``.txt`` / ``.csv`` / ``.dat``）：三列 ``kx ky E`` 散点，``#`` 起始的
      行忽略。
    """
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"找不到能量面网格文件：{target}")
    if target.suffix.lower() == ".npz":
        with np.load(target) as payload:
            keys = set(payload.files)

            def pick(*names):
                for name in names:
                    if name in keys:
                        return payload[name]
                raise ValueError(f"{target.name} 缺少字段：{'/'.join(names)}")

            z = np.asarray(pick("z", "E"), dtype=np.float64)
            x = np.asarray(pick("x", "kx"), dtype=np.float64).reshape(-1)
            y = np.asarray(pick("y", "ky"), dtype=np.float64).reshape(-1)
            label = ""
            if "label" in keys:
                label = str(np.asarray(payload["label"]).reshape(-1)[0])
        if z.ndim != 2:
            raise ValueError("npz 里的 z 必须是二维数组。")
        if z.shape != (x.size, y.size):
            raise ValueError(
                f"npz 里 z 的形状 {z.shape} 与 x/y 长度 {(x.size, y.size)} 不一致。"
            )
        return SurfaceGrid(z=z, x=x, y=y, label=label, source=target.name)

    rows = []
    with open(target, "r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            parts = text.replace(",", " ").split()
            if len(parts) < 3:
                continue
            try:
                rows.append([float(item) for item in parts[:3]])
            except ValueError:
                continue
    if not rows:
        raise ValueError(f"{target.name} 里没有可解析的 (kx, ky, E) 数据行。")
    points = np.asarray(rows, dtype=np.float64)
    grid = _grid_from_points(points)
    return SurfaceGrid(z=grid.z, x=grid.x, y=grid.y, label="", source=target.name)
