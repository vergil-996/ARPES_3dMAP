import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QKeyEvent, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLineEdit, QPushButton, QWidget

from bandscope.ui.crop_controls import CropController
from bandscope.app.crop_integration import CropInteractionMixin
from bandscope.ui.timeline_bar import TimelineBar


class CropControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = QWidget()
        self.controller = CropController(self.parent)
        self.context = {"view": "1d", "x_data": np.arange(5.), "y_data": np.arange(5.), "xlabel": "Delay (ps)"}
        self.controller.activate("a", self.context)

    def tearDown(self):
        self.controller.popup.hide()
        self.parent.close()
        self.parent.deleteLater()

    def test_six_fields_use_plot_labels_and_disable_unrelated_axis(self):
        popup = self.controller.popup
        self.assertEqual(len(popup.edits), 6)
        self.assertIn("Delay", popup.labels[0].text())
        self.assertIn("Intensity", popup.labels[2].text())
        self.assertTrue(all(edit.isEnabled() for edit in popup.edits[:4]))
        self.assertFalse(any(edit.isEnabled() for edit in popup.edits[4:]))
        self.assertEqual(popup.apply_button.text(), "裁剪")

    def test_valid_edits_emit_once_and_invalid_edits_never_apply(self):
        changes, applications = [], []
        self.controller.selection_changed.connect(lambda: changes.append(True))
        self.controller.apply_requested.connect(lambda: applications.append(True))
        self.controller.popup.edits[0].setText("1")
        self.assertTrue(self.controller.commit_edits())
        self.assertEqual(changes, [True])
        self.assertEqual(self.controller.selection.bounds[0], 1)
        self.controller.popup.edits[1].setText("nan")
        self.controller._apply()
        self.assertFalse(applications)
        self.assertEqual(self.controller.selection.bounds[1], 4)
        self.assertTrue(self.controller.popup.error_label.text())

    def test_drafts_are_per_page_while_mode_is_global(self):
        self.controller.enabled = True
        original = replace(self.controller.selection, bounds=(1, 3, 1, 3))
        self.controller.set_selection(original)
        self.controller.activate("b", self.context)
        self.assertEqual(self.controller.selection.bounds, (0, 4, 0, 4))
        self.controller.activate("a", self.context, e_flip=True)
        self.assertEqual(self.controller.selection.bounds, original.bounds)
        self.assertTrue(self.controller.selection.e_flip)
        self.assertTrue(self.controller.enabled)

    def test_multiple_selections_are_ordered_and_active_id_is_restored_per_page(self):
        first = self.controller.active_selection_id
        original = replace(self.controller.selection, bounds=(1, 3, 1, 3))
        self.controller.set_selection(original, notify=False)
        second = self.controller.add_selection()
        self.assertIsNotNone(first)
        self.assertNotEqual(first, second)
        self.assertEqual(self.controller.active_selection_id, second)
        self.assertEqual(
            [selection_id for selection_id, _ in self.controller.selections_for_page()],
            [first, second],
        )

        self.assertTrue(self.controller.activate_selection(first, notify=False))
        self.controller.activate("b", self.context)
        other = self.controller.active_selection_id
        self.assertEqual(len(self.controller.selections_for_page("b")), 1)
        self.controller.add_selection()
        self.controller.activate("a", self.context)

        self.assertEqual(self.controller.active_selection_id, first)
        self.assertEqual(self.controller.selection, original)
        self.assertEqual(len(self.controller.selections_for_page("a")), 2)
        self.assertNotEqual(other, self.controller.active_selection_id)
        self.controller.activate("b", self.context)
        self.assertEqual(len(self.controller.selections_for_page()), 2)
        self.assertNotEqual(self.controller.active_selection_id, other)

    def test_add_activate_and_update_by_id_only_change_their_target(self):
        first = self.controller.active_selection_id
        original_first = self.controller.selection
        second = self.controller.add_selection()
        original_second = self.controller.selection
        changes = []
        self.controller.selection_changed.connect(lambda: changes.append(True))
        updated_first = replace(original_first, bounds=(1, 2, 1, 2))

        self.assertTrue(self.controller.update_selection(first, updated_first))
        self.assertEqual(changes, [True])
        self.assertEqual(self.controller.selection, original_second)
        self.assertEqual(dict(self.controller.selections_for_page())[first], updated_first)
        self.assertTrue(self.controller.activate_selection(second, notify=False))
        self.assertTrue(self.controller.activate_selection(first))
        self.assertEqual(self.controller.selection, updated_first)
        self.assertEqual(self.controller.popup.edits[0].text(), "1")
        self.assertFalse(self.controller.activate_selection(-1, notify=False))
        self.assertFalse(self.controller.update_selection(-1, original_first, notify=False))

    def test_operation_popup_and_legacy_set_selection_only_edit_active_item(self):
        first = self.controller.active_selection_id
        second = self.controller.add_selection()
        self.controller.set_operation("erase")
        drafts = dict(self.controller.selections_for_page())
        self.assertEqual(drafts[first].operation, "crop")
        self.assertEqual(drafts[second].operation, "erase")

        self.controller.popup.edits[0].setText("1.5")
        self.assertTrue(self.controller.commit_edits())
        drafts = dict(self.controller.selections_for_page())
        self.assertEqual(drafts[second].bounds[0], 1.5)
        self.assertEqual(drafts[first].bounds[0], 0)
        self.controller.activate_selection(first, notify=False)
        replacement = replace(self.controller.selection, bounds=(2, 3, 2, 3))
        self.controller.set_selection(replacement, notify=False)
        drafts = dict(self.controller.selections_for_page())
        self.assertEqual(drafts[first], replacement)
        self.assertEqual(drafts[second].bounds[0], 1.5)
        self.assertEqual(drafts[second].operation, "erase")

    def test_activate_none_preserves_drafts_and_add_uses_cached_context(self):
        context = {
            "view": "2d",
            "data": np.ones((11, 11)),
            "slice_info": {"axis": 1},
            "coords": {"X": np.arange(11.), "E": np.arange(11.)},
        }
        self.controller.activate("image", context, e_flip=True)
        first = self.controller.active_selection_id
        original = self.controller.selection
        self.assertEqual(original.bounds, (1, 9, 1, 9))
        for _ in range(3):
            self.controller.activate("image", context, e_flip=True)
            self.assertEqual(len(self.controller.selections_for_page()), 1)
            self.assertEqual(self.controller.active_selection_id, first)

        self.controller.activate("image", None, e_flip=True)
        self.assertEqual(self.controller.selection, original)
        self.assertEqual(self.controller.active_selection_id, first)
        second = self.controller.add_selection()
        additions = [self.controller.add_selection() for _ in range(5)]
        selections = self.controller.selections_for_page()
        self.assertEqual(len(selections), 7)
        self.assertTrue(all(selection.e_flip for _, selection in selections))
        self.assertEqual(selections[0][1].bounds, (1, 9, 1, 9))
        self.assertEqual(dict(selections)[second].bounds, (2, 8, 2, 8))
        for previous, current in zip(selections[1:-1], selections[2:]):
            self.assertLess(current[1].bounds[0], 4)
            self.assertGreater(current[1].bounds[0], previous[1].bounds[0])
            self.assertLess(current[1].bounds[1], previous[1].bounds[1])
        self.assertTrue(all(selection_id is not None for selection_id in additions))

        self.controller.activate("missing", None)
        self.assertIsNone(self.controller.selection)
        self.assertEqual(len(self.controller.selections_for_page("image")), 7)
        self.controller.activate("image", None)
        self.assertEqual(self.controller.active_selection_id, selections[-1][0])
        unflipped = self.controller.add_selection(e_flip=False)
        self.assertFalse(dict(self.controller.selections_for_page())[unflipped].e_flip)

    def test_remove_page_and_clear_remove_all_ids_and_drafts(self):
        first = self.controller.active_selection_id
        self.controller.add_selection()
        self.controller.activate("b", self.context)
        other = self.controller.active_selection_id
        other_second = self.controller.add_selection()
        self.controller.remove_page("a")
        self.assertEqual(self.controller.selections_for_page("a"), ())
        self.assertEqual(self.controller.active_selection_id, other_second)
        self.assertEqual(len(self.controller.selections_for_page("b")), 2)

        self.controller.remove_page("b")
        self.assertEqual(self.controller.selections_for_page(), ())
        self.assertIsNone(self.controller.active_selection_id)
        self.assertIsNone(self.controller.selection)
        self.controller.activate("a", self.context)
        self.controller.activate("b", self.context)
        self.controller.clear()
        self.assertEqual(self.controller.selections_for_page("a"), ())
        self.assertEqual(self.controller.selections_for_page("b"), ())
        self.assertIsNone(self.controller.active_selection_id)
        self.assertIsNone(self.controller.selection)
        self.assertIsNone(self.controller.page_id)
        self.assertNotEqual(first, other)

    def test_popup_clamps_to_screen_and_escape_preserves_draft(self):
        popup = self.controller.popup
        screen = self.app.primaryScreen().availableGeometry()
        popup.show_at(screen.bottomRight())
        self.assertLessEqual(popup.geometry().right(), screen.right())
        self.assertLessEqual(popup.geometry().bottom(), screen.bottom())
        selection = self.controller.selection
        QTest.keyClick(popup.edits[0], Qt.Key_Escape)
        self.assertFalse(popup.isVisible())
        self.assertEqual(self.controller.selection, selection)

    def test_canvas_bar_state_has_no_legacy_crop_widgets_or_serialized_mode(self):
        bar = TimelineBar()
        self.assertFalse(hasattr(bar, "switch_coord"))
        self.assertFalse(hasattr(bar, "btn_cut"))
        self.assertNotIn("switch_coord", bar.export_state())
        self.assertNotIn("slice_values", bar.export_state())
        self.assertTrue(hasattr(bar, "edit_rotation"))
        bar.deleteLater()

    def _region_host(self):
        """最小宿主替身：只提供 _toggle_region_mode 走到的属性。"""
        parent = QWidget()
        controller = CropController(parent)
        controller.activate("a", self.context)
        host = SimpleNamespace(
            crop_controller=controller,
            btn_tb_crop=QPushButton(),
            btn_tb_erase=QPushButton(),
            _can_show_interactive_box=lambda: False,
            _restore_volume_opacity_if_dimmed=Mock(),
            _clear_interactive_box=Mock(),
            _refresh_axis_crop_interaction=Mock(),
            left_workspace=SimpleNamespace(current_spec=lambda: None),
            timeline_bar=SimpleNamespace(switch_flip=SimpleNamespace(isChecked=lambda: False)),
            current_render_context=None,
            _update_crop_cursor=Mock(),
            plotter=SimpleNamespace(render=Mock()),
        )
        for button in (host.btn_tb_crop, host.btn_tb_erase):
            button.setCheckable(True)
        host.btn_tb_crop.toggled.connect(
            lambda checked: CropInteractionMixin._toggle_region_mode(host, "crop", checked)
        )
        host.btn_tb_erase.toggled.connect(
            lambda checked: CropInteractionMixin._toggle_region_mode(host, "erase", checked)
        )
        return parent, controller, host

    def _place_region_button(self, host, target):
        popup = host.crop_controller.popup
        host.btn_tb_crop.setGeometry(
            target.x() - (80 - popup.width()) // 2,
            target.y() - 32 - 8,
            80,
            32,
        )

    def test_toggle_buttons_open_and_close_the_range_window(self):
        parent, controller, host = self._region_host()
        popup = controller.popup
        target = self.app.primaryScreen().availableGeometry().topLeft() + QPoint(10, 10)
        self._place_region_button(host, target)
        self.assertFalse(popup.isVisible())

        host.btn_tb_crop.click()
        self.assertTrue(controller.enabled)
        self.assertTrue(popup.isVisible())
        # 窗口落在按钮正下方并居中对齐，且是独立浮窗而不是模态弹出层。
        self.assertTrue(popup.windowFlags() & Qt.Tool)
        self.assertEqual(popup.pos(), target)
        self.assertEqual(popup.apply_button.text(), "裁剪")

        # 切到裁空：窗口留在原地，只换标题与动作。
        host.btn_tb_erase.click()
        self.assertTrue(popup.isVisible())
        self.assertEqual(popup.apply_button.text(), "裁空")

        host.btn_tb_erase.click()
        self.assertFalse(controller.enabled)
        self.assertFalse(popup.isVisible())
        parent.close()

    def test_crop_and_erase_use_the_same_cross_cursor(self):
        canvas, plotter = QWidget(), QWidget()
        plotter.interactor = plotter
        host = SimpleNamespace(
            crop_controller=self.controller,
            canvas_2d=canvas,
            plotter=plotter,
        )
        self.controller.enabled = True
        for operation in ("crop", "erase"):
            self.controller.set_operation(operation)
            CropInteractionMixin._update_crop_cursor(host)
            self.assertEqual(canvas.cursor().shape(), Qt.CrossCursor)
            self.assertEqual(plotter.cursor().shape(), Qt.CrossCursor)
        canvas.deleteLater()
        plotter.deleteLater()

    def test_range_window_keeps_user_position_and_follows_toggle_state(self):
        parent, controller, host = self._region_host()
        popup = controller.popup
        target = self.app.primaryScreen().availableGeometry().topLeft()
        self._place_region_button(host, target)
        host.btn_tb_crop.click()
        start = popup.pos()
        self.assertEqual(start, target)

        delta = QPoint(20, 20)
        self.assertTrue(self._drag(popup, delta))
        self.assertEqual(popup.pos(), start + delta)

        # 关掉再打开回到用户拖到的位置，而不是按钮下方。
        host.btn_tb_crop.click()
        host.btn_tb_crop.click()
        self.assertEqual(popup.pos(), start + delta)

        # 页面渲染就绪后窗口自动回来；离开可裁剪内容则收起。
        controller.popup.hide()
        CropInteractionMixin._activate_crop_context(host, SimpleNamespace(page_id="a"), self.context)
        self.assertTrue(popup.isVisible())
        CropInteractionMixin._activate_crop_context(host, SimpleNamespace(page_id="a"), None)
        self.assertFalse(popup.isVisible())
        parent.close()

    def _drag(self, popup, delta):
        origin = QPoint(10, 10)
        start = popup.mapToGlobal(origin)
        for event in (
            QMouseEvent(QEvent.MouseButtonPress, origin, start, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier),
            QMouseEvent(QEvent.MouseMove, origin + delta, start + delta, Qt.NoButton, Qt.LeftButton, Qt.NoModifier),
            QMouseEvent(QEvent.MouseButtonRelease, origin + delta, start + delta, Qt.LeftButton, Qt.NoButton, Qt.NoModifier),
        ):
            QApplication.sendEvent(popup, event)
        return True

    def test_event_priority_and_right_click_routing(self):
        canvas, plotter = QWidget(), QWidget()
        plotter.interactor = plotter
        host = SimpleNamespace(crop_controller=self.controller, canvas_2d=canvas, plotter=plotter,
                               btn_tb_crop=SimpleNamespace(setChecked=Mock()), _show_crop_popup=Mock())
        self.controller.enabled = True
        press = QMouseEvent(QEvent.MouseButtonPress, QPoint(2, 3), Qt.RightButton, Qt.RightButton, Qt.NoModifier)
        self.assertTrue(CropInteractionMixin._crop_event_filter(host, canvas, press))
        host._show_crop_popup.assert_called_once()
        self.controller.popup.show_at(QPoint(10, 10))
        escape = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
        self.assertTrue(CropInteractionMixin._crop_event_filter(host, self.controller.popup, escape))
        host.btn_tb_crop.setChecked.assert_not_called()
        self.assertTrue(CropInteractionMixin._crop_event_filter(host, canvas, escape))
        host.btn_tb_crop.setChecked.assert_called_once_with(False)
        self.assertFalse(CropInteractionMixin._crop_event_filter(host, QLineEdit(), escape))
        self.controller.enabled = False
        self.assertFalse(CropInteractionMixin._crop_event_filter(host, canvas, press))
        canvas.deleteLater()
        plotter.deleteLater()

    def test_right_click_in_crop_mode_also_asks_for_the_axis_title_menu(self):
        """事件过滤器吞掉了右键，轴标题入口就得由它自己排出来。"""
        canvas, plotter = QWidget(), QWidget()
        plotter.interactor = plotter
        host = SimpleNamespace(
            crop_controller=self.controller,
            canvas_2d=canvas,
            plotter=plotter,
            btn_tb_crop=SimpleNamespace(setChecked=Mock()),
            _show_crop_popup=Mock(),
            _show_axis_title_menu=Mock(),
        )
        self.controller.enabled = True
        try:
            press = QMouseEvent(
                QEvent.MouseButtonPress, QPoint(2, 3), Qt.RightButton, Qt.RightButton, Qt.NoModifier
            )
            self.assertTrue(CropInteractionMixin._crop_event_filter(host, canvas, press))
            # 菜单排在过滤器之外，跑一轮事件循环才会被调用。
            QApplication.processEvents()
            host._show_axis_title_menu.assert_called_once()
            self.assertIs(host._show_axis_title_menu.call_args[0][0], canvas)
        finally:
            self.controller.enabled = False
            canvas.deleteLater()
            plotter.deleteLater()

    def test_host_without_the_menu_ability_is_left_alone(self):
        """只填了部分属性的测试替身不该因为少了菜单方法而报错。"""
        canvas = QWidget()
        canvas.interactor = canvas
        host = SimpleNamespace(
            crop_controller=self.controller,
            canvas_2d=canvas,
            plotter=canvas,
            btn_tb_crop=SimpleNamespace(setChecked=Mock()),
            _show_crop_popup=Mock(),
        )
        self.controller.enabled = True
        try:
            press = QMouseEvent(
                QEvent.MouseButtonPress, QPoint(2, 3), Qt.RightButton, Qt.RightButton, Qt.NoModifier
            )
            self.assertTrue(CropInteractionMixin._crop_event_filter(host, canvas, press))
            QApplication.processEvents()
        finally:
            self.controller.enabled = False
            canvas.deleteLater()


if __name__ == "__main__":
    unittest.main()
