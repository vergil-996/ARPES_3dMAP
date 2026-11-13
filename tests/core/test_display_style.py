import unittest

from bandscope.core.display_style import (
    INDEPENDENT_MODE,
    LEGACY_MODE,
    create_display_state,
    is_independent,
    legacy_display_style,
    migrate_display_state,
    new_display_style,
    new_render_quality,
    resolved_display_style,
    with_display_mode,
)


class DisplayStyleTests(unittest.TestCase):
    def test_new_defaults_and_explicit_legacy_migration_are_distinct(self):
        style = new_display_style()
        self.assertEqual(style["normalization"]["percentiles"], (0.0, 99.5))
        self.assertEqual(style["opacity"], {
            "threshold": 0.0, "transition": 0.0, "gamma": 1.4,
            "density": 12.0, "preset": "custom",
        })
        self.assertTrue(is_independent(style))
        old = legacy_display_style((5, 60, 90), "sigmoid")
        migrated = migrate_display_state({
            "background": old,
            "regions": [{"style": dict(levels=(1, 50, 99), opacity_mode="linear")}],
        })
        self.assertEqual(migrated["background"]["display_mode"], LEGACY_MODE)
        self.assertEqual(migrated["regions"][0]["style"]["display_mode"], LEGACY_MODE)
        self.assertNotIn("normalization", migrated["background"])
        self.assertEqual(create_display_state(legacy=True)["background"], legacy_display_style())

    def test_switching_mode_preserves_only_color_controls_and_legacy_curve(self):
        old = legacy_display_style((7, 63, 91), "对数")
        independent = with_display_mode(old, INDEPENDENT_MODE)
        self.assertEqual(independent["levels"], old["levels"])
        self.assertEqual(independent["opacity_mode"], "对数")
        self.assertEqual(independent["opacity"]["density"], 12.0)
        restored = with_display_mode(independent, LEGACY_MODE)
        self.assertEqual(restored, old)

    def test_custom_opacity_does_not_change_color_or_normalization(self):
        style = new_display_style((10, 70, 90), "power")
        style["normalization"] = {
            "mode": "manual", "percentiles": (2.0, 98.0), "manual_range": (-3.0, 7.0),
        }
        style["opacity"].update({
            "threshold": 21.739, "transition": 0.0, "gamma": 1.3,
            "density": 40.0, "preset": "custom",
        })
        resolved = resolved_display_style(style)
        self.assertEqual(resolved["levels"], (10.0, 70.0, 90.0))
        self.assertEqual(resolved["normalization"]["manual_range"], (-3.0, 7.0))
        self.assertEqual(resolved["opacity"], {
            "threshold": 21.739, "transition": 0.0, "gamma": 1.3,
            "density": 40.0, "preset": "custom",
        })

    def test_new_quality_is_explicit_and_linear(self):
        self.assertEqual(new_render_quality(), {
            "mode": "explicit", "interpolation": "linear", "resting": "standard",
        })


if __name__ == "__main__":
    unittest.main()
