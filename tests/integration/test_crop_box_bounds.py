"""Unbounded 3D selection drafts survive widget callbacks and page refreshes."""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pyvista as pv
import vtk
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.crop_model import apply_selection
from bandscope.rendering.refresh_pipeline import RenderQuality
from bandscope.ui.crop_controls import CropController
from tests.support.environment import requires_opengl


class CropBoxBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = QWidget()
        self.controller = CropController(self.parent)
        self.core = AnalyzerCore()
        self.core.coords = {"X": np.array([-2., -1., 1., 4.]),
                            "Y": np.arange(5.), "E": np.array([-3., -2., -.5, 0., .2, 1.])}
        self.core.raw_data = np.arange(120.).reshape(4, 5, 6)
        self.context = dict(view="3d", data=self.core.raw_data[1:3, 1:4, 1:5],
                            coords=self.core.coords, data_bounds=(1, 2, 1, 3, 1, 4), full_shape=(4, 5, 6))
        self.analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        self.analyzer.core = self.core
        self.analyzer._full_domain_spatial_shape = lambda: (4, 5, 6)
        self.analyzer.crop_controller = self.controller
        self.analyzer.current_render_context = self.context
        self.analyzer._box_interacting = False
        self.analyzer._orig_volume_opacity = None
        self.flip = SimpleNamespace(checked=False)
        self.flip.isChecked = lambda: self.flip.checked
        self.analyzer.timeline_bar = SimpleNamespace(switch_flip=self.flip)
        self.controller.activate("roi", self.context)

    def tearDown(self):
        self.parent.close()
        self.parent.deleteLater()

    def drag(self, bounds):
        render = self.core.logical_to_render_bounds(bounds, (4, 5, 6), clamp=False)
        self.analyzer._sync_slice_edits_from_render_bounds(render)
        return render

    def test_drag_outside_current_roi_survives_refresh(self):
        bounds = (0, 3, 0, 4, 0, 5)
        render = self.drag(bounds)
        self.controller.activate("roi", self.context)
        np.testing.assert_allclose(self.analyzer._get_render_bounds_for_box(), render)
        np.testing.assert_allclose(self.analyzer._crop_logical_bounds(), bounds)
        result = apply_selection(self.context, self.controller.selection)
        np.testing.assert_array_equal(result["data"], self.context["data"])
        self.assertEqual(result["data_bounds"], self.context["data_bounds"])

    def test_drag_beyond_full_domain_then_back_for_both_energy_directions_and_flips(self):
        original = self.core.coords["E"].copy()
        for energy in (original, original[::-1]):
            self.core.coords["E"] = energy
            for flipped in (False, True):
                with self.subTest(descending=energy[0] > energy[-1], flipped=flipped):
                    self.flip.checked = flipped
                    outside = (-1, 4, -2, 6, -1, 6)
                    render = self.drag(outside)
                    np.testing.assert_allclose(self.analyzer._crop_logical_bounds(), outside)
                    np.testing.assert_allclose(self.analyzer._get_render_bounds_for_box(), render)
                    self.controller.activate("roi", self.context, e_flip=flipped)
                    np.testing.assert_allclose(self.analyzer._get_render_bounds_for_box(), render)
                    inside = (1.2, 1.8, 1.5, 2.5, 2, 3)
                    self.drag(inside)
                    np.testing.assert_allclose(self.analyzer._crop_logical_bounds(), inside)

    def test_numeric_edits_outside_domain_survive_initial_widget_callback(self):
        physical = (-3, 7, -2, 6, -4, 1.8)
        for edit, value in zip(self.controller.popup.edits, physical):
            edit.setText(str(value))
        self.assertTrue(self.controller.commit_edits())

        def add_box_widget(callback, bounds, **kwargs):
            widget = vtk.vtkBoxWidget()
            widget.SetPlaceFactor(kwargs["factor"])
            widget.PlaceWidget(bounds)
            poly = vtk.vtkPolyData()
            widget.GetPolyData(poly)
            callback(SimpleNamespace(bounds=poly.GetBounds()))
            return widget

        self.analyzer._can_show_interactive_box = lambda: True
        self.analyzer._box_bounds_signature = None
        self.analyzer._restore_volume_opacity_if_dimmed = Mock()
        self.analyzer._dim_current_volume = Mock()
        self.analyzer.plotter = SimpleNamespace(clear_box_widgets=Mock(), add_box_widget=add_box_widget)
        self.analyzer._rebuild_interactive_box()
        np.testing.assert_allclose(self.controller.selection.bounds, physical)
        self.analyzer._rebuild_interactive_box()
        self.analyzer.plotter.clear_box_widgets.assert_not_called()
        self.assertEqual(len(self.analyzer._crop_box_widgets), 1)

    @requires_opengl
    def test_real_vtk_boxes_have_visible_overlay_props_and_native_activation(self):
        plotter = pv.Plotter(off_screen=True, window_size=(320, 240))
        plotter.add_volume(self.core.raw_data, cmap="magma")
        self.addCleanup(plotter.close)
        self.analyzer.plotter = plotter
        self.analyzer._can_show_interactive_box = lambda: True
        self.analyzer._dim_current_volume = Mock()
        self.analyzer._box_interacting = False
        first = self.controller.active_selection_id
        second = self.controller.add_selection()

        self.analyzer._rebuild_interactive_box()

        widgets = list(plotter.box_widgets)
        self.assertEqual(len(widgets), 2)
        self.assertTrue(all(widget.GetEnabled() for widget in widgets))
        overlay = self.analyzer._crop_box_renderer
        self.assertIsNotNone(overlay)
        self.assertTrue(overlay.GetInteractive())
        self.assertEqual(overlay.GetActiveCamera(), plotter.renderer.GetActiveCamera())
        props = overlay.GetViewProps()
        props.InitTraversal()
        visible_bounds = []
        for _ in range(props.GetNumberOfItems()):
            prop = props.GetNextProp()
            if prop.GetVisibility() and prop.GetBounds() is not None:
                visible_bounds.append(tuple(prop.GetBounds()))
        for widget in widgets:
            poly = vtk.vtkPolyData()
            widget.GetPolyData(poly)
            self.assertTrue(any(
                np.allclose(poly.GetBounds(), bounds, rtol=1e-7, atol=1e-7)
                for bounds in visible_bounds
            ))

        for selection_id, widget in zip((first, second), widgets):
            widget.InvokeEvent("InteractionEvent")
            self.assertEqual(self.controller.active_selection_id, selection_id)
            widget.InvokeEvent("EndInteractionEvent")

    def test_page_switch_and_energy_flip_preserve_outside_geometry(self):
        bounds = (-1, 4, -2, 6, -1, 6)
        render = self.drag(bounds)
        for flipped in (True, False, True):
            self.controller.activate("other", self.context)
            self.flip.checked = flipped
            self.controller.activate("roi", self.context, e_flip=flipped)
            np.testing.assert_allclose(self.analyzer._get_render_bounds_for_box(), render)

    def test_flip_handler_keeps_box_instead_of_replacing_it_with_axis_slab(self):
        render = self.drag((-1, 4, -2, 6, -1, 6))
        self.analyzer.base_raw_data = self.core.raw_data
        self.analyzer._refresh_core_display_state = Mock()
        self.analyzer.update_ax_slider_range = Mock()
        self.analyzer.sync_ax_sliders_to_box = Mock()
        self.analyzer._persist_axis_interval_for_current_page = Mock()
        self.analyzer.global_refresh = Mock()
        for flipped in (True, False):
            self.flip.checked = flipped
            self.analyzer.on_toggle_e_flip(flipped)
            self.assertEqual(self.controller.selection.e_flip, flipped)
            np.testing.assert_allclose(self.analyzer._get_render_bounds_for_box(), render)
        self.analyzer.sync_ax_sliders_to_box.assert_not_called()

    def test_fully_outside_crop_is_empty_and_erase_keeps_data(self):
        self.drag((-3, -1, 0, 4, 0, 5))
        selection = self.controller.selection
        self.assertTrue(apply_selection(self.context, selection)["crop_empty"])
        erased = apply_selection(self.context, replace(selection, operation="erase"))
        np.testing.assert_array_equal(erased["data"], self.context["data"])
        np.testing.assert_allclose(self.analyzer._crop_logical_bounds(), (-3, -1, 0, 4, 0, 5))

    def test_partial_overlap_crops_and_erases_only_available_samples(self):
        for flipped in (False, True):
            self.flip.checked = flipped
            self.drag((-1, 1, -1, 2, -1, 3))
            cropped = apply_selection(self.context, self.controller.selection)
            np.testing.assert_array_equal(cropped["data"], self.context["data"][:1, :2, :3])
            erased = apply_selection(self.context, replace(self.controller.selection, operation="erase"))
            expected = self.context["data"].copy()
            expected[:1, :2, :3] = np.nan
            np.testing.assert_equal(erased["data"], expected)

    def test_volume_redim_guard_accepts_uninitialized_widget_host(self):
        host = My3DAnalyzer.__new__(My3DAnalyzer)
        data = np.ones((2, 2, 2), dtype=np.float32)
        spec = SimpleNamespace(page_id="home", page_kind="home", params={})
        context = {"view": "3d", "data": data, "coords": {}}
        host.left_workspace = SimpleNamespace(current_spec=lambda: spec)
        host.core = SimpleNamespace(raw_data=data, coords={})
        host.current_render_context = None
        host.left_display_stack = Mock()
        host.plot_coordinate_tooltip = Mock()
        host.ax_2d = Mock()
        host.page_render = SimpleNamespace(
            get_display_style=lambda: {"opacity_mode": "linear"},
            get_selected_cmap=lambda: "magma",
        )
        host.timeline_bar = SimpleNamespace(
            switch_axes=SimpleNamespace(isChecked=lambda: False),
            switch_flip=SimpleNamespace(isChecked=lambda: False),
        )
        host.volume_session = Mock()
        host.render_quality = SimpleNamespace()
        host.rotation_angle = 0.0
        host._compute_render_context = lambda _spec: context
        host._get_display_levels = lambda: (0.0, 1.0)
        host._current_transfer_signature = lambda: ()
        host._three_d_axis_titles = lambda _spec: None
        host._enhancement_state = lambda _spec: None
        host._prepare_display_statistics = Mock(return_value=False)
        host._enhancement_display = Mock(return_value=None)
        host._plugin_render_context = lambda ctx, **_kwargs: (ctx, None)
        host._can_show_interactive_box = lambda: False
        host._plugin_session = lambda: None
        host._path_slices = lambda: None
        host._dim_current_volume = Mock()
        for name in (
            "_clear_axis_crop_interaction", "_reset_axis_title_interaction",
            "_activate_crop_context", "_restore_3d_camera_position",
            "_apply_pending_e_flip_camera", "_reset_volume_actor_transform",
            "_capture_3d_camera_position", "_restore_volume_opacity_if_dimmed",
            "_clear_interactive_box", "_refresh_axis_crop_interaction",
            "_sync_enhancement_controls", "_draw_enhancement_regions",
            "_set_camera_view_visible", "_sync_camera_view_controls",
        ):
            setattr(host, name, Mock())

        host._render_active_page(RenderQuality.EXACT)
        host._dim_current_volume.assert_not_called()

        host._orig_volume_opacity = object()
        host._render_active_page(RenderQuality.EXACT)
        host._dim_current_volume.assert_called_once_with()
        self.assertIsNone(host._orig_volume_opacity)

    def test_visible_crop_boxes_keep_only_main_volume_dimmed_until_forced_restore(self):
        def opacity_function(scale):
            function = vtk.vtkPiecewiseFunction()
            function.AddPoint(0.0, scale)
            function.AddPoint(100.0, scale / 2)
            return function

        def opacity_values(volume):
            function = volume.GetProperty().GetScalarOpacity()
            values = []
            for index in range(function.GetSize()):
                node = [0.0, 0.0, 0.0, 0.0]
                function.GetNodeValue(index, node)
                values.append(tuple(node[:2]))
            return values

        main_volume = vtk.vtkVolume()
        main_volume.GetProperty().SetScalarOpacity(opacity_function(1.0))
        enhancement_volume = vtk.vtkVolume()
        enhancement_volume.GetProperty().SetScalarOpacity(opacity_function(0.8))
        renderer = vtk.vtkRenderer()
        renderer.AddVolume(main_volume)
        renderer.AddVolume(enhancement_volume)
        enhancement_widget = SimpleNamespace(Off=Mock())
        box_widgets = [enhancement_widget]

        def add_box_widget(callback, bounds, **kwargs):
            widget = SimpleNamespace(Off=Mock(), PlaceWidget=Mock(), AddObserver=Mock())
            box_widgets.append(widget)
            return widget

        self.analyzer.plotter = SimpleNamespace(
            renderer=renderer,
            box_widgets=box_widgets,
            add_box_widget=add_box_widget,
            clear_box_widgets=Mock(),
        )
        self.analyzer._enhancement_box_widget = enhancement_widget
        self.analyzer._can_show_interactive_box = lambda: True
        self.analyzer._box_bounds_signature = None
        self.controller.add_selection()

        self.analyzer._rebuild_interactive_box()
        self.assertEqual(len(self.analyzer._crop_box_widgets), 2)
        np.testing.assert_allclose(opacity_values(main_volume), [(0.0, 0.15), (100.0, 0.075)])
        np.testing.assert_allclose(opacity_values(enhancement_volume), [(0.0, 0.8), (100.0, 0.4)])

        self.analyzer._rebuild_interactive_box()
        np.testing.assert_allclose(opacity_values(main_volume), [(0.0, 0.15), (100.0, 0.075)])
        self.analyzer._on_box_interaction_start(None, None)
        self.assertTrue(self.analyzer._box_interacting)
        self.analyzer._on_box_interaction_end(None, None)
        self.assertFalse(self.analyzer._box_interacting)
        np.testing.assert_allclose(opacity_values(main_volume), [(0.0, 0.15), (100.0, 0.075)])

        self.analyzer._restore_volume_opacity_if_dimmed()
        self.analyzer._clear_interactive_box()
        np.testing.assert_allclose(opacity_values(main_volume), [(0.0, 1.0), (100.0, 0.5)])
        np.testing.assert_allclose(opacity_values(enhancement_volume), [(0.0, 0.8), (100.0, 0.4)])
        self.assertEqual(box_widgets, [enhancement_widget])
        self.assertIs(self.analyzer._enhancement_box_widget, enhancement_widget)
        self.analyzer.plotter.clear_box_widgets.assert_not_called()

    def test_normal_coordinate_conversions_still_clamp_by_default(self):
        self.assertEqual(self.core.physical_to_logical("X", -3), 0)
        self.assertEqual(self.core.logical_to_physical("X", 4), 4)
        np.testing.assert_allclose(self.core.logical_to_render_bounds((-1, 4, -2, 6, -1, 6), (4, 5, 6)),
                                   (0, 200, 0, 200, 0, 200))
        np.testing.assert_allclose(self.core.render_to_logical_bounds((-50, 250, -50, 250, -50, 250), (4, 5, 6)),
                                   (0, 3, 0, 4, 0, 5))


if __name__ == "__main__":
    unittest.main()
