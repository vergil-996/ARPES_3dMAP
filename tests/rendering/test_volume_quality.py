import unittest

import numpy as np
import pyvista as pv
import vtk
from vtkmodules.util.numpy_support import vtk_to_numpy

from bandscope.core.display_style import new_display_style, new_render_quality
from bandscope.processing.display_transfer import volume_display_unit_distance
from bandscope.processing.local_enhancement import resolve_display
from bandscope.rendering.render_core import VolumeRenderSession
from tests.support.environment import requires_opengl


@requires_opengl
class VolumeQualityTests(unittest.TestCase):
    def setUp(self):
        self.plotter = pv.Plotter(off_screen=True, window_size=(100, 80))
        self.addCleanup(self.plotter.close)
        self.session = VolumeRenderSession(self.plotter)
        self.data = np.random.default_rng(31).random((14, 12, 10), dtype=np.float32)

    def test_preview_resting_fine_and_native_interactor_are_explicit(self):
        quality = new_render_quality()
        self.session.render(
            self.data, (0, 50, 100), "linear", show_axes=False,
            quality="preview", quality_config=quality,
        )
        mapper = self.session.volume.mapper
        minimum = volume_display_unit_distance(self.data.shape)
        self.assertEqual(mapper.GetInterpolationMode(), vtk.VTK_LINEAR_INTERPOLATION)
        self.assertEqual(mapper.GetAutoAdjustSampleDistances(), 0)
        self.assertEqual(mapper.GetInteractiveAdjustSampleDistances(), 0)
        self.assertAlmostEqual(mapper.GetSampleDistance(), 2.0 * minimum, places=5)
        native = self.plotter.iren.interactor
        self.assertAlmostEqual(native.GetDesiredUpdateRate(), 15.0)
        self.assertAlmostEqual(native.GetStillUpdateRate(), 2.0)

        self.session.render(
            self.data, (0, 50, 100), "linear", show_axes=False,
            quality="exact", quality_config=quality,
        )
        self.assertAlmostEqual(mapper.GetSampleDistance(), 0.5 * minimum, places=5)
        self.assertAlmostEqual(native.GetDesiredUpdateRate(), 2.0)
        quality["resting"] = "fine"
        self.session.render(
            self.data, (0, 50, 100), "linear", show_axes=False,
            quality="exact", quality_config=quality,
        )
        self.assertAlmostEqual(mapper.GetSampleDistance(), 0.25 * minimum, places=5)
        quality["interpolation"] = "nearest"
        self.session.render(
            self.data, (0, 50, 100), "linear", show_axes=False,
            quality="exact", quality_config=quality,
        )
        self.assertEqual(mapper.GetInterpolationMode(), vtk.VTK_NEAREST_INTERPOLATION)

    def test_independent_channels_survive_color_opacity_and_plugin_changes(self):
        style = new_display_style()
        style["normalization"].update(mode="manual", manual_range=(0.0, 1.0))
        state = {"background": style, "regions": [], "common": False, "revision": 0}
        quality = new_render_quality()
        original = self.data.copy()
        gains = np.linspace(0.2, 1.4, self.data.shape[2], dtype=np.float32)

        def render(revision):
            state["revision"] = revision
            fields = resolve_display(
                {"view": "3d", "data": self.data, "full_shape": self.data.shape,
                 "coords": {key: np.arange(size) for key, size in zip(("X", "Y", "E"), self.data.shape)}},
                state,
            )
            self.session.render(
                self.data, style["levels"], "linear", show_axes=False,
                display_fields=fields, display_token=revision,
                opacity_multiplier=gains, quality_config=quality,
            )
            scalars = self.session.volume.mapper.GetInput().GetPointData().GetScalars()
            values = vtk_to_numpy(scalars).reshape(-1, 2).copy()
            return fields, values

        fields, first = render(1)
        science = vtk_to_numpy(self.session.grid.GetPointData().GetScalars())
        np.testing.assert_array_equal(science, original.ravel(order="F"))
        self.assertAlmostEqual(
            self.session.volume.GetProperty().GetScalarOpacityUnitDistance(),
            volume_display_unit_distance(self.data.shape),
        )
        np.testing.assert_allclose(
            first[:, 1], np.clip(fields["alpha"] * gains[None, None, :], 0, 1).ravel(order="F"),
            rtol=1e-6, atol=1e-8,
        )
        state["background"]["levels"] = (10, 90, 90)
        _fields, second = render(2)
        self.assertFalse(np.array_equal(first[:, 0], second[:, 0]))
        np.testing.assert_array_equal(first[:, 1], second[:, 1])
        state["background"]["opacity"]["threshold"] = 35.0
        _fields, third = render(3)
        np.testing.assert_array_equal(second[:, 0], third[:, 0])
        self.assertFalse(np.array_equal(second[:, 1], third[:, 1]))
        np.testing.assert_array_equal(self.data, original)


if __name__ == "__main__":
    unittest.main()
