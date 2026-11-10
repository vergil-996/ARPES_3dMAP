# -*- coding: utf-8 -*-
"""Blender 检测、后台外部转换与工程／资产事务化提交。"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Callable, Optional

import numpy as np

from bandscope.app.startup import resource_path
from bandscope.exporting.blender_scene import BLENDER_SCENE_SCHEMA, prepare_blender_scene

BLENDER_PATH_SETTING = "publication_export/v1/blender_path"
CAPABILITY_PREFIX = "BANDSCOPE_CAPABILITY "
PROGRESS_PREFIX = "BANDSCOPE_PROGRESS "
ERROR_PREFIX = "BANDSCOPE_ERROR "


class BlenderExportError(RuntimeError):
    pass


class BlenderNotFoundError(BlenderExportError):
    pass


class BlenderCapabilityError(BlenderExportError):
    pass


class BlenderProcessError(BlenderExportError):
    pass


class BlenderOutputExistsError(BlenderExportError):
    pass


class BlenderExportCancelled(BlenderExportError):
    pass


@dataclass(frozen=True)
class BlenderExportResult:
    blend_path: Path
    assets_path: Path
    report: dict


def blender_script_path() -> Path:
    return Path(resource_path("bandscope/exporting/blender_script.py"))


def assets_path_for(blend_path) -> Path:
    path = Path(blend_path)
    return path.parent / f"{path.stem}.assets"


def _valid_executable(path) -> bool:
    if not path:
        return False
    try:
        return Path(os.path.expandvars(str(path))).expanduser().is_file()
    except (OSError, ValueError):
        return False


def _registry_candidates():
    if os.name != "nt":
        return
    try:
        import winreg
    except ImportError:
        return
    roots = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    keys = (
        r"SOFTWARE\Blender Foundation\Blender",
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Blender",
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Blender_is1",
    )
    views = (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
    for root in roots:
        for key_name in keys:
            for view in views:
                try:
                    with winreg.OpenKey(root, key_name, 0, winreg.KEY_READ | view) as key:
                        values = {}
                        for value_name in ("InstallLocation", "InstallDir", "DisplayIcon", "Path"):
                            try:
                                values[value_name] = winreg.QueryValueEx(key, value_name)[0]
                            except OSError:
                                pass
                    for value_name, value in values.items():
                        text = str(value).strip().strip('"')
                        if value_name == "DisplayIcon":
                            yield text.split(",")[0]
                        else:
                            yield str(Path(text) / "blender.exe")
                except OSError:
                    continue


def blender_candidates(settings=None):
    seen = set()

    def emit(value):
        if not value:
            return None
        expanded = os.path.expandvars(str(value)).strip().strip('"')
        path = Path(expanded).expanduser()
        if path.is_dir():
            path = path / ("blender.exe" if os.name == "nt" else "blender")
        key = os.path.normcase(str(path))
        if key in seen:
            return None
        seen.add(key)
        return path

    if settings is not None:
        try:
            path = emit(settings.value(BLENDER_PATH_SETTING, "", type=str))
            if path is not None:
                yield path
        except (AttributeError, TypeError, ValueError):
            pass
    for name in ("blender.exe", "blender"):
        path = emit(shutil.which(name))
        if path is not None:
            yield path
    for path in _registry_candidates() or ():
        normalized = emit(path)
        if normalized is not None:
            yield normalized
    roots = []
    for name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        value = os.environ.get(name)
        if value:
            roots.append(Path(value))
    for root in roots:
        base = root / "Blender Foundation"
        for pattern in ("Blender */blender.exe", "Blender/blender.exe", "blender/blender.exe"):
            try:
                matches = sorted(base.glob(pattern), reverse=True)
            except OSError:
                matches = []
            for match in matches:
                path = emit(match)
                if path is not None:
                    yield path


def detect_blender(settings=None, preferred_path=None) -> Optional[Path]:
    if _valid_executable(preferred_path):
        return Path(os.path.expandvars(str(preferred_path))).expanduser().resolve()
    for candidate in blender_candidates(settings):
        if _valid_executable(candidate):
            try:
                return candidate.resolve()
            except OSError:
                return candidate
    return None


def remember_blender_path(settings, path) -> None:
    if settings is not None and _valid_executable(path):
        settings.setValue(BLENDER_PATH_SETTING, str(Path(path).resolve()))


def _hidden_process_kwargs():
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {
        "startupinfo": startup,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def blender_version(path, timeout=20) -> str:
    try:
        completed = subprocess.run(
            [str(path), "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
            **_hidden_process_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BlenderNotFoundError(f"无法启动 Blender：{exc}") from exc
    output = completed.stdout or ""
    match = re.search(r"Blender\s+([^\r\n]+)", output)
    if completed.returncode != 0 or not match:
        raise BlenderNotFoundError("所选程序没有返回有效的 Blender 版本信息。")
    return match.group(1).strip()


def probe_blender(path, timeout=60) -> dict:
    script = blender_script_path()
    if not script.is_file():
        raise BlenderCapabilityError(f"随程序提供的 Blender 转换脚本缺失：{script}")
    command = [str(path), "--background", "--factory-startup", "--python", str(script), "--", "--probe"]
    try:
        completed = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
            **_hidden_process_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise BlenderCapabilityError("Blender 能力检查超时；请确认程序可以后台启动。") from exc
    except OSError as exc:
        raise BlenderNotFoundError(f"无法启动 Blender：{exc}") from exc
    output = completed.stdout or ""
    missing = []
    if completed.returncode == 0:
        for line in output.splitlines():
            if line.startswith(CAPABILITY_PREFIX):
                try:
                    report = json.loads(line[len(CAPABILITY_PREFIX):])
                except ValueError as exc:
                    raise BlenderCapabilityError("Blender 能力检查结果无法解析。") from exc
                required = ("volume_coefficients", "object_space_density", "cycles",
                            "box_materials", "light_linking")
                missing = [key for key in required if not report.get(key)]
                if not missing:
                    return report
    details = "\n".join(output.splitlines()[-12:])
    raise BlenderCapabilityError(
        "Blender 缺少本功能所需的 OpenVDB、NumPy、体积系数节点、八种薄板材质节点或 "
        "Cycles 灯光链接能力。"
        + (f"\n缺少能力：{', '.join(missing)}" if missing else "")
        + (f"\n{details}" if details else "")
    )


def usage_text(metadata, assets_dir_name) -> str:
    source = metadata["source"]
    return f"""BandScope → Blender 工程
======================

来源：{source.get('description') or source.get('page_title')}
冻结时间：{source.get('captured_at')}

文件与移动
----------
请始终将 .blend 与整个 {assets_dir_name}/ 目录一起移动或分享。
工程使用相对路径引用 {assets_dir_name}/volume.vdb；在 Blender 中“另存为”不会复制 VDB。
arrays.npz 保存处理后的强度、线性显示颜色、最终 alpha 和体密度，便于复核或二次转换。
scene.json 保存版本化坐标、ROI、物理坐标数组、单位、相机和颜色／透明度换算信息。

继续创作
--------
数据体、坐标箭头、轴名与范围标记位于 “BandScope Data” Collection。
每次导出都会生成默认可见的三轴方向标（“Orientation marker · publication” Collection）：
采用单色立体箭杆、封闭圆锥和 kx／ky／E 短轴名，三轴沿真实 X／Y／Z 方向；
转动相机仍保持立体结构，与主视图的「坐标轴」开关无关。
整体移动、旋转或缩放时，请选择父对象 “BandScope Root”。
数据体旁边始终生成 “BandScope guide frame” 辅助盒框（“Guide frame · auxiliary” Collection）：
其局部尺寸比坐标轴框大一圈；辅助元素按数据体中心的六方位布局分组分离：
坐标轴及文字在左侧（−X），盒框在后侧（+Y），立体方向标在上方（+Z）。
右、前、下三个方位留空；这些方向固定在世界空间中，不随相机转动。
相机保留数据体视角；可在视口使用 Home 查看全部组件，再自行组合。
默认不参与渲染，需要时在 Outliner 中打开该 Collection 的渲染开关。
该盒框与主视图的「坐标轴」开关无关，每次导出都会生成。
体材质提供 “Density multiplier” 和 “Color adjustment” 节点；颜色输入为线性 RGB。
密度使用对象空间，父对象缩放时保持相应的透明度观感。
辅助平面请在 Blender 中自行添加；首版不导出动画、等值面或二维排版。

组合多个结果
------------
在另一个工程中通过 File → Append → 本工程 → Collection 追加 “BandScope Data”。
不要追加 “BandScope Camera”，这样不会替换目标场景的相机。
Append 后仍须保留原工程对应的资产目录；合并工程不会自动复制外部 VDB。

科学坐标与渲染
--------------
E、kx、ky 保留各自科学意义，不是毫米。Blender 的 2 个场景单位对应 BandScope 的
0–200 显示空间，并居中到原点；ROI 和额外裁剪的绝对位置均已保留。
E 轴显示翻转只由相机表达，没有再次翻转数组或物理坐标。
默认使用 Cycles CPU、Standard 色彩变换、透明背景和方形像素；可继续修改相机与画幅。
外观可能因 Cycles 与 BandScope/VTK 的积分方式不同而存在校准差异，不承诺逐像素一致。
"""


def _remove_path(path: Path):
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _lexists(path: Path) -> bool:
    return os.path.lexists(str(path))


def commit_staged_output(stage_root, blend_path, overwrite=False) -> BlenderExportResult:
    stage_root = Path(stage_root)
    blend_path = Path(blend_path)
    assets_path = assets_path_for(blend_path)
    staged_blend = stage_root / blend_path.name
    staged_assets = stage_root / assets_path.name
    report_path = stage_root / "blender-report.json"
    for required in (staged_blend, staged_assets / "volume.vdb", staged_assets / "arrays.npz",
                     staged_assets / "scene.json", staged_assets / "README.txt", report_path):
        if not required.is_file() or required.stat().st_size == 0:
            raise BlenderProcessError(f"Blender 产物校验失败，缺少文件：{required.name}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected_relative = f"//{assets_path.name}/volume.vdb"
    actual_relative = str(report.get("volume_filepath", "")).replace("\\", "/")
    if not report.get("blend_reopened") or actual_relative != expected_relative:
        raise BlenderProcessError(
            "Blender 工程未通过重开或相对路径校验："
            f"reopened={report.get('blend_reopened')!r}, "
            f"path={report.get('volume_filepath')!r}, expected={expected_relative!r}。"
        )
    if report.get("density_space") != "OBJECT" or float(report.get("weak_signal_clipping", -1)) != 0.0:
        raise BlenderProcessError("Blender 体积密度设置未通过校验。")
    guide = report.get("guide_frame") or {}
    if (not guide.get("object") or not guide.get("larger_than_axis_frame")
            or not guide.get("encloses_data_bounds")):
        raise BlenderProcessError("Blender 工程未生成辅助盒框，或盒框未大于坐标轴框／未包住数据体。")
    indicator = report.get("axis_indicator") or {}
    indicator_axes = indicator.get("axes") or []
    if (not indicator.get("all_present") or not indicator.get("visible_by_default")
            or indicator.get("style") != "solid_world_axes"
            or not indicator.get("geometry_validated")
            or {axis.get("key") for axis in indicator_axes} != {"X", "Y", "E"}
            or {axis.get("role") for axis in indicator_axes} != {"horizontal", "vertical", "depth"}
            or not all(axis.get("visible") for axis in indicator_axes)):
        raise BlenderProcessError("Blender 工程未生成完整、默认可见且具有立体几何的三轴方向标。")
    if not overwrite and (_lexists(blend_path) or _lexists(assets_path)):
        raise BlenderOutputExistsError("目标工程或配套资产目录已存在；未覆盖任何文件。")
    backup_root = stage_root / "previous-output"
    backup_root.mkdir()
    backups = []
    installed = []
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


class BlenderExportJob:
    def __init__(self, snapshot, blender_path, output_path, *, overwrite=False,
                 cancel_event=None, progress: Optional[Callable[[int, str], None]] = None,
                 log_path=None):
        self.snapshot = snapshot
        self.blender_path = Path(blender_path)
        self.output_path = Path(output_path)
        self.overwrite = bool(overwrite)
        self.cancel_event = cancel_event or threading.Event()
        self.progress = progress or (lambda _percent, _stage: None)
        self.log_path = Path(log_path) if log_path else None
        self._process = None

    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise BlenderExportCancelled("已取消；已有工程和资产未被修改。")

    def _run_process(self, job_path, log_handle):
        command = [
            str(self.blender_path), "--background", "--factory-startup",
            "--python", str(blender_script_path()), "--", "--job", str(job_path),
        ]
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                **_hidden_process_kwargs(),
            )
        except OSError as exc:
            raise BlenderNotFoundError(f"无法启动 Blender 后台进程：{exc}") from exc
        self._process = process
        lines = queue.Queue()

        def read_output():
            try:
                for line in process.stdout:
                    lines.put(line)
            finally:
                lines.put(None)

        reader = threading.Thread(target=read_output, name="BandScope-Blender-output", daemon=True)
        reader.start()
        tail = []
        done = False
        cancelled = False
        try:
            while not done:
                if self.cancel_event.is_set() and process.poll() is None:
                    cancelled = True
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                try:
                    line = lines.get(timeout=0.1)
                except queue.Empty:
                    if reader.is_alive():
                        continue
                    line = None
                if line is None:
                    done = True
                    continue
                log_handle.write(line)
                log_handle.flush()
                text = line.rstrip("\r\n")
                tail.append(text)
                tail = tail[-30:]
                if text.startswith(PROGRESS_PREFIX):
                    try:
                        payload = json.loads(text[len(PROGRESS_PREFIX):])
                        self.progress(int(payload["percent"]), str(payload["stage"]))
                    except (ValueError, KeyError, TypeError):
                        pass
            return_code = process.wait(timeout=5)
            reader.join(timeout=5)
            if cancelled:
                raise BlenderExportCancelled("已取消；已有工程和资产未被修改。")
            self._check_cancel()
            if return_code != 0:
                detail = "\n".join(tail)
                raise BlenderProcessError("Blender 后台转换失败。" + (f"\n{detail}" if detail else ""))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            self._process = None

    def run(self) -> BlenderExportResult:
        """兼容首版单快照调用；实际执行统一走单条目工程请求。"""
        from bandscope.exporting.blender_jobs import BlenderProjectExportJob
        from bandscope.exporting.blender_project import BlenderProjectRequest

        job = BlenderProjectExportJob(
            BlenderProjectRequest([self.snapshot]), self.blender_path, self.output_path,
            overwrite=self.overwrite, cancel_event=self.cancel_event,
            progress=self.progress, log_path=self.log_path,
        )
        return job.run()
