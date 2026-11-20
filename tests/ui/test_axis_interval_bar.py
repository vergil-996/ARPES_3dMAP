"""Mouse-level tests for the canvas dual-ended interval control."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QColor, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.core.axis_interval import AxisSpace
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
            from PyQt5.QtCore import QPoint
            return QPoint(int(x), self.slider.TRACK_Y)
        from PyQt5.QtCore import QPoint
        return QPoint(int(self.slider.handle_center(endpoint)), self.slider.TRACK_Y)

    def _drag(self, start, end):
        press = QMouseEvent(QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        move = QMouseEvent(QEvent.MouseMove, end, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        release = QMouseEvent(QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        QApplication.sendEvent(self.slider, press)
        QApplication.sendEvent(self.slider, move)
        QApplication.sendEvent(self.slider, release)

    def test_endpoint_contact_locks_and_release_commits_once(self):
        self._drag(self._point("up"), self._point("low"))

        interval = self.controller.interval
        self.assertEqual(interval.low, interval.up)
        self.assertAlmostEqual(interval.low, -0.5)
        self.assertTrue(interval.locked)
        self.assertTrue(self.slider.lock_badge.isVisible())
        self.assertTrue(self.control.input_length.isReadOnly())
        self.assertEqual(len(self.commits), 1)

    def test_unlock_keeps_position_then_white_endpoint_separates_left(self):
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

    def test_middle_drag_preserves_length_and_stops_at_axis_edge(self):
        center = (self.slider.handle_center("low") + self.slider.handle_center("up")) / 2
        self._drag(self._point(x=center), self._point(x=self.slider.width() + 100))

        interval = self.controller.interval
        self.assertAlmostEqual(interval.length, 1.0)
        self.assertAlmostEqual(interval.low, 0.0)
        self.assertAlmostEqual(interval.up, 1.0)

    def test_locked_merged_handle_browses_single_layer(self):
        self.controller.interval.set_up(-0.5)
        self.controller.sync_widgets()
        start = self._point("low")
        self._drag(start, self._point(x=start.x() + 90))

        self.assertEqual(self.controller.interval.low, self.controller.interval.up)
        self.assertTrue(self.controller.interval.locked)
        self.assertNotAlmostEqual(self.controller.interval.center, -0.5)

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

    def test_length_resizes_about_center_and_zero_locks(self):
        self.control.input_length.setValue(0.4)
        self.control.input_length.editingFinished.emit()
        self.assertAlmostEqual(self.controller.interval.center, 0.0)
        self.assertAlmostEqual(self.controller.interval.length, 0.4)

        self.control.input_length.setValue(0.0)
        self.control.input_length.editingFinished.emit()
        self.assertTrue(self.controller.interval.locked)
        self.assertTrue(self.control.input_length.isReadOnly())

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
        for dx in (-9, -7, -5, 5, 7, 9):
            for dy in (-4, 0, 4):
                sample_x = int((self.slider.handle_center("low") + dx) * ratio)
                sample_y = int((self.slider.TRACK_Y + dy) * ratio)
                if QColor(image.pixel(sample_x, sample_y)).lightness() > 200:
                    shell_found = True
        self.assertTrue(shell_found)


if __name__ == "__main__":
    unittest.main()
