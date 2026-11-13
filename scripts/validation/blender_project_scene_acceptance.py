"""Run inside Blender for schema-7/8/9 multi-entry and non-destructive update acceptance."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import traceback

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
import numpy as np
import openvdb

DECORATIONS = (
    "BandScope acceptance light", "BandScope acceptance text",
    "BandScope acceptance cube", "BandScope acceptance camera",
)


def _grid_check(volume, assets, entry):
    path = Path(bpy.path.abspath(volume.data.filepath)).resolve()
    grids = {grid.name: grid for grid in openvdb.readAll(str(path))[0]}
    if not {"density", "color"}.issubset(grids):
        raise RuntimeError(f"条目 {entry['entry_id']} 缺少 VDB 网格")
    with np.load(assets / entry["assets"]["arrays"], allow_pickle=False) as arrays:
        density = np.zeros(arrays["density"].shape, dtype=np.float32)
        color = np.zeros(arrays["display_color"].shape, dtype=np.float32)
        grids["density"].copyToArray(density)
        grids["color"].copyToArray(color)
        np.testing.assert_array_equal(density, arrays["density"])
        np.testing.assert_array_equal(color, arrays["display_color"])
    return {"entry_id": entry["entry_id"], "shape": list(density.shape), "path": volume.data.filepath}


def _actual_bounds(collection):
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    for source in collection.all_objects:
        if source.type in {"EMPTY", "CAMERA", "LIGHT"}:
            continue
        obj = source.evaluated_get(depsgraph)
        if obj.type == "FONT":
            mesh = obj.to_mesh()
            try:
                if mesh.vertices:
                    points.extend(tuple(obj.matrix_world @ vertex.co) for vertex in mesh.vertices)
                    continue
            finally:
                obj.to_mesh_clear()
        points.extend(tuple(obj.matrix_world @ Vector(corner)) for corner in obj.bound_box)
    values = np.asarray(points)
    return values.min(axis=0), values.max(axis=0)


def check(blend, report_path, render=None, samples=8, require_decorations=False):
    blend = Path(blend).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    scene = bpy.context.scene
    assets = blend.parent / f"{blend.stem}.assets"
    metadata = json.loads((assets / "scene.json").read_text(encoding="utf-8"))
    project = metadata["project"]
    presets = project["presets"]
    if require_decorations:
        missing = [name for name in DECORATIONS if bpy.data.objects.get(name) is None]
        if missing:
            raise RuntimeError(f"原位更新后创作对象丢失：{missing}")
    grids = []
    bounds = []
    box_materials = []
    box_meshes = []
    text_materials = []
    text_curves = []
    text_presets = []
    for entry in metadata["entries"]:
        entry_id = entry["entry_id"]
        composition = entry["composition"]
        volume = bpy.data.objects.get(composition["volume"])
        root = bpy.data.objects.get(composition["parent"])
        if volume is None or root is None or volume.parent != root:
            raise RuntimeError(f"条目 {entry_id} 的体或父对象缺失")
        expected_translation = project["layout"]["entries"][entry_id]["translation"]
        np.testing.assert_allclose(root.location[:], expected_translation, atol=1e-6)
        expected_path = f"//{project['assets']['directory']}/{entry['assets']['vdb']}"
        if volume.data.filepath.replace("\\", "/") != expected_path:
            raise RuntimeError(f"条目 {entry_id} 的 VDB 路径改向")
        if not volume.data.grids.load():
            raise RuntimeError(f"条目 {entry_id} 的 VDB 无法载入")
        grids.append(_grid_check(volume, assets, entry))
        collection = bpy.data.collections[composition["data_collection"]]
        suffix = composition.get("name_suffix", "")
        reference = bpy.data.collections.get("Coordinate reference · editable" + suffix)
        if bool(reference is not None) != bool(presets["coordinates"]):
            raise RuntimeError(f"条目 {entry_id} 的坐标预设不符")
        guide = bpy.data.objects.get(entry["guide_frame"]["object"])
        if bool(guide is not None) != bool(presets["guide_frame"]):
            raise RuntimeError(f"条目 {entry_id} 的盒框预设不符")
        indicator = bpy.data.collections.get(entry["axis_indicator"]["collection"])
        if bool(indicator is not None) != bool(presets["orientation"]):
            raise RuntimeError(f"条目 {entry_id} 的方向标预设不符")
        for box in entry.get("box_presets") or ():
            obj = bpy.data.objects.get(box["object"])
            if not presets.get(box.get("preset_group", box["preset"])):
                if obj is not None:
                    raise RuntimeError(f"未勾选预设被创建：{box['preset']}")
                continue
            if obj is None or len(obj.data.vertices) != 8 or len(obj.data.polygons) != 6:
                raise RuntimeError(f"长方体不是完整独立网格：{box['preset']}")
            material = obj.data.materials[0]
            if material.name != box["material"]:
                raise RuntimeError(f"长方体材质不符：{box['preset']}")
            box_materials.append(material.name)
            box_meshes.append(obj.data.name)
        for light in (entry.get("box_lighting") or {}).get("lights") or ():
            obj = bpy.data.objects.get(light["object"])
            if obj is None or obj.type != "LIGHT" or obj.parent != root:
                raise RuntimeError(f"条目 {entry_id} 的辅助灯缺失或父对象错误")
            if obj.light_linking.receiver_collection is None:
                raise RuntimeError(f"条目 {entry_id} 的辅助灯没有灯光链接接收者")
        text_layout = entry.get("text_layout") or {}
        text_collection = bpy.data.collections.get(text_layout.get("collection", ""))
        text_enabled = bool(presets.get("text_presets"))
        if bool(text_collection is not None) != text_enabled:
            raise RuntimeError(f"条目 {entry_id} 的文本预设 Collection 与开关不符")
        for item in entry.get("text_presets") or ():
            obj = bpy.data.objects.get(item["object"])
            if not text_enabled:
                if obj is not None:
                    raise RuntimeError(f"未勾选文本预设被创建：{item['preset']}")
                continue
            if (obj is None or obj.type != "FONT" or obj.parent != root or obj.data.body != "A"
                    or obj.data.font.packed_file is None):
                raise RuntimeError(f"文本对象、正文、父对象或打包字体不符：{item['preset']}")
            if (text_collection is None or obj.name not in {value.name for value in text_collection.objects}
                    or abs(float(obj.dimensions.y) - float(text_layout["cap_height"])) > 1e-4):
                raise RuntimeError(f"文本 Collection 或实际字高不符：{item['preset']}")
            material = obj.data.materials[0]
            if material.name != item["material"]:
                raise RuntimeError(f"文本材质不符：{item['preset']}")
            text_materials.append(material.name)
            text_curves.append(obj.data.name)
            text_presets.append(item["preset"])
        text_lighting = entry.get("text_lighting") or {}
        for light in text_lighting.get("lights") or ():
            obj = bpy.data.objects.get(light["object"])
            if text_enabled and (obj is None or obj.type != "LIGHT" or obj.parent != root):
                raise RuntimeError(f"条目 {entry_id} 的文本辅助灯缺失或父对象错误")
            if text_enabled:
                receiver = obj.light_linking.receiver_collection
                if receiver is None or len(receiver.objects) != 10:
                    raise RuntimeError(f"条目 {entry_id} 的文本灯光链接接收者不正确")
        camera = bpy.data.objects.get(composition["camera"])
        if camera is None or camera.type != "CAMERA":
            raise RuntimeError(f"条目 {entry_id} 的备用相机缺失")
        bounds.append((entry_id, *_actual_bounds(collection)))
    if len(set(box_materials)) != len(box_materials) or len(set(box_meshes)) != len(box_meshes):
        raise RuntimeError("不同条目或长方体共享网格/材质")
    if len(set(text_materials)) != len(text_materials) or len(set(text_curves)) != len(text_curves):
        raise RuntimeError("不同条目或文本预设共享文字曲线/材质")
    for (_first_id, _first_low, first_high), (_second_id, second_low, _second_high) in zip(bounds, bounds[1:]):
        if first_high[0] >= second_low[0]:
            raise RuntimeError("多页面实际对象边界沿世界 X 相撞")
    overview = project["overview_camera"]
    if overview["enabled"]:
        active = bpy.data.objects.get(overview["object"])
        if active is None or scene.camera != active:
            raise RuntimeError("多页面总览相机缺失或不是活动相机")
        scene.camera = active
        corners = [
            Vector((x, y, z))
            for record in project["layout"]["entries"].values()
            for x in record["world_bounds"][0:2]
            for y in record["world_bounds"][2:4]
            for z in record["world_bounds"][4:6]
        ]
        projected = [world_to_camera_view(scene, active, point) for point in corners]
        if any(point.x < -1e-5 or point.x > 1.00001 or point.y < -1e-5 or point.y > 1.00001 or point.z <= 0
               for point in projected):
            raise RuntimeError("总览相机未覆盖所有数据组和预设")
    if render:
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = int(samples)
        scene.render.filepath = str(Path(render).resolve())
        bpy.ops.render.render(write_still=True)
        if not Path(render).is_file():
            raise RuntimeError("多页 Cycles 渲染没有输出图像")
    report = {
        "blend": str(blend), "entries": grids, "entry_count": len(grids),
        "overview_camera": scene.camera.name if scene.camera else None,
        "non_colliding": True, "independent_box_materials": sorted(box_materials),
        "text_count": len(text_presets), "text_presets": text_presets,
        "independent_text_materials": sorted(text_materials),
        "decorations_preserved": bool(require_decorations),
        "render": str(render) if render else None,
    }
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def decorate(blend, report_path):
    blend = Path(blend).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    bpy.ops.object.light_add(type="AREA", location=(0, -4, 5))
    bpy.context.object.name = DECORATIONS[0]
    bpy.ops.object.text_add(location=(0, 0, 2))
    text = bpy.context.object
    text.name = DECORATIONS[1]
    text.data.body = "Keep after BandScope update"
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, -2))
    bpy.context.object.name = DECORATIONS[2]
    bpy.ops.object.camera_add(location=(4, -4, 3))
    bpy.context.object.name = DECORATIONS[3]
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
    Path(report_path).write_text(json.dumps({"decorated": True, "objects": list(DECORATIONS)}, indent=2), encoding="utf-8")


def redirect_volume(blend, report_path):
    blend = Path(blend).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    metadata = json.loads((blend.parent / f"{blend.stem}.assets/scene.json").read_text(encoding="utf-8"))
    volume = bpy.data.objects[metadata["entries"][0]["composition"]["volume"]]
    volume.data.filepath = "//redirected-volume.vdb"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
    Path(report_path).write_text(json.dumps({"redirected": True}), encoding="utf-8")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "decorate", "redirect"))
    parser.add_argument("--blend", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--render")
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--require-decorations", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "decorate":
            decorate(args.blend, args.report)
        elif args.command == "redirect":
            redirect_volume(args.blend, args.report)
        else:
            check(args.blend, args.report, args.render, args.samples, args.require_decorations)
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
