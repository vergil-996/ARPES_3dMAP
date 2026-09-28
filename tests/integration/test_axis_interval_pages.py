# -*- coding: utf-8 -*-
"""积分区间接入页面、底栏与运算的集成测试（离屏，无需 VTK）。

覆盖三类契约：

- 区间模型到采样下标、再到实际求和结果的数值链路；
- 页面切换时各自恢复自己的物理区间与锁定状态；单层切片页移动位置只更新
  产生该切面的源范围，保留面内裁剪与裁空链；
- 底栏位置组在无时间轴时占用原时间轴位置，窄窗下视图控件换行。
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.axis_interval import AxisInterval, AxisSpace
from bandscope.ui.axis_interval_controller import AxisIntervalController, IntervalEditMode
from bandscope.ui.page_data_process_v2 import DataProcessPage
from bandscope.ui.result_workspace import AnalysisPageSpec
from bandscope.ui.timeline_bar import TimelineBar

configure_qt_plugin_path()


def _spec(page_id, kind="axis_integral", **params):
    return AnalysisPageSpec(page_id, page_id, kind, "test", params=params)


def _wait_for_animation(widget, *, timeout_ms=4000):
    """等到宽度/高度动画落定；固定 sleep 在慢机器上会偶发失败。"""
    animation = getattr(widget, "_visibility_animation", None)
    waited = 0
    while animation is not None and waited < timeout_ms:
        QTest.qWait(25)
        waited += 25
        animation = getattr(widget, "_visibility_animation", None)
    return animation is None


class _Workspace:
    """最小工作区替身：只提供区间接入需要的当前页与页面查询。"""

    def __init__(self, spec):
        self.current = spec

    def current_spec(self):
        return self.current

    def page_by_id(self, page_id):
        return self.current if self.current is not None and self.current.page_id == page_id else None


class _AxisHarness:
    """真实控件 + 真实核心 + 合成坐标的积分区间环境。"""

    def __init__(self, coords=None, *, t_max=3):
        self.core = AnalyzerCore()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "axis-interval.npz"
            kx = np.linspace(-1.0, 1.0, 11) if coords is None else np.asarray(coords, dtype=np.float64)
            np.savez(
                path,
                sample=np.arange(11 * 5 * 6 * (t_max + 1), dtype=np.float32).reshape(
                    11, 5, 6, t_max + 1
                ),
                kx=kx,
                ky=np.linspace(-2.0, 2.0, 5),
                E=np.linspace(-3.0, 3.0, 6),
                time=np.linspace(0.0, 30.0, t_max + 1),
            )
            success, info = self.core.load_npz(str(path))
        assert success, f"合成数据加载失败: {info}"

        self.page_data = DataProcessPage()
        self.bar = TimelineBar()
        self.controller = AxisIntervalController()
        self.controller.attach(
            slider_up=self.page_data.s_ax_up,
            box_up=self.page_data.input_ax_up,
            slider_low=self.page_data.s_ax_low,
            box_low=self.page_data.input_ax_low,
            box_length=self.page_data.input_ax_length,
            button_lock=self.page_data.btn_ax_lock,
            slider_position=self.bar.slider_axis,
            box_position=self.bar.input_axis,
        )

        self.analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        self.analyzer.core = self.core
        self.analyzer.page_data = self.page_data
        self.analyzer.timeline_bar = self.bar
        self.analyzer.axis_interval_controller = self.controller
        self.analyzer.axis_space = None
        self.analyzer.axis_interval = None
        self.analyzer.axis_source_mode = "frame"
        self.analyzer._syncing_controls = False
        self.analyzer.active_page_spec = None
        self.analyzer.home_slice_info = None
        self.analyzer.precise_logical_bounds = None
        self.analyzer.last_synced_slice_texts = None
        self.analyzer.left_workspace = _Workspace(None)
        # 页面切换链路会走到选择盒与刷新管线；这里只关心区间与页面参数。
        self.refreshes = []
        self.analyzer._can_show_interactive_box = lambda: False
        self.analyzer.sync_ax_sliders_to_box = lambda: None
        self.analyzer.request_refresh = lambda *args, **kwargs: self.refreshes.append(args[:1])

    def bind(self, spec, *, animate=False):
        self.analyzer.active_page_spec = spec
        self.analyzer.left_workspace.current = spec
        self.analyzer._bind_axis_interval(spec, animate=animate)
        return self.analyzer.axis_interval


class IntervalNumericTests(unittest.TestCase):
    """区间 → 采样下标 → 实际求和：函数值必须和模型一致。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _sum_for(coords, volume, interval):
        low_idx, up_idx = interval.to_indices(coords)
        return float(np.sum(volume[low_idx:up_idx + 1])), (low_idx, up_idx)

    def test_uniform_coords_match_hand_computed_indices(self):
        coords = np.linspace(0.0, 1.0, 11)
        volume = np.arange(11, dtype=np.float64)
        interval = AxisInterval(0.0, 1.0, low=0.2, up=0.6)
        total, indices = self._sum_for(coords, volume, interval)
        self.assertEqual(indices, (2, 6))
        self.assertEqual(total, float(np.sum(volume[2:7])))

    def test_endpoints_between_samples_include_both_neighbours(self):
        coords = np.linspace(0.0, 1.0, 11)
        volume = np.arange(11, dtype=np.float64)
        interval = AxisInterval(0.0, 1.0, low=0.24, up=0.56)
        _total, indices = self._sum_for(coords, volume, interval)
        self.assertEqual(indices, (2, 6))

    def test_non_uniform_coords(self):
        coords = np.array([-3.0, -0.2, 0.0, 4.0])
        volume = np.array([1.0, 2.0, 4.0, 8.0])
        interval = AxisInterval(-3.0, 4.0, low=-0.1, up=3.0)
        total, indices = self._sum_for(coords, volume, interval)
        self.assertEqual(indices, (1, 3))
        self.assertEqual(total, 14.0)

    def test_reverse_coords_sum_the_sorted_index_window(self):
        coords = np.array([2.0, 1.0, 0.0, -1.0])
        volume = np.array([1.0, 2.0, 4.0, 8.0])
        interval = AxisInterval(-1.0, 2.0, low=0.9, up=1.9)
        total, indices = self._sum_for(coords, volume, interval)
        self.assertEqual(indices, (0, 1))
        self.assertEqual(total, 3.0)

    def test_fine_step_coords_keep_sub_sample_endpoints(self):
        coords = np.arange(0.0, 1.0001, 0.001)
        volume = np.ones(coords.size, dtype=np.float64)
        interval = AxisInterval(0.0, 1.0, low=0.1004, up=0.2006)
        total, indices = self._sum_for(coords, volume, interval)
        self.assertEqual(indices, (100, 201))
        self.assertAlmostEqual(total, 102.0)

    def test_zero_length_interval_sums_one_sample(self):
        coords = np.linspace(0.0, 4.0, 5)
        volume = np.array([1.0, 2.0, 4.0, 8.0, 16.0])
        interval = AxisInterval(0.0, 4.0, low=2.0, up=2.0)
        total, indices = self._sum_for(coords, volume, interval)
        self.assertEqual(indices, (2, 2))
        self.assertEqual(total, 4.0)


class PageIntervalBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.harness = _AxisHarness()

    def test_integral_page_restores_its_own_interval_and_lock(self):
        spec = _spec("integral", low=2, up=8, mid=5)
        spec.params["axis_interval"] = AxisSpace(
            "X", self.harness.core.coords["X"]
        ).as_interval(low=-0.6, up=0.6, locked=True).as_dict()

        interval = self.harness.bind(spec)

        self.assertAlmostEqual(interval.low, -0.6, places=9)
        self.assertAlmostEqual(interval.up, 0.6, places=9)
        self.assertTrue(interval.locked)
        self.assertTrue(self.harness.controller.interval is interval)
        self.assertEqual(self.harness.controller.mode, IntervalEditMode.FULL)
        self.assertTrue(self.harness.page_data.s_ax_up.isEnabled())

    def test_two_pages_keep_independent_state(self):
        first = _spec("first", low=1, up=3, mid=2)
        # 没有历史索引范围的页面从完整轴范围、未锁定开始。
        second = _spec("second")

        self.harness.bind(first)
        self.harness.controller.interval.set_length(0.4)
        self.harness.controller.interval.set_center(0.2)
        self.harness.controller.set_locked(True)
        self.harness.analyzer._persist_axis_interval_state(first)

        self.harness.bind(second)
        self.assertAlmostEqual(
            self.harness.analyzer.axis_interval.length,
            self.harness.analyzer.axis_space.physical_span,
            places=9,
        )
        self.assertFalse(self.harness.controller.interval.locked)

        self.harness.bind(first)
        self.assertAlmostEqual(self.harness.analyzer.axis_interval.length, 0.4, places=9)
        self.assertTrue(self.harness.controller.interval.locked)
        self.assertTrue(self.harness.page_data.btn_ax_lock.isChecked())

    def test_legacy_page_migrates_its_integer_range(self):
        spec = _spec("legacy", low=2, up=8, mid=5)

        interval = self.harness.bind(spec)

        coords = self.harness.core.coords["X"]
        self.assertAlmostEqual(interval.low, float(coords[2]), places=9)
        self.assertAlmostEqual(interval.up, float(coords[8]), places=9)
        # 旧的 locked_half_width 不解释成新锁定状态。
        self.assertFalse(interval.locked)

    def test_position_mode_collapses_the_interval_to_a_point(self):
        spec = _spec("slice", kind="home", home_slice_info={"axis": 0, "index": 4})

        interval = self.harness.bind(spec)

        self.assertEqual(self.harness.controller.mode, IntervalEditMode.POSITION)
        self.assertAlmostEqual(interval.low, interval.up, places=9)
        self.assertFalse(self.harness.page_data.s_ax_up.isEnabled())
        self.assertFalse(self.harness.page_data.input_ax_length.isEnabled())
        self.assertTrue(self.harness.bar.slider_axis.isEnabled())

    def test_3d_derivative_page_disables_the_controls(self):
        spec = _spec(
            "deriv3d", kind="second_derivative", source_view="3d", source_page_kind="home"
        )

        self.harness.bind(spec)

        self.assertEqual(self.harness.controller.mode, IntervalEditMode.DISABLED)
        self.assertIsNone(self.harness.analyzer.axis_interval)
        self.assertTrue(self.harness.bar.axis_group.isHidden())

    def test_persist_derives_integer_range_from_physical_interval(self):
        spec = _spec("integral", low=0, up=10, mid=5)
        self.harness.bind(spec)
        self.harness.controller.interval.set_up(0.6)
        self.harness.controller.interval.set_low(-0.6)
        self.harness.analyzer._persist_axis_interval_state(spec)

        coords = self.harness.core.coords["X"]
        self.assertEqual(spec.params["low"], 2)
        self.assertEqual(spec.params["up"], 8)
        self.assertEqual(spec.params["mid"], 5)
        self.assertAlmostEqual(spec.params["axis_interval"]["up"], float(coords[8]), places=9)

    def test_new_page_keeps_sub_sample_endpoints_but_starts_unlocked(self):
        spec = _spec("integral", low=0, up=10, mid=5)
        self.harness.bind(spec)
        self.harness.controller.interval.set_length(0.37)
        self.harness.controller.interval.set_center(0.111)
        self.harness.controller.set_locked(True)

        params = self.harness.analyzer._build_axis_request_params()

        interval = AxisInterval.from_dict(params["axis_interval"])
        self.assertAlmostEqual(interval.low, self.harness.analyzer.axis_interval.low, places=9)
        self.assertAlmostEqual(interval.up, self.harness.analyzer.axis_interval.up, places=9)
        # 端点不在采样点上：整数下标表达不了，物理区间必须原样带走。
        self.assertNotAlmostEqual(interval.low, float(self.harness.core.coords["X"][0]), places=9)
        # 锁定是编辑方式，新页面从头开始未锁定。
        self.assertFalse(interval.locked)

    def test_switching_the_axis_direction_rebuilds_for_the_new_axis(self):
        # 回归：下拉是轴向选择器时，必须先让页面参数跟上再重建区间，
        # 否则旧轴的物理区间会被映射到新轴的下标上。
        spec = _spec("integral", axis_index=0, low=2, up=8, mid=5)
        self.harness.bind(spec)

        self.harness.page_data.combo_ax.setCurrentIndex(2)
        self.harness.analyzer.on_axis_selection_changed(2)

        self.assertEqual(spec.params["axis_index"], 2)
        self.assertEqual(spec.params["axis_name"], "Z轴")
        self.assertEqual(self.harness.analyzer.axis_space.key, "E")
        self.assertEqual(self.harness.bar.axis_title_label.text(), "E")
        # 新轴的完整范围：E 轴 6 个采样点 → 0..5。
        interval = self.harness.analyzer.axis_interval
        self.assertAlmostEqual(interval.low, float(self.harness.core.coords["E"][0]), places=9)
        self.assertAlmostEqual(interval.up, float(self.harness.core.coords["E"][-1]), places=9)
        self.assertEqual((spec.params["low"], spec.params["up"]), (0, 5))
        self.assertEqual(spec.params["axis_interval"]["axis_key"], "E")

    def test_disabled_page_clears_the_controller_and_the_lock_light(self):
        spec = _spec("integral", low=2, up=8, mid=5)
        self.harness.bind(spec)
        self.harness.controller.set_locked(True)
        self.assertTrue(self.harness.page_data.btn_ax_lock.isChecked())

        self.harness.bind(_spec("time", kind="time_integral"))

        self.assertEqual(self.harness.controller.mode, IntervalEditMode.DISABLED)
        self.assertIsNone(self.harness.controller.interval)
        self.assertFalse(self.harness.page_data.s_ax_up.isEnabled())
        self.assertFalse(self.harness.page_data.btn_ax_lock.isChecked())

    def test_committing_a_box_on_a_3d_page_still_persists_the_interval(self):
        # 回归：3D 主页上只有输入框提交、没有拖动滑条时，区间同样要写回页面
        # 参数，否则切页回来会退回完整范围而输入框还停在用户输入的值上。
        spec = _spec("home", kind="home")
        self.harness.bind(spec)

        self.harness.page_data.input_ax_up.setValue(0.4)
        self.harness.page_data.input_ax_up.editingFinished.emit()
        self.harness.analyzer.flush_axis_refresh()

        self.assertIn("axis_interval", spec.params)
        self.assertAlmostEqual(spec.params["axis_interval"]["up"], 0.4, places=9)
        # 3D 页面不因区间变化请求重算。
        self.assertEqual(self.harness.refreshes, [])

    def test_position_bar_label_uses_the_axis_and_its_unit(self):
        spec = _spec("integral", low=0, up=10, mid=5)
        self.harness.bind(spec)
        self.assertEqual(self.harness.bar.axis_title_label.text(), "kx")

        source = _spec("integral2", low=0, up=10, mid=5)
        self.harness.core.coord_units["X"] = "Å⁻¹"
        self.harness.bind(source)
        self.assertEqual(self.harness.bar.axis_title_label.text(), "kx / Å⁻¹")

    def test_index_coordinates_are_labelled_index(self):
        harness = _AxisHarness()
        harness.core.coord_sources["X"] = "index"
        spec = _spec("integral", low=0, up=10, mid=5)
        harness.bind(spec)
        self.assertEqual(harness.bar.axis_title_label.text(), "kx (index)")


class SliceSourceRangeTests(unittest.TestCase):
    """单层切片页移动位置：更新产生该切面的源范围，保留裁剪链其余部分。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(cls.app_instance() if False else [])

    def setUp(self):
        self.harness = _AxisHarness()

    @staticmethod
    def _slice_spec(position=0.0):
        return _spec(
            "slice",
            kind="home",
            home_slice_info={"axis": 0, "index": 5},
            crop_regions=[
                {
                    "view": "3d",
                    "axes": ("X", "Y", "E"),
                    "bounds": (position, position, -1.0, 1.0, -2.0, 0.0),
                    "e_flip": False,
                    "operation": "crop",
                },
                {
                    "view": "2d",
                    "axes": ("Y", "E"),
                    "bounds": (-0.5, 0.5, -1.0, 0.0),
                    "e_flip": False,
                    "operation": "crop",
                },
                {
                    "view": "2d",
                    "axes": ("Y", "E"),
                    "bounds": (-0.2, 0.2, -0.5, 0.0),
                    "e_flip": False,
                    "operation": "erase",
                },
            ],
        )

    def test_moving_the_position_rewrites_only_the_slice_region(self):
        spec = self._slice_spec()
        self.harness.bind(spec)
        self.harness.controller.interval.set_center(0.6)
        spec.params["axis_interval"] = self.harness.analyzer.axis_interval.as_dict()

        changed = self.harness.analyzer._update_slice_source_range(spec)

        self.assertTrue(changed)
        regions = spec.params["crop_regions"]
        self.assertEqual(len(regions), 3)
        coords = self.harness.core.coords["X"]
        expected = float(coords[8])
        self.assertAlmostEqual(regions[0]["bounds"][0], expected, places=9)
        self.assertAlmostEqual(regions[0]["bounds"][1], expected, places=9)
        # 面内裁剪与裁空链原样保留。
        untouched = self._slice_spec().params["crop_regions"]
        self.assertEqual(regions[1], untouched[1])
        self.assertEqual(regions[2], untouched[2])
        self.assertEqual(spec.params["home_slice_info"]["index"], 8)

    def test_unchanged_position_reports_no_change(self):
        spec = self._slice_spec()
        coords = self.harness.core.coords["X"]
        spec.params["home_slice_info"] = {"axis": 0, "index": 5}
        spec._params = spec.params
        self.harness.bind(spec)
        self.harness.controller.interval.set_center(float(coords[5]))
        spec.params["axis_interval"] = self.harness.analyzer.axis_interval.as_dict()

        self.assertFalse(self.harness.analyzer._update_slice_source_range(spec))

    def test_saved_interval_is_snapped_to_the_slice_sample(self):
        spec = self._slice_spec()
        self.harness.bind(spec)
        # 位置落在采样点之间：模型保留精确值，保存的区间对齐到最近采样点。
        self.harness.controller.interval.set_center(0.62)
        spec.params["axis_interval"] = self.harness.analyzer.axis_interval.as_dict()

        self.harness.analyzer._update_slice_source_range(spec)

        coords = self.harness.core.coords["X"]
        saved = spec.params["axis_interval"]
        self.assertEqual(saved["low"], saved["up"])
        self.assertAlmostEqual(saved["low"], float(coords[8]), places=9)
        self.assertEqual(spec.params["home_slice_info"]["index"], 8)

    def test_precise_logical_bounds_follow_the_new_slice(self):
        spec = self._slice_spec()
        spec.params["precise_logical_bounds"] = [5.0, 5.0, 0.0, 4.0, 0.0, 5.0]
        self.harness.bind(spec)
        self.harness.controller.interval.set_center(0.6)
        spec.params["axis_interval"] = self.harness.analyzer.axis_interval.as_dict()

        self.harness.analyzer._update_slice_source_range(spec)

        self.assertEqual(spec.params["precise_logical_bounds"][:2], [8.0, 8.0])


class BottomBarLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_position_group_takes_the_timeline_place_when_there_is_no_time_axis(self):
        bar = TimelineBar()
        bar.resize(1400, bar.HEIGHT)
        bar.show()
        bar.set_timeline_visible(False, animate=False)
        bar.set_position_visible(True, animate=False)

        geometry = bar.axis_group.geometry()
        left_margin = bar.ROW_MARGINS[0]
        # 位置组从底栏左侧内边距处开始，不为时间轴保留空位（只差 1px 边框）。
        self.assertLessEqual(abs(geometry.left() - left_margin), 2)
        self.assertFalse(bar.timeline_group.isVisible())
        self.assertLess(geometry.right(), bar.width() - bar.view_controls.width())
        # 滑条拿到整行富余宽度，明显宽于与其他分组同排时的份额。
        self.assertGreater(bar.slider_axis.width(), bar.AXIS_SLIDER_MIN_WIDTH * 2)

    def test_position_group_shares_the_row_with_the_timeline(self):
        bar = TimelineBar()
        bar.resize(1500, bar.HEIGHT)
        bar.show()
        bar.set_position_visible(True, animate=False)

        self.assertTrue(bar.timeline_group.isVisible())
        self.assertLess(bar.axis_group.geometry().right(), bar.timeline_group.geometry().left())

    def test_narrow_window_wraps_the_view_controls_to_a_second_row(self):
        bar = TimelineBar()
        bar.resize(1500, bar.HEIGHT)
        bar.show()
        bar.set_position_visible(True, animate=False)
        self.assertFalse(bar._wrapped)
        self.assertEqual(bar.height(), bar.HEIGHT)

        bar.resize(1180, bar.HEIGHT)
        bar._apply_wrap(bar._should_wrap())

        self.assertTrue(bar._wrapped)
        self.assertEqual(bar.height(), bar.HEIGHT + bar.SECOND_ROW_HEIGHT)
        self.assertTrue(bar.secondary_row.isVisible())

    def test_unwrapping_restores_the_single_row_height(self):
        bar = TimelineBar()
        bar.resize(1180, bar.HEIGHT)
        bar.show()
        bar.set_position_visible(True, animate=False)
        bar._apply_wrap(bar._should_wrap())
        self.assertTrue(bar._wrapped)

        bar.resize(1600, bar.HEIGHT)
        bar._apply_wrap(bar._should_wrap())

        self.assertFalse(bar._wrapped)
        self.assertEqual(bar.height(), bar.HEIGHT)

    def test_revealing_twice_does_not_restart_the_animation(self):
        bar = TimelineBar()
        bar.resize(1500, bar.HEIGHT)
        bar.show()
        bar.set_position_visible(True, animate=True)
        self.assertTrue(_wait_for_animation(bar.axis_group))
        self.assertFalse(bar.axis_group.isHidden())

        # 同类页面之间只更新内容，不重复呼出。
        bar.set_position_visible(True, animate=True)
        self.assertIsNone(getattr(bar.axis_group, "_visibility_animation", None))
        self.assertFalse(bar.axis_group.isHidden())

    def test_reverse_collapse_continues_from_the_current_state(self):
        bar = TimelineBar()
        bar.resize(1500, bar.HEIGHT)
        bar.show()
        bar.set_position_visible(True, animate=True)
        # 呼出动画已同步启动：此刻反向收起必须从当前宽度继续，而不是硬跳变。
        self.assertIsNotNone(getattr(bar.axis_group, "_visibility_animation", None))

        bar.set_position_visible(False, animate=True)
        self.assertTrue(_wait_for_animation(bar.axis_group))
        self.assertTrue(bar.axis_group.isHidden())

    def test_collapse_then_reveal_again_settles_visible(self):
        bar = TimelineBar()
        bar.resize(1500, bar.HEIGHT)
        bar.show()
        bar.set_position_visible(True, animate=True)
        self.assertTrue(_wait_for_animation(bar.axis_group))
        bar.set_position_visible(False, animate=True)
        self.assertTrue(_wait_for_animation(bar.axis_group))

        bar.set_position_visible(True, animate=True)
        self.assertTrue(_wait_for_animation(bar.axis_group))
        self.assertFalse(bar.axis_group.isHidden())


if __name__ == "__main__":
    unittest.main()
