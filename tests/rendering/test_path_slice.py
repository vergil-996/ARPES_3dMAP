import unittest

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from bandscope.core.path_slice import PathNode, PathSegmentStyle, PathSliceModel
from bandscope.processing.path_slice import MomentumCalibration, PathSliceSampler
from bandscope.rendering.path_slice import (
    full_viewport,
    recommended_viewport,
    render_path_slice,
    segment_rgba,
    viewport_for_segment,
)


class PathSliceRenderingTests(unittest.TestCase):
    def setUp(self):
        self.model = PathSliceModel(
            [PathNode(0, 1, "Γ"), PathNode(2, 1, "K"), PathNode(2, 3, "M")],
            [PathSegmentStyle(), PathSegmentStyle(black=20, intensity_map="对数")],
        )
        volume = np.arange(4 * 5 * 3, dtype=float).reshape(4, 5, 3)
        calibration = MomentumCalibration.create(np.arange(4), np.arange(5))
        self.sample = PathSliceSampler().sample(
            volume, np.array([1.0, 0.0, -1.0]), self.model, calibration
        )
        self.figure = Figure(figsize=(7, 4), dpi=100)
        self.canvas = FigureCanvasAgg(self.figure)
        self.ax = self.figure.add_subplot(111)

    def render(self):
        return render_path_slice(
            self.ax, self.canvas, self.sample, self.model, viewport=(0, 4)
        )

    def test_annotation_switches_are_independent(self):
        for guides in (False, True):
            for labels in (False, True):
                with self.subTest(guides=guides, labels=labels):
                    self.model.show_guides = guides
                    self.model.show_labels = labels
                    self.render()
                    lines = [artist for artist in self.ax._path_slice_artists if artist in self.ax.lines]
                    texts = [artist for artist in self.ax._path_slice_artists if artist in self.ax.texts]
                    self.assertEqual(len(lines), 3 if guides else 0)
                    self.assertEqual(len(texts), 3 if labels else 0)
                    self.assertEqual(len(self.ax.get_xticks()), 0)

    def test_render_after_axes_clear_ignores_detached_path_artists(self):
        self.render()
        stale = list(self.ax._path_slice_artists)
        self.ax.clear()

        self.render()

        current = self.ax._path_slice_artists
        self.assertTrue(current)
        self.assertTrue(all(artist.axes is self.ax for artist in current))
        self.assertTrue(all(artist not in current for artist in stale))

    def test_multiselect_with_different_mapping_hides_single_colorbar(self):
        self.model.selected = [0, 1]
        result = self.render()
        self.assertFalse(result["colorbar"])
        self.model.selected = [0]
        result = self.render()
        self.assertTrue(result["colorbar"])
        self.assertIsNotNone(self.ax._arpes_colorbar)

    def test_descending_energy_is_displayed_ascending_without_changing_export_data(self):
        original = self.sample.intensity.copy()
        self.render()
        np.testing.assert_array_equal(self.sample.intensity, original)
        self.assertLess(self.ax.get_ylim()[0], self.ax.get_ylim()[1])

    def test_viewport_scroll_keeps_full_overview_and_centers_offscreen_segment(self):
        full = full_viewport(self.sample)
        recommended = recommended_viewport(self.sample, 300, 400)
        self.assertLessEqual(recommended[1] - recommended[0], full[1] - full[0])
        viewport = viewport_for_segment(self.sample, 1, (0, 3))
        self.assertLessEqual(viewport[0], self.sample.segments[1].start_k)
        self.assertGreaterEqual(viewport[1], self.sample.segments[1].end_k)

    def test_invalid_samples_are_transparent_and_mapping_affects_color(self):
        values = np.array([[0.0, 0.1, 0.25, np.nan]])
        linear, _ = segment_rgba(values, PathSegmentStyle(intensity_map="线性"), "magma")
        logarithmic, _ = segment_rgba(values, PathSegmentStyle(intensity_map="对数"), "magma")
        self.assertEqual(linear[0, 3, 3], 0.0)
        self.assertFalse(np.allclose(linear[0, 1], logarithmic[0, 1]))


if __name__ == "__main__":
    unittest.main()
