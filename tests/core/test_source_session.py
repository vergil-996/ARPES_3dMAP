# -*- coding: utf-8 -*-
"""多数据源会话模型：别名、活动源、页面归属与源级关闭顺序。"""
import unittest

from bandscope.core.source_session import (
    ENTRY_NPZ,
    ENTRY_VIEW_RECIPE,
    FALLBACK_ALIAS,
    SourceSessionManager,
    default_alias,
    new_session_id,
    unique_alias,
)


class AliasTests(unittest.TestCase):
    def test_npz_alias_drops_one_extension(self):
        self.assertEqual(default_alias(r"D:\data\WSe2_step.npz", entry_kind=ENTRY_NPZ), "WSe2_step")
        self.assertEqual(default_alias("scan.mat", entry_kind=ENTRY_NPZ), "scan")

    def test_view_recipe_alias_drops_the_full_suffix(self):
        self.assertEqual(
            default_alias("figure.png.bsview.json", entry_kind=ENTRY_VIEW_RECIPE),
            "figure.png",
        )
        # 后缀识别不区分大小写，但保留原始大小写的剩余部分。
        self.assertEqual(
            default_alias("Figure.BSVIEW.JSON", entry_kind=ENTRY_VIEW_RECIPE),
            "Figure",
        )

    def test_empty_path_still_gets_a_readable_alias(self):
        self.assertEqual(default_alias("", entry_kind=ENTRY_NPZ), FALLBACK_ALIAS)
        self.assertEqual(unique_alias([FALLBACK_ALIAS], "  "), f"{FALLBACK_ALIAS}_2")


class ManagerTests(unittest.TestCase):
    def make_manager(self):
        manager = SourceSessionManager()
        first = manager.add(entry_kind=ENTRY_NPZ, entry_path=r"C:\a\npz\scan.npz")
        second = manager.add(entry_kind=ENTRY_NPZ, entry_path=r"C:\b\npz\scan.npz")
        return manager, first, second

    def test_duplicate_files_create_two_independent_sessions(self):
        manager, first, second = self.make_manager()
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertNotEqual(first.session_id, new_session_id())
        self.assertEqual(len(manager), 2)
        self.assertEqual([session.alias for session in manager], ["scan", "scan_2"])
        self.assertEqual(manager.active_id, second.session_id)

    def test_activation_is_a_no_op_for_the_current_or_unknown_source(self):
        manager, first, second = self.make_manager()
        self.assertFalse(manager.set_active(second.session_id))
        self.assertFalse(manager.set_active("missing"))
        self.assertTrue(manager.set_active(first.session_id))
        self.assertTrue(manager.is_active(first.session_id))

    def test_pages_are_owned_by_one_session_and_reject_reuse(self):
        manager, first, second = self.make_manager()
        manager.register_pages(first.session_id, ["home-a", "roi-a"], home_page_id="home-a")
        manager.register_page(second.session_id, "home-b")
        self.assertEqual(manager.owner_of("home-a"), first.session_id)
        self.assertEqual(manager.session_for_page("home-b"), second)
        self.assertTrue(manager.owns_page(first.session_id, "roi-a"))
        with self.assertRaisesRegex(ValueError, "占用"):
            manager.register_page(first.session_id, "home-b")

    def test_closing_an_active_source_selects_the_previous_source(self):
        manager, first, second = self.make_manager()
        closed, active = manager.close(second.session_id)
        self.assertIs(closed, second)
        self.assertEqual(active, first.session_id)
        self.assertFalse(manager.has_session(second.session_id))
        self.assertEqual(manager.session_ids(), [first.session_id])

    def test_closing_the_first_source_selects_the_next_one(self):
        manager, first, second = self.make_manager()
        manager.set_active(first.session_id)
        _closed, active = manager.close(first.session_id)
        self.assertEqual(active, second.session_id)

    def test_closing_the_last_source_leaves_an_empty_manager(self):
        manager = SourceSessionManager()
        session = manager.add(entry_kind=ENTRY_NPZ, entry_path="only.npz")
        manager.register_pages(session.session_id, ["home", "p"])
        closed, active = manager.close(session.session_id)
        self.assertIs(closed, session)
        self.assertIsNone(active)
        self.assertEqual(len(manager), 0)
        self.assertIsNone(manager.owner_of("home"))
        self.assertEqual(session.page_ids, [])

    def test_closing_a_background_source_keeps_the_active_source(self):
        manager, first, second = self.make_manager()
        _closed, active = manager.close(first.session_id)
        self.assertEqual(active, second.session_id)
        self.assertTrue(manager.is_active(second.session_id))

    def test_a_source_being_closed_cannot_be_activated(self):
        manager, first, second = self.make_manager()
        manager.set_active(first.session_id)
        manager.mark_closing(second.session_id)
        self.assertFalse(manager.set_active(second.session_id))
        self.assertTrue(manager.is_active(first.session_id))
        manager.unmark_closing(second.session_id)
        self.assertTrue(manager.set_active(second.session_id))

    def test_alias_edit_restores_default_for_empty_and_dedupes(self):
        manager, first, second = self.make_manager()
        self.assertEqual(manager.set_alias(second.session_id, "第一次"), "第一次")
        # 空名称恢复默认别名；默认别名已被别的源占用时自动追加序号。
        self.assertEqual(manager.set_alias(second.session_id, ""), "scan_2")
        self.assertEqual(manager.set_alias(second.session_id, "scan"), "scan_2")
        self.assertEqual(manager.alias_for(second.session_id), "scan_2")
        self.assertEqual(manager.source_items(), [
            (first.session_id, "scan"),
            (second.session_id, "scan_2"),
        ])

    def test_renaming_to_the_same_alias_keeps_it(self):
        manager, first, _second = self.make_manager()
        self.assertEqual(manager.set_alias(first.session_id, "scan"), "scan")


if __name__ == "__main__":
    unittest.main()
