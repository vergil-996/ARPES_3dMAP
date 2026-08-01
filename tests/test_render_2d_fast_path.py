import unittest
from unittest.mock import Mock

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

    def test_preview_writes_pixels_without_mutating_the_exact_artist(self):
        figure = Figure(figsize=(10, 7.6), dpi=100)
        canvas = FigureCanvasAgg(figure)
        axes = figure.add_subplot(111)
        coords = {
            "X": np.linspace(-1.0, 1.0, 2),
            "Y": np.linspace(-2.0, 2.0, 200),
            "E": np.linspace(-0.5, 0.5, 800),
        }
        slice_info = {"axis": 0, "mode": "integral", "range": (0, 0)}
        data = np.arange(200 * 800, dtype=np.float32).reshape(200, 800)

        VisualEngine.render_2d_slice(
            axes, canvas, data, slice_info, (0, 50, 100), coords
        )
        canvas.draw()
        original_artist_data = np.asarray(axes._arpes_image.get_array()).copy()
        original_frame = np.asarray(canvas.buffer_rgba()).copy()
        canvas.draw_idle = Mock()
        canvas.blit = Mock(wraps=canvas.blit)

        VisualEngine.render_2d_slice(
            axes,
            canvas,
            np.flip(data, axis=1),
            {**slice_info, "range": (0, 1)},
            (0, 50, 100),
            coords,
            quality="preview",
        )

        np.testing.assert_array_equal(
            np.asarray(axes._arpes_image.get_array()),
            original_artist_data,
        )
        self.assertEqual(axes._arpes_image.get_interpolation(), "spline16")
        self.assertFalse(np.array_equal(np.asarray(canvas.buffer_rgba()), original_frame))
        canvas.blit.assert_called_once()
        canvas.draw_idle.assert_not_called()

    def test_preview_redraws_overlay_and_refreshes_clean_background(self):
        figure = Figure(figsize=(4, 3), dpi=100)
        canvas = FigureCanvasAgg(figure)
        axes = figure.add_subplot(111)
        coords = {
            "X": np.linspace(-1.0, 1.0, 2),
            "Y": np.linspace(-2.0, 2.0, 20),
            "E": np.linspace(-0.5, 0.5, 30),
        }
        data = np.ones((20, 30), dtype=np.float32)
        slice_info = {"axis": 0, "mode": "integral", "range": (0, 0)}
        VisualEngine.render_2d_slice(
            axes, canvas, data, slice_info, (0, 50, 100), coords
        )
        canvas.draw()
        overlay = axes.annotate("preview", xy=(0.0, 0.0))
        overlay.set_animated(True)
        old_background = axes._arpes_preview_background
        axes.draw_artist = Mock(wraps=axes.draw_artist)

        blitted = VisualEngine.render_2d_slice(
            axes,
            canvas,
            data * 2,
            slice_info,
            (0, 50, 100),
            coords,
            quality="preview",
            overlay_artists=(overlay,),
        )

        self.assertTrue(blitted)
        self.assertIsNot(axes._arpes_preview_background, old_background)
        self.assertIn(overlay, [call.args[0] for call in axes.draw_artist.call_args_list])

    def test_exact_after_preview_restores_full_resolution_and_spline(self):
        figure = Figure(figsize=(4, 3))
        canvas = FigureCanvasAgg(figure)
        axes = figure.add_subplot(111)
        coords = {
            "X": np.linspace(-1.0, 1.0, 2),
            "Y": np.linspace(-2.0, 2.0, 200),
            "E": np.linspace(-0.5, 0.5, 800),
        }
        slice_info = {"axis": 0, "mode": "integral", "range": (0, 0)}
        data = np.ones((200, 800), dtype=np.float32)

        VisualEngine.render_2d_slice(
            axes, canvas, data, slice_info, (0, 50, 100), coords
        )
        canvas.draw()
        VisualEngine.render_2d_slice(
            axes,
            canvas,
            data * 2,
            slice_info,
            (0, 50, 100),
            coords,
            quality="preview",
        )
        VisualEngine.render_2d_slice(
            axes,
            canvas,
            data * 3,
            slice_info,
            (0, 50, 100),
            coords,
            quality="exact",
        )

        self.assertEqual(np.asarray(axes._arpes_image.get_array()).shape, data.T.shape)
        self.assertEqual(axes._arpes_image.get_interpolation(), "spline16")
        np.testing.assert_allclose(np.asarray(axes._arpes_image.get_array()), 3.0)


if __name__ == "__main__":
    unittest.main()
