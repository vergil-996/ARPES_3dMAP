# -*- coding: utf-8 -*-
"""Run inside Blender: inspect and render schema-8 thin-box materials.

Usage (host runner passes these arguments after ``--``):
  blender --background --factory-startup --python blender_material_scene.py -- \
      inspect --blend project.blend --report report.json [--render out.png]
      [--samples 64] [--long-edge 640] [--require-schema 8]
  blender ... -- append --base base.blend --append other.blend \
      --output combined.blend --report report.json --collection "BandScope Data · entry-002"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bandscope.exporting.blender_materials import material_spec  # noqa: E402


def _bounds(collection, *, include_light=False):
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    excluded = {"EMPTY", "CAMERA"} if include_light else {"EMPTY", "CAMERA", "LIGHT"}
    points = [
        tuple(obj.matrix_world @ Vector(corner))
        for source in collection.all_objects if source.type not in excluded
        for obj in (source.evaluated_get(depsgraph),) for corner in obj.bound_box
    ]
    if not points:
        raise RuntimeError(f"Collection {collection.name} 没有可测量对象")
    values = np.asarray(points, dtype=np.float64)
    return np.column_stack((values.min(axis=0), values.max(axis=0))).ravel()


def _mesh_center_size(obj):
    coordinates = [vertex.co for vertex in obj.data.vertices]
    low = Vector(tuple(min(value[axis] for value in coordinates) for axis in range(3)))
    high = Vector(tuple(max(value[axis] for value in coordinates) for axis in range(3)))
    return (low + high) * 0.5, high - low


def _node_types(material):
    return {node.bl_idname for node in material.node_tree.nodes}


def _emission_nodes(material):
    return [node for node in material.node_tree.nodes if node.bl_idname == "ShaderNodeEmission"]


def _check_material(box):
    spec = material_spec(box["material_id"])
    material = bpy.data.objects[box["object"]].data.materials[0]
    types = _node_types(material)
    missing = [name for name in spec.node_contract if name not in types]
    if missing:
        raise RuntimeError(f"{box['preset']} 缺少节点 {missing}")
    parameters = dict(box["parameters"])
    emission = _emission_nodes(material)
    compensation = float(parameters.get("emission_compensation", 0.0) or 0.0)
    expected_emission = bool(spec.emits_by_design or compensation > 0.0)
    if bool(emission) != expected_emission:
        raise RuntimeError(
            f"{box['preset']} 的薄板自发光与契约不符："
            f"actual={bool(emission)} expected={expected_emission}"
        )
    mix = material.node_tree.nodes.get("BandScope opacity")
    if "opacity" in parameters:
        if mix is None or mix.bl_idname != "ShaderNodeMixShader":
            raise RuntimeError(f"{box['preset']} 缺少单表面不透明度混合节点")
        actual = float(mix.inputs[0].default_value)
        if abs(actual - float(parameters["opacity"])) > 1e-6:
            raise RuntimeError(f"{box['preset']} 的不透明度 {actual} 与配置不一致")
    if spec.material_id == "holographic":
        edge = material.node_tree.nodes.get("BandScope edge opacity")
        if edge is None or abs(float(edge.inputs[0].default_value) - float(parameters["edge_opacity"])) > 1e-6:
            raise RuntimeError(f"{box['preset']} 的掠射角上限不正确")
        weight = material.node_tree.nodes.get("BandScope holographic weight")
        if weight is None or weight.operation != "MULTIPLY":
            raise RuntimeError(f"{box['preset']} 的全息权重没有屏蔽背面")
    if spec.material_id == "data_translucent":
        if "BandScope translucency" not in material.node_tree.nodes:
            raise RuntimeError(f"{box['preset']} 缺少漫透射混合节点")
    if spec.material_id == "transmission_filter":
        transparent = material.node_tree.nodes.get("BandScope transmission filter")
        if transparent is None:
            raise RuntimeError(f"{box['preset']} 缺少透射窗节点")
    if spec.material_id == "optical_glass":
        glass = material.node_tree.nodes.get("BandScope Glass")
        if glass is None or abs(float(glass.inputs["IOR"].default_value) - float(parameters["ior"])) > 1e-6:
            raise RuntimeError(f"{box['preset']} 的玻璃 IOR 不正确")
        volume = material.node_tree.nodes.get("BandScope Volume Absorption")
        if volume is None or abs(float(volume.inputs["Density"].default_value) - float(parameters["absorption_density"])) > 1e-6:
            raise RuntimeError(f"{box['preset']} 的厚度吸收不正确")
    return {
        "material": material.name,
        "node_types": sorted(types),
        "opacity": box.get("opacity"),
        "emission": {"effective": bool(emission), "count": len(emission)},
    }


def _check_volume_material(volume):
    material = volume.data.materials[0]
    nodes = material.node_tree.nodes
    if nodes.get("Density multiplier") is None or nodes.get("Color adjustment") is None:
        raise RuntimeError("体材质的 Density multiplier / Color adjustment 节点被改动")
    coefficients = next(
        (node for node in nodes if node.bl_idname == "ShaderNodeVolumeCoefficients"), None
    )
    if coefficients is None or any(
        abs(float(value)) > 1e-9
        for value in coefficients.inputs["Scatter Coefficients"].default_value
    ):
        raise RuntimeError("体材质不再是无散射吸收＋发射")
    return {"density_multiplier": True, "color_adjustment": True, "no_scattering": True}


def _check_entry(scene, entry, project):
    entry_id = entry["entry_id"]
    composition = entry["composition"]
    presets = project["presets"]
    collection = bpy.data.collections.get(composition["data_collection"])
    root = bpy.data.objects.get(composition["parent"])
    if collection is None or root is None:
        raise RuntimeError(f"条目 {entry_id} 的数据 Collection 或根对象缺失")
    volume = bpy.data.objects.get(composition["volume"])
    if volume is None or volume.type != "VOLUME" or volume.parent != root:
        raise RuntimeError(f"条目 {entry_id} 的体对象缺失或父对象错误")
    volume_material = _check_volume_material(volume)
    layout = project["layout"]["entries"][entry_id]
    actual_bounds = _bounds(collection)
    expected_bounds = np.asarray(layout["world_bounds"], dtype=np.float64)
    if not np.allclose(actual_bounds, expected_bounds, atol=1e-4):
        raise RuntimeError(
            f"条目 {entry_id} 的科学排版边界与布局记录不一致（灯光不得参与此边界）："
            f"{actual_bounds.tolist()} != {expected_bounds.tolist()}"
        )
    lighting = entry.get("box_lighting") or {}
    box_records = []
    material_names, mesh_names = [], []
    for box in entry.get("box_presets") or ():
        if not presets.get(box.get("preset_group", box["preset"])):
            if bpy.data.objects.get(box["object"]) is not None:
                raise RuntimeError(f"未勾选的薄板被创建：{box['preset']}")
            continue
        obj = bpy.data.objects.get(box["object"])
        if obj is None or obj.type != "MESH" or obj.parent != root:
            raise RuntimeError(f"薄板 {box['preset']} 缺失或父对象不正确")
        if len(obj.data.vertices) != 8 or len(obj.data.polygons) != 6:
            raise RuntimeError(f"薄板 {box['preset']} 不是封闭六面薄板")
        if len(obj.data.materials) != 1 or obj.data.materials[0].name != box["material"]:
            raise RuntimeError(f"薄板 {box['preset']} 材质不独立")
        center, size = _mesh_center_size(obj)
        if (center - Vector(box["center"])).length > 1e-5:
            raise RuntimeError(f"薄板 {box['preset']} 中心与布局配置不一致")
        if (size - Vector(box["size"])).length > 1e-5:
            raise RuntimeError(f"薄板 {box['preset']} 尺寸与布局配置不一致")
        if abs(size.z - 0.04) > 1e-5:
            raise RuntimeError(f"薄板 {box['preset']} 厚度被改变：{size.z}")
        spec = material_spec(box["material_id"])
        if project["box_layout"]["id"] == "right_front_groups":
            expected_slot = "front" if spec.requires_light else "right"
            if box.get("layout_slot") != expected_slot:
                raise RuntimeError(f"薄板 {box['preset']} 的方位分组不正确")
            slot_center = entry["layout"]["slots"][expected_slot]
            if any(abs(box["center"][axis] - slot_center[axis]) > 1e-5 for axis in (0, 1)):
                raise RuntimeError(f"薄板 {box['preset']} 没有位于预期的{expected_slot}方位")
        material = _check_material(box)
        material_names.append(box["material"])
        mesh_names.append(obj.data.name)
        data = np.asarray(entry["blender_grid"]["material_clip_bounds"], dtype=np.float64).reshape(3, 2)
        low = np.asarray(box["center"]) - np.asarray(box["size"]) * 0.5
        high = np.asarray(box["center"]) + np.asarray(box["size"]) * 0.5
        intersects = bool(np.all(low <= data[:, 1] + 1e-9) and np.all(high >= data[:, 0] - 1e-9))
        if intersects != bool(box.get("intersects_data")):
            raise RuntimeError(f"薄板 {box['preset']} 的相交关系与配置不符")
        if project["box_layout"]["id"] == "data_aligned" and spec.prefers_data_aligned:
            if not intersects:
                raise RuntimeError(f"透射薄板 {box['preset']} 在同轴布局中没有覆盖数据体")
        box_records.append({
            "preset": box["preset"],
            "material_id": box["material_id"],
            "material_label": box["material_label"],
            "object": obj.name,
            "mesh": obj.data.name,
            "color": list(box["color"]),
            "parameters": dict(box["parameters"]),
            "layout_slot": box.get("layout_slot"),
            "group_height_offset": float(box.get("group_height_offset", 0.0)),
            "center": [float(value) for value in center],
            "size": [float(value) for value in size],
            "thickness": float(size.z),
            "intersects_data": intersects,
            "requires_light": bool(spec.requires_light),
            "material_report": material,
        })
    if project["box_layout"]["id"] == "right_front_groups":
        right_z = sorted(box["center"][2] for box in box_records if box["layout_slot"] == "right")
        front_z = sorted(box["center"][2] for box in box_records if box["layout_slot"] == "front")
        if (len(right_z) != 4 or len(front_z) != 4
                or any(abs(front - right - 0.175) > 1e-5 for right, front in zip(right_z, front_z))):
            raise RuntimeError(f"条目 {entry_id} 的前侧薄板没有相对右侧上移半层")
    if len(set(material_names)) != len(material_names) or len(set(mesh_names)) != len(mesh_names):
        raise RuntimeError(f"条目 {entry_id} 的薄板共享材质或网格")
    lights = []
    expected_lights = {
        record["preset"]: record for record in (lighting.get("lights") or ())
        if presets.get(record.get("preset_group", record["preset"]))
    }
    receiver = bpy.data.collections.get(lighting.get("receiver_collection", ""))
    receiver_objects = sorted(obj.name for obj in receiver.objects) if receiver else []
    for preset, record in expected_lights.items():
        obj = bpy.data.objects.get(record["object"])
        if obj is None or obj.type != "LIGHT" or obj.parent != root:
            raise RuntimeError(f"条目 {entry_id} 的辅助灯 {record['object']} 缺失或父对象错误")
        if obj.data.type != "AREA" or float(obj.data.energy) <= 0.0 or float(obj.data.size) <= 0.0:
            raise RuntimeError(f"条目 {entry_id} 的辅助灯 {record['object']} 不是有效 Area 灯")
        linked = obj.light_linking.receiver_collection
        if linked is None:
            raise RuntimeError(f"条目 {entry_id} 的辅助灯 {record['object']} 没有灯光链接接收者")
        lights.append({
            "object": obj.name, "preset": preset, "energy": float(obj.data.energy),
            "size": float(obj.data.size), "receiver_collection": linked.name,
            "receiver_objects": sorted(item.name for item in linked.objects),
        })
    box_lights = [obj.name for obj in collection.all_objects if obj.type == "LIGHT"]
    if sorted(box_lights) != sorted(record["object"] for record in expected_lights.values()):
        raise RuntimeError(f"条目 {entry_id} 的 LIGHT 对象集合与需要照明的薄板不一致：{box_lights}")
    including_lights = _bounds(collection, include_light=True)
    lights_expand_bounds = not np.allclose(including_lights, actual_bounds, atol=1e-6)
    return {
        "entry_id": entry_id,
        "collection": collection.name,
        "layout_bounds": expected_bounds.tolist(),
        "actual_non_light_bounds": actual_bounds.tolist(),
        "bounds_including_lights": including_lights.tolist(),
        "lights_expand_bounds": bool(lights_expand_bounds),
        "boxes": box_records,
        "lights": lights,
        "receiver_collection": receiver.name if receiver else None,
        "receiver_objects": receiver_objects,
        "light_object_count": len(box_lights),
        "data_collection_hide_render": bool(collection.hide_render),
        "volume_material": volume_material,
    }


def _render(scene, path, long_edge, samples):
    first = scene.camera
    if first is None:
        raise RuntimeError("场景没有活动相机")
    width, height = scene.render.resolution_x, scene.render.resolution_y
    aspect = width / max(height, 1)
    if aspect >= 1.0:
        scene.render.resolution_x = int(long_edge)
        scene.render.resolution_y = max(1, int(round(long_edge / aspect)))
    else:
        scene.render.resolution_x = max(1, int(round(long_edge * aspect)))
        scene.render.resolution_y = int(long_edge)
    scene.render.resolution_percentage = 100
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = int(samples)
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("CPU 渲染没有生成图像")


def inspect(args):
    blend = Path(args.blend).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    assets = blend.parent / f"{blend.stem}.assets"
    metadata = json.loads((assets / "scene.json").read_text(encoding="utf-8"))
    schema = int(metadata.get("schema_version", 0))
    if args.require_schema and schema != int(args.require_schema):
        raise RuntimeError(f"工程 schema {schema}，期望 {args.require_schema}")
    project = metadata["project"]
    scene = bpy.context.scene
    entries = [_check_entry(scene, entry, project) for entry in metadata["entries"]]
    material_names = [box["material_report"]["material"] for entry in entries for box in entry["boxes"]]
    if len(set(material_names)) != len(material_names):
        raise RuntimeError("不同条目或薄板共享材质")
    overview = project.get("overview_camera") or {}
    overview_frame = None
    if overview.get("enabled"):
        active = bpy.data.objects.get(overview.get("object"))
        if active is None or scene.camera != active:
            raise RuntimeError("多页工程的总览相机缺失或不是活动相机")
        corners = [
            Vector((x, y, z))
            for record in project["layout"]["entries"].values()
            for x in record["world_bounds"][0:2]
            for y in record["world_bounds"][2:4]
            for z in record["world_bounds"][4:6]
        ]
        projected = [world_to_camera_view(scene, active, corner) for corner in corners]
        overview_frame = {
            "covers_data_bounds": all(
                -1e-5 <= point.x <= 1.00001 and -1e-5 <= point.y <= 1.00001 and point.z > 0
                for point in projected
            ),
        }
        if not overview_frame["covers_data_bounds"]:
            raise RuntimeError("总览相机没有覆盖排除灯光后的数据组边界")
    render_path = None
    if args.render:
        render_path = Path(args.render).resolve()
        _render(scene, render_path, int(args.long_edge), int(args.samples))
    report = {
        "blend": str(blend),
        "blend_reopened": True,
        "schema_version": schema,
        "layout": project.get("box_layout"),
        "render_samples": int(scene.cycles.samples),
        "requested_render_samples": int(args.samples) if args.render else None,
        "transparent_background": bool(scene.render.film_transparent),
        "view_transform": scene.view_settings.view_transform,
        "entries": entries,
        "overview_camera": overview_frame,
        "render": str(render_path) if render_path else None,
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def append(args):
    base = Path(args.base).resolve()
    other = Path(args.append).resolve()
    output = Path(args.output).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(base))
    scene = bpy.context.scene
    camera_before = scene.camera.name if scene.camera else None
    directory = str(other) + "/Collection"
    bpy.ops.wm.append(filepath=str(other), directory=directory, filename=args.collection)
    before = len([obj for obj in bpy.data.objects if obj.type == "VOLUME"])
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=str(output))
    scene = bpy.context.scene
    volumes = [obj for obj in bpy.data.objects if obj.type == "VOLUME"]
    lights = [obj for obj in bpy.data.objects if obj.type == "LIGHT"]
    linking = []
    for light in lights:
        receiver = light.light_linking.receiver_collection
        linking.append({
            "light": light.name,
            "receiver_collection": receiver.name if receiver else None,
            "receiver_objects": sorted(obj.name for obj in receiver.objects) if receiver else [],
        })
    report = {
        "base": str(base), "appended": str(other), "output": str(output),
        "collection": args.collection,
        "saved_and_reopened": True,
        "camera_unchanged": (scene.camera.name if scene.camera else None) == camera_before,
        "volume_count_after": len(volumes),
        "light_count_after": len(lights),
        "light_linking_after": linking,
        "all_lights_linked": all(item["receiver_collection"] for item in linking),
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv=None):
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--blend", required=True)
    inspect_parser.add_argument("--report", required=True)
    inspect_parser.add_argument("--render")
    inspect_parser.add_argument("--samples", type=int, default=32)
    inspect_parser.add_argument("--long-edge", type=int, default=640)
    inspect_parser.add_argument("--require-schema", type=int, default=8)
    append_parser = subparsers.add_parser("append")
    append_parser.add_argument("--base", required=True)
    append_parser.add_argument("--append", required=True)
    append_parser.add_argument("--output", required=True)
    append_parser.add_argument("--report", required=True)
    append_parser.add_argument("--collection", required=True)
    args = parser.parse_args(argv)
    if args.command == "inspect":
        inspect(args)
    else:
        append(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
