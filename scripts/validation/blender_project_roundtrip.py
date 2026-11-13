# -*- coding: utf-8 -*-
"""Real Blender multi-entry, five-preset and in-place display-update acceptance."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from bandscope.exporting.blender_export import BlenderProcessError, detect_blender
from bandscope.exporting.blender_jobs import BlenderProjectExportJob, BlenderProjectUpdateJob
from bandscope.exporting.blender_project import (
    BlenderProjectRequest, load_blender_project, update_snapshot_display,
)
from tests.support.blender import make_blender_snapshot
from bandscope.core.display_style import new_display_style, new_render_quality


def _hidden_kwargs():
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {"startupinfo": startup, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / ".local/outputs/blender-project-roundtrip")
    args = parser.parse_args()
    blender = detect_blender(None, args.blender)
    if blender is None:
        parser.error("未检测到 Blender")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    logs = REPO_ROOT / ".local/logs"
    logs.mkdir(parents=True, exist_ok=True)
    inspector = REPO_ROOT / "scripts/validation/blender_project_scene_acceptance.py"
    records = []

    def record(name, status, detail=""):
        records.append({"name": name, "status": status, "detail": str(detail)})
        print(f"[{status}] {name}: {detail}", flush=True)

    def run_inspector(command, blend, report, *, render=None, decorations=False, append_collection=None):
        script = inspector if command in {"check", "decorate", "redirect"} else REPO_ROOT / "scripts/validation/blender_scene_acceptance.py"
        if command == "append":
            arguments = [command, "--base", blend[0], "--append", blend[1], "--output", blend[2], "--report", report]
            if append_collection:
                arguments += ["--collection", append_collection]
        else:
            arguments = [command, "--blend", blend, "--report", report]
            if render:
                arguments += ["--render", render, "--samples", "8"]
            if decorations:
                arguments += ["--require-decorations"]
        completed = subprocess.run(
            [str(blender), "--background", "--factory-startup", "--python", str(script), "--", *map(str, arguments)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            timeout=900, **_hidden_kwargs(),
        )
        log = logs / f"blender-project-{command}-{len(records)}.log"
        log.write_text(completed.stdout or "", encoding="utf-8")
        if completed.returncode != 0:
            raise RuntimeError(f"Blender {command} 失败：\n" + "\n".join((completed.stdout or "").splitlines()[-25:]))
        return json.loads(Path(report).read_text(encoding="utf-8"))

    try:
        first = make_blender_snapshot(cmap_name="magma", levels=(0.0, 45.0, 100.0))
        first.snapshot_id = "multi-first"
        first.source_session_id = "source-a"
        first.source_page_title = "第一页"
        style = new_display_style(first.levels_params, "linear")
        style["normalization"]["mode"] = "manual"
        style["normalization"]["manual_range"] = (
            first.payload["level_info"]["data_min"], first.payload["level_info"]["data_max"],
        )
        style["opacity"].update(threshold=21.739, gamma=1.3, density=40.0, preset="strong")
        first.payload["display"] = {
            "background_style": style, "resolved_range": style["normalization"]["manual_range"],
            "state": None,
        }
        first.payload["render_quality"] = new_render_quality()
        second = make_blender_snapshot(cmap_name="viridis", levels=(5.0, 65.0, 95.0), viewport_aspect=1.5)
        second.snapshot_id = "multi-second"
        second.source_session_id = "source-b"
        second.source_page_title = "第二页"
        second.payload["volume"] = second.payload["volume"] * 2.0 + 0.3
        from bandscope.exporting.publication_renderers import compute_level_info
        second.payload["level_info"] = compute_level_info(second.payload["volume"], second.levels_params)
        multi_blend = output / "multi.blend"
        try:
            request = BlenderProjectRequest([first, second])
            BlenderProjectExportJob(request, blender, multi_blend, overwrite=True).run()
            report = run_inspector("check", multi_blend, output / "multi-check.json", render=output / "multi-overview.png")
            if report["entry_count"] != 2 or not report["non_colliding"]:
                raise RuntimeError("多页条目或布局报告不符")
            if report.get("text_count") != 20:
                raise RuntimeError("多页工程没有生成两组独立的十种文本预设")
            loaded = load_blender_project(multi_blend)
            edit = loaded.metadata["entries"][0]["bandscope_edit"]
            if (loaded.schema_version != 11 or edit.get("format_version") != 2
                    or edit["display_style"].get("display_mode") != "independent"
                    or edit["render_quality"].get("mode") != "explicit"):
                raise RuntimeError("schema 11 独立显示或编辑格式 2 未完整写入／回读")
            record("多页面、五项预设、独立 VDB/材质/文本与总览相机", "PASS",
                   f"{report['overview_camera']}；schema 11 独立显示与编辑格式 2 完整")
        except Exception as exc:
            record("多页面与五预设", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        partial_presets = {key: False for key in request.presets}
        partial_presets.update(coordinates=True, thin_boxes=True)
        partial_blend = output / "partial.blend"
        try:
            BlenderProjectExportJob(
                BlenderProjectRequest([first], partial_presets), blender, partial_blend, overwrite=True,
            ).run()
            partial_report = run_inspector("check", partial_blend, output / "partial-check.json")
            if partial_report.get("text_count") != 0:
                raise RuntimeError("关闭文本预设后仍创建了文本对象")
            record("部分预设只创建勾选对象", "PASS", "坐标轴及八块橙色薄层长方体；无文本或文本字体／灯组")
        except Exception as exc:
            record("部分预设", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        moved = output / "moved 中文 空格" / "multi.blend"
        try:
            moved.parent.mkdir(exist_ok=True)
            shutil.copy2(multi_blend, moved)
            shutil.copytree(output / "multi.assets", moved.parent / "multi.assets", dirs_exist_ok=True)
            run_inspector("decorate", moved, output / "decorated.json")
            decorated_hash = _sha(moved)
            project = load_blender_project(moved)
            snapshot = project.entries[0].snapshot
            update_snapshot_display(
                snapshot, cmap_name="plasma", levels_params=(8.0, 70.0, 88.0),
                locked_range=(0.0, 0.75), include_zero=True, opacity_mode="power",
            )
            BlenderProjectUpdateJob(project, {"entry-001": snapshot}, blender).run()
            if _sha(moved) != decorated_hash:
                raise RuntimeError("原位更新改写了 .blend 字节")
            report = run_inspector(
                "check", moved, output / "updated-check.json",
                render=output / "updated-overview.png", decorations=True,
            )
            if report.get("text_count") != 20:
                raise RuntimeError("原位更新后十种文本预设没有完整保留")
            record("目录搬移后回读、显示编辑与原位更新", "PASS", ".blend 摘要不变；VDB 与新 arrays 一致；创作对象和文本保留")
        except Exception as exc:
            record("回读与原位更新", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        try:
            combined = output / "combined-multi.blend"
            report_path = output / "combined-multi.json"
            run_inspector(
                "append", (partial_blend, moved, combined), report_path,
                append_collection="BandScope Data · entry-002",
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if report["volume_count_after"] != 2 or not report["camera_unchanged"]:
                raise RuntimeError("多页 Append 报告不符")
            record("多页条目 Append 后保存重开", "PASS", "目标相机不变；外部资产可加载")
        except Exception as exc:
            record("多页 Append", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)

        try:
            run_inspector("redirect", moved, output / "redirected.json")
            redirected = load_blender_project(moved)
            arrays_hash = _sha(redirected.entries[0].arrays_path)
            blend_hash = _sha(moved)
            try:
                BlenderProjectUpdateJob(
                    redirected, {"entry-001": redirected.entries[0].snapshot}, blender,
                ).run()
            except BlenderProcessError as exc:
                if "改向" not in str(exc):
                    raise
            else:
                raise RuntimeError("VDB 路径改向后更新没有失败")
            if _sha(redirected.entries[0].arrays_path) != arrays_hash or _sha(moved) != blend_hash:
                raise RuntimeError("路径改向失败后旧资产或 .blend 被改写")
            record("VDB 路径改向必须失败并保留旧工程", "PASS", "只读检查拒绝更新；摘要不变")
        except Exception as exc:
            record("路径改向负向验收", "FAIL", f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)
    finally:
        payload = {
            "blender": str(blender), "records": records,
            "manual_visual_review": "NOT RUN: maintainer must review overview, transparency and updated render.",
        }
        (output / "roundtrip-report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = ["# Blender 多页面与原位更新验收", ""]
        lines += [f"- **{item['status']}** {item['name']}：{item['detail']}" for item in records]
        lines += ["", "维护者人工视觉复核未由本脚本代替。"]
        (output / "roundtrip-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if any(item["status"] == "FAIL" for item in records) else 0


if __name__ == "__main__":
    sys.exit(main())
