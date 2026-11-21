import unittest

import numpy as np

from bandscope.core.display_style import (
    DEFAULT_OPACITY_POINTS,
    INDEPENDENT_MODE,
    LEGACY_MODE,
    create_display_state,
    default_opacity_points,
    editable_display_state,
    is_independent,
    legacy_display_style,
    migrate_display_state,
    new_display_style,
    new_render_quality,
    normalize_opacity_points,
    resolved_display_style,
    with_display_mode,
)
from bandscope.processing.display_transfer import (
    compatible_opacity_points,
    legacy_opacity_points,
    opacity_reference_distance,
)

REFERENCE = opacity_reference_distance((150, 150, 200))


class DisplayStyleTests(unittest.TestCase):
    def test_new_defaults_use_three_control_points(self):
        style = new_display_style()
        self.assertEqual(style["normalization"], {"mode": "full"})
        self.assertEqual(style["opacity"], {"points": default_opacity_points()})
        self.assertEqual(style["opacity"]["points"], [list(p) for p in DEFAULT_OPACITY_POINTS])
        self.assertTrue(is_independent(style))

    def test_explicit_legacy_style_and_state_migration_stay_legacy(self):
        old = legacy_display_style((5, 60, 90), "sigmoid")
        migrated = migrate_display_state({
            "background": old,
            "regions": [{"style": dict(levels=(1, 50, 99), opacity_mode="linear")}],
        })
        self.assertEqual(migrated["background"]["display_mode"], LEGACY_MODE)
        self.assertEqual(migrated["regions"][0]["style"]["display_mode"], LEGACY_MODE)
        self.assertNotIn("normalization", migrated["background"])
        self.assertEqual(create_display_state(legacy=True)["background"], legacy_display_style())

    def test_switching_mode_preserves_color_controls_and_colormap_range(self):
        old = legacy_display_style((7, 63, 91), "对数", (0.2, 0.8))
        independent = with_display_mode(old, INDEPENDENT_MODE, reference_distance=REFERENCE)
        self.assertEqual(independent["levels"], old["levels"])
        self.assertEqual(independent["opacity_mode"], "对数")
        self.assertEqual(independent["colormap_range"], (0.2, 0.8))
        self.assertIn("points", independent["opacity"])
        restored = with_display_mode(independent, LEGACY_MODE)
        self.assertEqual(restored, old)

    def test_default_legacy_linear_converts_to_identity_curve(self):
        migrated = with_display_mode(
            legacy_display_style(), INDEPENDENT_MODE, reference_distance=REFERENCE,
        )
        np.testing.assert_allclose(
            np.asarray(migrated["opacity"]["points"], dtype=float),
            np.asarray([[0.0, 0.0], [1.0, 1.0]]), atol=1e-12,
        )

    def test_compatible_curve_accounts_for_levels_mapping_and_reference_distance(self):
        points = np.asarray(compatible_opacity_points(
            (20, 50, 80), "sigmoid", REFERENCE,
        ), dtype=float)
        self.assertEqual(points[0][0], 0.0)
        self.assertEqual(points[-1][0], 1.0)
        levels = (20.0, 50.0, 80.0)
        from bandscope.processing.display_transfer import OPACITY_MAPS

        black, _gamma, white = levels
        low, high = black / 100.0, white / 100.0
        ramp = OPACITY_MAPS["sigmoid"]
        for intensity in np.linspace(0.0, 1.0, 257):
            position = np.clip((intensity - low) / (high - low), 0.0, 1.0)
            expected = np.interp(position, np.linspace(0.0, 1.0, len(ramp)), ramp)
            actual = np.interp(intensity, points[:, 0], points[:, 1])
            self.assertLessEqual(abs(actual - expected), 1.0 / 255.0 + 1e-9,
                                 msg=f"intensity={intensity}")

    def test_legacy_four_parameter_curve_error_is_below_one_quantization_step(self):
        params = {"threshold": 21.739, "transition": 15.0, "gamma": 1.3, "density": 60.0}
        points = np.asarray(
            legacy_opacity_points(params, REFERENCE), dtype=float,
        )
        for intensity in np.linspace(0.0, 1.0, 513):
            threshold = params["threshold"] / 100.0
            transition = params["transition"] / 100.0
            signal = max(intensity - threshold, 0.0)
            position = np.clip((intensity - threshold) / transition, 0.0, 1.0) if transition else 1.0
            gate = position * position * (3.0 - 2.0 * position) if transition else 1.0
            sigma = (params["density"] / 200.0) * signal ** params["gamma"] * gate
            expected = 1.0 - np.exp(-sigma * REFERENCE)
            actual = np.interp(intensity, points[:, 0], points[:, 1])
            self.assertLessEqual(abs(actual - expected), 1.0 / 255.0 + 1e-9,
                                 msg=f"intensity={intensity}")

    def test_four_parameter_opacity_is_converted_when_resolved(self):
        style = new_display_style((10, 70, 90), "power")
        style["normalization"] = {
            "mode": "manual", "percentiles": (2.0, 98.0), "manual_range": (-3.0, 7.0),
        }
        style["opacity"] = {
            "threshold": 21.739, "transition": 0.0, "gamma": 1.3,
            "density": 40.0, "preset": "custom",
        }
        resolved = resolved_display_style(style, reference_distance=REFERENCE)
        self.assertEqual(resolved["levels"], (10.0, 70.0, 90.0))
        self.assertEqual(resolved["normalization"], {"mode": "full"})
        self.assertEqual(resolved["opacity"]["points"][0][0], 0.0)
        self.assertEqual(resolved["opacity"]["points"][-1][0], 1.0)
        self.assertGreater(len(resolved["opacity"]["points"]), 2)
        same = resolved_display_style(style, reference_distance=REFERENCE)
        self.assertEqual(resolved, same)

    def test_resolved_points_are_sorted_clipped_and_endpoint_fixed(self):
        style = new_display_style()
        style["opacity"] = {"points": [[0.8, 0.9], [0.2, 1.7], [0.2, -0.4], [0.5, "x"]]}
        points = resolved_display_style(style)["opacity"]["points"]
        self.assertEqual(points[0], [0.0, 0.0])
        self.assertEqual(points[-1], [1.0, 0.9])
        self.assertEqual(points[1], [0.2, 0.0])
        intensities = [point[0] for point in points]
        self.assertEqual(intensities, sorted(intensities))
        self.assertTrue(all(0.0 <= point[1] <= 1.0 for point in points))
        self.assertEqual([list(point) for point in normalize_opacity_points([])],
                         [list(point) for point in DEFAULT_OPACITY_POINTS])

    def test_colormap_range_survives_mode_switch_and_old_styles_use_full_palette(self):
        old = {"levels": (7, 63, 91), "opacity_mode": "对数"}
        self.assertEqual(resolved_display_style(old)["colormap_range"], (0.0, 1.0))
        style = new_display_style((7, 63, 91), "对数", (0.2, 0.8))
        legacy = with_display_mode(style, LEGACY_MODE)
        self.assertEqual(legacy["colormap_range"], (0.2, 0.8))
        restored = with_display_mode(legacy, INDEPENDENT_MODE, reference_distance=REFERENCE)
        self.assertEqual(restored["colormap_range"], (0.2, 0.8))

    def test_editable_state_converts_every_style(self):
        state = {
            "background": legacy_display_style((0, 50, 100), "线性"),
            "regions": [{"style": legacy_display_style((10, 60, 90), "幂函数")}],
            "common_style": new_display_style((5, 50, 95)),
            "revision": 2,
        }
        state["regions"][0]["style"]["display_mode"] = LEGACY_MODE
        converted = editable_display_state(state, reference_distance=REFERENCE)
        self.assertTrue(is_independent(converted["background"]))
        self.assertTrue(is_independent(converted["regions"][0]["style"]))
        self.assertTrue(is_independent(converted["common_style"]))
        self.assertEqual(converted["revision"], 2)
        for style in (converted["background"], converted["regions"][0]["style"],
                      converted["common_style"]):
            self.assertIn("points", style["opacity"])

    def test_new_quality_is_explicit_and_linear(self):
        self.assertEqual(new_render_quality(), {
            "mode": "explicit", "interpolation": "linear", "resting": "standard",
        })


if __name__ == "__main__":
    unittest.main()
