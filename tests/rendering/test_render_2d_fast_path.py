import unittest
from unittest.mock import Mock

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from bandscope.core.display_style import create_display_state
from bandscope.processing.local_enhancement import resolve_display
from bandscope.rendering.render_core import VisualEngine
from tests.support.enhancement import context_2d, region


def _display_fields(context, regions=()):
    state = create_display_state()
    state["regions"] = list(regions)
    return resolve_display(context, state)


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
        # 色条是主轴的子坐标系，跟着图像一起等比收放，不是图级坐标轴。
        self.assertEqual(len(figure.axes), 1)
        self.assertEqual(len(axes.child_axes), 1)
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

    def test_preview_blits_with_background_only_display_fields(self):
        figure = Figure(figsize=(4, 3), dpi=100)
        canvas = FigureCanvasAgg(figure)
        axes = figure.add_subplot(111)
        data = np.arange(36, dtype=np.float32).reshape(6, 6)
        updated = data + 100.0
        context = context_2d(data)
        updated_context = context_2d(updated)
        VisualEngine.render_2d_slice(
            axes, canvas, data, context["slice_info"], (0, 50, 100), context["coords"],
            display_fields=_display_fields(context),
        )
        canvas.draw()
        original_artist_data = np.asarray(axes._arpes_image.get_array()).copy()
        original_frame = np.asarray(canvas.buffer_rgba()).copy()
        canvas.draw_idle = Mock()
        canvas.blit = Mock(wraps=canvas.blit)

        blitted = VisualEngine.render_2d_slice(
            axes, canvas, updated, updated_context["slice_info"], (0, 50, 100),
            updated_context["coords"], quality="preview",
            display_fields=_display_fields(updated_context),
        )

        self.assertTrue(blitted)
        canvas.blit.assert_called_once()
        canvas.draw_idle.assert_not_called()
        np.testing.assert_array_equal(
            np.asarray(axes._arpes_image.get_array()), original_artist_data
        )
        self.assertFalse(np.array_equal(np.asarray(canvas.buffer_rgba()), original_frame))

    def test_preview_with_ready_or_constant_region_uses_full_path(self):
        cases = (
            ("ready", np.arange(36, dtype=np.float32).reshape(6, 6)),
            ("constant", np.ones((6, 6), dtype=np.float32)),
        )
        for status, data in cases:
            with self.subTest(status=status):
                figure = Figure(figsize=(4, 3), dpi=100)
                canvas = FigureCanvasAgg(figure)
                axes = figure.add_subplot(111)
                updated = data + 10.0
                context = context_2d(data)
                updated_context = context_2d(updated)
                fields = _display_fields(context, [region(1, (0, 1, 0, 5))])
                updated_fields = _display_fields(
                    updated_context, [region(1, (0, 1, 0, 5))]
                )
                self.assertEqual(fields["regions"][0]["status"], status)
                VisualEngine.render_2d_slice(
                    axes, canvas, data, context["slice_info"], (0, 50, 100),
                    context["coords"], display_fields=fields,
                )
                canvas.draw()
                canvas.draw_idle = Mock()
                canvas.blit = Mock()

                result = VisualEngine.render_2d_slice(
                    axes, canvas, updated, updated_context["slice_info"], (0, 50, 100),
                    updated_context["coords"], quality="preview",
                    display_fields=updated_fields,
                )

                self.assertIsNone(result)
                canvas.draw_idle.assert_called_once()
                canvas.blit.assert_not_called()
                self.assertEqual(len(axes._arpes_enhancement_images), 1)
                np.testing.assert_array_equal(
                    np.asarray(axes._arpes_image.get_array()), updated.T
                )

    def test_preview_with_residual_region_layer_uses_full_path_and_removes_it(self):
        figure = Figure(figsize=(4, 3), dpi=100)
        canvas = FigureCanvasAgg(figure)
        axes = figure.add_subplot(111)
        data = np.arange(36, dtype=np.float32).reshape(6, 6)
        updated = data + 10.0
        context = context_2d(data)
        updated_context = context_2d(updated)
        VisualEngine.render_2d_slice(
            axes, canvas, data, context["slice_info"], (0, 50, 100), context["coords"],
            display_fields=_display_fields(context, [region(1, (0, 1, 0, 5))]),
        )
        canvas.draw()
        self.assertEqual(len(axes._arpes_enhancement_images), 1)
        canvas.draw_idle = Mock()
        canvas.blit = Mock()

        result = VisualEngine.render_2d_slice(
            axes, canvas, updated, updated_context["slice_info"], (0, 50, 100),
            updated_context["coords"], quality="preview",
            display_fields=_display_fields(updated_context),
        )

        self.assertIsNone(result)
        canvas.draw_idle.assert_called_once()
        canvas.blit.assert_not_called()
        self.assertEqual(axes._arpes_enhancement_images, [])
        np.testing.assert_array_equal(np.asarray(axes._arpes_image.get_array()), updated.T)


if __name__ == "__main__":
    unittest.main()
