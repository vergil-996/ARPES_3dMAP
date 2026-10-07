# -*- coding: utf-8 -*-
"""阶段 E：主程序升级前的插件影响评估。

评估只读清单、不导入插件代码；目标宿主与接口集合取自目录声明，而不是当前宿主。
"""
from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from bandscope.app_metadata import APP_VERSION
from bandscope.extensions.catalog import parse_catalog
from bandscope.extensions.compat import SUPPORTED_API_VERSIONS
from bandscope.extensions.plugin_manager import PluginManager, install_package
from bandscope.extensions.upgrade import (
    IMPACT_OK,
    IMPACT_PAUSED,
    IMPACT_UNKNOWN,
    IMPACT_UPDATE,
    evaluate_upgrade,
    format_impact,
)
from tests.support.plugins import (
    PLUGIN_ID,
    SECOND_PLUGIN_ID,
    install_fake,
    make_plugin_archive,
    manifest_payload,
    synthetic_source,
)


def catalog_with(entries, *, revision=1, host_version="1.12.0", api_versions=None):
    import json

    # 默认让“目标宿主”与当前宿主支持同一组接口版本：这样默认目录里的插件
    # 只要对当前宿主兼容，对目标宿主也兼容。需要更窄集合的用例显式传入。
    if api_versions is None:
        api_versions = tuple(sorted(SUPPORTED_API_VERSIONS))
    return parse_catalog(
        json.dumps(
            {
                "schema": 1,
                "revision": revision,
                "generated_at": "2026-10-06T00:00:00+00:00",
                "host": {"app_version": host_version, "api_versions": list(api_versions)},
                "plugins": entries,
            },
            ensure_ascii=False,
        )
    )


def catalog_entry(version="1.0.1", *, requires_app=APP_VERSION, api_version=1, sha="a" * 64):
    name = f"BandScope-{PLUGIN_ID}-{version}.bsplugin"
    return {
        "id": PLUGIN_ID,
        "name": "平带增强",
        "version": version,
        "requires_app": requires_app,
        "api_version": api_version,
        "capabilities": ["opacity_multiplier"],
        "package": {
            "name": name,
            "url": f"https://github.com/vergil-996/ARPES_3dMAP/releases/download/v1.12.0/{name}",
            "size": 1024,
            "sha256": sha,
        },
    }


class UpgradeEvaluationTests(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.root = Path(self._root.name) / "extensions"

    def manager(self, *, app_version=APP_VERSION):
        manager = PluginManager(app_version=app_version, root=self.root)
        self.addCleanup(manager.shutdown)
        return manager

    def test_missing_catalog_means_unknown(self):
        install_fake(self.root)
        manager = self.manager()
        manager.startup()
        report = evaluate_upgrade(manager, None, target_version="1.12.0")
        self.assertFalse(report.catalog_available)
        self.assertTrue(report.has_blocking)
        self.assertEqual([item.category for item in report.items], [IMPACT_UNKNOWN])
        self.assertIn("无法确认", report.items[0].reason)

    def test_compatible_plugin_can_continue(self):
        install_fake(self.root)
        manager = self.manager()
        manager.startup()
        catalog = catalog_with([catalog_entry(version="1.0.0")])
        report = evaluate_upgrade(manager, catalog, target_version=APP_VERSION)
        self.assertEqual([item.category for item in report.items], [IMPACT_OK])
        self.assertFalse(report.has_blocking)

    def test_incompatible_with_the_target_host_is_paused(self):
        # 已装版本能跑在当前宿主上（所以装得进去），但目标宿主超出它的声明范围。
        install_fake(self.root, requires_app=f">={APP_VERSION},<2.0.0")
        manager = self.manager()
        manager.startup()
        catalog = catalog_with([catalog_entry(version="1.0.0")])
        report = evaluate_upgrade(manager, catalog, target_version="2.0.0")
        self.assertEqual([item.category for item in report.items], [IMPACT_PAUSED])
        self.assertTrue(report.has_blocking)
        self.assertIn("2.0.0", report.items[0].reason)

    def test_target_api_set_not_current_api(self):
        # 目标宿主只提供接口 2，声明接口 1 的插件就算暂停加载。
        install_fake(self.root, api_version=1)
        manager = self.manager()
        manager.startup()
        catalog = catalog_with([catalog_entry(version="1.0.0")], api_versions=(2,))
        report = evaluate_upgrade(manager, catalog, target_version=APP_VERSION)
        self.assertEqual(report.items[0].category, IMPACT_PAUSED)

    def test_matching_update_is_reported(self):
        install_fake(self.root)
        manager = self.manager()
        manager.startup()
        catalog = catalog_with([catalog_entry(version="1.0.1")])
        report = evaluate_upgrade(manager, catalog, target_version=APP_VERSION)
        self.assertEqual(report.items[0].category, IMPACT_UPDATE)
        self.assertEqual(report.items[0].target_version, "1.0.1")

    def test_disabled_and_pending_removal_are_skipped(self):
        install_fake(self.root)
        install_fake(self.root, SECOND_PLUGIN_ID)
        manager = self.manager()
        manager.startup()
        manager.set_enabled(SECOND_PLUGIN_ID, False)
        manager.note_removal(PLUGIN_ID)
        catalog = catalog_with([catalog_entry(version="1.0.1")])
        report = evaluate_upgrade(manager, catalog, target_version=APP_VERSION)
        self.assertEqual(report.items, ())

    def test_evaluation_never_imports_plugin_code(self):
        # 入口代码一导入就抛异常：评估仍然必须给出结论，因为升级评估不执行它。
        archive = make_plugin_archive(
            self.root / "staging" / "broken.bsplugin",
            manifest=manifest_payload(),
            entry_source="raise RuntimeError('这个入口一旦被导入就会抛异常')\n",
        )
        install_package(archive, root=self.root, source=synthetic_source())
        manager = self.manager()
        manager.startup()  # 加载失败，记录里没有清单，但内容还在
        self.assertFalse(manager.record(PLUGIN_ID).ready)

        catalog = catalog_with([catalog_entry(version="1.0.1")])
        report = evaluate_upgrade(manager, catalog, target_version=APP_VERSION)
        self.assertEqual(len(report.items), 1)

    def test_summary_lines_group_by_category(self):
        install_fake(self.root)
        install_fake(self.root, SECOND_PLUGIN_ID)
        manager = self.manager()
        manager.startup()
        catalog = catalog_with([catalog_entry(version="1.0.1")])
        report = evaluate_upgrade(manager, catalog, target_version=APP_VERSION)
        text = format_impact(report)
        self.assertIn("存在匹配更新", text)
        self.assertIn("可继续使用", text)

    def test_empty_report_text(self):
        report = evaluate_upgrade(_EmptyManager(), None, target_version="1.12.0")
        self.assertEqual(report.items, ())
        self.assertIn("没有已启用的插件", format_impact(report))


class _EmptyManager:
    def plugins(self):
        return []

    def installed_manifest(self, record):
        return None


if __name__ == "__main__":
    unittest.main()
