# -*- coding: utf-8 -*-
"""统一区间控制器的真实控件同步测试（离屏，无需 VTK）。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication, QDoubleSpinBox, QLabel, QPushButton

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.core.axis_interval import AxisSpace
from bandscope.ui.axis_interval_controller import (
    AxisIntervalController,
    IntervalEditMode,
)
from bandscope.ui.ui_controls import SyncedSlider


configure_qt_plugin_path()


def _slider():
    widget = SyncedSlider()
    widget.setFixedHeight(32)
    return widget


class _Harness:
    """一组真实控件 + 控制器，模拟右侧卡片与底栏同时存在的场景。"""

    def __init__(self, coords, *, decimals_source="file"):
        self.space = AxisSpace("X", coords, source=decimals_source)
        self.slider_up = _slider()
        self.slider_low = _slider()
        self.slider_position = _slider()
        self.box_up = QDoubleSpinBox()
        self.box_low = QDoubleSpinBox()
        self.box_position = QDoubleSpinBox()
        self.box_length = QDoubleSpinBox()
        self.button_lock = QPushButton()
        self.button_lock.setCheckable(True)
        self.label_lock = QLabel()

        self.controller = AxisIntervalController(slider_steps=1000)
        self.changed = []
        self.committed = []
        self.locks = []
        self.controller.intervalChanged.connect(lambda: self.changed.append(True))
        self.controller.intervalCommitted.connect(lambda: self.committed.append(True))
        self.controller.lockChanged.connect(self.locks.append)
        self.controller.attach(
            slider_up=self.slider_up,
            box_up=self.box_up,
            slider_low=self.slider_low,
            box_low=self.box_low,
            box_length=self.box_length,
            button_lock=self.button_lock,
            slider_position=self.slider_position,
            box_position=self.box_position,
            label_lock=self.label_lock,
        )
        self.controller.bind(self.space, self.space.as_interval())
        self.controller.set_mode(IntervalEditMode.FULL)


class AxisIntervalControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.harness = _Harness(np.linspace(-1.0, 1.0, 5))

    def test_full_range_initialises_every_widget(self):
        h = self.harness
        self.assertEqual(h.slider_low.value(), 0)
        self.assertEqual(h.slider_up.value(), 1000)
        self.assertAlmostEqual(h.box_low.value(), -1.0, places=6)
        self.assertAlmostEqual(h.box_up.value(), 1.0, places=6)
        self.assertAlmostEqual(h.box_length.value(), 2.0, places=6)
        self.assertFalse(h.button_lock.isChecked())
        self.assertEqual(h.label_lock.text(), "● 区间未锁定")

    def test_slider_drag_updates_every_linked_widget_once_per_event(self):
        h = self.harness
        h.slider_up.setValue(750)

        expected_up = h.space.minimum + h.space.physical_span * 0.75
        self.assertAlmostEqual(h.controller.interval.up, expected_up, places=9)
        self.assertAlmostEqual(h.box_up.value(), expected_up, places=6)
        # 位置滑条与长度输入框都跟着同一个模型走。
        self.assertEqual(h.slider_position.value(), (0 + 750) // 2)
        self.assertAlmostEqual(h.box_length.value(), expected_up - h.space.minimum, places=6)
        self.assertEqual(h.changed, [True])
        self.assertEqual(h.committed, [])

    def test_slider_release_commits_exactly_once(self):
        h = self.harness
        h.slider_up.setValue(600)
        h.controller.intervalCommitted.emit()
        self.assertEqual(len(h.committed), 1)

    def test_box_edit_commits_without_a_separate_preview(self):
        h = self.harness
        h.box_up.setValue(0.5)
        h.box_up.editingFinished.emit()

        self.assertAlmostEqual(h.controller.interval.up, 0.5, places=9)
        self.assertEqual(h.committed, [True])
        self.assertEqual(h.changed, [])
        # 其它联动控件同步到新值。
        self.assertAlmostEqual(h.box_length.value(), 1.5, places=6)
        self.assertEqual(h.slider_up.value(), 750)

    def test_box_input_keeps_full_precision_on_a_fine_axis(self):
        harness = _Harness(np.arange(0.0, 1.001, 0.001))
        harness.box_low.setValue(0.1234)
        harness.box_low.editingFinished.emit()

        self.assertAlmostEqual(harness.controller.interval.low, 0.1234, places=9)
        state = harness.controller.interval.as_dict()
        self.assertAlmostEqual(state["low"], 0.1234, places=9)
        self.assertEqual(harness.box_low.value(), 0.1234)

    def test_length_box_resizes_around_the_center(self):
        h = self.harness
        h.box_length.setValue(1.0)
        h.box_length.editingFinished.emit()

        self.assertAlmostEqual(h.controller.interval.length, 1.0, places=9)
        self.assertAlmostEqual(h.controller.interval.center, 0.0, places=9)
        self.assertAlmostEqual(h.box_low.value(), -0.5, places=6)
        self.assertAlmostEqual(h.box_up.value(), 0.5, places=6)

    def test_position_slider_pans_without_changing_the_length(self):
        h = self.harness
        h.box_length.setValue(0.5)
        h.box_length.editingFinished.emit()
        h.controller.interval.set_center(0.0)
        h.controller.sync_widgets()

        h.slider_position.setValue(1000)

        # 区间只能平移到 [low, up] 贴住轴上边界为止：+0.5 → +0.75。
        self.assertAlmostEqual(h.controller.interval.center, 0.75, places=9)
        self.assertAlmostEqual(h.controller.interval.length, 0.5, places=9)
        self.assertAlmostEqual(h.controller.interval.up, 1.0, places=9)

    def test_locking_makes_the_length_box_read_only_and_translates_endpoints(self):
        h = self.harness
        h.box_low.setValue(-0.4)
        h.box_low.editingFinished.emit()
        h.box_up.setValue(0.4)
        h.box_up.editingFinished.emit()
        h.changed.clear()
        h.committed.clear()

        h.button_lock.click()

        self.assertEqual(h.locks, [True])
        self.assertTrue(h.controller.interval.locked)
        self.assertEqual(h.label_lock.text(), "● 区间已锁定")
        self.assertTrue(h.box_length.isReadOnly())

        # 锁定期改上限：整体平移，长度与被锁定的下限一起移动。
        h.slider_up.setValue(750)
        self.assertAlmostEqual(h.controller.interval.length, 0.8, places=9)
        self.assertAlmostEqual(h.controller.interval.up, 0.5, places=9)
        self.assertAlmostEqual(h.controller.interval.low, -0.3, places=9)

    def test_unlocking_restores_independent_endpoints(self):
        h = self.harness
        h.box_length.setValue(0.5)
        h.box_length.editingFinished.emit()
        h.button_lock.click()
        h.button_lock.click()

        self.assertFalse(h.controller.interval.locked)
        self.assertFalse(h.box_length.isReadOnly())

        h.box_low.setValue(-0.9)
        h.box_low.editingFinished.emit()
        self.assertAlmostEqual(h.controller.interval.low, -0.9, places=9)
        self.assertAlmostEqual(h.controller.interval.up, 0.25, places=9)

    def test_programmatic_sync_does_not_request_a_business_update(self):
        h = self.harness
        h.controller.interval.set_length(0.5)
        h.controller.sync_widgets()

        self.assertEqual(h.changed, [])
        self.assertEqual(h.committed, [])

    def test_position_mode_disables_interval_editing(self):
        h = self.harness
        h.controller.interval.set_length(0.0)
        h.controller.set_mode(IntervalEditMode.POSITION)

        self.assertFalse(h.slider_up.isEnabled())
        self.assertFalse(h.slider_low.isEnabled())
        self.assertFalse(h.box_up.isEnabled())
        self.assertFalse(h.box_low.isEnabled())
        self.assertFalse(h.box_length.isEnabled())
        self.assertFalse(h.button_lock.isEnabled())
        self.assertTrue(h.slider_position.isEnabled())
        self.assertTrue(h.box_position.isEnabled())

    def test_position_mode_still_pans(self):
        h = self.harness
        h.controller.interval.set_length(0.0)
        h.controller.set_mode(IntervalEditMode.POSITION)

        h.slider_position.setValue(500)
        self.assertAlmostEqual(h.controller.interval.low, 0.0, places=9)
        self.assertAlmostEqual(h.controller.interval.up, 0.0, places=9)

    def test_disabled_mode_turns_everything_off(self):
        h = self.harness
        h.controller.set_mode(IntervalEditMode.DISABLED)
        for widget in (
            h.slider_up,
            h.slider_low,
            h.slider_position,
            h.box_up,
            h.box_low,
            h.box_position,
            h.box_length,
            h.button_lock,
        ):
            self.assertFalse(widget.isEnabled())

    def test_switching_axis_resets_to_full_range_unlocked(self):
        h = self.harness
        h.controller.interval.set_locked(True)
        h.controller.interval.set_length(0.5)

        new_space = AxisSpace("Y", np.linspace(-2.0, 2.0, 9))
        h.controller.reset_for_space(new_space)

        self.assertEqual((h.controller.interval.low, h.controller.interval.up), (-2.0, 2.0))
        self.assertFalse(h.controller.interval.locked)
        self.assertEqual(h.slider_low.value(), 0)
        self.assertEqual(h.slider_up.value(), 1000)

    def test_rebinding_an_existing_page_restores_its_interval(self):
        h = self.harness
        interval = AxisSpace("X", np.linspace(-1.0, 1.0, 5)).as_interval(low=-0.5, up=0.5, locked=True)
        h.controller.bind(AxisSpace("X", np.linspace(-1.0, 1.0, 5)), interval)

        self.assertEqual(h.slider_up.value(), 750)
        self.assertEqual(h.slider_low.value(), 250)
        self.assertTrue(h.button_lock.isChecked())

    def test_box_wider_than_the_axis_is_clamped_into_the_model(self):
        h = self.harness
        h.box_up.setValue(99.0)
        h.box_up.editingFinished.emit()

        self.assertAlmostEqual(h.controller.interval.up, 1.0, places=9)
        # 显示被规范化回模型值。
        self.assertAlmostEqual(h.box_up.value(), 1.0, places=6)

    def test_slider_tooltip_reports_the_physical_value(self):
        h = self.harness
        h.slider_up.setValue(500)
        self.assertIn("kx", h.slider_up.toolTip())
        self.assertIn("0.0", h.slider_up.toolTip())

    def test_reverse_coords_still_map_endpoints_onto_the_slider(self):
        harness = _Harness(np.array([0.0, -1.0, -2.0]))
        self.assertAlmostEqual(harness.controller.interval.low, -2.0, places=9)
        self.assertAlmostEqual(harness.controller.interval.up, 0.0, places=9)
        self.assertEqual(harness.slider_low.value(), 0)
        self.assertEqual(harness.slider_up.value(), 1000)

    def test_zero_width_axis_is_safe(self):
        # 单采样轴：AxisSpace 补出一个极小跨度，控件仍然可用且不越界。
        harness = _Harness(np.array([3.0]))
        self.assertAlmostEqual(harness.controller.interval.low, 3.0, places=9)
        harness.box_length.setValue(1.0)
        harness.box_length.editingFinished.emit()
        self.assertAlmostEqual(
            harness.controller.interval.length,
            harness.space.physical_span,
            places=9,
        )
        self.assertEqual(harness.slider_low.value(), 0)
        self.assertEqual(harness.slider_up.value(), 1000)


if __name__ == "__main__":
    unittest.main()
