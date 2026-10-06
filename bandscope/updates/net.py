# -*- coding: utf-8 -*-
"""更新服务与插件目录共用的 HTTPS 取件原语。

两条下载路径接受的文件完全不同——更新服务只收 ``.exe`` 安装器，插件目录要收
JSON 目录和 ``.bsplugin`` 包——所以这里**只**抽真正共用的部分：HTTPS + 主机
白名单、超时、分块读取、进度回调、取消判定、大小与 SHA-256 校验、以及“先写
``.part`` 再原子替换”的落盘方式。接受什么文件、多大上限，由调用方决定。

本模块不导入 Qt，也不导入 ``update_service``；错误类型自成一层，调用方按需
包装成自己的用户可读异常。
"""
from __future__ import annotations

import hashlib
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.parse import urlparse

DEFAULT_TIMEOUT_SECONDS = 20
DOWNLOAD_CHUNK_SIZE = 1024 * 1024

#: 允许的下载主机。请求 URL 与重定向后的最终 URL 都要过这一关。
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "www.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class NetworkError(RuntimeError):
    """可展示的网络或下载失败。"""


class UntrustedHostError(NetworkError):
    """下载地址不是受信任的 HTTPS 主机。"""


class DownloadIntegrityError(NetworkError):
    """下载内容的大小或摘要与声明不符。"""


def normalize_sha256(value) -> str:
    """接受 ``sha256:<hex>`` 或裸 hex，返回小写 64 位十六进制；无效返回空串。"""
    digest = str(value or "").strip().lower()
    if digest.startswith("sha256:"):
        digest = digest.partition(":")[2]
    return digest if _SHA256_PATTERN.fullmatch(digest) else ""


def ensure_https_download_url(url: str) -> None:
    """下载地址必须是受信任主机的 HTTPS 地址；否则拒绝。"""
    parsed = urlparse(str(url or ""))
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() not in ALLOWED_DOWNLOAD_HOSTS:
        raise UntrustedHostError(f"资源使用了不受信任的下载地址：{url}")


def request(url: str, *, accept: str = "", user_agent: str = "") -> urllib.request.Request:
    """构造一个带默认请求头的 GET 请求。"""
    headers = {}
    if accept:
        headers["Accept"] = accept
    if user_agent:
        headers["User-Agent"] = user_agent
    return urllib.request.Request(str(url), headers=headers)


def open_stream(
    url: str,
    *,
    opener: Optional[Callable[..., object]] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    accept: str = "",
    user_agent: str = "",
):
    """打开一个受信任主机的 HTTPS 流；网络失败转成可展示的 :class:`NetworkError`。"""
    ensure_https_download_url(url)
    open_function = opener or urllib.request.urlopen
    try:
        return open_function(request(url, accept=accept, user_agent=user_agent), timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NetworkError("远端还没有发布该资源。") from exc
        raise NetworkError(f"下载服务返回 HTTP {exc.code}。") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise NetworkError(f"无法连接下载服务：{reason}") from exc


def save_stream(
    stream,
    target: Path,
    *,
    max_bytes: int = 0,
    expected_size: int = 0,
    expected_sha256: str = "",
    progress: Optional[Callable[[int, int], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    chunk_size: int = DOWNLOAD_CHUNK_SIZE,
) -> Tuple[Path, str]:
    """把响应流写入 ``target``，校验通过后才落盘；返回 ``(路径, 实际摘要)``。

    过程中先写 ``<target>.part``；任何失败（含取消）都会删掉半截文件，绝不留下
    一个看起来完整、实际截断的产物。
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    partial.unlink(missing_ok=True)
    expected_digest = normalize_sha256(expected_sha256)

    received = 0
    digest = hashlib.sha256()
    try:
        with partial.open("wb") as output:
            while True:
                if cancelled is not None and cancelled():
                    raise NetworkError("下载已取消。")
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                received += len(chunk)
                if max_bytes and received > max_bytes:
                    raise NetworkError(f"下载内容超过安全上限（{max_bytes} 字节），已拒绝。")
                digest.update(chunk)
                output.write(chunk)
                if progress is not None:
                    progress(received, int(expected_size or 0))
        actual = digest.hexdigest()
        if expected_size and received != int(expected_size):
            raise DownloadIntegrityError(
                f"下载大小不完整：预期 {int(expected_size)} 字节，实际 {received} 字节。"
            )
        if expected_digest and actual != expected_digest:
            raise DownloadIntegrityError("SHA-256 校验失败，文件可能损坏或已被篡改。")
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return target, actual
