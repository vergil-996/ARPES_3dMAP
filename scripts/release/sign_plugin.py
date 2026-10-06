# -*- coding: utf-8 -*-
"""为插件包或官方目录写签名 sidecar（阶段 D）。

用法::

    # 正式发布：从 Secret 取私钥，先确认与内置公钥匹配，再逐个签名
    python scripts/release/sign_plugin.py release/*.bsplugin

    # 只做密钥检查（发布前置门禁，缺 Secret 时让发布任务失败）
    python scripts/release/sign_plugin.py --check-only

    # 本地用私钥文件试签
    python scripts/release/sign_plugin.py release/x.bsplugin --key-file key.pem --key-id my-key

    # 目录用不同的用途前缀
    python scripts/release/sign_plugin.py plugins-index.json --purpose catalog

签名输入是“用途前缀 + 原始文件字节”。同一份字节在客户端会用内置公钥再验一次，
所以这里不做任何重新序列化。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bandscope.extensions.trust import (  # noqa: E402
    PURPOSE_CATALOG,
    PURPOSE_PLUGIN,
    build_signature,
    sidecar_path,
    write_signature_sidecar,
)
from scripts.release.plugin_signing import (  # noqa: E402
    KEY_ENV,
    KEY_ID_ENV,
    SigningKeyError,
    check_trusted,
    key_id_material,
    key_material,
    load_private_key,
    resolve_key_id,
)

PURPOSES = {"plugin": PURPOSE_PLUGIN, "catalog": PURPOSE_CATALOG}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="为插件发布产物写签名 sidecar")
    parser.add_argument("paths", nargs="*", help="待签名的文件（目录请用 --purpose catalog）")
    parser.add_argument(
        "--purpose",
        choices=sorted(PURPOSES),
        default="plugin",
        help="签名用途前缀：plugin（默认）或 catalog",
    )
    parser.add_argument("--key-file", default=None, help="私钥文件（PEM 或 Base64）")
    parser.add_argument("--key-id", default=None, help=f"密钥标识（默认取 {KEY_ID_ENV}）")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="只检查密钥已配置且与内置公钥匹配，不写任何文件",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        private_key = load_private_key(key_material(args.key_file))
        key_id = resolve_key_id(key_id_material(args.key_id))
        public_key = check_trusted(private_key, key_id)
    except SigningKeyError as exc:
        print(f"签名密钥不可用：{exc}", file=sys.stderr)
        return 1

    print(f"签名密钥 {key_id} 与宿主内置公钥匹配。")
    if args.check_only:
        if args.paths:
            print("--check-only 忽略传入的文件参数。")
        return 0

    if not args.paths:
        print("没有要签名的文件。", file=sys.stderr)
        return 2

    purpose = PURPOSES[args.purpose]
    for raw in args.paths:
        target = Path(raw)
        if not target.is_file():
            print(f"找不到文件：{target}", file=sys.stderr)
            return 1
        envelope = build_signature(
            private_key,
            target.read_bytes(),
            purpose=purpose,
            key_id=key_id,
        )
        written = write_signature_sidecar(target, envelope)
        print(f"已签名 {target.name} -> {written.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
