import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtGui import QColor, QImage, QTransform
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QFrame, QPushButton, QVBoxLayout, QWidget

from bandscope.app.qt_bootstrap import configure_qt_plugin_path

configure_qt_plugin_path()

from bandscope.ui import theme
from bandscope.ui.canvas_overlay import CanvasToolOverlay


class CanvasToolOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_overlay(self):
        parent = QWidget()
        parent.resize(800, 600)
        buttons = [QPushButton(name) for name in ("裁剪", "裁空", "导出结果")]
        buttons[0].setCheckable(True)
        buttons[1].setCheckable(True)
        overlay = CanvasToolOverlay(parent, *buttons)
        self.addCleanup(parent.deleteLater)
        return parent, overlay, buttons

    def test_existing_actions_move_into_the_canvas_tool_group(self):
        parent, overlay, buttons = self.make_overlay()
        crop_button, erase_button, export_button = buttons
        self.assertIs(export_button.parentWidget(), overlay)
        self.assertIs(overlay.selection_button.parentWidget(), overlay)
        self.assertIs(overlay.selection_panel.parentWidget(), parent)
        self.assertIsInstance(overlay.selection_panel, QFrame)
        self.assertIsInstance(overlay.selection_panel.layout(), QVBoxLayout)
        self.assertFalse(overlay.selection_panel.windowFlags() & Qt.Popup)
        self.assertTrue(overlay.selection_button.isCheckable())
        self.assertEqual(overlay.selection_button.accessibleName(), "框选范围")
        for button, name, icon, letter in (
            (crop_button, "裁剪", "ic_fluent_crop_regular", None),
            (erase_button, "裁空", "ic_fluent_eraser_regular", None),
            (overlay.edc_button, "生成 EDC", None, "E"),
            (overlay.slice_dos_button, "切片内强度积分", None, "T"),
            (overlay.new_region_button, "新增框选范围", "ic_fluent_add_regular", None),
        ):
            expected_parent = (
                overlay if button in (crop_button, erase_button) else overlay.selection_panel
            )
            self.assertIs(button.parentWidget(), expected_parent)
            self.assertEqual(button.text(), "")
            self.assertEqual(button.accessibleName(), name)
            self.assertEqual(button.toolTip(), name)
            self.assertEqual(button.property("fluentIconName"), icon)
            self.assertEqual(button.property("iconLetter"), letter)
            self.assertFalse(button.icon().isNull())
            self.assertEqual(button.size().width(), 28)
            self.assertEqual(button.size().height(), 26)
        self.assertEqual(export_button.accessibleName(), "导出结果")
        self.assertEqual(export_button.size().width(), 28)
        self.assertEqual(export_button.size().height(), 26)
        self.assertEqual(
            overlay.selection_button.property("fluentIconName"),
            "ic_fluent_select_object_regular",
        )
        self.assertFalse(overlay.selection_button.icon().isNull())

    def test_letter_tool_icons_have_visible_pixels_after_theme_refresh(self):
        parent, overlay, _buttons = self.make_overlay()

        theme.apply_widget_theme(parent)

        for button, letter in (
            (overlay.edc_button, "E"),
            (overlay.slice_dos_button, "T"),
        ):
            self.assertEqual(button.property("iconLetter"), letter)
            image = button.icon().pixmap(16, 16).toImage()
            self.assertTrue(any(
                image.pixelColor(x, y).alpha() > 0
                for x in range(image.width())
                for y in range(image.height())
            ))

    def test_selection_button_toggles_panel_and_emits_state_changes(self):
        parent, overlay, _buttons = self.make_overlay()
        parent.show()
        states = []
        overlay.selection_toggled.connect(states.append)
        overlay.selection_button.click()
        self.assertTrue(overlay.selection_button.isChecked())
        self.assertTrue(overlay.selection_panel.isVisible())
        overlay.selection_button.click()
        self.assertFalse(overlay.selection_button.isChecked())
        self.assertFalse(overlay.selection_panel.isVisible())
        self.assertEqual(states, [True, False])

        overlay.set_selection_panel_visible(True)
        overlay.set_selection_panel_visible(True)
        self.assertTrue(overlay.selection_panel.isVisible())
        self.assertEqual(states, [True, False, True])
        overlay.close_selection_panel()
        overlay.close_selection_panel()
        self.assertFalse(overlay.selection_panel.isVisible())
        self.assertEqual(states, [True, False, True, False])

        overlay.set_selection_panel_visible(True)
        overlay.hide()
        self.assertFalse(overlay.selection_panel.isVisible())
        self.assertFalse(overlay.selection_button.isChecked())
        self.assertEqual(states, [True, False, True, False, True, False])

    def test_primary_and_secondary_actions_emit_from_real_button_clicks(self):
        _parent, overlay, buttons = self.make_overlay()
        crop_button, erase_button, _export_button = buttons
        actions = []
        crop_button.clicked.connect(lambda: actions.append("crop"))
        erase_button.clicked.connect(lambda: actions.append("erase"))
        overlay.edc_requested.connect(lambda: actions.append("edc"))
        overlay.slice_dos_requested.connect(lambda: actions.append("slice_dos"))
        overlay.new_region_requested.connect(lambda: actions.append("new"))
        self.assertFalse(overlay.edc_button.isCheckable())
        self.assertFalse(overlay.slice_dos_button.isCheckable())
        self.assertFalse(overlay.new_region_button.isCheckable())
        for button in (
            crop_button,
            erase_button,
            overlay.edc_button,
            overlay.slice_dos_button,
            overlay.new_region_button,
        ):
            button.click()
        self.assertEqual(actions, ["crop", "erase", "edc", "slice_dos", "new"])

    def test_crop_and_erase_keep_their_toggled_behavior(self):
        _parent, overlay, buttons = self.make_overlay()
        states = {"crop": [], "erase": []}
        for key, button in zip(("crop", "erase"), buttons[:2]):
            self.assertTrue(button.isCheckable())
            button.toggled.connect(lambda checked, key=key: states[key].append(checked))
            button.click()
            self.assertTrue(button.isChecked())
            button.click()
            self.assertFalse(button.isChecked())
        self.assertEqual(states, {"crop": [True, False], "erase": [True, False]})

    def test_overlay_uses_a_vertical_tool_group(self):
        _parent, overlay, _buttons = self.make_overlay()
        overlay.set_3d_active(True)
        self.assertIsInstance(overlay.layout(), QVBoxLayout)
        self.assertIsInstance(overlay.preset_container.layout(), QVBoxLayout)
        self.assertGreater(overlay.height(), overlay.width())

    def test_overlay_stays_inside_the_right_edge_after_resize(self):
        parent, overlay, _buttons = self.make_overlay()
        parent.show()
        overlay.set_3d_active(True)
        self.assertEqual(overlay.x() + overlay.width(), parent.width() - 12)
        self.assertEqual(overlay.y(), 12)
        parent.resize(420, 360)
        overlay.reposition()
        self.assertEqual(overlay.x() + overlay.width(), parent.width() - 12)
        parent.resize(640, 600)
        overlay.reposition()
        self.assertFalse(overlay.preset_container.isHidden())
        self.assertEqual(overlay.crop_button.height(), 26)
        self.assertEqual(overlay.x() + overlay.width(), parent.width() - 12)
        parent.resize(640, 280)
        overlay.reposition()
        self.assertTrue(overlay.preset_container.isHidden())
        self.assertEqual(overlay.crop_button.height(), 22)
        self.assertEqual(overlay.erase_button.height(), 22)
        self.assertFalse(overlay.reset_button.isHidden())
        self.assertLessEqual(overlay.y() + overlay.height(), parent.height() - 12)
        overlay.install_path_button(QPushButton("折线路径切片"))
        overlay.set_path_available(True)
        overlay.set_3d_active(False)
        overlay.set_return_active(True)
        parent.resize(640, 248)
        overlay.reposition()
        self.assertLessEqual(overlay.y() + overlay.height(), parent.height())
        self.assertFalse(overlay.path_button.isHidden())

        parent.resize(640, 218)
        overlay.reposition()
        self.assertTrue(overlay.reset_button.isHidden())
        self.assertLessEqual(overlay.y() + overlay.height(), parent.height())
        remaining_buttons = (
            overlay.switch_axes,
            overlay.switch_flip,
            overlay.rotation_button,
            overlay.path_button,
            overlay.selection_button,
            overlay.crop_button,
            overlay.erase_button,
            overlay.export_button,
            overlay.focus_button,
        )
        for button in remaining_buttons:
            self.assertFalse(button.isHidden(), button.accessibleName())
            top = button.mapTo(parent, QPoint(0, 0)).y()
            bottom = button.mapTo(parent, QPoint(0, button.height())).y()
            self.assertGreaterEqual(top, 0, button.accessibleName())
            self.assertLessEqual(bottom, parent.height(), button.accessibleName())

        parent.resize(640, 600)
        overlay.reposition()
        self.assertFalse(overlay.reset_button.isHidden())
        self.assertEqual(overlay.reset_button.height(), 26)

    def test_selection_panel_repositions_left_of_button_and_stays_in_parent(self):
        parent, overlay, _buttons = self.make_overlay()
        parent.show()
        overlay.reposition()
        overlay.set_selection_panel_visible(True)
        anchor = overlay.selection_button.mapTo(parent, QPoint(0, 0))
        panel = overlay.selection_panel
        self.assertLessEqual(panel.x() + panel.width() + 8, anchor.x())
        self.assertGreaterEqual(panel.x(), 0)
        self.assertGreaterEqual(panel.y(), 0)
        self.assertLessEqual(panel.x() + panel.width(), parent.width())
        self.assertLessEqual(panel.y() + panel.height(), parent.height())

        parent.resize(360, 90)
        overlay.reposition()
        anchor = overlay.selection_button.mapTo(parent, QPoint(0, 0))
        self.assertLessEqual(panel.x() + panel.width() + 8, anchor.x())
        self.assertGreaterEqual(panel.x(), 0)
        self.assertGreaterEqual(panel.y(), 0)
        self.assertLessEqual(panel.x() + panel.width(), parent.width())
        self.assertLessEqual(panel.y() + panel.height(), parent.height())
        self.assertEqual(panel.layout().spacing(), 0)

    def test_rounded_corners_are_transparent(self):
        _parent, overlay, _buttons = self.make_overlay()
        image = QImage(overlay.size(), QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        visible = overlay.rect().adjusted(2, 2, -2, -2)
        # QWidget.render 将可见区域的左上角放在 targetOffset。
        overlay.render(image, visible.topLeft())
        self.assertEqual(overlay.mask().boundingRect(), visible)
        corners = (visible.topLeft(), visible.topRight(), visible.bottomLeft(), visible.bottomRight())
        for point in corners:
            self.assertEqual(image.pixelColor(point).alpha(), 0)
            self.assertFalse(overlay.mask().contains(point))
        border = QPoint(visible.left(), visible.center().y())
        self.assertEqual(image.pixelColor(border), QColor(theme.BORDER_HEX))
        self.assertEqual(image.pixelColor(border - QPoint(1, 0)).alpha(), 0)

    def test_pixels_inside_the_window_mask_are_opaque(self):
        parent, overlay, _buttons = self.make_overlay()
        parent.show()
        for scale in (1.0, 1.25):
            with self.subTest(scale=scale):
                image = QImage(overlay.size() * scale, QImage.Format_ARGB32)
                image.setDevicePixelRatio(scale)
                image.fill(Qt.transparent)
                overlay.render(image, overlay.mask().boundingRect().topLeft())
                mask = QTransform.fromScale(scale, scale).map(overlay.mask())
                self.assertFalse(mask.isEmpty())
                for y in range(image.height()):
                    for x in range(image.width()):
                        if mask.contains(QPoint(x, y)):
                            self.assertEqual(image.pixelColor(x, y).alpha(), 255, (x, y))

    def test_native_window_clips_the_overlay_after_show_and_resize(self):
        parent, overlay, _buttons = self.make_overlay()
        parent.show()
        widget_mask = overlay.mask()
        self.assertFalse(widget_mask.isEmpty())
        overlay.hide()
        overlay.setAttribute(Qt.WA_NativeWindow)
        overlay.show()
        window = overlay.windowHandle()
        self.assertIsNotNone(window)
        self.assertTrue(overlay.mask().isEmpty())
        self.assertEqual(window.mask(), widget_mask)

        overlay.resize(overlay.width() + 12, overlay.height() + 24)
        visible = overlay.rect().adjusted(2, 2, -2, -2)
        self.assertTrue(overlay.mask().isEmpty())
        self.assertEqual(window.mask().boundingRect(), visible)
        self.assertFalse(window.mask().contains(visible.bottomRight()))
        self.assertTrue(window.mask().contains(QPoint(visible.right(), visible.center().y())))

    def test_plane_buttons_create_slices_and_3d_returns_to_source(self):
        _parent, overlay, _buttons = self.make_overlay()
        overlay.set_3d_active(True)
        slices = []
        cameras = []
        returned = []
        overlay.slice_requested.connect(slices.append)
        overlay.view_preset_requested.connect(cameras.append)
        overlay.return_3d_requested.connect(lambda: returned.append(True))
        overlay.preset_buttons["xy"].click()
        self.assertEqual(slices, ["xy"])
        self.assertEqual(cameras, [])
        self.assertTrue(overlay.preset_buttons["3d"].isChecked())

        overlay.set_3d_active(False)
        overlay.set_return_active(True)
        overlay.preset_buttons["3d"].click()
        self.assertEqual(returned, [True])

    def test_view_controls_are_checkable_focusable_and_grouped_above_crop(self):
        _parent, overlay, _buttons = self.make_overlay()
        for button, name in (
            (overlay.switch_axes, "显示坐标"),
            (overlay.switch_flip, "图像翻转"),
            (overlay.rotation_button, "Z 轴旋转"),
        ):
            self.assertTrue(button.isCheckable())
            self.assertEqual(button.focusPolicy(), Qt.StrongFocus)
            self.assertEqual(button.accessibleName(), name)
            self.assertIs(button.parentWidget(), overlay.view_container)
        layout = overlay.layout()
        self.assertGreater(layout.indexOf(overlay.view_container), layout.indexOf(overlay.reset_button))
        self.assertLess(layout.indexOf(overlay.view_container), layout.indexOf(overlay.selection_button))
        self.assertLess(layout.indexOf(overlay.selection_button), layout.indexOf(overlay.crop_button))
        self.assertEqual(
            layout.indexOf(overlay.crop_button) + 1,
            layout.indexOf(overlay.erase_button),
        )
        overlay.switch_axes.click()
        overlay.switch_flip.click()
        self.assertTrue(overlay.switch_axes.isChecked())
        self.assertTrue(overlay.switch_flip.isChecked())

    def test_path_button_is_inserted_before_selection_in_the_main_toolbar(self):
        _parent, overlay, _buttons = self.make_overlay()
        overlay.install_path_button(QPushButton("折线路径切片"))
        overlay.set_path_available(True)
        layout = overlay.layout()
        self.assertIs(overlay.path_button.parentWidget(), overlay)
        self.assertEqual(
            layout.indexOf(overlay.path_button) + 1,
            layout.indexOf(overlay.selection_button),
        )
        self.assertLess(layout.indexOf(overlay.selection_button), layout.indexOf(overlay.crop_button))
        self.assertLess(layout.indexOf(overlay.erase_button), layout.indexOf(overlay.export_button))

    def test_rotation_tool_highlight_tracks_visibility_instead_of_angle(self):
        parent, overlay, _buttons = self.make_overlay()
        parent.show()
        overlay.show()
        overlay.reposition()
        overlay.set_rotation_angle(-7.5)
        self.assertFalse(overlay.rotation_button.isChecked())
        self.assertIn("−7.5°", overlay.rotation_button.toolTip())

        overlay.rotation_button.click()
        self.assertTrue(overlay.rotation_panel.isVisible())
        self.assertTrue(overlay.rotation_button.isChecked())
        button_left = overlay.rotation_button.mapToGlobal(QPoint(0, 0)).x()
        self.assertLess(overlay.rotation_panel.x() + overlay.rotation_panel.width(), button_left)
        overlay.rotation_button.click()
        self.assertFalse(overlay.rotation_panel.isVisible())
        self.assertFalse(overlay.rotation_button.isChecked())

        overlay.rotation_button.click()
        QTest.keyClick(overlay.rotation_panel, Qt.Key_Escape)
        self.assertFalse(overlay.rotation_panel.isVisible())
        self.assertFalse(overlay.rotation_button.isChecked())
        overlay.rotation_button.click()
        overlay.rotation_panel.zero_button.click()
        self.assertTrue(overlay.rotation_panel.isVisible())
        self.assertTrue(overlay.rotation_button.isChecked())
        overlay.close_rotation_panel(commit=False)
        overlay.set_rotation_angle(0)
        self.assertFalse(overlay.rotation_button.isChecked())

    def test_suppressed_rotation_click_keeps_hidden_button_unchecked(self):
        parent, overlay, _buttons = self.make_overlay()
        parent.show()
        overlay._suppress_rotation_click = True

        overlay.rotation_button.click()

        self.assertFalse(overlay.rotation_panel.isVisible())
        self.assertFalse(overlay.rotation_button.isChecked())
        self.assertFalse(overlay._suppress_rotation_click)

    @staticmethod
    def _global_rect(widget):
        return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())

    def _assert_panels_do_not_overlap(self, overlay, crop_popup=None):
        panels = [overlay.rotation_panel, overlay.selection_panel]
        if crop_popup is not None:
            panels.append(crop_popup)
        rects = [self._global_rect(panel) for panel in panels if panel.isVisible()]
        for index, rect in enumerate(rects):
            for other in rects[index + 1:]:
                self.assertFalse(rect.intersects(other), (rect, other))

    def _assert_panels_stay_on_screen(self, overlay):
        anchor = overlay.rotation_button.mapToGlobal(QPoint(0, 0))
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        available = screen.availableGeometry()
        self.assertTrue(available.contains(self._global_rect(overlay.rotation_panel)))
        self.assertTrue(available.contains(self._global_rect(overlay.selection_panel)))
        parent = overlay.parentWidget()
        self.assertTrue(self._global_rect(parent).contains(self._global_rect(overlay.selection_panel)))

    def test_rotation_and_selection_panels_coexist_in_both_open_orders(self):
        parent, overlay, _buttons = self.make_overlay()
        parent.show()
        overlay.show()
        overlay.reposition()

        for order in (("selection", "rotation"), ("rotation", "selection")):
            with self.subTest(order=order):
                overlay.close_rotation_panel(commit=False)
                overlay.close_selection_panel()
                for tool in order:
                    if tool == "selection":
                        overlay.selection_button.click()
                    else:
                        overlay.rotation_button.click()
                self.assertTrue(overlay.selection_panel.isVisible())
                self.assertTrue(overlay.selection_button.isChecked())
                self.assertTrue(overlay.rotation_panel.isVisible())
                self.assertTrue(overlay.rotation_button.isChecked())
                self._assert_panels_do_not_overlap(overlay)
                self._assert_panels_stay_on_screen(overlay)

    def test_external_click_and_selection_tool_do_not_dismiss_rotation(self):
        parent, overlay, _buttons = self.make_overlay()
        outside = QPushButton("画布", parent)
        outside.setGeometry(20, 20, 100, 30)
        parent.show()
        overlay.show()
        overlay.reposition()
        overlay.rotation_button.click()

        QTest.mouseClick(outside, Qt.LeftButton)
        QApplication.processEvents()

        self.assertTrue(overlay.rotation_panel.isVisible())
        self.assertTrue(overlay.rotation_button.isChecked())
        overlay.selection_button.click()
        self.assertTrue(overlay.selection_panel.isVisible())
        self.assertTrue(overlay.rotation_panel.isVisible())

        QTest.keyClick(overlay.rotation_panel, Qt.Key_Escape)
        self.assertFalse(overlay.rotation_panel.isVisible())
        self.assertFalse(overlay.rotation_button.isChecked())
        overlay.rotation_button.click()
        overlay.rotation_button.click()
        self.assertFalse(overlay.rotation_panel.isVisible())
        self.assertFalse(overlay.rotation_button.isChecked())

        overlay.rotation_button.click()
        overlay.hide()
        self.assertFalse(overlay.rotation_panel.isVisible())
        self.assertFalse(overlay.selection_panel.isVisible())
        self.assertFalse(overlay.rotation_button.isChecked())

    def test_visible_panels_avoid_a_crop_popup_opened_later(self):
        parent, overlay, _buttons = self.make_overlay()
        available = QApplication.primaryScreen().availableGeometry()
        parent.resize(min(560, available.width() - 40), min(420, available.height() - 40))
        parent.move(available.topLeft() + QPoint(10, 10))
        parent.show()
        overlay.show()
        overlay.reposition()
        overlay.rotation_button.click()
        crop_popup = QFrame(None, Qt.Tool | Qt.FramelessWindowHint)
        crop_popup.setObjectName("crop_popup")
        crop_popup.resize(min(180, available.width() // 5), min(140, available.height() // 4))
        crop_popup.move(overlay.rotation_panel.pos())
        self.addCleanup(crop_popup.deleteLater)

        crop_popup.show()
        QTest.qWait(0)
        self.assertTrue(crop_popup.isVisible())
        self._assert_panels_do_not_overlap(overlay, crop_popup)

        overlay.selection_button.click()
        self._assert_panels_do_not_overlap(overlay, crop_popup)
        self._assert_panels_stay_on_screen(overlay)

        parent.resize(min(460, available.width()), min(320, available.height()))
        parent.move(
            available.right() - parent.width() + 1,
            available.bottom() - parent.height() + 1,
        )
        overlay.reposition()
        self._assert_panels_do_not_overlap(overlay, crop_popup)
        self._assert_panels_stay_on_screen(overlay)

    def test_timeline_state_round_trip_drives_the_same_overlay_controls(self):
        from bandscope.ui.timeline_bar import TimelineBar

        _parent, overlay, _buttons = self.make_overlay()
        bar = TimelineBar(view_controls=overlay)
        self.addCleanup(bar.deleteLater)
        self.assertIs(bar.switch_axes, overlay.switch_axes)
        self.assertIs(bar.switch_flip, overlay.switch_flip)
        self.assertIs(bar.edit_rotation, overlay.edit_rotation)
        previews = []
        commits = []
        overlay.rotation_preview.connect(previews.append)
        overlay.rotation_committed.connect(commits.append)

        bar.restore_state({"switch_axes": True, "switch_flip": True, "rotation_angle": -7.5})

        self.assertEqual(bar.export_state(), {
            "switch_axes": True,
            "switch_flip": True,
            "rotation_angle": -7.5,
        })
        self.assertEqual(overlay.rotation_panel.dial.angle(), -7.5)
        self.assertEqual(previews, [])
        self.assertEqual(commits, [])

    def test_focus_mode_updates_the_existing_button_and_3d_state(self):
        _parent, overlay, _buttons = self.make_overlay()
        overlay.set_3d_active(False)
        self.assertFalse(overlay.reset_button.isEnabled())
        overlay.set_3d_active(True)
        self.assertTrue(overlay.reset_button.isEnabled())
        overlay.set_focus_mode(True)
        self.assertTrue(overlay.focus_button.isChecked())
        self.assertIn("恢复", overlay.focus_button.toolTip())
        overlay.set_focus_mode(False)
        self.assertFalse(overlay.focus_button.isChecked())


if __name__ == "__main__":
    unittest.main()
