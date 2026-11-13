import unittest
from unittest.mock import patch

import numpy as np

from bandscope.core.display_style import new_display_style
from bandscope.processing import gpu_display
from bandscope.processing.display_transfer import (
    channels,
    display_level_info,
    independent_opacity_values,
    normalization_range,
    volume_display_unit_distance,
)
from bandscope.processing.local_enhancement import clear_range_cache, finite_range


class IndependentDisplayTests(unittest.TestCase):
    def setUp(self):
        clear_range_cache()
        self.addCleanup(clear_range_cache)
        self.data = np.array([-2.0, 0.0, 0.1, 0.25, 0.5, 1.0, 2.0, np.nan, np.inf, -np.inf])
        self.style = new_display_style()
        self.style["normalization"] = {
            "mode": "manual", "percentiles": (0.0, 99.5), "manual_range": (0.0, 1.0),
        }
        self.info = display_level_info(self.data, self.style)

    def test_alpha_formula_threshold_smoothstep_extremes_and_nonfinite(self):
        self.style["opacity"] = {
            "threshold": 25.0, "transition": 25.0, "gamma": 2.0,
            "density": 40.0, "preset": "custom",
        }
        actual = independent_opacity_values(self.data, self.info, self.style, 2.0)
        q = np.asarray(self.data, dtype=float)
        signal = np.maximum(q - 0.25, 0.0)
        position = np.clip((q - 0.25) / 0.25, 0.0, 1.0)
        gate = position * position * (3.0 - 2.0 * position)
        expected = 1.0 - np.exp(-(0.2 * signal ** 2 * gate) * 2.0)
        finite = np.isfinite(self.data)
        np.testing.assert_allclose(actual[finite], expected[finite], rtol=1e-12, atol=1e-12)
        self.assertTrue(np.all(actual[:2] == 0.0))
        self.assertGreater(actual[6], 0.7)

    def test_color_and_opacity_controls_are_independent(self):
        original = self.data.copy()
        other_color = new_display_style(levels=(20, 100, 80))
        other_color["normalization"] = self.style["normalization"].copy()
        other_info = display_level_info(self.data, other_color)
        color_a, alpha_a = channels(self.data, self.info, "linear", style=self.style, unit_distance=2.0)
        color_b, alpha_b = channels(self.data, other_info, "sigmoid", style=other_color, unit_distance=2.0)
        self.assertFalse(np.array_equal(color_a, color_b))
        np.testing.assert_array_equal(alpha_a, alpha_b)

        stronger = new_display_style()
        stronger["normalization"] = self.style["normalization"].copy()
        stronger["opacity"].update(threshold=50.0, gamma=2.0, density=60.0, preset="custom")
        color_c, alpha_c = channels(self.data, self.info, "linear", style=stronger, unit_distance=2.0)
        np.testing.assert_array_equal(color_a, color_c)
        self.assertFalse(np.array_equal(alpha_a, alpha_c))
        np.testing.assert_array_equal(self.data, original)

    def test_zero_density_and_zero_transition(self):
        self.style["opacity"].update(density=0.0)
        alpha = independent_opacity_values(self.data, self.info, self.style, 100.0)
        self.assertTrue(np.all(alpha[np.isfinite(self.data)] == 0.0))
        self.style["opacity"].update(density=100.0, threshold=50.0)
        alpha = independent_opacity_values(self.data, self.info, self.style, 1.0)
        self.assertEqual(float(alpha[3]), 0.0)
        self.assertGreater(float(alpha[5]), 0.0)

    def test_percentile_manual_empty_and_constant_ranges(self):
        values = np.array([0.0, 1.0, 2.0, 100.0, np.nan, np.inf])
        style = new_display_style()
        expected = np.percentile(values[np.isfinite(values)], (0.0, 99.5))
        np.testing.assert_allclose(normalization_range(values, style), expected)
        with patch("numpy.percentile", wraps=np.percentile) as percentile:
            finite_range(values, style)
            finite_range(values, style)
            self.assertEqual(percentile.call_count, 1)
        style["normalization"]["mode"] = "manual"
        style["normalization"]["manual_range"] = (-4.0, 8.0)
        self.assertEqual(normalization_range(values, style), (-4.0, 8.0))
        self.assertEqual(normalization_range([], new_display_style()), (0.0, 1.0))
        info = display_level_info(np.full(3, 4.0), new_display_style())
        self.assertEqual((info["data_min"], info["data_max"]), (4.0, 5.0))

    def test_unit_distance_depends_on_display_grid_not_crop_size(self):
        full = (100, 80, 40)
        expected = volume_display_unit_distance(full)
        self.assertEqual(expected, min(200.0 / 99.0, 200.0 / 79.0, 200.0 / 39.0))
        for cropped in ((20, 20, 10), (100, 80, 40)):
            with self.subTest(cropped=cropped):
                self.assertEqual(volume_display_unit_distance(full), expected)

    def test_cuda_independent_mapping_matches_cpu(self):
        if not gpu_display.set_enabled(True):
            self.skipTest(f"CUDA display mapping unavailable: {gpu_display.failure_reason()}")
        self.addCleanup(gpu_display.set_enabled, False)
        self.addCleanup(gpu_display.reset)
        rng = np.random.default_rng(19)
        values = rng.random((80, 80, 80), dtype=np.float32)
        values[0, 0, 0] = np.nan
        values[1, 0, 0] = np.inf
        style = new_display_style()
        style["normalization"]["mode"] = "manual"
        style["normalization"]["manual_range"] = (0.0, 1.0)
        style["opacity"].update(threshold=13.0, transition=17.0, gamma=1.7, density=33.0, preset="custom")
        info = display_level_info(values, style)
        gpu_display.set_enabled(False)
        cpu_color, cpu_alpha = channels(values, info, "linear", style=style, unit_distance=2.5)
        gpu_display.set_enabled(True)
        gpu_color, gpu_alpha = channels(values, info, "linear", style=style, unit_distance=2.5)
        self.assertTrue(gpu_display.enabled())
        np.testing.assert_allclose(gpu_color, cpu_color, rtol=0.0, atol=1e-7)
        np.testing.assert_allclose(gpu_alpha, cpu_alpha, rtol=0.0, atol=1e-7)


if __name__ == "__main__":
    unittest.main()
