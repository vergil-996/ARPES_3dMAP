import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pyvista as pv
from PyQt5.QtWidgets import QApplication, QPushButton, QWidget
from vtkmodules.util.numpy_support import vtk_to_numpy

from bandscope.ui.crop_controls import CropController
from bandscope.app.crop_integration import CropInteractionMixin
from bandscope.core.crop_model import CropSelection, apply_crop_regions, apply_selection, export_cropped_context
from bandscope.rendering.render_core import VolumeRenderSession
from bandscope.ui.result_workspace import AnalysisPageSpec


class EraseModelTests(unittest.TestCase):
    def setUp(self):
        self.data = np.arange(6 * 7 * 8).reshape(6, 7, 8)
        self.context = dict(view="3d", data=self.data,
                            coords={"X": np.arange(6.), "Y": np.arange(7.), "E": np.arange(8.)})
        self.region = CropSelection("3d", ("X", "Y", "E"), (1, 3, 2, 4, 3, 6), operation="erase")

    def test_volume_preserves_domain_and_source_and_exports_nan(self):
        result = apply_selection(self.context, self.region)
        expected = self.data.astype(float)
        expected[1:4, 2:5, 3:7] = np.nan
        np.testing.assert_equal(result["data"], expected)
        self.assertEqual(result["data"].shape, self.data.shape)
        self.assertFalse(np.shares_memory(result["data"], self.data))
        self.assertTrue(np.isfinite(self.data).all())
        payload = export_cropped_context(result)
        np.testing.assert_equal(payload["sample"], expected)
        np.testing.assert_equal(payload["E"], np.arange(8))
        np.testing.assert_equal(payload["erase_range"], self.region.bounds)

    def test_erase_single_layer_does_not_collapse_and_outside_is_noop(self):
        result = apply_selection(self.context, replace(self.region, bounds=(1, 3, 2, 2, 3, 6)))
        self.assertEqual(result["view"], "3d")
        self.assertEqual(np.isnan(result["data"]).sum(), 12)
        outside = apply_selection(self.context, replace(self.region, bounds=(10, 20, 2, 4, 3, 6)))
        np.testing.assert_equal(outside["data"], self.data)
        self.assertFalse(outside.get("crop_empty", False))

    def test_mixed_chain_keeps_holes_with_absolute_coordinates(self):
        crop = replace(self.region, operation="crop", bounds=(1, 4, 1, 5, 2, 7))
        result = apply_crop_regions(self.context, [self.region.to_dict(), crop.to_dict()])
        expected = self.data.astype(float)
        expected[1:4, 2:5, 3:7] = np.nan
        np.testing.assert_equal(result["data"], expected[1:5, 1:6, 2:8])
        after = apply_crop_regions(self.context, [crop.to_dict(), self.region.to_dict()])
        np.testing.assert_equal(result["data"], after["data"])

    def test_flipped_descending_slice_masks_visible_selection(self):
        context = dict(self.context, view="2d", data=self.data[:, 2, :], slice_info={"axis": 1, "index": 2})
        context["coords"] = dict(context["coords"], E=np.arange(8.)[::-1])
        selection = CropSelection("2d", ("X", "E"), (1, 3, 5, 7), True, "erase")
        result = apply_selection(context, selection)
        expected = context["data"].astype(float)
        expected[1:4, -3:] = np.nan
        np.testing.assert_equal(result["data"], expected)

    def test_curve_and_comparison_keep_samples_and_gaps(self):
        curve = dict(view="1d", x_data=np.arange(6.), y_data=np.arange(6.))
        selection = CropSelection("1d", ("x", "y"), (1, 4, 2, 3), operation="erase")
        result = apply_selection(curve, selection)
        np.testing.assert_equal(result["x_data"], curve["x_data"])
        np.testing.assert_equal(result["y_data"], [0, 1, np.nan, np.nan, 4, 5])
        comparison = dict(view="1d_comparison", curves=[curve, dict(curve, y_data=np.arange(6.) + 10)])
        result = apply_selection(comparison, replace(selection, view="1d_comparison"))
        np.testing.assert_equal(result["curves"][1]["y_data"], np.arange(6.) + 10)
        self.assertTrue(np.isnan(result["curves"][0]["y_data"][2:4]).all())

    def test_waterfall_masks_raw_and_normalized_at_display_offsets(self):
        context = dict(view="waterfall", energy_axis=np.arange(4.), k_values=np.arange(3.), offset_step=2,
                       curves=np.tile([0., .5, 1., .5], (3, 1)), raw_curves=np.arange(12.).reshape(3, 4))
        selection = CropSelection("waterfall", ("x", "E"), (2.3, 3.1, 1, 2), operation="erase")
        result = apply_selection(context, selection)
        self.assertEqual(result["curves"].shape, (3, 4))
        expected = np.zeros((3, 4), dtype=bool)
        expected[1, 1:3] = True
        np.testing.assert_equal(np.isnan(result["curves"]), expected)
        np.testing.assert_equal(np.isnan(result["raw_curves"]), expected)

    def test_all_erased_and_legacy_serialization(self):
        result = apply_selection(self.context, replace(self.region, bounds=(0, 5, 0, 6, 0, 7)))
        self.assertTrue(result["crop_empty"])
        self.assertTrue(np.isnan(result["data"]).all())
        self.assertEqual(CropSelection.from_dict(self.region.to_dict()), self.region)
        legacy = self.region.to_dict()
        legacy.pop("operation")
        self.assertEqual(CropSelection.from_dict(legacy).operation, "crop")


class EraseInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_modes_are_exclusive_and_popup_applies_erase(self):
        parent = QWidget()
        controller = CropController(parent)
        controller.activate("a", dict(view="1d", x_data=np.arange(5.), y_data=np.arange(5.)))
        host = SimpleNamespace(crop_controller=controller, btn_tb_crop=QPushButton(), btn_tb_erase=QPushButton(),
                               _can_show_interactive_box=lambda: False, _restore_volume_opacity_if_dimmed=Mock(),
                               _clear_interactive_box=Mock(), _refresh_axis_crop_interaction=Mock(),
                               left_workspace=SimpleNamespace(current_spec=lambda: None), current_render_context=None,
                               _update_crop_cursor=Mock(), plotter=SimpleNamespace(render=Mock()))
        for button in (host.btn_tb_crop, host.btn_tb_erase):
            button.setCheckable(True)
        host.btn_tb_crop.setChecked(True)
        host.btn_tb_erase.toggled.connect(lambda checked: CropInteractionMixin._toggle_region_mode(host, "erase", checked))
        host.btn_tb_erase.click()
        self.assertFalse(host.btn_tb_crop.isChecked())
        self.assertEqual(controller.selection.operation, "erase")
        self.assertEqual(controller.popup.apply_button.text(), "裁空")
        controller.activate("b", dict(view="1d", x_data=np.arange(5.), y_data=np.arange(5.)))
        self.assertEqual(controller.selection.operation, "erase")
        host.btn_tb_erase.click()
        self.assertFalse(controller.enabled)
        parent.close()

    def test_erase_creates_independent_result_and_crop_preserves_chain(self):
        context = dict(view="3d", data=np.ones((5, 5, 5)),
                       coords={key: np.arange(5.) for key in ("X", "Y", "E")})
        selection = CropSelection("3d", ("X", "Y", "E"), (1, 2, 1, 2, 1, 2), operation="erase")
        source = AnalysisPageSpec("home", "原始数据", "home", "home")
        pages = []
        host = SimpleNamespace(crop_controller=SimpleNamespace(selection=selection, popup=SimpleNamespace(set_error=Mock(), hide=Mock())),
                               left_workspace=SimpleNamespace(current_spec=lambda: source, add_page=pages.append),
                               _render_exact_ready=True, _compute_render_context=lambda spec: apply_crop_regions(context, spec.params.get("crop_regions", [])),
                               _persist_active_page_state=Mock(), _make_unique_page_title=lambda text: text, _make_page_id=lambda: "child")
        CropInteractionMixin.on_cut(host)
        self.assertEqual(len(pages), 1)
        self.assertTrue(pages[0].title.startswith("裁空"))
        self.assertNotIn("crop_regions", source.params)
        source = pages[0]
        host.crop_controller.selection = replace(selection, operation="crop", bounds=(0, 3, 0, 3, 0, 3))
        CropInteractionMixin.on_cut(host)
        result = host._compute_render_context(pages[1])
        self.assertEqual(result["data"].shape, (4, 4, 4))
        self.assertEqual(np.isnan(result["data"]).sum(), 8)


class EraseVolumeTests(unittest.TestCase):
    def test_volume_mask_renders_hole_and_updates_without_stale_voxels(self):
        plotter = pv.Plotter(off_screen=True, window_size=(240, 240))
        try:
            plotter.set_background("white")
            session = VolumeRenderSession(plotter)
            data = np.ones((24, 24, 24), dtype=np.float32)
            data[0, 0, 0] = 0
            data[7:17, 7:17, :] = np.nan
            session.render(data, (0, 50, 100), "sigmoid", show_axes=False)
            mask = session.volume.mapper.GetMaskInput()
            np.testing.assert_equal(vtk_to_numpy(mask.GetPointData().GetScalars()), np.isfinite(data).ravel(order="F"))
            self.assertTrue(np.isfinite(session.host_buffer).all())
            plotter.view_xy()
            plotter.reset_camera()
            image = plotter.screenshot(return_img=True)
            self.assertTrue(np.all(image[115:125, 115:125, :3] == 255))
            self.assertTrue(np.any(image[:, :, :3] < 240))
            updated = data.copy()
            updated[7:17, 7:17, :] = 1
            updated[2:5, 2:5, :] = np.nan
            session.render(updated, (0, 50, 100), "sigmoid", show_axes=False)
            image = plotter.screenshot(return_img=True)
            self.assertTrue(np.any(image[115:125, 115:125, :3] < 240))
            self.assertEqual(session.rebuild_count, 1)
        finally:
            plotter.close()


if __name__ == "__main__":
    unittest.main()
