# -*- coding: utf-8 -*-
"""插件能带面的三维叠加层。

体渲染把体素放在 ``index × spacing`` 的世界坐标上（``spacing[axis] =
200 / (full_shape[axis] - 1)``，域映射到 ``[0, 200]³``），所以叠加层只要把面的
``(kx, ky, E)`` 物理坐标按同一套坐标轴换算成**绝对体素索引**再乘 ``spacing``，
就能和体数据落在同一个坐标系里。

两个旋转要分开处理（两者都由调用方传入，本模块不读窗口状态）：

- ``rotation_angle``：**数据级**旋转（Kx-Ky 平面，scipy 约定：把数组索引 (i, j)
  当成平面上的 (x, y)，绕**索引**中心逆时针转），体数据本身已经被转过，所以叠加
  点要在索引空间转同样的角度。注意动量两轴的体素数通常不同、spacing 因此不等比，
  「索引空间旋转」与「世界坐标旋转」并不等价——必须按前者，才能和体数据对上；
- ``actor_orientation``：**actor 级**滚轮预览旋转（VTK 的 ``SetOrientation(0,0,δ)``，
  绕 ``(100, 100, 100)``，在世界坐标里做），体数据没变、只是 actor 被转，叠加层
  照搬同一个 actor 变换即可。

几何换算是纯函数（:func:`surface_world_geometry`），可以脱离 VTK 单独验证；
本模块只在真正建 actor 时才用 pyvista。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence, Tuple

import numpy as np

__all__ = [
    "OverlaySurface",
    "SurfaceOverlayManager",
    "rotate_indices",
    "surface_world_geometry",
]

#: 三维场景的边长（与 ``render_core`` 的 ``[0, 200]³`` 约定一致）。
WORLD_SPAN = 200.0

#: 叠加层默认不透明度。
DEFAULT_OPACITY = 0.9


@dataclass(frozen=True)
class OverlaySurface:
    """一条能带面 + 显示偏好（显示层的输入，不含任何宿主状态）。"""

    key: str
    label: str
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    color: str = ""
    opacity: float = DEFAULT_OPACITY
    group: str = ""

    def __post_init__(self):
        z = np.asarray(self.z, dtype=np.float64)
        if z.ndim != 2:
            raise ValueError("叠加面的 z 必须是二维数组。")
        object.__setattr__(self, "z", z)


def rotate_indices(rows, columns, angle: float, shape: Sequence[int]):
    """按 ``scipy.ndimage.rotate(axes=(0, 1))`` 的约定旋转数组索引。

    约定（与真实后端 ``CpuComputeBackend.rotate_volume`` 逐点核对过）：索引
    ``(i, j)`` 当作平面上的 ``(x, y)``，绕数组中心 ``((nx-1)/2, (ny-1)/2)``
    逆时针旋转 ``angle`` 度。数组中心这个点旋转后仍在原地，而它在世界坐标里
    永远对应 ``(100, 100)``（``((n-1)/2) × 200/(n-1) = 100``）。
    """
    center_row = (float(shape[0]) - 1.0) / 2.0
    center_column = (float(shape[1]) - 1.0) / 2.0
    radians = np.deg2rad(float(angle))
    cos, sin = float(np.cos(radians)), float(np.sin(radians))
    delta_row = np.asarray(rows, dtype=np.float64) - center_row
    delta_column = np.asarray(columns, dtype=np.float64) - center_column
    return (
        center_row + delta_row * cos - delta_column * sin,
        center_column + delta_row * sin + delta_column * cos,
    )


def _axis_indices(values, axis_values) -> np.ndarray:
    """物理坐标 → 绝对体素索引（越界夹到端点）。"""
    grid = np.asarray(axis_values, dtype=np.float64).reshape(-1)
    positions = np.arange(grid.size, dtype=np.float64)
    if grid.size < 2:
        raise ValueError("坐标轴至少需要两个采样点。")
    if grid[0] > grid[-1]:
        grid = grid[::-1]
        positions = positions[::-1]
    return np.interp(np.asarray(values, dtype=np.float64), grid, positions)


def surface_world_geometry(
    surface: OverlaySurface,
    *,
    coords: Mapping[str, np.ndarray],
    full_shape: Sequence[int],
    rotation_angle: float = 0.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """把一条能带面换算成世界坐标的 ``(points, faces)``。

    - 点：只保留 ``z`` 有限的网格点（NaN 表示无解，不连线也不占点表）；
    - 面：四个角都有限的四边形才生成，索引指向压缩后的点表；
    - 旋转后落到原数组框外的点同样丢弃：体数据的旋转是 ``reshape=False``，框外
      没有数据，叠加层画出去就会在角落里多出体数据根本没有的四个尖角；
    - ``points`` 为空表示这条带在当前数据上没有可画的区域。
    """
    x = np.asarray(surface.x, dtype=np.float64).reshape(-1)
    y = np.asarray(surface.y, dtype=np.float64).reshape(-1)
    z = np.asarray(surface.z, dtype=np.float64)
    if z.shape != (x.size, y.size):
        raise ValueError(
            f"叠加面的 z 形状 {z.shape} 与坐标网格 {(x.size, y.size)} 不一致。"
        )
    shape = tuple(int(size) for size in full_shape[:3])
    if len(shape) != 3 or any(size < 2 for size in shape):
        raise ValueError("full_shape 必须是三个 ≥2 的维度。")

    def spacing(axis: int) -> float:
        return WORLD_SPAN / float(shape[axis] - 1)

    rows = _axis_indices(x, coords["X"])
    columns = _axis_indices(y, coords["Y"])
    table = _axis_indices(z, coords["E"])

    if abs(float(rotation_angle)) > 1e-9:
        rows2d, columns2d = np.meshgrid(rows, columns, indexing="ij")
        rotated_rows, rotated_columns = rotate_indices(
            rows2d, columns2d, rotation_angle, shape[:2]
        )
    else:
        rotated_rows, rotated_columns = np.meshgrid(rows, columns, indexing="ij")

    finite = np.isfinite(table)
    if abs(float(rotation_angle)) > 1e-9:
        # 旋转会把方格的四个角推到原数组之外；体数据在框外没有内容（reshape=False
        # 的旋转把它裁掉了），叠加层同样裁掉，否则角落会多出体数据不存在的尖角。
        finite &= (rotated_rows >= -0.5) & (rotated_rows <= shape[0] - 0.5)
        finite &= (rotated_columns >= -0.5) & (rotated_columns <= shape[1] - 0.5)
    if not np.any(finite):
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0,), dtype=np.int64)

    points = np.column_stack(
        [
            rotated_rows[finite] * spacing(0),
            rotated_columns[finite] * spacing(1),
            table[finite] * spacing(2),
        ]
    ).astype(np.float32)

    index_map = np.full(table.shape, -1, dtype=np.int64)
    index_map[finite] = np.arange(points.shape[0], dtype=np.int64)

    # 四个角都有解才生成四边形；NaN 区域整块跳过（不补洞、不外推）。
    corners = (
        index_map[:-1, :-1],
        index_map[:-1, 1:],
        index_map[1:, 1:],
        index_map[1:, :-1],
    )
    valid = corners[0] >= 0
    for corner in corners[1:]:
        valid &= corner >= 0
    block = np.stack([corner[valid] for corner in corners], axis=1)
    if block.size == 0:
        return points, np.zeros((0,), dtype=np.int64)

    faces = np.empty(block.shape[0] * 5, dtype=np.int64)
    faces[0::5] = 4
    faces[1::5] = block[:, 0]
    faces[2::5] = block[:, 1]
    faces[3::5] = block[:, 2]
    faces[4::5] = block[:, 3]
    return points, faces


@dataclass
class _Actor:
    actor: object
    signature: tuple
    visible: bool = True


class SurfaceOverlayManager:
    """管理三维场景里的叠加 actor：创建、更新、显隐与清理。

    ``sync`` 是幂等的：同一批面重复调用不会重建 actor；不在列表里的面会被移除。
    宿主负责传入当前有效的那批面（数据换代、来源页关闭后自然就不在列表里了）。
    """

    def __init__(self, plotter):
        self.plotter = plotter
        self._actors: Dict[str, _Actor] = {}
        self._hidden: set = set()

    # ------------------------------------------------------------------ 查询
    def keys(self) -> List[str]:
        return list(self._actors)

    def is_visible(self, key: str) -> bool:
        entry = self._actors.get(key)
        return bool(entry is not None and entry.visible)

    def visible_keys(self) -> List[str]:
        return [key for key, entry in self._actors.items() if entry.visible]

    # ------------------------------------------------------------------ 同步
    def sync(
        self,
        surfaces: Sequence[OverlaySurface],
        *,
        coords: Mapping[str, np.ndarray],
        full_shape: Sequence[int],
        rotation_angle: float = 0.0,
        actor_orientation: Sequence[float] = (0.0, 0.0, 0.0),
    ) -> None:
        wanted = {surface.key: surface for surface in surfaces}
        for key in list(self._actors):
            if key not in wanted:
                # 这一条带彻底不在场景里了：连显隐偏好一起忘掉。
                self._remove(key)
        for key, surface in wanted.items():
            points, faces = surface_world_geometry(
                surface,
                coords=coords,
                full_shape=full_shape,
                rotation_angle=rotation_angle,
            )
            signature = self._signature(surface, points, rotation_angle)
            entry = self._actors.get(key)
            if entry is None:
                if points.shape[0] == 0:
                    continue
                self._add(key, surface, points, faces, signature, actor_orientation)
            elif entry.signature != signature:
                # 重建几何，但保留用户对这个带选的显隐。
                self._remove(key, forget=False)
                if points.shape[0] > 0:
                    self._add(key, surface, points, faces, signature, actor_orientation)
            else:
                self._apply_orientation(entry.actor, actor_orientation)
                self._apply_visibility(key)

    def set_visible(self, key: str, visible: bool) -> None:
        """只切换显隐，不重建几何。"""
        if visible:
            self._hidden.discard(key)
        else:
            self._hidden.add(key)
        self._apply_visibility(key)

    def set_actor_orientation(self, orientation: Sequence[float]) -> None:
        """把同一个 actor 变换施加到所有叠加层（跟住滚轮预览旋转）。"""
        for entry in self._actors.values():
            self._apply_orientation(entry.actor, orientation)

    def clear(self) -> None:
        for key in list(self._actors):
            self._remove(key)

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _signature(surface: OverlaySurface, points: np.ndarray, rotation_angle: float) -> tuple:
        z = np.asarray(surface.z, dtype=np.float64)
        return (
            hashlib.blake2b(np.ascontiguousarray(z).tobytes(), digest_size=8).digest(),
            str(surface.color),
            round(float(surface.opacity), 4),
            round(float(rotation_angle), 6),
            int(points.shape[0]),
        )

    def _add(self, key, surface, points, faces, signature, actor_orientation) -> None:
        import pyvista as pv

        mesh = pv.PolyData(points, faces)
        color = str(surface.color or "") or None
        actor = self.plotter.add_mesh(
            mesh,
            name=key,
            color=color,
            opacity=float(surface.opacity),
            smooth_shading=False,
            lighting=True,
            pickable=False,
        )
        entry = _Actor(actor=actor, signature=signature, visible=True)
        self._actors[key] = entry
        self._apply_orientation(actor, actor_orientation)
        self._apply_visibility(key)

    def _apply_orientation(self, actor, actor_orientation) -> None:
        if actor is None:
            return
        try:
            actor.SetOrigin(float(WORLD_SPAN) / 2.0, float(WORLD_SPAN) / 2.0, float(WORLD_SPAN) / 2.0)
            actor.SetOrientation(
                float(actor_orientation[0]),
                float(actor_orientation[1]),
                float(actor_orientation[2]),
            )
        except Exception:
            pass

    def _apply_visibility(self, key: str) -> None:
        entry = self._actors.get(key)
        if entry is None:
            return
        visible = key not in self._hidden
        entry.visible = visible
        try:
            entry.actor.SetVisibility(bool(visible))
        except Exception:
            pass

    def _remove(self, key: str, *, forget: bool = True) -> None:
        entry = self._actors.pop(key, None)
        if forget:
            self._hidden.discard(key)
        if entry is None:
            return
        try:
            self.plotter.remove_actor(key, render=False)
        except TypeError:
            self.plotter.remove_actor(key)
        except Exception:
            pass
