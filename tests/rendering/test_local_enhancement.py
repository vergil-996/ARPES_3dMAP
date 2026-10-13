import unittest

import numpy as np
import pyvista as pv
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from bandscope.processing.local_enhancement import orient_2d, resolve_display
from bandscope.rendering.local_enhancement import display_rgba
from bandscope.rendering.render_core import VisualEngine, VolumeRenderSession
from bandscope.core.crop_model import CropSelection, apply_selection
from bandscope.core.local_enhancement import physical_bounds
from tests.support.enhancement import context_2d, context_3d, region, state_2d
from tests.support.environment import requires_opengl


class Display2DTests(unittest.TestCase):
    def setUp(self):
        VisualEngine.unlock_data_range()

    def test_2d_is_opaque_like_crop_and_restores_scalar_image(self):
        context, state = context_2d(axis=0), state_2d()
        for r in state["regions"]:
            r["axes"] = ("Y", "E")
        info = dict(context["slice_info"], display_e_flip=True)
        fields = resolve_display(context, state)
        fig = Figure()
        ax = fig.add_subplot()
        canvas = FigureCanvasAgg(fig)
        VisualEngine.render_2d_slice(ax, canvas, context["data"], info, (0, 50, 100), context["coords"], display_fields=fields)
        self.assertEqual(len(ax._arpes_enhancement_images), 2)
        first = ax._arpes_enhancement_images[0]
        rgba = first.to_rgba(first.get_array(), bytes=True).copy()
        state["regions"][0]["style"]["opacity_mode"] = "sigmoid"
        fields2 = resolve_display(context, state)
        VisualEngine.render_2d_slice(ax, canvas, context["data"], info, (0, 50, 100), context["coords"], display_fields=fields2)
        first = ax._arpes_enhancement_images[0]
        after = first.to_rgba(first.get_array(), bytes=True)
        np.testing.assert_array_equal(after, rgba)
        self.assertTrue(np.all(after[..., 3] == 255))
        VisualEngine.render_2d_slice(ax, canvas, context["data"], info, (0, 50, 100), context["coords"])
        self.assertEqual(ax._arpes_image.get_array().ndim, 2)
        self.assertEqual(ax._arpes_enhancement_images, [])
        self.assertEqual(len(fig.axes), 1)

    def test_background_pixels_stay_identical_when_adding_and_removing_region(self):
        data = np.random.default_rng(17).uniform(2, 99, (36, 36)).astype(np.float32)
        context = context_2d(data)
        style = dict(levels=(13, 66, 88), opacity_mode="sigmoid")
        roi = region(1, (12, 20, 12, 20), style=style)
        fig = Figure(figsize=(5, 5), dpi=100)
        canvas = FigureCanvasAgg(fig)
        ax = fig.add_subplot()
        VisualEngine.render_2d_slice(ax, canvas, data, context["slice_info"], style["levels"], context["coords"])
        canvas.draw()
        before = np.array(canvas.buffer_rgba(), copy=True)
        x, y = ax.transData.transform((5, 5))
        patch = (slice(before.shape[0]-int(y)-20, before.shape[0]-int(y)+20), slice(int(x)-20, int(x)+20))
        fields = resolve_display(context, dict(regions=[roi], background=style))
        VisualEngine.render_2d_slice(ax, canvas, data, context["slice_info"], style["levels"], context["coords"], display_fields=fields)
        canvas.draw()
        np.testing.assert_array_equal(np.array(canvas.buffer_rgba())[patch], before[patch])
        VisualEngine.render_2d_slice(ax, canvas, data, context["slice_info"], style["levels"], context["coords"])
        canvas.draw()
        np.testing.assert_array_equal(np.array(canvas.buffer_rgba()), before)
        fig.clear()

    def test_region_layer_matches_a_real_crop_for_levels_energy_flip_and_descending_axes(self):
        data = np.random.default_rng(3).uniform(-3, 10, (8, 9)).astype(np.float32)
        for flip in (False, True):
            for levels in ((0, 50, 100), (20, 72, 81)):
                with self.subTest(flip=flip, levels=levels):
                    context = context_2d(data, axis=0)
                    context["coords"]["Y"] = np.linspace(3, -3, 8)
                    bounds = (1, 6, 2, 7)
                    roi = region(1, bounds, axes=("Y", "E"), style=dict(levels=levels, opacity_mode="sigmoid"))
                    crop = apply_selection(context, CropSelection("2d", ("Y", "E"), physical_bounds(context, bounds)))
                    overlap = region(2, (3, 6, 5, 7), axes=("Y", "E"))
                    fields = resolve_display(context, dict(regions=[roi, overlap], background=roi["style"]))
                    fig = Figure()
                    canvas = FigureCanvasAgg(fig)
                    ax = fig.add_subplot()
                    VisualEngine.render_2d_slice(ax, canvas, data, dict(context["slice_info"], display_e_flip=flip), levels, context["coords"], display_fields=fields)
                    actual = ax._arpes_enhancement_images[0]
                    crop_fig = Figure()
                    crop_ax = crop_fig.add_subplot()
                    VisualEngine.render_2d_slice(crop_ax, FigureCanvasAgg(crop_fig), crop["data"], dict(crop["slice_info"], display_e_flip=flip), levels, crop["coords"])
                    expected = crop_ax._arpes_image
                    np.testing.assert_array_equal(actual.get_array(), expected.get_array())
                    np.testing.assert_array_equal(actual.to_rgba(actual.get_array(), bytes=True), expected.to_rgba(expected.get_array(), bytes=True))
                    self.assertEqual(actual.get_clim(), expected.get_clim())
                    fig.clear()
                    crop_fig.clear()


class DisplayVolumeTests(unittest.TestCase):
    @requires_opengl
    def test_crop_transfer_functions_match_roi_and_background_transfer_is_unchanged(self):
        VisualEngine.unlock_data_range()
        shape, bounds = (12, 10, 8), (2, 7, 1, 6, 2, 5)
        source = 100 + np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
        context = dict(view="3d", data=source, enhancement_include_zero=True,
                       coords={k: np.arange(n, dtype=float) for k, n in zip(("X", "Y", "E"), shape)})
        slices = (slice(2, 8), slice(1, 7), slice(2, 6))
        full_plotter = pv.Plotter(off_screen=True, window_size=(160, 120))
        crop_plotter = pv.Plotter(off_screen=True, window_size=(160, 120))
        try:
            full, cropped = VolumeRenderSession(full_plotter), VolumeRenderSession(crop_plotter)
            for levels in ((0, 50, 100), (20, 72, 81), (0, 100, 100)):
                for mode in ("线性", "sigmoid"):
                    with self.subTest(levels=levels, mode=mode):
                        style = dict(levels=levels, opacity_mode=mode)
                        roi = region(1, bounds, view="3d", axes=("X", "Y", "E"), style=style)
                        full.render(source, levels, mode, show_axes=False)
                        actor = full.volume
                        point = (0, 2, 2)
                        before_rgb = [0., 0., 0.]
                        actor.GetProperty().GetRGBTransferFunction().GetColor(float(source[point]), before_rgb)
                        before_alpha = actor.GetProperty().GetScalarOpacity().GetValue(float(source[point]))
                        fields = resolve_display(context, dict(regions=[roi], background=style))
                        full.render(source, levels, mode, show_axes=False, display_fields=fields)
                        cropped.render(source[slices], levels, mode, show_axes=False,
                                       data_bounds=bounds, full_shape=shape, include_zero=True)
                        self.assertIs(full.volume, actor)
                        fp, cp = full.volume.GetProperty(), cropped.volume.GetProperty()
                        after_rgb = [0., 0., 0.]
                        fp.GetRGBTransferFunction().GetColor(float(fields["color"][point]), after_rgb)
                        np.testing.assert_allclose(after_rgb, before_rgb, atol=2e-5)
                        self.assertAlmostEqual(float(fields["alpha"][point]), before_alpha, places=6)
                        self.assertEqual(fields["regions"][0]["level_info"], cropped.level_info)
                        ratio = fp.GetScalarOpacityUnitDistance() / cp.GetScalarOpacityUnitDistance()
                        for point in ((2, 1, 2), (4, 3, 3), (7, 6, 5)):
                            actual, expected = [0., 0., 0.], [0., 0., 0.]
                            fp.GetRGBTransferFunction().GetColor(float(fields["color"][point]), actual)
                            cp.GetRGBTransferFunction().GetColor(float(source[point]), expected)
                            np.testing.assert_allclose(actual, expected, atol=2e-5)
                            expected_a = cp.GetScalarOpacity().GetValue(float(source[point]))
                            self.assertAlmostEqual(float(fields["alpha"][point]), 1-(1-expected_a)**ratio, places=6)
        finally:
            full_plotter.close()
            crop_plotter.close()

    @requires_opengl
    def test_masked_compact_domain_keeps_zero_extent_and_missing_alpha(self):
        context = context_3d()
        original = context["data"]
        context.update(data=original[1:5, 1:4, 1:4].copy(), full_shape=original.shape,
                       data_bounds=(1, 4, 1, 3, 1, 3))
        context["data"][0, 0, 0] = np.nan
        state = {"regions": [region(1, (1, 2, 1, 3, 1, 3), view="3d", axes=("X", "Y", "E"))],
                 "background": dict(levels=(0, 50, 100), opacity_mode="linear")}
        fields = resolve_display(context, state)
        plotter = pv.Plotter(off_screen=True, window_size=(160, 120))
        try:
            session = VolumeRenderSession(plotter)
            session.render(context["data"], (0, 50, 100), "linear", show_axes=False,
                           display_fields=fields, data_bounds=context["data_bounds"], full_shape=original.shape)
            self.assertEqual(session.grid.extent, (0, 3, 0, 2, 0, 2))
            self.assertEqual(session.display_channel.grid_alpha.extent, session.grid.extent)
            self.assertEqual(session.display_channel.buffer[0, 1], 0)
            self.assertTrue(np.isfinite(session.display_channel.buffer).all())
            self.assertTrue(np.isnan(context["data"][0, 0, 0]))
        finally:
            plotter.close()

    @requires_opengl
    def test_single_actor_survives_display_updates_and_plugin_alpha(self):
        VisualEngine.unlock_data_range()
        context = context_3d()
        state = {"regions": [region(1, (1, 3, 0, 4, 0, 3), view="3d", axes=("X", "Y", "E"))],
                 "background": dict(levels=(0, 50, 100), opacity_mode="linear")}
        fields = resolve_display(context, state)
        plotter = pv.Plotter(off_screen=True, window_size=(160, 120))
        try:
            session = VolumeRenderSession(plotter)
            weights = np.array([.3, .6, 1, 1.5], dtype=np.float32)
            session.render(context["data"], (0, 50, 100), "linear", show_axes=False,
                           display_fields=fields, opacity_multiplier=weights)
            actor = session.volume
            buffer = session.display_channel.buffer.copy()
            np.testing.assert_array_equal(buffer[:, 0], fields["color"].ravel(order="F"))
            np.testing.assert_allclose(buffer[:, 1], np.clip(fields["alpha"] * weights[None, None, :], 0, 1).ravel(order="F"))
            state["regions"][0]["style"]["opacity_mode"] = "sigmoid"
            fields2 = resolve_display(context, state)
            session.render(context["data"], (0, 50, 100), "linear", show_axes=False, display_fields=fields2)
            self.assertIs(session.volume, actor)
            self.assertEqual(session.rebuild_count, 1)
            np.testing.assert_array_equal(buffer[:, 0], session.display_channel.buffer[:, 0])
            self.assertFalse(actor.GetProperty().GetIndependentComponents())
            session.render(context["data"], (0, 50, 100), "linear", show_axes=False)
            self.assertIsNone(session.display_channel)
            self.assertTrue(actor.GetProperty().GetIndependentComponents())
            np.testing.assert_array_equal(session.level_data, context["data"])
        finally:
            plotter.close()
