import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMessageBox, QWidget

from bandscope.app.qt_bootstrap import configure_qt_plugin_path

configure_qt_plugin_path()

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.ui.toast import ToastManager


class FakeSlider:
    def __init__(self, minimum=0, maximum=100, value=50):
        self._minimum = minimum
        self._maximum = maximum
        self._value = value

    def minimum(self):
        return self._minimum

    def maximum(self):
        return self._maximum

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = value


class _AnalyzerStub(My3DAnalyzer):
    """绕过 __init__ 的最小实例，只挂测试需要的属性。"""

    def __init__(self):
        self.timeline_bar = SimpleNamespace(slider_time=FakeSlider())
        self.toast_manager = Mock()


class ToastRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_information_and_warning_go_to_toast(self):
        analyzer = _AnalyzerStub()
        analyzer._show_message("标题", "内容", QMessageBox.Information)
        analyzer.toast_manager.show.assert_called_with("内容", level="info", title="标题")

        analyzer.toast_manager.reset_mock()
        analyzer._show_message("标题", "内容", QMessageBox.Warning)
        analyzer.toast_manager.show.assert_called_with("内容", level="warning", title="标题")

    def test_critical_stays_modal(self):
        analyzer = _AnalyzerStub()
        fake_box = Mock()
        analyzer._create_message_box = Mock(return_value=fake_box)
        analyzer._show_message("严重", "内容", QMessageBox.Critical)
        analyzer.toast_manager.show.assert_not_called()
        fake_box.exec_.assert_called_once()

    def test_toast_falls_back_to_modal_before_manager_exists(self):
        analyzer = _AnalyzerStub()
        del analyzer.toast_manager
        fake_box = Mock()
        analyzer._create_message_box = Mock(return_value=fake_box)
        analyzer._show_message("标题", "内容", QMessageBox.Information)
        fake_box.exec_.assert_called_once()


class FrameStepShortcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _event(self, key, modifiers=Qt.NoModifier):
        event = Mock()
        event.key = Mock(return_value=key)
        event.modifiers = Mock(return_value=modifiers)
        return event

    def _analyzer(self):
        analyzer = _AnalyzerStub()
        analyzer._focus_blocks_frame_step = Mock(return_value=False)
        return analyzer

    def test_arrow_steps_one_frame(self):
        analyzer = self._analyzer()
        self.assertTrue(analyzer._handle_frame_step_shortcut(self._event(Qt.Key_Right)))
        self.assertEqual(analyzer.timeline_bar.slider_time.value(), 51)
        analyzer._handle_frame_step_shortcut(self._event(Qt.Key_Left))
        self.assertEqual(analyzer.timeline_bar.slider_time.value(), 50)

    def test_shift_steps_ten_frames(self):
        analyzer = self._analyzer()
        analyzer._handle_frame_step_shortcut(self._event(Qt.Key_Right, Qt.ShiftModifier))
        self.assertEqual(analyzer.timeline_bar.slider_time.value(), 60)

    def test_clamps_at_bounds(self):
        analyzer = self._analyzer()
        analyzer.timeline_bar.slider_time = FakeSlider(0, 100, 98)
        analyzer._handle_frame_step_shortcut(self._event(Qt.Key_Right, Qt.ShiftModifier))
        self.assertEqual(analyzer.timeline_bar.slider_time.value(), 100)
        analyzer.timeline_bar.slider_time = FakeSlider(0, 100, 3)
        analyzer._handle_frame_step_shortcut(self._event(Qt.Key_Left, Qt.ShiftModifier))
        self.assertEqual(analyzer.timeline_bar.slider_time.value(), 0)

    def test_ignored_when_focus_in_editable_or_with_wrong_modifiers(self):
        analyzer = self._analyzer()
        analyzer._focus_blocks_frame_step = Mock(return_value=True)
        self.assertFalse(analyzer._handle_frame_step_shortcut(self._event(Qt.Key_Right)))
        self.assertEqual(analyzer.timeline_bar.slider_time.value(), 50)

        analyzer = self._analyzer()
        self.assertFalse(analyzer._handle_frame_step_shortcut(self._event(Qt.Key_Right, Qt.ControlModifier)))
        self.assertEqual(analyzer.timeline_bar.slider_time.value(), 50)

    def test_ignored_when_no_frame_range(self):
        analyzer = self._analyzer()
        analyzer.timeline_bar.slider_time = FakeSlider(0, 0, 0)
        self.assertFalse(analyzer._handle_frame_step_shortcut(self._event(Qt.Key_Right)))


class ToastManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        # 立即销毁所有 Toast 并处理 DeferredDelete，避免挂起定时器在进程退出时崩溃
        for widget in QApplication.topLevelWidgets():
            manager = getattr(widget, "_toast_manager_for_test", None)
            if manager is not None:
                manager.clear()
        QApplication.sendPostedEvents(None, 2)  # QEvent.DeferredDelete
        self.app.processEvents()

    def _make_manager(self):
        parent = QWidget()
        parent.resize(1200, 800)
        manager = ToastManager(parent)
        parent._toast_manager_for_test = manager
        self._parents.append(parent)
        return manager

    def setUp(self):
        self._parents = []

    def test_stacks_and_caps_toasts(self):
        parent = QWidget()
        parent.resize(1200, 800)
        manager = ToastManager(parent)
        parent._toast_manager_for_test = manager
        for i in range(6):
            manager.show(f"消息 {i}", level="info")
        live = [t for t in manager._toasts if t is not None]
        self.assertLessEqual(len(live), 4)
        manager._reposition()
        ys = [t.y() for t in manager._toasts]
        self.assertEqual(ys, sorted(ys))
        manager.clear()
        QApplication.sendPostedEvents(None, 2)
        self.app.processEvents()
        parent.close()

    def test_invalid_level_falls_back_to_info(self):
        parent = QWidget()
        manager = ToastManager(parent)
        parent._toast_manager_for_test = manager
        toast = manager.show("普通消息", level="not-a-level")
        self.assertIsNotNone(toast)
        manager.clear()
        QApplication.sendPostedEvents(None, 2)
        self.app.processEvents()
        parent.close()


if __name__ == "__main__":
    unittest.main()
