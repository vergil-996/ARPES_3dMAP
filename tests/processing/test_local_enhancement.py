import copy
import unittest

import numpy as np

from bandscope.processing.local_enhancement import resolve_display
from bandscope.rendering.render_core import VisualEngine, opacity_ramp_points
from bandscope.rendering.local_enhancement import display_rgba
from tests.support.enhancement import context_2d, context_3d, region, state_2d


class LocalEnhancementMathTests(unittest.TestCase):
    def test_regions_and_background_have_separate_ranges(self):
        context, state = context_2d(), state_2d()
        original = context["data"].copy()
        fields = resolve_display(context, state)
        self.assertEqual([r["data_range"] for r in fields["regions"]], [(0, 11), (12, 23)])
        self.assertEqual((fields["background_level_info"]["data_min"], fields["background_level_info"]["data_max"]), (0, 35))
        self.assertAlmostEqual(float(fields["color"][0, 0]), 0)
        self.assertAlmostEqual(float(fields["color"][1, 5]), 1)
        bg_info = VisualEngine._level_info(original, state["background"]["levels"])
        expected = VisualEngine._leveled_cmap("magma", bg_info)(original / 35, bytes=True)
        actual = display_rgba(fields["color"], fields["alpha"], "magma")
        np.testing.assert_array_equal(actual[4:], expected[4:])
        np.testing.assert_array_equal(context["data"], original)

    def test_each_deletion_reintegrates_released_samples_with_background(self):
        context, state = context_2d(), state_2d()
        state["regions"].pop(0)
        fields = resolve_display(context, state)
        self.assertEqual(fields["background_level_info"]["data_min"], 0)
        self.assertEqual(fields["background_level_info"]["data_max"], 35)
        expected = VisualEngine._leveled_cmap("magma", fields["background_level_info"])(11 / 35, bytes=True)
        np.testing.assert_array_equal(display_rgba(fields["color"], fields["alpha"], "magma")[1, 5], expected)
        self.assertEqual(fields["regions"][0]["data_range"], (12, 23))
        state["regions"].clear()
        self.assertIsNone(resolve_display(context, state))

    def test_opacity_mapping_never_changes_color(self):
        context, state = context_3d(), state_2d()
        state["regions"] = [region(1, (0, 1, 0, 4, 0, 3), view="3d", axes=("X", "Y", "E")),
                            region(2, (2, 3, 0, 4, 0, 3), view="3d", axes=("X", "Y", "E"))]
        before = resolve_display(context, state)
        state["regions"][0]["style"]["opacity_mode"] = "sigmoid"
        after = resolve_display(context, state)
        np.testing.assert_array_equal(before["color"], after["color"])
        self.assertFalse(np.array_equal(before["alpha"][:2], after["alpha"][:2]))
        np.testing.assert_array_equal(before["alpha"][2:], after["alpha"][2:])

    def test_3d_levels_change_opacity_like_a_crop(self):
        context, state = context_3d(), state_2d()
        state["regions"] = [region(1, (0, 5, 0, 4, 0, 3), view="3d", axes=("X", "Y", "E"))]
        before = resolve_display(context, state)
        state["regions"][0]["style"]["levels"] = (10, 80, 90)
        after = resolve_display(context, state)
        self.assertFalse(np.array_equal(before["alpha"], after["alpha"]))
        self.assertFalse(np.array_equal(before["color"], after["color"]))
        info = VisualEngine._level_info(context["data"], (10, 80, 90))
        x, a = opacity_ramp_points("线性", info)
        np.testing.assert_allclose(after["alpha"], np.interp(context["data"], x, a), rtol=1e-6, atol=1e-7)

    def test_later_region_wins_and_statistics_are_not_cumulative(self):
        context, state = context_2d(), state_2d()
        state["regions"][1]["bounds"] = (1, 3, 0, 5)
        fields = resolve_display(context, state)
        self.assertEqual(fields["regions"][1]["data_range"], (6, 23))
        self.assertAlmostEqual(float(fields["color"][1, 0]), 0)
        state["regions"].pop()
        fields = resolve_display(context, state)
        rgba = display_rgba(fields["color"], fields["alpha"], "magma")
        info = VisualEngine._level_info(context["data"][:2], state["regions"][0]["style"]["levels"])
        np.testing.assert_array_equal(rgba[1, 0], VisualEngine._leveled_cmap("magma", info)(6 / 11, bytes=True))

    def test_disabled_regions_rejoin_background(self):
        context, state = context_2d(), state_2d()
        state["regions"][0]["enabled"] = False
        fields = resolve_display(context, state)
        self.assertEqual(fields["background_level_info"]["data_min"], 0)
        self.assertEqual(fields["regions"][0]["status"], "disabled")

    def test_auto_tracks_frame_but_locked_range_is_fixed(self):
        context, state = context_2d(), state_2d()
        state["regions"][0]["locked_range"] = (0, 11)
        context["data"] *= 2
        fields = resolve_display(context, state)
        self.assertEqual(fields["regions"][0]["data_range"], (0, 11))
        self.assertEqual(fields["regions"][1]["data_range"], (24, 46))

    def test_common_mode_has_same_range_and_style(self):
        context, state = context_2d(), state_2d()
        state.update(common=True, common_style=copy.deepcopy(state["background"]))
        fields = resolve_display(context, state)
        self.assertEqual([r["data_range"] for r in fields["regions"]], [(0, 23), (0, 23)])
        expected = VisualEngine._leveled_cmap("magma", fields["regions"][0]["level_info"])(11 / 23, bytes=True)
        np.testing.assert_array_equal(display_rgba(fields["color"], fields["alpha"], "magma")[1, 5], expected)
        state["common_locked_range"] = (0, 100)
        fields = resolve_display(context, state)
        self.assertEqual(fields["regions"][0]["data_range"], (0, 100))

    def test_invalid_and_constant_samples_do_not_manufacture_signal(self):
        context, state = context_2d(), state_2d()
        context["data"][:2] = np.nan
        context["data"][2:4] = -7
        context["data"][5, 5] = np.inf
        fields = resolve_display(context, state)
        self.assertEqual([r["status"] for r in fields["regions"]], ["empty", "constant"])
        self.assertTrue(fields["active"])
        self.assertTrue(np.all(fields["color"][2:4] == 0))
        self.assertTrue(np.all(fields["alpha"][2:4] == 1))
        self.assertTrue(np.all(np.isfinite(fields["color"])))
        self.assertEqual(fields["alpha"][5, 5], 0)
        self.assertTrue(np.all(fields["alpha"][:2] == 0))

    def test_other_axes_are_preserved_but_do_not_apply(self):
        state = state_2d()
        self.assertIsNone(resolve_display(context_2d(axis=0), state))

    def test_outside_region_is_empty_and_does_not_exclude_background(self):
        context, state = context_2d(), state_2d()
        state["regions"] = [region(1, (100, 110, 0, 5))]
        fields = resolve_display(context, state)
        self.assertEqual(fields["regions"][0]["status"], "empty")
        self.assertEqual(fields["background_level_info"]["data_min"], 0)
