import unittest

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from render_core import VisualEngine


class Render2DFastPathTests(unittest.TestCase):
    def test_same_geometry_reuses_image_and_colorbar(self):
        figure = Figure(figsize=(4, 3))
        canvas = FigureCanvasAgg(figure)
        axes = figure.add_subplot(111)
        coords = {
            "X": np.linspace(-1.0, 1.0, 4),
            "Y": np.linspace(-2.0, 2.0, 5),
            "E": np.linspace(-0.5, 0.5, 6),
        }
        slice_info = {"axis": 0, "mode": "integral", "range": (1, 1)}

        VisualEngine.render_2d_slice(
            axes,
            canvas,
            np.ones((5, 6), dtype=np.float32),
            slice_info,
            (0, 50, 100),
            coords,
        )
        first_image = axes._arpes_image
        first_colorbar = axes._arpes_colorbar

        VisualEngine.render_2d_slice(
            axes,
            canvas,
            np.full((5, 6), 3.0, dtype=np.float32),
            {**slice_info, "range": (0, 2)},
            (0, 50, 100),
            coords,
        )

        self.assertIs(axes._arpes_image, first_image)
        self.assertIs(axes._arpes_colorbar, first_colorbar)
        self.assertEqual(len(figure.axes), 2)
        np.testing.assert_allclose(np.asarray(first_image.get_array()), 3.0)

    def test_2d_axes_always_put_smaller_ticks_at_lower_left(self):
        figure = Figure(figsize=(4, 3))
        canvas = FigureCanvasAgg(figure)
        axes = figure.add_subplot(111)
        data = np.arange(12, dtype=np.float32).reshape(3, 4)
        coords = {
            "X": np.linspace(-1.0, 1.0, 2),
            "Y": np.asarray([3.0, 2.0, 1.0]),
            "E": np.asarray([4.0, 3.0, 2.0, 1.0]),
        }
        VisualEngine.render_2d_slice(
            axes,
            canvas,
            data,
            {"axis": 0, "mode": "integral", "range": (0, 0)},
            (0, 50, 100),
            coords,
        )
        self.assertLess(axes.get_xlim()[0], axes.get_xlim()[1])
        self.assertLess(axes.get_ylim()[0], axes.get_ylim()[1])
        np.testing.assert_array_equal(
            np.asarray(axes._arpes_image.get_array()),
            data.T[::-1, ::-1],
        )

    def test_e_flip_reverses_only_the_energy_pixels(self):
        figure = Figure(figsize=(4, 3))
        canvas = FigureCanvasAgg(figure)
        axes = figure.add_subplot(111)
        data = np.arange(12, dtype=np.float32).reshape(3, 4)
        coords = {
            "X": np.linspace(-1.0, 1.0, 2),
            "Y": np.asarray([1.0, 2.0, 3.0]),
            "E": np.asarray([1.0, 2.0, 3.0, 4.0]),
        }
        VisualEngine.render_2d_slice(
            axes,
            canvas,
            data,
            {
                "axis": 0,
                "mode": "integral",
                "range": (0, 0),
                "display_e_flip": True,
            },
            (0, 50, 100),
            coords,
        )
        np.testing.assert_array_equal(
            np.asarray(axes._arpes_image.get_array()),
            data.T[::-1, :],
        )


if __name__ == "__main__":
    unittest.main()
