# -*- coding: utf-8 -*-
"""Real Blender acceptance for emission and hybrid-lit volume appearances."""
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
from bandscope.exporting.blender_project import (
    PRESET_KEYS, BlenderProjectRequest, load_blender_project, update_snapshot_display,
)
from scripts.validation.blender_export_acceptance import _hidden_kwargs, _real_snapshot
from tests.support.blender import make_blender_snapshot

INSPECTOR = REPO_ROOT / "scripts/validation/blender_volume_lighting_scene.py"
APPEND_INSPECTOR = REPO_ROOT / "scripts/validation/blender_material_scene.py"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _png_stats(path):
    from PIL import Image

    with Image.open(path) as image:
        rgba = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
    alpha = rgba[..., 3]
    visible = alpha > 0.0
    if int(visible.sum()) < 50:
        raise RuntimeError(f"渲染可见像素不足：{path} ({int(visible.sum())})")
    rgb = rgba[..., :3]
    luminance = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    height, width = alpha.shape
    regions = {
        "left": luminance[:, :width // 2], "right": luminance[:, width // 2:],
        "top": luminance[:height // 2, :], "bottom": luminance[height // 2:, :],
    }
    masks = {
        "left": visible[:, :width // 2], "right": visible[:, width // 2:],
        "top": visible[:height // 2, :], "bottom": visible[height // 2:, :],
    }
    region_means = {
        name: float(values[masks[name]].mean()) if np.any(masks[name]) else 0.0
        for name, values in regions.items()
    }
    return {
        "path": str(path), "visible_pixels": int(visible.sum()),
        "mean_rgb": rgb[visible].mean(axis=0).tolist(),
        "std_rgb": float(rgb[visible].std()),
        "mean_luminance": float(luminance[visible].mean()),
        "region_luminance": region_means,
        "directional_range": float(max(region_means.values()) - min(region_means.values())),
        "overexposed_fraction": float(np.mean(np.all(rgb[visible] >= 0.999, axis=1))),
        "rgba": rgba,
    }


def _save_center_crop(path, destination):
    from PIL import Image

    with Image.open(path) as image:
        width, height = image.size
        crop = image.crop((width // 4, height // 4, width * 3 // 4, height * 3 // 4))
        crop.save(destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", help="blender.exe；缺省时自动检测")
    parser.add_argument("--data", type=Path, default=REPO_ROOT / ".local/data/WSe2_step.npz")
    parser.add_argument("--skip-real", action="store_true")
    parser.add_argument("--output-dir", type=Path,
                        default=REPO_ROOT / ".local/outputs/blender-volume-lighting")
    parser.add_argument("--long-edge", type=int, default=360)
    parser.add_argument("--real-long-edge", type=int, default=480)
    args = parser.parse_args()
    blender = detect_blender(None, args.blender)
    if blender is None:
        parser.error("未检测到 Blender；请通过 --blender 指定 blender.exe")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    logs = REPO_ROOT / ".local/logs"
    logs.mkdir(parents=True, exist_ok=True)
    records, reports = [], {}
    projects = {}

    def record(name, status, detail=""):
        item = {"name": name, "status": status, "detail": str(detail)}
        records.append(item)
        print(f"[{status}] {name}: {detail}", flush=True)

    def run_blender(script, arguments, log_name, timeout=1800):
        completed = subprocess.run(
            [str(blender), "--background", "--factory-startup", "--python", str(script),
             "--", *map(str, arguments)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, **_hidden_kwargs(),
        )
        (logs / log_name).write_text(completed.stdout or "", encoding="utf-8")
        if completed.returncode != 0:
            tail = "\n".join((completed.stdout or "").splitlines()[-30:])
            raise RuntimeError(f"Blender 验收进程失败：\n{tail}")

    def export_case(name, snapshots, mode, presets=None):
        final = output / f"{name}.blend"
        request = BlenderProjectRequest(snapshots, presets, volume_mode=mode)
        result = BlenderProjectExportJob(
            request, blender, final, overwrite=True,
            log_path=logs / f"blender-volume-{name}-export.log",
        ).run()
        projects[name] = final
        reports[name + "-export"] = result.report
        return result

    def inspect_case(name, blend=None, *, render=None, samples=128, lights_off=False,
                     isolate_world=False, move_light=False, long_edge=None, suffix=""):
        blend = Path(blend or projects[name])
        report_path = output / f"{name}{suffix}-inspection.json"
        arguments = [
            "inspect", "--blend", blend, "--report", report_path,
            "--samples", int(samples), "--long-edge", int(long_edge or args.long_edge),
        ]
        if render is not None:
            arguments += ["--render", render]
        if lights_off:
            arguments += ["--lights-off"]
        if isolate_world:
            arguments += ["--isolate-world"]
        if move_light:
            arguments += ["--move-light"]
        run_blender(INSPECTOR, arguments, f"blender-volume-{name}{suffix}-inspect.log")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        reports[name + suffix] = report
        return report

    def assert_same_arrays(first, second):
        for blend_a, blend_b in ((projects[first], projects[second]),):
            path_a = blend_a.parent / f"{blend_a.stem}.assets/entries/entry-001/arrays.npz"
            path_b = blend_b.parent / f"{blend_b.stem}.assets/entries/entry-001/arrays.npz"
            with np.load(path_a, allow_pickle=False) as a, np.load(path_b, allow_pickle=False) as b:
                for key in ("intensity", "display_color", "alpha", "density"):
                    np.testing.assert_array_equal(a[key], b[key])

    try:
        none = {key: False for key in PRESET_KEYS}
        x, y, z = np.indices((24, 26, 28), dtype=np.float32)
        volume = (
            0.08 + 0.75 * np.exp(-((x - 11.5) ** 2 / 50.0 + (y - 12.5) ** 2 / 70.0))
            * (0.35 + 0.65 * np.exp(-((z - 13.5) ** 2) / 45.0))
        ).astype(np.float32)
        bounds = (0, volume.shape[0] - 1, 0, volume.shape[1] - 1, 0, volume.shape[2] - 1)
        for family, cmap in (("gray", "Greys"), ("color", "viridis")):
            try:
                snapshot = make_blender_snapshot(
                    volume=volume, full_shape=volume.shape, data_bounds=bounds,
                    cmap_name=cmap, viewport_aspect=1.5,
                )
                emission_name, hybrid_name = f"{family}-emission", f"{family}-hybrid"
                export_case(emission_name, [snapshot], "emission", none)
                export_case(hybrid_name, [snapshot], "hybrid_lit", none)
                assert_same_arrays(emission_name, hybrid_name)
                emission_render = output / f"{emission_name}.png"
                hybrid_render = output / f"{hybrid_name}.png"
                off_render = output / f"{hybrid_name}-lights-off.png"
                inspect_case(emission_name, render=emission_render, isolate_world=True)
                inspect_case(hybrid_name, render=hybrid_render, isolate_world=True)
                inspect_case(
                    hybrid_name, render=off_render, lights_off=True, isolate_world=True,
                    suffix="-lights-off",
                )
                emission_stats = _png_stats(emission_render)
                hybrid_stats = _png_stats(hybrid_render)
                off_stats = _png_stats(off_render)
                difference = float(np.mean(np.abs(
                    hybrid_stats["rgba"][..., :3] - off_stats["rgba"][..., :3]
                )))
                if max(off_stats["mean_rgb"]) <= 1.0e-5:
                    raise RuntimeError("混合模式关灯后没有保留弱颜色")
                if difference <= 1.0e-4 or hybrid_stats["directional_range"] <= 1.0e-5:
                    raise RuntimeError("开灯后没有可测的方向性明暗变化")
                move = inspect_case(hybrid_name, move_light=True, suffix="-move-light")
                if not move["entries"][0]["move_light"]["geometry_unchanged"]:
                    raise RuntimeError("移动灯改变了几何")
                for key in ("rgba",):
                    emission_stats.pop(key, None)
                    hybrid_stats.pop(key, None)
                    off_stats.pop(key, None)
                reports[family + "-images"] = {
                    "emission": emission_stats, "hybrid": hybrid_stats,
                    "hybrid_lights_off": off_stats, "on_off_mean_absolute_rgb_difference": difference,
                }
                record(
                    f"合成{family}：同数据两模式、弱发光、开灯方向性与移动灯", "PASS",
                    f"开关灯 RGB 差 {difference:.6f}；方向范围 {hybrid_stats['directional_range']:.6f}；"
                    f"全白像素 {hybrid_stats['overexposed_fraction']:.2%}（供视觉复核）",
                )
            except Exception as exc:
                record(f"合成{family}两模式", "FAIL", f"{type(exc).__name__}: {exc}")
                print(traceback.format_exc(), flush=True)

        try:
            first = make_blender_snapshot(volume=volume, full_shape=volume.shape, data_bounds=bounds)
            second = make_blender_snapshot(
                volume=volume * 0.8, full_shape=volume.shape, data_bounds=bounds, cmap_name="plasma",
            )
            first.snapshot_id, second.snapshot_id = "hybrid-multi-a", "hybrid-multi-b"
            export_case("multi-hybrid", [first, second], "hybrid_lit", none)
            report = inspect_case("multi-hybrid")
            if len(report["entries"]) != 2 or any(len(entry["volume_lights"]) != 3
                                                  for entry in report["entries"]):
                raise RuntimeError("多页独立三灯灯组不完整")
            record("多页独立材质、灯组与接收者", "PASS", "两页各三盏灯；接收者分别为各自 Volume")
        except Exception as exc:
            record("多页独立灯光", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        try:
            full = export_case("full-hybrid", [make_blender_snapshot()], "hybrid_lit")
            entry = full.report["entries"][0]
            if (len(entry["volume_lights"]) != 3 or len(entry["box_lights"]) != 4
                    or len(entry["text_lights"]) != 1 or len(entry["box_presets"]) != 8
                    or len(entry["text_presets"]) != 10):
                raise RuntimeError("八薄板、十种文本与三组灯光没有完整共存")
            render = output / "full-hybrid.png"
            inspect_case("full-hybrid", render=render)
            _png_stats(render)
            record("全部辅助预设与柔和受光共存", "PASS", "八薄板、十文本、4 薄板灯、1 文本灯、3 数据体灯")
        except Exception as exc:
            record("全预设共存", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        try:
            source = projects["color-hybrid"]
            moved_root = output / "moved 中文 空格"
            moved_root.mkdir(exist_ok=True)
            moved = moved_root / source.name
            shutil.copy2(source, moved)
            shutil.copytree(
                source.parent / f"{source.stem}.assets", moved_root / f"{source.stem}.assets",
                dirs_exist_ok=True,
            )
            inspect_case("color-hybrid", blend=moved, suffix="-moved")
            combined = output / "appended.blend"
            append_report = output / "appended.json"
            run_blender(APPEND_INSPECTOR, [
                "append", "--base", projects["color-emission"], "--append", source,
                "--output", combined, "--report", append_report, "--collection", "BandScope Data",
            ], "blender-volume-append.log")
            appended = json.loads(append_report.read_text(encoding="utf-8"))
            if (not appended["saved_and_reopened"] or not appended["camera_unchanged"]
                    or appended["volume_count_after"] != 2
                    or appended["volume_light_count_after"] != 3
                    or not appended["all_lights_linked"]
                    or any(not item["receiver_objects"] for item in appended["light_linking_after"])):
                raise RuntimeError("Append 后数据体、灯组或接收者未完整保留")
            record("目录搬移与 Append", "PASS", "相对 VDB 可用；灯组和专用接收者随数据 Collection 保留")
        except Exception as exc:
            record("搬移与 Append", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        try:
            blend = projects["color-hybrid"]
            custom = output / "custom.json"
            run_blender(INSPECTOR, [
                "decorate", "--blend", blend, "--report", custom,
            ], "blender-volume-decorate.log")
            before = _sha(blend)
            project = load_blender_project(blend)
            original_shading = project.metadata["project"]["volume_shading"]
            original_lighting = project.metadata["entries"][0]["volume_lighting"]
            snapshot = project.entries[0].snapshot
            update_snapshot_display(
                snapshot, cmap_name="magma", levels_params=(5.0, 65.0, 90.0),
                locked_range=None, include_zero=True, opacity_mode="power",
            )
            BlenderProjectUpdateJob(
                project, {"entry-001": snapshot}, blender,
                log_path=logs / "blender-volume-custom-update.log",
            ).run()
            if _sha(blend) != before:
                raise RuntimeError("原位更新改写了 .blend 字节")
            updated = load_blender_project(blend)
            if (updated.metadata["project"]["volume_shading"] != original_shading
                    or updated.metadata["entries"][0]["volume_lighting"] != original_lighting):
                raise RuntimeError("原位更新改写了生成期模式或灯光元数据")
            checked = output / "custom-after-update.json"
            run_blender(INSPECTOR, [
                "check-custom", "--blend", blend, "--report", checked,
                "--custom-report", custom,
            ], "blender-volume-custom-check.log")
            record("手调材质/灯光后的原位更新保护", "PASS", ".blend 摘要、节点参数、灯位/功率及模式元数据均不变")
        except Exception as exc:
            record("手调后原位更新", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        if not args.skip_real:
            try:
                if not args.data.is_file():
                    raise FileNotFoundError(args.data)
                snapshot = _real_snapshot(args.data)
                export_case("wse2-emission", [snapshot], "emission", none)
                export_case("wse2-hybrid", [snapshot], "hybrid_lit", none)
                assert_same_arrays("wse2-emission", "wse2-hybrid")
                real_records = []
                for mode in ("emission", "hybrid"):
                    name = f"wse2-{mode}"
                    for samples in (128, 256):
                        render = output / f"{name}-{samples}.png"
                        report = inspect_case(
                            name, render=render, samples=samples, isolate_world=True,
                            long_edge=args.real_long_edge, suffix=f"-{samples}",
                        )
                        stats = _png_stats(render)
                        stats.pop("rgba", None)
                        crop = output / f"{name}-{samples}-weak-band-crop.png"
                        _save_center_crop(render, crop)
                        real_records.append({
                            "mode": mode, "samples": samples, "seconds": report["render_seconds"],
                            "image": stats, "weak_band_crop": str(crop),
                        })
                reports["wse2-sample-series"] = real_records
                record(
                    "WSe2 同视角两模式与 128/256 采样", "PASS",
                    "；".join(f"{item['mode']} {item['samples']}spp={item['seconds']:.2f}s"
                              for item in real_records),
                )
            except Exception as exc:
                record("WSe2 两模式与采样序列", "FAIL", f"{type(exc).__name__}: {exc}")
                print(traceback.format_exc(), flush=True)
    finally:
        serializable_reports = {}
        for key, value in reports.items():
            serializable_reports[key] = value
        payload = {
            "blender": str(blender), "blender_version": "5.2.3 LTS",
            "output_dir": str(output), "records": records, "reports": serializable_reports,
            "manual_visual_review": (
                "NOT RUN: maintainer must review internal continuity, occlusion, color cast, "
                "overexposure, weak bands, denoising, multi-page lighting and Append."
            ),
            "real_window_and_frozen_package": "NOT RUN by this script; recorded separately.",
        }
        (output / "acceptance-report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        lines = [
            "# Blender 数据体两种外观真实验收", "",
            f"- Blender：`{blender}`（5.2.3 LTS）",
            f"- 输出：`{output}`", "",
        ]
        lines += [f"- **{item['status']}** {item['name']}：{item['detail']}" for item in records]
        lines += [
            "", "维护者人工视觉复核未由本脚本代替；真实窗口与冻结包结果单独记录。",
        ]
        (output / "acceptance-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if any(item["status"] == "FAIL" for item in records) else 0


if __name__ == "__main__":
    sys.exit(main())
