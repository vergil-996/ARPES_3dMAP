# -*- coding: utf-8 -*-
"""外部扩展包的安装、校验、加载与卸载。

边界与本机布局：

- 安装根固定在 ``%LOCALAPPDATA%\\BandScope\\extensions``，与主程序安装目录分开。
  主程序升级会清理 ``{app}\\_internal``，扩展不能放在那里，也不能随 dist 混入
  基础包。
- 运行时只从该根目录下**明确安装并启用**的扩展加载；源码树不会被当成已安装
  插件，扩展目录也不会被加进 ``sys.path``。
- 安装流程：读清单并检查兼容性 → 校验包结构/大小/路径 → 解包到用户目录内的
  临时目录 → 完整校验 → 提交安装 → 重启生效。**兼容性检查之前不导入插件代码。**
- 卸载：先写卸载请求，下次启动加载插件之前删除目录。删除目标由插件 id 在固定
  安装根下解析，包内路径不能影响删除位置。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import types
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bandscope.app_metadata import APP_NAME, APP_VERSION
from bandscope.extensions.api import (
    API_VERSION,
    Plugin,
    PluginCompatibilityError,
    PluginError,
    PluginManifest,
    PluginRecord,
    normalize_plugin_id,
)

#: 扩展包扩展名。ZIP 容器，内容为清单 + Python 代码 + 必要的 UI 资源。
PLUGIN_SUFFIX = ".bsplugin"

#: 宿主编译进基础包的共享依赖；扩展不得自带这些库。
FORBIDDEN_LIBRARIES = ("numpy", "PyQt5", "vtk", "vtkmodules", "pyvista", "cupy", "torch")

#: 解压上限，防止压缩炸弹。插件只含 Python 与少量 UI 资源。
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ENTRY_COUNT = 512

REGISTRY_FILE = "registry.json"
REGISTRY_VERSION = 1


def extension_root() -> Path:
    """用户级扩展安装根目录。"""
    override = os.environ.get("BANDSCOPE_EXTENSION_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME / "extensions"


def _installed_dir(root: Path) -> Path:
    return root / "installed"


def _staging_dir(root: Path) -> Path:
    return root / "staging"


def _uninstall_dir(root: Path) -> Path:
    return root / "pending_uninstall"


def _registry_path(root: Path) -> Path:
    return root / REGISTRY_FILE


def _read_registry(root: Path) -> Dict[str, object]:
    path = _registry_path(root)
    if not path.is_file():
        return {"version": REGISTRY_VERSION, "plugins": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": REGISTRY_VERSION, "plugins": {}}
    if not isinstance(payload, dict):
        return {"version": REGISTRY_VERSION, "plugins": {}}
    plugins = payload.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
    return {"version": REGISTRY_VERSION, "plugins": plugins}


def _write_registry(root: Path, payload: Dict[str, object]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = _registry_path(root)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


class PluginArchiveError(PluginError):
    """包结构、大小或路径不合规范。"""


# ---------------------------------------------------------------------------
# 包结构校验
# ---------------------------------------------------------------------------


def _open_archive(path: Path) -> zipfile.ZipFile:
    if not path.is_file():
        raise PluginArchiveError(f"找不到扩展包：{path}")
    try:
        return zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise PluginArchiveError("扩展包不是有效的 ZIP 容器。") from exc
    except OSError as exc:
        raise PluginArchiveError(f"无法读取扩展包：{exc}") from exc


#: Windows 上路径分量里不允许出现的字符。冒号尤其危险：中间段里的 ``x/C:/y``
#: 在剥掉清单前缀之后可能变成盘符或 NTFS 数据流。
_INVALID_PATH_CHARS = set('<>:"|?*')


def safe_relative_member(name: str) -> str:
    """把包内条目名规整成可以安全拼接的相对路径。

    绝对路径（``/x``、``C:/x``）、盘符、``..``、空段、非法字符和符号链接都拒绝。
    **必须在去掉清单前缀之后再过一次**：``myplugin/C:/Users/x`` 在原样检查时看不
    出问题，剥掉前缀就变成了绝对路径。
    """
    text = str(name).replace("\\", "/")
    if not text or text.endswith("/"):
        return ""
    if text.startswith("/"):
        raise PluginArchiveError(f"扩展包包含绝对路径：{name}")
    if any(char in _INVALID_PATH_CHARS or ord(char) < 32 for char in text):
        raise PluginArchiveError(f"扩展包路径含有非法字符：{name}")
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise PluginArchiveError(f"扩展包包含不合规范的路径：{name}")
    if any(part.endswith((" ", ".")) for part in parts):
        # Windows 会悄悄去掉结尾的空格与点，让 `evil. ` 落到 `evil` 上。
        raise PluginArchiveError(f"扩展包路径以空格或点结尾：{name}")
    return "/".join(parts)


def _validated_members(archive: zipfile.ZipFile) -> List[zipfile.ZipInfo]:
    """校验条目数量、体积、路径与符号链接；返回校验过的条目列表。"""
    members = archive.infolist()
    if len(members) > MAX_ENTRY_COUNT:
        raise PluginArchiveError("扩展包条目过多，已拒绝。")
    total = 0
    for info in members:
        safe_relative_member(info.filename)
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise PluginArchiveError(f"扩展包包含符号链接：{info.filename}")
        total += int(info.file_size)
        if total > MAX_ARCHIVE_BYTES:
            raise PluginArchiveError("扩展包解压后体积超出上限，已拒绝。")
    return members


def _read_manifest(
    archive: zipfile.ZipFile, members
) -> Tuple[PluginManifest, str]:
    names = [info.filename for info in members]
    manifest_name = _find_manifest_name(names)
    if manifest_name is None:
        raise PluginArchiveError("扩展包内缺少 plugin.json。")
    try:
        raw = archive.read(manifest_name)
    except (KeyError, RuntimeError, zipfile.BadZipFile) as exc:
        # 加密包、损坏成员会在这里抛 RuntimeError / BadZipFile，不能让它漏成
        # 未捕获异常冲进 Qt 槽。
        raise PluginArchiveError(f"无法读取 plugin.json：{exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PluginArchiveError("plugin.json 不是有效的 UTF-8 JSON。") from exc
    if not isinstance(payload, dict):
        raise PluginArchiveError("plugin.json 顶层必须是对象。")
    manifest = PluginManifest.from_mapping(payload)
    prefix = manifest_name[: -len("plugin.json")].rstrip("/")
    return manifest, prefix


def inspect_archive(path: Path) -> Tuple[PluginManifest, str]:
    """在**不导入任何插件代码**的前提下读清单并校验包结构。

    返回清单，以及清单所在目录在包内的前缀（根目录时为空串）。
    """
    with _open_archive(Path(path)) as archive:
        members = _validated_members(archive)
        return _read_manifest(archive, members)


def _find_manifest_name(names: List[str]) -> Optional[str]:
    candidates = [
        name for name in names if name.replace("\\", "/").endswith("plugin.json")
    ]
    if not candidates:
        return None
    # 只接受根目录或单层子目录里的清单，避免包结构含糊。
    candidates.sort(key=lambda value: value.count("/"))
    return candidates[0]


def check_package_dependencies(staging: Path) -> None:
    """基础包已经带有 NumPy / Qt / VTK，扩展必须复用而不是自带。"""
    found = []
    for child in staging.iterdir():
        name = child.name.split(".")[0]
        if name in FORBIDDEN_LIBRARIES:
            found.append(child.name)
    if found:
        raise PluginArchiveError(
            "扩展包不应自带主程序已提供的依赖：" + "、".join(sorted(found))
        )


# ---------------------------------------------------------------------------
# 安装 / 卸载
# ---------------------------------------------------------------------------


def resolve_install_target(root: Path, manifest: PluginManifest) -> Path:
    """安装目标目录 ``installed/<id>/<version>``，并确认它落在扩展根之内。

    id 与 version 都来自包内清单。id 已收窄到 ``[a-z0-9_]``，版本号由
    :func:`plugin_api.normalize_plugin_version` 校验过；这里再做一次最终确认，
    因为旧版本目录会被递归删除，路径一旦越界就是任意目录删除。
    """
    installed = (_installed_dir(root) / manifest.plugin_id).resolve()
    target = (installed / manifest.version).resolve()
    if target.parent != installed:
        raise PluginArchiveError(
            f"安装路径越出扩展目录：{manifest.plugin_id}/{manifest.version}"
        )
    return target


def _commit_install(staging: Path, target_dir: Path, root: Path) -> None:
    """把临时目录就位到安装目录；失败时保留原有已安装版本。

    先把旧版本挪到一边而不是直接删：新版本没能就位时还能放回去。
    """
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if target_dir.exists():
        backup = _staging_dir(root) / f"obsolete-{uuid.uuid4().hex}"
        os.replace(target_dir, backup)
    try:
        os.replace(staging, target_dir)
    except Exception:
        if backup is not None and backup.exists() and not target_dir.exists():
            os.replace(backup, target_dir)
        raise
    finally:
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)


def install_package(path, *, app_version: str = APP_VERSION) -> PluginManifest:
    """校验并安装一个扩展包；返回清单。安装后需要重启才生效。"""
    source = Path(path)
    root = extension_root()
    staging = _staging_dir(root) / uuid.uuid4().hex

    # 校验与解包用同一个打开的包：分两次打开会给“校验 A、解包 B”留下窗口。
    with _open_archive(source) as archive:
        members = _validated_members(archive)
        manifest, prefix = _read_manifest(archive, members)
        manifest.check_compatibility(app_version, API_VERSION)
        target_dir = resolve_install_target(root, manifest)

        try:
            staging.mkdir(parents=True, exist_ok=True)
            for info in members:
                relative = safe_relative_member(info.filename)
                if not relative:
                    continue
                if prefix:
                    if relative == prefix:
                        continue
                    if not relative.startswith(prefix + "/"):
                        raise PluginArchiveError(
                            f"扩展包内存在清单目录之外的文件：{info.filename}"
                        )
                    # 剥掉前缀之后必须重新校验：`myplugin/C:/x` 原样看没问题，
                    # 剥完就是绝对路径。
                    relative = safe_relative_member(relative[len(prefix) + 1 :])
                    if not relative:
                        continue
                destination = staging / relative
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as handle, open(destination, "wb") as target:
                        shutil.copyfileobj(handle, target)
                except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
                    # 损坏成员、加密包、非法文件名：转成可展示的错误，别让它
                    # 带着 OSError 冲进 Qt 槽。
                    raise PluginArchiveError(
                        f"扩展包成员无法解压（{info.filename}）：{exc}"
                    ) from exc

            check_package_dependencies(staging)
            entry = staging / f"{manifest.entry_module.replace('.', os.sep)}.py"
            if not entry.is_file():
                raise PluginArchiveError(
                    f"扩展包内找不到入口模块 {manifest.entry_module}.py。"
                )

            _commit_install(staging, target_dir, root)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    registry = _read_registry(root)
    plugins = registry["plugins"]
    plugins[manifest.plugin_id] = {
        "version": manifest.version,
        "name": manifest.name,
        "path": str(Path(manifest.plugin_id) / manifest.version),
        "enabled": True,
    }
    _write_registry(root, registry)
    _clear_uninstall_request(root, manifest.plugin_id)

    # 旧版本目录留着会浪费空间，也会让“下次启动选哪个版本”变得含糊。
    installed = _installed_dir(root) / manifest.plugin_id
    for child in installed.iterdir() if installed.is_dir() else ():
        if child.is_dir() and child.name != manifest.version:
            shutil.rmtree(child, ignore_errors=True)
    return manifest


def request_uninstall(plugin_id: str) -> None:
    """记录卸载请求；下次启动加载插件之前真正删除。"""
    plugin_id = normalize_plugin_id(plugin_id)
    root = extension_root()
    _uninstall_dir(root).mkdir(parents=True, exist_ok=True)
    marker = _uninstall_dir(root) / f"{plugin_id}.json"
    marker.write_text(
        json.dumps({"plugin_id": plugin_id}, ensure_ascii=False), encoding="utf-8"
    )
    registry = _read_registry(root)
    plugins = registry["plugins"]
    if plugin_id in plugins:
        plugins[plugin_id]["enabled"] = False
    _write_registry(root, registry)


def _clear_uninstall_request(root: Path, plugin_id: str) -> None:
    marker = _uninstall_dir(root) / f"{plugin_id}.json"
    try:
        marker.unlink()
    except OSError:
        pass


def apply_pending_uninstalls(root: Optional[Path] = None) -> List[str]:
    """在加载任何插件之前执行待处理的卸载。"""
    root = root or extension_root()
    pending = _uninstall_dir(root)
    if not pending.is_dir():
        return []

    removed: List[str] = []
    registry = _read_registry(root)
    plugins = registry["plugins"]
    for marker in sorted(pending.glob("*.json")):
        try:
            plugin_id = normalize_plugin_id(
                json.loads(marker.read_text(encoding="utf-8")).get("plugin_id", "")
            )
        except (OSError, ValueError, PluginError):
            marker.unlink(missing_ok=True)
            continue
        # 删除目标只由 id 在固定安装根下解析，包内容无法影响它。
        shutil.rmtree(_installed_dir(root) / plugin_id, ignore_errors=True)
        plugins.pop(plugin_id, None)
        marker.unlink(missing_ok=True)
        removed.append(plugin_id)
    if removed:
        _write_registry(root, registry)
    return removed


def set_enabled(plugin_id: str, enabled: bool) -> None:
    plugin_id = normalize_plugin_id(plugin_id)
    root = extension_root()
    registry = _read_registry(root)
    plugins = registry["plugins"]
    if plugin_id not in plugins:
        raise PluginError(f"未安装扩展：{plugin_id}")
    plugins[plugin_id]["enabled"] = bool(enabled)
    _write_registry(root, registry)


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------


def _load_package_module(module_name: str, package_dir: Path, entry_module: str):
    """用插件专属模块名加载入口模块，不把扩展目录加进 ``sys.path``。

    入口模块所在目录被登记为合成包，包内相对导入因此照常工作。
    """
    package = types.ModuleType(module_name)
    package.__path__ = [str(package_dir)]
    package.__package__ = module_name
    sys.modules[module_name] = package

    entry_path = package_dir / f"{entry_module.replace('.', os.sep)}.py"
    full_name = f"{module_name}.{entry_module}"
    # 入口模块本身不是包：它的相对导入由上面那个合成包（``__path__`` 指向扩展
    # 目录）解析。给入口也带上搜索路径会让 ``__spec__.parent`` 与 ``__package__``
    # 不一致，Python 会发 DeprecationWarning。
    spec = importlib.util.spec_from_file_location(full_name, entry_path)
    if spec is None or spec.loader is None:
        raise PluginError(f"无法加载扩展入口：{entry_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def instantiate_plugin(manifest: PluginManifest, package_dir: Path) -> Plugin:
    """按清单加载并实例化插件；调用方负责先做兼容性检查。"""
    module_name = f"_bandscope_plugin_{manifest.plugin_id}"
    module = _load_package_module(module_name, package_dir, manifest.entry_module)
    factory = getattr(module, manifest.entry_object, None)
    if factory is None:
        raise PluginError(
            f"入口模块里找不到 {manifest.entry_object}。"
        )
    instance = factory() if callable(factory) else factory
    if not isinstance(instance, Plugin):
        raise PluginError("插件入口对象必须实现 plugin_api.Plugin。")
    instance.plugin_id = manifest.plugin_id
    instance.manifest = manifest
    return instance


def forget_plugin_modules(plugin_id: str) -> None:
    """卸载后清掉本插件留下的模块，避免下次安装复用到旧代码。"""
    prefix = f"_bandscope_plugin_{plugin_id}"
    for name in [key for key in sys.modules if key == prefix or key.startswith(prefix + ".")]:
        sys.modules.pop(name, None)


class PluginManager:
    """宿主侧的扩展登记表：扫描、加载、按页面状态管理。"""

    def __init__(self, *, app_version: str = APP_VERSION, root: Optional[Path] = None):
        self.app_version = str(app_version)
        self.root = Path(root) if root is not None else extension_root()
        self.records: Dict[str, PluginRecord] = {}
        self.pending_uninstalls: List[str] = []

    # -- 生命周期 -------------------------------------------------------
    def startup(self) -> None:
        """应用启动、Qt 与宿主接口就绪后调用。"""
        self.pending_uninstalls = apply_pending_uninstalls(self.root)
        for plugin_id in self.pending_uninstalls:
            forget_plugin_modules(plugin_id)
        self.scan()

    def scan(self) -> Dict[str, PluginRecord]:
        """按注册表加载明确安装并启用的扩展。"""
        self.records = {}
        registry = _read_registry(self.root)
        for plugin_id, entry in sorted(registry["plugins"].items()):
            try:
                normalized = normalize_plugin_id(plugin_id)
            except PluginError:
                continue
            record = PluginRecord(
                plugin_id=normalized,
                version=str(entry.get("version") or ""),
                path=str(entry.get("path") or ""),
                enabled=bool(entry.get("enabled", True)),
            )
            self.records[normalized] = record
            if not record.enabled:
                record.load_error = "已停用"
                continue
            self._load_record(record)
        return dict(self.records)

    def _load_record(self, record: PluginRecord) -> None:
        package_dir = _installed_dir(self.root) / record.path
        manifest_path = package_dir / "plugin.json"
        try:
            if not manifest_path.is_file():
                raise PluginError("已安装目录缺少 plugin.json。")
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = PluginManifest.from_mapping(payload)
            manifest.check_compatibility(self.app_version, API_VERSION)
            record.manifest = manifest
            record.instance = instantiate_plugin(manifest, package_dir)
        except PluginCompatibilityError as exc:
            record.load_error = str(exc)
            record.extra["incompatible"] = True
        except Exception as exc:  # 插件代码可能抛任何异常，全部拦在界面之外
            record.load_error = f"{type(exc).__name__}: {exc}"

    def register_installed(self, manifest: PluginManifest) -> PluginRecord:
        """把刚安装的扩展记入本次会话的登记表，但**不加载**。

        新装的扩展要等重启才生效；这里只是让管理界面立刻能看到它，而不是等到
        下次启动才发现装了什么。
        """
        record = PluginRecord(
            plugin_id=manifest.plugin_id,
            version=manifest.version,
            path=str(Path(manifest.plugin_id) / manifest.version),
            enabled=True,
        )
        record.manifest = manifest
        record.load_error = "已安装，重启后生效"
        self.records[manifest.plugin_id] = record
        return record

    def note_removal(self, plugin_id: str) -> None:
        """记录卸载请求并同步登记表，下次启动时删除。"""
        plugin_id = normalize_plugin_id(plugin_id)
        request_uninstall(plugin_id)
        record = self.records.get(plugin_id)
        if record is not None:
            record.pending_removal = True
            record.enabled = False

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """改写启用状态并同步登记表。"""
        plugin_id = normalize_plugin_id(plugin_id)
        set_enabled(plugin_id, enabled)
        record = self.records.get(plugin_id)
        if record is None:
            return
        record.enabled = bool(enabled)
        if enabled:
            # 重新启用后是否可用要等重启才知道，先清掉“已停用”的说明。
            if not record.extra.get("incompatible"):
                record.load_error = ""
        else:
            record.load_error = record.load_error or "已停用"

    # -- 查询 -----------------------------------------------------------
    def plugins(self) -> List[PluginRecord]:
        return [record for record in self.records.values()]

    def ready_plugins(self) -> List[PluginRecord]:
        return [record for record in self.records.values() if record.ready]

    def record(self, plugin_id: str) -> Optional[PluginRecord]:
        return self.records.get(str(plugin_id))

    def incompatible(self) -> List[PluginRecord]:
        return [r for r in self.records.values() if r.extra.get("incompatible")]

    def shutdown(self) -> None:
        for record in self.records.values():
            if record.instance is not None:
                try:
                    record.instance.release()
                except Exception:
                    pass
        self.records = {}
