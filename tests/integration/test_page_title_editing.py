# -*- coding: utf-8 -*-
"""页面改名：主页与结果页可改，工具页不行；自动名字与用户命名分开记。

覆盖设计意图：改名按 ``page_id`` 生效，不改页面身份/参数/来源关系；重复名
沿用 ``_make_unique_page_title`` 追加编号并排除当前页自身；自动命名只更新
底层默认值，不许覆盖用户命名；换数据后用户命名作废、派生页仍用自动名。
"""
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.app.qt_bootstrap import configure_qt_plugin_path


configure_qt_plugin_path()

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.ui import theme
from bandscope.ui.result_workspace import (
    AnalysisPageSpec,
    PageTitleLabel,
    ResultWorkspace,
)


class _DialogStub:
    """替身改名弹窗：直接给出预定输入。"""

    Accepted = 1
    Rejected = 0
    result = 1
    text = ""

    last = None

    def __init__(self, parent, title):
        type(self).last = self
        self.title = title

    def exec_(self):
        return type(self).result

    def page_title(self):
        return type(self).text

    def deleteLater(self):
        pass


def _spec(page_id, title, kind="axis_integral", **kwargs):
    return AnalysisPageSpec(page_id, title, kind, "test", **kwargs)


class _WorkspaceFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply_siui_palette()

    def setUp(self):
        self.workspace = ResultWorkspace(QWidget())
        self.workspace.resize(900, 600)
        self.home = _spec("home", "原始视图", "home", closeable=False)
        self.workspace.set_home_page(self.home)
        self.updated = []
        self.workspace.page_updated.connect(self.updated.append)

    def tearDown(self):
        self.workspace.close()
        self.workspace.deleteLater()

    def analyzer(self):
        """挂在真实工作区上的主窗口替身：改名逻辑走真实实现。"""
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.left_workspace = self.workspace
        analyzer._update_statusbar = Mock()
        analyzer._update_screenshot_tooltip = Mock()
        return analyzer


class PageTitleSurfaceTests(_WorkspaceFixture):
    def test_every_page_registers_its_creation_title_as_the_auto_name(self):
        page = _spec("p1", "X轴积分_5")
        self.workspace.add_page(page)
        self.assertEqual(page.auto_title, "X轴积分_5")
        self.assertFalse(page.title_overridden)

    def test_rename_shows_in_the_header_hint_and_emits_an_update(self):
        page = _spec("p1", "X轴积分_5")
        self.workspace.add_page(page)
        self.workspace.set_page_title("p1", "费米面")

        self.assertEqual(page.title, "费米面")
        self.assertTrue(page.title_overridden)
        self.assertEqual(self.workspace.page_title.text(), "费米面")
        self.assertIn("费米面", self.workspace.page_title.toolTip())
        self.assertIn("费米面", self.workspace.page_buttons["p1"].hint)
        self.assertEqual(self.updated, ["p1"])

    def test_rename_also_refreshes_the_page_tree(self):
        self.workspace.add_page(_spec("p1", "X轴积分_5", source_page_id="home"))
        self.workspace.popup_page_id = "home"
        # 子控件的 isVisible() 跟着祖先走：树弹层要挂在可见的工作区上。
        self.workspace.show()
        self.workspace.tree_popup.show()
        try:
            self.workspace.set_page_title("p1", "费米面")
            items = []
            root = self.workspace.tree_popup.tree.topLevelItem(0)
            for index in range(root.childCount()):
                items.append(root.child(index).text(0))
            self.assertEqual(items, ["费米面"])
        finally:
            self.workspace.tree_popup.hide()
            self.workspace.hide()

    def test_update_page_refreshes_the_current_header(self):
        page = _spec("p1", "X轴积分_5")
        self.workspace.add_page(page)
        self.workspace.update_page("p1", title="改名后")
        self.assertEqual(self.workspace.page_title.text(), "改名后")

    def test_rename_does_not_touch_identity_params_or_source_relations(self):
        page = _spec("p1", "X轴积分_5", params={"low": 3}, source_page_id="home")
        self.workspace.add_page(page)
        before = (page.page_id, dict(page.params), page.source_page_id, page.page_kind)
        self.workspace.set_page_title("p1", "费米面")
        after = (page.page_id, dict(page.params), page.source_page_id, page.page_kind)
        self.assertEqual(before, after)
        self.assertEqual(self.workspace.page_by_id("p1"), page)

    def test_derived_pages_keep_their_own_names_and_parent_link(self):
        parent = _spec("p1", "X轴积分_5")
        child = _spec("p2", "裁剪 - X轴积分_5", source_page_id="p1")
        self.workspace.add_page(parent)
        self.workspace.add_page(child)
        self.workspace.set_page_title("p1", "费米面")

        self.assertEqual(child.title, "裁剪 - X轴积分_5")
        self.assertEqual(self.workspace._parent_page_id(child), "p1")

    def test_tool_pages_do_not_offer_renaming(self):
        self.workspace.add_page(_spec("tools", "去噪参数", "control_panel"))
        self.assertFalse(self.workspace.page_title.renameable)

        self.workspace.activate_page("home")
        self.assertTrue(self.workspace.page_title.renameable)


class PageTitleMenuTests(_WorkspaceFixture):
    def test_double_click_emits_a_rename_request(self):
        label = PageTitleLabel("标题")
        label.set_rename_state(True, False)
        requested = []
        label.rename_requested.connect(lambda: requested.append(True))
        event = QMouseEvent(
            QMouseEvent.MouseButtonDblClick,
            QPoint(4, 4),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        label.mouseDoubleClickEvent(event)
        self.assertEqual(requested, [True])

    def test_double_click_is_ignored_when_the_page_has_no_rename_entry(self):
        label = PageTitleLabel("标题")
        label.set_rename_state(False, False)
        requested = []
        label.rename_requested.connect(lambda: requested.append(True))
        event = QMouseEvent(
            QMouseEvent.MouseButtonDblClick,
            QPoint(4, 4),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        label.mouseDoubleClickEvent(event)
        self.assertEqual(requested, [])

    def test_header_forwards_the_current_page_id(self):
        self.workspace.add_page(_spec("p1", "X轴积分_5"))
        requested = []
        self.workspace.page_rename_requested.connect(requested.append)
        self.workspace._on_rename_requested()
        self.assertEqual(requested, ["p1"])


class RenameFlowTests(_WorkspaceFixture):
    def setUp(self):
        super().setUp()
        self.page = _spec("p1", "X轴积分_5")
        self.workspace.add_page(self.page)
        self.workspace.add_page(_spec("p2", "Y轴积分_5"))
        self.analyzer = self.analyzer()

    def _rename_to(self, text, *, page_id="p1", accepted=True):
        _DialogStub.result = 1 if accepted else 0
        _DialogStub.text = text
        with patch("bandscope.app.refactored_app.PageRenameDialog", _DialogStub):
            self.analyzer._rename_page(page_id)

    def test_cancelling_keeps_the_original_name(self):
        self._rename_to("费米面", accepted=False)
        self.assertEqual(self.page.title, "X轴积分_5")
        self.assertFalse(self.page.title_overridden)

    def test_blank_input_is_not_submitted(self):
        for text in ("", "   "):
            with self.subTest(text=text):
                self._rename_to(text)
                self.assertEqual(self.page.title, "X轴积分_5")
                self.assertFalse(self.page.title_overridden)

    def test_chinese_names_and_surrounding_spaces(self):
        self._rename_to("  费米面切面  ")
        self.assertEqual(self.page.title, "费米面切面")

    def test_duplicate_names_get_a_number_suffix_excluding_the_page_itself(self):
        self._rename_to("Y轴积分_5")
        self.assertEqual(self.page.title, "Y轴积分_5_2")

        # 用自己的名字重命名：排除自身，不该多出编号
        self._rename_to("Y轴积分_5_2")
        self.assertEqual(self.page.title, "Y轴积分_5_2")

    def test_restore_auto_name_uses_the_latest_default_and_dedupes(self):
        self._rename_to("费米面")
        # 自动命名逻辑在用户改名期间继续更新底层默认值
        self.page.auto_title = "X轴积分_7"
        self.analyzer._restore_page_auto_title("p1")

        self.assertEqual(self.page.title, "X轴积分_7")
        self.assertFalse(self.page.title_overridden)

        self.workspace.update_page("p2", title="X轴积分_7")
        self.page.title_overridden = False
        self.analyzer._restore_page_auto_title("p1")
        self.assertEqual(self.page.title, "X轴积分_7_2")

    def test_auto_naming_does_not_overwrite_a_user_name(self):
        self._rename_to("费米面")
        self.analyzer._apply_auto_page_title(self.page, "X轴积分_9")
        self.assertEqual(self.page.title, "费米面")
        self.assertEqual(self.page.auto_title, "X轴积分_9")
        self.assertTrue(self.page.title_overridden)

    def test_auto_naming_still_updates_untouched_pages(self):
        self.analyzer._apply_auto_page_title(self.page, "X轴积分_9")
        self.assertEqual(self.page.title, "X轴积分_9")
        self.assertFalse(self.page.title_overridden)

    def test_waterfall_style_renaming_keeps_a_user_name(self):
        # 瀑布图步长变化走自动命名：被改过名的页面不该跟着变
        self._rename_to("费米面")
        self.analyzer._apply_auto_page_title(self.page, "EDC瀑布图 [步长 0.02]")
        self.assertEqual(self.page.title, "费米面")

    def test_new_data_load_drops_the_user_name(self):
        self._rename_to("我的原始视图", page_id="home")
        self.assertEqual(self.home.title, "我的原始视图")

        self.analyzer._restore_home_page_title()

        self.assertEqual(self.home.title, "原始视图")
        self.assertFalse(self.home.title_overridden)


if __name__ == "__main__":
    unittest.main()
