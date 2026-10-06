# -*- coding: utf-8 -*-
"""插件包与官方目录的签名验证（阶段 D）。

信任只来自**应用内置公钥**：不看 ``author`` 文本、不看文件名，也不把普通
SHA-256 sidecar 当成来源证明。仓库与安装包里只有公钥，私钥由维护者保存在
发布环境的 Secret。

签名输入是“用途前缀 + 原始文件字节”，不是重新序列化后的 JSON：验签方与签名方
必须对**完全相同的字节**做运算，否则两边各自 dumps 一遍就会因为空格、键序或
浮点表示不同而验不过。包与目录用不同的用途前缀，防止把目录签名挪去当包签名。

本模块只依赖 ``cryptography``，不导入 Qt / VTK。签名生成在发布脚本里
（``scripts/release/``），这里只负责验证与内置可信密钥的声明。
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from bandscope.extensions.api import PluginError

#: 签名协议版本。宿主只接受自己支持的协议，其它一律拒绝而不是“尽量解释”。
SIGNATURE_PROTOCOL = 1

#: 支持的签名算法标识。
ALGORITHM_ED25519 = "ed25519"

#: 签名用途前缀。包与目录必须不同，避免一种签名被搬到另一种用途上复用。
PURPOSE_PLUGIN = "plugin"
PURPOSE_CATALOG = "plugins-index"

#: 签名 sidecar 的后缀：``X.bsplugin`` -> ``X.bsplugin.sig``。
SIGNATURE_SUFFIX = ".sig"

#: sidecar 最大长度。它只装一个 Base64 签名，超过就是异常输入。
MAX_SIGNATURE_BYTES = 8 * 1024

#: 内置可信公钥：``key_id`` -> Base64 原始 32 字节 Ed25519 公钥。
#:
#: 这里是“哪些发布者算官方”的唯一来源。维护者生成密钥后把公钥填进来并随宿主
#: 更新部署；旧公钥按明确的支持期保留。目录**不能**自行添加可信公钥，未知
#: key_id 一律按未验证处理。
#:
#: 表空表示宿主没有任何内置官方密钥：任何签名包都会被判为“未知密钥”，官方发布
#: 任务也会因缺少匹配密钥而失败。表非空时，只有这里列出的 key_id 才算官方来源。
#:
#: ``bandscope-official-2026`` 自 v1.12.1 起内置。私钥在维护者受控位置与发布环境
#: Secret（``PLUGIN_SIGNING_KEY``）里，**不在仓库中**；轮换时新公钥要等宿主更新
#: 部署到位后才能签发新包，旧公钥按支持期继续保留。
TRUSTED_PLUGIN_KEYS: Dict[str, str] = {
    "bandscope-official-2026": "rkNcjzaNQPwzKDwvM2ykL+tbb5/tqDPtq0Y2jKOHtUA=",
}


class TrustError(PluginError):
    """签名缺失、格式错误、密钥未知或验签失败。"""


@dataclass(frozen=True)
class SignatureEnvelope:
    """一份签名 sidecar 的解析结果。"""

    protocol: int
    key_id: str
    algorithm: str
    signature: bytes

    def to_payload(self) -> dict:
        return {
            "protocol": int(self.protocol),
            "key_id": str(self.key_id),
            "algorithm": str(self.algorithm),
            "signature": base64.b64encode(self.signature).decode("ascii"),
        }


def encode_public_key(public_key: Ed25519PublicKey) -> str:
    """内置密钥的存储形式：Base64 原始 32 字节。"""
    from cryptography.hazmat.primitives import serialization

    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def signature_payload(purpose: str, data: bytes, *, protocol: int = SIGNATURE_PROTOCOL) -> bytes:
    """签名与验签共用的字节序列。

    ``purpose`` 参与前缀，协议版本也参与：换协议时旧签名自然失效，不需要靠
    人工记得改代码路径。
    """
    prefix = f"bandscope/{purpose}/v{int(protocol)}\x00".encode("ascii")
    return prefix + bytes(data)


def parse_signature(payload) -> SignatureEnvelope:
    """解析 sidecar 内容；任何结构问题都转成可展示的 :class:`TrustError`。"""
    if isinstance(payload, (bytes, bytearray)):
        try:
            text = bytes(payload).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TrustError("签名文件不是有效的 UTF-8 文本。") from exc
    else:
        text = str(payload)
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise TrustError("签名文件不是有效的 JSON。") from exc
    if not isinstance(raw, Mapping):
        raise TrustError("签名文件顶层必须是对象。")

    try:
        protocol = int(raw.get("protocol"))
    except (TypeError, ValueError) as exc:
        raise TrustError("签名文件缺少有效的 protocol。") from exc
    if protocol != SIGNATURE_PROTOCOL:
        raise TrustError(
            f"签名协议版本 {protocol} 不受支持（当前支持 {SIGNATURE_PROTOCOL}）。"
        )

    algorithm = str(raw.get("algorithm") or "").strip().lower()
    if algorithm != ALGORITHM_ED25519:
        raise TrustError(f"不支持的签名算法：{raw.get('algorithm')!r}。")

    key_id = str(raw.get("key_id") or "").strip()
    if not key_id:
        raise TrustError("签名文件缺少 key_id。")

    encoded = str(raw.get("signature") or "").strip()
    if not encoded:
        raise TrustError("签名文件缺少 signature。")
    try:
        signature = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise TrustError("签名内容不是有效的 Base64。") from exc

    return SignatureEnvelope(protocol, key_id, algorithm, signature)


def load_signature(path) -> SignatureEnvelope:
    target = Path(path)
    try:
        payload = target.read_bytes()
    except OSError as exc:
        raise TrustError(f"无法读取签名文件：{exc}") from exc
    if len(payload) > MAX_SIGNATURE_BYTES:
        raise TrustError("签名文件超出合理大小，已拒绝。")
    return parse_signature(payload)


def sidecar_path(package_path) -> Path:
    """包旁的签名文件路径：``BandScope-x-1.0.0.bsplugin.sig``。"""
    return Path(str(package_path) + SIGNATURE_SUFFIX)


def verify_signature(
    data: bytes,
    envelope: SignatureEnvelope,
    *,
    purpose: str,
    trusted_keys: Optional[Mapping[str, str]] = None,
    protocol: int = SIGNATURE_PROTOCOL,
) -> str:
    """用内置公钥验证 ``data``；成功返回 ``key_id``，失败抛 :class:`TrustError`。

    未知 key_id 直接拒绝，绝不“降级为未签名包继续”。
    """
    keys = TRUSTED_PLUGIN_KEYS if trusted_keys is None else trusted_keys
    if int(envelope.protocol) != int(protocol):
        raise TrustError("签名协议版本与当前宿主不一致。")
    encoded = keys.get(envelope.key_id)
    if not encoded:
        raise TrustError(
            f"签名使用未知密钥 {envelope.key_id}；宿主内置密钥中没有它，无法确认来源。"
        )
    try:
        raw_key = base64.b64decode(str(encoded).strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise TrustError(f"内置公钥 {envelope.key_id} 不是有效的 Base64。") from exc
    try:
        public_key = Ed25519PublicKey.from_public_bytes(raw_key)
    except ValueError as exc:
        raise TrustError(f"内置公钥 {envelope.key_id} 不是有效的 Ed25519 公钥。") from exc
    try:
        public_key.verify(
            envelope.signature,
            signature_payload(purpose, data, protocol=envelope.protocol),
        )
    except InvalidSignature as exc:
        raise TrustError("签名校验失败：内容与签名不匹配，文件可能已损坏或被篡改。") from exc
    return envelope.key_id


def build_signature(
    private_key,
    data: bytes,
    *,
    purpose: str,
    key_id: str,
    protocol: int = SIGNATURE_PROTOCOL,
) -> SignatureEnvelope:
    """用私钥为 ``data`` 生成签名 sidecar 内容。

    签名与验签共用 :func:`signature_payload`：两边各自拼一遍前缀迟早会不一致，
    这里让它们物理上只有一份实现。发布脚本与测试都走这个函数。
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not isinstance(private_key, Ed25519PrivateKey):
        raise TrustError("签名私钥必须是 Ed25519 私钥。")
    identifier = str(key_id or "").strip()
    if not identifier:
        raise TrustError("签名必须带 key_id。")
    signature = private_key.sign(signature_payload(purpose, data, protocol=protocol))
    return SignatureEnvelope(protocol, identifier, ALGORITHM_ED25519, signature)


def write_signature_sidecar(target, envelope: SignatureEnvelope):
    """把签名写到 ``<target>.sig``；返回写出的路径。"""
    path = sidecar_path(target)
    payload = json.dumps(envelope.to_payload(), ensure_ascii=False, indent=2)
    path.write_text(payload + "\n", encoding="utf-8")
    return path


def verify_file(path, *, purpose: str, trusted_keys: Optional[Mapping[str, str]] = None) -> str:
    """验证 ``path`` 与其 ``.sig`` sidecar；返回签名使用的 key_id。"""
    target = Path(path)
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise TrustError(f"无法读取待验证文件：{exc}") from exc
    envelope = load_signature(sidecar_path(target))
    return verify_signature(data, envelope, purpose=purpose, trusted_keys=trusted_keys)


# ---------------------------------------------------------------------------
# 来源标签（界面展示与登记表共用）
# ---------------------------------------------------------------------------

#: 官方已验证：签名由内置公钥验证通过。
SOURCE_OFFICIAL = "official"
#: 本地未验证：用户明确确认后导入的未签名包。
SOURCE_LOCAL = "local"
#: 历史未验证：从登记表 v1 迁移过来的旧安装，不能因此获得官方身份。
SOURCE_LEGACY = "legacy"
#: 有签名但验不过：拒绝安装，不允许降级成未签名包。
SOURCE_INVALID = "invalid"


def describe_source(source: Optional[Mapping]) -> str:
    """把登记表里的来源信息翻译成用户能看懂的一句话。"""
    data = dict(source or {})
    kind = str(data.get("kind") or SOURCE_LOCAL)
    if kind == SOURCE_OFFICIAL and data.get("verified"):
        key_id = str(data.get("key_id") or "")
        return f"官方已验证（{key_id}）" if key_id else "官方已验证"
    if kind == SOURCE_LEGACY:
        return "历史未验证（迁移自旧版本安装）"
    if data.get("accepted_unverified") or data.get("verified"):
        return "本地未验证（已确认）"
    return "本地未验证"


@dataclass(frozen=True)
class PackageSource:
    """本地包旁的签名检查结果（不导入任何插件代码）。"""

    kind: str
    key_id: str = ""
    reason: str = ""
    package_sha256: str = ""

    @property
    def verified(self) -> bool:
        return self.kind == SOURCE_OFFICIAL

    @property
    def rejected(self) -> bool:
        """有签名但验不过：必须拒绝安装，不允许降级成未签名包。"""
        return self.kind == SOURCE_INVALID


def inspect_package_source(
    path, *, trusted_keys: Optional[Mapping[str, str]] = None
) -> PackageSource:
    """查看包旁边是否有可验证的官方签名。

    - 没有 ``.sig``：``kind=local``（未验证），由界面询问用户后再决定是否安装；
    - 有 ``.sig`` 且验签通过：``kind=official``；
    - 有 ``.sig`` 但损坏、密钥未知或验不过：``kind=invalid`` 并带 ``reason``，
      调用方必须拒绝，不能自动降级。
    """
    target = Path(path)
    signature_file = sidecar_path(target)
    if not signature_file.is_file():
        return PackageSource(kind=SOURCE_LOCAL)
    try:
        key_id = verify_file(target, purpose=PURPOSE_PLUGIN, trusted_keys=trusted_keys)
    except TrustError as exc:
        return PackageSource(kind=SOURCE_INVALID, reason=str(exc))
    return PackageSource(kind=SOURCE_OFFICIAL, key_id=key_id)
