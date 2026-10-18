"""Exercise transfer rules at actual Matplotlib and VTK mapper inputs."""
import unittest

import numpy as np
import pyvista as pv
from matplotlib import colormaps
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from vtkmodules.util.numpy_support import vtk_to_numpy

from bandscope.processing.local_enhancement import resolve_display
from bandscope.processing.display_transfer import level_info
from bandscope.rendering.render_core import VisualEngine, VolumeRenderSession
from tests.support.enhancement import context_2d, region
from tests.support.environment import requires_opengl


class DisplayLegendTransferTests(unittest.TestCase):
    def test_legend_keeps_window_endpoints_and_gamma_midpoint(self):
        for levels in ((0, 100, 100), (20, 100, 80), (20, 0, 80), (20, 50, 80)):
            with self.subTest(levels=levels):
                info = level_info((0., 100.), levels)
                legend = VisualEngine._3d_colorbar_lut("magma", info)
                for index, (value, coordinate) in enumerate(((info["black_value"], 0.), (info["gray_value"], .5), (info["white_value"], 1.))):
                    expected = np.asarray(colormaps["magma"](coordinate, bytes=True)[:3]) / 255.
                    rgb = [0., 0., 0.]
                    legend.GetColor(coordinate, rgb)
                    np.testing.assert_allclose(rgb, expected, atol=1e-7)
                    self.assertEqual(legend.GetAnnotation(index), VisualEngine._format_level_tick(value))
                mappable = VisualEngine._colorbar_mappable("magma", info)
                self.assertAlmostEqual(float(mappable.norm(info["gray_value"])), .5)


class Display2DTransferTests(unittest.TestCase):
    def setUp(self):
        VisualEngine.unlock_data_range()
        self.figure = Figure(figsize=(4, 3), dpi=80)
        self.canvas = FigureCanvasAgg(self.figure)
        self.axes = self.figure.add_subplot()
        self.addCleanup(self.figure.clear)
        self.addCleanup(VisualEngine.unlock_data_range)

    def test_weak_spectrum_reaches_gamma_in_exact_and_preview(self):
        data = np.array([[0., .01, 1.], [100., 1000., 1e6]], dtype=np.float32)
        original = data.copy()
        context = context_2d(data)
        expected = colormaps["gray"]((data.T.astype(np.float64) / 1e6) ** .1, bytes=True)
        for quality in ("exact", "preview", "exact"):
            with self.subTest(quality=quality):
                VisualEngine.render_2d_slice(
                    self.axes, self.canvas, data, context["slice_info"], (0, 100, 100),
                    context["coords"], cmap="gray", quality=quality,
                )
                image = self.axes._arpes_image
                np.testing.assert_array_equal(image.to_rgba(image.get_array(), bytes=True), expected)
                self.assertAlmostEqual(float(self.axes._arpes_colorbar.norm(1e6 * .5 ** 10)), .5)
                self.assertGreater(int(expected[1, 0, 0]), 35)
        np.testing.assert_array_equal(data, original)

    def test_window_endpoints_use_full_palette_and_survive_style_restore(self):
        data = np.array([[0., 20., 35.], [50., 80., 100.]], dtype=np.float32)
        context = context_2d(data)
        for levels in ((20, 50, 80), (0, 100, 100), (20, 50, 80)):
            VisualEngine.render_2d_slice(
                self.axes, self.canvas, data, context["slice_info"], levels,
                context["coords"], cmap="gray",
            )
            image = self.axes._arpes_image
            rgba = image.to_rgba(image.get_array(), bytes=True)
            if levels == (20, 50, 80):
                self.assertEqual(int(rgba[1, 0, 0]), 0)
                self.assertEqual(int(rgba[1, 1, 0]), 255)

    def test_local_region_preserves_the_same_weak_samples_as_global(self):
        data = np.array([[0., .01, 1.], [100., 1000., 1e6]], dtype=np.float32)
        context = context_2d(data)
        style = dict(levels=(0, 100, 100), opacity_mode="linear")
        roi = region(1, (0, 1, 0, 2), style=style)
        fields = resolve_display(context, dict(regions=[roi], background=style))
        VisualEngine.render_2d_slice(
            self.axes, self.canvas, data, context["slice_info"], style["levels"],
            context["coords"], cmap="gray", display_fields=fields,
        )
        expected = colormaps["gray"]((data.T.astype(np.float64) / 1e6) ** .1, bytes=True)
        background = self.axes._arpes_image
        overlay = self.axes._arpes_enhancement_images[0]
        np.testing.assert_array_equal(background.to_rgba(background.get_array(), bytes=True), expected)
        np.testing.assert_array_equal(overlay.to_rgba(overlay.get_array(), bytes=True), expected)


@requires_opengl
class DisplayVolumeTransferTests(unittest.TestCase):
    def setUp(self):
        VisualEngine.unlock_data_range()
        self.addCleanup(VisualEngine.unlock_data_range)
        self.plotter = pv.Plotter(off_screen=True, window_size=(80, 80))
        self.addCleanup(self.plotter.close)
        self.session = VolumeRenderSession(self.plotter)
        self.data = np.broadcast_to(
            np.array([0., .01, 1., 100.], dtype=np.float32)[:, None, None], (4, 4, 8)
        ).copy()
        self.data[-1, -1, -1] = 1e6

    def _mapper_values(self):
        scalars = self.session.volume.mapper.GetInput().GetPointData().GetScalars()
        return vtk_to_numpy(scalars).reshape(-1, scalars.GetNumberOfComponents())

    def _assert_science_unchanged(self):
        actual = vtk_to_numpy(self.session.grid.GetPointData().GetScalars())
        np.testing.assert_array_equal(actual, self.data.ravel(order="F"))
        np.testing.assert_array_equal(self.session.level_data, self.data)

    def test_weak_volume_uses_gamma_coordinates_before_vtk_sampling(self):
        original = self.data.copy()
        expected = (self.data.astype(np.float64) / 1e6) ** .1
        for quality in ("exact", "preview", "exact"):
            with self.subTest(quality=quality):
                self.session.render(self.data, (0, 100, 100), "linear", cmap="gray",
                                    show_axes=False, quality=quality)
                actual = self._mapper_values()
                self.assertEqual(actual.shape[1], 2)
                np.testing.assert_allclose(actual[:, 0], expected.ravel(order="F"), rtol=1e-6, atol=1e-8)
                np.testing.assert_allclose(actual[:, 1], expected.ravel(order="F"), rtol=1e-6, atol=1e-8)
                weak = np.ravel_multi_index((1, 0, 0), self.data.shape, order="F")
                rgb = self.session.volume.GetProperty().GetRGBTransferFunction().GetColor(float(actual[weak, 0]))
                np.testing.assert_allclose(rgb, [expected[1, 0, 0]] * 3, atol=.005)
                alpha = self.session.volume.GetProperty().GetScalarOpacity().GetValue(float(actual[weak, 1]))
                self.assertAlmostEqual(alpha, expected[1, 0, 0], places=6)
                legend = self.plotter.scalar_bars[VisualEngine.COLORBAR_TITLE].GetLookupTable()
                middle = self.session.level_info["gray_value"]
                rgb = [0., 0., 0.]
                legend.GetColor(.5, rgb)
                np.testing.assert_allclose(rgb, [.5] * 3, atol=.005)
                self.assertEqual(legend.GetAnnotation(1), VisualEngine._format_level_tick(middle))
                self._assert_science_unchanged()
        np.testing.assert_array_equal(self.data, original)

    def test_plugin_gain_and_style_restore_keep_weak_signal_and_actor(self):
        gains = np.linspace(.2, 2., self.data.shape[2], dtype=np.float32)
        expected_color = (self.data.astype(np.float64) / 1e6) ** .1
        expected_alpha = np.clip(expected_color * gains[None, None, :], 0., 1.)
        self.session.render(self.data, (0, 100, 100), "linear", show_axes=False,
                            opacity_multiplier=gains)
        actor = self.session.volume
        before = self._mapper_values().copy()
        np.testing.assert_allclose(before[:, 0], expected_color.ravel(order="F"), rtol=1e-6, atol=1e-8)
        np.testing.assert_allclose(before[:, 1], expected_alpha.ravel(order="F"), rtol=1e-6, atol=1e-8)
        self.session.render(self.data, (20, 50, 80), "sigmoid", show_axes=False)
        self._assert_science_unchanged()
        self.session.render(self.data, (0, 100, 100), "linear", show_axes=False,
                            opacity_multiplier=gains)
        self.assertIs(self.session.volume, actor)
        np.testing.assert_array_equal(self._mapper_values(), before)
        self._assert_science_unchanged()

    def test_local_normalization_keeps_the_same_weak_coordinates_as_global(self):
        self.session.render(self.data, (0, 100, 100), "linear", show_axes=False)
        expected = self._mapper_values().copy()
        context = dict(view="3d", data=self.data, coords={
            key: np.arange(size, dtype=float)
            for key, size in zip(("X", "Y", "E"), self.data.shape)
        })
        style = dict(levels=(0, 100, 100), opacity_mode="linear")
        roi = region(1, (0, 3, 0, 3, 0, 7), view="3d", axes=("X", "Y", "E"), style=style)
        fields = resolve_display(context, dict(regions=[roi], background=style))
        self.session.render(self.data, (0, 100, 100), "linear", show_axes=False,
                            display_fields=fields)
        np.testing.assert_allclose(self._mapper_values(), expected, rtol=1e-6, atol=1e-8)
        self._assert_science_unchanged()

    def test_new_frame_and_forced_upload_refresh_the_nonlinear_display(self):
        self.session.render(self.data, (0, 100, 100), "linear", show_axes=False)
        actor = self.session.volume
        uploads = self.session.data_update_count
        replacement = self.data.copy()
        for maximum, force, quality in ((1e8, False, "preview"), (1e4, True, "exact")):
            with self.subTest(maximum=maximum, force_data=force):
                replacement[-1, -1, -1] = maximum
                self.session.render(replacement, (0, 100, 100), "linear", show_axes=False,
                                    force_data=force, quality=quality)
                expected = (replacement.astype(np.float64) / maximum) ** .1
                actual = self._mapper_values()
                np.testing.assert_allclose(actual[:, 0], expected.ravel(order="F"), rtol=1e-6, atol=1e-8)
                np.testing.assert_allclose(actual[:, 1], expected.ravel(order="F"), rtol=1e-6, atol=1e-8)
                np.testing.assert_array_equal(vtk_to_numpy(self.session.grid.GetPointData().GetScalars()), replacement.ravel(order="F"))
                self.assertIs(self.session.volume, actor)
        self.assertEqual(self.session.data_update_count, uploads + 2)


if __name__ == "__main__":
    unittest.main()
