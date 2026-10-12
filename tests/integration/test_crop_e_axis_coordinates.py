"""3D crop/erase fields, box geometry and voxels share displayed E coordinates."""
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.crop_model import CropSelection, apply_selection, export_cropped_context
from bandscope.ui.crop_controls import CropController
from bandscope.ui.result_workspace import AnalysisPageSpec


class CropEAxisCoordinateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = QWidget()
        self.controller = CropController(self.parent)
        self.core = AnalyzerCore()
        self.core.coords = {"X": np.arange(3.), "Y": np.arange(4.),
                            "E": np.array([-2., -1.4, -.8, -.3, -.1, 0.])}
        self.core.raw_data = np.arange(72.).reshape(3, 4, 6)
        self.analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        self.analyzer.core = self.core
        self.switch = SimpleNamespace(checked=True)
        self.switch.isChecked = lambda: self.switch.checked
        self.analyzer.timeline_bar = SimpleNamespace(switch_flip=self.switch)
        self.analyzer.crop_controller = self.controller
        self.analyzer._update_crop_cursor = Mock()

    def tearDown(self):
        self.controller.popup.hide()
        self.parent.close()
        self.parent.deleteLater()

    def context(self, low=0, high=5):
        return dict(view="3d", data=self.core.raw_data[:, :, low:high + 1],
                    coords=self.core.coords, data_bounds=(0, 2, 0, 3, low, high),
                    full_shape=(3, 4, 6))

    def activate(self, context):
        self.analyzer._activate_crop_context(SimpleNamespace(page_id="roi"), context)

    def test_popup_limits_match_rendered_roi_for_both_axis_directions_and_modes(self):
        original = self.core.coords["E"].copy()
        for energy in (original, original[::-1]):
            self.core.coords["E"] = energy
            for operation in ("crop", "erase"):
                with self.subTest(descending=energy[0] > energy[-1], operation=operation):
                    self.controller.clear()
                    self.controller.set_operation(operation)
                    context = self.context(1, 3)
                    self.activate(context)
                    rendered = self.analyzer._render_context_for_visual_flip(context)
                    expected = sorted(rendered["coords"]["E"][1:4][[0, -1]])
                    actual = [float(edit.text()) for edit in self.controller.popup.edits[4:6]]
                    np.testing.assert_allclose(actual, expected)
                    np.testing.assert_allclose(self.analyzer._crop_logical_bounds(), (0, 2, 0, 3, 1, 3))

    def test_edited_flipped_limits_select_the_same_voxels_as_the_box(self):
        context = self.context()
        self.activate(context)
        self.controller.popup.edits[4].setText("-1.4")
        self.controller.popup.edits[5].setText("-0.3")
        self.assertTrue(self.controller.commit_edits())
        np.testing.assert_allclose(self.analyzer._crop_logical_bounds(), (0, 2, 0, 3, 2, 4))
        cropped = apply_selection(context, self.controller.selection)
        self.assertEqual(cropped["data_bounds"], (0, 2, 0, 3, 2, 4))
        np.testing.assert_array_equal(cropped["data"], self.core.raw_data[:, :, 2:5])
        np.testing.assert_array_equal(cropped["coords"]["E"], self.core.coords["E"])
        exported = export_cropped_context(cropped, e_flip=True)
        np.testing.assert_allclose(exported["E"], self.core.coords["E"][::-1][2:5])
        erased = apply_selection(context, replace(self.controller.selection, operation="erase"))
        expected = np.zeros(self.core.raw_data.shape, dtype=bool)
        expected[:, :, 2:5] = True
        np.testing.assert_array_equal(np.isnan(erased["data"]), expected)
        self.assertTrue(np.isfinite(context["data"]).all())

    def test_box_drag_and_flip_transition_preserve_absolute_geometry(self):
        self.activate(self.context())
        bounds = (0, 2, 0, 3, 1.5, 3.5)
        for flipped in (True, False, True):
            with self.subTest(flipped=flipped):
                self.switch.checked = flipped
                self.analyzer._sync_slice_edits_from_logical_bounds(bounds)
                np.testing.assert_allclose(self.analyzer._crop_logical_bounds(), bounds)
                self.assertEqual(self.controller.selection.e_flip, flipped)
                energy = self.core.coords["E"][::-1] if flipped else self.core.coords["E"]
                expected = sorted(np.interp(bounds[4:6], np.arange(6), energy))
                actual = [float(edit.text()) for edit in self.controller.popup.edits[4:6]]
                np.testing.assert_allclose(actual, expected)

    def test_saved_page_draft_keeps_its_voxels_after_flip_on_another_page(self):
        context = self.context()
        self.switch.checked = False
        self.activate(context)
        self.controller.set_selection(replace(self.controller.selection, bounds=(0, 2, 0, 3, -1.4, -.3)), notify=False)
        expected = self.analyzer._crop_logical_bounds()
        self.controller.activate("other", context)
        self.switch.checked = True
        self.activate(context)
        np.testing.assert_allclose(self.analyzer._crop_logical_bounds(), expected)
        np.testing.assert_allclose(self.controller.selection.bounds[4:6], [-.8, -.1])

    def test_flipped_single_layer_restores_canonical_slice_coordinates(self):
        context = self.context()
        self.activate(context)
        for axis, bounds in (("E", (0, 2, 0, 3, -.3, -.3)),
                             ("Y", (0, 2, 1, 1, -1.4, -.3))):
            with self.subTest(axis=axis):
                result = apply_selection(context, replace(self.controller.selection, bounds=bounds))
                self.assertEqual(result["view"], "2d")
                np.testing.assert_array_equal(result["coords"]["E"], self.core.coords["E"])
                if axis == "E":
                    self.assertEqual(result["slice_info"]["index"], 2)
                    np.testing.assert_array_equal(result["data"], self.core.raw_data[:, :, 2])
                else:
                    np.testing.assert_array_equal(result["data"], self.core.raw_data[:, 1, 2:5])
                    np.testing.assert_allclose(result["slice_info"]["extent_override"], [0, 2, -.8, -.1])

    def test_new_pages_persist_canonical_ranges_for_slice_controls_and_erase(self):
        context = self.context()
        self.activate(context)
        for operation, bounds in (("crop", (0, 2, 0, 3, -.3, -.3)),
                                  ("erase", (0, 2, 0, 3, -1.4, -.3))):
            with self.subTest(operation=operation):
                selection = replace(self.controller.selection, bounds=bounds, operation=operation, e_flip=True)
                self.controller.set_selection(selection, notify=False)
                source = AnalysisPageSpec("source", "Source", "home", "data")
                self.analyzer.left_workspace = SimpleNamespace(current_spec=lambda: source, add_page=Mock())
                self.analyzer._render_exact_ready = True
                self.analyzer._compute_render_context = Mock(return_value=context)
                self.analyzer._persist_active_page_state = Mock()
                self.analyzer._make_unique_page_title = lambda title: title
                self.analyzer._make_page_id = lambda: "result"
                self.analyzer.on_cut()
                page = self.analyzer.left_workspace.add_page.call_args.args[0]
                saved = page.params["crop_regions"][0]
                self.assertFalse(saved["e_flip"])
                expected = apply_selection(context, selection)
                actual = apply_selection(context, CropSelection.from_dict(saved))
                np.testing.assert_equal(actual["data"], expected["data"])
                if operation == "crop":
                    self.assertEqual(self.analyzer._slice_physical_position(page, 2), -.3)


if __name__ == "__main__":
    unittest.main()
