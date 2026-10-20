import unittest
from unittest.mock import Mock, patch

import numpy as np
import pyvista as pv
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from vtkmodules.util.numpy_support import vtk_to_numpy

from bandscope.processing.display_transfer import level_info
from bandscope.processing.local_enhancement import orient_2d, resolve_display
from bandscope.rendering.local_enhancement import (
    add_region_scalar_bars_3d, display_rgba, draw_region_colorbars_2d,
)
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

    def test_2d_region_layers_are_reused_between_renders(self):
        context, state = context_2d(axis=0), state_2d()
        for r in state["regions"]:
            r["axes"] = ("Y", "E")
        info = dict(context["slice_info"], display_e_flip=True)
        fields = resolve_display(context, state)
        fig = Figure()
        ax = fig.add_subplot()
        canvas = FigureCanvasAgg(fig)
        VisualEngine.render_2d_slice(ax, canvas, context["data"], info, (0, 50, 100),
                                     context["coords"], display_fields=fields)
        images = list(ax._arpes_enhancement_images)
        self.assertEqual(len(images), 2)
        VisualEngine.render_2d_slice(ax, canvas, context["data"], info, (0, 50, 100),
                                     context["coords"], display_fields=fields)
        self.assertIs(ax._arpes_enhancement_images[0], images[0])
        self.assertIs(ax._arpes_enhancement_images[1], images[1])

    def test_region_colorbars_are_reused_and_rebuilt_by_count(self):
        regions = [dict(region_id="1", name="R1", label="R1", status="ready",
                        data_range=(0.0, 1.0), level_info=level_info((0.0, 1.0), (0, 50, 100)))]
        fig = Figure()
        ax = fig.add_subplot()
        FigureCanvasAgg(fig)
        bars = draw_region_colorbars_2d(ax, regions, "magma")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].ax._arpes_band_target, ("region", "1"))
        again = draw_region_colorbars_2d(ax, regions, "viridis", existing=bars)
        self.assertIs(again[0], bars[0])
        self.assertEqual(again[0].ax._arpes_band_target, ("region", "1"))
        empty = draw_region_colorbars_2d(ax, [], "magma", existing=again)
        self.assertEqual(empty, [])
        self.assertNotIn(again[0].ax, fig.axes)

    def test_region_scalar_bars_report_their_region_targets(self):
        class Plotter:
            background_color = (1.0, 1.0, 1.0)

            def __init__(self):
                self.calls = []

            def add_scalar_bar(self, **kwargs):
                bar = Mock()
                self.calls.append((kwargs, bar))
                return bar

        regions = [
            dict(region_id="1", name="R1", label="R1", status="ready",
                 data_range=(0.0, 1.0), level_info=level_info((0.0, 1.0), (0, 50, 100))),
            dict(region_id="2", name="R2", label="R2", status="ready",
                 data_range=(0.0, 1.0), level_info=level_info((0.0, 1.0), (10, 50, 90))),
        ]
        plotter = Plotter()
        records = add_region_scalar_bars_3d(plotter, regions, "magma")
        self.assertEqual([record["region_id"] for record in records], ["1", "2"])
        self.assertEqual([record["title"] for record in records], ["R1", "R2"])
        self.assertIs(records[0]["actor"], plotter.calls[0][1])
        self.assertIs(records[1]["actor"], plotter.calls[1][1])
        self.assertEqual(plotter.calls[0][0]["position_y"], 0.08)
        self.assertEqual(plotter.calls[1][0]["position_y"], 0.38)

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
    @staticmethod
    def _mapped_rgba(session, point, shape):
        scalars = session.volume.mapper.GetInput().GetPointData().GetScalars()
        values = vtk_to_numpy(scalars).reshape(-1, scalars.GetNumberOfComponents())
        sample = values[np.ravel_multi_index(point, shape, order="F")]
        prop = session.volume.GetProperty()
        rgb = prop.GetRGBTransferFunction().GetColor(float(sample[0]))
        alpha = prop.GetScalarOpacity().GetValue(float(sample[-1]))
        return rgb, alpha

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
                        before_rgb, before_alpha = self._mapped_rgba(full, point, shape)
                        fields = resolve_display(context, dict(regions=[roi], background=style))
                        full.render(source, levels, mode, show_axes=False, display_fields=fields)
                        cropped.render(source[slices], levels, mode, show_axes=False,
                                       data_bounds=bounds, full_shape=shape, include_zero=True)
                        self.assertIs(full.volume, actor)
                        fp, cp = full.volume.GetProperty(), cropped.volume.GetProperty()
                        after_rgb, _ = self._mapped_rgba(full, point, shape)
                        np.testing.assert_allclose(after_rgb, before_rgb, atol=2e-5)
                        self.assertAlmostEqual(float(fields["alpha"][point]), before_alpha, places=6)
                        self.assertEqual(fields["regions"][0]["level_info"], cropped.level_info)
                        ratio = fp.GetScalarOpacityUnitDistance() / cp.GetScalarOpacityUnitDistance()
                        for point in ((2, 1, 2), (4, 3, 3), (7, 6, 5)):
                            actual, _ = self._mapped_rgba(full, point, shape)
                            crop_point = tuple(value - sl.start for value, sl in zip(point, slices))
                            expected, expected_a = self._mapped_rgba(cropped, crop_point, source[slices].shape)
                            np.testing.assert_allclose(actual, expected, atol=2e-5)
                            self.assertAlmostEqual(float(fields["alpha"][point]), 1-(1-expected_a)**ratio, places=6)
                        np.testing.assert_array_equal(vtk_to_numpy(full.grid.GetPointData().GetScalars()), source.ravel(order="F"))
                        np.testing.assert_array_equal(vtk_to_numpy(cropped.grid.GetPointData().GetScalars()), source[slices].ravel(order="F"))
        finally:
            full_plotter.close()
            crop_plotter.close()

    @requires_opengl
    def test_same_display_token_skips_the_second_upload(self):
        """同一份增强通道重复渲染时不再重写两份体素数组。"""
        context = context_3d()
        state = {"regions": [region(1, (0, 3, 0, 3, 0, 3), view="3d", axes=("X", "Y", "E"))],
                 "background": dict(levels=(0, 50, 100), opacity_mode="linear")}
        fields = resolve_display(context, state)
        plotter = pv.Plotter(off_screen=True, window_size=(160, 120))
        self.addCleanup(plotter.close)
        session = VolumeRenderSession(plotter)
        with patch("bandscope.rendering.local_enhancement.apply_volume_display") as upload:
            for _ in range(2):
                session.render(context["data"], (0, 50, 100), "linear", show_axes=False,
                               display_fields=fields, display_token=1, render=False)
            self.assertEqual(upload.call_count, 1)
            session.render(context["data"], (0, 50, 100), "linear", show_axes=False,
                           display_fields=fields, display_token=2, render=False)
            self.assertEqual(upload.call_count, 2)

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
