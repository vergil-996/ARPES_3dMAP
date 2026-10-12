# -*- coding: utf-8 -*-
"""直接删除、批量删除与宿主清理：合并完整分支并保持页面切换与关闭顺序。

删除连带整支派生页（搜索隐藏的也算）；当前页被删时只回退一次到最近存活祖先，
删别的分支不切页。宿主收到关闭通知时全部待删记录已经摘除。方向键与数字键
在页面树持有焦点时归树使用。
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication, QLineEdit, QSlider, QTreeWidget

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.exporting.curve_presentation import publication_session
from bandscope.ui import theme
from bandscope.ui.axis_title_controller import AxisTitleState, axis_title_key
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
        self.workspace.page_closed.connect(self.closed.append)

    def tearDown(self):
        self.workspace.hide()
        self.workspace.deleteLater()

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
    def test_delete_removes_the_subtree_and_notifies_each_page_from_child_to_parent(self):
        self.assertTrue(self.workspace.delete_page("p1"))

        self.assertEqual(self.closed, ["p3", "p2", "p1"])
        self.assertEqual(self.remaining(), ["home", "p4"])

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

    def test_close_current_page_directly_removes_the_whole_branch(self):
        self.workspace.activate_page("p1")

        self.workspace.close_current_page()

        self.assertEqual(self.remaining(), ["home", "p4"])
        self.assertEqual(self.workspace.current_page_id, HOME_ID)
        self.assertEqual(self.closed, ["p3", "p2", "p1"])

    def test_header_close_button_directly_removes_the_current_page(self):
        self.workspace.activate_page("p2")

        self.workspace.close_button.click()

        self.assertEqual(self.remaining(), ["home", "p1", "p4"])
        self.assertEqual(self.workspace.current_page_id, "p1")
        self.assertEqual(self.closed, ["p3", "p2"])

    def test_reset_to_home_directly_removes_every_result_page(self):
        self.workspace.reset_to_home()

        self.assertEqual(self.remaining(), ["home"])
        self.assertEqual(self.workspace.current_page_id, HOME_ID)
        self.assertEqual(sorted(self.closed), ["p1", "p2", "p3", "p4"])
        # 子页先于父页清理。
        self.assertLess(self.closed.index("p2"), self.closed.index("p1"))
        self.assertLess(self.closed.index("p3"), self.closed.index("p2"))


class PageBatchDeleteTests(_WorkspaceFixture):
    def test_parent_child_and_duplicate_choices_close_each_page_once(self):
        self.assertTrue(self.workspace.delete_pages(["p1", "p2", "p1", "p3"]))

        self.assertEqual(self.remaining(), ["home", "p4"])
        self.assertEqual(self.closed, ["p3", "p2", "p1"])

    def test_unrelated_branches_and_all_their_descendants_are_removed(self):
        self.workspace.add_page(page_spec("p5", "E 组", source_page_id="p4"))

        self.assertTrue(self.workspace.delete_pages(["p2", "p4"]))

        self.assertEqual(self.remaining(), ["home", "p1"])
        self.assertCountEqual(self.closed, ["p2", "p3", "p4", "p5"])
        self.assertLess(self.closed.index("p3"), self.closed.index("p2"))
        self.assertLess(self.closed.index("p5"), self.closed.index("p4"))

    def test_a_surviving_current_page_is_not_reactivated(self):
        self.workspace.activate_page("p1")
        activated = []
        self.workspace.page_activated.connect(activated.append)
        surviving = {page_id: self.workspace.page_specs[page_id] for page_id in ("home", "p1", "p2")}

        with patch.object(self.workspace, "_refresh_navigation_layout", wraps=self.workspace._refresh_navigation_layout) as refresh:
            self.workspace.delete_pages(["p3", "p4"])

        self.assertEqual(self.workspace.current_page_id, "p1")
        self.assertEqual(activated, [])
        refresh.assert_called_once()
        for page_id, spec in surviving.items():
            self.assertIs(self.workspace.page_specs[page_id], spec)

    def test_a_deleted_current_page_activates_only_its_nearest_surviving_ancestor(self):
        self.workspace.activate_page("p3")
        activated = []
        self.workspace.page_activated.connect(activated.append)

        with patch.object(self.workspace, "_refresh_navigation_layout", wraps=self.workspace._refresh_navigation_layout) as refresh:
            self.workspace.delete_pages(["p2", "p4"])

        self.assertEqual(self.workspace.current_page_id, "p1")
        self.assertEqual(activated, ["p1"])
        refresh.assert_called_once()
        self.assertEqual(self.remaining(), ["home", "p1"])

    def test_selected_ancestors_are_skipped_when_finding_the_fallback(self):
        self.workspace.activate_page("p3")
        activated = []
        self.workspace.page_activated.connect(activated.append)

        self.workspace.delete_pages(["p2", "p1", "p4"])

        self.assertEqual(self.workspace.current_page_id, HOME_ID)
        self.assertEqual(activated, [HOME_ID])
        self.assertEqual(self.remaining(), ["home"])

    def test_protected_choices_do_not_block_other_valid_choices(self):
        self.workspace.add_pinned_page(
            page_spec("tools", "工具页", "control_panel", source_page_id="p1")
        )

        self.assertTrue(self.workspace.delete_pages([HOME_ID, "tools", "p1", "p4"]))

        self.assertEqual(self.remaining(), ["home", "p1", "p2", "p3", "tools"])
        self.assertEqual(self.closed, ["p4"])

    def test_only_protected_or_missing_choices_do_nothing(self):
        self.assertFalse(self.workspace.delete_pages([HOME_ID, "missing"]))
        self.assertEqual(self.remaining(), ["home", "p1", "p2", "p3", "p4"])
        self.assertEqual(self.closed, [])

    def test_missing_and_repeated_ids_are_ignored(self):
        self.assertTrue(self.workspace.delete_pages(["missing", "p4", "p4"]))
        self.assertFalse(self.workspace.delete_pages(["p4", "missing"]))
        self.assertEqual(self.remaining(), ["home", "p1", "p2", "p3"])
        self.assertEqual(self.closed, ["p4"])

    def test_empty_selection_does_nothing(self):
        activated = []
        self.workspace.page_activated.connect(activated.append)

        self.assertFalse(self.workspace.delete_pages([]))

        self.assertEqual(self.remaining(), ["home", "p1", "p2", "p3", "p4"])
        self.assertEqual(self.closed, [])
        self.assertEqual(activated, [])

    def test_search_hidden_descendants_are_still_removed(self):
        self.workspace.page_tree.search_edit.setText("A 组")
        self.assertTrue(self.workspace.page_tree._items["p3"].isHidden())

        self.workspace.delete_pages(["p1", "p4"])

        self.assertEqual(self.remaining(), ["home"])
        self.assertCountEqual(self.closed, ["p1", "p2", "p3", "p4"])

    def test_tree_batch_delete_signal_reaches_the_workspace(self):
        self.workspace.page_tree.pages_delete_requested.emit(["p2", "p4"])

        self.assertEqual(self.remaining(), ["home", "p1"])
        self.assertCountEqual(self.closed, ["p2", "p3", "p4"])
        self.assertNotIn("p2", self.workspace._tab_order)
        self.assertNotIn("p3", self.workspace.activation_history)
        self.assertNotIn("p4", self.workspace.page_tree._items)


class PageDeleteHostCleanupTests(_WorkspaceFixture):
    def test_close_handlers_see_all_removed_records_and_clean_each_page_once(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.left_workspace = self.workspace
        record_snapshots = []

        def observe_records(_page_id=None):
            record_snapshots.append(set(self.workspace.page_specs))

        analyzer.crop_controller = SimpleNamespace(remove_page=Mock(side_effect=observe_records))
        analyzer.refresh_coordinator = SimpleNamespace(cancel_page=Mock(side_effect=observe_records))
        analyzer.plugin_session = SimpleNamespace(cancel_page_analysis=Mock(side_effect=observe_records))
        analyzer._publication_dialog = SimpleNamespace(forget_page=Mock(side_effect=observe_records))
        analyzer._rotation_cache = {"rotation": object()}
        analyzer._computed_volume_cache = {"volume": object()}
        analyzer._second_derivative_volume_cache = object()
        analyzer._axis_prefix_cache = {"page_id": "p2", "prefix": object()}
        analyzer.axis_title_state = AxisTitleState()
        analyzer.active_page_spec = None
        analyzer.last_visual_page_id = "p3"
        analyzer._discard_unreferenced_roi_scopes = Mock(side_effect=observe_records)
        presentation = publication_session(analyzer)
        keys = {}
        for page_id in self.workspace.page_specs:
            presentation.pages[page_id] = object()
            keys[page_id] = axis_title_key(page_id, "2d", ("X", "Y"))
            analyzer.axis_title_state.set_text(keys[page_id], "x", page_id)
        # 主窗口替身未初始化 QObject，通过普通 Python 槽调用真实宿主处理器。
        self.workspace.page_closed.connect(lambda page_id: analyzer.on_result_page_closed(page_id))

        self.workspace.delete_pages(["p2", "p4"])

        self.assertEqual(self.remaining(), ["home", "p1"])
        self.assertTrue(record_snapshots)
        self.assertTrue(all(snapshot == {"home", "p1"} for snapshot in record_snapshots))
        expected_calls = [call(page_id) for page_id in self.closed]
        for handler in (
            analyzer.crop_controller.remove_page,
            analyzer.refresh_coordinator.cancel_page,
            analyzer.plugin_session.cancel_page_analysis,
            analyzer._publication_dialog.forget_page,
        ):
            self.assertEqual(handler.call_args_list, expected_calls)
        self.assertEqual(set(presentation.pages), {"home", "p1"})
        for page_id in ("p2", "p3", "p4"):
            self.assertEqual(analyzer.axis_title_state.texts(keys[page_id]), {})
        self.assertEqual(analyzer.axis_title_state.texts(keys["p1"]), {"x": "p1"})
        self.assertEqual(analyzer._rotation_cache, {})
        self.assertEqual(analyzer._computed_volume_cache, {})
        self.assertIsNone(analyzer._axis_prefix_cache)
        self.assertIsNone(analyzer._second_derivative_volume_cache)
        self.assertNotIn(analyzer.last_visual_page_id, {"p2", "p3", "p4"})


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


class PageFocusIsolationTests(_WorkspaceFixture):
    """树、搜索框与树内改名编辑框都会把方向键留下，不落回时间轴逐帧。"""

    def setUp(self):
        super().setUp()
        self.analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        self.analyzer.timeline_bar = SimpleNamespace(slider_time=QSlider())

    def test_the_tree_rows_hold_the_arrow_keys(self):
        self.assertTrue(
            self.analyzer._focus_blocks_frame_step(self.workspace.page_tree.tree)
        )

    def test_the_search_box_holds_the_arrow_keys(self):
        self.assertTrue(
            self.analyzer._focus_blocks_frame_step(self.workspace.page_tree.search_edit)
        )

    def test_the_inline_rename_editor_holds_the_arrow_keys(self):
        self.assertTrue(self.workspace.page_tree.begin_rename("p1"))

        editor = self.workspace.page_tree._editor
        self.assertTrue(self.analyzer._focus_blocks_frame_step(editor))
        self.workspace.page_tree._close_editor()


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
