# -*- coding: utf-8 -*-
"""积分区间模型的规则测试（纯数值，无 Qt）。"""

import unittest

import numpy as np

from bandscope.core.axis_interval import (
    AxisInterval,
    AxisSpace,
    nearest_index,
    physical_decimals,
)


class NearestIndexTests(unittest.TestCase):
    def test_picks_the_closest_sample_for_increasing_coords(self):
        coords = np.array([-1.0, -0.5, 0.0, 0.5, 1.0])
        self.assertEqual(nearest_index(coords, -0.51), 1)
        self.assertEqual(nearest_index(coords, 0.49), 3)
        self.assertEqual(nearest_index(coords, 9.0), 4)

    def test_ties_prefer_the_smaller_original_index(self):
        coords = np.array([0.0, 1.0, 2.0])
        self.assertEqual(nearest_index(coords, 0.5), 0)
        self.assertEqual(nearest_index(coords, 1.5), 1)

    def test_reverse_coords_keep_original_indices(self):
        coords = np.array([2.0, 1.0, 0.0])
        self.assertEqual(nearest_index(coords, 0.1), 2)
        self.assertEqual(nearest_index(coords, 1.9), 0)
        # 等距时仍取较小的原始下标，而不是坐标值较小的那个。
        self.assertEqual(nearest_index(coords, 1.5), 0)

    def test_non_uniform_coords(self):
        coords = np.array([-3.0, -0.2, 0.0, 4.0])
        self.assertEqual(nearest_index(coords, -0.15), 1)
        self.assertEqual(nearest_index(coords, 2.0), 2)


class IntervalRuleTests(unittest.TestCase):
    """交互规则表逐条覆盖：未锁定端点独立，锁定整体平移。"""

    def setUp(self):
        self.interval = AxisInterval(0.0, 10.0, low=2.0, up=8.0)

    def test_defaults_to_full_range_unlocked(self):
        interval = AxisInterval(0.0, 10.0)
        self.assertEqual((interval.low, interval.up), (0.0, 10.0))
        self.assertFalse(interval.locked)
        self.assertTrue(interval.is_full_range)

    def test_unlocked_up_keeps_low_and_updates_center_and_length(self):
        self.assertTrue(self.interval.set_up(6.0))
        self.assertEqual(self.interval.low, 2.0)
        self.assertEqual(self.interval.up, 6.0)
        self.assertEqual(self.interval.center, 4.0)
        self.assertEqual(self.interval.length, 4.0)

    def test_unlocked_low_keeps_up(self):
        self.assertTrue(self.interval.set_low(5.0))
        self.assertEqual(self.interval.low, 5.0)
        self.assertEqual(self.interval.up, 8.0)

    def test_unlocked_endpoints_cannot_cross_and_stop_at_the_other_end(self):
        self.assertTrue(self.interval.set_up(1.0))
        self.assertEqual((self.interval.low, self.interval.up), (2.0, 2.0))

        self.interval = AxisInterval(0.0, 10.0, low=2.0, up=8.0)
        self.assertTrue(self.interval.set_low(9.0))
        self.assertEqual((self.interval.low, self.interval.up), (8.0, 8.0))

    def test_zero_length_is_allowed(self):
        self.interval.set_up(2.0)
        self.assertEqual(self.interval.length, 0.0)

    def test_locked_up_translates_the_whole_interval(self):
        self.interval.set_locked(True)
        self.assertTrue(self.interval.set_up(9.0))
        self.assertEqual((self.interval.low, self.interval.up), (3.0, 9.0))
        self.assertEqual(self.interval.length, 6.0)

    def test_locked_low_translates_the_whole_interval(self):
        self.interval.set_locked(True)
        self.interval.set_low(1.0)
        self.assertEqual((self.interval.low, self.interval.up), (1.0, 7.0))

    def test_translation_stops_at_the_edge_and_keeps_length(self):
        self.interval.set_locked(True)
        # 上限推到 20 会越过轴上限：整体平移被限制，两端一起停在边界，长度不变。
        self.assertTrue(self.interval.set_up(20.0))
        self.assertEqual((self.interval.low, self.interval.up), (4.0, 10.0))
        self.assertEqual(self.interval.length, 6.0)

        # 已经贴住上边界，再往上平移没有位移。
        self.assertFalse(self.interval.set_center(100.0))
        self.assertEqual((self.interval.low, self.interval.up), (4.0, 10.0))

        self.assertTrue(self.interval.set_center(0.0))
        self.assertEqual((self.interval.low, self.interval.up), (0.0, 6.0))
        self.assertEqual(self.interval.length, 6.0)

    def test_center_move_keeps_length(self):
        self.assertTrue(self.interval.set_center(3.0))
        self.assertEqual((self.interval.low, self.interval.up), (0.0, 6.0))
        self.assertEqual(self.interval.center, 3.0)

    def test_length_grows_around_the_center(self):
        self.interval.set_center(5.0)
        self.assertTrue(self.interval.set_length(4.0))
        self.assertEqual((self.interval.low, self.interval.up), (3.0, 7.0))

    def test_length_shrinks_around_the_center(self):
        self.interval.set_length(2.0)
        self.assertEqual((self.interval.low, self.interval.up), (4.0, 6.0))

    def test_length_may_be_zero(self):
        self.assertTrue(self.interval.set_length(0.0))
        self.assertEqual(self.interval.length, 0.0)
        self.assertEqual(self.interval.center, 5.0)

    def test_length_clamps_to_the_full_axis_span(self):
        self.interval.set_length(999.0)
        self.assertEqual((self.interval.low, self.interval.up), (0.0, 10.0))

    def test_length_hitting_the_edge_translates_to_fit(self):
        interval = AxisInterval(0.0, 10.0, low=8.0, up=9.0)
        interval.set_length(4.0)
        self.assertEqual((interval.low, interval.up), (6.0, 10.0))

    def test_length_at_the_lower_edge_translates_the_other_way(self):
        interval = AxisInterval(0.0, 10.0, low=0.0, up=1.0)
        interval.set_length(4.0)
        self.assertEqual((interval.low, interval.up), (0.0, 4.0))

    def test_locked_interval_keeps_length_when_endpoint_is_pushed_out(self):
        self.interval.set_center(5.0)
        self.interval.set_length(4.0)
        self.interval.set_locked(True)
        self.interval.set_up(9.0)
        self.assertEqual((self.interval.low, self.interval.up), (5.0, 9.0))

    def test_set_bounds_resets_when_asked(self):
        self.interval.set_locked(True)
        changed = self.interval.set_bounds(-1.0, 1.0, reset=True)
        self.assertTrue(changed)
        self.assertEqual((self.interval.low, self.interval.up), (-1.0, 1.0))
        self.assertTrue(self.interval.locked)

    def test_set_axis_resets_range_and_unlocks(self):
        self.interval.set_locked(True)
        self.interval.set_axis("Y", -3.0, 4.0)
        self.assertEqual((self.interval.low, self.interval.up), (-3.0, 4.0))
        self.assertFalse(self.interval.locked)
        self.assertEqual(self.interval.axis_key, "Y")

    def test_invalid_span_is_rejected(self):
        with self.assertRaises(ValueError):
            AxisInterval(1.0, 0.0)
        with self.assertRaises(ValueError):
            AxisInterval(float("nan"), 1.0)

    def test_non_finite_input_is_ignored(self):
        self.assertFalse(self.interval.set_up(float("nan")))
        self.assertEqual(self.interval.up, 8.0)
        self.assertFalse(self.interval.set_low("not a number"))

    def test_zero_width_axis_is_usable(self):
        interval = AxisInterval(2.0, 2.0)
        self.assertEqual(interval.length, 0.0)
        self.assertFalse(interval.set_up(5.0))
        self.assertEqual(interval.center, 2.0)


class IntervalSamplingTests(unittest.TestCase):
    def test_exact_samples_map_to_their_own_indices(self):
        coords = np.linspace(0.0, 1.0, 11)
        interval = AxisInterval(0.0, 1.0, low=0.2, up=0.6)
        self.assertEqual(interval.to_indices(coords), (2, 6))

    def test_endpoints_between_samples_snap_to_the_nearest(self):
        coords = np.linspace(0.0, 1.0, 11)
        interval = AxisInterval(0.0, 1.0, low=0.24, up=0.56)
        self.assertEqual(interval.to_indices(coords), (2, 6))

    def test_non_uniform_coords(self):
        coords = np.array([0.0, 0.1, 1.0, 3.0, 3.5])
        interval = AxisInterval(0.0, 3.5, low=0.05, up=3.2)
        self.assertEqual(interval.to_indices(coords), (0, 3))

    def test_reverse_coords_return_sorted_indices(self):
        coords = np.array([2.0, 1.0, 0.0, -1.0])
        interval = AxisInterval(-1.0, 2.0, low=1.9, up=0.1)
        # 1.9 → 原始下标 0，0.1 → 原始下标 2；返回时按原始下标排序。
        self.assertEqual(interval.to_indices(coords), (0, 2))

    def test_zero_length_interval_selects_one_sample(self):
        coords = np.linspace(-1.0, 1.0, 5)
        interval = AxisInterval(-1.0, 1.0, low=0.1, up=0.1)
        self.assertEqual(interval.to_indices(coords), (2, 2))
        self.assertEqual(interval.single_index(coords), 2)

    def test_ties_take_the_smaller_original_index(self):
        coords = np.array([0.0, 1.0, 2.0])
        interval = AxisInterval(0.0, 2.0, low=0.5, up=1.5)
        self.assertEqual(interval.to_indices(coords), (0, 1))

    def test_full_range_covers_every_sample(self):
        coords = np.linspace(0.0, 5.0, 6)
        interval = AxisInterval(0.0, 5.0)
        self.assertEqual(interval.to_indices(coords), (0, 5))

    def test_empty_coords_are_tolerated(self):
        interval = AxisInterval(0.0, 1.0)
        self.assertEqual(interval.to_indices(np.array([])), (0, 0))


class IntervalSerializationTests(unittest.TestCase):
    def test_round_trip_preserves_exact_endpoints_and_lock(self):
        interval = AxisInterval(0.0, 2.0, low=0.123456, up=1.987654, locked=True, axis_key="X")
        state = interval.as_dict()
        restored = AxisInterval.from_dict(state)
        self.assertEqual((restored.low, restored.up), (interval.low, interval.up))
        self.assertTrue(restored.locked)
        self.assertEqual(restored.axis_key, "X")

    def test_from_dict_rejects_broken_state(self):
        self.assertIsNone(AxisInterval.from_dict(None))
        self.assertIsNone(AxisInterval.from_dict({"minimum": 0.0}))
        self.assertIsNone(AxisInterval.from_dict({"minimum": 1.0, "maximum": 0.0}))

    def test_from_dict_defaults_missing_endpoints_to_full_range(self):
        restored = AxisInterval.from_dict({"minimum": 0.0, "maximum": 4.0})
        self.assertEqual((restored.low, restored.up), (0.0, 4.0))

    def test_from_indices_migrates_legacy_index_range(self):
        coords = np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
        interval = AxisInterval.from_indices(coords, 3, 1, axis_key="Y")
        self.assertEqual((interval.low, interval.up), (-1.0, 1.0))
        self.assertFalse(interval.locked)
        self.assertEqual(interval.axis_key, "Y")

    def test_from_indices_is_unlocked_even_though_legacy_had_a_half_width(self):
        coords = np.linspace(0.0, 1.0, 5)
        interval = AxisInterval.from_indices(coords, 1, 3)
        self.assertFalse(interval.locked)

    def test_from_indices_handles_empty_coords(self):
        self.assertIsNone(AxisInterval.from_indices(np.array([]), 0, 0))


class AxisSpaceTests(unittest.TestCase):
    def test_index_coordinates_are_labelled_as_index(self):
        space = AxisSpace("X", np.arange(4), source="index")
        self.assertTrue(space.describes_index)
        self.assertEqual(space.display_label, "kx (index)")

    def test_declared_unit_is_shown(self):
        space = AxisSpace("X", np.linspace(-1, 1, 3), unit="Å⁻¹", source="file")
        self.assertEqual(space.display_label, "kx / Å⁻¹")

    def test_missing_unit_is_not_invented(self):
        space = AxisSpace("E", np.linspace(-2, 0, 3), source="file")
        self.assertEqual(space.display_label, "E")

    def test_range_and_spacing_come_from_the_coords(self):
        space = AxisSpace("X", np.array([-1.0, 0.0, 2.0, 4.0]))
        self.assertEqual((space.minimum, space.maximum), (-1.0, 4.0))
        self.assertAlmostEqual(space.step, 2.0)

    def test_single_sample_axis_gets_a_usable_span(self):
        space = AxisSpace("E", np.array([5.0]))
        self.assertEqual(space.minimum, 5.0)
        self.assertGreater(space.maximum, space.minimum)

    def test_empty_coords_fall_back_to_a_point(self):
        space = AxisSpace("Y", np.array([]))
        self.assertEqual(space.sample_count, 1)
        self.assertEqual(space.minimum, 0.0)

    def test_reverse_coords_keep_a_positive_range(self):
        space = AxisSpace("E", np.array([0.0, -1.0, -2.0]))
        self.assertEqual((space.minimum, space.maximum), (-2.0, 0.0))

    def test_as_interval_covers_the_whole_axis(self):
        space = AxisSpace("X", np.linspace(-1, 1, 5))
        interval = space.as_interval()
        self.assertEqual((interval.low, interval.up), (-1.0, 1.0))
        self.assertEqual(interval.axis_key, "X")


class PhysicalDecimalsTests(unittest.TestCase):
    def test_fine_steps_keep_enough_digits(self):
        self.assertGreaterEqual(physical_decimals(0.001), 4)

    def test_coarse_steps_do_not_lose_the_integer_part(self):
        self.assertGreaterEqual(physical_decimals(1.0), 3)

    def test_invalid_step_falls_back(self):
        self.assertEqual(physical_decimals(0.0), 4)
        self.assertEqual(physical_decimals(None), 4)


if __name__ == "__main__":
    unittest.main()
