import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from matplotlib.figure import Figure
from PyQt5.QtWidgets import QApplication

from bandscope.app.path_slice_integration import PathSliceController
from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.view_recipe import ViewRecipeDocument
from bandscope.core.path_slice import (
    PATH_SOURCE_KEY,
    PathNode,
    PathSegmentStyle,
    PathSliceModel,
    draft_nodes,
    write_draft,
)
from bandscope.exporting.publication_export import capture_snapshot
from bandscope.processing.compute_backends import ArrayLRUCache, BackendManager
from bandscope.ui.result_workspace import AnalysisPageSpec
from tests.support.view_recipe import control_window, synthetic_npz


class _Slider:
    def __init__(self, value=0):
        self._value = value

    def value(self):
        return self._value


class _Combo:
    def __init__(self, text="线性"):
        self.text = text

    def currentText(self):
        return self.text


class PathSliceHostTests(unittest.TestCase):
    def setUp(self):
        self.raw = np.zeros((4, 5, 3, 2), dtype=np.float32)
        for frame in range(2):
            x, y = np.meshgrid(np.arange(4), np.arange(5), indexing="ij")
            self.raw[..., frame] = (x + 10 * y + 100 * frame)[..., None]
        self.coords = {
            "X": np.arange(4, dtype=float), "Y": np.arange(5, dtype=float),
            "E": np.array([-1.0, 0.0, 1.0]), "delay": np.array([0.0, 100.0]),
        }
        self.window = My3DAnalyzer.__new__(My3DAnalyzer)
        self.window.core = AnalyzerCore()
        self.window.core.raw_data = self.raw
        self.window.core.coords = self.coords
        self.window.core.has_time_axis = True
        self.window.core.coord_sources = {"X": "index", "Y": "index", "E": "file"}
        self.window.core.coord_units = {"E": "eV"}
        self.window.original_raw_data = self.raw
        self.window.original_coords = self.coords
        self.window.rotation_angle = 0.0
        self.window.shared_denoise_version = 0
        self.window._rotation_cache = {}
        self.window._computed_volume_cache = ArrayLRUCache()
        self.window.backend_manager = BackendManager("CPU")
        self.window.timeline_bar = SimpleNamespace(slider_time=_Slider(0))
        self.window.page_data = SimpleNamespace(s_t_low=_Slider(0), s_t_up=_Slider(1))
        self.window._get_display_state_for_spec = lambda _spec: (self.raw, self.coords)
        self.window._syncing_controls = False
        self.window.left_workspace = SimpleNamespace(current_spec=lambda: self.source)
        self.source = AnalysisPageSpec(
            "source", "E map", "axis_integral", "test",
            params={
                "axis_index": 2, "low": 1, "up": 1, "mid": 1,
                "source_mode": "frame", "source_t_index": 0,
                "source_t_low": 0, "source_t_up": 1,
            }, data_scope_id="full",
        )
        self.controller = PathSliceController(self.window, initialize_ui=False)
        self.window.path_slice_controller = self.controller
        self.model = PathSliceModel(
            [PathNode(0, 0, "Γ"), PathNode(2, 0, "K"), PathNode(2, 2, "M")],
            [PathSegmentStyle(), PathSegmentStyle()],
        )

    def path_spec(self):
        params = {
            PATH_SOURCE_KEY: dict(self.source.params, source_page_kind="axis_integral"),
            "source_mode": "frame", "source_t_index": 0,
            "source_t_low": 0, "source_t_up": 1,
        }
        self.model.write_params(params)
        spec = AnalysisPageSpec(
            "path", "路径", "path_slice", "path_slice",
            params=params, source_page_id="source", data_scope_id="full",
        )
        self.window.left_workspace.current_spec = lambda: spec
        return spec

    def _show_draft(self):
        write_draft(self.source.params, [PathNode(0, 0, "P1"), PathNode(2, 0, "P2")])
        self.window.left_workspace.current_spec = lambda: self.source
        figure = Figure(figsize=(5, 4), dpi=100)
        self.window.ax_2d = figure.add_subplot(111)
        self.window.canvas_2d = SimpleNamespace(
            draw_idle=lambda: None,
            setCursor=lambda *_args: None,
            unsetCursor=lambda: None,
            mapToGlobal=lambda pos: pos,
        )
        self.window._update_crop_cursor = lambda: self.window.canvas_2d.unsetCursor()
        self.controller.active_source_id = "source"
        self.controller.redraw_draft()

    def test_context_uses_full_energy_range_and_exports_path_metadata(self):
        spec = self.path_spec()
        context = self.controller.compute_context(spec)
        np.testing.assert_array_equal(context["energy_axis"], self.coords["E"])
        self.assertEqual(context["raw_intensity"].shape, (5, 3))
        title, name, payload = self.controller.export_payload(spec, context)
        self.assertEqual(title, "导出路径切片")
        self.assertTrue(name.endswith(".mat"))
        np.testing.assert_array_equal(payload["E"], self.coords["E"])
        np.testing.assert_array_equal(payload["path_node_names"], ["Γ", "K", "M"])
        self.assertEqual(payload["intensity"].shape, (5, 3))

    def test_time_scrubbing_reuses_path_and_switches_source_frame(self):
        spec = self.path_spec()
        initial = self.controller.compute_context(spec)["raw_intensity"].copy()
        self.window.timeline_bar.slider_time._value = 1
        self.controller.persist_time(spec)
        changed = self.controller.compute_context(spec)["raw_intensity"]
        self.assertEqual(spec.params["source_t_index"], 1)
        self.assertEqual(spec.params[PATH_SOURCE_KEY]["source_t_index"], 1)
        np.testing.assert_allclose(changed, initial + 100)
        self.assertEqual([node["label"] for node in spec.params["path_nodes"]], ["Γ", "K", "M"])

    def test_crop_mask_excludes_outside_samples_from_result(self):
        self.source.params.update(crop_k_low=1, crop_k_up=2, crop_e_low=1, crop_e_up=2)
        spec = self.path_spec()
        context = self.controller.compute_context(spec)
        self.assertTrue(np.isnan(context["raw_intensity"][:3]).all())
        self.assertTrue(np.isfinite(context["raw_intensity"][3:]).all())

    def test_closing_selection_hides_draft_and_reopening_restores_it(self):
        self._show_draft()
        artists = list(self.controller._draft_artists)
        self.assertTrue(artists)

        self.controller.toggle_selection_mode(False)

        self.assertEqual(self.controller._draft_artists, [])
        self.assertTrue(all(artist.axes is None for artist in artists))
        self.assertEqual(len(draft_nodes(self.source.params)), 2)
        self.assertIsNone(self.controller.active_source_id)

        self.controller.toggle_selection_mode(True)

        self.assertTrue(self.controller._draft_artists)
        self.assertEqual(len(draft_nodes(self.source.params)), 2)
        self.assertEqual(self.controller.active_source_id, "source")

    def test_context_menu_clears_all_draft_nodes_without_closing_selection(self):
        self._show_draft()
        menu = mock.MagicMock()
        actions = {}

        def add_action(text):
            action = mock.MagicMock()
            actions[text] = action
            return action

        menu.addAction.side_effect = add_action
        with mock.patch("bandscope.app.path_slice_integration.QMenu", return_value=menu):
            self.assertTrue(self.controller.handle_context_menu(object()))

        clear = actions["清除所有选点"]
        clear.setEnabled.assert_called_once_with(True)
        clear.triggered.connect.call_args.args[0]()

        self.assertEqual(draft_nodes(self.source.params), [])
        self.assertEqual(self.controller._draft_artists, [])
        self.assertEqual(self.controller.active_source_id, "source")

    def test_double_click_renames_without_adding_a_node(self):
        write_draft(self.source.params, [PathNode(0, 0, "P1"), PathNode(2, 0, "P2")])
        self.window.left_workspace.current_spec = lambda: self.source
        self.controller.active_source_id = "source"
        figure = Figure(figsize=(5, 4), dpi=100)
        ax = figure.add_subplot(111)
        ax.set_xlim(-1, 4)
        ax.set_ylim(-1, 5)
        figure.canvas.draw()
        self.window.ax_2d = ax
        self.window.canvas_2d = SimpleNamespace(draw_idle=lambda: None)
        x, y = ax.transData.transform((0, 0))
        event = SimpleNamespace(
            button=1, dblclick=True, inaxes=ax, x=x, y=y, xdata=0.0, ydata=0.0,
        )

        class Dialog:
            Accepted = 1
            def __init__(self, *_args):
                pass
            def exec_(self):
                return self.Accepted
            def node_name(self):
                return "Γ"
            def deleteLater(self):
                pass

        single = SimpleNamespace(
            button=1, dblclick=False, inaxes=ax, x=x, y=y, xdata=0.0, ydata=0.0,
        )
        self.controller.on_canvas_click(single)
        self.assertEqual(len(self.source.params["_path_draft"]), 2)
        with mock.patch("bandscope.app.path_slice_integration.PathNodeRenameDialog", Dialog):
            self.controller.on_canvas_double_click(event)
        self.assertEqual(len(self.source.params["_path_draft"]), 2)
        self.assertEqual(self.source.params["_path_draft"][0]["label"], "Γ")
        self.controller.on_canvas_click(single)
        self.controller._commit_pending_single()
        self.assertEqual(len(self.source.params["_path_draft"]), 3)
        self.assertEqual(self.source.params["_path_draft"][-1]["x"], 0.0)
        self.assertEqual(self.source.params["_path_draft"][-1]["y"], 0.0)

    def test_slider_change_does_not_flatten_other_multi_value_fields(self):
        spec = self.path_spec()
        self.model.selected = [0, 1]
        self.model.segments[0].gray = 40
        self.model.segments[1].gray = 70
        self.model.segments[0].white = 80
        self.model.segments[1].white = 95
        self.model.write_params(spec.params)
        self.window.page_render = SimpleNamespace(
            s_low=_Slider(12), s_gamma=_Slider(50), s_up=_Slider(100),
            combo_map=_Combo("线性"), btn_apply_map=object(),
        )
        self.window.sender = lambda: self.window.page_render.s_low
        self.window.request_refresh = lambda *args, **kwargs: None
        self.controller.handle_levels_schedule()
        self.assertEqual(
            [segment["black"] for segment in spec.params["path_segments"]], [12, 12]
        )
        self.assertEqual(
            [segment["gray"] for segment in spec.params["path_segments"]], [40, 70]
        )
        self.assertEqual(
            [segment["white"] for segment in spec.params["path_segments"]], [80, 95]
        )

    def test_only_changed_display_fields_are_written_to_selected_segments(self):
        spec = self.path_spec()
        self.model.selected = [0, 1]
        self.model.segments[1].intensity_map = "对数"
        self.model.write_params(spec.params)
        self.window.page_render = SimpleNamespace(
            s_low=_Slider(10), s_gamma=_Slider(45), s_up=_Slider(90),
            combo_map=_Combo("幂函数"),
        )
        requests = []
        self.window.request_refresh = lambda *args, **kwargs: requests.append((args, kwargs))
        self.controller._write_levels(spec, include_map=True)
        self.assertEqual(spec.params["path_selected_segments"], [0, 1])
        for segment in spec.params["path_segments"]:
            self.assertEqual(segment["black"], 10)
            self.assertEqual(segment["gray"], 45)
            self.assertEqual(segment["white"], 90)
            self.assertEqual(segment["intensity_map"], "幂函数")
        self.assertEqual(len(requests), 1)


class PathSliceRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_path_page_can_be_reopened_after_returning_to_energy_map(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "input.npz"
            synthetic_npz(source_path, descending=True)
            window = control_window()
            self.addCleanup(window.close)
            window.load_data(str(source_path))
            axis_spec = window._build_canvas_axis_slice_spec(window.left_workspace.current_spec(), 2)
            window.left_workspace.add_page(axis_spec)
            source = window.left_workspace.current_spec()
            write_draft(source.params, [
                PathNode(1, 1, "Γ"), PathNode(3, 1, "K"), PathNode(3, 3, "M"),
            ])
            window.path_slice_controller.button.click()
            window.path_slice_controller.redraw_draft()
            window.global_refresh = lambda: window._render_active_page()
            page_id = window.path_slice_controller.generate_page()

            for _ in range(2):
                window.left_workspace.activate_page(source.page_id)
                self.assertEqual(window.current_render_context["view"], "2d")
                window.left_workspace.activate_page(page_id)
                self.assertEqual(window.current_render_context["view"], "path_slice")
                self.assertTrue(all(
                    artist.axes is window.ax_2d
                    for artist in window.ax_2d._path_slice_artists
                ))

    def test_path_page_publication_and_recipe_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "input.npz"
            synthetic_npz(source_path, descending=True)
            window = control_window()
            self.addCleanup(window.close)
            window.load_data(str(source_path))
            axis_spec = window._build_canvas_axis_slice_spec(window.left_workspace.current_spec(), 2)
            window.left_workspace.add_page(axis_spec)
            source = window.left_workspace.current_spec()
            self.assertEqual(source.params["axis_index"], 2)
            write_draft(source.params, [
                PathNode(1, 1, "Γ"), PathNode(3, 1, "K"), PathNode(3, 3, "M"),
            ])
            window.path_slice_controller.button.click()
            window.path_slice_controller.redraw_draft()
            self.assertTrue(window.path_slice_controller._draft_artists)
            page_id = window.path_slice_controller.generate_page()
            self.assertIsNotNone(page_id)
            self.assertFalse(window.path_slice_controller._draft_artists)
            self.assertEqual(window.active_page_spec.page_kind, "path_slice")
            before = window._compute_render_context(window.active_page_spec)
            snapshot = capture_snapshot(window)
            self.assertIsNotNone(snapshot.view_recipe, snapshot.recipe_error)
            self.assertEqual(snapshot.payload["extent"][:2], [0.0, float(before["k"][-1])])
            recipe_path = Path(directory) / "path.bsview.json"
            recipe_path.write_text(ViewRecipeDocument(snapshot.view_recipe).dumps(), encoding="utf-8")

            self.assertTrue(window._load_view_recipe(
                recipe_path, confirm=False, relocate=lambda _: str(source_path)
            ))
            restored = window.active_page_spec
            self.assertEqual(restored.page_kind, "path_slice")
            after = window._compute_render_context(restored)
            np.testing.assert_allclose(
                after["raw_intensity"], before["raw_intensity"], equal_nan=True,
            )
            self.assertEqual(
                [node["label"] for node in restored.params["path_nodes"]], ["Γ", "K", "M"]
            )


if __name__ == "__main__":
    unittest.main()
