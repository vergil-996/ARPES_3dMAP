# -*- coding: utf-8 -*-
"""扩展存储层：登记表 schema v2、跨进程锁、会话租约与不可变内容目录。

对照「插件管理入口与系统完善计划」阶段 B。本模块只负责磁盘与登记表事务，
不导入 Qt / VTK，也不导入插件代码；加载与生命周期在 ``plugin_manager``。

关键约定：

- 登记表每条记录分成 **desired（下次启动的期望配置）** 与运行历史
  （``last_good`` / ``previous_good`` / ``failed``），运行中的实例不序列化。
- 内容按 ``installed/<id>/<version>/<内容摘要>/`` **不可变**保存；同一 id/version
  下出现不同摘要视为发布错误，拒绝安装，要求提升插件版本。
- 内容摘要由受校验分发文件的规范相对路径、文件长度与文件字节按排序计算，
  字节码缓存（``__pycache__`` / ``*.pyc``）不进入摘要，也不写入内容目录。
- 登记表读改写使用跨进程 ``RootLock`` 串行化；每个会话持有一个 ``SessionLease``，
  只有确认没有其他活跃会话时才执行卸载与未引用内容清理。
- 登记表损坏或 schema 未知时保留原文件并报告诊断，禁止以空表覆盖。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from bandscope.app_metadata import APP_NAME
from bandscope.extensions.api import (
    PluginError,
    PluginManifest,
    normalize_plugin_id,
    normalize_plugin_version,
)

REGISTRY_FILE = "registry.json"
REGISTRY_VERSION = 2

INSTALLED_DIRNAME = "installed"
STAGING_DIRNAME = "staging"
LEASES_DIRNAME = "leases"
LOCK_FILENAME = "lock"

#: 迁移前的卸载标记目录（schema v1）；迁移提交成功后清理。
LEGACY_UNINSTALL_DIRNAME = "pending_uninstall"

#: 迁移重组目录的临时前缀：``installed/<id>/.migrate-<version>-<uuid>``。
MIGRATE_PREFIX = ".migrate-"

#: RootLock 的最长等待；超时视为另一个进程卡住，转成可展示错误。
LOCK_TIMEOUT_SECONDS = 30.0

_HEX_DIGITS = set("0123456789abcdef")


def extension_root() -> Path:
    """用户级扩展安装根目录。"""
    override = os.environ.get("BANDSCOPE_EXTENSION_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME / "extensions"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 跨进程锁原语
# ---------------------------------------------------------------------------


def _ensure_lockable(handle) -> None:
    """锁文件要至少有一个字节，字节区间锁才能覆盖到真实数据。"""
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)


if os.name == "nt":  # pragma: no cover - 平台分支
    import msvcrt

    def _try_lock(handle) -> bool:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(handle) -> None:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:  # pragma: no cover - 平台分支
    import fcntl

    def _try_lock(handle) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(handle) -> None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


def _lock_blocking(handle, *, timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if _try_lock(handle):
            return
        if time.monotonic() >= deadline:
            raise PluginError(
                "另一个 BandScope 实例正在修改插件配置，请稍后重试。"
            )
        time.sleep(0.05)


_lock_entries: Dict[str, dict] = {}
_lock_entries_guard = threading.Lock()


class RootLock:
    """扩展根目录的跨进程互斥锁。

    同一线程允许嵌套获取（内层只递增深度，不重复占用系统锁）；进程内用
    ``threading.RLock`` 串行化，进程间用文件字节区间锁。锁文件本身不承载
    任何状态，持有与否一律由操作系统锁判断。
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self._key = str(self.root.resolve())

    def __enter__(self) -> "RootLock":
        with _lock_entries_guard:
            entry = _lock_entries.setdefault(
                self._key, {"rlock": threading.RLock(), "depth": 0, "handle": None}
            )
        entry["rlock"].acquire()
        entry["depth"] += 1
        if entry["depth"] == 1:
            handle = None
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                handle = open(self.root / LOCK_FILENAME, "a+b")
                _ensure_lockable(handle)
                _lock_blocking(handle)
            except BaseException:
                if handle is not None:
                    handle.close()
                entry["depth"] = 0
                entry["rlock"].release()
                raise
            entry["handle"] = handle
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        with _lock_entries_guard:
            entry = _lock_entries[self._key]
        entry["depth"] -= 1
        if entry["depth"] == 0:
            handle = entry["handle"]
            entry["handle"] = None
            if handle is not None:
                _unlock(handle)
                handle.close()
        entry["rlock"].release()


class SessionLease:
    """一个运行会话的租约文件；是否存在由操作系统锁判断。"""

    def __init__(self, path: Path, handle):
        self.path = Path(path)
        self.name = self.path.stem
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            _unlock(handle)
            handle.close()
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 登记表 schema v2
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistryRef:
    """一段不可变版本内容的引用。"""

    version: str
    digest: str
    path: str


@dataclass
class RegistryEntry:
    """登记表里一条插件记录的解析结果。

    ``valid`` 为 False 时只保留 ``raw`` 原样展示，任何操作都不得改写入表。
    """

    plugin_id: str
    valid: bool = True
    reason: str = ""
    name: str = ""
    desired: Optional[RegistryRef] = None
    desired_enabled: bool = True
    desired_revision: int = 0
    last_good: Optional[RegistryRef] = None
    previous_good: Optional[RegistryRef] = None
    pending_uninstall: bool = False
    restore_requested: bool = False
    failed: Optional[dict] = None
    source: dict = field(default_factory=dict)
    last_error: Optional[dict] = None
    raw: dict = field(default_factory=dict)


@dataclass
class RegistryState:
    error: str = ""
    revision: int = 0
    entries: Dict[str, RegistryEntry] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


def _valid_digest(value) -> Optional[str]:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(char not in _HEX_DIGITS for char in text):
        return None
    return text


def _parse_ref(plugin_id: str, value) -> RegistryRef:
    if not isinstance(value, dict):
        raise PluginError("引用不是对象")
    version = normalize_plugin_version(value.get("version"))
    digest = _valid_digest(value.get("digest"))
    if digest is None:
        raise PluginError("内容摘要不是有效的 SHA-256")
    expected = f"{INSTALLED_DIRNAME}/{plugin_id}/{version}/{digest}"
    path = str(value.get("path") or "").replace("\\", "/")
    if path != expected:
        raise PluginError(f"内容路径与摘要不一致：{path!r}")
    return RegistryRef(version=version, digest=digest, path=expected)


def parse_entry(plugin_id: str, raw) -> RegistryEntry:
    """解析单条记录；结构无法确认时返回 ``valid=False`` 的裸条目。"""
    if not isinstance(raw, dict):
        return RegistryEntry(plugin_id=plugin_id, valid=False,
                             reason="记录不是 JSON 对象", raw={})
    try:
        desired = _parse_ref(plugin_id, raw.get("desired"))
    except (PluginError, TypeError) as exc:
        return RegistryEntry(plugin_id=plugin_id, valid=False,
                             reason=str(exc), raw=raw)
    last_good = None
    previous_good = None
    try:
        if raw.get("last_good"):
            last_good = _parse_ref(plugin_id, raw["last_good"])
        if raw.get("previous_good"):
            previous_good = _parse_ref(plugin_id, raw["previous_good"])
    except (PluginError, TypeError) as exc:
        return RegistryEntry(plugin_id=plugin_id, valid=False,
                             reason=f"成功版本记录异常：{exc}", raw=raw)
    failed = raw.get("failed")
    if failed is not None and not isinstance(failed, dict):
        failed = None
    last_error = raw.get("last_error")
    if last_error is not None and not isinstance(last_error, dict):
        last_error = None
    try:
        desired_revision = int(raw.get("desired", {}).get("revision") or 0)
    except (TypeError, ValueError):
        return RegistryEntry(plugin_id=plugin_id, valid=False,
                             reason="desired.revision 不是整数", raw=raw)
    return RegistryEntry(
        plugin_id=plugin_id,
        valid=True,
        name=str(raw.get("name") or plugin_id),
        desired=desired,
        desired_enabled=bool(raw.get("desired", {}).get("enabled", True)),
        desired_revision=desired_revision,
        last_good=last_good,
        previous_good=previous_good,
        pending_uninstall=bool(raw.get("pending_uninstall")),
        restore_requested=bool(raw.get("restore_requested")),
        failed=failed,
        source=raw.get("source") if isinstance(raw.get("source"), dict) else {},
        last_error=last_error,
        raw=raw,
    )


def compute_content_digest(directory: Path) -> str:
    """内容摘要：相对路径、长度与字节，按路径排序后逐文件更新。

    字节码缓存与运行目录里的其他非分发文件不参与：安装流程本来就不会把
    ``__pycache__`` 放进内容目录，这里跳过是为了容忍旧版本残留。
    """
    directory = Path(directory)
    files: List[Tuple[str, Path]] = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(directory)
        if "__pycache__" in relative.parts or relative.name.endswith(".pyc"):
            continue
        files.append((relative.as_posix(), path))
    digest = hashlib.sha256()
    for relative, path in sorted(files):
        data = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def _remove_bytecode_caches(directory: Path) -> None:
    for cache in Path(directory).rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    for stale in Path(directory).rglob("*.pyc"):
        try:
            stale.unlink()
        except OSError:
            pass


class PluginStore:
    """扩展根目录的读写入口；所有操作都走同一份 ``self.root``。"""

    def __init__(self, root: Path):
        self.root = Path(root)

    # -- 路径 -----------------------------------------------------------
    @property
    def registry_path(self) -> Path:
        return self.root / REGISTRY_FILE

    @property
    def installed_dir(self) -> Path:
        return self.root / INSTALLED_DIRNAME

    @property
    def staging_dir(self) -> Path:
        return self.root / STAGING_DIRNAME

    @property
    def leases_dir(self) -> Path:
        return self.root / LEASES_DIRNAME

    def content_dir(self, plugin_id: str, version: str, digest: str) -> Path:
        return self.installed_dir / plugin_id / version / digest

    def lock(self) -> RootLock:
        return RootLock(self.root)

    # -- 登记表读取 ------------------------------------------------------
    def read_state(self) -> RegistryState:
        """读取登记表；损坏、条目异常与未知 schema 都转成诊断信息。"""
        path = self.registry_path
        if not path.is_file():
            return RegistryState(
                raw={"version": REGISTRY_VERSION, "revision": 0, "plugins": {}}
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return RegistryState(error=f"登记表无法解析（{exc}）；原文件已保留。")
        if not isinstance(payload, dict):
            return RegistryState(error="登记表顶层不是 JSON 对象；原文件已保留。")
        version = payload.get("version")
        if version != REGISTRY_VERSION:
            return RegistryState(
                error=f"登记表 schema 版本 {version!r} 不受支持（需要 {REGISTRY_VERSION}）；原文件已保留。"
            )
        plugins = payload.get("plugins")
        if not isinstance(plugins, dict):
            return RegistryState(error="登记表的 plugins 字段不是对象；原文件已保留。")
        try:
            revision = int(payload.get("revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        entries = {
            str(plugin_id): parse_entry(str(plugin_id), raw)
            for plugin_id, raw in plugins.items()
        }
        return RegistryState(revision=revision, entries=entries, raw=payload)

    # -- 登记表写入 ------------------------------------------------------
    def _read_for_update(self) -> dict:
        """取回可写 payload；v1 先迁移，损坏或未知版本直接拒绝。"""
        if self.registry_path.is_file():
            head = None
            try:
                head = json.loads(self.registry_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                head = None
            if isinstance(head, dict) and head.get("version") == 1:
                error = self.ensure_schema()
                if error:
                    raise PluginError(error)
        state = self.read_state()
        if state.error:
            raise PluginError(state.error)
        payload = dict(state.raw)
        payload.setdefault("version", REGISTRY_VERSION)
        payload.setdefault("plugins", {})
        return payload

    def update(self, mutator: Callable[[dict], object]) -> int:
        """在根锁内完成一次读改写；返回配置修订号。

        ``mutator`` 收到 payload（``revision`` 已递增），只应改动目标条目，
        未确认的条目必须原样保留。**返回 False 表示无需改动**：此时不写盘，
        修订号保持不变——安全启动的维护步骤因此不会在每次启动都重写登记表。
        """
        with self.lock():
            payload = self._read_for_update()
            original = int(payload.get("revision") or 0)
            payload["revision"] = original + 1
            changed = mutator(payload)
            if changed is False:
                return original
            self._write_payload(payload)
            return original + 1

    def _write_payload(self, payload: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / f"{REGISTRY_FILE}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, self.registry_path)
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise

    @staticmethod
    def _entry_of(payload: dict, plugin_id: str) -> dict:
        plugins = payload.setdefault("plugins", {})
        entry = plugins.get(plugin_id)
        if not isinstance(entry, dict):
            raise PluginError(f"未安装扩展：{plugin_id}")
        parsed = parse_entry(plugin_id, entry)
        if not parsed.valid:
            raise PluginError(
                f"「{plugin_id}」的登记记录无法确认（{parsed.reason}），"
                "已保留原文件；修复前不能修改该条目。"
            )
        return entry

    # -- 各配置操作 ------------------------------------------------------
    def set_desired_enabled(self, plugin_id: str, enabled: bool) -> None:
        plugin_id = normalize_plugin_id(plugin_id)

        def mutate(payload):
            entry = self._entry_of(payload, plugin_id)
            entry["desired"]["enabled"] = bool(enabled)
            entry["desired"]["revision"] = payload["revision"]
            if enabled:
                # 用户明确要求重新加载：清掉失败历史，下次启动重新尝试。
                entry.pop("failed", None)

        self.update(mutate)

    def request_uninstall(self, plugin_id: str) -> None:
        plugin_id = normalize_plugin_id(plugin_id)

        def mutate(payload):
            entry = self._entry_of(payload, plugin_id)
            entry["pending_uninstall"] = True

        self.update(mutate)

    def request_restore(self, plugin_id: str) -> None:
        plugin_id = normalize_plugin_id(plugin_id)

        def mutate(payload):
            entry = self._entry_of(payload, plugin_id)
            entry["restore_requested"] = True

        self.update(mutate)

    def retry_load(self, plugin_id: str) -> None:
        """登记一次重试：清掉失败候选，让它在下一次启动重新加载。"""
        plugin_id = normalize_plugin_id(plugin_id)

        def mutate(payload):
            entry = self._entry_of(payload, plugin_id)
            entry.pop("failed", None)
            entry["restore_requested"] = False

        self.update(mutate)

    def record_failed(
        self, plugin_id: str, *, version: str, digest: str, phase: str, message: str
    ) -> None:
        """记录一次候选加载失败；下次启动不再自动重复同一候选。"""

        def mutate(payload):
            entry = payload.get("plugins", {}).get(plugin_id)
            parsed = parse_entry(plugin_id, entry) if isinstance(entry, dict) else None
            if parsed is None or not parsed.valid:
                return False
            if parsed.pending_uninstall:
                return False
            if parsed.desired is None or parsed.desired.digest != digest:
                # 配置已经变了，旧实例的回报不覆盖新选择。
                return False
            entry["failed"] = {
                "version": version,
                "digest": digest,
                "phase": phase,
                "message": message[:500],
                "at": _now(),
            }
            return True

        try:
            self.update(mutate)
        except PluginError:
            # 登记表不可写时只影响“下次启动的行为”，不能连累当前会话。
            pass

    def mark_healthy(
        self, plugin_id: str, *, revision: int, digest: str
    ) -> Optional[Tuple[RegistryRef, Optional[RegistryRef]]]:
        """实例与面板初始化成功后的健康回报。

        只有回报携带的配置修订与内容摘要仍与当前 ``desired`` 一致、
        且未被请求卸载时才更新成功历史，避免旧实例的晚回报覆盖新配置。
        """
        outcome: List[Optional[Tuple[RegistryRef, Optional[RegistryRef]]]] = [None]

        def mutate(payload):
            entry = payload.get("plugins", {}).get(plugin_id)
            parsed = parse_entry(plugin_id, entry) if isinstance(entry, dict) else None
            if parsed is None or not parsed.valid or parsed.desired is None:
                return False
            if parsed.pending_uninstall:
                return False
            if parsed.desired.digest != digest or parsed.desired_revision != int(revision):
                return False
            new_good = {
                "version": parsed.desired.version,
                "digest": parsed.desired.digest,
                "path": parsed.desired.path,
                "revision": parsed.desired_revision,
            }
            old_good = entry.get("last_good")
            if old_good != new_good:
                if isinstance(old_good, dict):
                    entry["previous_good"] = old_good
                entry["last_good"] = new_good
            entry.pop("failed", None)
            entry.pop("last_error", None)
            outcome[0] = (
                RegistryRef(parsed.desired.version, parsed.desired.digest, parsed.desired.path),
                parse_entry(plugin_id, entry).previous_good,
            )
            return True

        try:
            self.update(mutate)
        except PluginError:
            return None
        return outcome[0]

    def record_operation_error(self, plugin_id: str, *, phase: str, message: str) -> None:
        def mutate(payload):
            entry = payload.get("plugins", {}).get(plugin_id)
            if isinstance(entry, dict):
                entry["last_error"] = {
                    "phase": phase,
                    "message": message[:500],
                    "at": _now(),
                }

        try:
            self.update(mutate)
        except PluginError:
            pass

    # -- 安装内容 --------------------------------------------------------
    def commit_content(self, staging: Path, manifest: PluginManifest) -> Tuple[str, bool]:
        """把解包好的内容就位为不可变目录；返回 ``(摘要, 是否复用已有内容)``。

        同一 id/version 下若已有不同摘要的内容，说明发布方改了内容却没提升
        版本，直接拒绝，绝不覆盖已落盘的版本。
        """
        digest = compute_content_digest(staging)
        target = self.content_dir(manifest.plugin_id, manifest.version, digest)
        version_dir = target.parent
        with self.lock():
            if version_dir.is_dir():
                existing = [
                    child.name
                    for child in version_dir.iterdir()
                    if child.is_dir()
                    and child.name != digest
                    and _valid_digest(child.name) is not None
                ]
                if existing:
                    raise PluginError(
                        f"已安装同一版本 {manifest.version} 但内容不同；"
                        "请提升插件版本后再发布。"
                    )
            if target.exists():
                shutil.rmtree(staging, ignore_errors=True)
                return digest, True
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, target)
            return digest, False

    def remove_content(self, plugin_id: str) -> None:
        """删除一个插件的全部已安装内容；失败原样抛出让调用方保留待卸载记录。"""
        directory = self.installed_dir / plugin_id
        if directory.exists():
            shutil.rmtree(directory)
        if directory.exists():
            raise PluginError(f"目标目录未能完全移除：{directory}")

    # -- 会话租约 --------------------------------------------------------
    def acquire_lease(self) -> SessionLease:
        name = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.leases_dir.mkdir(parents=True, exist_ok=True)
        path = self.leases_dir / f"{name}.lock"
        handle = open(path, "a+b")
        try:
            _ensure_lockable(handle)
            _lock_blocking(handle)
        except BaseException:
            handle.close()
            raise
        return SessionLease(path, handle)

    def other_active_leases(self, *, exclude: str) -> List[str]:
        """其它仍在运行的会话；顺带清掉确认无人持有的陈旧租约文件。"""
        if not self.leases_dir.is_dir():
            return []
        active: List[str] = []
        for path in sorted(self.leases_dir.glob("*.lock")):
            if path.stem == exclude:
                continue
            try:
                handle = open(path, "a+b")
            except OSError:
                continue
            try:
                _ensure_lockable(handle)
                if _try_lock(handle):
                    # 能立刻拿到锁：没有人持有，陈旧文件。
                    _unlock(handle)
                    handle.close()
                    try:
                        path.unlink()
                    except OSError:
                        pass
                else:
                    active.append(path.stem)
                    handle.close()
            except OSError:
                handle.close()
        return active

    # -- 安全启动维护 ----------------------------------------------------
    def recover_relocations(self) -> List[str]:
        """修复迁移重组中断留下的 ``.migrate-*`` 目录。"""
        recovered: List[str] = []
        if not self.installed_dir.is_dir():
            return recovered
        for plugin_dir in sorted(self.installed_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            for leftover in sorted(plugin_dir.glob(f"{MIGRATE_PREFIX}*")):
                if not leftover.is_dir():
                    continue
                # 名字编码了版本：.migrate-<version>-<uuid>
                body = leftover.name[len(MIGRATE_PREFIX):]
                version = body.rsplit("-", 1)[0] if "-" in body else body
                version_dir = plugin_dir / version
                try:
                    if not version_dir.exists():
                        os.replace(leftover, version_dir)
                        recovered.append(str(leftover))
                    elif not any(version_dir.iterdir()):
                        version_dir.rmdir()
                        os.replace(leftover, version_dir)
                        recovered.append(str(leftover))
                    else:
                        shutil.rmtree(leftover, ignore_errors=True)
                except OSError:
                    continue
        return recovered

    def _migrate_v1_entry(self, plugin_id: str, raw) -> dict:
        """把一条 schema v1 记录连同磁盘内容转成 v2。"""
        if not isinstance(raw, dict):
            raise PluginError("记录不是 JSON 对象")
        version = normalize_plugin_version(raw.get("version"))
        enabled = bool(raw.get("enabled", True))
        legacy_path = str(raw.get("path") or "").replace("\\", "/").strip("/")
        expected = f"{plugin_id}/{version}"
        if legacy_path != expected:
            raise PluginError(f"安装路径 {legacy_path!r} 与 id/版本不一致")
        version_dir = self.installed_dir / plugin_id / version
        if not version_dir.is_dir():
            raise PluginError(f"安装目录不存在：{version_dir}")
        _remove_bytecode_caches(version_dir)

        direct_manifest = version_dir / "plugin.json"
        if direct_manifest.is_file():
            # 旧布局：内容直接在版本目录下。先算摘要，再搬进摘要子目录。
            digest = compute_content_digest(version_dir)
            final_dir = version_dir / digest
            if final_dir.exists():
                shutil.rmtree(final_dir, ignore_errors=True)
            temporary = version_dir.parent / f"{MIGRATE_PREFIX}{version}-{uuid.uuid4().hex[:8]}"
            os.replace(version_dir, temporary)
            try:
                version_dir.mkdir(parents=True)
                os.replace(temporary, final_dir)
            except OSError:
                # 尽力回退；recover_relocations 会在下次启动处理残留。
                if not version_dir.exists() and temporary.exists():
                    os.replace(temporary, version_dir)
                raise
            manifest_dir = final_dir
        else:
            # 可能已经重组过（上次迁移在提交登记表前中断）。
            candidates = [
                child
                for child in version_dir.iterdir()
                if child.is_dir() and _valid_digest(child.name)
            ]
            if len(candidates) != 1:
                raise PluginError("内容目录结构无法确认")
            manifest_dir = candidates[0]
            digest = manifest_dir.name

        manifest_file = manifest_dir / "plugin.json"
        if not manifest_file.is_file():
            raise PluginError("内容目录缺少 plugin.json")
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest = PluginManifest.from_mapping(payload)
        if manifest.plugin_id != plugin_id or manifest.version != version:
            raise PluginError("清单与登记记录的 id / 版本不一致")

        marker = self.root / LEGACY_UNINSTALL_DIRNAME / f"{plugin_id}.json"
        return {
            "name": manifest.name,
            "desired": {
                "version": version,
                "digest": digest,
                "path": f"{INSTALLED_DIRNAME}/{plugin_id}/{version}/{digest}",
                "enabled": enabled,
                "revision": 0,  # 迁移完成后由 _migrate_v1 统一填写修订号
            },
            "last_good": None,
            "previous_good": None,
            "pending_uninstall": marker.is_file(),
            "restore_requested": False,
            # 旧安装没有可验证的来源信息：迁移不赋予官方身份，如实标成历史未验证。
            "source": {
                "kind": "legacy",
                "verified": False,
                "key_id": "",
                "package_sha256": None,
            },
        }

    def ensure_schema(self) -> str:
        """确认登记表为 schema v2；v1 先备份再原子迁移。返回错误信息或空串。"""
        path = self.registry_path
        if not path.is_file():
            return ""
        with self.lock():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                return f"登记表无法解析（{exc}）；原文件已保留。"
            if not isinstance(payload, dict):
                return "登记表顶层不是 JSON 对象；原文件已保留。"
            version = payload.get("version")
            if version == REGISTRY_VERSION:
                return ""
            if version != 1:
                return (
                    f"登记表 schema 版本 {version!r} 不受支持（需要 {REGISTRY_VERSION}）；"
                    "原文件已保留。"
                )
            return self._migrate_v1(payload)

    def _migrate_v1(self, payload: dict) -> str:
        backup = self.root / f"{REGISTRY_FILE}.v1.bak"
        try:
            shutil.copyfile(self.registry_path, backup)
        except OSError as exc:
            return f"迁移前备份登记表失败（{exc}）；原文件已保留。"
        plugins = payload.get("plugins")
        if not isinstance(plugins, dict):
            return "登记表 v1 的 plugins 字段不是对象；原文件已保留。"
        converted: Dict[str, object] = {}
        migrated_ids: List[str] = []
        for plugin_id, raw in plugins.items():
            try:
                normalized = normalize_plugin_id(plugin_id)
            except PluginError:
                converted[str(plugin_id)] = raw  # 无法确认：原样保留
                continue
            if normalized != plugin_id:
                converted[str(plugin_id)] = raw
                continue
            try:
                converted[normalized] = self._migrate_v1_entry(normalized, raw)
                migrated_ids.append(normalized)
            except (PluginError, OSError, ValueError, KeyError, UnicodeDecodeError) as exc:
                # 单条无法确认：保留原样，不加载、不修改。
                print(f"Plugin registry entry {plugin_id!r} left unconfirmed: {exc}")
                converted[str(plugin_id)] = raw
        new_payload = {
            "version": REGISTRY_VERSION,
            "revision": 1,
            "plugins": converted,
        }
        for plugin_id in migrated_ids:
            entry = new_payload["plugins"][plugin_id]
            entry["desired"]["revision"] = 1
        try:
            self._write_payload(new_payload)
        except OSError as exc:
            return f"迁移登记表写入失败（{exc}）；v1 原文件已保留。"
        # v2 原子提交成功后才清理旧卸载标记。
        for plugin_id in migrated_ids:
            marker = self.root / LEGACY_UNINSTALL_DIRNAME / f"{plugin_id}.json"
            if marker.is_file() and new_payload["plugins"][plugin_id].get("pending_uninstall"):
                try:
                    marker.unlink()
                except OSError:
                    pass
        return ""

    def apply_pending_uninstalls(self) -> Tuple[List[str], List[str]]:
        """执行待卸载记录；删除失败的原样保留，下次安全启动重试。"""
        removed: List[str] = []
        failures: List[str] = []

        def mutate(payload):
            changed = False
            plugins = payload.get("plugins", {})
            for plugin_id in sorted(list(plugins.keys())):
                parsed = parse_entry(plugin_id, plugins.get(plugin_id))
                if not parsed.valid or not parsed.pending_uninstall:
                    continue
                try:
                    self.remove_content(plugin_id)
                except (OSError, PluginError) as exc:
                    plugins[plugin_id]["last_error"] = {
                        "phase": "uninstall",
                        "message": f"删除失败：{exc}",
                        "at": _now(),
                    }
                    failures.append(f"{plugin_id}：{exc}")
                    changed = True
                    continue
                plugins.pop(plugin_id)
                removed.append(plugin_id)
                changed = True
            return changed

        try:
            self.update(mutate)
        except PluginError as exc:
            if not removed and not failures:
                failures.append(str(exc))
        # 旧版留下的卸载标记：只处理登记表里已不存在的条目；登记表读不出来时
        # 不凭标记删除任何内容。
        legacy = self.root / LEGACY_UNINSTALL_DIRNAME
        state = self.read_state()
        if legacy.is_dir() and not state.error:
            for marker in sorted(legacy.glob("*.json")):
                try:
                    plugin_id = normalize_plugin_id(
                        json.loads(marker.read_text(encoding="utf-8")).get("plugin_id", "")
                    )
                except (OSError, ValueError, PluginError):
                    marker.unlink(missing_ok=True)
                    continue
                if plugin_id in state.raw.get("plugins", {}):
                    continue
                try:
                    self.remove_content(plugin_id)
                except (OSError, PluginError) as exc:
                    failures.append(f"{plugin_id}：{exc}")
                    continue
                marker.unlink(missing_ok=True)
                removed.append(plugin_id)
        return removed, failures

    def apply_restore_requests(self, app_version: str) -> List[str]:
        """执行“恢复上一版本”请求；先复核目标与当前宿主兼容。

        返回已生效的描述文本，供管理窗口/日志展示。
        """
        applied: List[str] = []

        def mutate(payload):
            changed = False
            plugins = payload.get("plugins", {})
            for plugin_id in sorted(list(plugins.keys())):
                raw = plugins.get(plugin_id)
                parsed = parse_entry(plugin_id, raw)
                if not parsed.valid or not parsed.restore_requested:
                    continue
                changed = True
                target = None
                if parsed.last_good is not None and (
                    parsed.desired is None
                    or parsed.last_good.digest != parsed.desired.digest
                    or parsed.last_good.version != parsed.desired.version
                ):
                    target = parsed.last_good
                elif parsed.previous_good is not None and (
                    parsed.desired is None
                    or parsed.previous_good.digest != parsed.desired.digest
                    or parsed.previous_good.version != parsed.desired.version
                ):
                    target = parsed.previous_good
                raw["restore_requested"] = False
                if target is None:
                    raw["last_error"] = {
                        "phase": "restore",
                        "message": "没有可恢复的上一版本。",
                        "at": _now(),
                    }
                    applied.append(f"{plugin_id} 没有可恢复的上一版本")
                    continue
                manifest_file = self.root / target.path / "plugin.json"
                try:
                    manifest = PluginManifest.from_mapping(
                        json.loads(manifest_file.read_text(encoding="utf-8"))
                    )
                    manifest.check_compatibility(app_version)
                except Exception as exc:
                    raw["last_error"] = {
                        "phase": "restore",
                        "message": f"上一版本 {target.version} 未恢复：{exc}",
                        "at": _now(),
                    }
                    applied.append(f"{plugin_id} 恢复 {target.version} 失败：{exc}")
                    continue
                raw["desired"] = {
                    "version": target.version,
                    "digest": target.digest,
                    "path": target.path,
                    "enabled": parsed.desired_enabled if parsed.desired else True,
                    "revision": payload["revision"],
                }
                raw.pop("failed", None)
                raw.pop("last_error", None)
                applied.append(f"{plugin_id} 恢复到 {target.version}")
            return changed

        try:
            self.update(mutate)
        except PluginError as exc:
            applied.append(f"恢复失败：{exc}")
        return applied

    def cleanup_unreferenced(self) -> List[str]:
        """安全启动时清掉既非期望、也非成功历史的版本内容。

        待卸载的插件整体跳过：它们的删除由卸载流程负责，失败要能重试。
        """
        removed: List[str] = []
        if not self.installed_dir.is_dir():
            return removed
        state = self.read_state()
        if state.error:
            return removed
        keep: Dict[str, set] = {}
        for plugin_id, entry in state.entries.items():
            if not entry.valid or entry.pending_uninstall:
                keep[plugin_id] = None  # None 表示“整棵树保留”
                continue
            paths = set()
            for ref in (entry.desired, entry.last_good, entry.previous_good):
                if ref is not None:
                    paths.add(ref.path.replace("\\", "/"))
            keep[plugin_id] = paths
        for plugin_dir in sorted(self.installed_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            plugin_id = plugin_dir.name
            # None 表示“登记表里无法确认 / 待卸载”：整棵树保留；
            # 不在 keep 里的插件没有登记条目，按未引用内容清理。
            if plugin_id in keep and keep[plugin_id] is None:
                continue
            paths = keep.get(plugin_id, set())
            for version_dir in sorted(plugin_dir.iterdir()):
                if not version_dir.is_dir() or version_dir.name.startswith(MIGRATE_PREFIX):
                    continue
                for content in sorted(version_dir.iterdir()):
                    if not content.is_dir():
                        continue
                    relative = f"{INSTALLED_DIRNAME}/{plugin_id}/{version_dir.name}/{content.name}"
                    if relative in paths:
                        continue
                    try:
                        shutil.rmtree(content)
                        removed.append(relative)
                    except OSError:
                        continue
                try:
                    if not any(version_dir.iterdir()):
                        version_dir.rmdir()
                except OSError:
                    pass
            try:
                if not any(plugin_dir.iterdir()):
                    plugin_dir.rmdir()
            except OSError:
                pass
        return removed
