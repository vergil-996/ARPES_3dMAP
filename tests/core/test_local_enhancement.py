import unittest

import numpy as np

from bandscope.core.local_enhancement import (
    EnhancementRegion, adapt_regions, logical_bounds, matching_regions,
    physical_bounds, region_slices, update_region_bounds,
)
from tests.support.enhancement import context_2d, context_3d, region


class EnhancementGeometryTests(unittest.TestCase):
    def test_invalid_definition_is_rejected(self):
        for bounds in ((1, 0, 1, 2), (0, float("nan"), 1, 2)):
            with self.assertRaises(ValueError):
                EnhancementRegion("1", "ROI", "2d", ("X", "Y"), bounds)

    def test_cropped_domain_uses_absolute_indices(self):
        context = context_2d(np.ones((3, 4)))
        context["plot_logical_bounds"] = dict(x_low=2, x_up=4, y_low=5, y_up=8)
        self.assertEqual(region_slices(context, region(1, (1, 3, 6, 9))), (slice(0, 2), slice(1, 4)))
        self.assertIsNone(region_slices(context, region(1, (20, 21, 1, 2))))

    def test_nonuniform_descending_axes_roundtrip(self):
        context = context_2d()
        context["coords"]["X"] = np.array([9, 7, 4, 2, 1, -1.0])
        bounds = (.5, 2.5, 1.25, 4.25)
        for flip in (False, True):
            np.testing.assert_allclose(logical_bounds(context, physical_bounds(context, bounds, e_flip=flip), e_flip=flip), bounds)

    def test_energy_flip_preserves_samples_on_cropped_2d(self):
        context = context_2d(np.ones((3, 4)), axis=0)
        context["coords"]["E"] = np.linspace(-2, 2, 12)
        context["plot_logical_bounds"] = dict(x_low=0, x_up=2, y_low=5, y_up=8)
        bounds = (0, 2, 5, 6)
        flipped = physical_bounds(context, bounds, e_flip=True)
        np.testing.assert_allclose(flipped[2:], context["coords"]["E"][[7, 8]])
        np.testing.assert_allclose(logical_bounds(context, flipped, e_flip=True), bounds)

    def test_3d_energy_flip_changes_labels_without_moving_box(self):
        context = context_3d()
        bounds = (1, 4, 0, 3, 0, 1)
        labels = physical_bounds(context, bounds, e_flip=True)
        self.assertEqual(labels[4:], (2, 3))
        np.testing.assert_allclose(logical_bounds(context, labels, e_flip=True), bounds)

    def test_volume_projects_onto_each_axis_and_keeps_depth(self):
        bounds = (1, 4, 2, 3, 0, 2)
        for axis, expected in ((0, (2, 3, 0, 2)), (1, (1, 4, 0, 2)), (2, (1, 4, 2, 3))):
            with self.subTest(axis=axis):
                context = context_2d(axis=axis)
                context["slice_info"] = dict(axis=axis, mode="integral", range=(0, 5))
                state = dict(regions=[region(1, bounds, view="3d", axes=("X", "Y", "E"))], revision=0)
                self.assertTrue(adapt_regions(context, state))
                projected = matching_regions(context, state["regions"])[0]
                self.assertEqual(projected["bounds"], expected)
                self.assertEqual(projected["volume_bounds"], bounds)
                self.assertFalse(adapt_regions(context, state))

    def test_projection_disappears_and_returns_when_interval_intersects(self):
        context = context_2d(axis=2)
        state = dict(regions=[region(1, (1, 3, 1, 3, 1.2, 1.8), view="3d", axes=("X", "Y", "E"))])
        adapt_regions(context, state)
        self.assertEqual(matching_regions(context, state["regions"]), [])
        for info in (dict(axis=2, index=1), dict(axis=2, mode="integral", range=(2, 4))):
            context["slice_info"] = info
            adapt_regions(context, state)
            self.assertEqual(len(matching_regions(context, state["regions"])), 1)
        context["slice_info"] = dict(axis=2, mode="integral", range=(3, 5))
        adapt_regions(context, state)
        self.assertEqual(matching_regions(context, state["regions"]), [])

    def test_projected_edit_survives_axis_change_and_return_to_volume(self):
        state = dict(regions=[region(1, (1, 4, 1, 3, 0, 2), view="3d", axes=("X", "Y", "E"))])
        adapt_regions(context_2d(), state)
        update_region_bounds(state["regions"][0], (2, 5, 2, 4))
        context = context_2d(axis=0)
        context["slice_info"]["index"] = 3
        adapt_regions(context, state)
        self.assertEqual(state["regions"][0]["bounds"], (2, 4, 0, 2))
        adapt_regions(context_3d(), state)
        self.assertEqual(state["regions"][0]["bounds"], (2, 5, 2, 4, 0, 2))

    def test_cropped_projection_uses_absolute_domain(self):
        context = context_2d(np.ones((3, 4)))
        context["plot_logical_bounds"] = dict(x_low=2, x_up=4, y_low=5, y_up=8)
        context["slice_info"]["index"] = 9
        state = dict(regions=[region(1, (1, 3, 6, 9, 8, 10), view="3d", axes=("X", "Y", "E"))])
        adapt_regions(context, state)
        self.assertEqual(region_slices(context, state["regions"][0]), (slice(0, 2), slice(1, 4)))
