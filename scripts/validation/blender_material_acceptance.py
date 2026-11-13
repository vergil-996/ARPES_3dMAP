# -*- coding: utf-8 -*-
"""Real Blender acceptance for the eight thin-box materials.

Covers eight material renders, mixed and partial preset projects, both plate
layouts, data-translucent sampling (32/128/256), transmission filtering,
multi-entry overview framing, Append, and optional real WSe2 data.

Usage:
  python scripts/validation/blender_material_acceptance.py --blender D:/blender/blender.exe
  python scripts/validation/blender_material_acceptance.py --blender D:/blender/blender.exe --data .local/data/WSe2_step.npz
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from bandscope.exporting.blender_export import detect_blender
from bandscope.exporting.blender_jobs import BlenderProjectExportJob, BlenderProjectUpdateJob
from bandscope.exporting.blender_materials import MATERIAL_IDS, material_spec
from bandscope.exporting.blender_project import (
    BOX_PRESETS, BlenderProjectRequest, load_blender_project, update_snapshot_display,
)
from scripts.validation.blender_export_acceptance import _hidden_kwargs, _real_snapshot
from tests.support.blender import make_blender_snapshot

INSPECTOR = REPO_ROOT / "scripts/validation/blender_material_scene.py"


class AcceptanceFailure(RuntimeError):
    pass


def _png_stats(path):
    from PIL import Image

    with Image.open(path) as image:
        rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = rgba[..., 3]
    visible = alpha > 0
    if int(alpha.min()) != 0:
        raise AcceptanceFailure(f"渲染图没有透明背景：{path}")
    if int(visible.sum()) < 200:
        raise AcceptanceFailure(f"渲染图没有足够可见像素：{path}（{int(visible.sum())}）")
    rgb = rgba[..., :3][visible]
    return {
        "path": str(path),
        "visible_pixels": int(visible.sum()),
        "alpha_min": int(alpha.min()),
        "alpha_max": int(alpha.max()),
        "mean_rgb": [float(value) for value in rgb.mean(axis=0)],
        "std_rgb": float(rgb.std()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", help="blender.exe；缺省时自动检测")
    parser.add_argument("--data", type=Path, help="可选：WSe2_step.npz 真实数据")
    parser.add_argument("--output-dir", type=Path,
                        default=REPO_ROOT / ".local/outputs/blender-material-acceptance")
    parser.add_argument("--long-edge", type=int, default=640)
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--sample-series-long-edge", type=int, default=320)
    parser.add_argument("--sample-series", default="32,128,256")
    args = parser.parse_args()
    blender = detect_blender(None, args.blender)
    if blender is None:
        parser.error("未检测到 Blender；请通过 --blender 指定 blender.exe")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    logs = REPO_ROOT / ".local/logs"
    logs.mkdir(parents=True, exist_ok=True)
    records, inspections = [], []
    projects = {}

    def record(name, status, detail=""):
        item = {"name": name, "status": status, "detail": str(detail)}
        records.append(item)
        print(f"[{status}] {name}: {detail}", flush=True)

    def run_blender(arguments, log_name):
        completed = subprocess.run(
            [str(blender), "--background", "--factory-startup", "--python", str(INSPECTOR),
             "--", *map(str, arguments)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", timeout=1800, **_hidden_kwargs(),
        )
        (logs / log_name).write_text(completed.stdout or "", encoding="utf-8")
        if completed.returncode != 0:
            tail = "\n".join((completed.stdout or "").splitlines()[-25:])
            raise AcceptanceFailure(f"Blender 验收进程失败：\n{tail}")

    def export_case(name, snapshots, box_settings, box_layout, presets=None):
        final = output / f"{name}.blend"
        request = BlenderProjectRequest(
            snapshots, presets, box_settings=box_settings, box_layout=box_layout,
        )
        result = BlenderProjectExportJob(
            request, blender, final, overwrite=True,
            log_path=logs / f"blender-material-{name}-export.log",
        ).run()
        projects[name] = final
        return result, request

    def inspect_case(name, request, *, render=True, samples=None, long_edge=None,
                     expected_layout=None, blend_path=None):
        blend = Path(blend_path) if blend_path is not None else projects[name]
        report_path = output / f"{name}-inspection.json"
        render_path = output / f"{name}.png" if render else None
        arguments = [
            "inspect", "--blend", blend, "--report", report_path,
            "--samples", int(samples if samples is not None else args.samples),
            "--long-edge", int(long_edge if long_edge is not None else args.long_edge),
        ]
        if render_path is not None:
            arguments += ["--render", render_path]
        run_blender(arguments, f"blender-material-{name}-inspect.log")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        presets = request.presets
        expected_layout = expected_layout or request.box_layout
        if report["schema_version"] != 11:
            raise AcceptanceFailure(f"{name} 的工程 schema 不是 11")
        if report["layout"]["id"] != expected_layout:
            raise AcceptanceFailure(f"{name} 的薄板布局不是 {expected_layout}")
        if len(report["entries"]) != len(request.entries):
            raise AcceptanceFailure(f"{name} 的条目数量不符")
        for entry, request_entry in zip(report["entries"], request.entries):
            if not np.allclose(entry["layout_bounds"], entry["actual_non_light_bounds"], atol=1e-4):
                raise AcceptanceFailure(f"{name} 的排版边界包含了灯光或与布局记录不一致")
            if not entry["volume_material"]["no_scattering"]:
                raise AcceptanceFailure(f"{name} 的体材质被改动")
            enabled = [key for key in BOX_PRESETS if presets.get("thin_boxes")]
            if [box["preset"] for box in entry["boxes"]] != enabled:
                raise AcceptanceFailure(f"{name} 的薄板集合与预设不一致")
            expected_lit = set()
            for box in entry["boxes"]:
                spec = material_spec(box["material_id"])
                expected_id = request.boxes[box["preset"]].material_id
                if box["material_id"] != expected_id:
                    raise AcceptanceFailure(
                        f"{name} 的薄板 {box['preset']} 材质 {box['material_id']} != {expected_id}"
                    )
                if box["color"] != [1.0, 0.35, 0.05]:
                    raise AcceptanceFailure(f"{name} 的薄板 {box['preset']} 不是统一橙色")
                if box["parameters"] != request.boxes[box["preset"]].parameters:
                    raise AcceptanceFailure(f"{name} 的薄板 {box['preset']} 参数与冻结请求不一致")
                emission = bool(box["material_report"]["emission"]["effective"])
                compensation = float(request.boxes[box["preset"]].parameters.get(
                    "emission_compensation", 0.0) or 0.0)
                if emission != bool(spec.emits_by_design or compensation > 0.0):
                    raise AcceptanceFailure(f"{name} 的薄板 {box['preset']} 自发光契约不符")
                if spec.requires_light:
                    expected_lit.add(box["preset"])
            actual_lit = {light["preset"] for light in entry["lights"]}
            if actual_lit != expected_lit:
                raise AcceptanceFailure(
                    f"{name} 的辅助灯集合 {sorted(actual_lit)} != {sorted(expected_lit)}"
                )
            if entry["light_object_count"] != len(expected_lit):
                raise AcceptanceFailure(f"{name} 存在多余的 LIGHT 对象")
            if expected_lit and set(entry["receiver_objects"]) != {
                    box["object"] for box in entry["boxes"] if box["preset"] in expected_lit
            }:
                raise AcceptanceFailure(f"{name} 的灯光接收者与需要照明的薄板不一致")
            if not expected_lit and entry["receiver_objects"]:
                raise AcceptanceFailure(f"{name} 不应创建灯光接收者 Collection")
        if render_path is not None:
            report["png"] = _png_stats(render_path)
        inspections.append(report)
        return report

    try:
        for material_id in MATERIAL_IDS:
            spec = material_spec(material_id)
            name = f"material-{material_id}"
            try:
                layout = "data_aligned" if spec.prefers_data_aligned else "separated_right"
                _result, request = export_case(
                    name, [make_blender_snapshot()], {key: material_id for key in BOX_PRESETS},
                    layout,
                )
                report = inspect_case(name, request)
                entry = report["entries"][0]
                record(
                    f"八材质 · {spec.label}（{material_id}）", "PASS",
                    f"布局 {layout}；节点 {entry['boxes'][0]['material_report']['node_types']}；"
                    f"发光 {entry['boxes'][0]['material_report']['emission']['effective']}；"
                    f"辅助灯 {len(entry['lights'])}；渲染可见像素 {report['png']['visible_pixels']}",
                )
            except Exception as exc:
                record(f"八材质 · {material_id}", "FAIL", f"{type(exc).__name__}: {exc}")
                print(traceback.format_exc(), flush=True)

        try:
            materials = {key: "flat_emission" for key in BOX_PRESETS}
            materials.update(matte="matte", optical_glass="optical_glass")
            _result, request = export_case(
                "mixed", [make_blender_snapshot()], materials, "separated_right",
            )
            report = inspect_case("mixed", request)
            entry = report["entries"][0]
            receiver = set(entry["receiver_objects"])
            if receiver != {"BandScope orange matte cuboid", "BandScope orange optical_glass cuboid"}:
                raise AcceptanceFailure(f"混合材质灯光接收者不正确：{sorted(receiver)}")
            if "BandScope Volume" in receiver:
                raise AcceptanceFailure("科学体数据被辅助灯直接照亮")
            record("混合材质互不联动且只照亮需要的薄板", "PASS",
                   f"接收者 {sorted(receiver)}；数据体不在接收者内")
        except Exception as exc:
            record("混合材质", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        try:
            if "mixed" not in projects:
                raise AcceptanceFailure("缺少混合材质工程")
            mixed_blend = projects["mixed"]
            mixed_assets = output / f"{mixed_blend.stem}.assets"
            moved_root = output / "moved 中文 空格"
            moved_root.mkdir(exist_ok=True)
            moved_blend = moved_root / mixed_blend.name
            shutil.copy2(mixed_blend, moved_blend)
            shutil.copytree(mixed_assets, moved_root / mixed_assets.name, dirs_exist_ok=True)
            moved_report = inspect_case("mixed-moved", request, blend_path=moved_blend)
            expected = {key: ("flat_emission", False) for key in BOX_PRESETS}
            expected.update(matte=("matte", True), optical_glass=("optical_glass", True))
            moved_lights = {
                light["preset"] for light in moved_report["entries"][0]["lights"]
            }
            actual = {
                box["preset"]: (box["material_id"], box["preset"] in moved_lights)
                for box in moved_report["entries"][0]["boxes"]
            }
            if actual != expected:
                raise AcceptanceFailure(f"目录搬移后材质/灯光不符：{actual}")
            before = hashlib.sha256(mixed_blend.read_bytes()).hexdigest()
            project = load_blender_project(mixed_blend)
            snapshot = project.entries[0].snapshot
            update_snapshot_display(
                snapshot, cmap_name="viridis", levels_params=(5.0, 60.0, 90.0),
                locked_range=None, include_zero=True, opacity_mode="power",
            )
            BlenderProjectUpdateJob(
                project, {"entry-001": snapshot}, blender,
                log_path=logs / "blender-material-mixed-update.log",
            ).run()
            after = hashlib.sha256(mixed_blend.read_bytes()).hexdigest()
            if before != after:
                raise AcceptanceFailure("原位更新改写了 .blend 字节")
            updated_report = inspect_case("mixed-updated", request, blend_path=mixed_blend)
            updated_lights = {
                light["preset"] for light in updated_report["entries"][0]["lights"]
            }
            updated = {
                box["preset"]: (box["material_id"], box["preset"] in updated_lights)
                for box in updated_report["entries"][0]["boxes"]
            }
            if updated != expected:
                raise AcceptanceFailure(f"原位更新后材质/灯光不符：{updated}")
            record(
                "混合材质：目录搬移、原位更新与 .blend 摘要不变", "PASS",
                ".blend 字节摘要不变；材质与灯光链接在搬移和数组更新后保持",
            )
        except Exception as exc:
            record("混合材质搬移与原位更新", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        try:
            presets = {key: False for key in request.presets}
            presets["thin_boxes"] = True
            _result, request = export_case(
                "partial", [make_blender_snapshot()], None, None, presets,
            )
            report = inspect_case("partial", request)
            entry = report["entries"][0]
            if [box["preset"] for box in entry["boxes"]] != list(BOX_PRESETS):
                raise AcceptanceFailure("统一薄层预设没有创建全部八种材质")
            expected_lights = {
                spec.material_id for spec in map(material_spec, BOX_PRESETS) if spec.requires_light
            }
            if {light["preset"] for light in entry["lights"]} != expected_lights:
                raise AcceptanceFailure("默认八薄板的辅助灯集合不正确")
            record("统一薄层预设创建八种默认橙色薄板", "PASS", "八块独立薄板及四个配套 Area 灯")
        except Exception as exc:
            record("统一薄层预设", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        try:
            right_name = "data-translucent-right"
            _result, right_request = export_case(
                right_name, [make_blender_snapshot()],
                {key: "data_translucent" for key in BOX_PRESETS}, "separated_right",
            )
            right_report = inspect_case(right_name, right_request)
            aligned_name = "data-translucent-aligned"
            _result, aligned_request = export_case(
                aligned_name, [make_blender_snapshot()],
                {key: "data_translucent" for key in BOX_PRESETS}, "data_aligned",
            )
            aligned_report = inspect_case(aligned_name, aligned_request)
            right_entry = right_report["entries"][0]
            aligned_entry = aligned_report["entries"][0]
            if right_entry["lights"] or aligned_entry["lights"]:
                raise AcceptanceFailure("数据漫透射创建了辅助灯")
            if any(box["material_report"]["emission"]["effective"] for box in aligned_entry["boxes"]):
                raise AcceptanceFailure("数据漫透射存在薄板自发光")
            if not all(box["intersects_data"] for box in aligned_entry["boxes"]):
                raise AcceptanceFailure("同轴布局的数据漫透射没有覆盖数据体")
            difference = float(np.mean(np.abs(
                np.asarray(right_report["png"]["mean_rgb"])
                - np.asarray(aligned_report["png"]["mean_rgb"])
            )))
            if difference <= 1e-3:
                raise AcceptanceFailure("右侧与同轴布局的漫透射渲染没有可测差异")
            series = []
            for samples in (int(value) for value in args.sample_series.split(",") if value.strip()):
                series_report = inspect_case(
                    f"data-translucent-aligned-{samples}samples", aligned_request,
                    samples=samples, long_edge=args.sample_series_long_edge,
                    blend_path=projects[aligned_name],
                )
                series.append({
                    "samples": samples,
                    "png": series_report["png"],
                })
            record(
                "数据漫透射：右置 vs 同轴、无自发光/无辅助灯与采样序列", "PASS",
                f"平均颜色差 {difference:.5f}；采样 "
                + ", ".join(f"{item['samples']}={item['png']['visible_pixels']}px" for item in series),
            )
        except Exception as exc:
            record("数据漫透射", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        try:
            window_name = "transmission-filter-window"
            _result, window_request = export_case(
                window_name, [make_blender_snapshot()],
                {key: {"material": "transmission_filter", "parameters": {"filter_strength": 0.0}}
                 for key in BOX_PRESETS},
                "data_aligned",
            )
            window_report = inspect_case(window_name, window_request)
            tinted_name = "transmission-filter-tinted"
            _result, tinted_request = export_case(
                tinted_name, [make_blender_snapshot()],
                {key: {"material": "transmission_filter", "parameters": {"filter_strength": 0.8}}
                 for key in BOX_PRESETS},
                "data_aligned",
            )
            tinted_report = inspect_case(tinted_name, tinted_request)
            if not all(box["intersects_data"] for box in window_report["entries"][0]["boxes"]):
                raise AcceptanceFailure("透射窗没有与数据体重叠")
            tint_delta = float(np.mean(np.abs(
                np.asarray(window_report["png"]["mean_rgb"])
                - np.asarray(tinted_report["png"]["mean_rgb"])
            )))
            if tint_delta <= 1e-3:
                raise AcceptanceFailure("滤色强度没有产生可测的透射染色")
            window_colors = [box["material_report"]["node_types"] for box in
                             window_report["entries"][0]["boxes"]]
            record(
                "透射窗／滤色片：同轴光路与强度 0/0.8 差异", "PASS",
                f"平均颜色差 {tint_delta:.5f}；前后双表面连续滤色；节点 {window_colors[0]}",
            )
        except Exception as exc:
            record("透射窗／滤色片", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        try:
            first = make_blender_snapshot()
            first.snapshot_id = "material-multi-first"
            first.source_session_id = "source-a"
            first.source_page_title = "材质多页一"
            second = make_blender_snapshot()
            second.snapshot_id = "material-multi-second"
            second.source_session_id = "source-b"
            second.source_page_title = "材质多页二"
            materials = {key: "flat_emission" for key in BOX_PRESETS}
            materials.update(matte="matte", reflection_glass="reflection_glass")
            _result, request = export_case(
                "multi-page", [first, second], materials, "separated_right",
            )
            report = inspect_case("multi-page", request)
            if len(report["entries"]) != 2:
                raise AcceptanceFailure("多页验收条目数不是 2")
            if not report["overview_camera"] or not report["overview_camera"]["covers_data_bounds"]:
                raise AcceptanceFailure("总览相机没有覆盖排除灯光后的数据组")
            first_entry, second_entry = report["entries"]
            if first_entry["actual_non_light_bounds"][1] >= second_entry["actual_non_light_bounds"][0]:
                raise AcceptanceFailure("多页条目在排除灯光后的边界沿世界 X 相撞")
            receivers = {entry["receiver_collection"] for entry in report["entries"]}
            if len(receivers) != 2:
                raise AcceptanceFailure("多页条目的灯光接收者 Collection 没有独立命名")
            record(
                "多页材质工程：独立灯光、无碰撞与总览取景排除灯光", "PASS",
                f"两页接收者 {sorted(receivers)}；条目边界不相撞",
            )
        except Exception as exc:
            record("多页材质工程", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        try:
            if "mixed" not in projects or "partial" not in projects:
                raise AcceptanceFailure("缺少 Append 所需的基础工程")
            combined = output / "appended.blend"
            report_path = output / "appended-report.json"
            run_blender(
                ["append", "--base", projects["mixed"], "--append", projects["partial"],
                 "--output", combined, "--report", report_path,
                 "--collection", "BandScope Data"],
                "blender-material-append.log",
            )
            report = json.loads(Path(report_path).read_text(encoding="utf-8"))
            if not report["saved_and_reopened"] or not report["camera_unchanged"]:
                raise AcceptanceFailure("Append 后保存重开或目标相机检查失败")
            if (report["volume_count_after"] != 2 or report["box_light_count_after"] != 6
                    or report["text_light_count_after"] != 1):
                raise AcceptanceFailure(
                    f"Append 后体积/薄板灯/文本灯数量不符：{report['volume_count_after']}/"
                    f"{report['box_light_count_after']}/{report['text_light_count_after']}"
                )
            if not report["all_lights_linked"]:
                raise AcceptanceFailure("Append 后有辅助灯失去灯光链接")
            for item in report["light_linking_after"]:
                if not item["receiver_objects"]:
                    raise AcceptanceFailure("Append 后灯光接收者为空")
            record(
                "Append 后材质、灯光链接与目标相机保持", "PASS",
                f"体积 {report['volume_count_after']}、灯光 {report['light_count_after']}；"
                "全部仍链接到接收者 Collection",
            )
        except Exception as exc:
            record("Append", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        if args.data:
            name = "real-data-mixed"
            try:
                if not args.data.is_file():
                    raise FileNotFoundError(args.data)
                snapshot = _real_snapshot(args.data)
                _result, request = export_case(
                    name, [snapshot],
                    {"data_translucent": "data_translucent",
                     "transmission_filter": "transmission_filter",
                     "optical_glass": "optical_glass"},
                    "data_aligned",
                )
                report = inspect_case(name, request, samples=max(args.samples, 128))
                record(
                    "WSe2 真实数据：同轴漫透射／滤色／玻璃", "PASS",
                    f"可见像素 {report['png']['visible_pixels']}；"
                    f"灯光 {len(report['entries'][0]['lights'])}",
                )
            except Exception as exc:
                record("WSe2 真实数据", "FAIL", f"{type(exc).__name__}: {exc}")
                print(traceback.format_exc(), flush=True)
    finally:
        payload = {
            "blender": str(blender),
            "output_dir": str(output),
            "long_edge": args.long_edge,
            "samples": args.samples,
            "records": records,
            "inspections": inspections,
            "manual_visual_review": (
                "NOT RUN: maintainer must review all eight material renders, both layouts, "
                "lighting highlights and transparency."
            ),
            "frozen_packages": "NOT RUN by this script.",
        }
        (output / "acceptance-report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        lines = [
            "# BandScope → Blender 八材质验收", "",
            f"- Blender：`{blender}`",
            f"- 输出：`{output}`",
            f"- 渲染基准：长边 {args.long_edge} 像素、{args.samples} 采样；数据漫透射另跑 "
            f"{args.sample_series} 采样、长边 {args.sample_series_long_edge}", "",
        ]
        for item in records:
            lines.append(f"- **{item['status']}** {item['name']}：{item['detail']}")
        lines += [
            "",
            "未由本脚本完成：维护者人工视觉复核（八材质观感、灯光高光、透明与折射效果）；"
            "CPU／NVIDIA 冻结安装包验收。",
        ]
        (output / "acceptance-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if any(item["status"] == "FAIL" for item in records) else 0


if __name__ == "__main__":
    sys.exit(main())
