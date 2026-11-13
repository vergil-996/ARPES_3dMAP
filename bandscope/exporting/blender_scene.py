# -*- coding: utf-8 -*-
"""冻结 3D 快照到 Blender 场景数据的无窗口、无 VTK 转换。"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from matplotlib import colormaps

from bandscope.exporting.publication_renderers import (
    _orientation_axis_order, compute_level_info,
)
from bandscope.core.display_style import (
    is_independent,
    legacy_display_style,
    legacy_render_quality,
    resolved_display_style,
    resolved_render_quality,
)
from bandscope.processing.display_transfer import (
    channels,
    volume_display_unit_distance,
    volume_unit_distance,
)

# 2: 增加始终生成的 guide_frame 元数据与辅助盒框契约。
# 3: 增加默认可见、独立于主视图坐标轴开关的 axis_indicator 三轴方向标。
# 5: 元素分组并排放置；方向标使用真实三维轴和封闭圆锥。
# 6: 以数据体中心为基准的六方位布局，现有三组分别占左、后、上。
# 7: 工程—条目契约、独立资产目录、六项可选预设与可回读的显示编辑状态。
# 8: 逐薄板八材质、两种薄板布局、配套 Cycles 灯光与推荐采样；工程 schema 7 仍可回读。
# 9: 十种统一大写 A 的可编辑文本样式、打包字体、下方两行五列布局与独立辅助灯。
# 10: 独立归一化、颜色／透明度样式与渲染质量；schema 7–9 更新保持旧模式。
# 11: 可选混合受光数据体材质与逐条目灯光；density 统一描述为总消光系数。
BLENDER_SCENE_SCHEMA = 11
BLENDER_DISPLAY_EDIT_FORMAT = 2
DISPLAY_TO_BLENDER = 0.01
ALPHA_CAP = 1.0 - 1.0e-6
DEFAULT_RENDER_LONG_EDGE = 1920
# 完整 0–200 显示域映射到 Blender 后固定为居中的 2 个单位；坐标轴参考（三轴箭头）
# 始终画到这个范围。辅助盒框再向外扩一圈，保证无论 ROI／裁剪如何，盒框都严格大于
# 坐标轴框并包住数据体。
AXIS_FRAME_BOUNDS = (-1.0, 1.0, -1.0, 1.0, -1.0, 1.0)
GUIDE_FRAME_MARGIN = 0.1
GUIDE_FRAME_OBJECT_NAME = "BandScope guide frame"
GUIDE_FRAME_COLLECTION_NAME = "Guide frame · auxiliary"
AXIS_INDICATOR_COLLECTION_NAME = "Orientation marker · publication"


def separated_layout(data_bounds):
    """Place intact groups around the data; offsets never change scientific grids.

    Slots are group centers in fixed world directions, not camera directions.
    The box and indicator have nonzero local centers, so their translations
    compensate for those centers rather than displacing them diagonally.
    """
    bounds = np.asarray(guide_frame_bounds(data_bounds)).reshape(3, 2)
    center = np.asarray(data_bounds, dtype=float).reshape(3, 2).mean(axis=1)
    step = np.maximum(4.0, bounds[:, 1] - bounds[:, 0] + 2.0)
    slots = {}
    for axis, names in enumerate((("left", "right"), ("front", "back"), ("bottom", "top"))):
        for sign, name in zip((-1, 1), names):
            position = center.copy()
            position[axis] += sign * step[axis]
            slots[name] = position.tolist()
    return {
        "data": [0.0, 0.0, 0.0], "center": center.tolist(), "slots": slots,
        "placements": {
            "coordinates": "left", "guide_frame": "back", "orientation": "top",
            "text_presets": "bottom",
        },
        "coordinates": slots["left"],
        "guide_frame": (np.asarray(slots["back"]) - bounds.mean(axis=1)).tolist(),
        "orientation": (np.asarray(slots["top"]) - 0.5).tolist(),
        "viewport": {"center": center.tolist(), "distance": float(np.max(step) * 2.5)},
    }


def axis_indicator_metadata(camera, resolution, axis_titles, origin):
    # Scientific X/Y/E directions are genuine world X/Y/Z, regardless of the camera.
    order = _orientation_axis_order(camera)
    origin = np.asarray(origin, dtype=float)
    right, up, _ = camera_basis(camera)
    axes = []
    for slot, axis in enumerate(order):
        direction = np.eye(3)[axis]
        side, other = np.eye(3)[(axis + 1) % 3], np.eye(3)[(axis + 2) % 3]
        tip = origin + direction
        base = tip - 0.18 * direction
        ring = [base + 0.065 * (math.cos(t) * side + math.sin(t) * other)
                for t in np.linspace(0, 2 * math.pi, 24, endpoint=False)]
        vertices = [tip.tolist()] + [v.tolist() for v in ring]
        faces = [[0, i + 1, (i + 1) % 24 + 1] for i in range(24)]
        faces.append(list(range(24, 0, -1)))
        role = ("horizontal", "vertical", "depth")[slot]
        label = axis_titles[axis] or ("X", "Y", "E")[axis]
        short = ("kx", "ky", "E")[axis]
        math_label = label == short or label.startswith(short + " (")
        if math_label:
            label = short
        label_world = tip + direction * 0.16
        name = f"BandScope orientation {role}"
        parts = [("", label, 0.0, 0.0, 1.0)]
        if math_label and label in ("kx", "ky"):
            parts = [("", "k", -0.035, 0.01, 1.0), (" subscript", label[1], 0.045, -0.025, 0.72)]
        axes.append({
            "key": ("X", "Y", "E")[axis], "role": role, "object": name,
            "head_object": name + " head", "label": label, "kind": "arrow",
            "tip_world": tip.tolist(), "label_world": label_world.tolist(),
            "shaft_end_world": base.tolist(), "head_vertices_world": vertices,
            "head_faces": faces,
            "label_parts": [{"object": name + " label" + suffix, "body": body,
                             "world": (label_world + right * dx + up * dy).tolist(),
                             "size_factor": size, "shear": 0.12 if math_label else 0.0}
                            for suffix, body, dx, dy, size in parts],
        })
    return {
        "collection": AXIS_INDICATOR_COLLECTION_NAME, "style": "solid_world_axes",
        "ink_color": [0.0, 0.0, 0.0], "origin_world": origin.tolist(), "axes": axes,
        "placement": {"line_radius": 0.015, "text_size": 0.16},
        "always_generated": True, "visible_by_default": True,
        "purpose": "Three orthogonal solid arrows in scientific world directions.",
    }


class BlenderSceneError(ValueError):
    pass


@dataclass
class BlenderSceneData:
    intensity: np.ndarray
    display_color: np.ndarray
    alpha: np.ndarray
    density: np.ndarray
    full_shape: Tuple[int, int, int]
    data_bounds: Tuple[int, int, int, int, int, int]
    display_spacing: Tuple[float, float, float]
    display_origin: Tuple[float, float, float]
    display_bounds: Tuple[float, float, float, float, float, float]
    blender_spacing: Tuple[float, float, float]
    blender_origin: Tuple[float, float, float]
    blender_bounds: Tuple[float, float, float, float, float, float]
    coords: Dict[str, np.ndarray]
    coord_sources: Dict[str, str]
    coord_units: Dict[str, Optional[str]]
    camera: Dict[str, Any]
    viewport_aspect: float
    resolution: Tuple[int, int]
    axis_titles: Tuple[str, str, str]
    unit_distance_display: float
    unit_distance_blender: float
    source_page_title: str
    source_desc: str
    source_page_id: str
    source_session_id: Optional[str]
    snapshot_id: str
    captured_at: str
    home_frame_index: Optional[int]
    level_info: Dict[str, float]
    cmap_name: str
    levels_params: Tuple[float, float, float]
    locked_range: Optional[Tuple[float, float]]
    include_zero: bool
    opacity_mode: str
    baked_effects: Tuple[str, ...]
    display_style: Dict[str, Any]
    render_quality: Dict[str, Any]
    resolved_range: Optional[Tuple[float, float]]

    @property
    def shape(self) -> Tuple[int, int, int]:
        return tuple(int(v) for v in self.intensity.shape)

    def metadata(self) -> Dict[str, Any]:
        layout = separated_layout(self.blender_bounds)
        return {
            "schema_version": BLENDER_SCENE_SCHEMA,
            "source": {
                "page_title": self.source_page_title,
                "page_id": self.source_page_id,
                "session_id": self.source_session_id,
                "description": self.source_desc,
                "snapshot_id": self.snapshot_id,
                "captured_at": self.captured_at,
                "home_frame_index": self.home_frame_index,
                "kind": "frozen_blender_project" if self.source_session_id is None else "bandscope_page",
            },
            "arrays": {
                "shape": list(self.shape),
                "intensity": "processed float32; NaN/Inf retained in NPZ only",
                "display_color": "linear RGB float32, converted from the BandScope sRGB palette",
                "alpha": "final display alpha before geometric clipping",
                "density": "total extinction coefficient in object-space Blender units; clipped",
            },
            "display_mapping": {
                "source_space": "BandScope 0..200 display coordinates",
                "blender_space": "(display - 100) * 0.01; full domain is centered on 2 Blender units",
                "scale": DISPLAY_TO_BLENDER,
                "spacing": list(self.display_spacing),
                "origin": list(self.display_origin),
                "data_sample_bounds": list(self.display_bounds),
                "full_shape": list(self.full_shape),
                "roi_absolute_start": [self.data_bounds[i] for i in (0, 2, 4)],
                "data_bounds_inclusive": list(self.data_bounds),
            },
            "blender_grid": {
                "transform": "index (i,j,k) -> origin + (i,j,k) * spacing",
                "origin": list(self.blender_origin),
                "spacing": list(self.blender_spacing),
                "material_clip_bounds": list(self.blender_bounds),
                "half_voxel_policy": "Shader clips at sample boundaries; OpenVDB half-voxel support is not added to the scientific domain.",
                "density_space": "OBJECT",
                "weak_signal_clipping": 0.0,
            },
            "guide_frame": {
                "object": GUIDE_FRAME_OBJECT_NAME,
                "collection": GUIDE_FRAME_COLLECTION_NAME,
                "bounds": list(guide_frame_bounds(self.blender_bounds)),
                "axis_frame_bounds": list(AXIS_FRAME_BOUNDS),
                "data_bounds": list(self.blender_bounds),
                "margin": GUIDE_FRAME_MARGIN,
                "always_generated": True,
                "independence": "Generated for every Blender export, whether or not the BandScope axis display is enabled.",
                "purpose": "Detached frame sized to enclose the original data in local coordinates; see layout for translation.",
            },
            "layout": layout,
            "axis_indicator": axis_indicator_metadata(
                self.camera, self.resolution, self.axis_titles,
                layout["orientation"]),
            "coordinates": {
                key: {
                    "values": np.asarray(self.coords[key], dtype=np.float64).tolist(),
                    "source": self.coord_sources.get(key, "index"),
                    "unit": self.coord_units.get(key),
                    "roi_values": [
                        float(self.coords[key][self.data_bounds[axis * 2]]),
                        float(self.coords[key][self.data_bounds[axis * 2 + 1]]),
                    ],
                }
                for axis, key in enumerate(("X", "Y", "E"))
            },
            "axis_titles": list(self.axis_titles),
            "camera": self.camera,
            "viewport_aspect": self.viewport_aspect,
            "resolution": list(self.resolution),
            "pixel_aspect": [1.0, 1.0],
            "opacity": {
                "mode": self.opacity_mode,
                "display_mode": self.display_style.get("display_mode", "legacy"),
                "unit_distance_display": self.unit_distance_display,
                "unit_distance_blender": self.unit_distance_blender,
                "alpha_cap": ALPHA_CAP,
                "formula": "sigma = -log(1-alpha)/unit_distance_blender",
                "independent_parameters": dict(self.display_style.get("opacity") or {}),
            },
            "level_info": dict(self.level_info),
            "bandscope_edit": {
                "format_version": BLENDER_DISPLAY_EDIT_FORMAT,
                "cmap_name": self.cmap_name,
                "levels_params": list(self.levels_params),
                "locked_range": list(self.locked_range) if self.locked_range is not None else None,
                "include_zero": bool(self.include_zero),
                "opacity_mode": self.opacity_mode,
                "display_style": dict(self.display_style),
                "render_quality": dict(self.render_quality),
                "resolved_range": list(self.resolved_range) if self.resolved_range is not None else None,
                "editable": True,
                "capabilities": [
                    "colormap", "levels", "locked_range", "include_zero", "opacity_mode",
                    "normalization", "independent_opacity", "render_quality",
                ],
                "baked_effects": list(self.baked_effects),
                "baked_effects_warning": (
                    "局部增强或插件透明度倍率已烘焙到体素；编辑显示参数会丢弃这些效果。"
                    if self.baked_effects else ""
                ),
            },
            "color_management": {
                "view_transform": "Standard",
                "look": "None",
                "display_device": "sRGB",
                "transparent_background": True,
            },
            "composition": {
                "data_collection": "BandScope Data",
                "camera_collection": "BandScope Camera",
                "parent": "BandScope Root",
                "append": "Append only the data collection; the target scene camera is unchanged.",
            },
        }


def _vector3(value, default) -> Tuple[float, float, float]:
    try:
        result = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        result = tuple(default)
    if len(result) != 3 or not np.all(np.isfinite(result)):
        result = tuple(default)
    return result


def _render_resolution(aspect: float, long_edge: int = DEFAULT_RENDER_LONG_EDGE) -> Tuple[int, int]:
    aspect = float(aspect) if math.isfinite(float(aspect)) and float(aspect) > 0 else 1.0
    if aspect >= 1.0:
        return int(long_edge), max(1, int(round(long_edge / aspect)))
    return max(1, int(round(long_edge * aspect))), int(long_edge)


def guide_frame_bounds(data_bounds) -> Tuple[float, float, float, float, float, float]:
    """辅助盒框的 Blender 坐标边界。

    取坐标轴框与数据体（含额外裁剪）的并集再向外扩 ``GUIDE_FRAME_MARGIN``。
    正常情况下完整 0–200 显示域总是覆盖数据，所以结果是固定的 ±1.1；
    保留并集计算是为了即使数据映射超出坐标轴域，盒框也一定包住数据体，
    并且每个方向都严格大于坐标轴框。
    """
    try:
        values = tuple(float(v) for v in data_bounds)
    except (TypeError, ValueError) as exc:
        raise BlenderSceneError("辅助盒框需要六个有限边界值。") from exc
    if len(values) != 6 or not np.all(np.isfinite(values)):
        raise BlenderSceneError("辅助盒框需要六个有限边界值。")
    bounds = []
    for index in range(6):
        if index % 2 == 0:
            bounds.append(min(AXIS_FRAME_BOUNDS[index], values[index]) - GUIDE_FRAME_MARGIN)
        else:
            bounds.append(max(AXIS_FRAME_BOUNDS[index], values[index]) + GUIDE_FRAME_MARGIN)
    return tuple(bounds)


def _map_camera(camera: Mapping[str, Any]) -> Dict[str, Any]:
    position = np.asarray(_vector3(camera.get("position"), (300.0, -300.0, 300.0)))
    focal = np.asarray(_vector3(camera.get("focal_point"), (100.0, 100.0, 100.0)))
    up = np.asarray(_vector3(camera.get("view_up"), (0.0, 0.0, 1.0)))
    forward = focal - position
    if np.linalg.norm(forward) < 1e-12:
        forward = np.asarray((-1.0, 1.0, -1.0))
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, up)
    if np.linalg.norm(right) < 1e-12:
        up = np.asarray((0.0, 1.0, 0.0)) if abs(forward[2]) > 0.9 else np.asarray((0.0, 0.0, 1.0))
        right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    true_up = np.cross(right, forward)
    true_up /= np.linalg.norm(true_up)
    parallel = bool(camera.get("parallel_projection", False))
    parallel_scale = max(float(camera.get("parallel_scale", 100.0) or 100.0), 1e-9)
    view_angle = min(max(float(camera.get("view_angle", 30.0) or 30.0), 1e-6), 179.0)
    clipping = camera.get("clipping_range", (0.1, 10000.0))
    try:
        near, far = (float(v) for v in clipping)
    except (TypeError, ValueError):
        near, far = 0.1, 10000.0
    near = max(near * DISPLAY_TO_BLENDER, 1e-6)
    far = max(far * DISPLAY_TO_BLENDER, near + 1e-5)
    sensor_height = 24.0
    lens = sensor_height / (2.0 * math.tan(math.radians(view_angle) / 2.0))
    return {
        "position": ((position - 100.0) * DISPLAY_TO_BLENDER).tolist(),
        "focal_point": ((focal - 100.0) * DISPLAY_TO_BLENDER).tolist(),
        "view_up": up.tolist(),
        "rotation_basis_columns": [right.tolist(), true_up.tolist(), (-forward).tolist()],
        "parallel_projection": parallel,
        "projection": "ORTHO" if parallel else "PERSP",
        "parallel_scale_blender": parallel_scale * DISPLAY_TO_BLENDER,
        "ortho_scale": 2.0 * parallel_scale * DISPLAY_TO_BLENDER,
        "view_angle_degrees": view_angle,
        "sensor_fit": "VERTICAL",
        "sensor_height_mm": sensor_height,
        "lens_mm": lens,
        "clip_start": near,
        "clip_end": far,
    }


def camera_basis(camera: Mapping[str, Any]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    columns = camera.get("rotation_basis_columns")
    if columns is not None:
        right, up, backward = (np.asarray(value, dtype=np.float64) for value in columns)
        forward = -backward
    else:
        mapped = _map_camera(camera)
        right, up, backward = (np.asarray(value, dtype=np.float64) for value in mapped["rotation_basis_columns"])
        forward = -backward
    return right, up, forward


def project_point(point, camera: Mapping[str, Any], resolution: Tuple[int, int]) -> Tuple[float, float, float]:
    """返回 Blender 相机下左上角原点的像素坐标和正深度。"""
    width, height = (int(v) for v in resolution)
    position = np.asarray(camera["position"], dtype=np.float64)
    right, up, forward = camera_basis(camera)
    relative = np.asarray(point, dtype=np.float64) - position
    x = float(np.dot(relative, right))
    y = float(np.dot(relative, up))
    depth = float(np.dot(relative, forward))
    if camera.get("parallel_projection", False):
        pixels_per_unit = height / float(camera["ortho_scale"])
        return width / 2.0 + x * pixels_per_unit, height / 2.0 - y * pixels_per_unit, depth
    vertical_tan = math.tan(math.radians(float(camera["view_angle_degrees"])) / 2.0)
    focal_pixels = height / (2.0 * vertical_tan)
    if depth <= 0:
        return math.nan, math.nan, depth
    return width / 2.0 + focal_pixels * x / depth, height / 2.0 - focal_pixels * y / depth, depth


def _srgb_to_linear(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def _display_channels(snapshot, volume: np.ndarray, level_info: Mapping[str, float],
                      display_style=None, unit_distance=None):
    payload = snapshot.payload
    enhancement = payload.get("enhancement")
    style = display_style or (payload.get("display") or {}).get("background_style")
    independent = is_independent(style)
    if enhancement is not None and (enhancement.get("active") or independent):
        color = np.asarray(enhancement["color"], dtype=np.float32)
        alpha = np.asarray(enhancement["alpha"], dtype=np.float32)
        if color.shape != volume.shape or alpha.shape != volume.shape:
            raise BlenderSceneError("局部增强通道与冻结体数据形状不一致。")
    else:
        color, alpha = channels(
            volume, level_info, payload.get("opacity_mode", "linear"),
            style=style, unit_distance=unit_distance,
        )
    multiplier = payload.get("opacity_multiplier")
    if multiplier is not None:
        multiplier = np.asarray(multiplier, dtype=np.float32).reshape(-1)
        if multiplier.size != volume.shape[2]:
            raise BlenderSceneError("插件透明度倍率与能量轴长度不一致。")
        multiplier = np.maximum(np.nan_to_num(multiplier, nan=0.0, posinf=0.0, neginf=0.0), 0.0)
        alpha = np.clip(alpha * multiplier[None, None, :], 0.0, 1.0)
    finite = np.isfinite(volume)
    color = np.where(finite, color, 0.0).astype(np.float32, copy=False)
    alpha = np.where(finite, alpha, 0.0).astype(np.float32, copy=False)
    return color, np.clip(alpha, 0.0, 1.0, dtype=np.float32)


def _palette_linear(color_coordinates: np.ndarray, cmap_name: str) -> np.ndarray:
    palette = colormaps.get_cmap(cmap_name)
    flat = np.asarray(color_coordinates, dtype=np.float32).reshape(-1)
    result = np.empty((flat.size, 3), dtype=np.float32)
    chunk = 262144
    for start in range(0, flat.size, chunk):
        stop = min(start + chunk, flat.size)
        srgb = np.asarray(palette(np.clip(flat[start:stop], 0.0, 1.0)))[:, :3]
        result[start:stop] = _srgb_to_linear(srgb).astype(np.float32)
    return result.reshape(color_coordinates.shape + (3,))


def prepare_blender_scene(snapshot) -> BlenderSceneData:
    if getattr(snapshot, "view_family", None) != "3d" or getattr(snapshot, "view", None) != "3d":
        raise BlenderSceneError("只有冻结的 3D 视图可以导出为 Blender 工程。")
    payload = snapshot.payload or {}
    volume = np.array(payload.get("volume"), dtype=np.float32, copy=True)
    if volume.ndim != 3 or volume.size == 0:
        raise BlenderSceneError("Blender 导出需要非空三维体数据。")
    full_shape = tuple(int(v) for v in payload.get("full_shape", volume.shape))
    data_bounds = tuple(int(v) for v in payload.get("data_bounds", (
        0, volume.shape[0] - 1, 0, volume.shape[1] - 1, 0, volume.shape[2] - 1,
    )))
    if len(full_shape) != 3 or len(data_bounds) != 6:
        raise BlenderSceneError("体数据域或 ROI 元数据无效。")
    expected = tuple(data_bounds[2 * axis + 1] - data_bounds[2 * axis] + 1 for axis in range(3))
    if expected != volume.shape or any(data_bounds[2 * axis] < 0 or data_bounds[2 * axis + 1] >= full_shape[axis] for axis in range(3)):
        raise BlenderSceneError(f"ROI {data_bounds} 与体数据形状 {volume.shape} 不一致。")
    spacing = tuple(float(v) for v in payload.get("spacing", tuple(
        200.0 / (full_shape[axis] - 1) if full_shape[axis] > 1 else 1.0 for axis in range(3)
    )))
    if len(spacing) != 3 or not np.all(np.isfinite(spacing)) or any(v <= 0 for v in spacing):
        raise BlenderSceneError("体数据间距必须为三个正有限数。")
    display = payload.get("display") or {}
    display_style = display.get("background_style") or legacy_display_style(
        snapshot.levels_params, payload.get("opacity_mode", "linear"),
    )
    display_style = resolved_display_style(display_style)
    resolved_range = display.get("resolved_range")
    level_info = dict(payload.get("level_info") or compute_level_info(
        volume, snapshot.levels_params, include_zero=bool(payload.get("include_zero", False)),
        locked_range=snapshot.locked_range, style=display_style, resolved_range=resolved_range,
    ))
    if resolved_range is None:
        resolved_range = (level_info["data_min"], level_info["data_max"])
    unit_display = (volume_display_unit_distance(full_shape) if is_independent(display_style)
                    else volume_unit_distance(volume.shape, full_shape))
    color_coordinates, alpha = _display_channels(
        snapshot, volume, level_info, display_style, unit_display,
    )
    display_color = _palette_linear(color_coordinates, snapshot.cmap_name)
    display_color[~np.isfinite(volume)] = 0.0
    origin = np.asarray([data_bounds[2 * axis] * spacing[axis] for axis in range(3)], dtype=np.float64)
    sample_max = origin + (np.asarray(volume.shape) - 1) * np.asarray(spacing)
    clip = payload.get("clip_render_bounds")
    if clip is None:
        clip_min, clip_max = origin.copy(), sample_max.copy()
    else:
        if len(clip) != 6:
            raise BlenderSceneError("额外裁剪范围无效。")
        clip_min = np.maximum(origin, np.asarray(clip[0::2], dtype=np.float64))
        clip_max = np.minimum(sample_max, np.asarray(clip[1::2], dtype=np.float64))
    clipped_alpha = np.array(alpha, copy=True)
    for axis in range(3):
        coordinates = origin[axis] + np.arange(volume.shape[axis]) * spacing[axis]
        inside = (coordinates >= clip_min[axis] - 1e-9) & (coordinates <= clip_max[axis] + 1e-9)
        shape = [1, 1, 1]
        shape[axis] = -1
        clipped_alpha *= inside.reshape(shape)
    if not math.isfinite(unit_display) or unit_display <= 0:
        unit_display = max(spacing)
    unit_blender = unit_display * DISPLAY_TO_BLENDER
    density = np.zeros(volume.shape, dtype=np.float32)
    flat_alpha = clipped_alpha.reshape(-1)
    flat_density = density.reshape(-1)
    chunk = 262144
    for start in range(0, flat_alpha.size, chunk):
        stop = min(start + chunk, flat_alpha.size)
        bounded = np.minimum(flat_alpha[start:stop], ALPHA_CAP).astype(np.float32)
        flat_density[start:stop] = -np.log1p(-bounded) / np.float32(unit_blender)
    density[~np.isfinite(density)] = 0.0
    coords = {}
    for axis, key in enumerate(("X", "Y", "E")):
        values = np.array(snapshot.coords.get(key, np.arange(full_shape[axis])), dtype=np.float64, copy=True).reshape(-1)
        if values.size != full_shape[axis]:
            raise BlenderSceneError(f"{key} 坐标长度 {values.size} 与完整数据域 {full_shape[axis]} 不一致。")
        coords[key] = values
    aspect = float(payload.get("viewport_aspect", 1.0) or 1.0)
    camera = _map_camera(snapshot.camera or {})
    titles = list(payload.get("axis_titles") or [snapshot.axis_label(key) for key in ("X", "Y", "E")])
    titles += [""] * (3 - len(titles))
    return BlenderSceneData(
        intensity=volume,
        display_color=display_color,
        alpha=np.array(alpha, copy=True),
        density=density,
        full_shape=full_shape,
        data_bounds=data_bounds,
        display_spacing=spacing,
        display_origin=tuple(float(v) for v in origin),
        display_bounds=tuple(float(v) for pair in zip(origin, sample_max) for v in pair),
        blender_spacing=tuple(float(v * DISPLAY_TO_BLENDER) for v in spacing),
        blender_origin=tuple(float((v - 100.0) * DISPLAY_TO_BLENDER) for v in origin),
        blender_bounds=tuple(float(v) for pair in zip(
            (clip_min - 100.0) * DISPLAY_TO_BLENDER,
            (clip_max - 100.0) * DISPLAY_TO_BLENDER,
        ) for v in pair),
        coords=coords,
        coord_sources=dict(snapshot.coord_sources),
        coord_units=dict(snapshot.coord_units),
        camera=camera,
        viewport_aspect=aspect,
        resolution=_render_resolution(aspect),
        axis_titles=tuple(str(v) for v in titles[:3]),
        unit_distance_display=float(unit_display),
        unit_distance_blender=float(unit_blender),
        source_page_title=str(snapshot.source_page_title),
        source_desc=str(snapshot.source_desc),
        source_page_id=str(snapshot.source_page_id),
        source_session_id=getattr(snapshot, "source_session_id", None),
        snapshot_id=str(snapshot.snapshot_id),
        captured_at=str(snapshot.captured_at),
        home_frame_index=snapshot.home_frame_index,
        level_info={key: float(value) for key, value in level_info.items()},
        cmap_name=str(snapshot.cmap_name),
        levels_params=tuple(float(value) for value in snapshot.levels_params),
        locked_range=(tuple(float(value) for value in snapshot.locked_range)
                      if snapshot.locked_range is not None else None),
        include_zero=bool(payload.get("include_zero", False)),
        opacity_mode=str(display_style["opacity_mode"]),
        baked_effects=tuple(
            name for name, present in (
                ("local_enhancement", bool((payload.get("enhancement") or {}).get("active"))),
                ("opacity_multiplier", payload.get("opacity_multiplier") is not None),
            ) if present
        ),
        display_style=display_style,
        render_quality=resolved_render_quality(payload.get("render_quality")),
        resolved_range=tuple(float(value) for value in resolved_range),
    )
