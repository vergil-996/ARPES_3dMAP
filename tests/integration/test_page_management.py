# -*- coding: utf-8 -*-
"""页面删除与宿主接入：统一删除核心、确认文案、快捷键焦点隔离。

覆盖设计意图：删除永远连带整支派生页（搜索隐藏的也算），确认里给出目标名称与
总数且默认取消；删到当前页的祖先时退到最近的存活父页，删别的分支不动当前页；
预览/复位这类内部清空不弹确认。方向键与数字键在页面树持有焦点时归树使用。
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication, QLineEdit, QMessageBox, QSlider, QTreeWidget

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.ui import theme
from tests.support.pages import HOME_ID, make_workspace, page_spec, reset_workspace_settings

TREE = [
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
        self.workspace = make_workspace(TREE)
        self.workspace.resize(900, 600)
        self.workspace.show()
        self.closed = []
        self.plans = []
        self.answers = []
        self.workspace.page_closed.connect(self.closed.append)
        self.workspace.set_delete_confirm_handler(self._confirm)

    def tearDown(self):
        self.workspace.hide()
        self.workspace.deleteLater()

    def _confirm(self, plan):
        self.plans.append(plan)
        return self.answers.pop(0) if self.answers else True

    def remaining(self):
        return sorted(self.workspace.page_specs)


class PageDeletePlanTests(_WorkspaceFixture):
    def test_plan_covers_the_whole_subtree_children_first(self):
        plan = self.workspace.page_delete_plan("p1")

        self.assertEqual(plan["ids"], ["p3", "p2", "p1"])
        self.assertEqual(plan["count"], 3)
        self.assertEqual(plan["title"], "A 组")
        self.assertIsNone(plan["blocked"])
        self.assertEqual(plan["fallback_id"], HOME_ID)

    def test_plan_counts_pages_that_the_search_is_hiding(self):
        self.workspace.page_tree.search_edit.setText("D 组")
        self.assertTrue(self.workspace.page_tree._items["p3"].isHidden())

        plan = self.workspace.page_delete_plan("p1")

        self.assertEqual(plan["count"], 3)
        self.assertEqual(plan["ids"], ["p3", "p2", "p1"])

    def test_plan_marks_whether_the_current_page_goes_away(self):
        self.workspace.activate_page("p3")
        self.assertTrue(self.workspace.page_delete_plan("p1")["current_removed"])
        self.assertFalse(self.workspace.page_delete_plan("p4")["current_removed"])

    def test_a_missing_page_has_no_plan(self):
        self.assertIsNone(self.workspace.page_delete_plan("没有这个页面"))

    def test_protected_root_blocks_the_delete(self):
        self.workspace.add_pinned_page(page_spec("tools", "工具页", "control_panel"))

        plan = self.workspace.page_delete_plan("tools")

        self.assertEqual(plan["blocked"], "protected_root")
        self.assertFalse(self.workspace.delete_page("tools"))
        self.assertIn("tools", self.workspace.page_specs)

    def test_a_protected_descendant_blocks_the_whole_branch(self):
        self.workspace.add_pinned_page(
            page_spec("tools", "工具页", "control_panel", source_page_id="p1")
        )

        plan = self.workspace.page_delete_plan("p1")

        self.assertEqual(plan["blocked"], "protected_descendant")
        self.assertFalse(self.workspace.delete_page("p1"))
        self.assertEqual(self.remaining(), ["home", "p1", "p2", "p3", "p4", "tools"])


class PageDeleteFlowTests(_WorkspaceFixture):
    def test_cancel_keeps_every_page(self):
        self.answers.append(False)

        self.assertFalse(self.workspace.delete_page("p1"))

        self.assertEqual(self.remaining(), ["home", "p1", "p2", "p3", "p4"])
        self.assertEqual(self.closed, [])
        self.assertEqual(len(self.plans), 1)

    def test_delete_removes_the_subtree_and_notifies_each_page_from_child_to_parent(self):
        self.workspace.delete_page("p1")

        self.assertEqual(self.closed, ["p3", "p2", "p1"])
        self.assertEqual(self.remaining(), ["home", "p4"])
        self.assertEqual(self.plans[0]["count"], 3)

    def test_the_surviving_pages_keep_their_order_and_records(self):
        before = [spec.title for page_id, spec in self.workspace.page_specs.items() if page_id != "p1"]

        self.workspace.delete_page("p1")

        self.assertEqual(
            [spec.title for spec in self.workspace.page_specs.values()], ["原始视图", "D 组"]
        )
        self.assertEqual(before, ["原始视图", "B 组", "C 组", "D 组"])

    def test_deleting_the_ancestor_of_the_current_page_falls_back_to_the_nearest_parent(self):
        self.workspace.activate_page("p3")

        self.workspace.delete_page("p2")

        self.assertEqual(self.workspace.current_page_id, "p1")
        self.assertEqual(self.remaining(), ["home", "p1", "p4"])

    def test_deleting_a_top_level_branch_falls_back_to_home(self):
        self.workspace.activate_page("p2")

        self.workspace.delete_page("p1")

        self.assertEqual(self.workspace.current_page_id, HOME_ID)

    def test_deleting_another_branch_keeps_the_current_page(self):
        self.workspace.activate_page("p1")

        self.workspace.delete_page("p4")

        self.assertEqual(self.workspace.current_page_id, "p1")
        self.assertEqual(self.closed, ["p4"])

    def test_deleting_a_leaf_keeps_the_current_page(self):
        self.workspace.activate_page("p4")

        self.workspace.delete_page("p3")

        self.assertEqual(self.workspace.current_page_id, "p4")
        self.assertEqual(self.remaining(), ["home", "p1", "p2", "p4"])

    def test_delete_drops_the_page_from_the_saved_order(self):
        self.workspace.delete_page("p2")
        self.assertNotIn("p2", self.workspace._tab_order)
        self.assertNotIn("p2", self.workspace.page_tree._items)
        self.assertEqual(
            self.workspace.page_tree.visible_page_ids(), [HOME_ID, "p1", "p4"]
        )

    def test_delete_never_touches_the_pages_that_were_not_selected(self):
        titles = {page_id: spec.title for page_id, spec in self.workspace.page_specs.items()}

        self.workspace.delete_page("p4")

        for page_id, title in titles.items():
            if page_id == "p4":
                continue
            self.assertEqual(self.workspace.page_specs[page_id].title, title)

    def test_close_current_page_uses_the_same_rule_and_confirmation(self):
        self.workspace.activate_page("p1")
        self.answers.append(False)

        self.workspace.close_current_page()

        self.assertEqual(self.plans[0]["page_id"], "p1")
        self.assertEqual(self.plans[0]["count"], 3)
        self.assertEqual(self.remaining(), ["home", "p1", "p2", "p3", "p4"])

        self.answers.append(True)
        self.workspace.close_current_page()
        self.assertEqual(self.remaining(), ["home", "p4"])
        self.assertEqual(self.workspace.current_page_id, HOME_ID)

    def test_reset_to_home_never_asks_for_confirmation(self):
        self.answers.append(False)

        self.workspace.reset_to_home()

        self.assertEqual(self.plans, [])
        self.assertEqual(self.remaining(), ["home"])
        self.assertEqual(self.workspace.current_page_id, HOME_ID)
        self.assertEqual(sorted(self.closed), ["p1", "p2", "p3", "p4"])
        # 子页先于父页清理。
        self.assertLess(self.closed.index("p2"), self.closed.index("p1"))
        self.assertLess(self.closed.index("p3"), self.closed.index("p2"))

    def test_reset_to_home_keeps_working_without_any_handler(self):
        self.workspace.set_delete_confirm_handler(None)

        self.workspace.reset_to_home()

        self.assertEqual(self.remaining(), ["home"])


class _FakeButton:
    def __init__(self, role, box):
        self.role = role
        self.box = box

    def setText(self, text):
        self.box.labels[self.role] = text


class _MessageBoxStub:
    """替身消息框：记下 ``_create_message_box`` 落下来的文案与按钮。"""

    instances = []
    answer = QMessageBox.No

    def __init__(self, parent=None):
        type(self).instances.append(self)
        self.parent = parent
        self.title = ""
        self.text = ""
        self.icon = None
        self.buttons = None
        self.default_button = None
        self.escape_button = None
        self.labels = {}

    def setWindowTitle(self, title):
        self.title = title

    def setIcon(self, icon):
        self.icon = icon

    def setText(self, text):
        self.text = text

    def setStandardButtons(self, buttons):
        self.buttons = buttons

    def setDefaultButton(self, button):
        self.default_button = button

    def setEscapeButton(self, button):
        self.escape_button = button

    def button(self, role):
        return _FakeButton(role, self)

    def exec_(self):
        return type(self).answer


class PageDeleteConfirmationTests(unittest.TestCase):
    """主窗口的确认弹窗：文案、默认按钮与受保护分支的提示。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        _MessageBoxStub.instances = []
        _MessageBoxStub.answer = QMessageBox.No
        self.boxes = patch(
            "bandscope.app.refactored_app.QuickCloseMessageBox", _MessageBoxStub
        )
        self.boxes.start()
        self.addCleanup(self.boxes.stop)
        self.analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        self.analyzer._show_message = Mock()

    def last_box(self):
        return _MessageBoxStub.instances[-1]

    def test_the_question_names_the_target_and_the_total(self):
        self.analyzer._confirm_page_delete(
            {"title": "A 组", "count": 3, "blocked": None}
        )

        box = self.last_box()
        self.assertEqual(box.title, "删除页面")
        self.assertIn("A 组", box.text)
        self.assertIn("3", box.text)
        self.assertEqual(box.default_button, QMessageBox.No)
        self.assertEqual(box.escape_button, QMessageBox.No)
        self.assertEqual(box.labels[QMessageBox.Yes], "删除")
        self.assertEqual(box.labels[QMessageBox.No], "取消")

    def test_cancelling_returns_false_and_confirming_returns_true(self):
        plan = {"title": "A 组", "count": 3, "blocked": None}

        self.assertFalse(self.analyzer._confirm_page_delete(plan))

        _MessageBoxStub.answer = QMessageBox.Yes
        self.assertTrue(self.analyzer._confirm_page_delete(plan))

    def test_a_single_page_says_nothing_about_derived_pages(self):
        self.analyzer._confirm_page_delete({"title": "D 组", "count": 1, "blocked": None})

        self.assertIn("D 组", self.last_box().text)
        self.assertNotIn("派生页面", self.last_box().text)

    def test_a_blocked_branch_is_reported_instead_of_asked(self):
        answer = self.analyzer._confirm_page_delete(
            {"title": "A 组", "count": 2, "blocked": "protected_descendant"}
        )

        self.assertFalse(answer)
        self.assertEqual(_MessageBoxStub.instances, [])
        self.analyzer._show_message.assert_called_once()


class PageShortcutFocusTests(unittest.TestCase):
    """页面树 / 编辑框持有焦点时，方向键不落回时间轴逐帧。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tree = QTreeWidget()
        self.line_edit = QLineEdit()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 50)
        self.slider.setValue(10)
        self.analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        self.analyzer.timeline_bar = SimpleNamespace(slider_time=self.slider)
        self.addCleanup(self.tree.deleteLater)
        self.addCleanup(self.line_edit.deleteLater)
        self.addCleanup(self.slider.deleteLater)

    def test_the_page_tree_takes_the_arrow_keys(self):
        self.assertTrue(self.analyzer._focus_blocks_frame_step(self.tree))

    def test_text_editing_and_other_sliders_still_block_frame_steps(self):
        self.assertTrue(self.analyzer._focus_blocks_frame_step(self.line_edit))
        self.assertTrue(
            self.analyzer._focus_blocks_frame_step(QSlider(Qt.Horizontal))
        )

    def test_the_time_slider_itself_does_not_block(self):
        self.assertFalse(self.analyzer._focus_blocks_frame_step(self.slider))

    def test_the_default_path_reads_the_real_focus(self):
        with patch.object(QApplication, "focusWidget", return_value=self.tree):
            self.assertTrue(self.analyzer._focus_blocks_frame_step())

    def test_arrow_keys_with_the_tree_focused_do_not_step_frames(self):
        with patch.object(QApplication, "focusWidget", return_value=self.tree):
            for key in (Qt.Key_Left, Qt.Key_Right):
                with self.subTest(key=key):
                    event = QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier)
                    self.assertFalse(self.analyzer._handle_frame_step_shortcut(event))
            self.assertEqual(self.slider.value(), 10)

    def test_arrow_keys_outside_the_tree_still_step_frames(self):
        with patch.object(QApplication, "focusWidget", return_value=None):
            event = QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier)
            self.assertTrue(self.analyzer._handle_frame_step_shortcut(event))
            self.assertEqual(self.slider.value(), 11)


class PageNumberShortcutTests(_WorkspaceFixture):
    """数字切页按页面树中可见行的顺序。"""

    def setUp(self):
        super().setUp()
        self.analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        self.analyzer.left_workspace = self.workspace
        self.analyzer._preserve_control_tab_on_next_page_sync = False

    def test_visible_order_follows_the_tree(self):
        self.assertEqual(
            self.analyzer._ordered_left_workspace_page_ids(),
            [HOME_ID, "p1", "p2", "p3", "p4"],
        )

        self.workspace.move_page_within_siblings("p4", "p1", True)

        self.assertEqual(
            self.analyzer._ordered_left_workspace_page_ids(),
            [HOME_ID, "p4", "p1", "p2", "p3"],
        )

    def test_hidden_pages_are_not_numbered(self):
        self.workspace.page_tree.search_edit.setText("C 组")

        self.assertEqual(
            self.analyzer._ordered_left_workspace_page_ids(), [HOME_ID, "p1", "p2", "p3"]
        )

    def test_the_number_shortcut_activates_the_page_in_that_position(self):
        selected = []
        self.analyzer._activate_left_workspace_page_from_shortcut = selected.append

        self.analyzer._select_left_workspace_page_by_index(2)

        self.assertEqual(selected, ["p2"])


if __name__ == "__main__":
    unittest.main()
