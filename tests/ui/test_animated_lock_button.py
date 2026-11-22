import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QAbstractAnimation, QSignalBlocker
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.ui import theme
from bandscope.ui.page_render_control import RenderControlPage
from bandscope.ui.ui_controls import AnimatedLockButton


configure_qt_plugin_path()


class AnimatedLockButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.button = AnimatedLockButton()
        theme.style_push_button(self.button, "secondary")
        self.button.show()

    def tearDown(self):
        self.button._lock_animation.stop()
        self.button.close()
        self.button.deleteLater()
        self.app.processEvents()

    def test_toggle_animates_shackle_between_open_and_closed(self):
        self.assertEqual(self.button.lockProgress, 0.0)
        self.assertEqual(self.button.text(), "")

        self.button.click()
        self.assertTrue(self.button.isChecked())
        self.assertEqual(self.button._lock_animation.state(), QAbstractAnimation.Running)
        self.button._lock_animation.setCurrentTime(self.button._lock_animation.duration() // 2)
        self.assertGreater(self.button.lockProgress, 0.0)
        self.assertLess(self.button.lockProgress, 1.0)
        self.button._lock_animation.setCurrentTime(self.button._lock_animation.duration())
        self.assertEqual(self.button.lockProgress, 1.0)

        self.button.click()
        self.button._lock_animation.setCurrentTime(self.button._lock_animation.duration())
        self.assertFalse(self.button.isChecked())
        self.assertEqual(self.button.lockProgress, 0.0)
        self.assertFalse(self.button.grab().isNull())

    def test_signal_blocked_restore_updates_icon_immediately(self):
        blocker = QSignalBlocker(self.button)
        self.button.setChecked(True)
        del blocker

        self.assertTrue(self.button.isChecked())
        self.assertEqual(self.button.lockProgress, 1.0)
        self.assertEqual(self.button._lock_animation.state(), QAbstractAnimation.Stopped)

        blocker = QSignalBlocker(self.button)
        self.button.setChecked(False)
        del blocker

        self.assertFalse(self.button.isChecked())
        self.assertEqual(self.button.lockProgress, 0.0)

    def test_animation_finishes_through_the_qt_event_loop(self):
        self.button.click()
        QTest.qWait(self.button._lock_animation.duration() + 60)

        self.assertEqual(self.button.lockProgress, 1.0)
        self.assertEqual(self.button._lock_animation.state(), QAbstractAnimation.Stopped)

    def test_render_page_uses_icon_only_lock_button(self):
        page = RenderControlPage()
        self.addCleanup(page.deleteLater)

        self.assertIsInstance(page.btn_lock, AnimatedLockButton)
        self.assertEqual(page.btn_lock.attachment().text(), "")
        self.assertEqual(page.btn_lock.accessibleName(), "锁定全部强度范围")
        self.assertIn("颜色与不透明度共用该范围", page.btn_lock.toolTip())
        self.assertIn("不会禁止编辑色阶或曲线", page.btn_lock.toolTip())
        self.assertIsNone(page.btn_lock.property("fluentIconName"))
        self.assertNotIn(page.btn_lock, page._adaptive_buttons)


if __name__ == "__main__":
    unittest.main()
