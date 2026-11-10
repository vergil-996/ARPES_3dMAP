# -*- coding: utf-8 -*-
"""BandScope → Blender real conversion, projection, render, move and Append acceptance.

Usage:
  python scripts/validation/blender_export_acceptance.py --blender D:/blender/blender.exe
  python scripts/validation/blender_export_acceptance.py --blender D:/blender/blender.exe --data .local/data/WSe2_step.npz
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from bandscope.exporting.blender_export import BlenderExportJob, detect_blender
from bandscope.exporting.publication_renderers import compute_level_info
from bandscope.processing.display_transfer import channels
from tests.support.blender import make_blender_snapshot


def _hidden_kwargs():
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {"startupinfo": startup, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _run_blender(blender, arguments, log_path):
    command = [str(blender), "--background", "--factory-startup", "--python",
               str(REPO_ROOT / "scripts/validation/blender_scene_acceptance.py"), "--", *map(str, arguments)]
    completed = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", timeout=900,
        **_hidden_kwargs(),
    )
    log_path.write_text(completed.stdout or "", encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError("Blender 验收进程失败；日志末尾：\n" + "\n".join((completed.stdout or "").splitlines()[-20:]))
    return completed.stdout


def _check_png(path):
    from PIL import Image

    with Image.open(path) as image:
        rgba = np.asarray(image.convert("RGBA"))
    alpha = rgba[..., 3]
    visible = int(np.count_nonzero(alpha))
    if visible < 100 or int(alpha.max()) == 0:
        raise RuntimeError(f"渲染图没有足够可见像素：{path}")
    if int(alpha.min()) != 0:
        raise RuntimeError(f"渲染图没有透明背景：{path}")
    return {"visible_pixels": visible, "alpha_min": int(alpha.min()), "alpha_max": int(alpha.max())}


def _camera(parallel):
    return {
        "position": (250.0, -350.0, 280.0), "focal_point": (100.0, 100.0, 100.0),
        "view_up": (0.0, 0.0, 1.0), "parallel_projection": parallel,
        "parallel_scale": 120.0, "view_angle": 35.0, "clipping_range": (1.0, 2000.0),
    }


def _real_snapshot(path):
    with np.load(path, allow_pickle=False) as data:
        volume = np.array(data["sample"][..., 0], dtype=np.float32, copy=True)
        kx = np.array(data["kx"], dtype=np.float64, copy=True)
        ky = np.array(data["ky"], dtype=np.float64, copy=True)
        delay = np.array(data["time"], dtype=np.float64, copy=True)
    shape = volume.shape
    bounds = (0, shape[0] - 1, 0, shape[1] - 1, 0, shape[2] - 1)
    snapshot = make_blender_snapshot(
        volume=volume, full_shape=shape, data_bounds=bounds,
        levels=(10.0, 80.0, 99.5),
        clip_render_bounds=(20.0, 180.0, 20.0, 180.0, 25.0, 175.0),
        viewport_aspect=1.5, camera=_camera(True),
        opacity_multiplier=np.linspace(0.3, 1.0, shape[2], dtype=np.float32),
    )
    snapshot.coords.update(X=kx, Y=ky, E=np.arange(shape[2], dtype=np.float64), delay=delay)
    snapshot.coord_sources.update(X="file", Y="file", E="index", delay="file")
    snapshot.coord_units.update(X="Å⁻¹", Y="Å⁻¹", E=None, delay="fs")
    snapshot.source_page_title = "WSe2 帧0 弱信号与局部增强"
    snapshot.source_desc = f"{Path(path).name} · 帧 0 · delay {delay[0]:.4g} fs · 局部增强与裁空"
    snapshot.payload["axis_titles"] = ["kx (Å⁻¹)", "ky (Å⁻¹)", "E (index)"]
    info = snapshot.payload["level_info"]
    color, alpha = channels(volume, info, "linear")
    region = (slice(45, 105), slice(45, 105), slice(60, 140))
    region_info = compute_level_info(volume[region], (0.0, 65.0, 100.0))
    region_color, region_alpha = channels(volume[region], region_info, "linear")
    color[region] = region_color
    alpha[region] = region_alpha
    snapshot.payload["enhancement"] = {
        "active": True, "color": color, "alpha": alpha,
        "background_level_info": info, "common": False,
    }
    return snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", help="blender.exe；缺省时按保存路径、PATH、注册表和标准目录检测")
    parser.add_argument("--data", type=Path, help="可选：额外验收 WSe2_step.npz 的第 0 帧")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / ".local/outputs/blender-export-acceptance")
    parser.add_argument("--long-edge", type=int, default=1024)
    parser.add_argument("--samples", type=int, default=24)
    args = parser.parse_args()
    blender = detect_blender(None, args.blender)
    if blender is None:
        parser.error("未检测到 Blender；请通过 --blender 指定 blender.exe")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    logs = REPO_ROOT / ".local/logs"
    logs.mkdir(parents=True, exist_ok=True)
    inspector = REPO_ROOT / "scripts/validation/blender_scene_acceptance.py"
    records = []
    reports = []
    projects = {}

    def record(name, status, detail=""):
        item = {"name": name, "status": status, "detail": str(detail)}
        records.append(item)
        print(f"[{status}] {name}: {detail}", flush=True)

    def export_case(name, snapshot):
        final = output / f"{name}.blend"
        job = BlenderExportJob(
            snapshot, blender, final, overwrite=True,
            log_path=logs / f"blender-acceptance-{name}-conversion.log",
        )
        result = job.run()
        projects[name] = final
        return result

    def inspect_case(name, *, moved=None, add_plane=False):
        blend = moved or projects[name]
        report_path = output / f"{name}{'-moved' if moved else ''}-inspection.json"
        render = None if moved else output / f"{name}.png"
        arguments = ["inspect", "--blend", blend, "--report", report_path,
                     "--long-edge", args.long_edge, "--samples", args.samples]
        if render:
            arguments += ["--render", render]
        if add_plane:
            arguments += ["--add-plane"]
        _run_blender(blender, arguments, logs / f"blender-acceptance-{name}-inspection.log")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report["max_projection_error_pixels"] > 1.0:
            raise RuntimeError("投影误差超过 1 像素")
        if render:
            report["png"] = _check_png(render)
        reports.append(report)
        return report

    try:
        for parallel, aspect in ((True, 1.6), (True, 0.625), (False, 1.6), (False, 0.625)):
            name = f"{'ortho' if parallel else 'persp'}-{'landscape' if aspect > 1 else 'portrait'}"
            try:
                snapshot = make_blender_snapshot(
                    camera=_camera(parallel), viewport_aspect=aspect,
                    clip_render_bounds=(30.0, 180.0, 40.0, 190.0, 20.0, 180.0),
                    opacity_multiplier=np.linspace(0.3, 1.2, 5, dtype=np.float32),
                )
                snapshot.payload["volume"][0, 0, 0] = np.nan
                snapshot.payload["volume"][1, 1, 1] = np.inf
                export_case(name, snapshot)
                report = inspect_case(name)
                record(name, "PASS", f"投影误差 {report['max_projection_error_pixels']:.6f} px；VDB 回读与 CPU 渲染通过")
            except Exception as exc:
                record(name, "FAIL", f"{type(exc).__name__}: {exc}")
                print(traceback.format_exc(), flush=True)

        try:
            snapshot = make_blender_snapshot(camera=_camera(True), viewport_aspect=1.5, show_axes=False)
            export_case("guide-frame-no-axes", snapshot)
            report = inspect_case("guide-frame-no-axes")
            guide = report["guide_frame"]
            if not guide["larger_than_axis_frame"] or not guide["encloses_data_bounds"]:
                raise RuntimeError("辅助盒框没有通过大于坐标轴框／包住数据体的检查")
            indicator = report["axis_indicator"]
            keys = [axis["key"] for axis in indicator["axes"]]
            if (not indicator["all_present"] or not indicator["visible_by_default"]
                    or indicator["style"] != "solid_world_axes"
                    or set(keys) != {"X", "Y", "E"}
                    or not all(axis["monochrome"] for axis in indicator["axes"])):
                raise RuntimeError("图片样式的三轴方向标未完整生成或默认不可见")
            record(
                "主视图坐标轴开关关闭时的辅助盒框与三轴方向标", "PASS",
                f"盒框 {guide['edge_count']} 条边；单色方向标 {keys} 默认可见；父对象 {guide['parent']}",
            )
        except Exception as exc:
            record("主视图坐标轴开关关闭时的辅助盒框与三轴方向标", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        try:
            camera = _camera(True)
            focal = np.asarray(camera["focal_point"], dtype=np.float64)
            distance = np.linalg.norm(np.asarray(camera["position"]) - focal)
            camera["position"] = tuple(focal + np.asarray((0.0, -distance, 0.0)))
            snapshot = make_blender_snapshot(camera=camera, viewport_aspect=1.5)
            export_case("orientation-depth-facing", snapshot)
            report = inspect_case("orientation-depth-facing")
            indicator = report["axis_indicator"]
            depth = indicator["axes"][2]
            if depth["kind"] != "arrow" or depth["label"] != "ky":
                raise RuntimeError("正视纵深轴没有保留立体箭头和 ky 短标签")
            record(
                "立体方向标的纵深正视", "PASS",
                f"三种角色 {[axis['kind'] for axis in indicator['axes']]}；"
                "方向标保留三维箭杆和封闭圆锥",
            )
        except Exception as exc:
            record("立体方向标的纵深正视", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        if projects:
            try:
                first = next(iter(projects))
                moved_root = output / "moved 中文 空格"
                moved_root.mkdir(exist_ok=True)
                moved_blend = moved_root / projects[first].name
                shutil.copy2(projects[first], moved_blend)
                shutil.copytree(output / f"{projects[first].stem}.assets",
                                moved_root / f"{projects[first].stem}.assets", dirs_exist_ok=True)
                inspect_case(first, moved=moved_blend)
                record("整目录搬移重开", "PASS", str(moved_blend))
            except Exception as exc:
                record("整目录搬移重开", "FAIL", f"{type(exc).__name__}: {exc}")

        if len(projects) >= 2:
            try:
                first, second = list(projects)[:2]
                combined = output / "combined.blend"
                report_path = output / "combined-inspection.json"
                _run_blender(blender, ["append", "--base", projects[first], "--append", projects[second],
                                       "--output", combined, "--report", report_path],
                             logs / "blender-acceptance-append.log")
                report = json.loads(report_path.read_text(encoding="utf-8"))
                if not report["saved_and_reopened"] or not report["camera_unchanged"] or report["volume_count_after"] != 2:
                    raise RuntimeError("Append 报告不符合契约")
                record("两个工程 Append 后保存重开", "PASS", "目标相机未替换；两个外部 VDB 均可重载")
            except Exception as exc:
                record("两个工程 Append 后保存重开", "FAIL", f"{type(exc).__name__}: {exc}")
                print(traceback.format_exc(), flush=True)

        if args.data:
            name = "WSe2-frame0-enhanced"
            try:
                if not args.data.is_file():
                    raise FileNotFoundError(args.data)
                snapshot = _real_snapshot(args.data)
                export_case(name, snapshot)
                report = inspect_case(name, add_plane=True)
                record("WSe2 弱信号、局部增强、裁空与辅助平面渲染", "PASS",
                       f"投影误差 {report['max_projection_error_pixels']:.6f} px；平面 {report['auxiliary_plane']}")
            except Exception as exc:
                record("WSe2 真实数据", "FAIL", f"{type(exc).__name__}: {exc}")
                print(traceback.format_exc(), flush=True)
    finally:
        payload = {
            "blender": str(blender), "output_dir": str(output), "long_edge": args.long_edge,
            "records": records, "inspection_reports": reports,
            "manual_visual_review": "NOT RUN: volume color/opacity, clipping boundaries, auxiliary-plane occlusion and Append layout require maintainer review.",
            "frozen_packages": "NOT RUN by this script.",
        }
        (output / "acceptance-report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = ["# BandScope → Blender 验收", "", f"- Blender：`{blender}`", f"- 输出：`{output}`", f"- 投影基准：长边 {args.long_edge} 像素、方形像素、误差上限 1 像素", ""]
        for item in records:
            lines.append(f"- **{item['status']}** {item['name']}：{item['detail']}")
        lines += ["", "未由本脚本完成：维护者人工视觉复核；CPU／NVIDIA 冻结安装包验收。真实数据渲染若包含辅助平面，需另行检查遮挡关系。"]
        (output / "acceptance-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if any(item["status"] == "FAIL" for item in records) else 0


if __name__ == "__main__":
    sys.exit(main())
