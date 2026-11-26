import os
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from matplotlib.backend_bases import MouseEvent
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication, QPushButton, QWidget

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.app.enhancement_integration import EnhancementInteractionMixin
from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.crop_model import CropSelection
from bandscope.ui.crop_controls import CropController
from bandscope.ui.result_workspace import AnalysisPageSpec


def _image_context():
    return {
        "view": "2d",
        "data": np.ones((11, 11)),
        "slice_info": {"axis": 2},
        "coords": {"X": np.arange(11.0), "Y": np.arange(11.0), "E": np.arange(11.0)},
    }


def _volume_context():
    data = np.arange(5 * 6 * 7, dtype=np.float32).reshape(5, 6, 7)
    return {
        "view": "3d",
        "data": data,
        "coords": {key: np.arange(size, dtype=float) for key, size in zip(("X", "Y", "E"), data.shape)},
    }


def _curve(curve_id, x_data, y_data):
    return {
        "curve_kind": "edc_curve",
        "curve_id": curve_id,
        "title": curve_id,
        "source_title": "source",
        "label": curve_id,
        "xlabel": "Energy (eV)",
        "x_data": list(x_data),
        "y_data": list(y_data),
    }


class MultiRegion2DTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = QWidget()
        self.host = My3DAnalyzer.__new__(My3DAnalyzer)
        self.host.canvas_2d = FigureCanvasAgg(Figure(figsize=(6, 5)))
        self.host.ax_2d = self.host.canvas_2d.figure.add_subplot()
        self.host.ax_2d.set(xlim=(0, 10), ylim=(0, 10))
        self.host.axis_crop_selector = None
        self.host.axis_crop_selectors = {}
        self.host.axis_crop_overlay = None
        self.host.left_display_stack = SimpleNamespace(currentIndex=lambda: 1)
        self.spec = SimpleNamespace(page_id="image", page_kind="home")
        self.host.left_workspace = SimpleNamespace(current_spec=lambda: self.spec)
        self.host.current_render_context = _image_context()
        self.host.plotter = SimpleNamespace(interactor=SimpleNamespace(), render=Mock())
        self.controller = self.host.crop_controller = CropController(self.parent)
        self.controller.enabled = True
        self.controller.activate("image", self.host.current_render_context)
        self.controller.selection_changed.connect(lambda: self.host._on_crop_selection_changed())
        self.host.plot_coordinate_tooltip = SimpleNamespace(annotation=None, _background=None)

    def tearDown(self):
        self.host._clear_axis_crop_interaction()
        self.controller.clear()
        self.parent.close()
        self.parent.deleteLater()

    def refresh(self):
        self.host._refresh_axis_crop_interaction(self.spec, self.host.current_render_context)
        self.host.canvas_2d.draw()

    def event(self, name, xy):
        x, y = self.host.ax_2d.transData.transform(xy)
        return MouseEvent(name, self.host.canvas_2d, x, y, button=1)

    def drag(self, start, end):
        for name, xy in (("button_press_event", start),
                         ("motion_notify_event", end),
                         ("button_release_event", end)):
            self.host.canvas_2d.callbacks.process(name, self.event(name, xy))

    def right_click(self, xy):
        x, y = self.host.ax_2d.transData.transform(xy)
        local = QPoint(round(x), round(self.host.canvas_2d.figure.bbox.height - y))
        event = QMouseEvent(
            QEvent.MouseButtonPress, local, QPoint(100, 100),
            Qt.RightButton, Qt.RightButton, Qt.NoModifier,
        )
        return self.host._crop_event_filter(self.host.canvas_2d, event)

    def test_multiple_selectors_activate_and_update_only_their_own_id(self):
        first = self.controller.active_selection_id
        second = self.controller.add_selection()
        self.refresh()

        self.assertEqual(set(self.host.axis_crop_selectors), {first, second})
        first_selector = self.host.axis_crop_selectors[first]
        second_selection = self.controller.selection
        self.drag((1, 1), (1, 1))
        self.assertEqual(self.controller.active_selection_id, first)
        self.assertIs(self.host.axis_crop_selector, first_selector)

        press = SimpleNamespace(xdata=0.0, ydata=1.0)
        release = SimpleNamespace(xdata=4.0, ydata=5.0)
        self.host._on_axis_crop_selected(press, release, selection_id=first)
        drafts = dict(self.controller.selections_for_page())
        self.assertEqual(drafts[first].bounds, (0, 4, 1, 5))
        self.assertEqual(drafts[second], second_selection)
        self.assertEqual(self.controller.active_selection_id, first)

        self.controller.enabled = False
        self.refresh()
        self.assertEqual(self.host.axis_crop_selectors, {})
        self.assertEqual(len(self.controller.selections_for_page()), 2)
        self.controller.enabled = True
        self.refresh()
        self.assertEqual(self.host.axis_crop_selectors[first].extents, (0, 4, 1, 5))

        old = dict(self.host.axis_crop_selectors)
        self.host.ax_2d.clear()
        self.host.ax_2d.set(xlim=(0, 10), ylim=(0, 10))
        self.refresh()
        self.assertEqual(len(self.host.axis_crop_selectors), 2)
        self.assertTrue(all(not selector.active for selector in old.values()))

    def test_nested_canvas_gesture_has_one_target_and_survives_axes_rebuild(self):
        first = self.controller.active_selection_id
        original_first = self.controller.selection
        second = self.controller.add_selection()
        self.controller.update_selection(
            second, replace(self.controller.selection, bounds=(3, 7, 3, 7)), notify=False
        )
        self.refresh()

        self.drag((5, 5), (6, 6))

        drafts = dict(self.controller.selections_for_page())
        self.assertEqual(drafts[first], original_first)
        np.testing.assert_allclose(drafts[second].bounds, (4, 8, 4, 8))
        self.assertEqual(self.controller.active_selection_id, second)
        self.assertIs(self.host.axis_crop_selector, self.host.axis_crop_selectors[second])

        self.drag((2, 2), (1, 1))

        drafts = dict(self.controller.selections_for_page())
        np.testing.assert_allclose(drafts[first].bounds, (0, 8, 0, 8))
        np.testing.assert_allclose(drafts[second].bounds, (4, 8, 4, 8))
        self.assertEqual(self.controller.active_selection_id, first)

        old = dict(self.host.axis_crop_selectors)
        self.host.ax_2d.clear()
        self.host.ax_2d.set(xlim=(0, 10), ylim=(0, 10))
        self.refresh()
        self.assertTrue(all(not selector.active for selector in old.values()))

        self.drag((6, 6), (7, 7))

        drafts = dict(self.controller.selections_for_page())
        np.testing.assert_allclose(drafts[first].bounds, (0, 8, 0, 8))
        np.testing.assert_allclose(drafts[second].bounds, (5, 9, 5, 9))
        self.assertEqual(self.controller.active_selection_id, second)
        self.assertIs(self.host.axis_crop_selector, self.host.axis_crop_selectors[second])

    def test_selector_labels_are_stable_across_refresh_and_axes_rebuild(self):
        first = self.controller.active_selection_id
        second = self.controller.add_selection()
        self.refresh()
        expected = {first: f"Z{first}", second: f"Z{second}"}
        self.assertEqual({
            selection_id: selector._label_artist.get_text()
            for selection_id, selector in self.host.axis_crop_selectors.items()
        }, expected)

        self.controller.activate_selection(first, notify=False)
        self.controller.set_selection(
            replace(self.controller.selection, bounds=(0, 5, 2, 6)), notify=False
        )
        self.refresh()
        self.assertEqual(
            self.host.axis_crop_selectors[first]._label_artist.get_position(), (0, 6)
        )
        self.host.ax_2d.clear()
        self.host.ax_2d.set(xlim=(0, 10), ylim=(0, 10))
        self.refresh()
        self.assertEqual({
            selection_id: selector._label_artist.get_text()
            for selection_id, selector in self.host.axis_crop_selectors.items()
        }, expected)

    def test_right_click_handle_opens_and_removes_that_selection(self):
        first = self.controller.active_selection_id
        second = self.controller.add_selection()
        second_selection = self.controller.selection
        self.refresh()
        self.host._show_axis_title_menu = Mock()
        applications = Mock()
        self.controller.apply_requested.connect(applications)

        self.assertTrue(self.right_click((1, 1)))
        QApplication.processEvents()

        self.assertEqual(self.controller.active_selection_id, first)
        self.assertTrue(self.controller.popup.isVisible())
        self.assertEqual(self.controller.popup.title_label.text(), f"裁剪范围 Z{first}")
        self.host._show_axis_title_menu.assert_not_called()
        self.controller.popup.remove_button.click()

        self.assertEqual(dict(self.controller.selections_for_page()), {second: second_selection})
        self.assertEqual(self.controller.active_selection_id, second)
        self.assertEqual(set(self.host.axis_crop_selectors), {second})
        self.assertEqual(self.controller.popup.title_label.text(), f"裁剪范围 Z{second}")
        self.assertFalse(self.controller.popup.isVisible())
        applications.assert_not_called()

    def test_right_click_away_from_handles_keeps_generic_popup_and_axis_menu(self):
        first = self.controller.active_selection_id
        second = self.controller.add_selection()
        self.refresh()
        self.host._show_axis_title_menu = Mock()

        self.assertTrue(self.right_click((0.2, 0.2)))
        QApplication.processEvents()

        self.assertNotEqual(first, second)
        self.assertEqual(self.controller.active_selection_id, second)
        self.assertEqual(self.controller.popup.title_label.text(), f"裁剪范围 Z{second}")
        self.host._show_axis_title_menu.assert_called_once()

    def test_removing_last_selection_clears_selector(self):
        self.refresh()
        self.assertTrue(self.host.axis_crop_selectors)

        self.assertTrue(self.controller.remove_selection(self.controller.active_selection_id))

        self.assertEqual(self.host.axis_crop_selectors, {})
        self.assertIsNone(self.host.axis_crop_selector)
        self.host.plotter.render.assert_called_once()

    def test_all_selectors_share_blit_and_background(self):
        self.controller.add_selection()
        self.refresh()
        selectors = list(self.host.axis_crop_selectors.values())
        press = self.event("button_press_event", (5, 5))
        self.host.canvas_2d.callbacks.process("button_press_event", press)
        move = self.event("motion_notify_event", (6, 6))
        self.host.canvas_2d.callbacks.process("motion_notify_event", move)

        self.assertTrue(self.host._axis_crop_selector_will_blit(move))
        artists = self.host._active_2d_blit_artists()
        for selector in selectors:
            self.assertTrue(all(artist in artists for artist in selector.artists))
        background = object()
        self.host.ax_2d._arpes_preview_background = background
        self.host._sync_active_2d_blit_background()
        self.assertTrue(all(selector.background is background for selector in selectors))
        release = self.event("button_release_event", (6, 6))
        self.host.canvas_2d.callbacks.process("button_release_event", release)

    def test_crop_applies_only_the_active_selection(self):
        first = self.controller.active_selection_id
        second = self.controller.add_selection()
        active = self.controller.selection
        source = AnalysisPageSpec(
            "axis", "XY", "axis_integral", "data_process",
            params={"axis_index": 2, "low": 1, "up": 2, "mid": 1},
        )
        added = []
        self.host.left_workspace = SimpleNamespace(
            current_spec=lambda: source, add_page=added.append
        )
        self.host._render_exact_ready = True
        self.host._compute_render_context = lambda _spec: _image_context()
        self.host._persist_active_page_state = Mock()
        self.host._make_unique_page_title = lambda title: title
        self.host._make_page_id = lambda: "child"
        self.host.rotation_angle = 0.0

        self.host.on_cut()

        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].params["crop_regions"], [active.to_dict()])
        self.assertNotEqual(active, dict(self.controller.selections_for_page())[first])
        self.assertEqual(self.controller.active_selection_id, second)


class SelectionModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_enhancement_entry_closes_master_selection_mode(self):
        host = My3DAnalyzer.__new__(My3DAnalyzer)
        host.canvas_tool_overlay = SimpleNamespace(close_selection_panel=Mock())
        with patch.object(
            EnhancementInteractionMixin, "_add_enhancement_region", return_value="created"
        ) as add_region:
            self.assertEqual(host._add_enhancement_region(), "created")
        host.canvas_tool_overlay.close_selection_panel.assert_called_once_with()
        add_region.assert_called_once_with()

    def test_secondary_toggle_does_not_close_master_mode_and_collapse_keeps_drafts(self):
        parent = QWidget()
        controller = CropController(parent)
        controller.activate("image", _image_context())
        draft = controller.selection
        host = My3DAnalyzer.__new__(My3DAnalyzer)
        host.crop_controller = controller
        host.btn_tb_crop = QPushButton()
        host.btn_tb_erase = QPushButton()
        for button in (host.btn_tb_crop, host.btn_tb_erase):
            button.setCheckable(True)
        host.canvas_tool_overlay = SimpleNamespace(selection_button=QPushButton())
        host.canvas_tool_overlay.selection_button.setCheckable(True)
        host.path_slice_controller = Mock()
        host._clear_enhancement_interaction = Mock()
        host._can_show_interactive_box = lambda: False
        host._restore_volume_opacity_if_dimmed = Mock()
        host._clear_interactive_box = Mock()
        host._refresh_axis_crop_interaction = Mock()
        host.left_workspace = SimpleNamespace(current_spec=lambda: None)
        host.current_render_context = None
        host._update_crop_cursor = Mock()
        host.plotter = SimpleNamespace(render=Mock())
        host.canvas_tool_overlay.selection_button.toggled.connect(
            lambda checked: host.on_selection_toggled(checked)
        )
        host.btn_tb_crop.toggled.connect(lambda checked: host.on_toggle_interactive_box(checked))
        host.btn_tb_erase.toggled.connect(lambda checked: host.on_toggle_erase(checked))

        host.canvas_tool_overlay.selection_button.click()
        self.assertTrue(controller.enabled)
        host.btn_tb_crop.click()
        self.assertTrue(controller.popup.isVisible())
        host.btn_tb_crop.click()
        self.assertTrue(controller.enabled)
        self.assertTrue(host.canvas_tool_overlay.selection_button.isChecked())
        self.assertFalse(controller.popup.isVisible())

        host.canvas_tool_overlay.selection_button.click()
        self.assertFalse(controller.enabled)
        self.assertFalse(host.btn_tb_crop.isChecked())
        self.assertFalse(host.btn_tb_erase.isChecked())
        self.assertEqual(controller.selection, draft)
        self.assertEqual(len(controller.selections_for_page()), 1)
        controller.clear()
        parent.close()
        parent.deleteLater()


class _FakeBoxWidget:
    def __init__(self, callback, bounds):
        self.callback = callback
        self.bounds = tuple(bounds)
        self.observers = {}
        self.enabled = True

    def PlaceWidget(self, bounds):
        self.bounds = tuple(bounds)
        self.callback(SimpleNamespace(bounds=self.bounds))

    def invoke(self, bounds):
        self.bounds = tuple(bounds)
        self.callback(SimpleNamespace(bounds=self.bounds))

    def AddObserver(self, event, callback):
        self.observers[event] = callback

    def Off(self):
        self.enabled = False


class _FakePlotter:
    def __init__(self):
        self.box_widgets = []
        self.render = Mock()
        self.clear_box_widgets = Mock(side_effect=self._clear)

    def _clear(self):
        for widget in self.box_widgets:
            widget.Off()
        self.box_widgets.clear()

    def add_box_widget(self, callback, bounds, **_kwargs):
        widget = _FakeBoxWidget(callback, bounds)
        widget.PlaceWidget(bounds)
        self.box_widgets.append(widget)
        return widget


class MultiRegion3DTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = QWidget()
        self.context = _volume_context()
        self.core = AnalyzerCore()
        self.core.raw_data = self.context["data"]
        self.core.coords = self.context["coords"]
        self.host = My3DAnalyzer.__new__(My3DAnalyzer)
        self.host.core = self.core
        self.host._full_domain_spatial_shape = lambda: self.core.raw_data.shape
        self.host.crop_controller = CropController(self.parent)
        self.host.crop_controller.enabled = True
        self.host.crop_controller.activate("home", self.context)
        self.host.current_render_context = self.context
        self.host.timeline_bar = SimpleNamespace(
            switch_flip=SimpleNamespace(isChecked=lambda: False)
        )
        self.host.plotter = _FakePlotter()
        self.host._can_show_interactive_box = lambda: True
        self.host._restore_volume_opacity_if_dimmed = Mock()
        self.host._crop_box_widgets = {}
        self.host._crop_box_widget_ids = {}
        self.host._crop_box_signatures = {}
        self.host._crop_box_syncing_ids = set()
        self.host._crop_box_widget = None
        self.host._box_interacting = False
        self.host.crop_controller.selection_changed.connect(lambda: self.host._on_crop_selection_changed())

    def tearDown(self):
        self.host.crop_controller.clear()
        self.parent.close()
        self.parent.deleteLater()

    def test_multiple_boxes_bind_ids_and_only_active_box_syncs_controls(self):
        first = self.host.crop_controller.active_selection_id
        original_first = self.host.crop_controller.selection
        second = self.host.crop_controller.add_selection()
        original_second = self.host.crop_controller.selection
        self.host._rebuild_interactive_box()

        self.assertEqual(set(self.host._crop_box_widgets), {first, second})
        self.assertEqual(dict(self.host.crop_controller.selections_for_page())[first], original_first)
        self.assertEqual(dict(self.host.crop_controller.selections_for_page())[second], original_second)
        first_logical = (0, 2, 1, 4, 2, 5)
        first_render = self.core.logical_to_render_bounds(first_logical, self.core.raw_data.shape, clamp=False)
        self.host._crop_box_widgets[first].invoke(first_render)

        drafts = dict(self.host.crop_controller.selections_for_page())
        self.assertEqual(self.host.crop_controller.active_selection_id, first)
        np.testing.assert_allclose(self.host._crop_logical_bounds(drafts[first]), first_logical)
        self.assertEqual(drafts[second], original_second)
        np.testing.assert_allclose(self.host.precise_logical_bounds, first_logical)
        self.assertIs(self.host._crop_box_widget, self.host._crop_box_widgets[first])

        second_logical = (1, 3, 0, 2, 1, 4)
        second_render = self.core.logical_to_render_bounds(second_logical, self.core.raw_data.shape, clamp=False)
        self.host._crop_box_widgets[second].invoke(second_render)
        drafts = dict(self.host.crop_controller.selections_for_page())
        np.testing.assert_allclose(self.host._crop_logical_bounds(drafts[second]), second_logical)
        np.testing.assert_allclose(self.host._crop_logical_bounds(drafts[first]), first_logical)
        np.testing.assert_allclose(self.host.precise_logical_bounds, second_logical)

    def test_remove_selection_updates_3d_widgets_including_the_last_box(self):
        controller = self.host.crop_controller
        first = controller.active_selection_id
        second = controller.add_selection()
        self.host._rebuild_interactive_box()

        self.assertTrue(controller.remove_selection(second))
        self.assertEqual(controller.active_selection_id, first)
        self.assertEqual(set(self.host._crop_box_widgets), {first})
        self.assertTrue(controller.remove_selection(first))
        self.assertIsNone(controller.selection)
        self.assertEqual(self.host._crop_box_widgets, {})
        self.assertIsNone(self.host._crop_box_widget)
        self.host._restore_volume_opacity_if_dimmed.assert_called()

    def test_clear_removes_all_crop_boxes_but_preserves_enhancement_box(self):
        self.host.crop_controller.add_selection()
        self.host._rebuild_interactive_box()
        enhancement = _FakeBoxWidget(lambda _poly: None, (0, 1, 0, 1, 0, 1))
        self.host._enhancement_box_widget = enhancement
        self.host.plotter.box_widgets.append(enhancement)

        self.host._clear_interactive_box()

        self.assertEqual(self.host._crop_box_widgets, {})
        self.assertIn(enhancement, self.host.plotter.box_widgets)
        self.assertTrue(enhancement.enabled)
        self.assertTrue(all(not widget.enabled for widget in self.host.plotter.box_widgets[:-1]))
        self.host.plotter.clear_box_widgets.assert_not_called()


class EdcSnapshotAndExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_per_curve_comparison_and_log_keep_distinct_energy_axes(self):
        base = _curve("a", [0, 1], [2, 3])
        overlay = _curve("b", [10, 20, 30], [4, 5, 6])
        spec = AnalysisPageSpec(
            "cmp", "EDC", My3DAnalyzer.COMPARISON_PAGE_KIND, "data_process",
            params={
                "comparison_kind": "edc_curve",
                "x_match_policy": "per_curve",
                "base_curve": base,
                "overlay_curves": [overlay],
            },
        )
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        context = analyzer._get_curve_comparison_context(spec)
        self.assertEqual(len(context["curves"]), 2)
        spec.params["x_match_policy"] = "shared"
        self.assertEqual(len(analyzer._get_curve_comparison_context(spec)["curves"]), 1)

        log_spec = AnalysisPageSpec(
            "log", "log", My3DAnalyzer.LOG_1D_PAGE_KIND, "data_process",
            params={
                "x_match_policy": "per_curve",
                "base_curve": base,
                "overlay_curves": [overlay],
            },
        )
        log_context = analyzer._get_log_curve_context(log_spec)
        self.assertEqual(len(log_context["curves"]), 2)
        self.assertEqual(log_context["x_match_policy"], "per_curve")

    def test_snapshot_single_page_and_per_curve_comparison_export_without_source_data(self):
        base = _curve("a", [0, 1], [2, 3])
        overlay = _curve("b", [10, 20, 30], [4, 5, 6])
        metadata = [
            {"curve_id": "a", "kx_range": (-1, 1), "source_t_index": 2},
            {"curve_id": "b", "energy_range": (10, 30)},
        ]
        single = AnalysisPageSpec(
            "single", "single", "edc_curve", "data_process",
            params={"base_curve": base, "curve_metadata": metadata[:1]},
        )
        comparison = AnalysisPageSpec(
            "cmp", "EDC comparison", My3DAnalyzer.COMPARISON_PAGE_KIND, "data_process",
            params={
                "comparison_kind": "edc_curve",
                "x_match_policy": "per_curve",
                "base_curve": base,
                "overlay_curves": [overlay],
                "curve_metadata": metadata,
            },
        )
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        context = analyzer._compute_base_render_context(single)
        np.testing.assert_allclose(context["x_data"], [0, 1])
        _, _, single_data = analyzer._build_edc_export_payload(single, None, {})
        np.testing.assert_allclose(single_data["intensity"], [2, 3])
        np.testing.assert_allclose(single_data["kx_range"], [-1, 1])

        analyzer.left_workspace = SimpleNamespace(current_spec=lambda: comparison)
        analyzer.core = SimpleNamespace(raw_data=None, coords={})
        analyzer.original_raw_data = None
        analyzer.base_raw_data = None
        analyzer._render_exact_ready = True
        analyzer.btn_tb_export = QPushButton()
        analyzer.btn_tb_shot = QPushButton()
        analyzer.btn_tb_crop = QPushButton()
        analyzer.btn_tb_erase = QPushButton()
        analyzer.canvas_tool_overlay = SimpleNamespace(selection_button=QPushButton())
        analyzer._scope_export_metadata = lambda _descriptor, _coords: {}
        analyzer._toast_success = Mock()
        analyzer._update_export_button_states()
        self.assertTrue(analyzer.btn_tb_export.isEnabled())

        with tempfile.TemporaryDirectory() as tmp:
            single_path = os.path.join(tmp, "single_edc.npz")
            analyzer.left_workspace.current_spec = lambda: single
            analyzer._choose_export_path = lambda _title, _name: single_path
            analyzer.export_current_result()
            with np.load(single_path) as exported:
                np.testing.assert_allclose(exported["E"], [0, 1])
                np.testing.assert_allclose(exported["intensity"], [2, 3])

            path = os.path.join(tmp, "edc.npz")
            analyzer.left_workspace.current_spec = lambda: comparison
            analyzer._choose_export_path = lambda _title, _name: path
            analyzer.export_current_result()
            self.assertFalse(analyzer._export_allow_tabular)
            with np.load(path) as exported:
                np.testing.assert_allclose(exported["E_1"], [0, 1])
                np.testing.assert_allclose(exported["intensity_2"], [4, 5, 6])
                np.testing.assert_allclose(exported["energy_range_2"], [10, 30])

        comparison.params["x_match_policy"] = "shared"
        analyzer._update_export_button_states()
        self.assertFalse(analyzer.btn_tb_export.isEnabled())


if __name__ == "__main__":
    unittest.main()
