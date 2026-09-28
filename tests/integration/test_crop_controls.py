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

    def test_toggle_buttons_open_and_close_the_range_window(self):
        parent, controller, host = self._region_host()
        popup = controller.popup
        host.btn_tb_crop.setGeometry(200, 100, 80, 32)
        self.assertFalse(popup.isVisible())

        host.btn_tb_crop.click()
        self.assertTrue(controller.enabled)
        self.assertTrue(popup.isVisible())
        # 窗口落在按钮正下方并居中对齐，且是独立浮窗而不是模态弹出层。
        self.assertTrue(popup.windowFlags() & Qt.Tool)
        self.assertEqual(popup.pos(), QPoint(200 + (80 - popup.width()) // 2, 100 + 32 + 8))
        self.assertEqual(popup.apply_button.text(), "裁剪")

        # 切到裁空：窗口留在原地，只换标题与动作。
        host.btn_tb_erase.click()
        self.assertTrue(popup.isVisible())
        self.assertEqual(popup.apply_button.text(), "裁空")

        host.btn_tb_erase.click()
        self.assertFalse(controller.enabled)
        self.assertFalse(popup.isVisible())
        parent.close()

    def test_range_window_keeps_user_position_and_follows_toggle_state(self):
        parent, controller, host = self._region_host()
        popup = controller.popup
        host.btn_tb_crop.setGeometry(200, 100, 80, 32)
        host.btn_tb_crop.click()
        start = popup.pos()

        self.assertTrue(self._drag(popup, QPoint(60, 40)))
        self.assertEqual(popup.pos(), start + QPoint(60, 40))

        # 关掉再打开回到用户拖到的位置，而不是按钮下方。
        host.btn_tb_crop.click()
        host.btn_tb_crop.click()
        self.assertEqual(popup.pos(), start + QPoint(60, 40))

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
