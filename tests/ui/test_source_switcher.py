# -*- coding: utf-8 -*-
"""顶部数据源切换器：可编辑下拉、别名提交 / 撤销、2 份起展开与收起动画。"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.ui import theme
from bandscope.ui.source_switcher import (
    EXPAND_DURATION_MS,
    SWITCHER_WIDTH,
    SourceSwitcher,
    SourceSwitcherBar,
)

ITEMS = [("session-a", "scan"), ("session-b", "scan_2")]


class SourceSwitcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply_siui_palette()

    def setUp(self):
        self.switcher = SourceSwitcher()
        self.selections = []
        self.aliases = []
        self.switcher.source_selected.connect(self.selections.append)
        self.switcher.alias_committed.connect(lambda sid, alias: self.aliases.append((sid, alias)))

    def tearDown(self):
        self.switcher.deleteLater()

    def test_items_carry_session_ids_and_the_active_one_is_selected(self):
        self.switcher.sync(ITEMS, "session-b")
        self.assertEqual(self.switcher.count(), 2)
        self.assertEqual(self.switcher.itemData(0), "session-a")
        self.assertEqual(self.switcher.itemText(1), "scan_2")
        self.assertEqual(self.switcher.current_session_id(), "session-b")
        self.assertEqual(self.switcher.lineEdit().text(), "scan_2")

    def test_selecting_another_item_reports_its_session(self):
        self.switcher.sync(ITEMS, "session-a")
        self.switcher.setCurrentIndex(1)
        self.assertEqual(self.selections, ["session-b"])

    def test_programmatic_sync_does_not_report_a_selection(self):
        self.switcher.sync(ITEMS, "session-a")
        self.switcher.sync(ITEMS, "session-b")
        self.assertEqual(self.selections, [])

    def test_enter_or_focus_out_commits_the_edited_alias(self):
        self.switcher.sync(ITEMS, "session-b")
        editor = self.switcher.lineEdit()
        editor.setText("我的数据")
        editor.editingFinished.emit()
        self.assertEqual(self.aliases, [("session-b", "我的数据")])
        self.assertEqual(self.switcher.itemText(1), "我的数据")

    def test_empty_or_unchanged_text_is_not_a_rename_transaction(self):
        self.switcher.sync(ITEMS, "session-b")
        editor = self.switcher.lineEdit()
        editor.setText("扫描数据")
        editor.editingFinished.emit()
        self.assertEqual(self.aliases, [("session-b", "扫描数据")])

        # 未修改文字：失焦不应反复提交。
        editor.editingFinished.emit()
        self.assertEqual(len(self.aliases), 1)

        # 空文字提交给宿主，由宿主恢复默认别名。
        editor.setText("")
        editor.editingFinished.emit()
        self.assertEqual(self.aliases[-1], ("session-b", ""))

    def test_escape_reverts_the_edit_without_committing(self):
        self.switcher.sync(ITEMS, "session-b")
        editor = self.switcher.lineEdit()
        editor.setText("临时名字")
        QTest.keyClick(editor, Qt.Key_Escape)
        self.assertEqual(editor.text(), "scan_2")
        self.assertEqual(self.aliases, [])

    def test_sync_rewrites_the_editor_text_to_the_final_alias(self):
        self.switcher.sync(ITEMS, "session-b")
        self.switcher.lineEdit().setText("未提交")
        self.switcher.sync([("session-a", "scan"), ("session-b", "scan_3")], "session-b")
        self.assertEqual(self.switcher.lineEdit().text(), "scan_3")
        self.assertEqual(self.switcher.itemText(1), "scan_3")


class SourceSwitcherBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply_siui_palette()

    def setUp(self):
        self.bar = SourceSwitcherBar()

    def tearDown(self):
        self.bar.deleteLater()

    def test_zero_or_one_source_takes_no_layout_space(self):
        self.bar.apply_sources([], None)
        self.assertTrue(self.bar.isHidden())
        self.assertFalse(self.bar.is_expanded())

        self.bar.apply_sources(ITEMS[:1], "session-a")
        self.assertTrue(self.bar.isHidden())
        self.assertFalse(self.bar.is_expanded())

    def test_two_sources_expand_with_the_shared_duration(self):
        self.bar.apply_sources(ITEMS, "session-a")
        self.assertTrue(self.bar.is_expanded())
        self.assertFalse(self.bar.isHidden())
        self.assertTrue(self.bar.is_animating())

        QTest.qWait(EXPAND_DURATION_MS + 120)
        self.assertFalse(self.bar.is_animating())
        self.assertFalse(self.bar.isHidden())
        self.assertEqual(self.bar.switcher.current_session_id(), "session-a")

    def test_falling_back_to_one_source_collapses_again(self):
        self.bar.apply_sources(ITEMS, "session-a")
        QTest.qWait(EXPAND_DURATION_MS + 120)

        self.bar.apply_sources(ITEMS[:1], "session-a")
        self.assertFalse(self.bar.is_expanded())
        self.assertTrue(self.bar.is_animating())
        QTest.qWait(EXPAND_DURATION_MS + 120)
        self.assertTrue(self.bar.isHidden())
        self.assertFalse(self.bar.is_animating())

    def test_repeated_operations_continue_from_the_current_position(self):
        self.bar.apply_sources(ITEMS, "session-a")
        QTest.qWait(40)
        self.bar.apply_sources(ITEMS[:1], "session-a")
        # 打断后仍按同一时长收敛到收起态，而不是重新展开。
        self.assertFalse(self.bar.is_expanded())
        QTest.qWait(EXPAND_DURATION_MS + 120)
        self.assertTrue(self.bar.isHidden())

    def test_bar_keeps_the_switcher_width_stable(self):
        self.bar.apply_sources(ITEMS, "session-a")
        QTest.qWait(EXPAND_DURATION_MS + 120)
        self.assertEqual(self.bar.switcher.width(), SWITCHER_WIDTH)

    def test_selection_and_alias_signals_are_forwarded(self):
        selections = []
        aliases = []
        self.bar.source_selected.connect(selections.append)
        self.bar.alias_committed.connect(lambda sid, alias: aliases.append((sid, alias)))
        self.bar.apply_sources(ITEMS, "session-a")

        self.bar.switcher.setCurrentIndex(1)
        self.bar.switcher.lineEdit().setText("新名字")
        self.bar.switcher.lineEdit().editingFinished.emit()

        self.assertEqual(selections, ["session-b"])
        self.assertEqual(aliases, [("session-b", "新名字")])


if __name__ == "__main__":
    unittest.main()
