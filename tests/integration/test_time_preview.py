import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.rendering.refresh_pipeline import RefreshCause, RenderQuality
from bandscope.rendering.render_core import VisualEngine, opacity_ramp_points


class TimePreviewTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(VisualEngine.unlock_data_range)
        VisualEngine.unlock_data_range()
        # Above the preview threshold, with an odd-index peak lost by stride 2.
        self.data = np.ones((137, 139, 133), dtype=np.float32)
        self.data[1, 1, 1] = 70.0
        self.data[3, 3, :] = 35.0

    def analyzer(self, *, flipped=False, compact=False):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        spec = SimpleNamespace(page_kind="home", params={})
        coords = {"X": np.arange(137), "Y": np.arange(139), "E": np.arange(133)}
        context = {"view": "3d", "data": self.data, "coords": coords,
                   "clip_ranges": (1, 135, 2, 137, 3, 130)}
        if compact:
            context.update(data_bounds=(0, 136, 0, 138, 0, 132), full_shape=self.data.shape)
        analyzer.left_workspace = SimpleNamespace(current_spec=lambda: spec)
        analyzer.core = SimpleNamespace(raw_data=self.data, coords=coords,
                                        logical_to_render_bounds=lambda bounds, shape: tuple(bounds))
        analyzer.current_render_context = None
        analyzer.left_display_stack = Mock()
        analyzer.ax_2d = Mock()
        analyzer.plot_coordinate_tooltip = Mock()
        analyzer.page_render = SimpleNamespace(combo_map=SimpleNamespace(currentText=lambda: "linear"),
                                               get_selected_cmap=lambda: "magma")
        analyzer.timeline_bar = SimpleNamespace(switch_axes=SimpleNamespace(isChecked=lambda: True),
                                                switch_flip=SimpleNamespace(isChecked=lambda: flipped))
        analyzer.volume_session = Mock()
        analyzer.rotation_angle = 0.0
        analyzer._box_interacting = False
        analyzer._compute_render_context = lambda _: context
        analyzer._get_display_levels = lambda: (0, 50, 100)
        analyzer._current_transfer_signature = lambda: ()
        analyzer._three_d_axis_titles = lambda _: None
        analyzer._can_show_interactive_box = lambda: False
        analyzer._plugin_session = lambda: None
        analyzer._plugin_render_context = Mock(side_effect=lambda ctx, **kw: (
            ctx, np.linspace(0.5, 1.5, kw["energy_length"], dtype=np.float32)))
        for name in ("_clear_axis_crop_interaction", "_reset_axis_title_interaction",
                     "_activate_crop_context", "_restore_3d_camera_position",
                     "_apply_pending_e_flip_camera", "_reset_volume_actor_transform",
                     "_capture_3d_camera_position", "_restore_volume_opacity_if_dimmed",
                     "_clear_interactive_box", "_refresh_axis_crop_interaction",
                     "_set_camera_view_visible", "_sync_camera_view_controls"):
            setattr(analyzer, name, Mock())
        return analyzer

    def test_time_preview_preserves_peaks_geometry_and_plugin_weights(self):
        for flipped, compact, locked in ((False, False, False), (True, False, False),
                                         (False, True, False), (True, True, True)):
            with self.subTest(flipped=flipped, compact=compact, locked=locked):
                VisualEngine.unlock_data_range()
                if locked:
                    VisualEngine._level_info(np.array([0, 100]), (0, 50, 100))
                    VisualEngine.lock_data_range()
                analyzer = self.analyzer(flipped=flipped, compact=compact)
                analyzer._render_active_page(RenderQuality.PREVIEW, RefreshCause.DATA_SOURCE)
                analyzer._render_active_page(RenderQuality.EXACT, RefreshCause.DATA_SOURCE)
                preview, exact = analyzer.volume_session.render.call_args_list
                self.assertIs(preview.args[0], self.data)
                self.assertIs(exact.args[0], self.data)
                self.assertEqual(float(preview.args[0][1, 1, 1]), 70.0)
                np.testing.assert_array_equal(preview.args[0][3, 3, :], 35.0)
                for key in ("clip_ranges", "data_bounds", "full_shape", "core_coords"):
                    # Coordinates contain arrays, so compare each axis explicitly.
                    if key == "core_coords":
                        for axis in ("X", "Y", "E"):
                            np.testing.assert_array_equal(preview.kwargs[key][axis], exact.kwargs[key][axis])
                    else:
                        self.assertEqual(preview.kwargs[key], exact.kwargs[key])
                np.testing.assert_array_equal(preview.kwargs["core_coords"]["E"], np.arange(133))
                np.testing.assert_array_equal(preview.kwargs["opacity_multiplier"], exact.kwargs["opacity_multiplier"])
                self.assertEqual(preview.kwargs["quality"], "preview")
                self.assertEqual(exact.kwargs["quality"], "exact")
                info_p = VisualEngine._level_info(preview.args[0], preview.args[1])
                info_e = VisualEngine._level_info(exact.args[0], exact.args[1])
                self.assertEqual(info_p, info_e)
                self.assertEqual(info_p["data_max"], 100.0 if locked else 70.0)
                for p, e in zip(opacity_ramp_points("linear", info_p), opacity_ramp_points("linear", info_e)):
                    np.testing.assert_array_equal(p, e)

    def test_other_preview_causes_keep_the_existing_downsampling(self):
        analyzer = self.analyzer()
        analyzer._render_active_page(RenderQuality.PREVIEW, RefreshCause.DENOISE)
        preview = analyzer.volume_session.render.call_args
        self.assertEqual(preview.args[0].shape, (69, 70, 67))
        self.assertEqual(float(np.max(preview.args[0])), 1.0)


if __name__ == "__main__":
    unittest.main()
