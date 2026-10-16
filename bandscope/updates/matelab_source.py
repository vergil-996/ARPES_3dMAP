# -*- coding: utf-8 -*-
"""MatElab（物质科学电子实验室）更新源。

发布约定：在 MatElab 上建一个发布记录本（默认「BandScope发布」），每个正式版
对应一条记录，记录关键词包含 ``MATELAB_RELEASE_KEYWORD``；记录内有一个名为
``manifest`` 的字段，内容为 JSON：``{"version": "1.14.0", "notes": "…",
"published_at": "…"}``；安装包作为文件字段挂在记录里，文件名遵循
:func:`bandscope.app_metadata.installer_asset_name` 的命名。

接口形状依据官方帮助文档（数据 API 接口）并对线上只读接口做过实测：

- 登录 ``POST {base}/tokens``（表单），刷新 ``POST {base}/tokens/tokens_refresh``；
- 业务接口一律 ``POST`` JSON + ``Authorization: Bearer {token}``，
  token 过期返回 ``code == "refresh"``；
- 记录本列表 ``/eln_api/elns``、记录列表 ``/eln_api/items``、
  数据导出 ``/eln_api/export``；
- 文件下载 ``GET {base}/eln_url/eln?token=…&eln=记录本id&i=记录id&f=文件名&h=sha256&s=大小&c=1``。

本模块不导入 Qt；凭证由调用方（Qt 层）以零参 callable 注入，网络层可注入
假 opener 以便单元测试。
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from html import unescape as _html_unescape
from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.parse import urlencode, urlparse

from bandscope.app_metadata import (
    APP_NAME,
    APP_VERSION,
    MATELAB_API_BASE,
    MATELAB_RELEASE_KEYWORD,
    MATELAB_RELEASE_NOTEBOOK,
    MATELAB_WEB_URL,
    installer_asset_name,
)
from bandscope.updates.net import (
    DEFAULT_TIMEOUT_SECONDS,
    DownloadIntegrityError,
    ensure_https_download_url,
    save_stream,
)
from bandscope.updates.update_service import (
    MAX_INSTALLER_BYTES,
    ReleaseAsset,
    ReleaseAssetNotFound,
    ReleaseFormatError,
    ReleaseInfo,
    SemanticVersion,
    UpdateCheckResult,
    UpdateError,
    update_cache_dir,
)

#: 发布记录里安装包文件名的识别模式（与清理缓存用的命名约定一致）。
_INSTALLER_PATTERN = re.compile(
    rf"^{re.escape(APP_NAME)}-(?P<version>[^-]+)-Windows-x64-(?:CPU|NVIDIA)-Setup\.exe$",
    re.IGNORECASE,
)
_TITLE_VERSION_PATTERN = re.compile(r"v?(?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_MANIFEST_FIELD = "manifest"


class MatelabAuthError(UpdateError):
    """MatElab 登录失败、凭证缺失或登录状态失效。"""


@dataclass(frozen=True)
class MatelabCredentials:
    username: str
    password: str


@dataclass(frozen=True)
class _FileRef:
    """构造下载 URL 所需的全部要素（来自导出数据与记录本信息）。"""

    eln_id: object
    item_id: object
    server: str
    filename: str
    sha256: str
    size: int


def _strip_html(text) -> str:
    plain = _HTML_TAG_PATTERN.sub("", str(text or ""))
    return _html_unescape(plain).strip()


def _collect_file_fields(node, found) -> None:
    """递归扫描导出数据，收集 ``{hash, filename, size}`` 形状的文件字段。"""
    if isinstance(node, dict):
        if {"hash", "filename", "size"} <= set(node.keys()):
            found.append(node)
            return
        for value in node.values():
            _collect_file_fields(value, found)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _collect_file_fields(value, found)


class MatelabSession:
    """登录态管理：access token 内存缓存，过期自动刷新，刷新失效重新登录。"""

    def __init__(self, credentials: MatelabCredentials, base_url: str, *, timeout: int, opener):
        self.credentials = credentials
        self.base_url = str(base_url).rstrip("/")
        self.timeout = int(timeout)
        self._opener = opener or urllib.request.urlopen
        self._access_token = ""
        self._refresh_token = ""

    # -- 底层请求 ---------------------------------------------------------

    def _open(self, request):
        try:
            return self._opener(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise UpdateError(f"MatElab 更新服务返回 HTTP {exc.code}。") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise UpdateError(f"无法连接 MatElab 更新服务：{reason}") from exc

    def _post(self, path: str, *, form=None, json_payload=None, token: str = "") -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"User-Agent": f"{APP_NAME}/{APP_VERSION}"}
        data = None
        if form is not None:
            data = urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_payload is not None:
            data = json.dumps(json_payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with self._open(request) as response:
            try:
                payload = json.load(response)
            except (TypeError, ValueError, UnicodeDecodeError) as exc:
                raise ReleaseFormatError("MatElab 服务返回了无效的 JSON 数据。") from exc
        if not isinstance(payload, dict):
            raise ReleaseFormatError("MatElab 服务返回了无法识别的数据。")
        return payload

    # -- 登录与刷新 --------------------------------------------------------

    def login(self) -> None:
        payload = self._post(
            "tokens",
            form={
                "username": self.credentials.username,
                "password": self.credentials.password,
            },
        )
        if payload.get("code") != 0:
            raise MatelabAuthError(str(payload.get("msg") or "MatElab 登录失败。"))
        access = payload.get("access") or {}
        refresh = payload.get("refresh") or {}
        self._access_token = str(access.get("token") or "")
        self._refresh_token = str(refresh.get("token") or "")
        if not self._access_token:
            raise MatelabAuthError("MatElab 登录返回中没有可用的访问令牌。")

    def refresh(self) -> None:
        if not self._refresh_token:
            self.login()
            return
        payload = self._post("tokens/tokens_refresh", form={"refresh_token": self._refresh_token})
        if payload.get("code") != 0:
            # 刷新令牌也失效：回到完整登录。
            self.login()
            return
        access = payload.get("access") or {}
        refresh = payload.get("refresh") or {}
        self._access_token = str(access.get("token") or self._access_token)
        self._refresh_token = str(refresh.get("token") or self._refresh_token)

    def token(self) -> str:
        if not self._access_token:
            self.login()
        return self._access_token

    # -- 业务接口 ----------------------------------------------------------

    def api(self, path: str, payload: dict, *, _retried: bool = False) -> dict:
        result = self._post(path, json_payload=payload, token=self.token())
        code = result.get("code")
        if code == 0:
            return result
        if code == "refresh" and not _retried:
            self.refresh()
            return self.api(path, payload, _retried=True)
        if code in (1, "refresh"):
            raise MatelabAuthError("MatElab 登录状态已失效，请重新登录。")
        raise UpdateError(str(result.get("msg") or f"MatElab 接口返回错误（code={code}）。"))


#: 文档要求上传碎片小于 10M（上限 20M）；取 8 MiB 留有余量。
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024


def _multipart_body(fields, file_field: str, filename: str, content: bytes) -> tuple:
    boundary = f"----bandscope-{uuid.uuid4().hex}"
    parts = []
    for key, value in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode(
                "utf-8"
            )
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode("utf-8")
    )
    parts.append(bytes(content))
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return boundary, b"".join(parts)


def upload_file(
    session: MatelabSession,
    path,
    *,
    name: str,
    eln: str = "",
    chunk_size: int = UPLOAD_CHUNK_SIZE,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """按官方大文件机制分片上传，返回服务器反馈（含 id/url/size/hash）。

    发布侧工具使用；``name`` 是后续 ``#file{name}`` 引用的唯一名，不能重复。
    """
    path = Path(path)
    total = path.stat().st_size
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    file_hash = digest.hexdigest()

    uid = uuid.uuid4().hex[:32]
    sent = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk and total:
                break
            last = len(chunk) < chunk_size
            fields = [("uid", uid), ("name", name), ("last", "1" if last else "0")]
            if eln:
                fields.append(("eln", eln))
            if last:
                fields.append(("hash", file_hash))
            # 当前碎片允许在 token 过期后刷新重传一次。
            for attempt in (0, 1):
                boundary, body = _multipart_body(fields, "file", path.name, chunk)
                request = urllib.request.Request(
                    f"{session.base_url}/eln_api/upload",
                    data=body,
                    headers={
                        "Content-Type": f"multipart/form-data; boundary={boundary}",
                        "Authorization": f"Bearer {session.token()}",
                        "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                    },
                    method="POST",
                )
                with session._open(request) as response:
                    try:
                        payload = json.load(response)
                    except (TypeError, ValueError, UnicodeDecodeError) as exc:
                        raise ReleaseFormatError("MatElab 上传接口返回了无效的 JSON 数据。") from exc
                code = payload.get("code")
                if code == "refresh" and attempt == 0:
                    session.refresh()
                    continue
                break
            if code != 0:
                raise UpdateError(str(payload.get("errmsg") or payload.get("msg") or f"MatElab 上传失败（code={code}）。"))
            sent += len(chunk)
            if progress is not None:
                progress(sent, total)
            if last:
                return payload


class MatelabReleaseClient:
    """MatElab 更新源客户端，实现 :class:`~bandscope.updates.sources.UpdateSource` 协议。

    ``credentials_provider`` 是零参 callable，返回 :class:`MatelabCredentials`
    或 ``None``（未登录）。Qt 层在启动检查线程前解析好凭证再传入。
    """

    def __init__(
        self,
        credentials_provider: Callable[[], Optional[MatelabCredentials]],
        notebook: str = MATELAB_RELEASE_NOTEBOOK,
        base_url: str = MATELAB_API_BASE,
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        opener: Callable[..., object] | None = None,
        extra_download_hosts: Tuple[str, ...] = (),
    ):
        self.credentials_provider = credentials_provider
        self.notebook = str(notebook)
        self.base_url = str(base_url).rstrip("/")
        self.timeout = int(timeout)
        self._opener = opener
        #: 信任边界是固定主机集合：API 主机 + 显式配置的课题组服务器主机。
        #: 记录里的 ``server`` 字段只用于拼接下载地址，不会扩大白名单。
        self._extra_hosts = {
            str(host).strip().lower() for host in extra_download_hosts if str(host).strip()
        }
        self._session: Optional[MatelabSession] = None
        # 最近一次检查解析出的文件要素，下载时用来按新 token 重建 URL。
        self._asset_files: dict[str, _FileRef] = {}

    # -- 会话与主机白名单 ---------------------------------------------------

    def _get_session(self) -> MatelabSession:
        credentials = self.credentials_provider()
        if credentials is None or not credentials.username:
            raise MatelabAuthError("尚未登录 MatElab 账号。")
        if self._session is None or self._session.credentials != credentials:
            self._session = MatelabSession(
                credentials, self.base_url, timeout=self.timeout, opener=self._opener
            )
        return self._session

    def _allowed_hosts(self) -> set:
        """固定白名单：API 主机加显式配置的额外下载主机。"""
        hosts = {(urlparse(self.base_url).hostname or "").lower()}
        hosts |= self._extra_hosts
        hosts.discard("")
        return hosts

    def _download_base(self, server: str) -> str:
        """记录本 ``server`` 字段为 ``localhost`` 时用 API 基址，否则按给定主机。"""
        text = str(server or "").strip()
        if not text or text == "localhost":
            return self.base_url
        if "://" not in text:
            text = f"https://{text}"
        return text.rstrip("/")

    # -- 发布记录发现 --------------------------------------------------------

    def _find_notebook(self, session: MatelabSession) -> dict:
        payload = session.api("eln_api/elns", {})
        candidates = list(payload.get("my") or ()) + list(payload.get("share") or ())
        for entry in candidates:
            if str(entry.get("showtext") or "") == self.notebook:
                return entry
        raise UpdateError(f"MatElab 上没有找到发布记录本「{self.notebook}」。")

    def _list_release_items(self, session: MatelabSession, notebook: dict) -> list:
        payload: dict = {"eln": self.notebook}
        if not notebook.get("owner"):
            owner_key = notebook.get("email") or notebook.get("userid")
            if owner_key:
                payload["user"] = owner_key
        result = session.api("eln_api/items", payload)
        items = result.get("items") or []
        releases = []
        for item in items:
            keywords = [str(k) for k in (item.get("keywords") or ())]
            title = str(item.get("title") or "")
            if MATELAB_RELEASE_KEYWORD in keywords or _TITLE_VERSION_PATTERN.search(title):
                releases.append(item)
        if not releases:
            raise UpdateError(f"发布记录本「{self.notebook}」中还没有 BandScope 版本记录。")
        return releases

    def _export_records(self, session: MatelabSession, notebook: dict, items: list) -> list:
        if notebook.get("owner"):
            eln_ref: object = self.notebook
        else:
            eln_ref = {"title": self.notebook, "user": notebook.get("email") or notebook.get("userid")}
        uids = [{"eln": eln_ref, "uid": str(item.get("sn") or "")} for item in items]
        result = session.api("eln_api/export", {"uids": uids})
        return list(result.get("dataset") or ())

    # -- 记录解析 ------------------------------------------------------------

    @staticmethod
    def _manifest(record: dict) -> dict:
        for module in record.get("data") or ():
            if not isinstance(module, dict):
                continue
            if str(module.get("name") or "").strip().lower() != _MANIFEST_FIELD:
                continue
            text = module.get("data")
            if isinstance(text, dict):
                # 表单里名为 manifest 的项。
                text = text.get("data")
            try:
                manifest = json.loads(_strip_html(text))
            except (TypeError, ValueError):
                continue
            if isinstance(manifest, dict):
                return manifest
        return {}

    @staticmethod
    def _notes(record: dict, manifest: dict) -> str:
        notes = str(manifest.get("notes") or "").strip()
        if notes:
            return notes
        for module in record.get("data") or ():
            if isinstance(module, dict) and module.get("type") == "richtext":
                text = _strip_html(module.get("data"))
                if text:
                    return text
        return ""

    def _record_release(self, record: dict, notebook: dict) -> ReleaseInfo | None:
        manifest = self._manifest(record)
        raw_version = str(manifest.get("version") or "")
        if not raw_version:
            match = _TITLE_VERSION_PATTERN.search(str(record.get("title") or ""))
            raw_version = match.group("version") if match else ""
        try:
            version = SemanticVersion.parse(raw_version)
        except ReleaseFormatError:
            return None

        files: list = []
        _collect_file_fields(record.get("data"), files)
        assets = []
        item_id = record.get("id")
        for field in files:
            filename = str(field.get("filename") or "")
            if _INSTALLER_PATTERN.fullmatch(filename) is None:
                continue
            ref = _FileRef(
                eln_id=notebook.get("id"),
                item_id=item_id,
                server=str(notebook.get("server") or ""),
                filename=filename,
                sha256=str(field.get("hash") or ""),
                size=max(0, int(field.get("size") or 0)),
            )
            assets.append(
                ReleaseAsset(
                    name=filename,
                    download_url=self._file_url(ref, self._get_session().token()),
                    size=ref.size,
                    sha256=ref.sha256,
                )
            )
            self._asset_files[filename] = ref

        return ReleaseInfo(
            version=version,
            tag_name=f"v{version}",
            title=str(record.get("title") or f"{APP_NAME} v{version}"),
            notes=self._notes(record, manifest),
            page_url=MATELAB_WEB_URL,
            published_at=str(manifest.get("published_at") or record.get("datetime_modify") or ""),
            assets=tuple(assets),
        )

    # -- 下载 URL -------------------------------------------------------------

    def _file_url(self, ref: _FileRef, token: str) -> str:
        query = urlencode(
            {
                "token": token,
                "eln": ref.eln_id,
                "i": ref.item_id,
                "f": ref.filename,
                "h": ref.sha256,
                "s": ref.size,
                "c": 1,
            }
        )
        return f"{self._download_base(ref.server)}/eln_url/eln?{query}"

    # -- UpdateSource 协议 -----------------------------------------------------

    def fetch_latest_release(self) -> ReleaseInfo:
        session = self._get_session()
        notebook = self._find_notebook(session)
        items = self._list_release_items(session, notebook)
        records = self._export_records(session, notebook, items)
        releases = [
            release
            for release in (self._record_release(record, notebook) for record in records)
            if release is not None
        ]
        if not releases:
            raise ReleaseFormatError("MatElab 发布记录中没有可识别的版本号。")
        return max(releases, key=lambda release: release.version)

    def check_for_update(self, current_version: str, flavor: str) -> UpdateCheckResult:
        current = SemanticVersion.parse(current_version)
        release = self.fetch_latest_release()
        installer = None
        if current < release.version:
            installer = release.installer_for(flavor)
        return UpdateCheckResult(current, release, installer)

    def download_installer(
        self,
        asset: ReleaseAsset,
        destination_dir=None,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> Tuple[Path, str]:
        if not asset.name.lower().endswith(".exe") or Path(asset.name).name != asset.name:
            raise ReleaseFormatError(f"安装包文件名无效：{asset.name!r}")
        if asset.size > MAX_INSTALLER_BYTES:
            raise UpdateError("安装包超过 2 GiB 安全上限，已拒绝下载。")
        expected_digest = str(asset.sha256 or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
            raise DownloadIntegrityError("MatElab 记录未提供该安装包的 SHA-256 摘要，已拒绝自动安装。")

        ref = self._asset_files.get(asset.name)
        if ref is None:
            raise UpdateError("安装包信息已过期，请重新检查更新后再下载。")
        session = self._get_session()
        url = self._file_url(ref, session.token())
        ensure_https_download_url(url, allowed_hosts=self._allowed_hosts())

        target_dir = Path(destination_dir) if destination_dir else update_cache_dir()
        request = urllib.request.Request(
            url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"}
        )
        opener = self._opener or urllib.request.urlopen
        try:
            response = opener(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise UpdateError(f"MatElab 更新服务返回 HTTP {exc.code}。") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise UpdateError(f"无法连接 MatElab 更新服务：{reason}") from exc
        with response:
            final_url = getattr(response, "geturl", lambda: url)()
            ensure_https_download_url(final_url, allowed_hosts=self._allowed_hosts())
            return save_stream(
                response,
                target_dir / asset.name,
                max_bytes=MAX_INSTALLER_BYTES,
                expected_size=asset.size,
                expected_sha256=expected_digest,
                progress=progress,
            )
