# -*- coding: utf-8 -*-
"""BandScope Blender 工程契约、配套资产读取和冻结快照恢复。"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np

from bandscope.core.blender_recipe import BlenderRecipeDocument, blender_sidecar_path
from bandscope.exporting.blender_materials import (
    BOX_LAYOUTS,
    DEFAULT_BOX_LAYOUT,
    MATERIAL_BY_ID,
    MATERIAL_IDS,
    BlenderMaterialError,
    layout_spec,
    material_spec,
    resolve_parameters,
    resolved_samples,
)
from bandscope.exporting.blender_scene import (
    AXIS_FRAME_BOUNDS,
    BLENDER_DISPLAY_EDIT_FORMAT,
    DISPLAY_TO_BLENDER,
    BlenderSceneError,
)
from bandscope.exporting.blender_volume import (
    VOLUME_MODE_EMISSION,
    VOLUME_MODE_HYBRID_LIT,
    VOLUME_MODE_LABELS,
    BlenderVolumeError,
    resolve_volume_mode,
    resolve_volume_shading,
)
from bandscope.core.display_style import (
    is_independent,
    legacy_display_style,
    legacy_render_quality,
    resolved_display_style,
    resolved_render_quality,
)
from bandscope.exporting.blender_texts import (
    TEXT_BODY,
    TEXT_CAP_HEIGHT,
    TEXT_COLLECTION_NAME,
    TEXT_COLUMN_SPACING,
    TEXT_COLUMNS,
    TEXT_GROUP_PRESET,
    TEXT_PRESETS,
    TEXT_ROW_SPACING,
    text_font_metadata,
    text_style_summaries,
    TEXT_STYLES,
)
from bandscope.exporting.publication_models import PublicationSnapshot
from bandscope.exporting.publication_renderers import compute_level_info

BLENDER_PROJECT_SCHEMA = 11
BLENDER_PROJECT_BOX_MATERIAL_SCHEMA = 8
BLENDER_PROJECT_TEXT_SCHEMA = 9
BLENDER_PROJECT_DISPLAY_SCHEMA = 10
BLENDER_PROJECT_VOLUME_SCHEMA = 11
# schema 7–10 回读和原位更新保留原版本及创作内容，不升级为柔和受光。
BLENDER_PROJECT_MIN_ENTRY_SCHEMA = 7
BLENDER_PROJECT_FORMAT = "bandscope-blender-project"
BLENDER_EDIT_FORMAT = BLENDER_DISPLAY_EDIT_FORMAT
BOX_GROUP_PRESET = "thin_boxes"
PRESET_KEYS = ("coordinates", "guide_frame", "orientation", BOX_GROUP_PRESET, TEXT_GROUP_PRESET)
PRESET_LABELS = {
    "coordinates": "坐标轴及文字",
    "guide_frame": "辅助盒框",
    "orientation": "方向标",
    BOX_GROUP_PRESET: "薄层长方体",
    TEXT_GROUP_PRESET: "文本预设",
}
BOX_PRESETS = MATERIAL_IDS
FRONT_BOX_PRESETS = frozenset(("matte", "satin", "reflection_glass", "optical_glass"))
BOX_COLOR = (1.0, 0.35, 0.05)
BOX_COLORS = {key: BOX_COLOR for key in BOX_PRESETS}
BOX_NAMES = {key: f"orange {key}" for key in BOX_PRESETS}
BOX_THICKNESS = 0.04
BOX_LAYER_SPACING = 0.35
BOX_OPACITY = 0.30


class BlenderProjectError(RuntimeError):
    pass


@dataclass(frozen=True)
class BlenderProjectEntryRequest:
    snapshot: Any
    entry_id: str


@dataclass(frozen=True)
class BoxMaterialConfig:
    """一个薄板的已冻结材质配置；参数已按注册表补齐并通过范围校验。"""

    preset: str
    material_id: str
    parameters: Mapping[str, float]

    @property
    def spec(self):
        return material_spec(self.material_id)

    def settings(self) -> Dict[str, Any]:
        return {"material": self.material_id, "parameters": dict(self.parameters)}


class BlenderProjectRequest:
    """有序的工程级导出请求；单快照只是只有一个条目的特例。"""

    def __init__(self, snapshots: Sequence[Any], presets: Optional[Mapping[str, bool]] = None,
                 *, box_settings: Optional[Mapping[str, Any]] = None,
                 box_layout: Optional[str] = None, volume_mode=VOLUME_MODE_EMISSION):
        if not snapshots:
            raise BlenderProjectError("至少选择一个 3D 页面。")
        try:
            self.volume_mode = resolve_volume_mode(volume_mode)
            self.volume_shading = resolve_volume_shading(self.volume_mode)
        except BlenderVolumeError as exc:
            raise BlenderProjectError(str(exc)) from exc
        entries = []
        used = set()
        for index, value in enumerate(snapshots, 1):
            if isinstance(value, BlenderProjectEntryRequest):
                snapshot, requested_id = value.snapshot, value.entry_id
            else:
                snapshot, requested_id = value, f"entry-{index:03d}"
            entry_id = str(requested_id or f"entry-{index:03d}")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", entry_id) or entry_id in {".", ".."}:
                raise BlenderProjectError(f"条目 ID 不安全：{entry_id!r}")
            if entry_id in used:
                raise BlenderProjectError(f"条目 ID 重复：{entry_id}")
            used.add(entry_id)
            entries.append(BlenderProjectEntryRequest(snapshot, entry_id))
        self.entries = tuple(entries)
        requested = dict(presets or {})
        unknown = set(requested).difference(PRESET_KEYS)
        if unknown:
            raise BlenderProjectError(f"未知 Blender 预设：{sorted(unknown)}")
        self.presets = {key: bool(requested.get(key, True)) for key in PRESET_KEYS}
        self.box_layout = str(box_layout or DEFAULT_BOX_LAYOUT)
        try:
            layout_spec(self.box_layout)
        except BlenderMaterialError as exc:
            raise BlenderProjectError(str(exc)) from exc
        self.boxes = self._resolve_boxes(box_settings)

    @staticmethod
    def _resolve_boxes(box_settings):
        requested = dict(box_settings or {})
        unknown = set(requested).difference(BOX_PRESETS)
        if unknown:
            raise BlenderProjectError(f"未知薄板预设：{sorted(unknown)}")
        result = {}
        for key in BOX_PRESETS:
            value = requested.get(key)
            if value is None:
                material_id, parameters = key, {}
            elif isinstance(value, str):
                material_id, parameters = value, {}
            elif isinstance(value, Mapping):
                extra = set(value).difference({"material", "parameters"})
                if extra:
                    raise BlenderProjectError(f"薄板 {key} 含未知字段：{sorted(extra)}")
                material_id = value.get("material", key)
                parameters = value.get("parameters") or {}
                if not isinstance(parameters, Mapping):
                    raise BlenderProjectError(f"薄板 {key} 的参数必须是映射。")
            else:
                raise BlenderProjectError(f"薄板 {key} 的材质配置类型不受支持。")
            try:
                resolved = resolve_parameters(material_id, parameters)
            except BlenderMaterialError as exc:
                raise BlenderProjectError(str(exc)) from exc
            result[key] = BoxMaterialConfig(key, str(material_id), MappingProxyType(resolved))
        return result

    @property
    def snapshots(self):
        return tuple(entry.snapshot for entry in self.entries)

    @property
    def recommended_samples(self) -> int:
        samples = resolved_samples([box.material_id for box in self.boxes.values()])
        return max(samples, 128) if self.volume_mode == VOLUME_MODE_HYBRID_LIT else samples

    def box_settings(self) -> Dict[str, Dict[str, Any]]:
        return {key: box.settings() for key, box in self.boxes.items()}

    def enabled_boxes(self):
        if not self.presets.get(BOX_GROUP_PRESET):
            return []
        return [self.boxes[key] for key in BOX_PRESETS]

    def enabled_texts(self):
        if not self.presets.get(TEXT_GROUP_PRESET):
            return []
        return list(TEXT_STYLES)

    def material_summary(self) -> Dict[str, Dict[str, Any]]:
        result = {}
        for key, box in self.boxes.items():
            spec = box.spec
            result[key] = {
                "material_id": spec.material_id,
                "label": spec.label,
                "parameters": dict(box.parameters),
                "controls": list(spec.controls),
                "requires_light": spec.requires_light,
                "prefers_data_aligned": spec.prefers_data_aligned,
                "experimental": spec.experimental,
                "emits_by_design": spec.emits_by_design,
                "recommended_samples": spec.recommended_samples,
            }
        return result

    def recipe_settings(self):
        return json.loads(json.dumps({
            "presets": dict(self.presets),
            "volume_shading": copy.deepcopy(self.volume_shading),
            "box_settings": self.box_settings(),
            "box_layout": self.box_layout,
            "box_materials": self.material_summary(),
            "text_styles": text_style_summaries(),
            "render": {
                "samples": self.recommended_samples,
                "volume_bounces": 2 if self.volume_mode == VOLUME_MODE_HYBRID_LIT else 0,
            },
        }))


def request_from_recipe_settings(settings, snapshots):
    if not isinstance(settings, Mapping):
        raise BlenderProjectError("Blender 复现配置的导出设置无效。")
    try:
        request = BlenderProjectRequest(
            snapshots,
            settings.get("presets"),
            box_settings=settings.get("box_settings"),
            box_layout=settings.get("box_layout"),
            volume_mode=(settings.get("volume_shading") or {}).get("mode"),
        )
    except (BlenderProjectError, AttributeError, TypeError, ValueError) as exc:
        raise BlenderProjectError(f"Blender 复现配置的导出设置无效：{exc}") from exc
    if settings != request.recipe_settings():
        raise BlenderProjectError("Blender 复现配置的导出设置与当前版本不一致。")
    return request


@dataclass
class BlenderProjectEntry:
    entry_id: str
    snapshot: PublicationSnapshot
    metadata: Dict[str, Any]
    arrays_path: Path
    vdb_path: Path


@dataclass
class LoadedBlenderProject:
    blend_path: Path
    assets_path: Path
    metadata: Dict[str, Any]
    entries: Tuple[BlenderProjectEntry, ...]
    fingerprints: Dict[str, Dict[str, int]]
    legacy: bool
    schema_version: int = BLENDER_PROJECT_MIN_ENTRY_SCHEMA
    recipe: Optional[dict] = None

    def entry(self, entry_id) -> BlenderProjectEntry:
        for entry in self.entries:
            if entry.entry_id == str(entry_id):
                return entry
        raise KeyError(str(entry_id))

    @property
    def supports_box_materials(self) -> bool:
        project = self.metadata.get("project") or {}
        return self.schema_version >= BLENDER_PROJECT_BOX_MATERIAL_SCHEMA and bool(project.get("box_materials"))

    @property
    def supports_text_presets(self) -> bool:
        project = self.metadata.get("project") or {}
        return self.schema_version >= BLENDER_PROJECT_TEXT_SCHEMA and bool(project.get("text_styles"))

    @property
    def supports_display_styles(self) -> bool:
        return self.schema_version >= BLENDER_PROJECT_DISPLAY_SCHEMA

    @property
    def volume_shading(self):
        if self.schema_version < BLENDER_PROJECT_VOLUME_SCHEMA:
            return resolve_volume_shading()
        record = (self.metadata.get("project") or {}).get("volume_shading") or {}
        return resolve_volume_shading(record.get("mode"), record.get("parameters"))

    @property
    def warnings(self):
        messages = []
        if self.legacy:
            messages.append("旧格式只能有限恢复；无法判断是否含局部增强或插件效果，更新前需要确认烘焙警告。")
        elif not self.supports_box_materials:
            messages.append(
                "工程为 schema 7：长方体仍是平面发光材质；原位更新保留原版本、材质和布局，"
                "不会升级为八材质工程。"
            )
        elif not self.supports_text_presets:
            messages.append(
                "工程为 schema 8：不包含十种文本预设；原位更新保留原版本，"
                "需要文本预设时请重新导出。"
            )
        if not self.legacy and not self.supports_display_styles:
            messages.append(
                f"工程为 schema {self.schema_version}：原位更新保留兼容显示；"
                "需要独立透明度和归一化时请重新导出 schema 10。"
            )
        if self.recipe is None:
            messages.append("未找到 Blender 配套复现配置；现有显示编辑仍可使用，从原始数据重新导出可获得完整复现配置。")
        for entry in self.entries:
            state = entry.snapshot.payload.get("blender_project") or {}
            if state.get("needs_cmap"):
                messages.append(f"条目 {entry.entry_id} 未保存 colormap，请重新选择后再更新。")
            baked = state.get("baked_effects") or ()
            if baked and not self.legacy:
                messages.append(f"条目 {entry.entry_id} 含已烘焙效果：{', '.join(baked)}；编辑会丢弃这些效果。")
        return messages


def default_presets():
    return {key: True for key in PRESET_KEYS}


def _suffix(entry_id, multiple):
    return f" · {entry_id}" if multiple else ""


def entry_composition(entry_id, multiple=False):
    suffix = _suffix(entry_id, multiple)
    return {
        "data_collection": "BandScope Data" + suffix,
        "camera_collection": "BandScope Camera" + suffix,
        "parent": "BandScope Root" + suffix,
        "volume": "BandScope Volume" + suffix,
        "camera": "BandScope Camera" + suffix,
        "name_suffix": suffix,
        "append": "Append only the data collection; the target scene camera is unchanged.",
    }


def _box_layout_slot(layout_id, key):
    if layout_id == "data_aligned":
        return "data_aligned"
    if layout_id == "right_front_groups" and key in FRONT_BOX_PRESETS:
        return "front"
    return "right"


def _box_geometry(scene, request):
    """按布局计算八块薄板的中心与尺寸；不改科学网格、ROI 或父对象位移。"""
    data = np.asarray(scene["blender_grid"]["material_clip_bounds"], dtype=float).reshape(3, 2)
    guide = np.asarray(scene["guide_frame"]["bounds"], dtype=float).reshape(3, 2)
    centers, sizes = [], []
    count = len(BOX_PRESETS)
    if request.box_layout == "data_aligned":
        center_xy = data.mean(axis=1)
        span = np.maximum(data[:, 1] - data[:, 0], BOX_THICKNESS)
        depth = max(float(data[2, 1] - data[2, 0]), 0.0)
        spacing = depth / count
        for index in range(count):
            z = float(data[2, 0]) + spacing * (index + 0.5)
            centers.append([float(center_xy[0]), float(center_xy[1]), z])
            sizes.append([float(span[0]), float(span[1]), BOX_THICKNESS])
    else:
        spacing = BOX_LAYER_SPACING
        grouped = {"right": list(BOX_PRESETS)}
        if request.box_layout == "right_front_groups":
            grouped = {
                "right": [key for key in BOX_PRESETS if key not in FRONT_BOX_PRESETS],
                "front": [key for key in BOX_PRESETS if key in FRONT_BOX_PRESETS],
            }
        by_key = {}
        for slot, keys in grouped.items():
            slot_center = scene["layout"]["slots"][slot]
            height_offset = (
                spacing * 0.5
                if request.box_layout == "right_front_groups" and slot == "front" else 0.0
            )
            for index, key in enumerate(keys):
                by_key[key] = [
                    float(slot_center[0]), float(slot_center[1]),
                    float(slot_center[2] + height_offset
                          + (index - (len(keys) - 1) / 2.0) * spacing),
                ]
        size = [
            float(guide[0, 1] - guide[0, 0]),
            float(guide[1, 1] - guide[1, 0]),
            BOX_THICKNESS,
        ]
        centers = [by_key[key] for key in BOX_PRESETS]
        sizes = [list(size) for _key in BOX_PRESETS]
    return centers, sizes, float(spacing), data


def _intersects_data(center, size, data_bounds):
    center = np.asarray(center, dtype=float)
    half = np.asarray(size, dtype=float) * 0.5
    low, high = center - half, center + half
    return bool(np.all(low <= data_bounds[:, 1] + 1e-9) and np.all(high >= data_bounds[:, 0] - 1e-9))


def _box_metadata(scene, entry_id, multiple, request):
    suffix = _suffix(entry_id, multiple)
    centers, sizes, spacing, data_bounds = _box_geometry(scene, request)
    result = []
    for index, key in enumerate(BOX_PRESETS):
        config = request.boxes[key]
        spec = config.spec
        center, size = centers[index], sizes[index]
        result.append({
            "preset": key,
            "preset_group": BOX_GROUP_PRESET,
            "object": f"BandScope {BOX_NAMES[key]} cuboid{suffix}",
            "material": f"BandScope {BOX_NAMES[key]} cuboid material{suffix}",
            "material_id": spec.material_id,
            "material_label": spec.label,
            "parameters": dict(config.parameters),
            "controls": list(spec.controls),
            "requires_light": bool(spec.requires_light),
            "emits_by_design": bool(spec.emits_by_design),
            "experimental": bool(spec.experimental),
            "recommended_samples": int(spec.recommended_samples),
            "layout": request.box_layout,
            "layout_slot": _box_layout_slot(request.box_layout, key),
            "group_height_offset": (
                BOX_LAYER_SPACING * 0.5
                if request.box_layout == "right_front_groups" and key in FRONT_BOX_PRESETS else 0.0
            ),
            "center": [float(value) for value in center],
            "size": [float(value) for value in size],
            "color": list(BOX_COLORS[key]),
            "opacity": (float(config.parameters["opacity"])
                        if "opacity" in config.parameters else None),
            "thickness": BOX_THICKNESS,
            "layer_center_spacing": spacing,
            "layers_overlap": bool(spacing < BOX_THICKNESS),
            "intersects_data": _intersects_data(center, size, data_bounds),
            "independent_material": True,
            "full_cuboid": True,
        })
    return result


def _box_lighting_metadata(scene, entry_id, multiple, request):
    suffix = _suffix(entry_id, multiple)
    lights = []
    for key in BOX_PRESETS:
        config = request.boxes[key]
        if not request.presets.get(BOX_GROUP_PRESET) or not config.spec.requires_light:
            continue
        lights.append({
            "object": f"BandScope {BOX_NAMES[key]} box light{suffix}",
            "preset": key,
            "preset_group": BOX_GROUP_PRESET,
            "material_id": config.material_id,
        })
    return {
        "enabled": bool(lights),
        "link_method": "cycles_receiver_collection",
        "collection": f"BandScope box lights{suffix}",
        "receiver_collection": f"BandScope lit boxes{suffix}",
        "lights": lights,
        "camera_relative": True,
        "excluded_from_layout": True,
        "note": (
            "Area lights are parented to BandScope Root inside the data collection and use Cycles "
            "light linking so they illuminate only the thin boxes that need external lighting."
        ),
    }


def _text_metadata(scene, entry_id, multiple, request):
    suffix = _suffix(entry_id, multiple)
    center = np.asarray(scene["layout"]["slots"]["bottom"], dtype=float)
    right, up, backward = (
        np.asarray(value, dtype=float) for value in scene["camera"]["rotation_basis_columns"]
    )
    layout = {
        "enabled": bool(request.presets.get(TEXT_GROUP_PRESET)),
        "collection": TEXT_COLLECTION_NAME + suffix,
        "body": TEXT_BODY,
        "cap_height": TEXT_CAP_HEIGHT,
        "layout_slot": "bottom",
        "center": center.tolist(),
        "right": right.tolist(),
        "up": up.tolist(),
        "backward": backward.tolist(),
        "columns": TEXT_COLUMNS,
        "rows": 2,
        "column_spacing": TEXT_COLUMN_SPACING,
        "row_spacing": TEXT_ROW_SPACING,
        "camera_facing": True,
    }
    records = []
    for index, style in enumerate(TEXT_STYLES):
        row, column = divmod(index, TEXT_COLUMNS)
        position = (
            center
            + right * ((column - (TEXT_COLUMNS - 1) / 2.0) * TEXT_COLUMN_SPACING)
            + up * ((0.5 - row) * TEXT_ROW_SPACING)
        )
        record = style.settings()
        record.update({
            "preset_group": TEXT_GROUP_PRESET,
            "object": f"BandScope text {style.style_id}{suffix}",
            "material": f"BandScope text {style.style_id} material{suffix}",
            "body": TEXT_BODY,
            "row": row,
            "column": column,
            "center": position.tolist(),
            "independent_material": True,
        })
        records.append(record)
    lighting = {
        "enabled": bool(request.presets.get(TEXT_GROUP_PRESET)),
        "link_method": "cycles_receiver_collection",
        "collection": f"BandScope text lights{suffix}",
        "receiver_collection": f"BandScope text receivers{suffix}",
        "lights": [{
            "object": f"BandScope text softbox{suffix}",
            "type": "AREA",
            "energy": 45.0,
            "size": 1.8,
            "distance": 2.0,
        }],
        "excluded_from_layout": True,
    }
    return layout, records, lighting


def _text_points(scene):
    layout = scene.get("text_layout") or {}
    if not layout:
        return []
    right, up, backward = (np.asarray(layout[key], dtype=float) for key in ("right", "up", "backward"))
    half_width = TEXT_CAP_HEIGHT * 0.75
    half_height = TEXT_CAP_HEIGHT * 0.6
    half_depth = 0.03
    points = []
    for record in scene.get("text_presets") or ():
        center = np.asarray(record["center"], dtype=float)
        for x, y, z in ((-1, -1, -1), (-1, -1, 1), (-1, 1, -1), (-1, 1, 1),
                        (1, -1, -1), (1, -1, 1), (1, 1, -1), (1, 1, 1)):
            points.append(center + right * x * half_width + up * y * half_height + backward * z * half_depth)
    return points


def _volume_lighting_metadata(scene, entry_id, multiple, request):
    suffix = _suffix(entry_id, multiple)
    enabled = request.volume_mode == VOLUME_MODE_HYBRID_LIT
    bounds = np.asarray(scene["blender_grid"]["material_clip_bounds"], dtype=float).reshape(3, 2)
    center = bounds.mean(axis=1)
    length = max(float(np.max(bounds[:, 1] - bounds[:, 0])), 0.1)
    distance = 2.0 * length
    right, up, backward = (
        np.asarray(value, dtype=float) for value in scene["camera"]["rotation_basis_columns"]
    )
    key_energy = 60.0 * distance * distance
    definitions = (
        ("key", "主光", (-1.0, 1.0, 1.5), key_energy),
        ("fill", "补光", (1.0, 0.3, 1.0), key_energy * 0.25),
        ("rim", "轮廓光", (0.5, 1.0, -1.0), key_energy * 0.5),
    )
    lights = []
    if enabled:
        for role, label, components, energy in definitions:
            direction = right * components[0] + up * components[1] + backward * components[2]
            direction /= np.linalg.norm(direction)
            offset = direction * distance
            lights.append({
                "role": role,
                "label": label,
                "object": f"BandScope volume {role} light{suffix}",
                "type": "AREA",
                "color": [1.0, 1.0, 1.0],
                "energy": float(energy),
                "size": float(length),
                "distance": float(distance),
                "basis_direction": list(components),
                "position": (center + offset).tolist(),
                "target": center.tolist(),
            })
    return {
        "enabled": enabled,
        "link_method": "cycles_receiver_collection",
        "collection": f"BandScope volume lights{suffix}",
        "receiver_collection": f"BandScope volume receivers{suffix}",
        "lights": lights,
        "center": center.tolist(),
        "light_size": float(length),
        "light_distance": float(distance),
        "camera_basis": {
            "right": right.tolist(), "up": up.tolist(), "backward": backward.tolist(),
        },
        "camera_bound": False,
        "excluded_from_layout": True,
        "note": (
            "Initial positions use the export camera basis; lights are then fixed in entry-local space "
            "under BandScope Root. Cycles light linking limits direct illumination, not all indirect light."
        ),
    }


def _shifted(bounds, offset):
    values = np.asarray(bounds, dtype=float).reshape(3, 2)
    return (values + np.asarray(offset, dtype=float).reshape(3, 1)).ravel()


def entry_local_bounds(scene, presets):
    bounds = []
    bounds.append(np.asarray(scene["blender_grid"]["material_clip_bounds"], dtype=float))
    if presets.get("coordinates"):
        bounds.append(_shifted(AXIS_FRAME_BOUNDS, np.asarray(scene["layout"]["coordinates"], dtype=float)))
    if presets.get("guide_frame"):
        guide = np.asarray(scene["guide_frame"]["bounds"], dtype=float).reshape(3, 2)
        guide += np.asarray(scene["layout"]["guide_frame"], dtype=float).reshape(3, 1)
        bounds.append(guide.ravel())
    if presets.get("orientation"):
        indicator = scene["axis_indicator"]
        points = [indicator["origin_world"]]
        for axis in indicator["axes"]:
            points.extend(axis["head_vertices_world"])
            points.extend(part["world"] for part in axis["label_parts"])
        values = np.asarray(points, dtype=float)
        bounds.append(np.column_stack((values.min(axis=0), values.max(axis=0))).ravel())
    for box in scene.get("box_presets") or ():
        if not presets.get(box.get("preset_group", box["preset"])):
            continue
        center = np.asarray(box["center"], dtype=float)
        half = np.asarray(box["size"], dtype=float) * 0.5
        bounds.append(np.column_stack((center - half, center + half)).ravel())
    if presets.get(TEXT_GROUP_PRESET):
        points = _text_points(scene)
        if points:
            values = np.asarray(points, dtype=float)
            bounds.append(np.column_stack((values.min(axis=0), values.max(axis=0))).ravel())
    values = np.asarray(bounds, dtype=float).reshape(-1, 6)
    result = []
    for axis in range(3):
        result.extend((float(values[:, axis * 2].min()), float(values[:, axis * 2 + 1].max())))
    return result


def make_project_metadata(items, request: BlenderProjectRequest, assets_dir_name):
    """组装唯一权威 ``scene.json``；输入是 ``(entry request, scene metadata)``。"""
    if len(items) != len(request.entries):
        raise BlenderProjectError("工程条目数量不一致。")
    multiple = len(items) > 1
    entries = []
    for requested, original in items:
        if requested.entry_id != original.get("entry_id", requested.entry_id):
            raise BlenderProjectError("工程条目顺序或身份不一致。")
        scene = copy.deepcopy(original)
        entry_id = requested.entry_id
        scene["entry_id"] = entry_id
        scene["schema_version"] = BLENDER_PROJECT_SCHEMA
        scene["composition"] = entry_composition(entry_id, multiple)
        suffix = _suffix(entry_id, multiple)
        scene["assets"] = {
            "directory": f"entries/{entry_id}",
            "vdb": f"entries/{entry_id}/volume.vdb",
            "arrays": f"entries/{entry_id}/arrays.npz",
            "readme": "README.txt",
        }
        scene["guide_frame"]["object"] += suffix
        scene["guide_frame"]["collection"] += suffix
        scene["guide_frame"]["enabled"] = request.presets["guide_frame"]
        scene["axis_indicator"]["collection"] += suffix
        scene["axis_indicator"]["enabled"] = request.presets["orientation"]
        for axis in scene["axis_indicator"]["axes"]:
            axis["object"] += suffix
            axis["head_object"] += suffix
            for part in axis["label_parts"]:
                part["object"] += suffix
        scene["box_presets"] = _box_metadata(scene, entry_id, multiple, request)
        scene["box_lighting"] = _box_lighting_metadata(scene, entry_id, multiple, request)
        scene["volume_lighting"] = _volume_lighting_metadata(scene, entry_id, multiple, request)
        text_layout, text_presets, text_lighting = _text_metadata(scene, entry_id, multiple, request)
        scene["text_layout"] = text_layout
        scene["text_presets"] = text_presets
        scene["text_lighting"] = text_lighting
        entries.append(scene)

    layout = {"axis": "X", "gap": 1.0, "entries": {}}
    previous_max = None
    for scene in entries:
        local = entry_local_bounds(scene, request.presets)
        translation_x = 0.0 if previous_max is None else previous_max + layout["gap"] - local[0]
        world = list(local)
        world[0] += translation_x
        world[1] += translation_x
        layout["entries"][scene["entry_id"]] = {
            "translation": [float(translation_x), 0.0, 0.0],
            "local_bounds": local,
            "world_bounds": world,
        }
        previous_max = world[1]
    return {
        "schema_version": BLENDER_PROJECT_SCHEMA,
        "project": {
            "format": BLENDER_PROJECT_FORMAT,
            "display_edit_format": BLENDER_EDIT_FORMAT,
            "entry_order": [scene["entry_id"] for scene in entries],
            "assets": {"directory": str(assets_dir_name), "readme": "README.txt"},
            "presets": dict(request.presets),
            "preset_labels": dict(PRESET_LABELS),
            "volume_shading": copy.deepcopy(request.volume_shading),
            "box_materials": request.material_summary(),
            "box_layout": {
                "id": request.box_layout,
                "label": BOX_LAYOUTS[request.box_layout]["label"],
                "description": BOX_LAYOUTS[request.box_layout]["description"],
                "thickness": BOX_THICKNESS,
                "default_layer_center_spacing": BOX_LAYER_SPACING,
            },
            "box_lighting": {
                "enabled": any(scene["box_lighting"]["enabled"] for scene in entries),
                "link_method": "cycles_receiver_collection",
                "excluded_from_layout": True,
            },
            "text_styles": text_style_summaries(),
            "text_fonts": text_font_metadata(request.presets.get(TEXT_GROUP_PRESET)),
            "text_lighting": {
                "enabled": any(scene["text_lighting"]["enabled"] for scene in entries),
                "link_method": "cycles_receiver_collection",
                "excluded_from_layout": True,
            },
            "render": {
                "device": "CPU",
                "samples": request.recommended_samples,
                "samples_by_material": {
                    key: request.boxes[key].spec.recommended_samples for key in BOX_PRESETS
                },
                "volume_bounces": 2 if request.volume_mode == VOLUME_MODE_HYBRID_LIT else 0,
                "film_transparent": True,
                "view_transform": "Standard",
            },
            "layout": layout,
            "overview_camera": {
                "enabled": multiple,
                "object": "BandScope Overview Camera" if multiple else entries[0]["composition"]["camera"],
                "along_first_entry": True,
                "exclude_light_objects": True,
            },
            "edit_notice": "scene.json is the sole authoritative editing configuration; embedded Blender copies are diagnostic only.",
        },
        "entries": entries,
    }


def _legacy_project_metadata(scene, assets_dir_name):
    entry = copy.deepcopy(scene)
    entry_id = "entry-001"
    entry["entry_id"] = entry_id
    entry["composition"] = entry_composition(entry_id, False)
    entry["assets"] = {
        "directory": ".", "vdb": "volume.vdb", "arrays": "arrays.npz", "readme": "README.txt",
    }
    entry["box_presets"] = []
    presets = default_presets()
    presets[BOX_GROUP_PRESET] = False
    presets[TEXT_GROUP_PRESET] = False
    request = BlenderProjectRequest([object()], presets)
    # Reuse only the geometry/layout calculation; do not rewrite legacy asset paths or names.
    probe = make_project_metadata(
        [(BlenderProjectEntryRequest(object(), entry_id), entry)], request, assets_dir_name,
    )
    probe["entries"] = [entry]
    probe["project"]["layout"] = {
        "axis": "X", "gap": 1.0,
        "entries": {entry_id: {
            "translation": [0.0, 0.0, 0.0],
            "local_bounds": entry_local_bounds(entry, presets),
            "world_bounds": entry_local_bounds(entry, presets),
        }},
    }
    probe["project"]["overview_camera"] = {"enabled": False, "object": "BandScope Camera"}
    probe["schema_version"] = BLENDER_PROJECT_MIN_ENTRY_SCHEMA
    return probe


def _validate_box_configuration(metadata):
    """校验 schema 8 的逐薄板材质、参数与布局；非法配置不进入导出或更新。"""
    project = metadata.get("project") or {}
    layout = project.get("box_layout")
    if not isinstance(layout, Mapping) or layout.get("id") not in BOX_LAYOUTS:
        raise BlenderProjectError("工程缺少有效的薄板布局配置。")
    materials = project.get("box_materials")
    if not isinstance(materials, Mapping) or set(materials) != set(BOX_PRESETS):
        raise BlenderProjectError("工程缺少完整的逐薄板材质配置。")
    resolved = {}
    for key in BOX_PRESETS:
        record = materials.get(key)
        if not isinstance(record, Mapping):
            raise BlenderProjectError(f"薄板 {key} 的材质记录无效。")
        material_id = record.get("material_id")
        spec = MATERIAL_BY_ID.get(str(material_id))
        if spec is None:
            raise BlenderProjectError(f"薄板 {key} 使用未知材质 {material_id!r}。")
        try:
            parameters = resolve_parameters(spec.material_id, record.get("parameters") or {})
        except BlenderMaterialError as exc:
            raise BlenderProjectError(f"薄板 {key} 的参数不合规：{exc}") from exc
        resolved[key] = (spec.material_id, parameters)
    for entry in metadata.get("entries") or ():
        boxes = entry.get("box_presets")
        if not isinstance(boxes, list) or [box.get("preset") for box in boxes] != list(BOX_PRESETS):
            raise BlenderProjectError(f"条目 {entry.get('entry_id')} 的薄板记录不完整。")
        for box in boxes:
            key = box["preset"]
            expected_id, expected_parameters = resolved[key]
            if box.get("material_id") != expected_id:
                raise BlenderProjectError(
                    f"条目 {entry.get('entry_id')} 的薄板 {key} 与工程材质配置不一致。"
                )
            try:
                actual = resolve_parameters(expected_id, box.get("parameters") or {})
            except BlenderMaterialError as exc:
                raise BlenderProjectError(
                    f"条目 {entry.get('entry_id')} 的薄板 {key} 参数不合规：{exc}"
                ) from exc
            if actual != expected_parameters:
                raise BlenderProjectError(
                    f"条目 {entry.get('entry_id')} 的薄板 {key} 参数与工程配置不一致。"
                )
            if box.get("layout") != layout["id"]:
                raise BlenderProjectError(
                    f"条目 {entry.get('entry_id')} 的薄板 {key} 布局与工程配置不一致。"
                )
            for field in ("center", "size"):
                values = box.get(field)
                if not isinstance(values, list) or len(values) != 3 or not np.all(
                        np.isfinite(np.asarray(values, dtype=float))):
                    raise BlenderProjectError(
                        f"条目 {entry.get('entry_id')} 的薄板 {key} {field} 不是有限三维值。"
                    )
    return metadata


def _validate_text_configuration(metadata):
    project = metadata.get("project") or {}
    enabled = bool((project.get("presets") or {}).get(TEXT_GROUP_PRESET))
    if project.get("text_styles") != text_style_summaries():
        raise BlenderProjectError("工程缺少完整、一致的十种文本样式配置。")
    if project.get("text_fonts") != text_font_metadata(enabled):
        raise BlenderProjectError("工程字体资产记录与文本预设开关不一致。")
    expected_styles = {style.style_id: style.settings() for style in TEXT_STYLES}
    for entry in metadata.get("entries") or ():
        entry_id = entry.get("entry_id")
        layout = entry.get("text_layout") or {}
        if (layout.get("body") != TEXT_BODY or layout.get("cap_height") != TEXT_CAP_HEIGHT
                or layout.get("layout_slot") != "bottom" or layout.get("columns") != TEXT_COLUMNS
                or layout.get("rows") != 2 or bool(layout.get("enabled")) != enabled):
            raise BlenderProjectError(f"条目 {entry_id} 的文本布局或字号配置无效。")
        records = entry.get("text_presets")
        if not isinstance(records, list) or [item.get("preset") for item in records] != list(TEXT_PRESETS):
            raise BlenderProjectError(f"条目 {entry_id} 的十种文本预设记录不完整。")
        names = set()
        for index, record in enumerate(records):
            expected = expected_styles[record["preset"]]
            if any(record.get(key) != value for key, value in expected.items()):
                raise BlenderProjectError(f"条目 {entry_id} 的文本样式 {record['preset']} 与注册表不一致。")
            if (record.get("body") != TEXT_BODY or record.get("preset_group") != TEXT_GROUP_PRESET
                    or record.get("row") != index // TEXT_COLUMNS
                    or record.get("column") != index % TEXT_COLUMNS):
                raise BlenderProjectError(f"条目 {entry_id} 的文本 {record['preset']} 不是统一的大写 A 或位置错误。")
            center = record.get("center")
            if not isinstance(center, list) or len(center) != 3 or not np.all(
                    np.isfinite(np.asarray(center, dtype=float))):
                raise BlenderProjectError(f"条目 {entry_id} 的文本 {record['preset']} 中心无效。")
            for field in ("object", "material"):
                name = record.get(field)
                if not isinstance(name, str) or not name or name in names:
                    raise BlenderProjectError(f"条目 {entry_id} 的文本对象或材质命名不独立。")
                names.add(name)
        lighting = entry.get("text_lighting") or {}
        if bool(lighting.get("enabled")) != enabled or len(lighting.get("lights") or ()) != 1:
            raise BlenderProjectError(f"条目 {entry_id} 的文本辅助灯配置无效。")
    return metadata


def _validate_display_configuration(metadata):
    if (metadata.get("project") or {}).get("display_edit_format") != BLENDER_EDIT_FORMAT:
        raise BlenderProjectError("schema 10 工程缺少显示编辑格式 2 配置。")
    for entry in metadata.get("entries") or ():
        edit = entry.get("bandscope_edit") or {}
        if edit.get("format_version") != BLENDER_EDIT_FORMAT:
            raise BlenderProjectError(f"条目 {entry.get('entry_id')} 的显示编辑格式不是 2。")
        if not isinstance(edit.get("display_style"), dict) or not isinstance(edit.get("render_quality"), dict):
            raise BlenderProjectError(f"条目 {entry.get('entry_id')} 缺少显示样式或渲染质量。")
        resolved_display_style(edit["display_style"])
        resolved_render_quality(edit["render_quality"])
        data_range = edit.get("resolved_range")
        if (not isinstance(data_range, (list, tuple)) or len(data_range) != 2
                or not np.all(np.isfinite(np.asarray(data_range, dtype=float)))):
            raise BlenderProjectError(f"条目 {entry.get('entry_id')} 的已解析强度范围无效。")
    return metadata


def _validate_volume_configuration(metadata):
    project = metadata.get("project") or {}
    record = project.get("volume_shading")
    if not isinstance(record, Mapping):
        raise BlenderProjectError("schema 11 工程缺少数据体外观配置。")
    try:
        shading = resolve_volume_shading(record.get("mode"), record.get("parameters"))
    except BlenderVolumeError as exc:
        raise BlenderProjectError(str(exc)) from exc
    hybrid = shading["mode"] == VOLUME_MODE_HYBRID_LIT
    for entry in metadata.get("entries") or ():
        entry_id = entry.get("entry_id")
        lighting = entry.get("volume_lighting")
        if not isinstance(lighting, Mapping) or bool(lighting.get("enabled")) != hybrid:
            raise BlenderProjectError(f"条目 {entry_id} 的数据体灯组开关与外观模式不一致。")
        lights = lighting.get("lights")
        if not isinstance(lights, list) or len(lights) != (3 if hybrid else 0):
            raise BlenderProjectError(f"条目 {entry_id} 的数据体灯光数量与外观模式不一致。")
        if hybrid and {light.get("role") for light in lights} != {"key", "fill", "rim"}:
            raise BlenderProjectError(f"条目 {entry_id} 缺少主光、补光或轮廓光。")
        for field in ("collection", "receiver_collection"):
            if not isinstance(lighting.get(field), str) or not lighting[field]:
                raise BlenderProjectError(f"条目 {entry_id} 的数据体灯光 {field} 无效。")
        for light in lights:
            if (light.get("type") != "AREA" or not isinstance(light.get("object"), str)
                    or not light["object"]):
                raise BlenderProjectError(f"条目 {entry_id} 的数据体灯对象无效。")
            for field in ("position", "target", "color"):
                values = light.get(field)
                if (not isinstance(values, list) or len(values) != 3
                        or not np.all(np.isfinite(np.asarray(values, dtype=float)))):
                    raise BlenderProjectError(f"条目 {entry_id} 的数据体灯 {field} 无效。")
            if (not np.isfinite(float(light.get("energy", 0.0))) or float(light["energy"]) <= 0.0
                    or not np.isfinite(float(light.get("size", 0.0))) or float(light["size"]) <= 0.0):
                raise BlenderProjectError(f"条目 {entry_id} 的数据体灯功率或尺寸无效。")
    return metadata


def canonical_project_metadata(metadata, assets_dir_name):
    version = int(metadata.get("schema_version", 0) or 0)
    if version < 1:
        raise BlenderProjectError("scene.json 缺少有效的 schema_version。")
    if version > BLENDER_PROJECT_SCHEMA:
        raise BlenderProjectError(f"工程 schema {version} 高于当前支持的 {BLENDER_PROJECT_SCHEMA}。")
    if "entries" in metadata:
        if version < BLENDER_PROJECT_MIN_ENTRY_SCHEMA:
            raise BlenderProjectError("工程—条目元数据版本不受支持。")
        if (metadata.get("project") or {}).get("format") != BLENDER_PROJECT_FORMAT:
            raise BlenderProjectError("工程—条目元数据不完整或格式不受支持。")
        result = copy.deepcopy(metadata)
        entries = result.get("entries")
        order = (result.get("project") or {}).get("entry_order")
        if not isinstance(entries, list) or not entries or order != [entry.get("entry_id") for entry in entries]:
            raise BlenderProjectError("工程条目顺序与 entry_order 不一致。")
        ids = [entry.get("entry_id") for entry in entries]
        if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
            raise BlenderProjectError("工程条目 ID 缺失或重复。")
        if version >= BLENDER_PROJECT_BOX_MATERIAL_SCHEMA:
            _validate_box_configuration(result)
        if version >= BLENDER_PROJECT_TEXT_SCHEMA:
            _validate_text_configuration(result)
        if version >= BLENDER_PROJECT_DISPLAY_SCHEMA:
            _validate_display_configuration(result)
        if version >= BLENDER_PROJECT_VOLUME_SCHEMA:
            _validate_volume_configuration(result)
        return result
    return _legacy_project_metadata(metadata, assets_dir_name)


def _safe_asset_path(root, relative, label):
    if not isinstance(relative, str) or not relative:
        raise BlenderProjectError(f"{label} 缺少资产相对路径。")
    pure = PurePosixPath(relative.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts or re.match(r"^[A-Za-z]:", pure.as_posix()):
        raise BlenderProjectError(f"{label} 资产路径越出配套目录：{relative}")
    path = root.joinpath(*pure.parts)
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise BlenderProjectError(f"{label} 资产路径无效：{relative}") from exc
    if not path.is_file() or path.stat().st_size == 0:
        raise BlenderProjectError(f"{label} 资产缺失或为空：{path}")
    return path


def file_fingerprint(path):
    path = Path(path)
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns), "sha256": digest.hexdigest()}


def project_fingerprints(blend_path):
    blend_path = Path(blend_path)
    assets = blend_path.parent / f"{blend_path.stem}.assets"
    result = {"//" + blend_path.name: file_fingerprint(blend_path)}
    recipe = Path(blender_sidecar_path(blend_path))
    if recipe.is_file():
        result["//" + recipe.name] = file_fingerprint(recipe)
    for root, directories, files in os.walk(str(assets)):
        directories.sort()
        for name in sorted(files):
            path = Path(root) / name
            relative = path.relative_to(assets).as_posix()
            result[relative] = file_fingerprint(path)
    return result


def _legacy_levels(info):
    black = float(info.get("black_pos", 0.0)) * 100.0
    white = float(info.get("white_pos", 1.0)) * 100.0
    power = max(float(info.get("gamma_power", 1.0)), 1.0e-6)
    gamma = 50.0 - 50.0 * math.log10(power)
    return (black, min(100.0, max(0.0, gamma)), white)


def restore_display_camera(camera):
    mapped = camera or {}
    scale = float(DISPLAY_TO_BLENDER)
    return {
        "position": tuple(float(value) / scale + 100.0 for value in mapped.get("position", (2, -2, 2))),
        "focal_point": tuple(float(value) / scale + 100.0 for value in mapped.get("focal_point", (0, 0, 0))),
        "view_up": tuple(float(value) for value in mapped.get("view_up", (0, 0, 1))),
        "parallel_projection": bool(mapped.get("parallel_projection", False)),
        "parallel_scale": float(mapped.get("parallel_scale_blender", mapped.get("ortho_scale", 2.0) / 2.0)) / scale,
        "view_angle": float(mapped.get("view_angle_degrees", 30.0)),
        "clipping_range": (
            float(mapped.get("clip_start", 0.001)) / scale,
            float(mapped.get("clip_end", 100.0)) / scale,
        ),
    }


def _snapshot_from_entry(blend_path, entry, arrays_path, legacy):
    source = entry.get("source") or {}
    mapping = entry.get("display_mapping") or {}
    grid = entry.get("blender_grid") or {}
    shape = tuple(int(value) for value in (entry.get("arrays") or {}).get("shape", ()))
    if len(shape) != 3 or any(value <= 0 for value in shape):
        raise BlenderProjectError(f"条目 {entry.get('entry_id')} 的体数据形状无效。")
    with np.load(arrays_path, allow_pickle=False) as arrays:
        required = {"intensity", "display_color", "alpha", "density"}
        if not required.issubset(arrays.files):
            raise BlenderProjectError(f"条目 {entry.get('entry_id')} 的 arrays.npz 缺少 {sorted(required.difference(arrays.files))}。")
        volume = np.array(arrays["intensity"], dtype=np.float32, copy=True)
        if volume.shape != shape:
            raise BlenderProjectError(f"条目 {entry.get('entry_id')} 的强度形状与 scene.json 不一致。")
        for name in ("alpha", "density"):
            value = arrays[name]
            if value.shape != shape:
                raise BlenderProjectError(f"条目 {entry.get('entry_id')} 的 {name} 形状不一致。")
        if arrays["display_color"].shape != shape + (3,):
            raise BlenderProjectError(f"条目 {entry.get('entry_id')} 的 display_color 形状不一致。")
    full_shape = tuple(int(value) for value in mapping.get("full_shape", shape))
    bounds = tuple(int(value) for value in mapping.get("data_bounds_inclusive", (
        0, shape[0] - 1, 0, shape[1] - 1, 0, shape[2] - 1,
    )))
    coords, sources, units = {}, {}, {}
    for axis, key in enumerate(("X", "Y", "E")):
        record = (entry.get("coordinates") or {}).get(key) or {}
        values = np.asarray(record.get("values", np.arange(full_shape[axis])), dtype=np.float64)
        if values.shape != (full_shape[axis],):
            raise BlenderProjectError(f"条目 {entry.get('entry_id')} 的 {key} 坐标长度不一致。")
        coords[key] = values.copy()
        sources[key] = str(record.get("source", "index"))
        units[key] = record.get("unit")
    coords["delay"] = np.asarray([], dtype=np.float64)
    edit = entry.get("bandscope_edit") or {}
    info = dict(entry.get("level_info") or {})
    levels = tuple(float(value) for value in edit.get("levels_params", _legacy_levels(info)))
    if len(levels) != 3:
        raise BlenderProjectError(f"条目 {entry.get('entry_id')} 的三点色阶无效。")
    locked = edit.get("locked_range")
    if locked is None and legacy and info:
        locked = (info.get("data_min"), info.get("data_max"))
    if locked is not None:
        if len(locked) != 2 or not np.all(np.isfinite(np.asarray(locked, dtype=float))):
            raise BlenderProjectError(f"条目 {entry.get('entry_id')} 的锁定范围无效。")
        locked = tuple(float(value) for value in locked)
    include_zero = bool(edit.get("include_zero", False))
    opacity_mode = str(edit.get("opacity_mode", (entry.get("opacity") or {}).get("mode", "linear")))
    cmap_name = str(edit.get("cmap_name", ""))
    baked = list(edit.get("baked_effects") or ())
    if legacy:
        baked = ["unknown_legacy_effects"]
    display_schema = int(entry.get("schema_version", 0)) >= BLENDER_PROJECT_DISPLAY_SCHEMA
    if display_schema:
        display_style = resolved_display_style(edit.get("display_style") or legacy_display_style(
            levels, opacity_mode,
        ))
        levels = tuple(display_style["levels"])
        opacity_mode = str(display_style["opacity_mode"])
        render_quality = resolved_render_quality(edit.get("render_quality"))
        resolved_range = edit.get("resolved_range")
        if resolved_range is None and info:
            resolved_range = (info.get("data_min"), info.get("data_max"))
        if resolved_range is not None:
            if len(resolved_range) != 2 or not np.all(np.isfinite(np.asarray(resolved_range, dtype=float))):
                raise BlenderProjectError(f"条目 {entry.get('entry_id')} 的已解析强度范围无效。")
            resolved_range = tuple(float(value) for value in resolved_range)
        computed_info = compute_level_info(
            volume, levels, include_zero=include_zero, locked_range=locked,
            style=display_style, resolved_range=resolved_range,
        )
    else:
        display_style = legacy_display_style(levels, opacity_mode)
        render_quality = legacy_render_quality()
        resolved_range = None
        computed_info = compute_level_info(volume, levels, include_zero=include_zero, locked_range=locked)
    if info and all(key in info for key in ("black_value", "white_value", "gamma_power")):
        computed_info = info
    clip = tuple(float(value) / DISPLAY_TO_BLENDER + 100.0 for value in grid.get("material_clip_bounds", ()))
    if len(clip) != 6:
        clip = None
    entry_id = str(entry["entry_id"])
    description = str(source.get("description") or source.get("page_title") or entry_id)
    snapshot = PublicationSnapshot(
        snapshot_id=str(source.get("snapshot_id") or entry_id),
        source_page_id=str(source.get("page_id") or entry_id),
        source_page_title=str(source.get("page_title") or entry_id),
        page_kind="blender_project",
        view="3d",
        view_family="3d",
        captured_at=str(source.get("captured_at") or ""),
        cmap_name=cmap_name,
        levels_params=levels,
        locked_range=locked,
        coords=coords,
        coord_sources=sources,
        coord_units=units,
        camera=restore_display_camera(entry.get("camera") or {}),
        source_desc=f"Blender 工程 · {blend_path.name} · {description}",
        home_frame_index=source.get("home_frame_index"),
        source_revision=("blender-project", entry_id, str(source.get("snapshot_id") or "")),
        source_session_id=None,
        payload={
            "volume": volume,
            "data_bounds": bounds,
            "full_shape": full_shape,
            "spacing": tuple(float(value) for value in mapping.get("spacing", tuple(
                200.0 / (size - 1) if size > 1 else 1.0 for size in full_shape
            ))),
            "clip_render_bounds": clip,
            "include_zero": include_zero,
            "opacity_mode": opacity_mode,
            "opacity_multiplier": None,
            "show_axes": True,
            "viewport_aspect": float(entry.get("viewport_aspect", 1.0)),
            "level_info": computed_info,
            "display": {
                "background_style": display_style,
                "resolved_range": resolved_range,
                "state": None,
                "resolved": True,
            },
            "render_quality": render_quality,
            "intensity_label": "Intensity (a.u.)",
            "axis_titles": list(entry.get("axis_titles") or ["X", "Y", "E"]),
            "enhancement": None,
            "blender_project": {
                "blend_path": str(blend_path),
                "entry_id": entry_id,
                "legacy": bool(legacy),
                "baked_effects": baked,
                "needs_cmap": not bool(cmap_name),
                "frozen_result": True,
                "display_schema": display_schema,
                "display_edited": False,
            },
        },
    )
    return snapshot


def load_blender_project(blend_path) -> LoadedBlenderProject:
    blend_path = Path(blend_path).expanduser().resolve()
    if blend_path.suffix.lower() != ".blend" or not blend_path.is_file():
        raise BlenderProjectError("请选择存在的 .blend 工程。")
    with blend_path.open("rb") as handle:
        header = handle.read(16)
    compressed_zstd = header.startswith(b"\x28\xb5\x2f\xfd")
    compressed_gzip = header.startswith(b"\x1f\x8b")
    if compressed_gzip:
        import gzip
        try:
            with gzip.open(blend_path, "rb") as handle:
                header = handle.read(7)
        except OSError as exc:
            raise BlenderProjectError(f"压缩 Blender 工程无法读取：{exc}") from exc
    if not header.startswith(b"BLENDER") and not compressed_zstd:
        raise BlenderProjectError("这不是可识别的 Blender 工程；不支持任意 .blend 导入。")
    assets = blend_path.parent / f"{blend_path.stem}.assets"
    metadata_path = assets / "scene.json"
    if not metadata_path.is_file():
        raise BlenderProjectError(f"缺少同名配套资产或权威配置：{metadata_path}")
    try:
        original = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BlenderProjectError(f"scene.json 无法读取或不是有效 JSON：{exc}") from exc
    legacy = "entries" not in original
    metadata = canonical_project_metadata(original, assets.name)
    if int(metadata.get("schema_version", 0)) >= BLENDER_PROJECT_TEXT_SCHEMA:
        fonts = metadata["project"]["text_fonts"]
        for relative in fonts["files"]:
            _safe_asset_path(assets, relative, "文本预设字体")
        if fonts.get("license"):
            _safe_asset_path(assets, fonts["license"], "字体许可证")
    entries = []
    for entry in metadata["entries"]:
        entry_id = entry["entry_id"]
        assets_info = entry.get("assets") or {}
        arrays = _safe_asset_path(assets, assets_info.get("arrays"), f"条目 {entry_id} 数组")
        _safe_asset_path(assets, assets_info.get("vdb"), f"条目 {entry_id} VDB")
        snapshot = _snapshot_from_entry(blend_path, entry, arrays, legacy)
        entries.append(BlenderProjectEntry(
            entry_id, snapshot, entry, arrays,
            _safe_asset_path(assets, assets_info.get("vdb"), f"条目 {entry_id} VDB"),
        ))
    recipe_path = Path(blender_sidecar_path(blend_path))
    recipe = None
    if recipe_path.is_file():
        try:
            recipe = BlenderRecipeDocument.load(recipe_path).data
        except ValueError as exc:
            raise BlenderProjectError(str(exc)) from exc
        if [entry["entry_id"] for entry in recipe["entries"]] != metadata["project"]["entry_order"]:
            raise BlenderProjectError("Blender 复现配置与工程的条目顺序不一致。")
        settings_request = request_from_recipe_settings(
            recipe["export_settings"],
            [BlenderProjectEntryRequest(entry.snapshot, entry.entry_id) for entry in entries],
        )
        project = metadata["project"]
        settings = settings_request.recipe_settings()
        if (settings["presets"] != project.get("presets")
                or settings["volume_shading"] != project.get("volume_shading")
                or settings["box_layout"] != (project.get("box_layout") or {}).get("id")
                or settings["box_materials"] != project.get("box_materials")):
            raise BlenderProjectError("Blender 复现配置与工程的实际导出设置不一致。")
    return LoadedBlenderProject(
        blend_path, assets, metadata, tuple(entries), project_fingerprints(blend_path), legacy,
        schema_version=int(metadata.get("schema_version", BLENDER_PROJECT_MIN_ENTRY_SCHEMA)),
        recipe=recipe,
    )


def update_snapshot_display(snapshot, *, cmap_name, levels_params, locked_range, include_zero,
                            opacity_mode, display_style=None, render_quality=None):
    """在冻结条目上重算全局显示，不触碰任何活动数据源的锁定作用域。"""
    if not str(cmap_name).strip():
        raise BlenderProjectError("旧工程缺少 colormap；请先选择色带。")
    levels = tuple(float(value) for value in levels_params)
    if len(levels) != 3:
        raise BlenderProjectError("色阶需要黑点、gamma 和白点三个参数。")
    state = snapshot.payload.setdefault("blender_project", {})
    display_schema = bool(state.get("display_schema", False))
    current = (snapshot.payload.get("display") or {}).get("background_style")
    if display_style is not None:
        selected = display_style
    elif is_independent(current):
        selected = copy.deepcopy(current)
        selected["levels"] = levels
        selected["opacity_mode"] = str(opacity_mode)
    else:
        selected = legacy_display_style(levels, opacity_mode)
    selected = resolved_display_style(selected)
    if not display_schema and is_independent(selected):
        raise BlenderProjectError("schema 7–9 工程不能原位启用独立显示，请重新导出 schema 10。")
    quality = resolved_render_quality(
        render_quality if render_quality is not None else snapshot.payload.get("render_quality")
    )
    if not display_schema and quality["mode"] == "explicit":
        raise BlenderProjectError("schema 7–9 工程保留原渲染质量，请重新导出后调整质量。")
    snapshot.cmap_name = str(cmap_name)
    snapshot.levels_params = tuple(selected["levels"])
    snapshot.locked_range = (tuple(float(value) for value in locked_range)
                             if locked_range is not None else None)
    snapshot.payload["include_zero"] = bool(include_zero)
    snapshot.payload["opacity_mode"] = str(selected["opacity_mode"])
    snapshot.payload["level_info"] = compute_level_info(
        snapshot.payload["volume"], snapshot.levels_params,
        include_zero=bool(include_zero), locked_range=snapshot.locked_range,
        **({"style": selected} if display_schema else {}),
    )
    info = snapshot.payload["level_info"]
    snapshot.payload["display"] = {
        "background_style": selected,
        "resolved_range": (info["data_min"], info["data_max"]),
        "state": None,
        "resolved": True,
    }
    snapshot.payload["render_quality"] = quality
    state["display_edited"] = True
    state["needs_cmap"] = False
    snapshot.payload["enhancement"] = None
    snapshot.payload["opacity_multiplier"] = None
    return snapshot


def merge_updated_entry(original, generated):
    """更新科学数据和编辑状态，保留对象身份、布局、预设、材质与灯光。"""
    result = copy.deepcopy(generated)
    for key in ("entry_id", "assets", "composition", "layout", "box_presets", "box_lighting",
                "volume_lighting", "text_layout", "text_presets", "text_lighting",
                "guide_frame", "axis_indicator"):
        if key in original:
            result[key] = copy.deepcopy(original[key])
    if int(original.get("schema_version", 0)) < BLENDER_PROJECT_DISPLAY_SCHEMA:
        generated_edit = result.get("bandscope_edit") or {}
        legacy_keys = {
            "format_version", "cmap_name", "levels_params", "locked_range", "include_zero",
            "opacity_mode", "editable", "capabilities", "baked_effects", "baked_effects_warning",
        }
        result["bandscope_edit"] = {
            key: copy.deepcopy(value) for key, value in generated_edit.items() if key in legacy_keys
        }
        result["bandscope_edit"]["format_version"] = 1
        result["bandscope_edit"]["capabilities"] = [
            "colormap", "levels", "locked_range", "include_zero", "opacity_mode",
        ]
        opacity = result.get("opacity") or {}
        opacity.pop("display_mode", None)
        opacity.pop("independent_parameters", None)
    previous_edit = original.get("bandscope_edit") or {}
    discarded = list(previous_edit.get("baked_effects") or ())
    if discarded:
        result.setdefault("bandscope_edit", {})["discarded_baked_effects"] = discarded
        result["bandscope_edit"]["baked_effects"] = []
        result["bandscope_edit"]["baked_effects_warning"] = ""
    return result


def merged_project_metadata(project: LoadedBlenderProject, generated_entries):
    metadata = copy.deepcopy(project.metadata)
    by_id = {entry["entry_id"]: entry for entry in metadata["entries"]}
    for entry_id, generated in generated_entries.items():
        if entry_id not in by_id:
            raise BlenderProjectError(f"更新条目不属于当前工程：{entry_id}")
        by_id[entry_id] = merge_updated_entry(by_id[entry_id], generated)
    metadata["entries"] = [by_id[entry_id] for entry_id in metadata["project"]["entry_order"]]
    # 保留原工程版本：schema 7–10 的原位更新不得补入后续材质、文本、独立显示或数据体灯光。
    project_version = int(project.metadata.get("schema_version", BLENDER_PROJECT_SCHEMA))
    metadata["schema_version"] = project_version
    for entry in metadata["entries"]:
        entry["schema_version"] = project_version
    return metadata


def prepare_scene_for_update(snapshot):
    if (snapshot.payload.get("blender_project") or {}).get("needs_cmap"):
        raise BlenderProjectError("旧工程缺少 colormap；请先选择色带。")
    try:
        from bandscope.exporting.blender_scene import prepare_blender_scene
        return prepare_blender_scene(snapshot)
    except BlenderSceneError as exc:
        raise BlenderProjectError(str(exc)) from exc
