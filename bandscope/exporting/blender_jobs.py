# -*- coding: utf-8 -*-
"""多页面 Blender 工程导出及只更新配套资产的事务任务。"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile

import numpy as np

from bandscope.exporting import blender_export as _legacy
from bandscope.exporting.blender_export import (
    BlenderExportCancelled,
    BlenderExportError,
    BlenderExportJob,
    BlenderExportResult,
    BlenderOutputExistsError,
    BlenderProcessError,
    _lexists,
    _remove_path,
    assets_path_for,
    probe_blender,
)
from bandscope.exporting.blender_materials import material_spec
from bandscope.exporting.blender_project import (
    BLENDER_PROJECT_SCHEMA,
    BOX_GROUP_PRESET,
    BOX_PRESETS,
    LoadedBlenderProject,
    BlenderProjectError,
    BlenderProjectRequest,
    load_blender_project,
    make_project_metadata,
    merged_project_metadata,
    prepare_scene_for_update,
    file_fingerprint,
    project_fingerprints,
)
from bandscope.exporting.blender_scene import prepare_blender_scene


def project_usage_text(metadata, assets_dir_name):
    project = metadata["project"]
    presets = project["presets"]
    labels = project.get("preset_labels") or {}
    selected = [labels.get(key, key) for key, enabled in presets.items() if enabled]
    layout = project.get("box_layout") or {}
    box_materials = project.get("box_materials") or {}
    material_lines = []
    for key, record in box_materials.items():
        if not presets.get(BOX_GROUP_PRESET):
            continue
        parameters = ", ".join(
            f"{name}={value:g}" for name, value in sorted((record.get("parameters") or {}).items())
        )
        material_lines.append(
            f"{record.get('label', key)}（{record.get('material_id', key)}）"
            + (f"：{parameters}" if parameters else "")
        )
    lines = [
        "BandScope → Blender 多页面工程",
        "============================",
        "",
        f"schema：{metadata.get('schema_version')}",
        f"条目数：{len(metadata['entries'])}",
        f"条目顺序：{', '.join(project['entry_order'])}",
        f"启用预设：{', '.join(selected) if selected else '无'}",
        f"薄板布局：{layout.get('label', layout.get('id', '未记录'))}",
        "",
        "薄层长方体材质",
        "--------------",
        *(material_lines or ["未创建长方体。"]),
        "统一预设会为八种材质各创建一块橙色薄板，全部使用注册表默认参数。",
        "每块薄板拥有独立网格、独立材质和独立节点树；同一数据组与不同数据组都不共享材质。",
        (
            "平面发光与全息边缘自带发光；哑光、缎面、无折射玻璃和真实玻璃使用逐条目中性 Area 灯，"
            "并通过 Cycles 灯光链接只照亮需要外部照明的薄板。"
        ),
        "数据漫透射不使用薄板自发光；效果依赖数据体亮度和较高采样。",
        "透射窗／滤色片的前后两个表面会连续滤色两次；默认强度 0 为无染色透射窗。",
        "所有材质参数都可以在 Blender 节点的稳定名称下继续编辑；宿主不复制节点编辑器。",
        "",
        "文件与移动",
        "----------",
        f"请始终将 .blend 与整个 {assets_dir_name}/ 目录一起移动或分享。",
        "每个条目在 entries/<条目ID>/ 下拥有独立 arrays.npz 和 volume.vdb。",
        "工程通过相对路径引用 VDB；另存或 Append 不会自动复制这些资产。",
        "scene.json 是唯一权威编辑配置；.blend 内嵌元数据仅供生成期诊断。",
        "",
        "工程组织与预设",
        "--------------",
        "每个条目有独立 Collection、BandScope Root、体材质和 VDB，按页面树顺序沿世界 X 排列。",
        "科学网格保持原坐标，组间排布只平移父对象。多页活动相机为总览相机，各页原相机保留为备用。",
        "坐标轴及文字在左、辅助盒框在后、方向标在上；四种自发光／透射薄板在右，四种受光薄板在前并上移半层，移到数据体后八层交替。",
        "长方体是完整薄长方体，厚度固定 0.04；实际层间距与是否和数据体相交记录在 scene.json。",
        "辅助盒框 Collection 默认不参与渲染；未勾选的预设不会创建。",
        "辅助灯位于数据 Collection 内、父对象为 BandScope Root，不参与科学布局边界、取景和总览相机。",
        "",
        "回读与原位更新",
        "--------------",
        "BandScope 的独立 Blender 工程对话框可打开此工程，逐条目调整色带、三点色阶、锁定范围和不透明度。",
        "更新工程只替换修改条目的 VDB、arrays.npz 和 scene.json，不改写 .blend。",
        "相机、灯光、排版、附加对象及 Blender 中的后续创作因此保留。",
        "schema 7 旧工程仍可回读和原位更新，但会保留原版本与平面发光长方体，不升级为八材质工程。",
        "如果工程已经在 Blender 中打开，更新后必须重新载入 VDB 或重新打开工程。",
        "局部增强和插件透明度倍率属于已烘焙效果；基础显示编辑会丢弃这些效果并在保存前警告。",
        "Cycles 与 BandScope/VTK 的体积分方式不同，不承诺逐像素一致。",
        "",
        "科学坐标",
        "----------",
        "0–200 显示空间映射为居中的 2 个 Blender 单位；E、kx、ky 不是毫米。",
        "ROI、物理坐标、降序坐标和 E 轴相机翻转语义原样保留。",
        "体材质使用无散射吸收＋发射，密度采用对象空间；弱信号裁除阈值为 0。",
    ]
    return "\n".join(lines) + "\n"


def _box_selected(settings, presets):
    key = settings["preset"]
    return bool(presets.get(settings.get("preset_group", key), True))


def _expected_box_records(scene, presets, project):
    materials = (project or {}).get("box_materials") or {}
    records = {}
    for settings in scene.get("box_presets") or ():
        key = settings["preset"]
        if not _box_selected(settings, presets):
            continue
        record = dict(settings)
        record["project_material"] = dict(materials.get(key) or {})
        records[key] = record
    return records


def _validate_box_report(report, scene, project, presets):
    boxes = report.get("box_presets") or []
    expected_boxes = [
        key for key in BOX_PRESETS if presets.get(BOX_GROUP_PRESET, True)
    ]
    if [box.get("preset") for box in boxes] != expected_boxes:
        raise BlenderProcessError(f"条目 {scene['entry_id']} 的长方体预设与勾选不一致。")
    materials = [box.get("material") for box in boxes]
    if len(set(materials)) != len(materials) or not all(box.get("independent_material") for box in boxes):
        raise BlenderProcessError(f"条目 {scene['entry_id']} 的长方体意外共享材质。")
    if any(not box.get("full_cuboid") for box in boxes):
        raise BlenderProcessError(f"条目 {scene['entry_id']} 的长方体不是完整独立网格。")
    layout_id = ((project or {}).get("box_layout") or {}).get("id")
    if report.get("box_layout") != layout_id:
        raise BlenderProcessError(f"条目 {scene['entry_id']} 的薄板布局与工程配置不一致。")
    expected = _expected_box_records(scene, presets, project)
    lights = {light.get("preset"): light for light in report.get("box_lights") or ()}
    expected_lit = set()
    for box in boxes:
        key = box["preset"]
        settings = expected.get(key)
        if settings is None:
            raise BlenderProcessError(f"条目 {scene['entry_id']} 的报告含未知薄板 {key}。")
        material = settings["project_material"]
        material_id = material.get("material_id") or settings.get("material_id")
        if not material_id:
            raise BlenderProcessError(f"条目 {scene['entry_id']} 的薄板 {key} 缺少材质 ID。")
        spec = material_spec(material_id)
        if box.get("material_id") != spec.material_id:
            raise BlenderProcessError(
                f"条目 {scene['entry_id']} 的薄板 {key} 重开材质 {box.get('material_id')!r} "
                f"与工程配置 {spec.material_id!r} 不一致。"
            )
        parameters = material.get("parameters") or settings.get("parameters") or {}
        if layout_id and box.get("layout") != layout_id:
            raise BlenderProcessError(f"条目 {scene['entry_id']} 的薄板 {key} 布局不一致。")
        if "opacity" in parameters:
            actual = box.get("opacity")
            if actual is None or abs(float(actual) - float(parameters["opacity"])) > 1e-6:
                raise BlenderProcessError(f"条目 {scene['entry_id']} 的薄板 {key} 不透明度不正确。")
        elif box.get("opacity") is not None:
            raise BlenderProcessError(f"条目 {scene['entry_id']} 的薄板 {key} 不应有不透明度控件值。")
        if material_id == "transmission_filter":
            actual = box.get("filter_strength")
            if actual is None or abs(float(actual) - float(parameters["filter_strength"])) > 1e-6:
                raise BlenderProcessError(f"条目 {scene['entry_id']} 的透射滤色强度不正确。")
        emission = box.get("emission") or {}
        effective = bool(emission.get("effective"))
        compensation = float(parameters.get("emission_compensation", 0.0) or 0.0)
        if spec.emits_by_design:
            if not effective:
                raise BlenderProcessError(f"条目 {scene['entry_id']} 的薄板 {key} 缺少应有效果发光。")
        elif effective and not (spec.supports("emission_compensation") and compensation > 0.0):
            raise BlenderProcessError(
                f"条目 {scene['entry_id']} 的薄板 {key} 不应存在有效自发光。"
            )
        if spec.requires_light:
            expected_lit.add(key)
            light = lights.get(key)
            if not light:
                raise BlenderProcessError(f"条目 {scene['entry_id']} 的薄板 {key} 缺少辅助灯。")
            if light.get("type") != "AREA" or float(light.get("energy") or 0.0) <= 0.0:
                raise BlenderProcessError(f"条目 {scene['entry_id']} 的薄板 {key} 辅助灯无效。")
            receiver = ((scene.get("box_lighting") or {}).get("receiver_collection"))
            if receiver and light.get("receiver_collection") != receiver:
                raise BlenderProcessError(f"条目 {scene['entry_id']} 的薄板 {key} 辅助灯未链接到接收者。")
        elif box.get("light"):
            raise BlenderProcessError(f"条目 {scene['entry_id']} 的薄板 {key} 不应有辅助灯。")
        if layout_id == "data_aligned" and spec.prefers_data_aligned and not box.get("intersects_data"):
            raise BlenderProcessError(
                f"条目 {scene['entry_id']} 的透射薄板 {key} 在同轴布局中没有实际光路重叠。"
            )
    if set(lights) != expected_lit:
        raise BlenderProcessError(
            f"条目 {scene['entry_id']} 的辅助灯集合与需要照明的薄板不一致："
            f"{sorted(lights)} != {sorted(expected_lit)}"
        )


def _validate_entry_report(report, scene, assets_name, project=None):
    entry_id = scene["entry_id"]
    expected = f"//{assets_name}/{scene['assets']['vdb']}"
    actual = str(report.get("volume_filepath", "")).replace("\\", "/")
    if actual != expected:
        raise BlenderProcessError(f"条目 {entry_id} 的 VDB 相对路径不正确：{actual!r}，应为 {expected!r}。")
    if report.get("density_space") != "OBJECT" or float(report.get("weak_signal_clipping", -1)) != 0.0:
        raise BlenderProcessError(f"条目 {entry_id} 的体积密度设置未通过校验。")
    if not {"density", "color"}.issubset(set(report.get("grid_names") or ())):
        raise BlenderProcessError(f"条目 {entry_id} 缺少 density/color VDB 网格。")
    presets = scene.get("_selected_presets") or {}
    guide = report.get("guide_frame") or {}
    if presets.get("guide_frame", True):
        if (not guide.get("object") or not guide.get("larger_than_axis_frame")
                or not guide.get("encloses_data_bounds")):
            raise BlenderProcessError(f"条目 {entry_id} 的辅助盒框缺失或尺寸不合规。")
    elif guide.get("object"):
        raise BlenderProcessError(f"条目 {entry_id} 未勾选辅助盒框却创建了对象。")
    indicator = report.get("axis_indicator") or {}
    if presets.get("orientation", True):
        axes = indicator.get("axes") or []
        if (not indicator.get("all_present") or not indicator.get("visible_by_default")
                or indicator.get("style") != "solid_world_axes"
                or not indicator.get("geometry_validated")
                or {axis.get("key") for axis in axes} != {"X", "Y", "E"}
                or {axis.get("role") for axis in axes} != {"horizontal", "vertical", "depth"}
                or not all(axis.get("visible") for axis in axes)):
            raise BlenderProcessError(f"条目 {entry_id} 的三轴方向标不完整、被隐藏或几何不合规。")
    elif indicator.get("all_present"):
        raise BlenderProcessError(f"条目 {entry_id} 未勾选方向标却创建了对象。")
    coordinates = bool(report.get("coordinates_present"))
    if coordinates != bool(presets.get("coordinates", True)):
        raise BlenderProcessError(f"条目 {entry_id} 的坐标轴及文字与预设选择不一致。")
    _validate_box_report(report, scene, project, presets)


def commit_staged_project(stage_root, blend_path, metadata, overwrite=False):
    stage_root = Path(stage_root)
    blend_path = Path(blend_path)
    assets_path = assets_path_for(blend_path)
    staged_blend = stage_root / blend_path.name
    staged_assets = stage_root / assets_path.name
    report_path = stage_root / "blender-report.json"
    required = [staged_blend, staged_assets / "scene.json", staged_assets / "README.txt", report_path]
    for scene in metadata["entries"]:
        required.extend((staged_assets / scene["assets"]["arrays"], staged_assets / scene["assets"]["vdb"]))
    for path in required:
        if not path.is_file() or path.stat().st_size == 0:
            raise BlenderProcessError(f"Blender 工程产物校验失败，缺少文件：{path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report.get("blend_reopened"):
        raise BlenderProcessError("Blender 工程没有通过保存后的重新打开校验。")
    reports = {entry.get("entry_id"): entry for entry in report.get("entries") or ()}
    if set(reports) != set(metadata["project"]["entry_order"]):
        raise BlenderProcessError("Blender 重开报告的条目集合不完整。")
    expected_samples = int((metadata["project"].get("render") or {}).get("samples", 0))
    if int(report.get("render_samples") or 0) < expected_samples:
        raise BlenderProcessError("Blender 工程的推荐渲染采样没有写入或低于配置要求。")
    material_names, mesh_names = [], []
    for scene in metadata["entries"]:
        selected = dict(metadata["project"]["presets"])
        scene = dict(scene)
        scene["_selected_presets"] = selected
        _validate_entry_report(
            reports[scene["entry_id"]], scene, assets_path.name, metadata["project"],
        )
        material_names.extend(box.get("material") for box in reports[scene["entry_id"]]["box_presets"])
        mesh_names.extend(box.get("mesh") for box in reports[scene["entry_id"]]["box_presets"])
    if len(set(material_names)) != len(material_names) or len(set(mesh_names)) != len(mesh_names):
        raise BlenderProcessError("不同条目或长方体意外共享网格或材质。")
    if not overwrite and (_lexists(blend_path) or _lexists(assets_path)):
        raise BlenderOutputExistsError("目标工程或配套资产目录已存在；未覆盖任何文件。")
    backup_root = stage_root / "previous-output"
    backup_root.mkdir()
    backups, installed = [], []
    try:
        for target in (blend_path, assets_path):
            if _lexists(target):
                if not overwrite:
                    raise BlenderOutputExistsError("目标已存在；未覆盖任何文件。")
                backup = backup_root / target.name
                os.replace(target, backup)
                backups.append((target, backup))
        os.replace(staged_blend, blend_path)
        installed.append(blend_path)
        os.replace(staged_assets, assets_path)
        installed.append(assets_path)
    except Exception:
        rollback_errors = []
        for target in reversed(installed):
            try:
                _remove_path(target)
            except OSError as exc:
                rollback_errors.append(str(exc))
        for target, backup in reversed(backups):
            try:
                if _lexists(backup):
                    os.replace(backup, target)
            except OSError as exc:
                rollback_errors.append(str(exc))
        if rollback_errors:
            raise BlenderExportError("提交失败且回滚未完整执行：" + "; ".join(rollback_errors)) from None
        raise
    return BlenderExportResult(blend_path, assets_path, report)


class BlenderProjectExportJob(BlenderExportJob):
    def __init__(self, request, blender_path, output_path, **kwargs):
        if not isinstance(request, BlenderProjectRequest):
            request = BlenderProjectRequest([request])
        self.request = request
        super().__init__(request.entries[0].snapshot, blender_path, output_path, **kwargs)

    def run(self):
        if self.output_path.suffix.lower() != ".blend":
            raise BlenderExportError("输出文件必须使用 .blend 扩展名。")
        if not self.output_path.parent.is_dir():
            raise BlenderExportError("输出目录不存在。")
        assets_path = assets_path_for(self.output_path)
        if not self.overwrite and (_lexists(self.output_path) or _lexists(assets_path)):
            raise BlenderOutputExistsError("目标工程或配套资产目录已存在；请选择其他名称。")
        stage_root = Path(tempfile.mkdtemp(
            prefix=f".{self.output_path.stem}.bandscope-", suffix=".tmp", dir=self.output_path.parent,
        ))
        log_handle = None
        try:
            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                log_handle = self.log_path.open("w", encoding="utf-8")
            self.progress(2, "检查 Blender 与所需能力")
            self._check_cancel()
            _legacy.probe_blender(self.blender_path)
            staged_blend = stage_root / self.output_path.name
            staged_assets = stage_root / assets_path.name
            staged_assets.mkdir()
            metadata_items, job_entries = [], []
            total = len(self.request.entries)
            for index, entry in enumerate(self.request.entries):
                self._check_cancel()
                self.progress(10 + int(12 * index / total), f"准备条目 {entry.entry_id} 的颜色与体密度")
                scene = _legacy.prepare_blender_scene(entry.snapshot)
                item_assets = staged_assets / "entries" / entry.entry_id
                item_assets.mkdir(parents=True)
                arrays_path = item_assets / "arrays.npz"
                np.savez_compressed(
                    arrays_path, intensity=scene.intensity, display_color=scene.display_color,
                    alpha=scene.alpha, density=scene.density,
                )
                scene_metadata = scene.metadata()
                scene_metadata["entry_id"] = entry.entry_id
                metadata_items.append((entry, scene_metadata))
                job_entries.append({
                    "entry_id": entry.entry_id,
                    "arrays_file": str(arrays_path),
                    "vdb_path": str(item_assets / "volume.vdb"),
                })
                del scene
            metadata = make_project_metadata(metadata_items, self.request, assets_path.name)
            metadata_path = staged_assets / "scene.json"
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            readme_path = staged_assets / "README.txt"
            readme_path.write_text(project_usage_text(metadata, assets_path.name), encoding="utf-8")
            job = {
                "schema_version": BLENDER_PROJECT_SCHEMA,
                "mode": "build",
                "metadata_file": str(metadata_path),
                "readme_file": str(readme_path),
                "output_blend": str(staged_blend),
                "assets_dir_name": assets_path.name,
                "report_file": str(stage_root / "blender-report.json"),
                "entries": job_entries,
            }
            job_path = stage_root / "job.json"
            job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
            self.progress(25, "启动 Blender 后台逐条目转换")
            self._check_cancel()
            process_log = log_handle or open(os.devnull, "w", encoding="utf-8")
            try:
                self._run_process(job_path, process_log)
            finally:
                if log_handle is None:
                    process_log.close()
            self._check_cancel()
            self.progress(97, "逐条目校验并整体提交工程与资产")
            result = commit_staged_project(stage_root, self.output_path, metadata, overwrite=self.overwrite)
            self.progress(100, "Blender 工程导出完成")
            return result
        finally:
            if log_handle is not None:
                log_handle.close()
            shutil.rmtree(stage_root, ignore_errors=True)


@dataclass
class BlenderProjectUpdateResult:
    blend_path: Path
    assets_path: Path
    project: LoadedBlenderProject
    report: dict


def commit_staged_update(stage_root, project, updated_entries):
    """只替换修改条目资产与权威配置；失败时恢复全部旧文件。"""
    stage_root = Path(stage_root)
    assets = project.assets_path
    staged_assets = stage_root / assets.name
    replacements = []
    for entry_id in updated_entries:
        original = project.entry(entry_id).metadata
        for key in ("arrays", "vdb"):
            relative = PurePosixPath(original["assets"][key])
            staged = staged_assets.joinpath(*relative.parts)
            if not staged.is_file() or staged.stat().st_size == 0:
                raise BlenderProcessError(f"条目 {entry_id} 的暂存 {key} 缺失。")
            target = assets.joinpath(*relative.parts)
            replacements.append((staged, target))
    replacements.append((staged_assets / "scene.json", assets / "scene.json"))
    for staged, _target in replacements:
        if not staged.is_file() or staged.stat().st_size == 0:
            raise BlenderProcessError(f"更新产物校验失败：{staged}")
    backup_root = stage_root / "previous-assets"
    backups, installed = [], []
    blend_key = "//" + project.blend_path.name
    expected_blend = project.fingerprints[blend_key]
    if file_fingerprint(project.blend_path) != expected_blend:
        raise BlenderProjectError("来源 .blend 在提交前已变化；未替换任何资产。")
    try:
        for index, (_staged, target) in enumerate(replacements):
            backup = backup_root / f"{index:03d}-{target.name}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, backup)
            backups.append((target, backup))
        for staged, target in replacements:
            os.replace(staged, target)
            installed.append((target, staged))
        if file_fingerprint(project.blend_path) != expected_blend:
            raise BlenderProjectError("来源 .blend 在提交期间被外部程序改写；已回滚资产更新。")
        return load_blender_project(project.blend_path)
    except Exception:
        rollback_errors = []
        for target, staged in reversed(installed):
            try:
                if _lexists(target):
                    os.replace(target, staged)
            except OSError as exc:
                rollback_errors.append(str(exc))
        for target, backup in reversed(backups):
            try:
                if _lexists(backup):
                    os.replace(backup, target)
            except OSError as exc:
                rollback_errors.append(str(exc))
        if rollback_errors:
            raise BlenderExportError("更新失败且回滚未完整执行：" + "; ".join(rollback_errors)) from None
        raise


class BlenderProjectUpdateJob(BlenderExportJob):
    def __init__(self, project, snapshots, blender_path, **kwargs):
        self.project = project
        self.updates = {str(entry_id): snapshot for entry_id, snapshot in dict(snapshots).items()}
        if not self.updates:
            raise BlenderProjectError("没有需要更新的工程条目。")
        first = next(iter(self.updates.values()))
        super().__init__(first, blender_path, project.blend_path, overwrite=True, **kwargs)

    def _check_unchanged(self):
        try:
            current = project_fingerprints(self.project.blend_path)
        except OSError as exc:
            raise BlenderProjectError(f"工程或资产在更新前无法读取：{exc}") from exc
        if current != self.project.fingerprints:
            changed = sorted(set(current).symmetric_difference(self.project.fingerprints))
            changed += sorted(key for key in set(current).intersection(self.project.fingerprints)
                              if current[key] != self.project.fingerprints[key])
            raise BlenderProjectError(
                "工程或资产自打开后已变化，已中止原位更新；请重新打开工程。"
                + (f"\n变化文件：{', '.join(changed[:8])}" if changed else "")
            )

    def run(self):
        if set(self.updates).difference(self.project.metadata["project"]["entry_order"]):
            raise BlenderProjectError("更新列表包含不属于当前工程的条目。")
        self._check_unchanged()
        stage_root = Path(tempfile.mkdtemp(
            prefix=f".{self.project.blend_path.stem}.bandscope-update-",
            suffix=".tmp", dir=self.project.blend_path.parent,
        ))
        log_handle = None
        try:
            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                log_handle = self.log_path.open("w", encoding="utf-8")
            self.progress(3, "检查 Blender 能力及工程来源")
            _legacy.probe_blender(self.blender_path)
            staged_assets = stage_root / self.project.assets_path.name
            staged_assets.mkdir()
            generated, job_entries = {}, {}
            total = len(self.updates)
            for index, (entry_id, snapshot) in enumerate(self.updates.items()):
                self._check_cancel()
                self.progress(10 + int(25 * index / total), f"重新计算条目 {entry_id}")
                scene = prepare_scene_for_update(snapshot)
                scene_metadata = scene.metadata()
                scene_metadata["entry_id"] = entry_id
                generated[entry_id] = scene_metadata
                original = self.project.entry(entry_id).metadata
                arrays_relative = PurePosixPath(original["assets"]["arrays"])
                vdb_relative = PurePosixPath(original["assets"]["vdb"])
                arrays_path = staged_assets.joinpath(*arrays_relative.parts)
                arrays_path.parent.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(
                    arrays_path, intensity=scene.intensity, display_color=scene.display_color,
                    alpha=scene.alpha, density=scene.density,
                )
                job_entries[entry_id] = {
                    "entry_id": entry_id,
                    "arrays_file": str(arrays_path),
                    "vdb_path": str(staged_assets.joinpath(*vdb_relative.parts)),
                }
                del scene
            metadata = merged_project_metadata(self.project, generated)
            metadata_path = staged_assets / "scene.json"
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            check_path = stage_root / "source-scene.json"
            check_path.write_text(json.dumps(self.project.metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            job = {
                "schema_version": BLENDER_PROJECT_SCHEMA,
                "mode": "update",
                "blend_path": str(self.project.blend_path),
                "source_metadata_file": str(check_path),
                "metadata_file": str(metadata_path),
                "assets_dir_name": self.project.assets_path.name,
                "report_file": str(stage_root / "blender-update-report.json"),
                "entries": list(job_entries.values()),
            }
            job_path = stage_root / "update-job.json"
            job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
            self.progress(40, "只读核对 Blender 对象并写出暂存 VDB")
            process_log = log_handle or open(os.devnull, "w", encoding="utf-8")
            try:
                self._run_process(job_path, process_log)
            finally:
                if log_handle is None:
                    process_log.close()
            self._check_cancel()
            report_path = stage_root / "blender-update-report.json"
            if not report_path.is_file():
                raise BlenderProcessError("Blender 没有生成更新校验报告。")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not report.get("project_checked") or not report.get("vdb_staged"):
                raise BlenderProcessError("Blender 工程结构或暂存 VDB 未通过校验。")
            if report.get("blend_untouched") is not True:
                raise BlenderProcessError("Blender 更新模式试图改写工程文件，已拒绝。")
            self.progress(85, "复查并发变化、备份并提交修改资产")
            self._check_cancel()
            self._check_unchanged()
            updated_project = commit_staged_update(stage_root, self.project, job_entries)
            self.progress(100, "工程资产已原位更新；.blend 未改写")
            return BlenderProjectUpdateResult(
                self.project.blend_path, self.project.assets_path, updated_project, report,
            )
        finally:
            if log_handle is not None:
                log_handle.close()
            shutil.rmtree(stage_root, ignore_errors=True)
