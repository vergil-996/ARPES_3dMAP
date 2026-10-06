# -*- coding: utf-8 -*-
"""扩展包的安装、校验、加载与生命周期。

对照「插件管理入口与系统完善计划」阶段 A、B。本模块负责：

- 包的校验与安装事务（与 :mod:`bandscope.extensions.plugin_store` 的存储层配合）；
- 会话内的加载、健康回报与失败恢复（重试加载 / 恢复上一版本）。

边界与本机布局（详细约定见 ``plugin_store`` 的模块注释）：

- 安装根固定在 ``%LOCALAPPDATA%\\BandScope\\extensions``，与主程序安装目录分开。
  主程序升级会清理 ``{app}\\_internal``，扩展不能放在那里，也不能随 dist 混入
  基础包。
- 运行时只从该根目录下**明确安装并启用**的扩展加载；源码树不会被当成已安装
  插件，扩展目录也不会被加进 ``sys.path``。
- **期望配置与当前会话分离**：安装、启用/停用和卸载请求只改登记表里下次启动
  的期望配置；当前会话已加载的实例在退出前保持不变，由统一入口恰好释放一次。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import types
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bandscope.app_metadata import APP_VERSION
from bandscope.extensions.api import (
    Plugin,
    PluginCompatibilityError,
    PluginError,
    PluginManifest,
    PluginRecord,
    normalize_plugin_id,
)
from bandscope.extensions.plugin_store import (
    PluginStore,
    RegistryEntry,
    compute_content_digest,
    extension_root,
    parse_entry,
)
from bandscope.extensions.trust import (
    SOURCE_LOCAL,
    SOURCE_OFFICIAL,
    PackageSource,
    inspect_package_source,
)

# 兼容旧导入位置：这两个名字原先定义在本模块。
from bandscope.extensions.plugin_store import (  # noqa: F401
    REGISTRY_FILE,
    REGISTRY_VERSION,
)

#: 扩展包扩展名。ZIP 容器，内容为清单 + Python 代码 + 必要的 UI 资源。
PLUGIN_SUFFIX = ".bsplugin"

#: 宿主编译进基础包的共享依赖；扩展不得自带这些库。
FORBIDDEN_LIBRARIES = ("numpy", "PyQt5", "vtk", "vtkmodules", "pyvista", "cupy", "torch")

#: 解压上限，防止压缩炸弹。插件只含 Python 与少量 UI 资源。
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ENTRY_COUNT = 512


class PluginArchiveError(PluginError):
    """包结构、大小或路径不合规范。"""


@dataclass(frozen=True)
class InstallOutcome:
    """一次安装事务的结果。"""

    manifest: PluginManifest
    digest: str
    reused: bool = False


@dataclass(frozen=True)
class InstallSource:
    """一次安装请求的来源与信任凭证。

    ``accepted_unverified`` 与 ``accepted_downgrade`` 对应界面上的两次**不同**
    确认：前者是“装这个没有官方签名的包”，后者是“把官方已验证的插件换成未验证
    来源”。默认都是 False——不确认就不装，而不是默认放行。
    """

    kind: str = SOURCE_LOCAL
    #: 用户在界面上明确接受了未签名包。
    accepted_unverified: bool = False
    #: 用户明确接受把官方已验证的来源换成未验证来源。
    accepted_downgrade: bool = False
    #: 目录声明的清单身份与包摘要；在线安装必须与包内内容一字不差。
    expected_id: str = ""
    expected_version: str = ""
    expected_sha256: str = ""


def _resolve_install_trust(
    package_path: Path,
    request: InstallSource,
    store: PluginStore,
    *,
    plugin_id: str,
    package_sha256: str,
) -> dict:
    """复核包旁签名并给出登记表要记录的来源信息。

    验签永远在这里重新做一遍：界面先看过一遍签名，但从“检查”到“提交”之间文件
    可能被替换，安装层不能相信上层传下来的结论。任何一步失败都在导入插件代码
    之前返回。
    """
    detected: PackageSource = inspect_package_source(package_path)
    if detected.rejected:
        raise PluginError(f"插件包带有签名但没有通过验证：{detected.reason}")

    if detected.verified:
        kind, key_id, verified = SOURCE_OFFICIAL, detected.key_id, True
    else:
        if request.kind == SOURCE_OFFICIAL:
            raise PluginError(
                "该插件包没有可验证的官方签名，不能作为官方来源安装。"
            )
        if not request.accepted_unverified:
            raise PluginError(
                "插件包没有官方签名，无法确认来源；需要用户确认后才能安装。"
            )
        kind, key_id, verified = SOURCE_LOCAL, "", False

    # 已安装的官方已验证插件不能被未签名包静默覆盖。
    state = store.read_state()
    existing = state.entries.get(plugin_id)
    if (
        kind != SOURCE_OFFICIAL
        and existing is not None
        and existing.valid
        and existing.source.get("kind") == SOURCE_OFFICIAL
        and existing.source.get("verified")
        and not request.accepted_downgrade
    ):
        raise PluginError(
            "已安装的「{0}」是官方已验证来源，替换为未验证来源需要用户明确确认。".format(
                plugin_id
            )
        )

    return {
        "kind": kind,
        "verified": verified,
        "key_id": key_id,
        "package_sha256": package_sha256,
        # 未签名但用户确认过：重启后仍要能如实显示“本地未验证（已确认）”。
        "accepted_unverified": bool(kind == SOURCE_LOCAL and request.accepted_unverified),
    }


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
        relative = safe_relative_member(info.filename)
        if relative and (
            "__pycache__" in relative.split("/") or relative.endswith(".pyc")
        ):
            # 字节码不进内容摘要，加载器也不使用未纳入校验的字节码；直接拒绝，
            # 保证内容目录里的每个文件都被摘要覆盖。
            raise PluginArchiveError(f"扩展包包含字节码缓存：{info.filename}")
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
# 安装事务
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_package(
    path,
    *,
    app_version: str = APP_VERSION,
    root: Optional[Path] = None,
    source: Optional[InstallSource] = None,
) -> InstallOutcome:
    """校验并安装一个扩展包；返回安装结果。安装后需要重启才生效。

    流程：来源与签名复核 → 兼容性检查 → 解包到临时目录 → 完整性检查 →
    不可变内容目录落盘 → 原子替换登记表。任一步失败都保留原登记表与原有已安装
    版本；目录已落盘但登记失败的残留内容由下次安全启动清理。

    验签、解包与提交都基于同一个已读取的文件：签名通过之后再按路径重新打开，
    两次读到的可能不是同一份内容。
    """
    request = source or InstallSource()
    package_path = Path(path)
    store = PluginStore(Path(root) if root is not None else extension_root())
    schema_error = store.ensure_schema()
    if schema_error:
        raise PluginError(schema_error)
    package_sha256 = _sha256_file(package_path)
    if request.expected_sha256 and package_sha256 != request.expected_sha256.strip().lower():
        raise PluginError(
            "插件包内容与来源声明不一致（SHA-256 不符）；已拒绝安装。"
        )
    staging = store.staging_dir / uuid.uuid4().hex

    with _open_archive(package_path) as archive:
        members = _validated_members(archive)
        manifest, prefix = _read_manifest(archive, members)
        if request.expected_id and manifest.plugin_id != request.expected_id:
            raise PluginError(
                f"插件包内的 id 是 {manifest.plugin_id}，与来源声明的 "
                f"{request.expected_id} 不一致；已拒绝安装。"
            )
        if request.expected_version and manifest.version != request.expected_version:
            raise PluginError(
                f"插件包内的版本是 {manifest.version}，与来源声明的 "
                f"{request.expected_version} 不一致；已拒绝安装。"
            )
        recorded_source = _resolve_install_trust(
            package_path,
            request,
            store,
            plugin_id=manifest.plugin_id,
            package_sha256=package_sha256,
        )
        manifest.check_compatibility(app_version)

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

            digest, reused = store.commit_content(staging, manifest)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _register(payload: dict) -> None:
        plugins = payload.setdefault("plugins", {})
        raw = plugins.get(manifest.plugin_id)
        prior_enabled = True
        if raw is not None:
            prior = parse_entry(manifest.plugin_id, raw)
            if not prior.valid:
                raise PluginError(
                    f"「{manifest.plugin_id}」的登记记录无法确认（{prior.reason}），"
                    "已保留原文件；修复前不能安装该插件。"
                )
            prior_enabled = prior.desired_enabled
        entry = raw if isinstance(raw, dict) else {}
        entry["name"] = manifest.name
        entry["desired"] = {
            "version": manifest.version,
            "digest": digest,
            "path": f"installed/{manifest.plugin_id}/{manifest.version}/{digest}",
            # 更新保留用户原有的启用意愿；首次安装默认启用。
            "enabled": prior_enabled,
            "revision": payload["revision"],
        }
        entry["pending_uninstall"] = False
        entry["restore_requested"] = False
        entry.pop("failed", None)
        entry.pop("last_error", None)
        entry["source"] = dict(recorded_source)
        entry.setdefault("last_good", None)
        entry.setdefault("previous_good", None)
        plugins[manifest.plugin_id] = entry

    try:
        store.update(_register)
    except Exception:
        # 内容已落盘但没有登记：视为未引用内容，下次安全启动清理。
        raise
    return InstallOutcome(manifest=manifest, digest=digest, reused=reused)


# ---------------------------------------------------------------------------
# 模块级兼容入口（带显式 root 参数）
# ---------------------------------------------------------------------------


def request_uninstall(plugin_id: str, *, root: Optional[Path] = None) -> None:
    """记录卸载请求；下次启动加载插件之前真正删除。"""
    store = PluginStore(Path(root) if root is not None else extension_root())
    store.request_uninstall(plugin_id)


def set_enabled(plugin_id: str, enabled: bool, *, root: Optional[Path] = None) -> None:
    store = PluginStore(Path(root) if root is not None else extension_root())
    store.set_desired_enabled(plugin_id, enabled)


def apply_pending_uninstalls(root: Optional[Path] = None) -> List[str]:
    """在加载任何插件之前执行待处理的卸载；返回已移除的插件 id。"""
    store = PluginStore(Path(root) if root is not None else extension_root())
    removed, _failures = store.apply_pending_uninstalls()
    return removed


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------


def _load_package_module(module_name: str, package_dir: Path, entry_module: str):
    """用插件专属模块名加载入口模块，不把扩展目录加进 ``sys.path``。

    入口模块所在目录被登记为合成包，包内相对导入因此照常工作。加载期间禁止
    写字节码：内容目录必须保持与内容摘要一一对应，运行产生的缓存不写进去。
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
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
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


# ---------------------------------------------------------------------------
# 会话生命周期
# ---------------------------------------------------------------------------


class PluginManager:
    """宿主侧的扩展登记表：期望配置、会话实例与安全启动维护。"""

    def __init__(self, *, app_version: str = APP_VERSION, root: Optional[Path] = None):
        self.app_version = str(app_version)
        self.root = Path(root) if root is not None else extension_root()
        self.store = PluginStore(self.root)
        self.records: Dict[str, PluginRecord] = {}
        #: 登记表损坏 / schema 未知时的诊断；非空即只读模式。
        self.registry_error = ""
        #: 启动时是否有其它活跃会话（决定卸载与清理是否被推迟）。
        self.other_sessions_active = False
        self.cleanup_deferred = False
        #: 本次启动实际移除的插件 id。
        self.pending_uninstalls: List[str] = []
        #: 需要向用户展示的启动说明（加载失败、卸载推迟等）。
        self._notes: List[str] = []
        self._lease = None
        self._shutdown_done = False

    # -- 生命周期 -------------------------------------------------------
    def startup(self) -> None:
        """应用启动、Qt 与宿主接口就绪后调用。"""
        self._notes = []
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.store.recover_relocations()
        except OSError as exc:
            self.registry_error = f"扩展目录不可用：{exc}"
            return
        try:
            self._lease = self.store.acquire_lease()
        except (PluginError, OSError) as exc:
            self._notes.append(f"未能登记本次会话（{exc}）；卸载与清理将被推迟。")
        error = self.store.ensure_schema()
        if error:
            self.registry_error = error
        else:
            self._run_safe_start_maintenance()
        self.scan()

    def _run_safe_start_maintenance(self) -> None:
        exclude = self._lease.name if self._lease is not None else ""
        try:
            others = self.store.other_active_leases(exclude=exclude)
        except OSError:
            others = []
        self.other_sessions_active = bool(others)
        if others:
            # 只在启动加载前、没有其他活跃会话时执行卸载与旧目录清理；
            # 有其他实例时保留请求，提示关闭其他窗口后重启。
            self.cleanup_deferred = True
            self._notes.append(
                "检测到另一个 BandScope 实例正在运行：待执行的卸载与内容清理已推迟，"
                "关闭其他窗口后重启即可执行。"
            )
            return
        removed, failures = self.store.apply_pending_uninstalls()
        self.pending_uninstalls = removed
        for plugin_id in removed:
            forget_plugin_modules(plugin_id)
        for failure in failures:
            self._notes.append(f"卸载未能完成：{failure}")
        for note in self.store.apply_restore_requests(self.app_version):
            self._notes.append(note)
        try:
            self.store.cleanup_unreferenced()
        except OSError as exc:
            self._notes.append(f"旧版本内容清理失败：{exc}")

    def scan(self) -> Dict[str, PluginRecord]:
        """按登记表建立会话记录并加载期望运行的插件。"""
        self.records = {}
        if self.registry_error:
            return dict(self.records)
        state = self.store.read_state()
        if state.error:
            self.registry_error = state.error
            return dict(self.records)
        for plugin_id, entry in sorted(state.entries.items()):
            record = self._record_from_entry(entry)
            self.records[plugin_id] = record
            if not entry.valid:
                record.load_error = f"登记记录无法确认：{entry.reason}"
                record.unconfirmed_reason = entry.reason
                continue
            if entry.pending_uninstall:
                continue
            if not entry.desired_enabled:
                continue
            if entry.failed:
                record.load_error = (
                    f"上次加载失败（{entry.failed.get('version', '?')}）："
                    f"{entry.failed.get('message', '')}；可重试加载或恢复上一版本。"
                )
                continue
            self._load_record(record, entry)
        return dict(self.records)

    @staticmethod
    def _record_from_entry(entry: RegistryEntry) -> PluginRecord:
        record = PluginRecord(plugin_id=entry.plugin_id)
        if entry.valid and entry.desired is not None:
            record.version = entry.desired.version
            record.path = entry.desired.path
            record.digest = entry.desired.digest
            record.enabled = entry.desired_enabled
        else:
            record.enabled = False
        record.pending_removal = entry.pending_uninstall
        record.restore_pending = entry.restore_requested
        if entry.last_good is not None:
            record.last_good_version = entry.last_good.version
            record.last_good_digest = entry.last_good.digest
        if entry.previous_good is not None:
            record.previous_good_version = entry.previous_good.version
            record.previous_good_digest = entry.previous_good.digest
        if entry.failed:
            record.failed_candidate = dict(entry.failed)
            record.failed_candidate.setdefault("version", entry.failed.get("version", ""))
        record.source = dict(entry.source)
        if entry.last_error:
            record.last_operation_error = str(entry.last_error.get("message") or "")
        return record

    def _load_record(self, record: PluginRecord, entry: RegistryEntry) -> None:
        assert entry.desired is not None
        package_dir = self.root / entry.desired.path
        try:
            manifest_path = package_dir / "plugin.json"
            if not manifest_path.is_file():
                raise PluginError(f"安装内容缺失：{entry.desired.path}")
            # 激活前再核对一次内容摘要：从“提交安装”到“重启加载”之间，磁盘上的
            # 插件代码可能已经变了，不能拿着旧摘要直接执行。
            actual_digest = compute_content_digest(package_dir)
            if actual_digest != entry.desired.digest:
                raise PluginError(
                    "安装内容与登记表记录的内容摘要不一致（内容可能已被修改）；"
                    "已拒绝加载。"
                )
            manifest = PluginManifest.from_mapping(
                json.loads(manifest_path.read_text(encoding="utf-8"))
            )
            if manifest.plugin_id != record.plugin_id or manifest.version != entry.desired.version:
                raise PluginError("清单与登记记录的 id / 版本不一致。")
            manifest.check_compatibility(self.app_version)
            record.manifest = manifest
            instance = instantiate_plugin(manifest, package_dir)
        except PluginCompatibilityError as exc:
            record.load_error = str(exc)
            record.extra["incompatible"] = True
        except Exception as exc:  # 插件代码可能抛任何异常，全部拦在界面之外
            record.load_error = f"{type(exc).__name__}: {exc}"
            self.store.record_failed(
                record.plugin_id,
                version=entry.desired.version,
                digest=entry.desired.digest,
                phase="load",
                message=record.load_error,
            )
            record.failed_candidate = {
                "version": entry.desired.version,
                "digest": entry.desired.digest,
                "phase": "load",
                "message": record.load_error,
            }
        else:
            record.instance = instance
            record.running = True
            record.running_version = entry.desired.version
            record.started_revision = entry.desired_revision
            record.started_digest = entry.desired.digest

    def mark_healthy(self, plugin_id: str) -> None:
        """实例化与面板初始化全部成功后调用，更新成功/回退历史。"""
        record = self.records.get(plugin_id)
        if record is None or not record.running or record.instance is None:
            return
        outcome = self.store.mark_healthy(
            record.plugin_id,
            revision=record.started_revision,
            digest=record.started_digest,
        )
        if outcome is None:
            return
        last_good, previous_good = outcome
        record.last_good_version = last_good.version
        record.last_good_digest = last_good.digest
        if previous_good is not None:
            record.previous_good_version = previous_good.version
            record.previous_good_digest = previous_good.digest
        record.failed_candidate = None

    def report_init_failure(self, record: PluginRecord, message: str) -> None:
        """面板创建或挂载失败：记录失败候选并立即释放实例。"""
        record.load_error = message
        if record.running:
            self.store.record_failed(
                record.plugin_id,
                version=record.running_version,
                digest=record.started_digest,
                phase="panel",
                message=message,
            )
            record.failed_candidate = {
                "version": record.running_version,
                "digest": record.started_digest,
                "phase": "panel",
                "message": message,
            }
        self._release_record(record)

    def shutdown(self) -> None:
        """统一释放入口：每个实例的 ``release()`` 恰好调用一次。"""
        if self._shutdown_done:
            return
        self._shutdown_done = True
        for record in self.records.values():
            self._release_record(record)
        lease = self._lease
        self._lease = None
        if lease is not None:
            lease.release()
        self.records = {}

    @staticmethod
    def _release_record(record: PluginRecord) -> None:
        instance = record.instance
        record.instance = None
        record.running = False
        if instance is not None:
            try:
                instance.release()
            except Exception:
                pass

    # -- 期望配置操作（只改登记表，不动当前会话效果） ---------------------
    def install(self, path, *, source: Optional[InstallSource] = None) -> PluginManifest:
        """安装/更新扩展包；登记候选版本，当前会话实例不受影响。"""
        outcome = install_package(
            path, app_version=self.app_version, root=self.root, source=source
        )
        self._sync_record_after_install(outcome)
        return outcome.manifest

    def _sync_record_after_install(self, outcome: InstallOutcome) -> None:
        state = self.store.read_state()
        entry = state.entries.get(outcome.manifest.plugin_id)
        if entry is None or not entry.valid:
            return
        record = self.records.get(outcome.manifest.plugin_id)
        if record is None:
            record = self._record_from_entry(entry)
            self.records[record.plugin_id] = record
            return
        record.version = entry.desired.version if entry.desired else record.version
        record.path = entry.desired.path if entry.desired else record.path
        record.digest = entry.desired.digest if entry.desired else record.digest
        record.enabled = entry.desired_enabled
        record.pending_removal = entry.pending_uninstall
        record.restore_pending = entry.restore_requested
        record.failed_candidate = None
        record.load_error = ""
        record.last_operation_error = ""
        record.source = dict(entry.source)

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """改写期望启用状态；当前会话已加载的实例不受影响。"""
        plugin_id = normalize_plugin_id(plugin_id)
        self.store.set_desired_enabled(plugin_id, enabled)
        record = self.records.get(plugin_id)
        if record is None:
            return
        record.enabled = bool(enabled)
        if enabled:
            record.failed_candidate = None
            record.load_error = ""

    def note_removal(self, plugin_id: str) -> None:
        """记录卸载请求；下次启动时删除，当前会话效果保持不变。"""
        plugin_id = normalize_plugin_id(plugin_id)
        self.store.request_uninstall(plugin_id)
        record = self.records.get(plugin_id)
        if record is not None:
            record.pending_removal = True

    def request_restore(self, plugin_id: str) -> None:
        """登记“恢复上一版本”；重启时复核兼容后再执行。"""
        plugin_id = normalize_plugin_id(plugin_id)
        self.store.request_restore(plugin_id)
        record = self.records.get(plugin_id)
        if record is not None:
            record.restore_pending = True

    def retry_load(self, plugin_id: str) -> None:
        """登记“重试加载”：清除失败候选，下次启动重新尝试。"""
        plugin_id = normalize_plugin_id(plugin_id)
        self.store.retry_load(plugin_id)
        record = self.records.get(plugin_id)
        if record is not None:
            record.failed_candidate = None
            record.restore_pending = False
            if not record.running:
                record.load_error = ""

    # -- 查询 -----------------------------------------------------------
    def plugins(self) -> List[PluginRecord]:
        """会话记录，按插件 id 稳定排序（界面行序不随安装先后漂移）。"""
        return [self.records[key] for key in sorted(self.records)]

    def ready_plugins(self) -> List[PluginRecord]:
        return [record for record in self.records.values() if record.ready]

    def record(self, plugin_id: str) -> Optional[PluginRecord]:
        return self.records.get(str(plugin_id))

    def installed_manifest(self, record: PluginRecord) -> Optional[PluginManifest]:
        """只读已安装内容的清单，**不导入插件代码**（主程序升级评估用）。

        停用、加载失败或内容缺失的记录没有会话内清单，但升级评估仍要知道它声明
        的兼容范围；这里直接从内容目录读 ``plugin.json``，读到什么算什么。
        """
        if record is None:
            return None
        if not record.path:
            return record.manifest
        manifest_file = self.root / record.path / "plugin.json"
        if not manifest_file.is_file():
            return None
        try:
            return PluginManifest.from_mapping(
                json.loads(manifest_file.read_text(encoding="utf-8"))
            )
        except Exception:
            return None

    def incompatible(self) -> List[PluginRecord]:
        return [r for r in self.records.values() if r.extra.get("incompatible")]

    def notes(self) -> List[str]:
        return list(self._notes)
