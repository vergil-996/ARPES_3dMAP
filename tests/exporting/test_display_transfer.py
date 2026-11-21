"""Publication rendering retains the weak-signal transfer used on screen."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from matplotlib import colormaps
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from vtkmodules.util.numpy_support import vtk_to_numpy

from bandscope.core.display_style import legacy_display_style, new_display_style
from bandscope.exporting.publication_export import _freeze_2d
from bandscope.exporting.publication_models import OutputOptions, resolve_style
from bandscope.exporting.publication_renderers import compute_level_info, render_snapshot
from bandscope.processing.local_enhancement import resolve_display
from bandscope.rendering.local_enhancement import apply_volume_display
from bandscope.rendering.render_core import VisualEngine
from tests.support.enhancement import context_2d
from tests.support.environment import requires_opengl
from tests.support.publication import _make_2d_snapshot, _make_3d_axes_snapshot


class PublicationDisplayTransferTests(unittest.TestCase):
    def setUp(self):
        VisualEngine.unlock_data_range()
        self.addCleanup(VisualEngine.unlock_data_range)

    def test_global_2d_publication_and_screen_keep_the_same_weak_colors(self):
        data = np.array([[0., .01, 1.], [100., 1000., 1e6]], dtype=np.float32)
        original = data.copy()
        levels = (0, 100, 100)
        context = context_2d(data)
        screen = Figure(figsize=(4, 3), dpi=80)
        self.addCleanup(screen.clear)
        axes = screen.add_subplot()
        VisualEngine.render_2d_slice(axes, FigureCanvasAgg(screen), data,
                                    context["slice_info"], levels, context["coords"], cmap="gray")
        image = axes._arpes_image
        expected_screen = image.to_rgba(image.get_array(), bytes=True)
        snapshot = _make_2d_snapshot(
            cmap_name="gray", levels_params=levels,
            payload=dict(image=data.T.copy(), extent=[0., 1., 0., 2.],
                         level_info=compute_level_info(data, levels)),
        )
        publication = render_snapshot(snapshot, resolve_style("2d", None), {},
                                      OutputOptions(width_mm=80, height_mm=80, dpi=80), dpi=80)
        self.addCleanup(publication.clear)
        published_image = next(ax.images[0] for ax in publication.axes if len(ax.images))
        actual = published_image.to_rgba(published_image.get_array(), bytes=True)
        expected = colormaps["gray"]((data.T.astype(np.float64) / 1e6) ** .1, bytes=True)
        np.testing.assert_array_equal(actual, expected_screen)
        np.testing.assert_array_equal(actual, expected)
        self.assertGreater(int(actual[1, 0, 0]), 35)
        np.testing.assert_array_equal(data, original)
        np.testing.assert_array_equal(snapshot.payload["image"], original.T)

    def test_gray_palette_range_matches_screen_colorbar_and_publication(self):
        data = np.array([[0.0, 0.5, 1.0]])
        style = legacy_display_style((0, 50, 100), "linear", (0.2, 0.8))
        context = context_2d(data)
        fields = resolve_display(context, {
            "background": style, "regions": [], "common": False, "revision": 0,
        })
        self.assertIsNotNone(fields)
        screen = Figure(figsize=(4, 3), dpi=80)
        self.addCleanup(screen.clear)
        axes = screen.add_subplot()
        VisualEngine.render_2d_slice(
            axes, FigureCanvasAgg(screen), data, context["slice_info"], style["levels"],
            context["coords"], cmap="gray", display_fields=fields,
        )
        image = axes._arpes_image
        expected = colormaps["gray"]([0.2, 0.5, 0.8], bytes=True)
        np.testing.assert_array_equal(
            image.to_rgba(image.get_array(), bytes=True).reshape(-1, 4), expected,
        )
        colorbar_values = axes._arpes_colorbar.mappable.to_rgba([0.0, 0.5, 1.0], bytes=True)
        np.testing.assert_array_equal(colorbar_values, expected)

        snapshot = _make_2d_snapshot(
            cmap_name="gray", levels_params=style["levels"],
            payload=dict(
                image=data.T.copy(), extent=[0.0, 1.0, 0.0, 2.0],
                level_info=fields["background_level_info"],
                display={"background_style": style},
            ),
        )
        publication = render_snapshot(
            snapshot, resolve_style("2d", None), {},
            OutputOptions(width_mm=80, height_mm=80, dpi=80), dpi=80,
        )
        self.addCleanup(publication.clear)
        published_image = next(ax.images[0] for ax in publication.axes if len(ax.images))
        np.testing.assert_array_equal(
            published_image.to_rgba(published_image.get_array(), bytes=True).reshape(-1, 4), expected,
        )

    def test_independent_publication_recomputes_full_range_instead_of_stale_snapshot(self):
        data = np.array([0.0, 1.0, 2.0, 1000.0, np.nan, np.inf])
        style = new_display_style()
        style["normalization"] = {
            "mode": "percentile", "percentiles": (0.0, 99.5), "manual_range": (-3.0, 7.0),
        }
        info = compute_level_info(
            data, style["levels"], style=style, resolved_range=(-3.0, 7.0),
        )
        self.assertEqual((info["data_min"], info["data_max"]), (0.0, 1000.0))
        locked = compute_level_info(
            data * 2.0, style["levels"], style=style,
            locked_range=(0.0, 1000.0), resolved_range=(-3.0, 7.0),
        )
        self.assertEqual((locked["data_min"], locked["data_max"]), (0.0, 1000.0))

    def test_freeze_2d_ignores_stale_independent_cached_range(self):
        data = np.array([[0.0, 1.0], [2.0, 1000.0]])
        style = new_display_style()
        style["normalization"] = {"mode": "manual", "manual_range": (-3.0, 7.0)}
        snapshot = _make_2d_snapshot(payload={
            "display": {
                "background_style": style,
                "background_level_info": {"data_min": -3.0, "data_max": 7.0},
                "resolved_range": (-3.0, 7.0),
            },
        })
        with patch("bandscope.exporting.publication_export._custom_axis_titles", return_value={}):
            _freeze_2d(SimpleNamespace(), snapshot, context_2d(data))
        info = snapshot.payload["level_info"]
        self.assertEqual((info["data_min"], info["data_max"]), (0.0, 1000.0))

    @requires_opengl
    def test_global_3d_publication_uses_gamma_coordinates_and_final_alpha(self):
        volume = np.broadcast_to(
            np.array([0., .01, 1., 100.], dtype=np.float32)[:, None, None], (4, 4, 8)
        ).copy()
        volume[-1, -1, -1] = 1e6
        original = volume.copy()
        expected = (volume.astype(np.float64) / 1e6) ** .1
        shape = volume.shape
        levels = (0, 100, 100)
        for gains in (None, np.linspace(.2, 2., shape[2], dtype=np.float32)):
            with self.subTest(plugin=gains is not None):
                snapshot = _make_3d_axes_snapshot()
                snapshot.cmap_name = "gray"
                snapshot.levels_params = levels
                snapshot.coords = {key: np.arange(size, dtype=float)
                                   for key, size in zip(("X", "Y", "E"), shape)}
                snapshot.camera = dict(position=(100., -600., 100.), focal_point=(100., 100., 100.),
                                       view_up=(0., 0., 1.), parallel_projection=True, parallel_scale=110.)
                snapshot.payload.update(
                    volume=volume, data_bounds=(0, 3, 0, 3, 0, 7), full_shape=shape,
                    spacing=tuple(200. / (size - 1) for size in shape),
                    clip_render_bounds=None, include_zero=False, opacity_mode="linear",
                    opacity_multiplier=gains, show_axes=False, viewport_aspect=1.,
                    level_info=compute_level_info(volume, levels), intensity_label="Intensity (a.u.)",
                )
                captured = []

                def record_display(channel, color, alpha, cmap, multiplier=None, **kwargs):
                    result = apply_volume_display(channel, color, alpha, cmap, multiplier, **kwargs)
                    scalars = channel.volume.mapper.GetInput().GetPointData().GetScalars()
                    captured.append(dict(
                        color=np.array(color, copy=True), alpha=np.array(alpha, copy=True),
                        mapper=vtk_to_numpy(scalars).copy(),
                        science=vtk_to_numpy(channel.grid.GetPointData().GetScalars()).copy(),
                        opacity=channel.volume.GetProperty().GetScalarOpacity().GetValue(float(channel.buffer[1, 1])),
                    ))
                    return result

                with patch("bandscope.rendering.local_enhancement.apply_volume_display", side_effect=record_display):
                    publication = render_snapshot(snapshot, resolve_style("3d", None), {},
                                                  OutputOptions(width_mm=70, height_mm=70, dpi=60), dpi=60)
                self.addCleanup(publication.clear)
                self.assertEqual(len(captured), 1)
                actual = captured[0]
                np.testing.assert_allclose(actual["color"], expected, rtol=1e-6, atol=1e-8)
                np.testing.assert_allclose(actual["alpha"], expected, rtol=1e-6, atol=1e-8)
                np.testing.assert_allclose(actual["mapper"][:, 0], expected.ravel(order="F"), rtol=1e-6, atol=1e-8)
                final_alpha = expected if gains is None else np.clip(expected * gains[None, None, :], 0., 1.)
                np.testing.assert_allclose(actual["mapper"][:, 1], final_alpha.ravel(order="F"), rtol=1e-6, atol=1e-8)
                self.assertAlmostEqual(actual["opacity"], final_alpha.ravel(order="F")[1], places=6)
                np.testing.assert_array_equal(actual["science"], volume.ravel(order="F"))
                self.assertNotIn("enhancement", snapshot.payload)
        np.testing.assert_array_equal(volume, original)


if __name__ == "__main__":
    unittest.main()
