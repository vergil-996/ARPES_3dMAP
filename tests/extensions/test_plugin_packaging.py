# -*- coding: utf-8 -*-
"""扩展包打包：同一份源码必须打出同一个摘要。

回归：``build_plugin.py`` 曾用 ``archive.write()`` 打包，zip 条目里带着源文件的
mtime。CI 的 checkout 会把 mtime 设成运行时刻，于是同一份源码每次发布得到不同
摘要；官方目录按「同一 id/version 的内容必须一致」校验，会把这种漂移判成内容被
替换并拒绝生成目录。这个失败发生在标签推上去之后，按仓库约定只能再发一个补丁
版本，所以要有用例把它挡在本地。
"""
import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from bandscope.app_metadata import APP_VERSION
from scripts.release import build_plugin
from tests.support.plugins import DEFAULT_ENTRY_SOURCE, manifest_payload

PLUGIN_ID = "packaging_probe"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReproduciblePackagingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source_root = self.root / "plugins"
        package = self.source_root / PLUGIN_ID
        package.mkdir(parents=True)
        (package / "plugin.json").write_text(
            json.dumps(manifest_payload(PLUGIN_ID, entry_point="entry:Plugin")),
            encoding="utf-8",
        )
        (package / "entry.py").write_text(DEFAULT_ENTRY_SOURCE, encoding="utf-8")
        (package / "controls.py").write_text("VALUE = 1\n" * 40, encoding="utf-8")
        patcher = mock.patch.object(
            build_plugin, "PLUGIN_SOURCE_ROOT", self.source_root
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _build(self, output_name: str, mtime: int) -> Path:
        for path in (self.source_root / PLUGIN_ID).rglob("*"):
            if path.is_file():
                os.utime(path, (mtime, mtime))
        return build_plugin.build(PLUGIN_ID, self.root / output_name, APP_VERSION)

    def test_digest_ignores_source_mtime(self):
        first = self._build("out_a", 1_600_000_000)
        second = self._build("out_b", 1_700_000_000)

        self.assertEqual(_sha256(first), _sha256(second))

    def test_entry_timestamps_are_fixed(self):
        archive = self._build("out_c", 1_600_000_000)

        with zipfile.ZipFile(archive) as handle:
            stamps = {info.date_time for info in handle.infolist()}

        self.assertEqual(stamps, {build_plugin.ZIP_ENTRY_TIMESTAMP})

    def test_sidecar_matches_the_archive(self):
        archive = self._build("out_d", 1_600_000_000)

        sidecar = archive.with_suffix(archive.suffix + ".sha256")
        recorded = sidecar.read_text(encoding="utf-8").split()[0]

        self.assertEqual(recorded, _sha256(archive))


if __name__ == "__main__":
    unittest.main()
