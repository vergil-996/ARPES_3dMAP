"""Mouse-level tests for the canvas dual-ended interval control."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QColor, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.core.axis_interval import (
    CONTINUOUS_DOCKING,
    PAN_FRAMES,
    AxisInterval,
    AxisSpace,
    FourthAxisDragSettings,
)
from bandscope.ui import theme
from bandscope.ui.axis_interval_bar import AxisIntervalControl
from bandscope.ui.axis_interval_controller import AxisIntervalController, IntervalEditMode

configure_qt_plugin_path()


class DualIntervalMouseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.control = AxisIntervalControl()
        self.control.resize(720, 52)
        self.control.show()
        self.space = AxisSpace("E", np.linspace(-1.0, 1.0, 11), unit="eV")
        self.controller = AxisIntervalController()
        self.controller.attach(
            interval_widget=self.control,
            box_length=self.control.input_length,
        )
        self.controller.bind(
            self.space, self.space.as_interval(low=-0.5, up=0.5)
        )
        self.controller.set_mode(IntervalEditMode.FULL)
        self.commits = []
        self.controller.intervalCommitted.connect(lambda: self.commits.append(True))
        self.slider = self.control.range_slider
        self.addCleanup(self.slider.coordinate_popup.close)
        self.addCleanup(self.control.close)

    def _point(self, endpoint=None, x=None):
        if x is not None:
            return QPoint(int(x), self.slider.TRACK_Y)
        if endpoint == "center":
            center = self.slider.center_handle_center()
            return QPoint(int(center.x()), int(center.y()))
        return QPoint(int(self.slider.handle_center(endpoint)), self.slider.TRACK_Y)

    def _drag(self, start, end):
        press = QMouseEvent(QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        move = QMouseEvent(QEvent.MouseMove, end, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        release = QMouseEvent(QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        QApplication.sendEvent(self.slider, press)
        QApplication.sendEvent(self.slider, move)
        QApplication.sendEvent(self.slider, release)

    def _bind_fourth_axis(self, coords, settings=None, interval=None):
        self.space = AxisSpace("delay", np.asarray(coords, dtype=float))
        self.controller.set_fourth_axis_drag(settings)
        self.controller.bind(
            self.space,
            interval if interval is not None else self.space.as_interval(),
        )

    def test_fourth_axis_endpoint_snaps_to_real_samples_in_automatic_mode(self):
        self._bind_fourth_axis([0.0, 10.0, 30.0])
        self._drag(self._point("low"), self._point(x=self.slider._x_for(11.0)))
        self.assertEqual(self.controller.interval.low, 10.0)
        self.assertEqual(len(self.commits), 1)

    def test_fourth_axis_auto_thirty_samples_allows_fractional_endpoint(self):
        self._bind_fourth_axis(np.arange(30, dtype=float))
        self._drag(self._point("low"), self._point(x=self.slider._x_for(1.25)))
        self.assertAlmostEqual(self.controller.interval.low, 1.25, delta=0.05)
        self.assertNotIn(self.controller.interval.low, self.space.coords)

    def test_fourth_axis_discrete_center_drag_moves_whole_frame_range(self):
        coords = np.array([0.0, 10.0, 30.0, 70.0, 140.0])
        interval = AxisInterval.from_indices(coords, 1, 3, locked=True)
        self._bind_fourth_axis(coords, interval=interval)
        self._drag(
            self._point("center"),
            self._point(x=self.slider._x_for(1000.0)),
        )
        self.assertEqual((self.controller.interval.low, self.controller.interval.up), (30.0, 140.0))
        self.assertEqual(self.controller.interval.to_indices(coords), (2, 4))

    def test_fourth_axis_continuous_frame_pan_allows_fractional_endpoints(self):
        coords = np.array([0.0, 10.0, 30.0, 70.0, 140.0])
        interval = AxisInterval.from_indices(coords, 1, 3, locked=True)
        self._bind_fourth_axis(
            coords,
            FourthAxisDragSettings(CONTINUOUS_DOCKING, PAN_FRAMES),
            interval,
        )
        self._drag(
            self._point("center"),
            self._point(x=self.slider._x_for(55.0)),
        )
        self.assertAlmostEqual(self.controller.interval.low, 22.5, delta=0.5)
        self.assertAlmostEqual(self.controller.interval.up, 113.75, delta=0.5)
        low, up = self.controller.interval.to_indices(coords)
        self.assertEqual(up - low + 1, 3)

    def test_endpoint_contact_locks_and_release_commits_once(self):
        self._drag(self._point("up"), self._point("low"))

        interval = self.controller.interval
        self.assertEqual(interval.low, interval.up)
        self.assertAlmostEqual(interval.low, -0.5)
        self.assertTrue(interval.locked)
        self.assertTrue(self.slider.lock_badge.isVisible())
        self.assertTrue(self.control.input_length.isReadOnly())
        self.assertEqual(len(self.commits), 1)

    def test_unlock_keeps_position_then_black_endpoint_separates_left(self):
        self.controller.interval.set_up(-0.5)
        self.controller.sync_widgets()
        self.slider.lock_badge.click()
        self.assertTrue(self.slider.lock_badge._animation.state() == self.slider.lock_badge._animation.Running)
        self.slider.lock_badge._animation.pause()
        self.slider.lock_badge._animation.setCurrentTime(70)
        self.assertGreater(self.slider.lock_badge._open_progress, 0.0)
        self.assertLess(self.slider.lock_badge._open_progress, 1.0)
        self.assertFalse(self.controller.interval.locked)
        self.assertEqual(self.controller.interval.low, self.controller.interval.up)

        target_x = self.slider.handle_center("low") - 80
        self._drag(self._point("low"), self._point(x=target_x))

        self.assertLess(self.controller.interval.low, self.controller.interval.up)
        self.assertFalse(self.controller.interval.locked)
        self.assertGreater(self.controller.interval.length, 0.0)

    def test_center_handle_drag_preserves_length_and_stops_at_axis_edge(self):
        start = self._point("center")
        self._drag(start, QPoint(self.slider.width() + 100, start.y()))

        interval = self.controller.interval
        self.assertAlmostEqual(interval.length, 1.0)
        self.assertAlmostEqual(interval.low, 0.0)
        self.assertAlmostEqual(interval.up, 1.0)

    def test_close_endpoints_do_not_block_center_handle_drag(self):
        self.control.input_length.setValue(0.04)
        self.control.input_length.editingFinished.emit()
        original_center = self.controller.interval.center
        self.commits.clear()
        start = self._point("center")

        self._drag(start, QPoint(start.x() + 60, start.y()))

        interval = self.controller.interval
        self.assertAlmostEqual(interval.length, 0.04)
        self.assertGreater(interval.center, original_center)
        self.assertFalse(interval.locked)
        self.assertEqual(len(self.commits), 1)

    def test_selected_track_itself_does_not_start_center_drag(self):
        center_x = self.slider.center_handle_center().x()
        original_center = self.controller.interval.center

        self._drag(self._point(x=center_x), self._point(x=center_x + 60))

        self.assertAlmostEqual(self.controller.interval.center, original_center)
        self.assertEqual(self.commits, [])

    def test_endpoint_hit_region_stays_on_the_track(self):
        original_low = self.controller.interval.low
        start = QPoint(
            int(self.slider.handle_center("low")), self.slider.CENTER_HANDLE_Y
        )

        self._drag(start, QPoint(start.x() + 60, start.y()))

        self.assertAlmostEqual(self.controller.interval.low, original_low)
        self.assertEqual(self.commits, [])

    def test_locked_merged_handle_browses_single_layer(self):
        self.controller.interval.set_up(-0.5)
        self.controller.sync_widgets()
        start = self._point("low")
        self._drag(start, self._point(x=start.x() + 90))

        self.assertEqual(self.controller.interval.low, self.controller.interval.up)
        self.assertTrue(self.controller.interval.locked)
        self.assertNotAlmostEqual(self.controller.interval.center, -0.5)

    def test_locked_center_drag_keeps_popup_on_the_merged_handle(self):
        """合拢锁定后点开浮框、再回来拖合并手柄，浮框要跟上手柄和坐标。"""
        self.controller.interval.set_up(-0.5)
        self.controller.sync_widgets()
        slider = self.slider
        popup = slider.coordinate_popup

        # 点开浮框后回到滑条时会因失焦提交而隐藏，这里先还原这个状态。
        slider._show_popup("low")
        self.assertTrue(popup.isVisible())
        popup.hide()

        start = self._point("low")
        self._drag(start, self._point(x=start.x() + 90))

        self.assertTrue(popup.isVisible())
        self.assertAlmostEqual(
            popup.value_edit.value(), self.controller.interval.low, delta=1e-3
        )
        anchor = slider.mapToGlobal(self._point("low")).x()
        self.assertLessEqual(
            abs(popup.pos().x() + popup.width() // 2 - anchor), 2
        )

    def test_popup_commits_coordinate_and_escape_does_not_change_model(self):
        self.slider._show_popup("low")
        popup = self.slider.coordinate_popup
        self.assertTrue(popup.isVisible())
        popup.value_edit.setValue(-0.2)
        popup.value_edit.editingFinished.emit()
        self.assertAlmostEqual(self.controller.interval.low, -0.2)

        self.slider._show_popup("low")
        before = self.controller.interval.low
        popup.value_edit.setValue(0.7)
        QTest.keyClick(popup.value_edit, Qt.Key_Escape)
        self.assertAlmostEqual(self.controller.interval.low, before)
        self.assertFalse(popup.isVisible())

    def test_length_input_is_hidden_and_not_in_the_layout(self):
        QApplication.processEvents()

        self.assertTrue(self.control.input_length.isHidden())
        self.assertFalse(self.control.input_length.isVisible())
        self.assertEqual(self.control.layout().indexOf(self.control.input_length), -1)

    def test_hidden_length_input_retains_programmatic_edit_protocol(self):
        self.control.input_length.setValue(0.4)
        self.control.input_length.editingFinished.emit()
        self.assertAlmostEqual(self.controller.interval.center, 0.0)
        self.assertAlmostEqual(self.controller.interval.length, 0.4)

        self.control.input_length.setValue(0.0)
        self.control.input_length.editingFinished.emit()
        self.assertTrue(self.controller.interval.locked)
        self.assertTrue(self.control.input_length.isReadOnly())

    def test_separated_handles_paint_black_left_and_white_right(self):
        QApplication.processEvents()
        image = self.slider.grab().toImage()
        ratio = image.devicePixelRatio()
        y = int(self.slider.TRACK_Y * ratio)
        low_x = int(self.slider.handle_center("low") * ratio)
        up_x = int(self.slider.handle_center("up") * ratio)

        self.assertLess(QColor(image.pixel(low_x, y)).lightness(), 60)
        self.assertGreater(QColor(image.pixel(up_x, y)).lightness(), 200)

    def test_separated_interval_paints_dashed_stem_and_theme_contrasting_center_handle(self):
        previous_theme = theme.current_theme()
        self.addCleanup(theme.set_theme, previous_theme)
        for name in ("light", "dark"):
            with self.subTest(theme=name):
                theme.set_theme(name)
                self.slider.update()
                QApplication.processEvents()
                image = self.slider.grab().toImage()
                ratio = image.devicePixelRatio()
                center = self.slider.center_handle_center()
                x = int(center.x() * ratio)
                y = int(center.y() * ratio)

                fill = QColor(image.pixel(x, y))
                outline_samples = [
                    QColor(image.pixel(
                        int((center.x() + dx) * ratio),
                        int((center.y() + dy) * ratio),
                    ))
                    for dx, dy in (
                        (-4, 0), (4, 0), (0, -4), (0, 4),
                        (-3, 0), (3, 0), (0, -3), (0, 3),
                    )
                ]
                stem_colors = {
                    QColor(image.pixel(x, int(stem_y * ratio))).rgba()
                    for stem_y in range(
                        self.slider.TRACK_Y + 4,
                        self.slider.CENTER_HANDLE_Y - self.slider.CENTER_HANDLE_RADIUS,
                    )
                }
                if name == "light":
                    self.assertGreater(fill.lightness(), 200)
                    self.assertLess(min(color.lightness() for color in outline_samples), 100)
                else:
                    self.assertLess(fill.lightness(), 60)
                    self.assertGreater(max(color.lightness() for color in outline_samples), 200)
                self.assertGreater(len(stem_colors), 1)

        self.controller.interval.set_up(self.controller.interval.low)
        self.controller.sync_widgets()
        self.assertFalse(self.slider._has_center_handle())

    def test_selected_and_unselected_tracks_have_groove_shading(self):
        previous_theme = theme.current_theme()
        self.addCleanup(theme.set_theme, previous_theme)
        for name in ("light", "dark"):
            theme.set_theme(name)
            self.slider.update()
            QApplication.processEvents()
            image = self.slider.grab().toImage()
            ratio = image.devicePixelRatio()
            low_x = self.slider.handle_center("low")
            up_x = self.slider.handle_center("up")
            sample_points = {
                "selected": (low_x + up_x) / 2,
                "unselected": (self.slider._track_left() + low_x) / 2,
            }
            for region, sample_x in sample_points.items():
                with self.subTest(theme=name, region=region):
                    x = int(sample_x * ratio)
                    center_y = int(self.slider.TRACK_Y * ratio)
                    below_y = int((self.slider.TRACK_Y + 1) * ratio)
                    center = QColor(image.pixel(x, center_y))
                    below = QColor(image.pixel(x, below_y))
                    self.assertNotEqual(center.rgba(), below.rgba())

    def test_interval_handles_have_top_highlights_and_bottom_shading(self):
        QApplication.processEvents()
        image = self.slider.grab().toImage()
        ratio = image.devicePixelRatio() or 1.0

        def pixel(endpoint, dy):
            return QColor(image.pixel(
                int(round(self.slider.handle_center(endpoint) * ratio)),
                int(round((self.slider.TRACK_Y + dy) * ratio)),
            ))

        self.assertGreater(pixel("low", -5).lightness(), pixel("low", 0).lightness())
        self.assertLess(pixel("low", 5).lightness(), pixel("low", 0).lightness())
        self.assertLess(pixel("up", 5).lightness(), pixel("up", 0).lightness())

    def test_track_is_centered_with_title(self):
        self.control.resize(720, 60)
        QApplication.processEvents()
        track_y = self.slider.y() + self.slider.TRACK_Y
        self.assertAlmostEqual(
            track_y, self.control.title_label.geometry().center().y(), delta=1
        )

    def test_lock_badge_stays_clear_of_the_handle(self):
        previous_style = self.app.styleSheet()
        self.app.setStyleSheet(theme.application_qss())
        self.addCleanup(self.app.setStyleSheet, previous_style)
        self.controller.set_locked(True)
        self.controller.sync_widgets()
        self.control.resize(720, 60)
        QApplication.processEvents()
        badge = self.slider.lock_badge
        self.assertTrue(badge.isVisible())
        self.assertEqual(badge.height(), 16)
        # 合拢时手柄多画 4px 外圈，徽标底边仍要留出间隙。
        handle_top = self.slider.TRACK_Y - (self.slider.HANDLE_HEIGHT + 4) / 2
        self.assertLessEqual(badge.geometry().bottom(), handle_top - 1)

    def test_collapsed_handle_paints_black_core_inside_white_shell(self):
        self.controller.interval.set_up(-0.5)
        self.controller.sync_widgets()
        QApplication.processEvents()
        image = self.slider.grab().toImage()
        ratio = image.devicePixelRatio()
        x = int(self.slider.handle_center("low") * ratio)
        y = int(self.slider.TRACK_Y * ratio)
        self.assertLess(QColor(image.pixel(x, y)).lightness(), 60)
        shell_found = False
        for dx in (-6, 6):
            for dy in (-3, 0, 3):
                sample_x = int((self.slider.handle_center("low") + dx) * ratio)
                sample_y = int((self.slider.TRACK_Y + dy) * ratio)
                if QColor(image.pixel(sample_x, sample_y)).lightness() > 200:
                    shell_found = True
        self.assertTrue(shell_found)


if __name__ == "__main__":
    unittest.main()
