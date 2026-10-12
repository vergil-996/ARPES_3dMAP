# -*- coding: utf-8 -*-
"""常驻页面树：层级、点击/键盘、展开记忆、搜索、同级排序、树内改名、栏宽。

覆盖设计意图：点击箭头只收展不切页，点击名称才切页；展开状态与滚动位置按
``page_id`` 在会话内保留，搜索用的是临时状态、清空后必须还原；排序只动目标
同级组，跨父节点与搜索期间都不允许；改名去首尾空白、空名保持原名、重名编号。
"""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QKeyEvent, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QAbstractItemView, QApplication, QMenu, QTreeWidget

from bandscope.ui import theme
from bandscope.ui.page_tree import PAGE_KIND_ICONS, PageTreeEntry, PageTreePanel
from bandscope.ui.result_workspace import (
    CANVAS_MIN_WIDTH,
    NAV_INITIAL_WIDTH,
    NAV_MIN_WIDTH,
)
from tests.support.pages import (
    HOME_ID,
    make_workspace,
    page_spec,
    reset_workspace_settings,
    sibling_titles,
)

NESTED = [
    ("p1", "A 组", None, "axis_integral"),
    ("p2", "B 组", "p1", "slice_dos"),
    ("p3", "C 组", "p2", "axis_integral"),
    ("p4", "D 组", None, "axis_integral"),
]


class _WorkspaceFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply_siui_palette()

    def setUp(self):
        reset_workspace_settings()
        self.workspace = make_workspace(NESTED)
        self.workspace.resize(900, 600)
        self.workspace.show()
        self.panel = self.workspace.page_tree
        self.tree = self.panel.tree
        self.activated = []
        self.panel.page_activated.connect(self.activated.append)

    def tearDown(self):
        self.panel._close_editor()
        self.workspace.hide()
        self.workspace.deleteLater()

    def item(self, page_id):
        return self.panel._items[page_id]

    def press(self, key):
        self.tree.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))

    def _click_at(self, position):
        """按下再松开：与 Qt 实际派发顺序一致，按下/松开都会走到视图处理。"""
        self.tree.mousePressEvent(
            QMouseEvent(
                QMouseEvent.MouseButtonPress,
                position,
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        self.tree.mouseReleaseEvent(
            QMouseEvent(
                QMouseEvent.MouseButtonRelease,
                position,
                Qt.LeftButton,
                Qt.NoButton,
                Qt.NoModifier,
            )
        )

    def click_arrow(self, page_id):
        """点该行左侧的展开箭头。"""
        rect = self.tree.visualItemRect(self.item(page_id))
        self._click_at(QPoint(rect.left() - 1, rect.center().y()))

    def click_name(self, page_id):
        """点该行的名称区域（箭头右边）。"""
        rect = self.tree.visualItemRect(self.item(page_id))
        self._click_at(QPoint(rect.left() + 20, rect.center().y()))

    def collapse_rows(self):
        """把树收成只剩顶层，等价于逐行点箭头收起。"""
        for page_id in list(self.panel._items):
            self.item(page_id).setExpanded(False)

    def expand_rows(self):
        for page_id in list(self.panel._items):
            self.item(page_id).setExpanded(True)

    def rename_to(self, page_id, text, *, cancelled=False):
        self.assertTrue(self.panel.begin_rename(page_id))
        self.panel._editor.setText(text)
        self.panel._editor._finish(cancelled=cancelled)


class PageTreeStructureTests(_WorkspaceFixture):
    def test_children_follow_the_source_relation(self):
        root = self.tree.topLevelItem(0)
        self.assertEqual(self.tree.item_page_id(root), HOME_ID)
        self.assertEqual(
            [self.tree.item_page_id(root.child(i)) for i in range(root.childCount())],
            ["p1", "p4"],
        )
        first = root.child(0)
        self.assertEqual(
            [self.tree.item_page_id(first.child(i)) for i in range(first.childCount())],
            ["p2"],
        )

    def test_row_order_is_the_tree_order(self):
        self.assertEqual(self.panel.visible_page_ids(), [HOME_ID, "p1", "p2", "p3", "p4"])

    def test_page_type_icons_are_kept(self):
        self.assertFalse(self.item("p2").icon(0).isNull())
        self.assertNotEqual(PAGE_KIND_ICONS["home"], PAGE_KIND_ICONS["axis_integral"])
        self.assertEqual(
            self.panel._entries["p2"].page_kind,
            self.workspace.page_by_id("p2").page_kind,
        )

    def test_long_names_are_not_elided_and_the_tooltip_carries_the_full_name(self):
        long_title = "超长的页面名称：沿能量轴积分得到的费米面（验收页眉排版）"
        self.workspace.set_page_title("p1", long_title)

        self.assertEqual(self.tree.textElideMode(), Qt.ElideNone)
        self.assertEqual(self.tree.horizontalScrollBarPolicy(), Qt.ScrollBarAsNeeded)
        self.assertEqual(self.item("p1").text(0), long_title)
        self.assertIn(long_title, self.item("p1").toolTip(0))
        # 列宽跟内容走，长名称真的能滚到（不是只靠提示）。
        self.assertGreater(self.tree.horizontalScrollBar().maximum(), 0)

    def test_scope_label_is_part_of_the_row_tooltip(self):
        self.workspace.page_by_id("p1").params["data_scope_label"] = "ROI #1"
        self.workspace.refresh_page_row("p1")
        self.assertIn("ROI #1", self.item("p1").toolTip(0))


class PageTreeClickTests(_WorkspaceFixture):
    def test_clicking_the_arrow_toggles_expansion_without_switching_page(self):
        self.workspace.activate_page("p4")
        self.assertTrue(self.item("p1").isExpanded())

        self.click_arrow("p1")

        self.assertFalse(self.item("p1").isExpanded())
        self.assertEqual(self.activated, [])
        self.assertEqual(self.workspace.current_page_id, "p4")

        self.click_arrow("p1")
        self.assertTrue(self.item("p1").isExpanded())
        self.assertEqual(self.activated, [])

    def test_clicking_the_arrow_keeps_the_selected_row(self):
        self.tree.setCurrentItem(self.item("p4"))
        self.click_arrow("p1")
        self.assertIs(self.tree.currentItem(), self.item("p4"))

    def test_clicking_the_name_activates_the_page(self):
        self.click_name("p2")
        self.assertEqual(self.activated, ["p2"])
        self.assertEqual(self.workspace.current_page_id, "p2")

    def test_clicking_a_leaf_row_activates_it(self):
        self.click_name("p4")
        self.assertEqual(self.activated, ["p4"])


class PageTreeKeyboardTests(_WorkspaceFixture):
    def test_enter_switches_page_and_arrows_do_not(self):
        self.tree.setCurrentItem(self.item("p1"))
        self.press(Qt.Key_Down)
        self.assertEqual(self.activated, [])
        self.assertEqual(self.tree.item_page_id(self.tree.currentItem()), "p2")

        self.press(Qt.Key_Return)
        self.assertEqual(self.activated, ["p2"])
        self.assertEqual(self.workspace.current_page_id, "p2")

    def test_left_and_right_only_collapse_and_expand(self):
        self.tree.setCurrentItem(self.item("p1"))
        self.press(Qt.Key_Left)
        self.assertFalse(self.item("p1").isExpanded())
        self.assertEqual(self.activated, [])

        self.press(Qt.Key_Right)
        self.assertTrue(self.item("p1").isExpanded())
        self.assertEqual(self.activated, [])

    def test_arrows_on_a_leaf_change_nothing(self):
        self.tree.setCurrentItem(self.item("p4"))
        before = self.workspace.current_page_id
        self.press(Qt.Key_Left)
        self.press(Qt.Key_Right)
        self.assertEqual(self.activated, [])
        self.assertEqual(self.workspace.current_page_id, before)

    def test_delete_directly_removes_the_selected_branch(self):
        requested = []
        self.panel.delete_requested.connect(requested.append)
        self.tree.setCurrentItem(self.item("p2"))

        self.press(Qt.Key_Delete)

        self.assertEqual(requested, ["p2"])
        self.assertNotIn("p2", self.workspace.page_specs)
        self.assertNotIn("p3", self.workspace.page_specs)

    def test_f2_opens_the_inline_editor(self):
        self.tree.setCurrentItem(self.item("p2"))
        self.press(Qt.Key_F2)
        self.assertTrue(self.panel.is_renaming("p2"))

    def test_typing_in_the_editor_is_not_taken_as_a_shortcut(self):
        self.panel.begin_rename("p2")
        editor = self.panel._editor
        editor.setText("B2")
        # Delete 在编辑框里是删字符，不该冒泡成删除页面请求。
        requested = []
        self.panel.delete_requested.connect(requested.append)
        editor.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))
        self.assertEqual(requested, [])


class PageTreeExpansionTests(_WorkspaceFixture):
    def test_expansion_state_survives_a_rebuild(self):
        self.item("p1").setExpanded(False)
        self.workspace.set_page_title("p4", "改名后重建")

        self.assertFalse(self.item("p1").isExpanded())
        self.assertEqual(self.item("p4").text(0), "改名后重建")

    def test_new_page_expands_ancestors_and_is_located(self):
        self.collapse_rows()
        self.assertFalse(self.item("p1").isExpanded())

        self.workspace.add_page(
            page_spec("p5", "新页面", "axis_integral", source_page_id="p1")
        )

        self.assertTrue(self.item("p1").isExpanded())
        self.assertIs(self.tree.currentItem(), self.item("p5"))

    def test_expanding_every_row_survives_a_rebuild(self):
        self.collapse_rows()
        self.expand_rows()

        self.workspace.refresh_navigation()

        self.assertTrue(self.item("p2").isExpanded())

    def test_deleting_a_page_does_not_resurrect_its_expansion(self):
        self.collapse_rows()
        self.workspace.delete_page("p2")

        self.assertNotIn("p2", self.panel._items)
        self.assertNotIn("p2", self.panel._expanded_ids)


class PageTreeMultiSelectionTests(_WorkspaceFixture):
    def name_position(self, page_id):
        rect = self.tree.visualItemRect(self.item(page_id))
        return QPoint(rect.left() + 20, rect.center().y())

    def click(self, page_id, modifiers=Qt.NoModifier):
        QTest.mouseClick(self.tree.viewport(), Qt.LeftButton, modifiers, self.name_position(page_id))

    def drag_blank(self, first_id, last_id, modifiers=Qt.NoModifier):
        first = self.tree.visualItemRect(self.item(first_id))
        last = self.tree.visualItemRect(self.item(last_id))
        x = self.tree.viewport().width() - 8
        start, end = QPoint(x, first.top() + 2), QPoint(x - 12, last.bottom() - 2)
        for event_type, position, button, buttons in (
            (QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton),
            (QEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton),
        ):
            QApplication.sendEvent(self.tree.viewport(), QMouseEvent(event_type, position, button, buttons, modifiers))
        self.assertTrue(self.tree._rubber_band.isVisible())
        QApplication.sendEvent(
            self.tree.viewport(),
            QMouseEvent(QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton, modifiers),
        )
        self.assertFalse(self.tree._rubber_band.isVisible())

    def test_blank_drag_selects_rows_without_activating_them(self):
        before = self.workspace.current_page_id
        self.drag_blank("p2", "p4")
        self.assertEqual(self.tree.selected_page_ids(), ["p2", "p3", "p4"])
        self.assertEqual(self.activated, [])
        self.assertEqual(self.workspace.current_page_id, before)

    def test_blank_drag_then_delete_emits_batch_and_removes_descendants_once(self):
        requested, closed = [], []
        self.panel.pages_delete_requested.connect(requested.append)
        self.workspace.page_closed.connect(closed.append)
        self.drag_blank("p2", "p4")
        QTest.keyClick(self.tree, Qt.Key_Delete)
        self.assertEqual(requested, [["p2", "p3", "p4"]])
        self.assertEqual(set(self.workspace.page_specs), {HOME_ID, "p1"})
        self.assertCountEqual(closed, ["p2", "p3", "p4"])

    def test_ctrl_click_adds_and_removes_without_switching_page(self):
        self.click("p2")
        self.activated.clear()
        self.click("p4", Qt.ControlModifier)
        self.assertEqual(self.tree.selected_page_ids(), ["p2", "p4"])
        self.assertEqual(self.activated, [])
        self.assertEqual(self.workspace.current_page_id, "p2")
        self.click("p2", Qt.ControlModifier)
        self.assertEqual(self.tree.selected_page_ids(), ["p4"])
        self.assertEqual(self.activated, [])

    def test_shift_click_selects_a_range_without_switching_page(self):
        self.click("p1")
        self.activated.clear()
        self.click("p4", Qt.ShiftModifier)
        self.assertEqual(self.tree.selected_page_ids(), ["p1", "p2", "p3", "p4"])
        self.assertEqual(self.activated, [])
        self.assertEqual(self.workspace.current_page_id, "p1")

    def test_ctrl_blank_drag_adds_to_existing_selection(self):
        self.click("p1")
        self.activated.clear()
        self.drag_blank("p3", "p4", Qt.ControlModifier)
        self.assertEqual(self.tree.selected_page_ids(), ["p1", "p3", "p4"])
        self.assertEqual(self.activated, [])

    def test_right_click_on_selection_keeps_the_batch_and_deletes_from_menu(self):
        self.drag_blank("p2", "p4")
        captured = []

        def execute(menu, _pos):
            captured.append((self.tree.selected_page_ids(), [action.text() for action in menu.actions()]))
            menu.actions()[-1].trigger()

        position = self.name_position("p3")
        QTest.mouseClick(self.tree.viewport(), Qt.RightButton, Qt.NoModifier, position)
        with patch.object(QMenu, "exec_", new=execute):
            self.tree.customContextMenuRequested.emit(position)
        self.assertEqual(captured[0][0], ["p2", "p3", "p4"])
        self.assertIn("删除选中的 3 个页面", captured[0][1])
        self.assertEqual(set(self.workspace.page_specs), {HOME_ID, "p1"})

    def test_right_click_unselected_row_replaces_the_batch(self):
        self.drag_blank("p2", "p4")
        position = self.name_position("p1")
        QTest.mouseClick(self.tree.viewport(), Qt.RightButton, Qt.NoModifier, position)
        with patch.object(QMenu, "exec_", return_value=None):
            self.tree.customContextMenuRequested.emit(position)
        self.assertEqual(self.tree.selected_page_ids(), ["p1"])
        self.assertEqual(self.activated, [])

    def test_rebuild_and_refresh_keep_selection_and_focused_row(self):
        self.click("p2")
        self.click("p4", Qt.ControlModifier)
        self.workspace.set_page_title("p1", "重新构建")
        self.assertEqual(self.tree.selected_page_ids(), ["p2", "p4"])
        self.assertEqual(self.tree.item_page_id(self.tree.currentItem()), "p4")
        self.workspace.refresh_navigation()
        self.assertEqual(self.tree.selected_page_ids(), ["p2", "p4"])

    def test_inactive_page_keeps_existing_selection_focus_and_delete_target(self):
        self.click("p2")
        self.click("p4", Qt.ControlModifier)
        self.workspace.add_page_inactive(
            page_spec("p5", "后台结果", "axis_integral", source_page_id=HOME_ID)
        )
        self.assertEqual(self.tree.selected_page_ids(), ["p2", "p4"])
        self.assertEqual(self.tree.item_page_id(self.tree.currentItem()), "p4")
        self.assertEqual(self.workspace.current_page_id, "p2")
        QTest.keyClick(self.tree, Qt.Key_Delete)
        self.assertEqual(set(self.workspace.page_specs), {HOME_ID, "p1", "p5"})

    def test_inactive_page_keeps_the_search_query_and_selection(self):
        self.click("p2")
        self.panel.search_edit.setText("B 组")
        self.workspace.add_page_inactive(
            page_spec("p5", "后台结果", "axis_integral", source_page_id=HOME_ID)
        )
        self.assertEqual(self.panel.search_text(), "B 组")
        self.assertEqual(self.panel.visible_page_ids(), [HOME_ID, "p1", "p2"])
        self.assertEqual(self.tree.selected_page_ids(), ["p2"])
        self.assertEqual(self.tree.item_page_id(self.tree.currentItem()), "p2")
        self.assertEqual(self.workspace.current_page_id, "p2")

    def test_activating_another_page_replaces_the_batch_selection(self):
        self.drag_blank("p2", "p4")
        self.workspace.activate_page("p1")
        self.assertEqual(self.tree.selected_page_ids(), ["p1"])
        self.assertEqual(self.workspace.current_page_id, "p1")

    def test_filter_prunes_hidden_selection_and_delete_keeps_hidden_pages(self):
        self.click("p2")
        self.click("p4", Qt.ControlModifier)
        self.panel.search_edit.setText("B 组")
        self.assertFalse(self.item("p4").isSelected())
        self.assertEqual(self.tree.selected_page_ids(), ["p2"])
        QTest.keyClick(self.tree, Qt.Key_Delete)
        self.assertIn("p4", self.workspace.page_specs)

    def test_collapsing_a_branch_prunes_selected_descendants(self):
        self.drag_blank("p2", "p4")
        self.click_arrow("p1")
        self.assertFalse(self.item("p2").isSelected())
        self.assertFalse(self.item("p3").isSelected())
        self.assertEqual(self.tree.selected_page_ids(), ["p4"])
        QTest.keyClick(self.tree, Qt.Key_Delete)
        self.assertIn("p2", self.workspace.page_specs)

    def test_rubber_band_ignores_rows_in_collapsed_branches(self):
        self.item("p1").setExpanded(False)
        self.drag_blank("p1", "p4")
        self.assertEqual(self.tree.selected_page_ids(), ["p1", "p4"])
        self.assertFalse(self.item("p2").isSelected())
        self.assertFalse(self.item("p3").isSelected())

    def test_protected_page_is_excluded_from_the_batch_signal(self):
        requested = []
        self.panel.pages_delete_requested.connect(requested.append)
        self.drag_blank(HOME_ID, "p4")
        QTest.keyClick(self.tree, Qt.Key_Delete)
        self.assertEqual(requested, [["p1", "p2", "p3", "p4"]])
        self.assertEqual(set(self.workspace.page_specs), {HOME_ID})

    def test_clicking_blank_deselects_and_delete_does_nothing(self):
        self.click("p2")
        QTest.mouseClick(self.tree.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(10, self.tree.viewport().height() - 10))
        QTest.keyClick(self.tree, Qt.Key_Delete)
        self.assertEqual(self.tree.selected_page_ids(), [])
        self.assertIn("p2", self.workspace.page_specs)

    def test_dragging_a_name_passes_to_qt_for_sibling_sorting(self):
        start, end = self.name_position("p1"), self.name_position("p4")
        QTest.mousePress(self.tree.viewport(), Qt.LeftButton, Qt.NoModifier, start)
        with patch.object(QTreeWidget, "mouseMoveEvent") as qt_move:
            QApplication.sendEvent(
                self.tree.viewport(),
                QMouseEvent(QEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton, Qt.NoModifier),
            )
        QTest.mouseRelease(self.tree.viewport(), Qt.LeftButton, Qt.NoModifier, end)
        qt_move.assert_called_once()
        self.assertFalse(self.tree._rubber_band.isVisible())
        self.assertIsNone(self.tree._rubber_origin)


class PageTreeSearchTests(_WorkspaceFixture):
    def test_search_shows_matches_and_their_ancestors_only(self):
        self.panel.search_edit.setText("B 组")

        self.assertEqual(self.panel.entry_titles(), ["原始视图", "A 组", "B 组"])
        self.assertEqual(self.panel.visible_page_ids(), [HOME_ID, "p1", "p2"])
        self.assertFalse(self.panel.empty_label.isVisible())

    def test_search_is_case_insensitive_and_matches_inside_the_name(self):
        self.workspace.set_page_title("p4", "Fermi Surface")
        self.panel.search_edit.setText("fermi")
        self.assertIn("Fermi Surface", self.panel.entry_titles())

    def test_search_expands_the_path_temporarily(self):
        self.collapse_rows()

        self.panel.search_edit.setText("B 组")

        self.assertTrue(self.item("p1").isExpanded())

    def test_clearing_search_restores_the_previous_expansion(self):
        self.collapse_rows()
        self.panel.search_edit.setText("B 组")
        self.assertTrue(self.item("p1").isExpanded())

        self.panel.search_edit.clear()

        self.assertFalse(self.item("p1").isExpanded())

    def test_no_match_shows_the_hint_and_keeps_the_current_page(self):
        before = self.workspace.current_page_id

        self.panel.search_edit.setText("没有这个页面")

        self.assertEqual(self.panel.entry_titles(), [])
        self.assertTrue(self.panel.empty_label.isVisible())
        self.assertEqual(self.workspace.current_page_id, before)

    def test_activating_a_page_hidden_by_the_search_clears_the_search(self):
        self.panel.search_edit.setText("B 组")
        self.assertTrue(self.item("p4").isHidden())

        self.workspace.activate_page("p4")

        self.assertEqual(self.panel.search_text(), "")
        self.assertFalse(self.item("p4").isHidden())
        self.assertIs(self.tree.currentItem(), self.item("p4"))

    def test_revealing_a_page_clears_the_search_and_expands_its_ancestors(self):
        self.workspace.activate_page("p2")
        self.collapse_rows()
        self.panel.search_edit.setText("D 组")

        self.panel.reveal_page("p2")

        self.assertEqual(self.panel.search_text(), "")
        self.assertTrue(self.item("p1").isExpanded())
        self.assertIs(self.tree.currentItem(), self.item("p2"))

    def test_sorting_is_disabled_while_searching(self):
        self.assertTrue(self.tree.dragEnabled())

        self.panel.search_edit.setText("B 组")

        self.assertFalse(self.tree.dragEnabled())
        self.assertFalse(self.tree.acceptDrops())
        self.assertFalse(self.workspace.move_page_within_siblings("p4", "p1", True))

        self.panel.search_edit.clear()
        self.assertTrue(self.tree.dragEnabled())

    def test_search_does_not_rewrite_the_real_page_records(self):
        before = sorted(self.workspace.page_specs)
        self.panel.search_edit.setText("B 组")
        self.assertEqual(sorted(self.workspace.page_specs), before)


class PageTreeReorderTests(_WorkspaceFixture):
    def test_same_parent_drops_are_accepted_at_every_indicator_position(self):
        root = self.tree.topLevelItem(0)
        dragged, target = root.child(0), root.child(1)

        for position, before in (
            (QAbstractItemView.AboveItem, True),
            (QAbstractItemView.BelowItem, False),
            (QAbstractItemView.OnItem, True),
        ):
            with self.subTest(position=position):
                self.assertEqual(
                    self.tree.resolve_drop(dragged, target, position),
                    ("p1", "p4", before),
                )

    def test_cross_parent_drops_are_rejected(self):
        for dragged, target in (("p2", "p4"), ("p4", "p2")):
            with self.subTest(dragged=dragged, target=target):
                self.assertIsNone(
                    self.tree.resolve_drop(
                        self.item(dragged), self.item(target), QAbstractItemView.AboveItem
                    )
                )

    def test_dropping_onto_itself_or_empty_space_is_rejected(self):
        self.assertIsNone(
            self.tree.resolve_drop(
                self.item("p2"), self.item("p2"), QAbstractItemView.AboveItem
            )
        )
        self.assertIsNone(
            self.tree.resolve_drop(
                self.item("p2"), self.item("p4"), QAbstractItemView.OnViewport
            )
        )

    def test_reordering_moves_only_the_target_sibling_group(self):
        self.assertEqual(sibling_titles(self.workspace, HOME_ID), ["A 组", "D 组"])
        self.assertEqual(sibling_titles(self.workspace, "p1"), ["B 组"])

        self.assertTrue(self.workspace.move_page_within_siblings("p4", "p1", True))

        self.assertEqual(sibling_titles(self.workspace, HOME_ID), ["D 组", "A 组"])
        self.assertEqual(sibling_titles(self.workspace, "p1"), ["B 组"])
        self.assertEqual(sibling_titles(self.workspace, "p2"), ["C 组"])

    def test_reordering_to_the_end_of_a_group(self):
        self.workspace.add_page(
            page_spec("p5", "E 组", "axis_integral", source_page_id=HOME_ID)
        )

        self.assertTrue(self.workspace.move_page_within_siblings("p1", "p5", False))

        self.assertEqual(sibling_titles(self.workspace, HOME_ID), ["D 组", "E 组", "A 组"])

    def test_deep_siblings_can_be_reordered_without_touching_the_top_level(self):
        self.workspace.add_page(
            page_spec("p6", "F 组", "axis_integral", source_page_id="p2")
        )

        self.assertTrue(self.workspace.move_page_within_siblings("p6", "p3", True))

        self.assertEqual(sibling_titles(self.workspace, "p2"), ["F 组", "C 组"])
        self.assertEqual(sibling_titles(self.workspace, HOME_ID), ["A 组", "D 组"])

    def test_home_cannot_be_reordered(self):
        self.assertFalse(self.workspace.move_page_within_siblings("p4", HOME_ID, True))
        self.assertFalse(self.workspace.move_page_within_siblings(HOME_ID, "p4", True))
        self.assertEqual(sibling_titles(self.workspace, HOME_ID), ["A 组", "D 组"])

    def test_sibling_order_is_saved_for_the_next_session(self):
        self.workspace.move_page_within_siblings("p4", "p1", True)

        reopened = make_workspace(NESTED)
        self.addCleanup(reopened.deleteLater)

        self.assertEqual(sibling_titles(reopened, HOME_ID), ["D 组", "A 组"])


class PageTreeRenameTests(_WorkspaceFixture):
    def test_enter_commits_a_trimmed_chinese_name(self):
        self.rename_to("p2", "  费米面切面  ")

        self.assertEqual(self.workspace.page_by_id("p2").title, "费米面切面")
        self.assertIn("费米面切面", self.panel.entry_titles())
        self.assertFalse(self.panel.is_renaming())

    def test_escape_cancels_the_edit(self):
        self.rename_to("p2", "不要保存", cancelled=True)

        self.assertEqual(self.workspace.page_by_id("p2").title, "B 组")
        self.assertFalse(self.panel.is_renaming())

    def test_blank_input_restores_the_original_name(self):
        self.rename_to("p2", "   ")

        self.assertEqual(self.workspace.page_by_id("p2").title, "B 组")

    def test_duplicate_names_get_a_number_suffix(self):
        self.rename_to("p2", "D 组")

        self.assertEqual(self.workspace.page_by_id("p2").title, "D 组_2")

    def test_renaming_keeps_identity_parameters_and_source_link(self):
        page = self.workspace.page_by_id("p2")
        page.params["low"] = 3
        before = (page.page_id, dict(page.params), page.source_page_id, page.page_kind)

        self.rename_to("p2", "改名")

        self.assertEqual(
            (page.page_id, dict(page.params), page.source_page_id, page.page_kind), before
        )

    def test_main_page_can_be_renamed_from_the_tree(self):
        self.rename_to(HOME_ID, "我的主页")

        self.assertEqual(self.workspace.page_by_id(HOME_ID).title, "我的主页")
        self.assertTrue(self.workspace.page_by_id(HOME_ID).title_overridden)

    def test_tool_pages_and_missing_pages_cannot_be_renamed(self):
        self.workspace.add_pinned_page(page_spec("tools", "工具页", "control_panel"))

        self.assertFalse(self.panel.begin_rename("tools"))
        self.assertFalse(self.panel.begin_rename("没有这个页面"))

    def test_starting_a_second_edit_closes_the_first(self):
        self.panel.begin_rename("p2")
        first = self.panel._editor

        self.panel.begin_rename("p4")

        self.assertIsNot(self.panel._editor, first)
        self.assertTrue(self.panel.is_renaming("p4"))
        self.assertFalse(self.panel.is_renaming("p2"))

    def test_restore_auto_name_request_is_forwarded(self):
        requested = []
        self.panel.restore_name_requested.connect(requested.append)

        self.panel.restore_name_requested.emit("p2")

        self.assertEqual(requested, ["p2"])


class PageTreeHelperTests(unittest.TestCase):
    """不依赖工作区的纯逻辑。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply_siui_palette()

    def test_entry_tooltip_only_shows_the_scope_when_there_is_one(self):
        self.assertEqual(PageTreeEntry("p", "名称").tooltip(), "名称")
        self.assertIn("ROI #1", PageTreeEntry("p", "名称", scope_label="ROI #1").tooltip())

    def test_empty_panel_shows_nothing_and_locates_nothing(self):
        panel = PageTreePanel()
        self.addCleanup(panel.deleteLater)

        self.assertEqual(panel.entry_titles(), [])
        panel.set_current_page("missing")
        panel.reveal_page("missing")
        panel.set_pages({}, {}, None)

        self.assertEqual(panel.visible_page_ids(), [])


class NavWidthTests(_WorkspaceFixture):
    def test_initial_width_and_minimums(self):
        self.assertEqual(NAV_INITIAL_WIDTH, 240)
        self.assertEqual(NAV_MIN_WIDTH, 180)
        self.assertEqual(self.workspace.page_tree.minimumWidth(), NAV_MIN_WIDTH)
        self.assertEqual(
            self.workspace.splitter.widget(1).minimumWidth(), CANVAS_MIN_WIDTH
        )
        self.assertEqual(self.workspace.nav_width(), NAV_INITIAL_WIDTH)

    def test_width_below_the_minimum_is_clamped(self):
        self.workspace.set_nav_width(100)
        self.assertEqual(self.workspace.nav_width(), NAV_MIN_WIDTH)

    def test_the_canvas_keeps_its_minimum_width(self):
        self.workspace.set_nav_width(100000)

        sizes = self.workspace.splitter.sizes()
        self.assertGreaterEqual(sizes[1], CANVAS_MIN_WIDTH)

    def test_width_is_restored_on_the_next_start(self):
        self.workspace.set_nav_width(300)
        self.workspace.save_nav_width()

        reopened = make_workspace([])
        self.addCleanup(reopened.deleteLater)
        reopened.resize(900, 600)
        reopened.show()

        self.assertEqual(reopened.nav_width(), 300)

    def test_a_stored_width_below_the_minimum_falls_back_to_the_default(self):
        settings = self.workspace.settings
        settings.setValue("result_workspace/nav_width", 20)
        settings.sync()

        reopened = make_workspace([])
        self.addCleanup(reopened.deleteLater)
        reopened.resize(900, 600)
        reopened.show()

        self.assertEqual(reopened.nav_width(), NAV_INITIAL_WIDTH)


if __name__ == "__main__":
    unittest.main()
