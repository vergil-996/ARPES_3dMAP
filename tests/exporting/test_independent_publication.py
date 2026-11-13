import unittest
from unittest.mock import patch

import numpy as np

from bandscope.core.display_style import new_display_style, new_render_quality
from bandscope.exporting.publication_models import OutputOptions, resolve_style
from bandscope.exporting.publication_renderers import render_snapshot
from bandscope.processing.display_transfer import volume_display_unit_distance
from bandscope.processing.local_enhancement import resolve_display
from bandscope.rendering.local_enhancement import apply_volume_display
from tests.support.environment import requires_opengl
from tests.support.publication import _make_3d_axes_snapshot


class IndependentPublicationTests(unittest.TestCase):
    @requires_opengl
    def test_frozen_channels_and_fine_mapper_match_main_display(self):
        volume = np.random.default_rng(37).random((8, 6, 5), dtype=np.float32)
        style = new_display_style()
        style["normalization"].update(mode="manual", manual_range=(0.0, 1.0))
        fields = resolve_display(
            {"view": "3d", "data": volume, "full_shape": volume.shape,
             "coords": {key: np.arange(size) for key, size in zip(("X", "Y", "E"), volume.shape)}},
            {"background": style, "regions": [], "common": False, "revision": 0},
        )
        snapshot = _make_3d_axes_snapshot()
        snapshot.cmap_name = "magma"
        snapshot.levels_params = style["levels"]
        snapshot.coords = {key: np.arange(size, dtype=float)
                           for key, size in zip(("X", "Y", "E"), volume.shape)}
        snapshot.camera = dict(
            position=(100.0, -600.0, 100.0), focal_point=(100.0, 100.0, 100.0),
            view_up=(0.0, 0.0, 1.0), parallel_projection=True, parallel_scale=110.0,
        )
        snapshot.payload.update(
            volume=volume, data_bounds=(0, 7, 0, 5, 0, 4), full_shape=volume.shape,
            spacing=tuple(200.0 / (size - 1) for size in volume.shape),
            clip_render_bounds=None, include_zero=False, opacity_mode="linear",
            opacity_multiplier=None, show_axes=False, viewport_aspect=1.0,
            level_info=fields["background_level_info"], intensity_label="Intensity (a.u.)",
            display={"background_style": style, "resolved_range": (0.0, 1.0)},
            enhancement={
                "color": fields["color"], "alpha": fields["alpha"], "active": False,
                "background_level_info": fields["background_level_info"],
            },
            render_quality=new_render_quality(),
        )
        captured = {}

        def record_display(channel, color, alpha, cmap, multiplier=None, **kwargs):
            captured["color"] = np.array(color, copy=True)
            captured["alpha"] = np.array(alpha, copy=True)
            return apply_volume_display(channel, color, alpha, cmap, multiplier, **kwargs)

        from bandscope.rendering.render_core import configure_volume_quality

        def record_quality(actor, plotter, quality, config, **kwargs):
            result = configure_volume_quality(actor, plotter, quality, config, **kwargs)
            captured["sample_distance"] = actor.mapper.GetSampleDistance()
            captured["interpolation"] = actor.mapper.GetInterpolationMode()
            captured["auto_adjust"] = actor.mapper.GetAutoAdjustSampleDistances()
            return result

        with patch("bandscope.rendering.local_enhancement.apply_volume_display", side_effect=record_display), \
                patch("bandscope.rendering.render_core.configure_volume_quality", side_effect=record_quality):
            figure = render_snapshot(
                snapshot, resolve_style("3d", None), {},
                OutputOptions(width_mm=70, height_mm=70, dpi=60), dpi=60,
            )
        self.addCleanup(figure.clear)
        np.testing.assert_array_equal(captured["color"], fields["color"])
        np.testing.assert_array_equal(captured["alpha"], fields["alpha"])
        self.assertAlmostEqual(
            captured["sample_distance"], 0.25 * volume_display_unit_distance(volume.shape), places=5,
        )
        self.assertEqual(captured["auto_adjust"], 0)
        self.assertEqual(captured["interpolation"], 1)


if __name__ == "__main__":
    unittest.main()
