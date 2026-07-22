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


if __name__ == "__main__":
    unittest.main()
