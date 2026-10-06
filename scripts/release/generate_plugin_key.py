# -*- coding: utf-8 -*-
"""生成一对插件签名密钥，并打印要填进宿主的公钥条目（阶段 D）。

维护者本地运行一次：私钥写进受控文件（或直接作为发布 Secret 保存），公钥填到
``bandscope/extensions/trust.py`` 的 ``TRUSTED_PLUGIN_KEYS`` 并随宿主发布。

用法::

    python scripts/release/generate_plugin_key.py --key-id bandscope-official-2026 \\
        --output .local/maintainer/plugin-signing-key.pem

    # 只打印公钥条目，不落盘（公钥不是秘密）
    python scripts/release/generate_plugin_key.py --key-id xxx --stdout-only

私钥一旦丢失就无法再签出客户端认可的包；轮换时新公钥要等宿主更新到位，旧公钥
按支持期保留。这个脚本只用于生成，**不**参与构建，也不接入 CI。
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from bandscope.extensions.trust import encode_public_key  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成插件签名密钥对")
    parser.add_argument("--key-id", required=True, help="密钥标识，会写进每个签名 sidecar")
    parser.add_argument(
        "--output",
        default=None,
        help="私钥输出路径（PKCS#8 PEM）；不指定则只打印，不写文件",
    )
    parser.add_argument(
        "--stdout-only",
        action="store_true",
        help="即使给了 --output 也不写私钥文件",
    )
    args = parser.parse_args(argv)

    key_id = str(args.key_id).strip()
    if not key_id:
        print("key_id 不能为空。", file=sys.stderr)
        return 2

    private_key = Ed25519PrivateKey.generate()
    public_key = encode_public_key(private_key.public_key())

    written = None
    if args.output and not args.stdout_only:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        written = destination

    seed = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )

    print("# 公钥：填进 bandscope/extensions/trust.py 的 TRUSTED_PLUGIN_KEYS")
    print(f'    "{key_id}": "{public_key}",')
    print()
    print("# 发布 Secret（二选一）：")
    print(f"#   {key_id}: BANDSCOPE_PLUGIN_SIGNING_KEY_ID={key_id}")
    print(f"#   BANDSCOPE_PLUGIN_SIGNING_KEY={base64.b64encode(seed).decode('ascii')}")
    if written is not None:
        print(f"\n私钥已写入 {written}（不要提交到仓库）。")
    else:
        print("\n未写出私钥文件；请立即把上面的 Base64 保存到发布环境 Secret。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
