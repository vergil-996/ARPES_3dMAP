# -*- coding: utf-8 -*-
"""Verify Blender export from a frozen BandScope executable in an isolated process.

python scripts/validation/verify_frozen_blender_flow.py <BandScope.exe> --blender <blender.exe>
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from bandscope.app_metadata import APP_VERSION
from bandscope.extensions.plugin_manager import InstallSource, install_package
from bandscope.extensions.trust import SOURCE_LOCAL
from tests.support.plugins import make_plugin_archive

PLUGIN_ID = "blender_frozen_acceptance"
ENTRY_SOURCE = r'''
import json
import os
import traceback
from pathlib import Path

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
from plugin_api import Plugin

_scheduled = False


def _run():
    config = json.loads(os.environ["BANDSCOPE_BLENDER_VALIDATION_CONFIG"])
    report = {"success": False}
    try:
        import sys
        import numpy as np
        from bandscope.exporting.blender_export import BlenderExportJob, blender_script_path
        from bandscope.exporting.blender_jobs import BlenderProjectExportJob, BlenderProjectUpdateJob
        from bandscope.exporting.blender_project import (
            BlenderProjectRequest, load_blender_project, update_snapshot_display,
        )
        from bandscope.exporting.publication_models import PublicationSnapshot
        from bandscope.core.display_style import new_display_style, new_render_quality
        from bandscope.processing.display_transfer import display_level_info

        shape = (2, 3, 4)
        volume = np.linspace(0.0, 1.0, 24, dtype=np.float32).reshape(shape)
        levels = (0.0, 60.0, 100.0)
        display_style = new_display_style(levels, "linear")
        display_style["normalization"].update(mode="manual", manual_range=(0.0, 1.0))
        render_quality = new_render_quality()
        camera = {
            "position": (250.0, -350.0, 280.0), "focal_point": (100.0, 100.0, 100.0),
            "view_up": (0.0, 0.0, 1.0), "parallel_projection": True,
            "parallel_scale": 120.0, "view_angle": 35.0, "clipping_range": (1.0, 2000.0),
        }
        snapshot = PublicationSnapshot(
            snapshot_id="frozen-validation", source_page_id="frozen", source_page_title="Frozen",
            page_kind="home", view="3d", view_family="3d", captured_at="2026-11-05T12:00:00",
            cmap_name="magma", levels_params=levels, locked_range=None,
            coords={key: np.linspace(-1.0, 1.0, size) for key, size in zip(("X", "Y", "E"), shape)},
            coord_sources={"X": "file", "Y": "file", "E": "file"},
            coord_units={"X": "Å⁻¹", "Y": "Å⁻¹", "E": "eV"},
            source_desc="Frozen validation", camera=camera,
            payload={
                "volume": volume, "data_bounds": (0, 1, 0, 2, 0, 3), "full_shape": shape,
                "spacing": tuple(200.0 / (size - 1) for size in shape),
                "clip_render_bounds": None, "include_zero": False, "opacity_mode": "linear",
                "opacity_multiplier": np.linspace(0.4, 1.0, shape[2]),
                "show_axes": True, "viewport_aspect": 1.0,
                "level_info": display_level_info(volume, display_style),
                "display": {"background_style": display_style, "resolved_range": (0.0, 1.0), "state": None},
                "render_quality": render_quality,
                "axis_titles": ["kx", "ky", "E"], "enhancement": None,
            },
        )
        output = Path(config["output_blend"])
        result = BlenderExportJob(snapshot, config["blender"], output, overwrite=True).run()
        import hashlib
        before = hashlib.sha256(output.read_bytes()).hexdigest()
        project = load_blender_project(output)
        restored = project.entries[0].snapshot
        if (not project.supports_display_styles
                or restored.payload["display"]["background_style"].get("display_mode") != "independent"):
            raise RuntimeError("Frozen independent display did not survive schema-11 roundtrip")
        update_snapshot_display(
            restored, cmap_name="viridis", levels_params=(5.0, 65.0, 90.0),
            locked_range=(0.0, 0.8), include_zero=True, opacity_mode="power",
        )
        updated = BlenderProjectUpdateJob(project, {"entry-001": restored}, config["blender"]).run()
        if hashlib.sha256(output.read_bytes()).hexdigest() != before:
            raise RuntimeError("Frozen in-place update changed the .blend bytes")
        from bandscope.exporting.blender_materials import MATERIAL_SPECS
        material_output = Path(config["material_output_blend"])
        expected_materials = {spec.material_id: spec.material_id for spec in MATERIAL_SPECS}
        material_result = BlenderProjectExportJob(
            BlenderProjectRequest([restored], volume_mode="hybrid_lit"),
            config["blender"], material_output, overwrite=True,
        ).run()
        material_entries = material_result.report.get("entries") or []
        if len(material_entries) != 1:
            raise RuntimeError("Frozen material export did not return one entry")
        boxes = {box["preset"]: box for box in material_entries[0].get("box_presets") or ()}
        actual_materials = {key: boxes[key]["material_id"] for key in expected_materials}
        if actual_materials != expected_materials or len(boxes) != 8:
            raise RuntimeError(f"Frozen material IDs differ: {actual_materials}")
        lights = {light["preset"] for light in material_entries[0].get("box_lights") or ()}
        expected_lights = {spec.material_id for spec in MATERIAL_SPECS if spec.requires_light}
        if lights != expected_lights:
            raise RuntimeError(f"Frozen material lights differ: {sorted(lights)}")
        scene_metadata = json.loads(
            (material_result.assets_path / "scene.json").read_text(encoding="utf-8")
        )
        edit = scene_metadata["entries"][0]["bandscope_edit"]
        if (scene_metadata["schema_version"] != 11 or edit["format_version"] != 2
                or edit["display_style"].get("display_mode") != "independent"
                or edit["render_quality"].get("mode") != "explicit"):
            raise RuntimeError("Frozen schema-11 display edit metadata is incomplete")
        volume_material = material_entries[0].get("volume_material") or {}
        volume_lights = material_entries[0].get("volume_lights") or ()
        if (volume_material.get("mode") != "hybrid_lit"
                or not volume_material.get("material_validated")
                or len(volume_lights) != 3
                or any(light.get("receiver_objects") != ["BandScope Volume"]
                       for light in volume_lights)):
            raise RuntimeError("Frozen hybrid-lit material or independent lights are incomplete")
        configured_boxes = scene_metadata["entries"][0]["box_presets"]
        expected_slots = {
            spec.material_id: ("front" if spec.requires_light else "right") for spec in MATERIAL_SPECS
        }
        if (any(box["color"] != [1.0, 0.35, 0.05] for box in configured_boxes)
                or any(box["layout_slot"] != expected_slots[box["material_id"]]
                       for box in configured_boxes)
                or any(box["group_height_offset"] != (
                    0.175 if expected_slots[box["material_id"]] == "front" else 0.0)
                       for box in configured_boxes)
                or any(box["parameters"] != spec.default_parameters()
                       for box in configured_boxes for spec in MATERIAL_SPECS
                       if spec.material_id == box["material_id"])):
            raise RuntimeError("Frozen thin boxes differ in color, parameters, slots or height offsets")
        if any(box["emission"]["effective"]
               for box in boxes.values() if box["material_id"] == "data_translucent"):
            raise RuntimeError("Frozen data-translucent plate emits")
        from bandscope.exporting.blender_texts import TEXT_CAP_HEIGHT, TEXT_PRESETS
        text_records = material_entries[0].get("text_presets") or ()
        actual_texts = [item["preset"] for item in text_records]
        if (actual_texts != list(TEXT_PRESETS)
                or any(item["body"] != "A" or not item["font_packed"]
                       or abs(item["cap_height"] - TEXT_CAP_HEIGHT) > 1e-4 for item in text_records)
                or len(material_entries[0].get("text_lights") or ()) != 1):
            raise RuntimeError(f"Frozen text presets differ: {actual_texts}")
        for relative in scene_metadata["project"]["text_fonts"]["files"]:
            if not (material_result.assets_path / relative).is_file():
                raise RuntimeError(f"Frozen font asset missing: {relative}")
        report.update(success=True, meipass=getattr(sys, "_MEIPASS", None),
                      script=str(blender_script_path()), script_exists=blender_script_path().is_file(),
                      blend=str(result.blend_path), assets=str(result.assets_path),
                      reader_roundtrip=True, in_place_update=True,
                      updated_entries=len(updated.project.entries),
                      material_blend=str(material_result.blend_path),
                      material_assets=str(material_result.assets_path),
                      material_schema=material_result.report.get("schema_version"),
                      material_layout=material_result.report.get("box_layout", {}).get("id"),
                      material_boxes=actual_materials,
                      material_lights=sorted(lights),
                      default_volume_mode=result.report["entries"][0]["volume_material"]["mode"],
                      hybrid_volume_mode=volume_material["mode"],
                      hybrid_volume_light_count=len(volume_lights),
                      hybrid_volume_bounces=material_result.report.get("volume_bounces"),
                      text_presets=actual_texts,
                      text_count=len(actual_texts),
                      text_fonts_packed=all(item["font_packed"] for item in text_records))
    except Exception as exc:
        report.update(error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
    Path(config["report"]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    QApplication.quit()


class Plugin(Plugin):
    def create_panel(self, host):
        global _scheduled
        if not _scheduled:
            _scheduled = True
            QTimer.singleShot(2000, _run)
        return None
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--blender", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / ".local/outputs/frozen-blender-validation")
    args = parser.parse_args()
    exe = args.executable.resolve()
    if not exe.is_file():
        parser.error(f"找不到冻结程序：{exe}")
    if not args.blender.is_file():
        parser.error(f"找不到 Blender：{args.blender}")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundled_candidates = [
        exe.parent / "_internal/bandscope/exporting/blender_script.py",
        exe.parent / "bandscope/exporting/blender_script.py",
    ]
    bundled = next((path for path in bundled_candidates if path.is_file()), None)
    if bundled is None:
        print("FAIL：冻结目录没有 bandscope/exporting/blender_script.py", flush=True)
        return 1
    print(f"PASS：冻结脚本资源 {bundled}", flush=True)
    with tempfile.TemporaryDirectory(prefix="frozen-blender-") as directory:
        root = Path(directory)
        extension_root = root / "extensions"
        archive = make_plugin_archive(
            root / "acceptance.bsplugin",
            manifest={
                "id": PLUGIN_ID, "name": "Blender frozen acceptance", "version": "1.0.0",
                "api_version": 1, "requires_app": APP_VERSION,
                "entry_point": "entry:Plugin", "capabilities": ["opacity_multiplier"],
            },
            entry_source=ENTRY_SOURCE,
        )
        install_package(
            archive, root=extension_root,
            source=InstallSource(kind=SOURCE_LOCAL, accepted_unverified=True),
        )
        report_path = output / f"{exe.stem}-process-report.json"
        blend = output / f"{exe.stem}-工程 中文.blend"
        material_blend = output / f"{exe.stem}-材质 工程.blend"
        config = {
            "blender": str(args.blender.resolve()), "output_blend": str(blend),
            "material_output_blend": str(material_blend), "report": str(report_path),
        }
        env = dict(os.environ)
        env["BANDSCOPE_EXTENSION_ROOT"] = str(extension_root)
        env["BANDSCOPE_BLENDER_VALIDATION_CONFIG"] = json.dumps(config, ensure_ascii=False)
        env.pop("QT_QPA_PLATFORM", None)
        process = subprocess.Popen([str(exe)], cwd=str(root), env=env)
        try:
            process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=30)
            if report_path.is_file():
                print(report_path.read_text(encoding="utf-8"), flush=True)
            print("FAIL：冻结验收进程超时", flush=True)
            return 1
        if not report_path.is_file():
            print(f"FAIL：冻结进程没有生成报告，退出码 {process.returncode}", flush=True)
            return 1
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if (not report.get("success") or not report.get("script_exists")
                or not report.get("reader_roundtrip") or not report.get("in_place_update")):
            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
            return 1
        from bandscope.exporting.blender_materials import MATERIAL_SPECS
        from bandscope.exporting.blender_texts import TEXT_PRESETS
        expected_materials = {spec.material_id: spec.material_id for spec in MATERIAL_SPECS}
        expected_lights = sorted(spec.material_id for spec in MATERIAL_SPECS if spec.requires_light)
        if (report.get("material_schema") != 11 or report.get("material_layout") != "right_front_groups"
                or report.get("material_boxes") != expected_materials
                or report.get("material_lights") != expected_lights
                or report.get("default_volume_mode") != "emission"
                or report.get("hybrid_volume_mode") != "hybrid_lit"
                or report.get("hybrid_volume_light_count") != 3
                or report.get("hybrid_volume_bounces") != 2
                or report.get("text_presets") != list(TEXT_PRESETS)
                or not report.get("text_fonts_packed")):
            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
            return 1
        print("PASS：冻结进程导出、回读并原位更新，外部 Blender 链路完整", flush=True)
        print("PASS：冻结进程生成八块薄板、十种文本及两种数据体外观（schema 11）", flush=True)

    inspection = output / f"{exe.stem}-inspection.json"
    render = output / f"{exe.stem}-render.png"
    command = [
        str(args.blender.resolve()), "--background", "--factory-startup", "--python",
        str(REPO_ROOT / "scripts/validation/blender_scene_acceptance.py"), "--",
        "inspect", "--blend", str(blend), "--report", str(inspection),
        "--long-edge", "256", "--samples", "8", "--render", str(render),
    ]
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", timeout=300)
    (REPO_ROOT / ".local/logs").mkdir(parents=True, exist_ok=True)
    (REPO_ROOT / f".local/logs/{exe.stem}-blender-frozen-inspection.log").write_text(
        completed.stdout or "", encoding="utf-8")
    if completed.returncode != 0 or not render.is_file():
        print("\n".join((completed.stdout or "").splitlines()[-25:]), flush=True)
        print("FAIL：冻结产物重开或渲染失败", flush=True)
        return 1
    summary = json.loads(inspection.read_text(encoding="utf-8"))
    print(f"PASS：冻结产物重开、VDB 回读与 CPU 渲染；投影误差 {summary['max_projection_error_pixels']:.6f} px", flush=True)

    material_inspection = output / f"{exe.stem}-material-inspection.json"
    material_render = output / f"{exe.stem}-material-render.png"
    command = [
        str(args.blender.resolve()), "--background", "--factory-startup", "--python",
        str(REPO_ROOT / "scripts/validation/blender_material_scene.py"), "--",
        "inspect", "--blend", str(material_blend), "--report", str(material_inspection),
        "--long-edge", "320", "--samples", "16", "--render", str(material_render),
    ]
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", timeout=300)
    (REPO_ROOT / f".local/logs/{exe.stem}-blender-frozen-material-inspection.log").write_text(
        completed.stdout or "", encoding="utf-8")
    if completed.returncode != 0 or not material_render.is_file():
        print("\n".join((completed.stdout or "").splitlines()[-25:]), flush=True)
        print("FAIL：冻结八材质工程重开或渲染失败", flush=True)
        return 1
    material_summary = json.loads(material_inspection.read_text(encoding="utf-8"))
    entry = material_summary["entries"][0]
    boxes = {box["preset"]: box["material_id"] for box in entry["boxes"]}
    if (material_summary.get("schema_version") != 11
            or material_summary["layout"]["id"] != "right_front_groups"
            or boxes != expected_materials
            or {light["preset"] for light in entry["lights"]} != set(expected_lights)
            or entry["volume_material"]["mode"] != "hybrid_lit"
            or entry.get("volume_light_count") != 3
            or any(box["intersects_data"] for box in entry["boxes"])):
        print(json.dumps(material_summary, ensure_ascii=False, indent=2), flush=True)
        print("FAIL：冻结八材质工程的重开报告不符合契约", flush=True)
        return 1
    print("PASS：冻结八材质工程重开、默认右侧／前侧分组布局与辅助灯链接；CPU 渲染通过", flush=True)
    print("全部通过。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
