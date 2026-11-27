import math
import unittest
from unittest.mock import patch

from PyQt5.QtCore import QAbstractAnimation, QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QImage, QPainterPath
from PyQt5.QtTest import QTest, QSignalSpy
from PyQt5.QtWidgets import QApplication

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.core.opacity_curve import OPACITY_LINEAR, OPACITY_SMOOTH
from bandscope.ui import theme
from bandscope.ui.opacity_interpolation_switch import OpacityInterpolationSwitch


configure_qt_plugin_path()


class OpacityInterpolationSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.original_theme = theme.current_theme()
        theme.set_theme("light")
        self.switch = OpacityInterpolationSwitch()
        self.switch.show()
        self.app.processEvents()

    def tearDown(self):
        self.switch._animation.stop()
        self.switch.close()
        self.switch.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        theme.set_theme(self.original_theme)

    def render_switch(self):
        image = QImage(self.switch.size(), QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        self.switch.render(image, QPoint())
        return image

    def test_initial_mode_geometry_and_accessibility(self):
        self.assertEqual(self.switch.interpolation(), OPACITY_SMOOTH)
        self.assertEqual(self.switch.progress, 0.0)
        self.assertFalse(self.switch.isChecked())
        self.assertTrue(self.switch.isCheckable())
        self.assertEqual(self.switch.size().width(), self.switch.WIDTH)
        self.assertEqual(self.switch.size().height(), self.switch.HEIGHT)
        self.assertEqual(self.switch.focusPolicy(), Qt.StrongFocus)
        self.assertIn("平滑曲线", self.switch.accessibleName())
        self.assertIn("直线", self.switch.toolTip())
        self.assertIn("空格", self.switch.toolTip())
        self.assertIn("回车", self.switch.toolTip())

    def test_click_to_linear_emits_once_and_animates_to_right(self):
        changes = QSignalSpy(self.switch.interpolationChanged)

        QTest.mouseClick(self.switch, Qt.LeftButton)

        self.assertEqual(list(changes), [[OPACITY_LINEAR]])
        self.assertEqual(self.switch.interpolation(), OPACITY_LINEAR)
        self.assertTrue(self.switch.isChecked())
        animation = self.switch._animation
        self.assertEqual(animation.state(), QAbstractAnimation.Running)
        self.assertEqual(animation.startValue(), 0.0)
        self.assertEqual(animation.endValue(), 1.0)
        self.assertEqual(self.switch._thumb_rect(1.0).center().x(),
                         self.switch._thumb_rect(0.0).center().x() + 34.0)
        animation.setCurrentTime(animation.duration() // 2)
        self.assertGreater(self.switch.progress, 0.0)
        self.assertLess(self.switch.progress, 1.0)
        animation.setCurrentTime(animation.duration())
        self.assertEqual(self.switch.progress, 1.0)
        self.assertEqual(animation.state(), QAbstractAnimation.Stopped)
        self.assertEqual(self.switch._thumb_rect().center(),
                         self.switch._thumb_rect(1.0).center())

    def test_click_back_to_smooth_emits_once_and_animates_to_left(self):
        self.switch.set_interpolation(OPACITY_LINEAR, animate=False)
        changes = QSignalSpy(self.switch.interpolationChanged)

        QTest.mouseClick(self.switch, Qt.LeftButton)

        self.assertEqual(list(changes), [[OPACITY_SMOOTH]])
        self.assertEqual(self.switch.interpolation(), OPACITY_SMOOTH)
        self.assertFalse(self.switch.isChecked())
        animation = self.switch._animation
        self.assertEqual(animation.startValue(), 1.0)
        self.assertEqual(animation.endValue(), 0.0)
        animation.setCurrentTime(animation.duration())
        self.assertEqual(self.switch.progress, 0.0)
        self.assertEqual(self.switch._thumb_rect().center(),
                         self.switch._thumb_rect(0.0).center())
        self.assertIn("平滑曲线", self.switch.accessibleName())

    def test_external_set_normalizes_without_emitting(self):
        changes = QSignalSpy(self.switch.interpolationChanged)

        self.switch.set_interpolation("pchip", animate=False)
        self.assertEqual(self.switch.interpolation(), OPACITY_SMOOTH)
        self.assertEqual(self.switch.progress, 0.0)
        self.switch.set_interpolation("unknown", animate=False)
        self.assertEqual(self.switch.interpolation(), OPACITY_LINEAR)
        self.assertEqual(self.switch.progress, 1.0)
        self.switch.set_interpolation("平滑", animate=False)
        self.assertEqual(self.switch.interpolation(), OPACITY_SMOOTH)
        self.assertEqual(self.switch.progress, 0.0)
        self.switch.set_interpolation(OPACITY_LINEAR)
        self.assertEqual(self.switch.interpolation(), OPACITY_LINEAR)
        self.assertEqual(self.switch._animation.state(), QAbstractAnimation.Running)
        self.assertEqual(len(changes), 0)

    def test_repeated_reversal_starts_at_the_visible_thumb_position(self):
        changes = QSignalSpy(self.switch.interpolationChanged)
        animation = self.switch._animation

        self.switch.click()
        animation.setCurrentTime(animation.duration() // 3)
        visible_progress = self.switch.progress
        self.assertGreater(visible_progress, 0.0)
        self.assertLess(visible_progress, 1.0)

        self.switch.click()
        self.assertEqual(self.switch.interpolation(), OPACITY_SMOOTH)
        self.assertAlmostEqual(animation.startValue(), visible_progress)
        self.assertEqual(animation.endValue(), 0.0)
        animation.setCurrentTime(animation.duration() // 2)
        self.assertAlmostEqual(self.switch.progress, visible_progress / 2.0)

        reverse_progress = self.switch.progress
        self.switch.click()
        self.assertEqual(self.switch.interpolation(), OPACITY_LINEAR)
        self.assertAlmostEqual(animation.startValue(), reverse_progress)
        self.assertEqual(animation.endValue(), 1.0)
        self.assertEqual(list(changes), [
            [OPACITY_LINEAR], [OPACITY_SMOOTH], [OPACITY_LINEAR],
        ])

    def test_same_mode_does_not_restart_animation_but_can_snap(self):
        self.switch.set_interpolation(OPACITY_LINEAR)
        animation = self.switch._animation
        animation.setCurrentTime(80)
        progress = self.switch.progress

        self.switch.set_interpolation(OPACITY_LINEAR)
        self.assertEqual(animation.currentTime(), 80)
        self.assertEqual(self.switch.progress, progress)
        self.assertEqual(animation.state(), QAbstractAnimation.Running)

        self.switch.set_interpolation(OPACITY_LINEAR, animate=False)
        self.assertEqual(animation.state(), QAbstractAnimation.Stopped)
        self.assertEqual(self.switch.progress, 1.0)

    def test_animation_finishes_through_the_event_loop(self):
        self.switch.set_interpolation(OPACITY_LINEAR)

        QTest.qWait(self.switch._animation.duration() + 80)

        self.assertEqual(self.switch.progress, 1.0)
        self.assertEqual(self.switch._animation.state(), QAbstractAnimation.Stopped)

    def test_space_return_and_enter_toggle_without_extra_signals(self):
        self.switch.setFocus(Qt.OtherFocusReason)

        for key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            with self.subTest(key=key):
                self.switch.set_interpolation(OPACITY_SMOOTH, animate=False)
                changes = QSignalSpy(self.switch.interpolationChanged)
                QTest.keyClick(self.switch, key)
                self.assertEqual(list(changes), [[OPACITY_LINEAR]])
                self.assertEqual(self.switch.interpolation(), OPACITY_LINEAR)
                QTest.keyClick(self.switch, key)
                self.assertEqual(list(changes), [
                    [OPACITY_LINEAR], [OPACITY_SMOOTH],
                ])
                self.assertEqual(self.switch.interpolation(), OPACITY_SMOOTH)

    def test_disabled_switch_ignores_mouse_and_keyboard_but_syncs_externally(self):
        self.switch.setEnabled(False)
        changes = QSignalSpy(self.switch.interpolationChanged)

        QTest.mouseClick(self.switch, Qt.LeftButton)
        QTest.keyClick(self.switch, Qt.Key_Space)
        QTest.keyClick(self.switch, Qt.Key_Return)

        self.assertEqual(len(changes), 0)
        self.assertEqual(self.switch.interpolation(), OPACITY_SMOOTH)
        self.assertEqual(self.switch.progress, 0.0)
        self.assertEqual(self.switch.cursor().shape(), Qt.ArrowCursor)
        self.assertFalse(self.render_switch().isNull())

        self.switch.set_interpolation(OPACITY_LINEAR, animate=False)
        self.assertEqual(self.switch.interpolation(), OPACITY_LINEAR)
        self.assertEqual(self.switch.progress, 1.0)
        self.assertEqual(len(changes), 0)

    def test_linear_icon_is_exactly_a_three_segment_polyline(self):
        center = QPointF(18.0, 14.0)
        path = self.switch._icon_path(OPACITY_LINEAR, center)
        points = [path.elementAt(index) for index in range(path.elementCount())]

        self.assertEqual(len(points), 4)
        self.assertEqual(points[0].type, QPainterPath.MoveToElement)
        self.assertEqual(
            [point.type for point in points[1:]],
            [QPainterPath.LineToElement] * 3,
        )
        self.assertEqual(len({(point.x, point.y) for point in points}), 4)
        relative_y = [point.y - center.y() for point in points]
        self.assertGreater(relative_y[0], 0.0)
        self.assertLess(relative_y[1], 0.0)
        self.assertGreater(relative_y[2], 0.0)
        self.assertLess(relative_y[3], 0.0)
        self.assertAlmostEqual(path.boundingRect().width(), 14.0)
        self.assertLessEqual(path.boundingRect().height(), 8.0)

    def test_smooth_icon_is_one_slightly_distorted_trigonometric_period(self):
        center = QPointF(18.0, 14.0)
        path = self.switch._icon_path(OPACITY_SMOOTH, center)
        samples = [path.elementAt(index) for index in range(path.elementCount())]
        values = [center.y() - point.y for point in samples]

        self.assertGreaterEqual(len(samples), 25)
        self.assertAlmostEqual(path.boundingRect().width(), 14.0)
        self.assertLessEqual(path.boundingRect().height(), 8.0)
        nonzero_values = [value for value in values if abs(value) > 1.0e-9]
        crossings = sum(
            left * right < 0.0
            for left, right in zip(nonzero_values, nonzero_values[1:])
        )
        peaks = sum(
            values[index] > values[index - 1]
            and values[index] > values[index + 1]
            for index in range(1, len(values) - 1)
        )
        troughs = sum(
            values[index] < values[index - 1]
            and values[index] < values[index + 1]
            for index in range(1, len(values) - 1)
        )
        self.assertEqual(crossings, 1)
        self.assertEqual(peaks, 1)
        self.assertEqual(troughs, 1)
        self.assertGreater(max(values), 2.0)
        self.assertLess(min(values), -2.0)
        self.assertAlmostEqual(values[0], 0.0)
        self.assertAlmostEqual(values[len(values) // 2], 0.0)
        self.assertAlmostEqual(values[-1], 0.0)

        deviations = []
        for point, value in zip(samples, values):
            position = (point.x - (center.x() - 7.0)) / 14.0
            fundamental = 3.1 * math.sin(2.0 * math.pi * position)
            deviations.append(abs(value - fundamental))
        self.assertGreater(max(deviations), 0.25)
        self.assertLess(max(deviations), 0.55)

    def test_groove_has_depth_and_icons_render(self):
        image = self.render_switch()
        self.assertNotEqual(image.pixelColor(36, 5), image.pixelColor(36, 22))
        colors = {
            image.pixel(x, y)
            for x in range(image.width())
            for y in range(image.height())
        }
        self.assertGreater(len(colors), 30)

    def test_focus_does_not_change_rendering(self):
        self.switch.clearFocus()
        unfocused = self.render_switch()

        self.switch.setFocus(Qt.OtherFocusReason)

        self.assertTrue(self.switch.hasFocus())
        self.assertEqual(self.render_switch(), unfocused)

    def test_rendering_tracks_theme_at_rest_and_mid_animation(self):
        self.switch.clearFocus()
        light = self.render_switch()
        light_thumb = light.pixelColor(8, 7)
        with patch.object(self.switch, "update", wraps=self.switch.update) as update:
            theme.changed.emit("light")
        update.assert_called()

        theme.set_theme("dark")
        self.app.processEvents()
        dark = self.render_switch()
        self.assertNotEqual(light_thumb, dark.pixelColor(8, 7))

        self.switch.set_interpolation(OPACITY_LINEAR)
        self.switch._animation.setCurrentTime(self.switch._animation.duration() // 2)
        middle = self.render_switch()
        self.assertFalse(middle.isNull())
        self.switch._animation.setCurrentTime(self.switch._animation.duration())
        self.assertFalse(self.render_switch().isNull())

    def test_destroyed_switch_disconnects_its_theme_listener(self):
        listeners_before = set(theme.changed._listeners)
        switch = OpacityInterpolationSwitch()
        self.assertEqual(len(theme.changed._listeners), len(listeners_before) + 1)

        switch.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

        self.assertEqual(set(theme.changed._listeners), listeners_before)
        theme.changed.emit("light")


if __name__ == "__main__":
    unittest.main()
