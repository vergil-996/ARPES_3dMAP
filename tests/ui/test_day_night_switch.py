import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QAbstractAnimation, QEvent, QPoint, QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QImage, QPainterPath
from PyQt5.QtTest import QTest, QSignalSpy
from PyQt5.QtWidgets import QApplication

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.ui.day_night_switch import DayNightSwitch


configure_qt_plugin_path()


class DayNightSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.switch = DayNightSwitch()
        self.switch.show()
        self.app.processEvents()

    def tearDown(self):
        self.switch._theme_animation.stop()
        self.switch._hover_animation.stop()
        self.switch.close()
        self.switch.deleteLater()
        self.app.processEvents()

    def render_switch(self):
        image = QImage(self.switch.size(), QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        self.switch.render(image, QPoint())
        return image

    def pixel_at_logical(self, image, x, y):
        scale = min(
            self.switch.width() / DayNightSwitch.SCENE_WIDTH,
            self.switch.height() / DayNightSwitch.SCENE_HEIGHT,
        )
        offset_x = (self.switch.width() - DayNightSwitch.SCENE_WIDTH * scale) / 2.0
        offset_y = (self.switch.height() - DayNightSwitch.SCENE_HEIGHT * scale) / 2.0
        return image.pixelColor(
            round(x * scale + offset_x),
            round(y * scale + offset_y),
        )

    def test_initial_state_and_accessibility(self):
        self.assertEqual(self.switch.theme_name(), "light")
        self.assertFalse(self.switch.isChecked())
        self.assertTrue(self.switch.isCheckable())
        self.assertEqual(self.switch.progress, 0.0)
        self.assertEqual(self.switch.size().width(), 96)
        self.assertEqual(self.switch.size().height(), 38)
        self.assertEqual(self.switch.focusPolicy(), Qt.StrongFocus)
        self.assertIn("浅色", self.switch.accessibleName())
        self.assertIn("深色", self.switch.toolTip())
        self.assertGreaterEqual(self.switch._theme_animation.duration(), 500)
        self.assertLessEqual(self.switch._theme_animation.duration(), 700)

    def test_mouse_click_emits_theme_request_and_starts_animation(self):
        requests = QSignalSpy(self.switch.theme_requested)

        QTest.mouseClick(self.switch, Qt.LeftButton)

        self.assertEqual(list(requests), [["dark"]])
        self.assertEqual(self.switch.theme_name(), "dark")
        self.assertTrue(self.switch.isChecked())
        self.assertEqual(
            self.switch._theme_animation.state(), QAbstractAnimation.Running
        )
        self.assertIn("深色", self.switch.accessibleName())
        self.assertIn("浅色", self.switch.toolTip())

    def test_space_key_emits_theme_request(self):
        self.switch.setFocus(Qt.OtherFocusReason)
        self.app.processEvents()
        requests = QSignalSpy(self.switch.theme_requested)

        QTest.keyClick(self.switch, Qt.Key_Space)

        self.assertTrue(self.switch.hasFocus())
        self.assertEqual(list(requests), [["dark"]])
        self.assertEqual(self.switch.theme_name(), "dark")
        self.assertTrue(self.switch.isChecked())

    def test_set_theme_updates_state_without_request(self):
        requests = QSignalSpy(self.switch.theme_requested)

        self.switch.set_theme("dark", animate=False)
        self.assertEqual(self.switch.theme_name(), "dark")
        self.assertTrue(self.switch.isChecked())
        self.assertEqual(self.switch.progress, 1.0)

        self.switch.set_theme("light", animate=False)
        self.assertEqual(self.switch.theme_name(), "light")
        self.assertFalse(self.switch.isChecked())
        self.assertEqual(self.switch.progress, 0.0)
        self.assertEqual(len(requests), 0)

    def test_same_theme_does_not_restart_animation(self):
        self.switch.set_theme("light")
        self.assertEqual(
            self.switch._theme_animation.state(), QAbstractAnimation.Stopped
        )

        self.switch.set_theme("dark")
        self.switch._theme_animation.setCurrentTime(180)
        progress = self.switch.progress
        self.switch.set_theme("dark")

        self.assertEqual(self.switch._theme_animation.currentTime(), 180)
        self.assertEqual(self.switch.progress, progress)
        self.assertEqual(
            self.switch._theme_animation.state(), QAbstractAnimation.Running
        )

        self.switch.set_theme("dark", animate=False)
        self.assertEqual(self.switch.progress, 1.0)
        self.assertEqual(
            self.switch._theme_animation.state(), QAbstractAnimation.Stopped
        )

    def test_animation_midpoint_and_endpoint(self):
        self.switch.set_theme("dark")
        animation = self.switch._theme_animation

        animation.setCurrentTime(animation.duration() // 2)
        self.assertGreater(self.switch.progress, 0.0)
        self.assertLess(self.switch.progress, 1.0)
        self.assertAlmostEqual(self.switch.progress, 0.5, places=3)
        self.assertFalse(self.render_switch().isNull())

        animation.setCurrentTime(animation.duration())
        self.assertEqual(self.switch.progress, 1.0)
        self.assertEqual(animation.state(), QAbstractAnimation.Stopped)
        self.assertTrue(self.switch.isChecked())

        self.switch.set_theme("light")
        animation.setCurrentTime(animation.duration())
        self.assertEqual(self.switch.progress, 0.0)
        self.assertFalse(self.switch.isChecked())

    def test_animation_finishes_through_event_loop(self):
        self.switch.set_theme("dark")

        QTest.qWait(self.switch._theme_animation.duration() + 80)

        self.assertEqual(self.switch.progress, 1.0)
        self.assertEqual(
            self.switch._theme_animation.state(), QAbstractAnimation.Stopped
        )

    def test_invalid_theme_names_do_not_change_state(self):
        self.switch.set_theme("dark", animate=False)
        for invalid in ("blue", "", None, 1):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.switch.set_theme(invalid)
                self.assertEqual(self.switch.theme_name(), "dark")
                self.assertTrue(self.switch.isChecked())
                self.assertEqual(self.switch.progress, 1.0)

    def test_rendering_is_nonempty_at_zero_half_and_one(self):
        for progress in (0.0, 0.5, 1.0):
            with self.subTest(progress=progress):
                self.switch.setProperty("progress", progress)
                self.app.processEvents()
                image = self.render_switch()
                colors = {
                    image.pixel(x, y)
                    for x in range(image.width())
                    for y in range(image.height())
                }
                self.assertFalse(image.isNull())
                self.assertTrue(
                    any(
                        QColor(image.pixel(x, y)).alpha() > 0
                        for x in range(image.width())
                        for y in range(image.height())
                    )
                )
                self.assertGreater(len(colors), 30)

    def test_cloud_band_is_solid_extended_and_behind_sun(self):
        cloud = DayNightSwitch._cloud_band_path()
        overlap = QPointF(103.0, 50.0)
        self.assertEqual(cloud.fillRule(), Qt.WindingFill)
        self.assertTrue(cloud.contains(overlap))
        self.assertTrue(cloud.contains(QPointF(2.0, 62.0)))
        self.assertTrue(cloud.contains(QPointF(171.0, 30.0)))

        sun = QPainterPath()
        sun.addEllipse(QRectF(6.0, 6.0, 58.0, 58.0))
        self.assertTrue(cloud.intersects(sun))

        bounds = cloud.boundingRect()
        self.assertLess(bounds.left(), 0.0)
        self.assertGreater(bounds.bottom(), 70.0)
        self.assertGreater(bounds.right(), 180.0)
        self.assertGreater(bounds.top(), 15.0)

        self.switch.setProperty("progress", 0.0)
        self.app.processEvents()
        image = self.render_switch()
        pixel = self.pixel_at_logical(image, overlap.x(), overlap.y())
        self.assertEqual(pixel.alpha(), 255)
        self.assertGreater(pixel.red(), 245)
        self.assertGreater(pixel.green(), 245)
        self.assertGreater(pixel.blue(), 245)

        sun_pixel = self.pixel_at_logical(image, 35.0, 52.0)
        self.assertGreater(sun_pixel.red(), 200)
        self.assertLess(sun_pixel.blue(), 180)

    def test_day_and_night_have_two_halo_layers(self):
        layers = DayNightSwitch.HALO_LAYERS
        self.assertEqual(len(layers), 2)
        self.assertLess(layers[0][0], layers[1][0])
        self.assertGreater(layers[0][1], layers[1][1])

        self.switch.clearFocus()
        self.switch.set_theme("light", animate=False)
        day_image = self.render_switch()
        warm_halo = self.pixel_at_logical(day_image, 35.0, 3.0)
        clear_day = self.pixel_at_logical(day_image, 145.0, 3.0)
        self.assertGreater(warm_halo.red(), clear_day.red() + 15)
        self.assertGreater(warm_halo.green(), clear_day.green() + 5)

        self.switch.set_theme("dark", animate=False)
        night_image = self.render_switch()
        cool_halo = self.pixel_at_logical(night_image, 145.0, 3.0)
        clear_night = self.pixel_at_logical(night_image, 35.0, 3.0)
        self.assertGreater(cool_halo.green(), clear_night.green() + 10)
        self.assertGreater(cool_halo.blue(), clear_night.blue() + 15)

    def test_left_star_field_has_varied_sizes_and_soft_brightness(self):
        left_stars = [star for star in DayNightSwitch.STAR_FIELD if star[0] < 70.0]
        self.assertGreaterEqual(len(left_stars), 6)
        self.assertGreaterEqual(len({star[2] for star in left_stars}), 3)
        self.assertGreaterEqual(len({star[3] for star in left_stars}), 3)
        self.assertTrue(all(0 < star[2] < 2.0 for star in left_stars))
        self.assertTrue(all(80 <= star[3] <= 220 for star in left_stars))

    def test_hover_rebounds_and_disabled_state_stays_renderable(self):
        enter = QEvent(QEvent.Enter)
        QApplication.sendEvent(self.switch, enter)
        self.switch._hover_animation.setCurrentTime(
            self.switch._hover_animation.duration()
        )
        self.assertEqual(self.switch.hoverProgress, 1.0)

        leave = QEvent(QEvent.Leave)
        QApplication.sendEvent(self.switch, leave)
        self.switch._hover_animation.setCurrentTime(
            self.switch._hover_animation.duration()
        )
        self.assertEqual(self.switch.hoverProgress, 0.0)

        self.switch.setEnabled(False)
        self.assertFalse(self.render_switch().isNull())
        requests = QSignalSpy(self.switch.theme_requested)
        QTest.mouseClick(self.switch, Qt.LeftButton)
        self.assertEqual(len(requests), 0)
        self.assertEqual(self.switch.theme_name(), "light")


if __name__ == "__main__":
    unittest.main()
