import unittest
from unittest import mock

import numpy as np

from bandscope.core.path_slice import PathNode, PathSegmentStyle, PathSliceModel
from bandscope.processing.path_slice import MomentumCalibration, PathSliceSampler, sample_segment


def nodes(*points):
    return [PathNode(x, y, f"P{index + 1}") for index, (x, y) in enumerate(points)]


def model_for(points, thicknesses=None):
    path_nodes = nodes(*points)
    thicknesses = thicknesses or [0.0] * (len(path_nodes) - 1)
    return PathSliceModel(path_nodes, [PathSegmentStyle(value) for value in thicknesses])


class MomentumCalibrationTests(unittest.TestCase):
    def test_nonuniform_descending_coordinates_define_physical_distance(self):
        calibration = MomentumCalibration.create(
            [10, 5, 0], [-1, -2, -4], x_source="file", y_source="file",
            x_unit="Å⁻¹", y_unit="Å⁻¹",
        )
        path_nodes = nodes((0, 0), (2, 2))
        self.assertTrue(calibration.calibrated)
        self.assertAlmostEqual(
            calibration.segment_length(path_nodes[0], path_nodes[1]), np.sqrt(109)
        )
        physical = calibration.node_physical(path_nodes[1])
        logical = calibration.to_logical(*physical)
        np.testing.assert_allclose(logical, (2, 2))

    def test_incompatible_units_fall_back_to_grid_for_both_axes(self):
        calibration = MomentumCalibration.create(
            [0, 1], [0, 1], x_source="file", y_source="file",
            x_unit="Å⁻¹", y_unit="nm⁻¹",
        )
        self.assertFalse(calibration.calibrated)
        self.assertEqual(calibration.thickness_unit, "index")
        self.assertEqual(calibration.segment_length(*nodes((0, 0), (2, 1))), np.sqrt(5))


class PathSamplingTests(unittest.TestCase):
    def setUp(self):
        x, y = np.meshgrid(np.arange(5), np.arange(5), indexing="ij")
        self.volume = (x + 10 * y)[..., None] * np.ones((1, 1, 3))
        self.energy = np.array([-1.0, 0.0, 1.0])
        self.calibration = MomentumCalibration.create(np.arange(5), np.arange(5))

    def test_polyline_and_closed_path_share_internal_nodes_only(self):
        model = model_for([(0, 0), (2, 0), (2, 2), (0, 0)])
        result = PathSliceSampler().sample(self.volume, self.energy, model, self.calibration)
        self.assertEqual(result.intensity.shape, (8, 3))
        np.testing.assert_allclose(result.node_k, [0, 2, 4, 4 + np.sqrt(8)])
        np.testing.assert_allclose(result.k[:5], [0, 1, 2, 3, 4])
        np.testing.assert_allclose(result.intensity[2], result.intensity[3] - 10)
        self.assertEqual(result.segments[1].column_slice, slice(2, 5))

    def test_uniform_signal_is_unchanged_by_thickness(self):
        volume = np.full((5, 5, 3), 7.0)
        result = sample_segment(
            volume, PathNode(1, 2, "A"), PathNode(3, 2, "B"), self.calibration,
            thickness=2.0,
        )
        np.testing.assert_allclose(result.intensity, 7.0)

    def test_thickness_changes_mean_and_outside_samples_do_not_count(self):
        volume = np.arange(5, dtype=float)[None, :, None] ** 2 * np.ones((5, 1, 3))
        result = sample_segment(
            volume, PathNode(1, 1, "A"), PathNode(3, 1, "B"), self.calibration,
            thickness=2.0,
        )
        np.testing.assert_allclose(result.intensity[0], (0 + 1 + 4) / 3)
        edge = sample_segment(
            np.arange(5, dtype=float)[None, :, None] * np.ones((5, 1, 3)),
            PathNode(1, 0, "A"), PathNode(3, 0, "B"), self.calibration,
            thickness=2.0,
        )
        np.testing.assert_allclose(edge.intensity[0], 0.5)

    def test_physical_thickness_outside_axis_does_not_clamp_to_edge(self):
        calibration = MomentumCalibration.create(
            [10, 20, 30, 40, 50], [0, 1, 2, 3, 4],
            x_source="file", y_source="file", x_unit="Å⁻¹", y_unit="Å⁻¹",
        )
        volume = np.arange(5, dtype=float)[None, :, None] * np.ones((5, 1, 3))
        result = sample_segment(
            volume, PathNode(1, 0, "A"), PathNode(3, 0, "B"), calibration,
            thickness=2.0,
        )
        np.testing.assert_allclose(result.intensity[0], 0.5)

    def test_invalid_mask_leaves_blank_when_no_valid_sample_exists(self):
        mask = np.zeros((5, 5, 3), dtype=bool)
        mask[2, 2, :] = True
        result = sample_segment(
            self.volume, PathNode(0, 0, "A"), PathNode(1, 0, "B"), self.calibration,
            valid_mask=mask,
        )
        self.assertTrue(np.isnan(result.intensity).all())

    def test_segment_cache_is_bounded_without_changing_current_result(self):
        sampler = PathSliceSampler()
        sampler.CACHE_LIMIT = 2
        model = model_for([(0, 0), (1, 0), (2, 0), (3, 0)])
        result = sampler.sample(self.volume, self.energy, model, self.calibration)
        self.assertEqual(len(sampler._segments), 2)
        self.assertEqual(result.intensity.shape, (4, 3))

    def test_only_changed_thickness_is_recomputed(self):
        sampler = PathSliceSampler()
        model = model_for([(0, 0), (2, 0), (2, 2)])
        sampler.sample(self.volume, self.energy, model, self.calibration, source_key="v1")
        model.segments[1].thickness = 1.0
        with mock.patch(
            "bandscope.processing.path_slice.sample_segment",
            wraps=sample_segment,
        ) as wrapped:
            sampler.sample(self.volume, self.energy, model, self.calibration, source_key="v1")
        self.assertEqual(wrapped.call_count, 1)
        self.assertEqual(wrapped.call_args.kwargs["index"], 1)


class PathModelTests(unittest.TestCase):
    def test_shift_style_selection_supports_noncontiguous_segments(self):
        model = model_for([(0, 0), (1, 0), (2, 0), (3, 0)])
        model.select(0)
        model.select(2, additive=True)
        self.assertEqual(model.selected, [0, 2])
        model.update_selected(thickness=0.25)
        self.assertEqual(model.segments[0].thickness, 0.25)
        self.assertEqual(model.segments[1].thickness, 0.0)
        self.assertEqual(model.segments[2].thickness, 0.25)
        model.select(0, additive=True)
        self.assertEqual(model.selected, [2])

    def test_closed_path_keeps_duplicate_endpoint_name(self):
        model = model_for([(0, 0), (1, 0), (0, 0)])
        model.nodes[0].label = model.nodes[-1].label = "Γ"
        self.assertEqual(model.path_name(), "Γ–P2–Γ")


if __name__ == "__main__":
    unittest.main()
