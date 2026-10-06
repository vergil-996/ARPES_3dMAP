# -*- coding: utf-8 -*-
"""阶段 C：统一兼容规则的判定表。

同一个声明在安装、加载、构建脚本与目录评估里必须给出同一个结论和同一句原因，
所以这里既测纯函数，也测“安装事务真的用上了这套结论”。
"""
from __future__ import annotations

import unittest

from packaging.version import Version

from bandscope.extensions.api import (
    PluginCompatibilityError,
    PluginManifest,
)
from bandscope.extensions.compat import (
    SUPPORTED_API_VERSIONS,
    SUPPORTED_CAPABILITIES,
    AppRequirement,
    compare_versions,
    evaluate_compatibility,
    is_prerelease,
    parse_requires_app,
    sortable_version,
)


def manifest_payload(**overrides) -> dict:
    payload = {
        "id": "sample",
        "name": "示例插件",
        "version": "1.0.0",
        "api_version": 1,
        "requires_app": "1.11.2",
        "entry_point": "entry:Plugin",
        "capabilities": ["opacity_multiplier"],
    }
    payload.update(overrides)
    return payload


def verdict(**overrides):
    payload = {
        "name": "示例插件",
        "requires_app": "1.11.2",
        "api_version": 1,
        "capabilities": (),
        "app_version": "1.11.2",
    }
    payload.update(overrides)
    return evaluate_compatibility(**payload)


class ParseRequiresAppTests(unittest.TestCase):
    def test_bare_version_is_exact(self):
        requirement = parse_requires_app("1.11.2")
        self.assertTrue(requirement.valid)
        self.assertTrue(requirement.exact)
        self.assertEqual(requirement.version, Version("1.11.2"))
        self.assertTrue(requirement.accepts("1.11.2"))
        self.assertFalse(requirement.accepts("1.11.3"))
        self.assertFalse(requirement.accepts("1.12.0"))

    def test_leading_v_is_still_an_exact_match(self):
        # PEP 440 允许 v 前缀；旧清单里出现过，不能因为换了解析器就判成非法。
        requirement = parse_requires_app("v1.11.2")
        self.assertTrue(requirement.valid)
        self.assertTrue(requirement.accepts("1.11.2"))

    def test_range_operators(self):
        requirement = parse_requires_app(">=1.10.0,<1.11.0")
        self.assertTrue(requirement.valid)
        self.assertFalse(requirement.exact)
        self.assertTrue(requirement.accepts("1.10.0"))  # 下边界包含
        self.assertTrue(requirement.accepts("1.10.9"))
        self.assertFalse(requirement.accepts("1.11.0"))  # 上边界排除
        self.assertFalse(requirement.accepts("1.9.9"))

    def test_exclusion_range(self):
        requirement = parse_requires_app(">=1.10,!=1.10.5")
        self.assertTrue(requirement.accepts("1.10.4"))
        self.assertFalse(requirement.accepts("1.10.5"))
        self.assertTrue(requirement.accepts("1.10.6"))

    def test_invalid_declarations_are_reported_not_guessed(self):
        for text in ("", ">=abc", "1.x", "not a version", ">=1.0,<"):
            with self.subTest(text=text):
                requirement = parse_requires_app(text)
                self.assertFalse(requirement.valid)
                self.assertTrue(requirement.error)
                self.assertFalse(requirement.accepts("1.11.2"))

    def test_blank_declaration_is_invalid(self):
        self.assertIsInstance(parse_requires_app(None), AppRequirement)
        self.assertFalse(parse_requires_app(None).valid)


class EvaluateCompatibilityTests(unittest.TestCase):
    def test_exact_old_declaration_keeps_working(self):
        self.assertTrue(verdict().ok)

    def test_range_accepted_inside_verified_window(self):
        self.assertTrue(verdict(requires_app=">=1.10.0,<1.12.0").ok)

    def test_out_of_range_reports_declared_range_and_current(self):
        result = verdict(requires_app=">=1.10.0,<1.11.0")
        self.assertFalse(result.ok)
        self.assertFalse(result.app_ok)
        self.assertIn(">=1.10.0,<1.11.0", result.reason)
        self.assertIn("1.11.2", result.reason)

    def test_invalid_declaration_is_incompatible_with_readable_reason(self):
        result = verdict(requires_app="1.x")
        self.assertFalse(result.ok)
        self.assertFalse(result.app_ok)
        self.assertIn("无法解析", result.reason)

    def test_unknown_api_version_rejected_even_when_range_matches(self):
        result = verdict(api_version=99, requires_app=">=1.0")
        self.assertFalse(result.ok)
        self.assertFalse(result.api_ok)
        self.assertIn("99", result.reason)

    def test_supported_api_set_is_explicit(self):
        self.assertIn(1, SUPPORTED_API_VERSIONS)
        for api_version in SUPPORTED_API_VERSIONS:
            self.assertTrue(verdict(api_version=api_version).ok)

    def test_missing_capability_rejected(self):
        result = verdict(capabilities=["opacity_multiplier", "time_travel"])
        self.assertFalse(result.ok)
        self.assertFalse(result.capabilities_ok)
        self.assertEqual(result.missing_capabilities, ("time_travel",))
        self.assertIn("time_travel", result.reason)

    def test_supported_capability_accepted(self):
        for capability in SUPPORTED_CAPABILITIES:
            self.assertTrue(verdict(capabilities=[capability]).ok)

    def test_api_failure_wins_over_range_failure(self):
        # 判定顺序固定：接口版本 → 主程序版本 → 能力。到处都一样，界面文案才稳定。
        result = verdict(api_version=99, requires_app="9.9.9", capabilities=["nope"])
        self.assertFalse(result.api_ok)
        self.assertTrue(result.app_ok)
        self.assertTrue(result.capabilities_ok)

    def test_build_and_load_share_one_conclusion(self):
        payload = manifest_payload(requires_app=">=1.10.0,<1.11.0")
        manifest = PluginManifest.from_mapping(payload)
        expected = verdict(requires_app=payload["requires_app"])
        with self.assertRaises(PluginCompatibilityError) as ctx:
            manifest.check_compatibility("1.11.2")
        self.assertEqual(str(ctx.exception), expected.reason)

    def test_manifest_accepts_when_verdict_accepts(self):
        manifest = PluginManifest.from_mapping(manifest_payload(requires_app=">=1.10.0,<1.12.0"))
        manifest.check_compatibility("1.11.2")


class VersionOrderingTests(unittest.TestCase):
    def test_numeric_ordering_beats_string_ordering(self):
        self.assertEqual(compare_versions("1.10", "1.9"), 1)
        self.assertEqual(compare_versions("1.9", "1.10"), -1)
        self.assertEqual(compare_versions("1.10.0", "1.10.0"), 0)

    def test_non_standard_versions_are_not_comparable(self):
        self.assertIsNone(sortable_version("beta-2"))
        self.assertIsNone(compare_versions("beta-2", "1.0.0"))
        # 不可比较不等于相等：调用方必须显式处理 None。
        self.assertNotEqual(compare_versions("beta-2", "1.0.0"), 0)

    def test_prerelease_detection(self):
        self.assertTrue(is_prerelease("1.0.0-beta1"))
        self.assertTrue(is_prerelease("2.0.0rc1"))
        self.assertFalse(is_prerelease("1.0.0"))
        self.assertFalse(is_prerelease("beta-2"))  # 非标准版本不作为预发布推荐

    def test_prerelease_ordering(self):
        self.assertEqual(compare_versions("1.0.0-beta1", "1.0.0"), -1)


if __name__ == "__main__":
    unittest.main()
