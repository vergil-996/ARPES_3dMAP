import unittest
from types import SimpleNamespace

import numpy as np

from refactored_app import My3DAnalyzer


class AnalysisControlRefreshTests(unittest.TestCase):
    @staticmethod
    def _analyzer_with_page(spec):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.core = SimpleNamespace(raw_data=np.zeros((4, 5, 6, 1), dtype=np.float32))
        analyzer._syncing_controls = False
        analyzer._syncing_axis_value_boxes = False
        analyzer.left_workspace = SimpleNamespace(current_spec=lambda: spec)
        analyzer._can_show_interactive_box = lambda: False
        analyzer.sync_ax_sliders_to_box = lambda: None
        analyzer._persist_axis_integral_page_state = lambda _spec: None
        analyzer._persist_second_derivative_page_state = lambda _spec: None
        analyzer.request_refresh = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("3D analysis controls must not request a refresh")
        )
        return analyzer

    def test_home_3d_axis_slider_only_moves_interactive_box(self):
        spec = SimpleNamespace(page_kind="home", params={})
        analyzer = self._analyzer_with_page(spec)
        calls = []
        analyzer._can_show_interactive_box = lambda: True
        analyzer.sync_ax_sliders_to_box = lambda: calls.append("box")

        analyzer.schedule_axis_refresh()

        self.assertEqual(calls, ["box"])

    def test_other_3d_pages_do_not_request_analysis_refresh(self):
        specs = [
            SimpleNamespace(page_kind="time_integral", params={}),
            SimpleNamespace(page_kind="second_derivative", params={"source_view": "3d"}),
        ]

        for spec in specs:
            with self.subTest(page_kind=spec.page_kind):
                analyzer = self._analyzer_with_page(spec)
                analyzer.schedule_axis_refresh()

    def test_time_slider_does_not_recompute_a_time_integral_3d_page(self):
        spec = SimpleNamespace(page_kind="time_integral", params={})
        analyzer = self._analyzer_with_page(spec)
        analyzer._syncing_controls = False
        analyzer.axis_source_mode = "frame"
        analyzer._persist_time_integral_page_state = lambda _spec: (_ for _ in ()).throw(
            AssertionError("3D result parameters must not be changed while dragging")
        )
        analyzer.schedule_axis_refresh = lambda: (_ for _ in ()).throw(
            AssertionError("3D time sliders must not schedule a refresh")
        )

        analyzer.on_time_integral_controls_changed(2)

        self.assertEqual(analyzer.axis_source_mode, "time_integral")

    def test_axis_integral_2d_page_still_refreshes_interactively(self):
        spec = SimpleNamespace(page_kind="axis_integral", params={})
        analyzer = self._analyzer_with_page(spec)
        calls = []
        analyzer._persist_axis_integral_page_state = lambda target: calls.append(("persist", target))
        analyzer.request_refresh = lambda *args, **kwargs: calls.append(("refresh", args, kwargs))

        analyzer.schedule_axis_refresh()

        self.assertEqual(calls[0], ("persist", spec))
        self.assertEqual(calls[1][0], "refresh")
        self.assertTrue(calls[1][2]["interactive"])

    def test_linked_slider_signal_does_not_schedule_a_duplicate_refresh(self):
        spec = SimpleNamespace(page_kind="axis_integral", params={})
        analyzer = self._analyzer_with_page(spec)
        analyzer.page_data = SimpleNamespace(_is_updating=True)
        calls = []
        analyzer.request_refresh = lambda *args, **kwargs: calls.append((args, kwargs))

        analyzer.schedule_axis_refresh()

        self.assertEqual(calls, [])

    def test_interactive_box_prefers_live_bounds_over_saved_clip(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.core = SimpleNamespace(
            raw_data=np.zeros((4, 5, 6, 1), dtype=np.float32),
            logical_to_render_bounds=lambda bounds, _shape: list(bounds),
        )
        analyzer.precise_logical_bounds = [1, 2, 1, 3, 2, 4]
        analyzer.clip_ranges = [0, 3, 0, 4, 0, 5]
        analyzer._map_visual_flip_logical_bounds = lambda bounds, _shape: list(bounds)

        actual = analyzer._get_render_bounds_for_box()

        self.assertEqual(actual, analyzer.precise_logical_bounds)

    def test_moving_one_box_axis_resets_the_other_axes_to_full_volume(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer._can_show_interactive_box = lambda: True
        analyzer.page_data = SimpleNamespace(combo_ax=SimpleNamespace(currentIndex=lambda: 1))
        analyzer._axis_input_logical_values = lambda: (2, 3, 2)
        analyzer._get_full_logical_bounds = lambda: [0, 3, 0, 4, 0, 5]
        analyzer.precise_logical_bounds = [1, 2, 0, 4, 1, 5]
        analyzer.clip_ranges = [0, 3, 0, 4, 0, 5]
        seen = []
        analyzer._rebuild_interactive_box = lambda bounds: seen.append(("box", list(bounds)))
        analyzer._sync_slice_edits_from_logical_bounds = lambda bounds: seen.append(
            ("edits", list(bounds))
        )

        analyzer.sync_ax_sliders_to_box()

        expected = [0, 3, 2, 3, 0, 5]
        self.assertEqual(seen, [("box", expected), ("edits", expected)])

    def test_switching_axis_updates_range_then_box_without_3d_compute(self):
        spec = SimpleNamespace(page_kind="home", params={})
        analyzer = self._analyzer_with_page(spec)
        calls = []
        analyzer.update_ax_slider_range = lambda: calls.append("range")
        analyzer.schedule_axis_refresh = lambda: calls.append("box")

        analyzer.on_axis_selection_changed(2)

        self.assertEqual(calls, ["range", "box"])


if __name__ == "__main__":
    unittest.main()
