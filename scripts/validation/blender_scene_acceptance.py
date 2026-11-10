"""Run inside Blender to inspect BandScope projects, render, or append collections."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import traceback

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
import numpy as np
import openvdb


def _load_volumes():
    volumes = [obj for obj in bpy.context.scene.objects if obj.type == "VOLUME"]
    for volume in volumes:
        if not volume.data.grids.load():
            raise RuntimeError(f"无法载入 {volume.name} 的 VDB：{volume.data.filepath}")
        resolved = Path(bpy.path.abspath(volume.data.filepath)).resolve()
        if not resolved.is_file():
            raise RuntimeError(f"VDB 相对路径解析失败：{resolved}")
    return volumes


def _projection_checks(scene, metadata, long_edge):
    aspect = float(metadata["viewport_aspect"])
    if aspect >= 1.0:
        width, height = int(long_edge), int(round(long_edge / aspect))
    else:
        width, height = int(round(long_edge * aspect)), int(long_edge)
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = 1.0
    scene.render.pixel_aspect_y = 1.0
    settings = metadata["camera"]
    offset = np.asarray(metadata.get("_world_translation", (0.0, 0.0, 0.0)), dtype=np.float64)
    position = np.asarray(settings["position"], dtype=np.float64) + offset
    right, up, backward = (np.asarray(value, dtype=np.float64) for value in settings["rotation_basis_columns"])
    forward = -backward
    bounds = metadata["blender_grid"]["material_clip_bounds"]
    corners = [(x, y, z) for x in bounds[0:2] for y in bounds[2:4] for z in bounds[4:6]]
    points = [(-1.0, -1.0, -1.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.23, -0.41, 0.67), *corners]
    records = []
    for local_point in points:
        point = np.asarray(local_point, dtype=np.float64) + offset
        relative = point - position
        x = float(np.dot(relative, right))
        y = float(np.dot(relative, up))
        depth = float(np.dot(relative, forward))
        if settings["parallel_projection"]:
            scale = height / float(settings["ortho_scale"])
            expected = np.array([width / 2.0 + x * scale, height / 2.0 - y * scale])
        else:
            focal = height / (2.0 * math.tan(math.radians(float(settings["view_angle_degrees"])) / 2.0))
            expected = np.array([width / 2.0 + focal * x / depth, height / 2.0 - focal * y / depth])
        actual = world_to_camera_view(scene, scene.camera, Vector(point))
        actual_pixels = np.array([actual.x * width, (1.0 - actual.y) * height])
        error = float(np.linalg.norm(actual_pixels - expected))
        records.append({
            "point": list(point), "expected_pixels": expected.tolist(),
            "actual_pixels": actual_pixels.tolist(), "error_pixels": error,
            "expected_depth": depth, "actual_depth": float(actual.z),
        })
    return records, (width, height)


def _grid_checks(volume, assets, metadata):
    arrays_path = assets / metadata.get("assets", {}).get("arrays", "arrays.npz")
    vdb = Path(bpy.path.abspath(volume.data.filepath)).resolve()
    grids = {grid.name: grid for grid in openvdb.readAll(str(vdb))[0]}
    if not {"density", "color"}.issubset(grids):
        raise RuntimeError(f"VDB 缺少 density/color：{sorted(grids)}")
    with np.load(arrays_path, allow_pickle=False) as arrays:
        shape = arrays["density"].shape
        density = np.zeros(shape, dtype=np.float32)
        color = np.zeros(shape + (3,), dtype=np.float32)
        grids["density"].copyToArray(density)
        grids["color"].copyToArray(color)
        np.testing.assert_array_equal(density, arrays["density"])
        np.testing.assert_array_equal(color, arrays["display_color"])
    origin = np.asarray(grids["density"].transform.indexToWorld((0, 0, 0)), dtype=np.float64)
    np.testing.assert_allclose(origin, metadata["blender_grid"]["origin"], rtol=0.0, atol=1e-6)
    for axis in range(3):
        index = [0, 0, 0]
        index[axis] = 1
        step = np.asarray(grids["density"].transform.indexToWorld(tuple(index))) - origin
        expected = np.zeros(3)
        expected[axis] = metadata["blender_grid"]["spacing"][axis]
        np.testing.assert_allclose(step, expected, rtol=0.0, atol=1e-6)
    return metadata, {
        "density_roundtrip": True, "color_roundtrip": True,
        "transform_origin": origin.tolist(), "shape": list(shape),
        "active_bounds": [list(value) for value in grids["density"].evalActiveVoxelBoundingBox()],
    }


def _guide_frame_checks(metadata):
    """Validate the always-on auxiliary box that must be larger than the axis frame."""
    settings = metadata.get("guide_frame") or {}
    obj = bpy.data.objects.get(settings.get("object", ""))
    if obj is None or obj.type != "CURVE":
        raise RuntimeError("工程缺少辅助盒框曲线对象")
    bounds = [float(value) for value in obj.get("bounds", [])]
    expected = [float(value) for value in settings.get("bounds", [])]
    if len(bounds) != 6 or len(expected) != 6:
        raise RuntimeError("辅助盒框缺少六面边界信息")
    np.testing.assert_allclose(bounds, expected, rtol=0.0, atol=1e-6)
    axis = [float(value) for value in settings["axis_frame_bounds"]]
    data = [float(value) for value in metadata["blender_grid"]["material_clip_bounds"]]
    larger = all(
        bounds[index] < axis[index] if index % 2 == 0 else bounds[index] > axis[index]
        for index in range(6)
    )
    encloses = all(
        bounds[axis_index * 2] <= data[axis_index * 2]
        and bounds[axis_index * 2 + 1] >= data[axis_index * 2 + 1]
        for axis_index in range(3)
    )
    if not larger or not encloses:
        raise RuntimeError("辅助盒框没有大于坐标轴框或没有包住数据体")
    parent = metadata["composition"]["parent"]
    if obj.parent is None or obj.parent.name != parent:
        raise RuntimeError("辅助盒框没有挂在 BandScope Root 下")
    guide_collection = next(
        (owner for owner in obj.users_collection if owner.name == settings["collection"]),
        None,
    )
    if guide_collection is None:
        raise RuntimeError("辅助盒框不在预期的 Collection 中")
    if not guide_collection.hide_render:
        raise RuntimeError("辅助盒框 Collection 默认没有从渲染中排除")
    data_collection = bpy.data.collections.get(metadata["composition"]["data_collection"])
    if data_collection is None or obj.name not in {item.name for item in data_collection.all_objects}:
        raise RuntimeError("辅助盒框不在 BandScope Data Collection 内")
    return {
        "object": obj.name,
        "collection": settings["collection"],
        "parent": obj.parent.name,
        "bounds": bounds,
        "axis_frame_bounds": axis,
        "data_bounds": data,
        "larger_than_axis_frame": larger,
        "encloses_data_bounds": encloses,
        "hide_render": bool(obj.hide_render),
        "collection_hide_render": bool(guide_collection.hide_render),
        "edge_count": len(obj.data.splines),
    }


def _axis_indicator_checks(metadata):
    settings = metadata.get("axis_indicator") or {}
    if settings.get("style") != "solid_world_axes":
        raise RuntimeError("三轴方向标不是实体世界坐标样式")
    indicator = bpy.data.collections.get(settings.get("collection", ""))
    if indicator is None:
        raise RuntimeError("工程缺少三轴方向标 Collection")
    if indicator.hide_render or indicator.hide_viewport:
        raise RuntimeError("三轴方向标 Collection 默认被隐藏")
    origin = Vector(settings["origin_world"])
    placement = settings["placement"]
    parent_name = metadata["composition"]["parent"]
    data_collection = bpy.data.collections.get(metadata["composition"]["data_collection"])
    data_objects = {obj.name for obj in data_collection.all_objects} if data_collection else set()
    records = []

    def check_object(obj, expected_type):
        if obj is None or obj.type != expected_type or obj.name not in data_objects:
            raise RuntimeError("三轴方向标的 Curve、Mesh 或 Text 对象不完整")
        if obj.parent is None or obj.parent.name != parent_name or indicator not in obj.users_collection:
            raise RuntimeError("三轴方向标对象的父对象或 Collection 不正确")
        if obj.hide_render or obj.hide_viewport or obj.hide_get():
            raise RuntimeError("三轴方向标对象默认被隐藏")
        emission = next(
            (node for node in obj.data.materials[0].node_tree.nodes if node.bl_idname == "ShaderNodeEmission"),
            None,
        )
        if emission is None:
            raise RuntimeError("三轴方向标缺少单色自发光材质")
        np.testing.assert_allclose(
            emission.inputs["Color"].default_value[:3], settings["ink_color"], rtol=0.0, atol=1e-6,
        )

    for axis in settings.get("axes", []):
        shaft = bpy.data.objects.get(axis["object"])
        head = bpy.data.objects.get(axis["head_object"])
        check_object(shaft, "CURVE")
        check_object(head, "MESH")
        if axis["kind"] == "arrow":
            if len(shaft.data.splines) != 1 or len(shaft.data.splines[0].points) != 2:
                raise RuntimeError(f"{axis['role']} 箭头杆不完整")
            points = shaft.data.splines[0].points
            np.testing.assert_allclose(points[0].co[:3], list(origin), rtol=0.0, atol=1e-6)
            np.testing.assert_allclose(points[1].co[:3], axis["shaft_end_world"], rtol=0.0, atol=1e-6)
            if len(head.data.vertices) != 25 or len(head.data.polygons) != 25:
                raise RuntimeError(f"{axis['role']} 箭头头部不是封闭圆锥")
            for vertex, expected in zip(head.data.vertices, axis["head_vertices_world"]):
                np.testing.assert_allclose(vertex.co[:], expected, rtol=0.0, atol=1e-6)
        else:
            raise RuntimeError(f"未知方向标类型：{axis['kind']}")
        label_objects = []
        for part in axis["label_parts"]:
            if not part["body"]:
                continue
            label = bpy.data.objects.get(part["object"])
            check_object(label, "FONT")
            if label.data.body != part["body"]:
                raise RuntimeError(f"{axis['role']} 的短轴名不正确")
            np.testing.assert_allclose(label.location[:], part["world"], rtol=0.0, atol=1e-6)
            np.testing.assert_allclose(
                label.data.size, placement["text_size"] * float(part["size_factor"]), rtol=0.0, atol=1e-6,
            )
            np.testing.assert_allclose(label.data.shear, float(part["shear"]), rtol=0.0, atol=1e-6)
            label_objects.append(label.name)
        records.append({
            "key": axis["key"], "role": axis["role"], "kind": axis["kind"],
            "object": shaft.name, "head_object": head.name, "label_objects": label_objects,
            "label": axis["label"], "parent": shaft.parent.name,
            "visible": True, "monochrome": True,
        })
    if ({record["key"] for record in records} != {"X", "Y", "E"}
            or {record["role"] for record in records} != {"horizontal", "vertical", "depth"}):
        raise RuntimeError("三轴方向标必须完整包含水平、竖直和纵深角色")
    directions = []
    for axis in settings["axes"]:
        shaft = bpy.data.objects[axis["object"]]
        points = [shaft.matrix_world @ Vector(p.co[:3]) for p in shaft.data.splines[0].points]
        directions.append(tuple((points[1] - points[0]).normalized()))
        head = bpy.data.objects[axis["head_object"]]
        vertices = np.asarray([tuple(head.matrix_world @ v.co) for v in head.data.vertices])
        if np.linalg.matrix_rank(vertices - vertices[0], tol=1e-6) != 3:
            raise RuntimeError("方向标头部退化为平面")
    np.testing.assert_allclose(np.asarray(directions) @ np.asarray(directions).T, np.eye(3), atol=1e-6)
    frame = bpy.data.objects[metadata["guide_frame"]["object"]]
    np.testing.assert_allclose(frame.location[:], metadata["layout"]["guide_frame"], atol=1e-6)
    suffix = metadata.get("composition", {}).get("name_suffix", "")
    references = bpy.data.collections["Coordinate reference · editable" + suffix]
    for obj in references.objects:
        if obj.type == "CURVE":
            np.testing.assert_allclose(obj.location[:], metadata["layout"]["coordinates"], atol=1e-6)
    # Inspect evaluated world bounds, including labels, rather than just trusting
    # exported offset metadata. All three groups must clear the scientific data.
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()

    def group_bounds(objects):
        corners = [tuple(obj.matrix_world @ Vector(corner))
                   for source in objects for obj in (source.evaluated_get(depsgraph),)
                   for corner in obj.bound_box]
        values = np.asarray(corners)
        return values.min(axis=0), values.max(axis=0)

    data_bounds = np.asarray(metadata["blender_grid"]["material_clip_bounds"]).reshape(3, 2)
    data_bounds += np.asarray(metadata.get("_world_translation", (0.0, 0.0, 0.0))).reshape(3, 1)
    _, coordinate_max = group_bounds(references.objects)
    frame_min, _ = group_bounds([frame])
    marker_min, _ = group_bounds(indicator.objects)
    if not (coordinate_max[0] < data_bounds[0, 0]
            and frame_min[1] > data_bounds[1, 1]
            and marker_min[2] > data_bounds[2, 1]):
        raise RuntimeError("坐标轴、盒框、方向标未分别与数据体在左、后、上方分离")
    return {
        "collection": indicator.name, "style": settings["style"],
        "ink_color": list(settings["ink_color"]), "origin_world": list(origin),
        "axes": records, "all_present": True, "visible_by_default": True, "geometry_validated": True,
        "collection_hide_render": bool(indicator.hide_render),
        "collection_hide_viewport": bool(indicator.hide_viewport),
    }


def _project_entry(assets, entry_id=None):
    root = json.loads((assets / "scene.json").read_text(encoding="utf-8"))
    if "entries" not in root:
        entry = dict(root)
        entry.setdefault("composition", {
            "data_collection": "BandScope Data", "camera_collection": "BandScope Camera",
            "parent": "BandScope Root", "volume": "BandScope Volume", "camera": "BandScope Camera",
            "name_suffix": "",
        })
        entry["_world_translation"] = [0.0, 0.0, 0.0]
        return root, entry
    entries = root["entries"]
    selected = next((entry for entry in entries if entry["entry_id"] == entry_id), None) if entry_id else entries[0]
    if selected is None:
        raise RuntimeError(f"工程没有条目 {entry_id}")
    entry = dict(selected)
    entry["_world_translation"] = root["project"]["layout"]["entries"][entry["entry_id"]]["translation"]
    return root, entry


def _box_preset_checks(project, metadata):
    if "entries" not in project:
        return []
    presets = project["project"]["presets"]
    records = []
    for settings in metadata.get("box_presets") or ():
        if not presets.get(settings.get("preset_group", settings["preset"])):
            if bpy.data.objects.get(settings["object"]) is not None:
                raise RuntimeError(f"未勾选的长方体被创建：{settings['preset']}")
            continue
        obj = bpy.data.objects.get(settings["object"])
        if obj is None or obj.type != "MESH" or len(obj.data.vertices) != 8 or len(obj.data.polygons) != 6:
            raise RuntimeError(f"长方体缺失或不是完整六面网格：{settings['preset']}")
        material = obj.data.materials[0]
        if material.name != settings["material"] or len(obj.data.materials) != 1:
            raise RuntimeError(f"长方体材质不独立：{settings['preset']}")
        parameters = settings.get("parameters") or {}
        if "opacity" in parameters:
            mix = material.node_tree.nodes.get("BandScope opacity")
            if mix is None or abs(mix.inputs[0].default_value - parameters["opacity"]) > 1e-6:
                raise RuntimeError(f"长方体透明度错误：{settings['preset']}")
        records.append({"preset": settings["preset"], "object": obj.name, "material": material.name})
    return records


def _add_plane():
    bpy.ops.mesh.primitive_plane_add(size=4.0, location=(0.0, 0.0, 0.0))
    plane = bpy.context.object
    plane.name = "BandScope acceptance · auxiliary plane"
    material = bpy.data.materials.new("Acceptance plane")
    material.diffuse_color = (0.12, 0.28, 0.55, 1.0)
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (0.12, 0.28, 0.55, 1.0)
    shader.inputs["Roughness"].default_value = 0.65
    plane.data.materials.append(material)
    return plane.name


def inspect(args):
    blend = Path(args.blend).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    scene = bpy.context.scene
    volumes = _load_volumes()
    assets = blend.parent / f"{blend.stem}.assets"
    project, metadata = _project_entry(assets, getattr(args, "entry", None))
    composition = metadata["composition"]
    volume = bpy.data.objects.get(composition["volume"])
    if volume is None or volume.type != "VOLUME":
        raise RuntimeError(f"工程缺少条目体对象：{composition['volume']}")
    camera = bpy.data.objects.get(composition["camera"])
    if camera is None:
        raise RuntimeError(f"工程缺少条目备用相机：{composition['camera']}")
    scene.camera = camera
    metadata, grid_report = _grid_checks(volume, assets, metadata)
    presets = (project.get("project", {}).get("presets") if "entries" in project else None)
    guide_enabled = presets.get("guide_frame", True) if presets else True
    indicator_enabled = presets.get("orientation", True) if presets else True
    guide_report = _guide_frame_checks(metadata) if guide_enabled else {}
    indicator_report = _axis_indicator_checks(metadata) if indicator_enabled else {}
    boxes_report = _box_preset_checks(project, metadata)
    projection, resolution = _projection_checks(scene, metadata, args.long_edge)
    max_error = max(record["error_pixels"] for record in projection)
    if max_error > 1.0:
        raise RuntimeError(f"相机投影误差 {max_error:.4f} 像素，超过 1 像素")
    if scene.camera.data.sensor_fit != "VERTICAL":
        raise RuntimeError("相机不是垂直 sensor fit")
    if volume.data.render.space != "OBJECT" or volume.data.render.clipping != 0.0:
        raise RuntimeError("体密度空间或弱信号裁除设置不正确")
    if not scene.render.film_transparent or scene.view_settings.view_transform != "Standard":
        raise RuntimeError("透明背景或 Standard 色彩管理未保留")
    material = volume.data.materials[0]
    coefficients = next(node for node in material.node_tree.nodes if node.bl_idname == "ShaderNodeVolumeCoefficients")
    if any(value != 0.0 for value in coefficients.inputs["Scatter Coefficients"].default_value):
        raise RuntimeError("材质不是无散射设置")
    plane = _add_plane() if args.add_plane else None
    render_path = None
    if args.render:
        render_path = Path(args.render).resolve()
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = int(args.samples)
        scene.render.filepath = str(render_path)
        bpy.ops.render.render(write_still=True)
        if not render_path.is_file() or render_path.stat().st_size == 0:
            raise RuntimeError("CPU 渲染没有生成图像")
    report = {
        "blend": str(blend), "blend_reopened": True,
        "relative_volume_path": volume.data.filepath,
        "grid": grid_report, "guide_frame": guide_report, "axis_indicator": indicator_report,
        "entry_id": metadata.get("entry_id", "entry-001"), "box_presets": boxes_report,
        "projection": projection, "max_projection_error_pixels": max_error,
        "resolution": list(resolution), "cpu_render": str(render_path) if render_path else None,
        "auxiliary_plane": plane, "view_transform": scene.view_settings.view_transform,
        "transparent_background": scene.render.film_transparent,
        "density_space": volume.data.render.space,
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BANDSCOPE_ACCEPTANCE " + json.dumps({
        "max_projection_error_pixels": max_error, "grid_roundtrip": True,
        "render": str(render_path) if render_path else None,
    }, ensure_ascii=False), flush=True)


def append(args):
    base = Path(args.base).resolve()
    addition = Path(args.append).resolve()
    output = Path(args.output).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(base))
    scene = bpy.context.scene
    camera = scene.camera
    before = len([obj for obj in scene.objects if obj.type == "VOLUME"])
    collection_name = getattr(args, "collection", None) or "BandScope Data"
    with bpy.data.libraries.load(str(addition), link=False) as (source, target):
        if collection_name not in source.collections:
            raise RuntimeError(f"追加工程没有 {collection_name} Collection")
        target.collections = [collection_name]
    for collection in target.collections:
        if collection is not None:
            scene.collection.children.link(collection)
    if scene.camera is not camera:
        raise RuntimeError("Append 数据 Collection 替换了目标场景相机")
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=str(output))
    volumes = _load_volumes()
    after = len(volumes)
    if after != before + 1:
        raise RuntimeError(f"Append 后体对象数量不正确：{before} → {after}")
    report = {
        "base": str(base), "append": str(addition), "output": str(output),
        "saved_and_reopened": True, "camera_unchanged": True,
        "volume_count_before": before, "volume_count_after": after,
        "volume_paths": [obj.data.filepath for obj in volumes],
        "resolved_assets": [str(Path(bpy.path.abspath(obj.data.filepath)).resolve()) for obj in volumes],
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BANDSCOPE_ACCEPTANCE " + json.dumps({"append_reopened": True, "volumes": after}), flush=True)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--blend", required=True)
    inspect_parser.add_argument("--report", required=True)
    inspect_parser.add_argument("--entry")
    inspect_parser.add_argument("--render")
    inspect_parser.add_argument("--long-edge", type=int, default=1024)
    inspect_parser.add_argument("--samples", type=int, default=24)
    inspect_parser.add_argument("--add-plane", action="store_true")
    append_parser = subparsers.add_parser("append")
    append_parser.add_argument("--base", required=True)
    append_parser.add_argument("--append", required=True)
    append_parser.add_argument("--output", required=True)
    append_parser.add_argument("--report", required=True)
    append_parser.add_argument("--collection")
    args = parser.parse_args(argv)
    try:
        inspect(args) if args.command == "inspect" else append(args)
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
