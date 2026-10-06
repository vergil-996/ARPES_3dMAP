# -*- coding: utf-8 -*-
"""官方插件目录：解析、验签、缓存、版本选择与下载（阶段 E）。

目录跟着主程序 Release 一起发布，是**唯一**的在线插件来源；不另建商店后端。
它本身也签名：目录验签失败就禁用本次在线安装，绝不用未验证内容去决定装什么。

几条固定规则：

- 目录只列**不可变**的 Release 资源地址，并且保留仍受支持宿主的历史匹配包；
  聚合历史记录时不允许把已有的 id/version 换成不同内容。
- 稳定目录默认只推荐稳定版本，不自动选预发布版，也不自动降级。
- 本地非标准版本（例如 ``beta-2``）仍可在本机登记表与界面上展示，但不参加
  在线“最新版本”比较——拿不可比的字符串排序会给出看似权威的错误结论。
- 评估升级时用**目录声明**的目标宿主版本与支持 API 集合，不能用当前 API 当作
  未来宿主的接口。

本模块不导入 Qt；界面线程与进度由调用方负责。
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from bandscope.app_metadata import APP_NAME, APP_VERSION
from bandscope.extensions.api import PluginError, normalize_plugin_id, normalize_plugin_version
from bandscope.extensions.compat import (
    SUPPORTED_API_VERSIONS,
    SUPPORTED_CAPABILITIES,
    compare_versions,
    evaluate_compatibility,
    is_prerelease,
    sortable_version,
)
from bandscope.extensions.plugin_store import extension_root
from bandscope.extensions.trust import (
    PURPOSE_CATALOG,
    PURPOSE_PLUGIN,
    SOURCE_OFFICIAL,
    TrustError,
    sidecar_path,
    verify_file,
)
from bandscope.updates import net
from bandscope.updates.update_service import GitHubReleaseClient

CATALOG_FILENAME = "plugins-index.json"
CATALOG_SCHEMA = 1

#: 目录自身的下载上限（计划规定 2 MiB）。
MAX_CATALOG_BYTES = 2 * 1024 * 1024
#: 单个插件包的下载上限（与解包上限一致：64 MiB）。
MAX_PLUGIN_PACKAGE_BYTES = 64 * 1024 * 1024
#: 签名 sidecar 的下载上限；只装一个 Base64 签名。
NET_SIGNATURE_LIMIT = 8 * 1024


class CatalogError(PluginError):
    """目录缺失、结构不合法、验签失败或与本地状态冲突。"""


@dataclass(frozen=True)
class CatalogAsset:
    """目录里指向某个不可变 Release 资源的引用。"""

    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class CatalogEntry:
    """一条插件版本记录。"""

    plugin_id: str
    name: str
    version: str
    requires_app: str
    api_version: int
    capabilities: Tuple[str, ...]
    package: CatalogAsset
    signature: Optional[CatalogAsset] = None
    notes: str = ""

    @property
    def signed(self) -> bool:
        """是否带有可验证的签名资源；没有的按“历史未签名包”对待。"""
        return self.signature is not None

    @property
    def sortable_version(self):
        return sortable_version(self.version)


@dataclass(frozen=True)
class Catalog:
    """解析后的官方目录。"""

    revision: int
    generated_at: str
    host_version: str
    host_api_versions: Tuple[int, ...]
    entries: Tuple[CatalogEntry, ...]
    schema: int = CATALOG_SCHEMA

    def for_plugin(self, plugin_id: str) -> List[CatalogEntry]:
        return [entry for entry in self.entries if entry.plugin_id == plugin_id]

    def stable_versions(self, plugin_id: str) -> List[CatalogEntry]:
        """该插件可排序的稳定版本，按版本升序。"""
        items = [
            entry
            for entry in self.for_plugin(plugin_id)
            if entry.sortable_version is not None and not is_prerelease(entry.version)
        ]
        return sorted(items, key=lambda entry: entry.sortable_version)


# ---------------------------------------------------------------------------
# 解析与校验
# ---------------------------------------------------------------------------


def _require_mapping(value, what: str) -> dict:
    if not isinstance(value, dict):
        raise CatalogError(f"{what}必须是 JSON 对象。")
    return value


def _parse_int(value, what: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CatalogError(f"{what}必须是整数。") from exc


def _parse_asset(value, what: str, *, limit: int) -> CatalogAsset:
    data = _require_mapping(value, what)
    name = str(data.get("name") or "").strip()
    url = str(data.get("url") or "").strip()
    if not name or Path(name).name != name:
        raise CatalogError(f"{what}的文件名无效：{name!r}")
    if not url:
        raise CatalogError(f"{what}缺少下载地址。")
    try:
        net.ensure_https_download_url(url)
    except net.NetworkError as exc:
        raise CatalogError(f"{what}的下载地址不可信：{exc}") from exc
    size = _parse_int(data.get("size") or 0, f"{what}的大小")
    if size < 0 or size > limit:
        raise CatalogError(f"{what}的大小超出允许范围：{size} 字节。")
    sha256 = net.normalize_sha256(data.get("sha256"))
    if not sha256:
        raise CatalogError(f"{what}缺少有效的 SHA-256。")
    return CatalogAsset(name=name, url=url, size=size, sha256=sha256)


def _parse_entry(value) -> CatalogEntry:
    data = _require_mapping(value, "插件记录")
    plugin_id = normalize_plugin_id(data.get("id"))
    version = normalize_plugin_version(data.get("version"))
    capabilities = data.get("capabilities") or ()
    if isinstance(capabilities, str):
        capabilities = [capabilities]
    if not isinstance(capabilities, (list, tuple)):
        raise CatalogError("插件的 capabilities 必须是列表。")
    signature = None
    if data.get("signature"):
        signature = _parse_asset(
            data["signature"], f"{plugin_id} 的签名", limit=NET_SIGNATURE_LIMIT
        )
    return CatalogEntry(
        plugin_id=plugin_id,
        name=str(data.get("name") or plugin_id),
        version=version,
        requires_app=str(data.get("requires_app") or ""),
        api_version=_parse_int(data.get("api_version"), f"{plugin_id} 的 api_version"),
        capabilities=tuple(str(item) for item in capabilities),
        package=_parse_asset(
            data.get("package"), f"{plugin_id} 的插件包", limit=MAX_PLUGIN_PACKAGE_BYTES
        ),
        signature=signature,
        notes=str(data.get("notes") or ""),
    )



def parse_catalog(payload) -> Catalog:
    """解析目录字节；结构不合法、schema 未知或记录自相矛盾都直接拒绝。"""
    if isinstance(payload, (bytes, bytearray)):
        try:
            text = bytes(payload).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CatalogError("插件目录不是有效的 UTF-8 文本。") from exc
    else:
        text = str(payload)
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise CatalogError("插件目录不是有效的 JSON。") from exc
    data = _require_mapping(raw, "插件目录")

    schema = _parse_int(data.get("schema"), "目录 schema")
    if schema != CATALOG_SCHEMA:
        raise CatalogError(
            f"插件目录 schema {schema} 不受支持（当前支持 {CATALOG_SCHEMA}）。"
        )
    revision = _parse_int(data.get("revision"), "目录修订号")
    host = _require_mapping(data.get("host"), "目录的宿主信息")
    host_version = str(host.get("app_version") or "").strip()
    if not host_version:
        raise CatalogError("目录没有声明目标宿主版本。")
    raw_apis = host.get("api_versions") or ()
    if not isinstance(raw_apis, (list, tuple)) or not raw_apis:
        raise CatalogError("目录没有声明支持的接口版本集合。")
    host_apis = tuple(_parse_int(item, "宿主接口版本") for item in raw_apis)

    raw_entries = data.get("plugins")
    if not isinstance(raw_entries, list):
        raise CatalogError("插件目录的 plugins 必须是列表。")
    entries: List[CatalogEntry] = []
    seen: dict = {}
    for item in raw_entries:
        entry = _parse_entry(item)
        key = (entry.plugin_id, entry.version)
        previous = seen.get(key)
        if previous is not None and previous.package.sha256 != entry.package.sha256:
            # 同一 id/version 出现不同内容：历史聚合出错，不能默默挑一个。
            raise CatalogError(
                f"目录里 {entry.plugin_id} {entry.version} 出现了不同内容的包；"
                "聚合历史记录时必须保持同一版本内容不变。"
            )
        seen[key] = entry
        entries.append(entry)
    return Catalog(
        revision=revision,
        generated_at=str(data.get("generated_at") or ""),
        host_version=host_version,
        host_api_versions=host_apis,
        entries=tuple(entries),
        schema=schema,
    )


def load_catalog(path, *, trusted_keys=None) -> Catalog:
    """读取并**验签**一份目录文件；签名不过就不返回内容。"""
    target = Path(path)
    try:
        payload = target.read_bytes()
    except OSError as exc:
        raise CatalogError(f"无法读取插件目录：{exc}") from exc
    if len(payload) > MAX_CATALOG_BYTES:
        raise CatalogError("插件目录超出大小上限，已拒绝。")
    try:
        verify_file(target, purpose=PURPOSE_CATALOG, trusted_keys=trusted_keys)
    except TrustError as exc:
        raise CatalogError(f"插件目录验签失败：{exc}") from exc
    return parse_catalog(payload)


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------


def catalog_cache_dir() -> Path:
    """目录缓存的落点：与扩展安装根同属一个应用数据目录。

    跟着 ``extension_root()`` 走，而不是另拼一份 ``%LOCALAPPDATA%``：测试与验收
    只要隔离了扩展根，目录缓存也就一起被隔离，不会读到用户的真实数据。
    """
    return extension_root().parent / "plugins"


def _cache_meta_path(directory: Path) -> Path:
    return Path(directory) / f"{CATALOG_FILENAME}.meta"


def read_cached_catalog(directory: Optional[Path] = None) -> Optional[Catalog]:
    """读取本地缓存的目录；缓存缺失或损坏时返回 None（不抛异常）。"""
    folder = Path(directory) if directory is not None else catalog_cache_dir()
    path = folder / CATALOG_FILENAME
    if not path.is_file():
        return None
    try:
        payload = path.read_bytes()
        catalog = parse_catalog(payload)
    except (OSError, CatalogError):
        return None
    # 缓存也要能验签：断网时展示的仍是“最后一次验证通过”的内容。
    try:
        verify_file(path, purpose=PURPOSE_CATALOG)
    except TrustError:
        return None
    return catalog


def cached_verified_at(directory: Optional[Path] = None) -> str:
    folder = Path(directory) if directory is not None else catalog_cache_dir()
    try:
        payload = json.loads(_cache_meta_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(payload.get("verified_at") or "")


def store_cached_catalog(
    catalog: Catalog,
    payload: bytes,
    signature: bytes,
    *,
    directory: Optional[Path] = None,
    verified_at: str = "",
) -> bool:
    """写入缓存；修订号低于本机已接受值时拒绝替换。

    返回是否写入。被拒绝不是错误：旧目录可能来自被回滚或缓存的响应，用它替换
    更新过的缓存会让用户“退回”到看不到新版插件的状态。
    """
    folder = Path(directory) if directory is not None else catalog_cache_dir()
    existing = read_cached_catalog(folder) if (folder / CATALOG_FILENAME).is_file() else None
    if existing is not None and catalog.revision < existing.revision:
        return False
    folder.mkdir(parents=True, exist_ok=True)
    (folder / CATALOG_FILENAME).write_bytes(bytes(payload))
    sidecar_path(folder / CATALOG_FILENAME).write_bytes(bytes(signature))
    _cache_meta_path(folder).write_text(
        json.dumps(
            {"revision": catalog.revision, "verified_at": verified_at},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return True


# ---------------------------------------------------------------------------
# 版本选择
# ---------------------------------------------------------------------------

STATUS_INSTALLABLE = "installable"
STATUS_UPDATE_AVAILABLE = "update_available"
STATUS_UP_TO_DATE = "up_to_date"
STATUS_INCOMPATIBLE = "incompatible"
STATUS_NOT_IN_CATALOG = "not_in_catalog"


@dataclass(frozen=True)
class CatalogMatch:
    """某个插件在目录里的匹配结论。"""

    plugin_id: str
    status: str
    entry: Optional[CatalogEntry] = None
    installed_version: str = ""
    reason: str = ""

    @property
    def can_install(self) -> bool:
        return self.entry is not None and self.status in (
            STATUS_INSTALLABLE,
            STATUS_UPDATE_AVAILABLE,
        )


def match_catalog_entry(
    catalog: Catalog,
    plugin_id: str,
    *,
    installed_version: str = "",
    app_version: str,
    supported_apis: Optional[Sequence[int]] = None,
    supported_capabilities: Optional[Sequence[str]] = None,
) -> CatalogMatch:
    """为一个插件挑出目录里最合适的版本。

    ``app_version`` / ``supported_apis`` 是**目标宿主**的声明：主程序升级评估
    要传目录里写明的未来宿主版本与接口集合，不能拿当前接口当未来接口。
    """
    apis = tuple(supported_apis if supported_apis is not None else catalog.host_api_versions)
    capabilities = tuple(
        supported_capabilities if supported_capabilities is not None else SUPPORTED_CAPABILITIES
    )
    entries = catalog.for_plugin(plugin_id)
    if not entries:
        return CatalogMatch(plugin_id, STATUS_NOT_IN_CATALOG, installed_version=installed_version)

    installed = sortable_version(installed_version)
    compatible: List[CatalogEntry] = []
    reasons: List[str] = []
    for entry in catalog.stable_versions(plugin_id):
        if installed is not None and compare_versions(entry.version, installed_version) <= 0:
            # 不自动降级，也不把同版本重复当成更新。
            continue
        verdict = evaluate_compatibility(
            name=entry.name,
            requires_app=entry.requires_app,
            api_version=entry.api_version,
            capabilities=entry.capabilities,
            app_version=app_version,
            supported_apis=apis,
            supported_capabilities=capabilities,
        )
        if verdict.ok:
            compatible.append(entry)
        else:
            reasons.append(f"{entry.version}：{verdict.reason}")

    if compatible:
        best = compatible[-1]
        if installed is None:
            # 本地版本不可比（历史非标准版本）：不当作“有更新”，只说可以安装官方版本。
            reason = ""
            if installed_version:
                reason = (
                    f"本地安装的是非标准版本 {installed_version}，无法与在线版本比较；"
                    f"安装 {best.version} 会更换来源。"
                )
            return CatalogMatch(
                plugin_id, STATUS_INSTALLABLE, entry=best,
                installed_version=installed_version, reason=reason,
            )
        return CatalogMatch(
            plugin_id, STATUS_UPDATE_AVAILABLE, entry=best,
            installed_version=installed_version,
        )

    if installed is not None and catalog.stable_versions(plugin_id):
        # 已装到最新且兼容：稳定版本里没有更高的可选版本。
        highest = catalog.stable_versions(plugin_id)[-1]
        if compare_versions(highest.version, installed_version) <= 0:
            return CatalogMatch(
                plugin_id, STATUS_UP_TO_DATE, entry=highest,
                installed_version=installed_version,
            )

    reason = reasons[-1] if reasons else "目录里没有适用于当前宿主与接口的版本。"
    return CatalogMatch(
        plugin_id, STATUS_INCOMPATIBLE, installed_version=installed_version, reason=reason
    )


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FetchedCatalog:
    """刚取回并通过验签的目录，连同原始字节（缓存要原样保存）。"""

    catalog: Catalog
    payload: bytes
    signature: bytes
    verified_at: str = ""


@dataclass(frozen=True)
class DownloadedPackage:
    """下载并验证通过的插件包。"""

    entry: CatalogEntry
    path: Path
    sha256: str
    verified: bool


class CatalogClient:
    """从最新 Release 取目录、下载插件包。

    所有下载都走 :mod:`bandscope.updates.net` 的主机白名单、大小上限与摘要校验；
    目录必须验签通过才会被解析，插件包必须与目录声明的名字、大小、摘要一致。
    """

    def __init__(
        self,
        release_client: Optional[GitHubReleaseClient] = None,
        *,
        timeout: int = net.DEFAULT_TIMEOUT_SECONDS,
        opener: Optional[Callable[..., object]] = None,
    ):
        self.release_client = release_client or GitHubReleaseClient(timeout=timeout)
        self.timeout = int(timeout)
        self._opener = opener

    def _open(self, url: str):
        return net.open_stream(
            url,
            opener=self._opener,
            timeout=self.timeout,
            accept="application/octet-stream",
            user_agent=f"{APP_NAME}/{APP_VERSION}",
        )

    def fetch_catalog(self, destination_dir: Optional[Path] = None) -> FetchedCatalog:
        """取最新 Release 里的签名目录；验签失败不返回任何内容。"""
        release = self.release_client.fetch_latest_release()
        assets = {asset.name: asset for asset in release.assets}
        index_asset = assets.get(CATALOG_FILENAME)
        if index_asset is None:
            raise CatalogError(
                f"最新 Release 没有提供插件目录（{CATALOG_FILENAME}）；"
                "本次不能在线安装插件。"
            )
        signature_asset = assets.get(f"{CATALOG_FILENAME}.sig")
        if signature_asset is None:
            raise CatalogError("插件目录缺少签名文件，已拒绝使用未验证内容。")

        folder = Path(destination_dir) if destination_dir else Path(tempfile.mkdtemp(prefix="bandscope-catalog-"))
        folder.mkdir(parents=True, exist_ok=True)
        index_path = folder / CATALOG_FILENAME
        with self._open(index_asset.download_url) as stream:
            # 目录自己也签名，所以这里只按大小与声明摘要校验，验签在下面统一做。
            net.save_stream(
                stream, index_path,
                max_bytes=MAX_CATALOG_BYTES,
                expected_size=index_asset.size,
                expected_sha256=index_asset.sha256,
            )
        signature_path = sidecar_path(index_path)
        with self._open(signature_asset.download_url) as stream:
            net.save_stream(
                stream, signature_path,
                max_bytes=NET_SIGNATURE_LIMIT,
                expected_size=signature_asset.size,
                expected_sha256=signature_asset.sha256,
            )
        catalog = load_catalog(index_path)
        return FetchedCatalog(
            catalog=catalog,
            payload=index_path.read_bytes(),
            signature=signature_path.read_bytes(),
            verified_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def download_package(
        self,
        entry: CatalogEntry,
        destination_dir: Path,
        *,
        progress: Optional[Callable[[int, int], None]] = None,
        cancelled: Optional[Callable[[], bool]] = None,
    ) -> DownloadedPackage:
        """下载 ``entry`` 的包与签名；任何不一致都抛错且不留半截文件。"""
        folder = Path(destination_dir)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / entry.package.name
        with self._open(entry.package.url) as stream:
            path, digest = net.save_stream(
                stream,
                target,
                max_bytes=MAX_PLUGIN_PACKAGE_BYTES,
                expected_size=entry.package.size,
                expected_sha256=entry.package.sha256,
                progress=progress,
                cancelled=cancelled,
            )
        verified = False
        if entry.signature is not None:
            signature_target = sidecar_path(path)
            with self._open(entry.signature.url) as stream:
                net.save_stream(
                    stream, signature_target,
                    max_bytes=NET_SIGNATURE_LIMIT,
                    expected_size=entry.signature.size,
                    expected_sha256=entry.signature.sha256,
                )
            try:
                verify_file(path, purpose=PURPOSE_PLUGIN)
            except TrustError as exc:
                path.unlink(missing_ok=True)
                sidecar_path(path).unlink(missing_ok=True)
                raise CatalogError(f"插件包验签失败：{exc}") from exc
            verified = True
        return DownloadedPackage(entry=entry, path=path, sha256=digest, verified=verified)

    def install_source(self, entry: CatalogEntry, *, verified: bool):
        """把目录声明转成安装事务要求的来源信息。"""
        from bandscope.extensions.plugin_manager import InstallSource

        return InstallSource(
            kind=SOURCE_OFFICIAL if verified else "local",
            accepted_unverified=not verified,
            expected_id=entry.plugin_id,
            expected_version=entry.version,
            expected_sha256=entry.package.sha256,
        )
