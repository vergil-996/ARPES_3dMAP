"""Run inside Blender for schema-11 volume material and lighting acceptance."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import traceback

import bpy
from mathutils import Vector
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bandscope.exporting.blender_script import (  # noqa: E402
    validate_volume_lighting, validate_volume_material,
)


def _geometry(volume, camera):
    return {
        "volume_matrix": np.asarray(volume.matrix_world, dtype=np.float64).tolist(),
        "camera_matrix": np.asarray(camera.matrix_world, dtype=np.float64).tolist(),
        "volume_bounds": [list(corner) for corner in volume.bound_box],
        "volume_path": volume.data.filepath,
        "grid_names": sorted(grid.name for grid in volume.data.grids),
    }


def _render(scene, path, long_edge, samples):
    if scene.camera is None:
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
    scene.cycles.use_denoising = True
    scene.render.filepath = str(path)
    started = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    elapsed = time.perf_counter() - started
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("Cycles 渲染没有生成图像")
    return elapsed


def inspect(args):
    blend = Path(args.blend).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    scene = bpy.context.scene
    metadata = json.loads((blend.parent / f"{blend.stem}.assets/scene.json").read_text(encoding="utf-8"))
    if int(metadata.get("schema_version", 0)) != 11:
        raise RuntimeError("柔和受光验收要求 schema 11")
    project = metadata["project"]
    reports = []
    geometry_checks = []
    for entry in metadata["entries"]:
        composition = entry["composition"]
        volume = bpy.data.objects.get(composition["volume"])
        root = bpy.data.objects.get(composition["parent"])
        camera = bpy.data.objects.get(composition["camera"])
        if volume is None or root is None or camera is None or volume.parent != root:
            raise RuntimeError(f"条目 {entry['entry_id']} 的体、Root 或相机缺失")
        material = validate_volume_material(volume, project)
        lights = validate_volume_lighting(entry, root, volume)
        before = _geometry(volume, camera)
        moved = None
        if args.move_light:
            key = next(obj for obj in bpy.data.objects if obj.get("bandscope_volume_light") == "key")
            key.location.x += 0.137
            bpy.context.view_layer.update()
            after = _geometry(volume, camera)
            if before != after:
                raise RuntimeError("移动数据体灯改变了几何、相机或 VDB 引用")
            moved = {"light": key.name, "new_position": list(key.location), "geometry_unchanged": True}
        reports.append({
            "entry_id": entry["entry_id"], "volume_material": material,
            "volume_lights": lights, "geometry": before, "move_light": moved,
        })
        geometry_checks.append(before)
    names = [entry["volume_material"]["material"] for entry in reports]
    if len(set(names)) != len(names):
        raise RuntimeError("不同条目共享了可变数据体材质")
    receivers = [tuple(entry["volume_lights"][0]["receiver_objects"]) for entry in reports if entry["volume_lights"]]
    if len(receivers) != len(set(receivers)):
        raise RuntimeError("多页数据体灯接收者没有相互独立")
    if args.isolate_world:
        scene.world = None
    volume_lights = [obj for obj in bpy.data.objects if obj.get("bandscope_volume_light")]
    for obj in volume_lights:
        obj.hide_render = bool(args.lights_off)
    render_path = Path(args.render).resolve() if args.render else None
    elapsed = None
    if render_path is not None:
        if len(metadata["entries"]) == 1:
            scene.camera = bpy.data.objects[metadata["entries"][0]["composition"]["camera"]]
        elapsed = _render(scene, render_path, int(args.long_edge), int(args.samples))
    report = {
        "blend": str(blend), "blend_reopened": True,
        "schema_version": metadata["schema_version"],
        "volume_mode": project["volume_shading"]["mode"],
        "entries": reports, "render": str(render_path) if render_path else None,
        "render_seconds": elapsed, "requested_samples": int(args.samples),
        "saved_volume_bounces": int(scene.cycles.volume_bounces),
        "world_isolated": bool(args.isolate_world),
        "volume_lights_off": bool(args.lights_off),
        "active_volume_light_count": sum(not obj.hide_render for obj in volume_lights),
        "manual_visual_review": "NOT RUN by this inspector",
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def decorate(args):
    blend = Path(args.blend).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    metadata = json.loads((blend.parent / f"{blend.stem}.assets/scene.json").read_text(encoding="utf-8"))
    entry = metadata["entries"][0]
    volume = bpy.data.objects[entry["composition"]["volume"]]
    nodes = volume.data.materials[0].node_tree.nodes
    nodes["Scattering amount"].outputs[0].default_value = 0.42
    nodes["Emission compensation"].outputs[0].default_value = 0.22
    key = next(obj for obj in bpy.data.objects if obj.get("bandscope_volume_light") == "key")
    key.data.energy = 4321.0
    key.location.x += 0.25
    expected = {
        "scattering_amount": 0.42, "emission_compensation": 0.22,
        "key_energy": 4321.0, "key_position": list(key.location),
    }
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
    Path(args.report).write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")


def check_custom(args):
    expected = json.loads(Path(args.custom_report).read_text(encoding="utf-8"))
    blend = Path(args.blend).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    metadata = json.loads((blend.parent / f"{blend.stem}.assets/scene.json").read_text(encoding="utf-8"))
    entry = metadata["entries"][0]
    volume = bpy.data.objects[entry["composition"]["volume"]]
    nodes = volume.data.materials[0].node_tree.nodes
    values = {
        "scattering_amount": float(nodes["Scattering amount"].outputs[0].default_value),
        "emission_compensation": float(nodes["Emission compensation"].outputs[0].default_value),
    }
    key = next(obj for obj in bpy.data.objects if obj.get("bandscope_volume_light") == "key")
    if (abs(values["scattering_amount"] - expected["scattering_amount"]) > 1.0e-6
            or abs(values["emission_compensation"] - expected["emission_compensation"]) > 1.0e-6
            or abs(float(key.data.energy) - expected["key_energy"]) > 1.0e-4
            or (key.location - Vector(expected["key_position"])).length > 1.0e-6):
        raise RuntimeError("原位更新覆盖了用户在 Blender 中调整的材质或灯光")
    report = {"custom_material_and_light_preserved": True, **values,
              "key_energy": float(key.data.energy), "key_position": list(key.location)}
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None):
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--blend", required=True)
    inspect_parser.add_argument("--report", required=True)
    inspect_parser.add_argument("--render")
    inspect_parser.add_argument("--samples", type=int, default=128)
    inspect_parser.add_argument("--long-edge", type=int, default=480)
    inspect_parser.add_argument("--lights-off", action="store_true")
    inspect_parser.add_argument("--isolate-world", action="store_true")
    inspect_parser.add_argument("--move-light", action="store_true")
    for name in ("decorate", "check-custom"):
        child = subparsers.add_parser(name)
        child.add_argument("--blend", required=True)
        child.add_argument("--report", required=True)
        if name == "check-custom":
            child.add_argument("--custom-report", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            inspect(args)
        elif args.command == "decorate":
            decorate(args)
        else:
            check_custom(args)
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
