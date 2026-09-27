"""右上控制页的滚动：滚动条显隐要跟着内容高度走，滚轮要能滚动整页。

回归背景：``SiScrollArea`` 只在自身 ``resizeEvent`` 里更新滚动条。控制页是靠
``container.adjustSize()`` 改变内容高度的（卡片显隐动画、自适应布局），滚动区域
自身尺寸没变，于是内容高出视口后滚动条仍然隐藏，而 ``wheelEvent`` 里的
``scroll_bar_vertical.isVisible()`` 判断又把滚轮一起挡掉，底部卡片彻底够不着。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, QSize, Qt
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.ui.camera_view_controls import CameraPose
from bandscope.ui.control_layout_utils import refresh_scroll_bars
from bandscope.ui.page_render_control import RenderControlPage
from bandscope.app.qt_bootstrap import configure_qt_plugin_path


configure_qt_plugin_path()


def wheel_event(delta, *, pos=QPoint(10, 10)):
    return QWheelEvent(
        pos,
        pos,
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.NoScrollPhase,
        False,
    )


class _ScrollStub:
    """只记录 resizeEvent 调用的滚动区域替身。"""

    def __init__(self, attachment):
        self.attachment_ = attachment
        self.resized = 0

    def size(self):
        return QSize(300, 500)

    def resizeEvent(self, _event):
        self.resized += 1


class _SizeStub:
    def __init__(self, width, height):
        self._width = width
        self._height = height

    def width(self):
        return self._width

    def height(self):
        return self._height


class RefreshScrollBarsTests(unittest.TestCase):
    def test_degenerate_attachment_is_skipped(self):
        empty = _ScrollStub(_SizeStub(0, 0))
        refresh_scroll_bars(empty)
        self.assertEqual(empty.resized, 0)

    def test_missing_scroll_area_is_ignored(self):
        refresh_scroll_bars(None)

    def test_non_degenerate_attachment_triggers_recalculation(self):
        scroll = _ScrollStub(_SizeStub(300, 500))
        refresh_scroll_bars(scroll)
        self.assertEqual(scroll.resized, 1)


class ControlPageScrollingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.page = RenderControlPage()
        self.page.resize(420, 900)
        self.page.show()

    def tearDown(self):
        self.page.hide()
        self.page.deleteLater()

    def _bar(self):
        return self.page.scroll.scroll_bar_vertical

    def test_scroll_bar_follows_the_content_height(self):
        # 视口比内容高：不需要滚动条。
        self.page.resize(420, 4000)
        self.page.relayout_scroll_content()
        self.assertTrue(self._bar().isHidden())

        # 视口变矮：内容装不下，必须把滚动条放出来。
        self.page.resize(420, 500)
        self.page.relayout_scroll_content()
        self.assertFalse(self._bar().isHidden())
        self.assertGreater(
            self.page.container.height(), self.page.scroll.height()
        )

    def test_content_growth_refreshes_the_bar_without_a_viewport_resize(self):
        # 视口先高到装得下所有卡片。
        self.page.resize(420, 4000)
        self.page.relayout_scroll_content()
        self.assertTrue(self._bar().isHidden())

        # 直接让内容变高（模拟卡片展开），滚动区域自身尺寸不变。
        tall = self.page.scroll.height() + self.page.container.height()
        self.page.container.setFixedHeight(tall)
        self.assertTrue(self._bar().isHidden())

        self.page.relayout_scroll_content()

        self.assertFalse(self._bar().isHidden())
        self.page.container.setMinimumHeight(0)
        self.page.container.setMaximumHeight(16777215)

    def test_wheel_scrolls_the_page(self):
        self.page.resize(420, 500)
        self.page.relayout_scroll_content()
        scroll = self.page.scroll
        self.assertFalse(self._bar().isHidden())

        event = wheel_event(-120)
        scroll.wheelEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertLess(scroll.widget_scroll_animation.target()[1], 0)
        QTest.qWait(600)
        self.assertLess(self.page.container.y(), 0)
        self.assertGreaterEqual(
            self.page.container.y(),
            self.page.scroll.height() - self.page.container.height(),
        )

    def test_wheel_does_nothing_when_everything_fits(self):
        self.page.resize(420, 4000)
        self.page.relayout_scroll_content()
        event = wheel_event(-120)

        self.page.scroll.wheelEvent(event)

        self.assertEqual(self.page.container.y(), 0)

    def test_camera_spin_box_leaves_the_wheel_to_the_page(self):
        panel = self.page.camera_panel
        panel.sync_pose(CameraPose(10.0, 5.0, 0.0, 500.0), force=True)
        box = panel._boxes["azimuth"]

        box.clearFocus()
        event = wheel_event(-120)
        QApplication.sendEvent(box, event)

        self.assertFalse(event.isAccepted())
        self.assertAlmostEqual(box.value(), 10.0)

    def test_focused_camera_spin_box_still_steps_with_the_wheel(self):
        panel = self.page.camera_panel
        panel.sync_pose(CameraPose(10.0, 5.0, 0.0, 500.0), force=True)
        box = panel._boxes["azimuth"]
        # 离屏环境拿不到真实键盘焦点，这里直接替换判定条件。
        box._wheel_adjusts_value = lambda: True

        QApplication.sendEvent(box, wheel_event(-120))

        self.assertAlmostEqual(box.value(), 9.0)


if __name__ == "__main__":
    unittest.main()
