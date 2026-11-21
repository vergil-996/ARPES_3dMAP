import math
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.app.qt_bootstrap import configure_qt_plugin_path

configure_qt_plugin_path()

from bandscope.ui.rotation_dial import RotationDialPanel


class RotationDialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self):
        panel = RotationDialPanel()
        self.addCleanup(panel.deleteLater)
        return panel

    @staticmethod
    def _point(dial, angle):
        center = dial.rect().center()
        radians = math.radians(angle)
        radius = dial._ring_geometry()[2]
        return QPointF(
            center.x() + math.sin(radians) * radius,
            center.y() - math.cos(radians) * radius,
        )

    def _send_at_angle(self, dial, event_type, pointer_angle, button, buttons):
        event = QMouseEvent(
            event_type,
            self._point(dial, pointer_angle),
            button,
            buttons,
            Qt.NoModifier,
        )
        QApplication.sendEvent(dial, event)

    def _press(self, dial, pointer_angle=0):
        self._send_at_angle(dial, QEvent.MouseButtonPress, pointer_angle, Qt.LeftButton, Qt.LeftButton)

    def _move(self, dial, pointer_angle):
        self._send_at_angle(dial, QEvent.MouseMove, pointer_angle, Qt.NoButton, Qt.LeftButton)

    def _release(self, dial, pointer_angle):
        self._send_at_angle(dial, QEvent.MouseButtonRelease, pointer_angle, Qt.LeftButton, Qt.NoButton)

    def test_drag_starts_at_current_angle_and_crosses_zero_continuously(self):
        panel = self.make_panel()
        panel.set_angle(-7.5)
        previews = []
        commits = []
        panel.rotation_preview.connect(previews.append)
        panel.rotation_committed.connect(commits.append)

        self._press(panel.dial, -10)
        self._move(panel.dial, 0)
        self._move(panel.dial, 10)
        self._release(panel.dial, 10)

        self.assertEqual(len(previews), 2)
        self.assertAlmostEqual(previews[0], 2.5, delta=0.3)
        self.assertAlmostEqual(previews[1], 12.5, delta=0.3)
        self.assertEqual(len(commits), 1)
        self.assertAlmostEqual(commits[0], 12.5, delta=0.3)
        self.assertAlmostEqual(panel.current_angle(), 12.5, delta=0.3)

    def test_drag_normalizes_the_signed_pointer_wrap(self):
        panel = self.make_panel()
        panel.set_angle(175)
        self._press(panel.dial, 179)
        self._move(panel.dial, -179)
        self._release(panel.dial, -179)
        self.assertAlmostEqual(panel.current_angle(), 177, delta=0.3)

    def test_drag_stops_at_both_limits(self):
        panel = self.make_panel()
        for start, pointer_end, expected in ((350, 120, 360), (-350, -120, -360)):
            with self.subTest(expected=expected):
                panel.set_angle(start)
                commits = []
                panel.rotation_committed.connect(commits.append)
                self._press(panel.dial, 0)
                self._move(panel.dial, pointer_end)
                self._release(panel.dial, pointer_end)
                self.assertEqual(panel.current_angle(), expected)
                self.assertEqual(commits, [expected])
                panel.rotation_committed.disconnect(commits.append)

    def test_one_drag_commits_exactly_once_after_all_previews(self):
        panel = self.make_panel()
        previews = []
        commits = []
        panel.rotation_preview.connect(previews.append)
        panel.rotation_committed.connect(commits.append)

        self._press(panel.dial)
        for angle in (20, 45, 70, 90):
            self._move(panel.dial, angle)
        self.assertEqual(commits, [])
        self._release(panel.dial, 90)

        self.assertEqual(len(previews), 4)
        for actual, expected in zip(previews, (20, 45, 70, 90)):
            self.assertAlmostEqual(actual, expected, delta=0.5)
        self.assertEqual(len(commits), 1)
        self.assertAlmostEqual(commits[0], 90, delta=0.3)

    def test_negative_decimal_input_updates_pointer_readout_and_commit(self):
        panel = self.make_panel()
        previews = []
        commits = []
        panel.rotation_preview.connect(previews.append)
        panel.rotation_committed.connect(commits.append)

        panel.edit_rotation.lineEdit().setText("-7.5")
        panel.edit_rotation.editingFinished.emit()

        self.assertEqual(previews[-1], -7.5)
        self.assertEqual(commits, [-7.5])
        self.assertEqual(panel.dial.angle(), -7.5)
        self.assertEqual(panel.current_angle(), -7.5)
        self.assertEqual(panel.edit_rotation.decimals(), 2)

    def test_keyboard_arrows_adjust_by_one_degree(self):
        panel = self.make_panel()
        panel.edit_rotation.setFocus()
        QTest.keyClick(panel.edit_rotation, Qt.Key_Up)
        self.assertEqual(panel.current_angle(), 1)
        self.assertEqual(panel.dial.angle(), 1)
        QTest.keyClick(panel.edit_rotation, Qt.Key_Down)
        self.assertEqual(panel.current_angle(), 0)
        self.assertEqual(panel.dial.angle(), 0)

    def test_dismissing_during_drag_prevents_a_delayed_commit(self):
        panel = self.make_panel()
        commits = []
        panel.rotation_committed.connect(commits.append)
        self._press(panel.dial)
        self._move(panel.dial, 30)
        panel.dismiss()

        self.assertFalse(panel.dial._dragging)
        self._release(panel.dial, 30)
        self.assertEqual(commits, [])

    def test_zero_commits_immediately_without_a_preview(self):
        panel = self.make_panel()
        panel.set_angle(-7.5)
        previews = []
        commits = []
        panel.rotation_preview.connect(previews.append)
        panel.rotation_committed.connect(commits.append)

        panel.zero_button.click()

        self.assertEqual(previews, [])
        self.assertEqual(commits, [0.0])
        self.assertEqual(panel.current_angle(), 0)
        self.assertEqual(panel.dial.angle(), 0)


if __name__ == "__main__":
    unittest.main()
