import unittest

import numpy as np

from bandscope.processing.local_enhancement import (
    cached_cumulative_histogram,
    clear_range_cache,
    cumulative_counts,
    cumulative_histogram,
)


class CumulativeHistogramTests(unittest.TestCase):
    def setUp(self):
        clear_range_cache()
        self.addCleanup(clear_range_cache)

    def test_threshold_counts_include_equal_and_greater_samples(self):
        values = np.array([1.0, 2.0, 2.0, 5.0])
        np.testing.assert_array_equal(
            cumulative_counts(values, [1.0, 2.0, 3.0, 5.0]), [4, 3, 1, 1],
        )
        result = cumulative_histogram(values, bin_count=5)
        np.testing.assert_array_equal(result["thresholds"], [1.0, 2.0, 3.0, 4.0, 5.0])
        np.testing.assert_array_equal(result["counts"], [4, 3, 1, 1, 1])
        self.assertEqual(result["total"], 4)
        hit, cached = cached_cumulative_histogram(values, bin_count=5)
        self.assertTrue(hit)
        self.assertIs(cached, result)

    def test_nonfinite_constant_and_empty_inputs(self):
        constant = cumulative_histogram(np.array([7.0, np.nan, 7.0, np.inf, -np.inf]))
        np.testing.assert_array_equal(constant["thresholds"], [7.0])
        np.testing.assert_array_equal(constant["counts"], [2])
        self.assertEqual(constant["total"], 2)
        self.assertEqual(constant["data_range"], (7.0, 7.0))

        empty = cumulative_histogram(np.array([np.nan, np.inf, -np.inf]))
        self.assertEqual(empty["total"], 0)
        self.assertIsNone(empty["data_range"])
        self.assertEqual(empty["thresholds"].size, 0)
        self.assertEqual(empty["counts"].size, 0)


if __name__ == "__main__":
    unittest.main()
