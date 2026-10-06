# -*- coding: utf-8 -*-
"""发布侧的签名材料处理（阶段 D）。

私钥只存在于发布环境的 Secret 或维护者本地文件，**不进仓库、不进安装包**。
公钥随宿主发布，写在 ``bandscope/extensions/trust.py`` 的 ``TRUSTED_PLUGIN_KEYS``
里；发布前必须确认“用来签名的私钥”与“仓库内置的公钥”是同一对，否则用户装到
的包在客户端一定会被拒，而那时已经发出去了。

支持两种私钥形态：

- ``-----BEGIN PRIVATE KEY-----`` 开头的 PKCS#8 PEM 文本；
- Base64 编码的裸 32 字节 Ed25519 种子（便于放进 Secret），或 Base64 的
  PKCS#8 DER。
"""
from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
from typing import Dict, Mapping, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bandscope.extensions.trust import TRUSTED_PLUGIN_KEYS, encode_public_key

#: 私钥环境变量（发布 Secret）。
KEY_ENV = "BANDSCOPE_PLUGIN_SIGNING_KEY"
#: 密钥标识环境变量；写进签名 sidecar，宿主据此查内置公钥。
KEY_ID_ENV = "BANDSCOPE_PLUGIN_SIGNING_KEY_ID"


class SigningKeyError(Exception):
    """密钥缺失、格式错误或与内置公钥不匹配。"""


def load_private_key(value) -> Ed25519PrivateKey:
    """把 Secret / 文件内容解析成 Ed25519 私钥。"""
    text = str(value or "").strip()
    if not text:
        raise SigningKeyError(
            f"没有签名私钥。请在发布环境配置 {KEY_ENV}（Base64 的 Ed25519 私钥）"
            f"或使用 --key-file 指定私钥文件。"
        )
    if text.startswith("-----BEGIN"):
        try:
            key = serialization.load_pem_private_key(text.encode("utf-8"), password=None)
        except (ValueError, TypeError) as exc:
            raise SigningKeyError(f"私钥 PEM 无法解析：{exc}") from exc
    else:
        try:
            raw = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SigningKeyError("私钥不是有效的 Base64 或 PEM。") from exc
        try:
            if len(raw) == 32:
                key = Ed25519PrivateKey.from_private_bytes(raw)
            else:
                key = serialization.load_der_private_key(raw, password=None)
        except (ValueError, TypeError) as exc:
            raise SigningKeyError(f"私钥无法解析：{exc}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise SigningKeyError("签名私钥必须是 Ed25519 私钥。")
    return key


def resolve_key_id(value: Optional[str] = None, trusted: Optional[Mapping[str, str]] = None) -> str:
    """确定密钥标识：显式给出优先，否则在只有一个内置公钥时取它。"""
    text = str(value or "").strip()
    if text:
        return text
    keys = TRUSTED_PLUGIN_KEYS if trusted is None else trusted
    if len(keys) == 1:
        return next(iter(keys))
    raise SigningKeyError(
        f"没有指定 key_id。请设置 {KEY_ID_ENV} 或传入 --key-id。"
    )


def check_trusted(
    private_key: Ed25519PrivateKey,
    key_id: str,
    *,
    trusted: Optional[Mapping[str, str]] = None,
) -> str:
    """确认私钥对应的公钥就是宿主内置的那一把；返回公钥的 Base64。

    “签名密钥与内置公钥匹配”是正式发布的硬性前置条件：不匹配时这里直接失败，
    而不是先发一个客户端必然拒收的包。
    """
    keys: Dict[str, str] = dict(TRUSTED_PLUGIN_KEYS if trusted is None else trusted)
    public = encode_public_key(private_key.public_key())
    if not keys:
        raise SigningKeyError(
            "宿主还没有内置任何官方公钥，无法发布签名的官方插件。\n"
            "请先生成密钥（scripts/release/generate_plugin_key.py），把公钥填进\n"
            "bandscope/extensions/trust.py 的 TRUSTED_PLUGIN_KEYS，再配置发布 Secret。"
        )
    expected = keys.get(key_id)
    if not expected:
        known = "、".join(sorted(keys)) or "（无）"
        raise SigningKeyError(
            f"key_id「{key_id}」不在宿主内置公钥里（已有：{known}）。"
        )
    if str(expected).strip() != public:
        raise SigningKeyError(
            f"签名私钥与宿主内置公钥不匹配（key_id={key_id}）。\n"
            f"内置公钥：{expected}\n"
            f"私钥导出：{public}\n"
            "请确认 Secret 与仓库里的公钥是同一对；不匹配时客户端会拒绝所有包。"
        )
    return public


def load_key_file(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SigningKeyError(f"无法读取私钥文件：{exc}") from exc


def key_material(key_file: Optional[str] = None) -> str:
    """从显式文件或环境变量取私钥材料。"""
    if key_file:
        return load_key_file(key_file)
    return os.environ.get(KEY_ENV, "")


def key_id_material(explicit: Optional[str] = None) -> str:
    return str(explicit or os.environ.get(KEY_ID_ENV, "") or "").strip()
