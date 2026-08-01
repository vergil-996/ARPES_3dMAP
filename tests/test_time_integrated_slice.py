import unittest
from unittest.mock import patch

import numpy as np

from analyzer_core import AnalyzerCore


class TimeIntegratedSliceNumericsTests(unittest.TestCase):
    def setUp(self):
        self.raw = np.arange(4 * 5 * 6 * 4, dtype=np.float32).reshape(4, 5, 6, 4)
        self.time_integrated = np.sum(self.raw[..., 1:4], axis=3)

    def test_single_layer_matches_time_integrated_slice_for_every_axis(self):
        for axis_index, slice_index in enumerate((2, 3, 4)):
            with self.subTest(axis=axis_index):
                prefix = AnalyzerCore.build_axis_prefix_sum(self.time_integrated, axis_index)
                actual = AnalyzerCore.range_sum_from_axis_prefix(
                    prefix,
                    axis_index,
                    slice_index,
                    slice_index,
                )
                expected = np.take(self.time_integrated, slice_index, axis=axis_index)
                np.testing.assert_allclose(actual, expected)

    def test_inclusive_range_matches_direct_sum_for_every_axis(self):
        for axis_index in range(3):
            with self.subTest(axis=axis_index):
                prefix = AnalyzerCore.build_axis_prefix_sum(self.time_integrated, axis_index)
                actual = AnalyzerCore.range_sum_from_axis_prefix(prefix, axis_index, 1, 3)
                expected = np.sum(
                    np.take(self.time_integrated, np.arange(1, 4), axis=axis_index),
                    axis=axis_index,
                )
                np.testing.assert_allclose(actual, expected)

    def test_range_is_reordered_and_clamped(self):
        prefix = AnalyzerCore.build_axis_prefix_sum(self.time_integrated, 0)
        actual = AnalyzerCore.range_sum_from_axis_prefix(prefix, 0, 99, -10)
        expected = np.sum(self.time_integrated, axis=0)
        np.testing.assert_allclose(actual, expected)

    def test_prefix_query_avoids_the_slow_scalar_take_path(self):
        for axis_index in range(3):
            with self.subTest(axis=axis_index):
                prefix = AnalyzerCore.build_axis_prefix_sum(
                    self.time_integrated,
                    axis_index,
                )
                with patch(
                    "analyzer_core.np.take",
                    side_effect=AssertionError("np.take used"),
                ):
                    actual = AnalyzerCore.range_sum_from_axis_prefix(
                        prefix,
                        axis_index,
                        1,
                        3,
                    )

                selectors = [slice(None)] * self.time_integrated.ndim
                selectors[axis_index] = slice(1, 4)
                expected = np.sum(
                    self.time_integrated[tuple(selectors)],
                    axis=axis_index,
                )
                np.testing.assert_allclose(actual, expected)


if __name__ == "__main__":
    unittest.main()
