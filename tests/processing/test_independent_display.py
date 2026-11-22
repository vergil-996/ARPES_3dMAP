import unittest
from unittest.mock import patch

import numpy as np

from bandscope.core.display_style import new_display_style, resolved_display_style
from bandscope.processing import gpu_display
from bandscope.processing.display_transfer import (
    channels,
    display_level_info,
    independent_opacity_values,
    normalization_range,
    opacity_reference_distance,
    rebase_opacity,
)
from bandscope.processing.local_enhancement import clear_range_cache, finite_range, resolve_display
from tests.support.enhancement import context_2d, region


def opacity_style(points):
    style = new_display_style()
    style["opacity"] = {"points": [list(point) for point in points]}
    return style


class IndependentDisplayTests(unittest.TestCase):
    def setUp(self):
        clear_range_cache()
        self.addCleanup(clear_range_cache)
        self.data = np.array([-2.0, 0.0, 0.1, 0.25, 0.5, 1.0, 2.0, np.nan, np.inf, -np.inf])
        self.style = opacity_style(((0.0, 0.0), (0.5, 0.5), (1.0, 1.0)))
        self.style["normalization"] = {
            "mode": "manual", "percentiles": (0.0, 99.5), "manual_range": (0.0, 1.0),
        }
        self.info = display_level_info(self.data, self.style)

    def test_alpha_is_the_piecewise_linear_curve_of_the_normalized_intensity(self):
        points = ((0.0, 0.0), (0.2, 0.8), (0.6, 0.8), (1.0, 0.1))
        self.style["opacity"] = {"points": [list(point) for point in points]}
        actual = independent_opacity_values(self.data, self.info, self.style, 2.0)
        q = (np.asarray(self.data, dtype=float) - self.info["data_min"]) / self.info["span"]
        expected = np.interp(q, [point[0] for point in points], [point[1] for point in points])
        finite = np.isfinite(self.data)
        np.testing.assert_allclose(actual[finite], expected[finite], rtol=1e-12, atol=1e-12)

    def test_alpha_endpoints_and_plateaus_are_exact(self):
        points = ((0.0, 0.0), (0.25, 1.0), (0.75, 1.0), (1.0, 0.0))
        self.style["opacity"] = {"points": [list(point) for point in points]}
        samples = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
        info = display_level_info(np.array([0.0, 1.0]), self.style)
        actual = independent_opacity_values(samples, info, self.style, 3.5)
        np.testing.assert_allclose(actual, [0.0, 1.0, 1.0, 1.0, 0.0], atol=1e-12)
        self.assertEqual(float(actual[-1]), 0.0)
        self.assertTrue(np.all(np.isfinite(actual)))

    def test_color_and_opacity_controls_are_independent(self):
        original = self.data.copy()
        other_color = new_display_style(levels=(20, 100, 80))
        other_color["normalization"] = self.style["normalization"].copy()
        other_color["opacity"] = {"points": self.style["opacity"]["points"]}
        other_info = display_level_info(self.data, other_color)
        color_a, alpha_a = channels(self.data, self.info, "linear", style=self.style, unit_distance=2.0)
        color_b, alpha_b = channels(self.data, other_info, "sigmoid", style=other_color, unit_distance=2.0)
        self.assertFalse(np.array_equal(color_a, color_b))
        np.testing.assert_array_equal(alpha_a, alpha_b)

        stronger = new_display_style()
        stronger["normalization"] = self.style["normalization"].copy()
        stronger["opacity"] = {"points": [[0.0, 0.0], [0.25, 0.9], [1.0, 1.0]]}
        color_c, alpha_c = channels(self.data, self.info, "linear", style=stronger, unit_distance=2.0)
        np.testing.assert_array_equal(color_a, color_c)
        self.assertFalse(np.array_equal(alpha_a, alpha_c))
        np.testing.assert_array_equal(self.data, original)

    def test_partial_channels_match_full_and_reuse_the_other_array(self):
        full_color, full_alpha = channels(
            self.data, self.info, "linear", style=self.style, unit_distance=2.0,
        )
        other = new_display_style(levels=(20, 100, 80))
        other["normalization"] = self.style["normalization"].copy()
        other["opacity"] = {"points": self.style["opacity"]["points"]}
        other_info = display_level_info(self.data, other)
        with patch("bandscope.processing.display_transfer.independent_opacity_values") as opacity:
            color, alpha = channels(
                self.data, other_info, "linear", style=other, unit_distance=2.0,
                alpha=full_alpha,
            )
        opacity.assert_not_called()
        self.assertIs(alpha, full_alpha)
        expected_color, expected_alpha = channels(
            self.data, other_info, "linear", style=other, unit_distance=2.0,
        )
        np.testing.assert_array_equal(color, expected_color)
        np.testing.assert_array_equal(alpha, expected_alpha)

        other["opacity"] = {"points": [[0.0, 0.0], [0.3, 0.7], [1.0, 1.0]]}
        with patch("bandscope.processing.display_transfer.normalized_color_samples") as normalized:
            color, alpha = channels(
                self.data, other_info, "linear", style=other, unit_distance=2.0,
                color=expected_color,
            )
        normalized.assert_not_called()
        self.assertIs(color, expected_color)
        _expected_color, expected_alpha = channels(
            self.data, other_info, "linear", style=other, unit_distance=2.0,
        )
        np.testing.assert_array_equal(alpha, expected_alpha)

    def test_zero_alpha_curve_returns_transparent_display(self):
        self.style["opacity"] = {"points": [[0.0, 0.0], [1.0, 0.0]]}
        alpha = independent_opacity_values(self.data, self.info, self.style, 100.0)
        self.assertTrue(np.all(alpha[np.isfinite(self.data)] == 0.0))
        self.style["opacity"] = {"points": [[0.0, 0.0], [0.75, 0.0], [1.0, 1.0]]}
        alpha = independent_opacity_values(self.data, self.info, self.style, 1.0)
        self.assertEqual(float(alpha[3]), 0.0)
        self.assertGreater(float(alpha[6]), 0.0)

    def test_reference_distance_is_derived_from_the_full_display_grid(self):
        from bandscope.processing.display_transfer import volume_unit_distance

        full = (100, 80, 40)
        self.assertEqual(opacity_reference_distance(full), volume_unit_distance(full, full))
        self.assertEqual(opacity_reference_distance(full),
                         opacity_reference_distance((100, 80, 40, 25)))
        self.assertGreater(opacity_reference_distance(full), 0.0)
        alpha = np.array([0.0, 0.25, 0.5, 1.0])
        rebased = rebase_opacity(alpha, 1.0, 2.0)
        np.testing.assert_allclose(rebased[:-1], 1.0 - (1.0 - alpha[:-1]) ** 2.0, rtol=1e-12)
        self.assertEqual(float(rebased[-1]), 1.0)

    def test_full_range_ignores_removed_policies_and_handles_empty_and_constant_data(self):
        values = np.array([0.0, 1.0, 2.0, 100.0, np.nan, np.inf])
        style = new_display_style()
        np.testing.assert_allclose(normalization_range(values, style), (0.0, 100.0))
        with patch("numpy.percentile", wraps=np.percentile) as percentile:
            finite_range(values, style)
            finite_range(values, style)
            percentile.assert_not_called()
        style["normalization"] = {
            "mode": "manual", "percentiles": (2.0, 98.0), "manual_range": (-4.0, 8.0),
        }
        self.assertEqual(normalization_range(values, style), (0.0, 100.0))
        self.assertEqual(normalization_range([], new_display_style()), (0.0, 1.0))
        info = display_level_info(np.full(3, 4.0), new_display_style())
        self.assertEqual((info["data_min"], info["data_max"]), (4.0, 5.0))

    def test_region_uses_full_range_and_lock_survives_frame_changes(self):
        style = new_display_style()
        style["normalization"] = {"mode": "percentile", "percentiles": (0.0, 99.5)}
        context = context_2d(np.array([[0.0, 1.0], [2.0, 1000.0]]))
        state = {
            "background": new_display_style(), "common": False, "revision": 0,
            "regions": [region(1, (0, 1, 0, 1), style=style)],
        }
        fields = resolve_display(context, state)
        self.assertEqual(fields["regions"][0]["data_range"], (0.0, 1000.0))

        state["regions"][0]["locked_range"] = (0.0, 1000.0)
        context["data"] = context["data"] * 3.0
        locked = resolve_display(context, state)
        self.assertEqual(locked["regions"][0]["data_range"], (0.0, 1000.0))
        state["regions"][0].pop("locked_range")
        unlocked = resolve_display(context, state)
        self.assertEqual(unlocked["regions"][0]["data_range"], (0.0, 3000.0))

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
        style["opacity"] = {"points": [[0.0, 0.0], [0.15, 0.4], [0.6, 0.9], [1.0, 0.2]]}
        info = display_level_info(values, style)
        gpu_display.set_enabled(False)
        cpu_color, cpu_alpha = channels(values, info, "linear", style=style, unit_distance=2.5)
        gpu_display.set_enabled(True)
        gpu_color, gpu_alpha = channels(values, info, "linear", style=style, unit_distance=2.5)
        self.assertTrue(gpu_display.enabled())
        np.testing.assert_allclose(gpu_color, cpu_color, rtol=0.0, atol=1e-7)
        np.testing.assert_allclose(gpu_alpha, cpu_alpha, rtol=0.0, atol=1e-7)
        self.assertIs(gpu_color.base, gpu_alpha.base)
        self.assertEqual(gpu_color.base.size, values.size * 2)
        self.assertEqual(
            gpu_alpha.__array_interface__["data"][0],
            gpu_color.__array_interface__["data"][0] + gpu_color.itemsize,
        )
        partial_color, partial_alpha = channels(
            values, info, "linear", style=style, unit_distance=2.5, color=gpu_color,
        )
        self.assertIs(partial_color, gpu_color)
        np.testing.assert_allclose(partial_alpha, cpu_alpha, rtol=0.0, atol=1e-7)

    def test_smooth_split_and_vertical_steps_match_shared_curve_on_voxels(self):
        from bandscope.core.opacity_curve import insert_opacity_point

        points = [[0.0, 0.0], [0.25, 0.8], [0.4, 0.2], [0.4, 0.7], [1.0, 0.9]]
        split, segments, _index = insert_opacity_point(
            points, 0.3, interpolation="smooth",
        )
        self.style["opacity"] = {
            "points": [list(point) for point in split],
            "interpolation": "smooth", "segments": segments,
        }
        info = display_level_info(np.array([0.0, 1.0]), self.style)
        samples = np.array([
            0.0, 0.3, np.nextafter(0.4, 0.0), 0.4, np.nextafter(0.4, 1.0), 1.0,
        ])
        actual = independent_opacity_values(samples, info, self.style, 2.0)
        expected_points = [0.0, split[2][1], 0.2, 0.7, 0.7, 0.9]
        np.testing.assert_allclose(actual, expected_points, atol=1.0e-14)

    def test_cuda_smooth_split_and_step_mapping_matches_cpu(self):
        if not gpu_display.set_enabled(True):
            self.skipTest(f"CUDA display mapping unavailable: {gpu_display.failure_reason()}")
        self.addCleanup(gpu_display.set_enabled, False)
        self.addCleanup(gpu_display.reset)
        from bandscope.core.opacity_curve import insert_opacity_point

        values = np.random.default_rng(31).random((80, 80, 80), dtype=np.float32)
        points, segments, _index = insert_opacity_point(
            [[0.0, 0.0], [0.2, 0.8], [0.5, 0.2], [0.5, 0.7], [1.0, 0.4]],
            0.35, interpolation="smooth",
        )
        style = new_display_style(opacity_points=points, opacity_segments=segments)
        info = display_level_info(values, style)
        gpu_display.set_enabled(False)
        cpu_color, cpu_alpha = channels(values, info, "linear", style=style, unit_distance=2.5)
        gpu_display.set_enabled(True)
        gpu_color, gpu_alpha = channels(values, info, "linear", style=style, unit_distance=2.5)
        self.assertTrue(gpu_display.enabled())
        np.testing.assert_allclose(gpu_color, cpu_color, rtol=0.0, atol=1.0e-7)
        np.testing.assert_allclose(gpu_alpha, cpu_alpha, rtol=0.0, atol=1.0e-7)

    def test_resolved_style_keeps_points_canonical(self):
        self.style["opacity"] = {"points": [[0.9, 0.2], [0.1, 0.6], [0.1, 0.3]]}
        resolved = resolved_display_style(self.style, reference_distance=2.0)
        self.assertEqual(resolved["opacity"]["points"], [
            [0.0, 0.6], [0.1, 0.6], [0.1, 0.3], [0.9, 0.2], [1.0, 0.2],
        ])
        self.assertEqual(resolved["opacity"]["interpolation"], "linear")


if __name__ == "__main__":
    unittest.main()
