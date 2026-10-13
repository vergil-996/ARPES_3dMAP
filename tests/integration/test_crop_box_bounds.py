"""Unbounded 3D selection drafts survive widget callbacks and page refreshes."""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import vtk
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.crop_model import apply_selection
from bandscope.ui.crop_controls import CropController


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
        self.analyzer.plotter = SimpleNamespace(clear_box_widgets=Mock(), add_box_widget=add_box_widget)
        self.analyzer._rebuild_interactive_box()
        np.testing.assert_allclose(self.controller.selection.bounds, physical)
        self.analyzer._rebuild_interactive_box()
        self.analyzer.plotter.clear_box_widgets.assert_called_once()

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

    def test_normal_coordinate_conversions_still_clamp_by_default(self):
        self.assertEqual(self.core.physical_to_logical("X", -3), 0)
        self.assertEqual(self.core.logical_to_physical("X", 4), 4)
        np.testing.assert_allclose(self.core.logical_to_render_bounds((-1, 4, -2, 6, -1, 6), (4, 5, 6)),
                                   (0, 200, 0, 200, 0, 200))
        np.testing.assert_allclose(self.core.render_to_logical_bounds((-50, 250, -50, 250, -50, 250), (4, 5, 6)),
                                   (0, 3, 0, 4, 0, 5))


if __name__ == "__main__":
    unittest.main()
