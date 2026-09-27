"""Integration tests for crop chains, derived analyses and cropped exports.

These complement test_crop_model.py (pure numerics) by exercising the
interaction between crop_regions chains, analysis builders and exports on a
stubbed analyzer.  No real window is created.
"""
import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.core.crop_model import (
    CropSelection,
    apply_crop_regions,
    apply_selection,
    export_cropped_context,
    selection_for_context,
    waterfall_offsets,
)
from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.ui.result_workspace import AnalysisPageSpec


def _grid_context(shape=(6, 7, 8)):
    data = np.arange(np.prod(shape), dtype=np.float64).reshape(shape)
    coords = {
        "X": np.linspace(-1.0, 1.0, shape[0]),
        "Y": np.linspace(0.0, 3.0, shape[1]),
        "E": np.linspace(2.0, 9.0, shape[2]),
    }
    return {"view": "3d", "data": data, "coords": coords}


def _axis_integral_context():
    data = np.arange(6 * 8, dtype=np.float64).reshape(6, 8)
    return {
        "view": "2d",
        "data": data,
        "slice_info": {"axis": 0, "mode": "integral", "range": (1, 3)},
        "coords": {
            "X": np.linspace(-1, 1, 6),
            "Y": np.linspace(0, 3, 7),
            "E": np.linspace(2, 9, 8),
        },
        "plot_axes": {"x_key": "Y", "y_key": "E", "x_label": "ky", "y_label": "E"},
        "plot_logical_bounds": {"x_low": 0, "x_up": 5, "y_low": 0, "y_up": 7},
    }


def _stub_analyzer(spec):
    analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
    analyzer.left_workspace = SimpleNamespace(current_spec=lambda: spec)
    analyzer.home_slice_info = None
    analyzer.clip_ranges = None
    analyzer.precise_logical_bounds = None
    analyzer.current_render_context = None
    analyzer.timeline_bar = SimpleNamespace(
        slider_time=SimpleNamespace(value=lambda: 0),
        switch_flip=SimpleNamespace(isChecked=lambda: False),
    )
    analyzer.page_data = SimpleNamespace(
        s_t_low=SimpleNamespace(value=lambda: 0),
        s_t_up=SimpleNamespace(value=lambda: 2),
    )
    analyzer._persist_axis_integral_page_state = lambda *a, **k: None
    analyzer.page_control_blank = SimpleNamespace(get_second_derivative_params=lambda: {"sg_window": 5})
    analyzer._ask_second_derivative_axis = lambda: 2
    analyzer._make_page_id = Mock(side_effect=["p1", "p2", "p3", "p4"])
    analyzer._make_unique_page_title = lambda title, exclude_page_id=None: title
    analyzer._show_message = Mock()
    analyzer._seed_control_state_for_spec = lambda _spec: None
    analyzer._seed_second_derivative_axis_control_state = lambda _spec: None
    analyzer._persist_time_integral_page_state = lambda _spec: None
    analyzer._get_display_state_for_spec = lambda _spec: (None, None)
    analyzer._build_second_derivative_context_from_params = lambda *a: {"view": "2d"}
    analyzer.core = SimpleNamespace(
        raw_data=np.zeros((4, 5, 6, 1), dtype=np.float32),
        has_time_axis=True,
        logical_to_physical=lambda key, index: float(index),
        coords={"delay": np.arange(3.0)},
    )
    return analyzer


class CropChainTests(unittest.TestCase):
    def test_collapse_then_2d_crop_chain_matches_manual_slice(self):
        context = _grid_context()
        collapse = CropSelection("3d", ("X", "Y", "E"), (-1.0, 1.0, 0.0, 3.0, 4.0, 4.0))
        flat = apply_selection(context, collapse)
        self.assertEqual(flat["view"], "2d")
        e_index = int(np.argmin(np.abs(context["coords"]["E"] - 4.0)))
        self.assertEqual(flat["slice_info"]["axis"], 2)
        self.assertEqual(flat["slice_info"]["index"], e_index)
        np.testing.assert_array_equal(flat["data"], context["data"][:, :, e_index])

        region2 = CropSelection("2d", ("X", "Y"), (-0.5, 0.6, 1.0, 2.1))
        chained = apply_crop_regions(context, [collapse.to_dict(), region2.to_dict()])
        # Outward rounding widens to the nearest samples outside the bounds:
        # X -0.6..0.6 (indices 1..4), Y 1.0..2.5 (indices 2..5).
        np.testing.assert_array_equal(
            chained["data"], context["data"][1:5, 2:6, e_index]
        )
        xs = context["coords"]["X"]
        ys = context["coords"]["Y"]
        self.assertEqual(
            chained["slice_info"]["extent_override"],
            [float(xs[1]), float(xs[4]), float(ys[2]), float(ys[5])],
        )

    def test_region_serialization_roundtrip(self):
        region = CropSelection("3d", ("X", "Y", "E"), (-1.0, 1.0, 0.0, 3.0, 2.0, 9.0), True)
        restored = CropSelection.from_dict(jsonable := copy.deepcopy(region.to_dict()))
        self.assertEqual(region, restored)
        self.assertIsInstance(jsonable["bounds"], tuple)

    def test_view_mismatch_marks_empty_instead_of_raising(self):
        context = _axis_integral_context()
        region = CropSelection("3d", ("X", "Y", "E"), (-1, 1, 0, 3, 2, 9))
        result = apply_selection(context, region)
        self.assertTrue(result["crop_empty"])

    def test_nonuniform_coords_use_outward_rounding(self):
        context = _axis_integral_context()
        context["coords"]["E"] = np.asarray([0.0, 0.1, 0.4, 0.9, 1.6, 2.5, 3.6, 4.9])
        # Bounds fall strictly inside sample gaps on both ends.
        region = CropSelection("2d", ("Y", "E"), (0.0, 3.0, 0.45, 1.5))
        result = apply_selection(context, region)
        # Outward rounding includes samples at 0.4, 0.9 and 1.6.
        self.assertEqual(result["data"].shape[1], 3)
        extent = result["slice_info"]["extent_override"]
        self.assertAlmostEqual(extent[2], 0.4)
        self.assertAlmostEqual(extent[3], 1.6)

    def test_descending_axis_selects_same_physical_range(self):
        context = _axis_integral_context()
        ascending = apply_selection(context, CropSelection("2d", ("Y", "E"), (0.0, 3.0, 3.0, 6.0)))
        flipped_context = dict(context)
        flipped_context["coords"] = dict(context["coords"], E=context["coords"]["E"][::-1])
        flipped_context["data"] = context["data"][:, ::-1]
        descending = apply_selection(flipped_context, CropSelection("2d", ("Y", "E"), (0.0, 3.0, 3.0, 6.0)))
        # Same physical samples, kept in the page's own (descending) axis order.
        np.testing.assert_array_equal(descending["data"], ascending["data"][:, ::-1])
        extent = descending["slice_info"]["extent_override"]
        self.assertGreater(extent[3], 0)  # y extent records the page axis direction
        self.assertGreater(extent[2], extent[3])

    def test_all_nan_grid_marks_empty(self):
        context = _axis_integral_context()
        context["data"] = np.full_like(context["data"], np.nan)
        result = apply_selection(context, CropSelection("2d", ("Y", "E"), (0.0, 3.0, 2.0, 9.0)))
        self.assertTrue(result["crop_empty"])

    def test_single_pixel_2d_defaults_and_crop(self):
        context = _axis_integral_context()
        context["data"] = context["data"][:1, :1]
        context["plot_logical_bounds"] = {"x_low": 0, "x_up": 0, "y_low": 0, "y_up": 0}
        selection = selection_for_context(context)
        self.assertIsNotNone(selection)
        result = apply_selection(context, selection)
        self.assertFalse(result.get("crop_empty"))
        self.assertEqual(result["data"].shape, (1, 1))

    def test_curve_middle_removal_leaves_nan_gap_without_new_points(self):
        context = {
            "view": "1d",
            "x_data": np.arange(10.0),
            "y_data": np.arange(10.0) ** 2,
            "xlabel": "Energy (eV)",
        }
        # y range keeps only y in [4, 25] -> x 2..5 survive, gaps appear.
        selection = CropSelection("1d", ("Energy (eV)", "Intensity (a.u.)"), (1.0, 8.0, 4.0, 25.0))
        result = apply_selection(context, selection)
        self.assertEqual(len(result["x_data"]), 8)  # x 1..8 only, no interpolation
        np.testing.assert_array_equal(result["x_data"], np.arange(1.0, 9.0))
        self.assertTrue(np.isnan(result["y_data"][0]))      # y=1 < 4
        self.assertTrue(np.isnan(result["y_data"][5:]).all())  # y=36,49,64 > 25
        self.assertTrue(np.isfinite(result["y_data"][1:5]).all())  # y=4,9,16,25

    def test_waterfall_keeps_slots_offsets_and_masks_raw(self):
        curves = np.arange(20, dtype=np.float64).reshape(4, 5) + 1.0
        context = {
            "view": "waterfall",
            "curves": curves.copy(),
            "raw_curves": curves.copy() * 10,
            "energy_axis": np.arange(5.0),
            "k_values": np.arange(4.0),
            "offset_step": 2.0,
            "xlabel": "Intensity (normalized, arb. u.)",
            "ylabel": "Energy (eV)",
        }
        offsets = waterfall_offsets(context)
        np.testing.assert_allclose(offsets, [0.0, 2.0, 4.0, 6.0])
        # x window only covers the first two offset slots at low energy.
        selection = CropSelection("waterfall", ("Intensity (normalized, arb. u.)", "Energy (eV)"), (0.0, 3.5, 0.0, 2.0))
        result = apply_selection(context, selection)
        self.assertEqual(result["curves"].shape, (4, 3))  # energy 0..2 kept
        np.testing.assert_allclose(result["curve_offsets"], offsets)
        # Slots whose displayed x falls outside are fully masked but kept.
        self.assertFalse(np.any(np.isfinite(result["curves"][3])))
        mask_finite = np.isfinite(result["curves"])
        np.testing.assert_array_equal(np.isfinite(result["raw_curves"]), mask_finite)

    def test_waterfall_explicit_offsets_win_over_step(self):
        context = {
            "view": "waterfall",
            "curves": np.ones((3, 4)),
            "offset_step": 9.9,
            "curve_offsets": np.asarray([0.5, 1.5, 4.0]),
        }
        np.testing.assert_allclose(waterfall_offsets(context), [0.5, 1.5, 4.0])

    def test_e_flip_crop_matches_visible_rectangle(self):
        context = _axis_integral_context()
        e = context["coords"]["E"]
        yl, yh = 3.0, 6.0
        flipped = apply_selection(context, CropSelection("2d", ("Y", "E"), (0.0, 3.0, yl, yh), e_flip=True))
        # Visible window under flip at labels [yl, yh] shows physical flip(e).
        # flip maps e -> e_min + e_max - e over the full axis.
        physical_lo, physical_hi = e[0] + e[-1] - yh, e[0] + e[-1] - yl
        e_idx = np.where((e >= physical_lo - 1e-9) & (e <= physical_hi + 1e-9))[0]
        np.testing.assert_array_equal(flipped["data"], context["data"][:, e_idx])
        # Result stays in internal orientation: re-rendering with flip shows
        # the same window again (involution).
        from bandscope.core.crop_model import _orient
        reoriented = _orient(flipped, True)
        self.assertEqual(reoriented["data"].shape, flipped["data"].shape)

    def test_export_axes_match_cropped_sample(self):
        context = _grid_context()
        region = CropSelection("3d", ("X", "Y", "E"), (-0.5, 0.5, 1.0, 2.0, 3.0, 7.0))
        cropped = apply_selection(context, region)
        payload = export_cropped_context(cropped)
        self.assertEqual(
            payload["sample"].shape,
            (len(payload["kx"]), len(payload["ky"]), len(payload["E"])),
        )
        np.testing.assert_allclose(payload["crop_range"], region.bounds)


class DerivativePropagationTests(unittest.TestCase):
    def test_collapsed_home_crop_recovers_slice_and_keeps_tail(self):
        regions = [
            CropSelection("3d", ("X", "Y", "E"), (-1.0, 1.0, 0.0, 3.0, 4.0, 4.0)).to_dict(),
            CropSelection("2d", ("X", "Y"), (-0.5, 0.5, 1.0, 2.0)).to_dict(),
        ]
        spec = AnalysisPageSpec("crop-page", "裁剪", "home", "system",
                                params={"crop_regions": regions, "home_slice_info": None})
        analyzer = _stub_analyzer(spec)
        analyzer.current_render_context = {
            "view": "2d",
            "slice_info": {"axis": 2, "index": 4},
        }
        # Collapsed axis 2 (E) contains no energy axis in the 2D view -> the
        # builder must refuse exactly like the legacy transient slice path.
        self.assertIsNone(analyzer._build_second_derivative_spec())
        analyzer._show_message.assert_called_once()

        analyzer.current_render_context = {
            "view": "2d",
            "slice_info": {"axis": 0, "index": 2},
        }
        result = analyzer._build_second_derivative_spec()
        self.assertIsNotNone(result)
        self.assertEqual(result.params["source_view"], "2d")
        self.assertEqual(result.params["slice_axis"], 0)
        self.assertEqual(result.params["slice_index"], 2)
        self.assertEqual(result.params["derivative_axis"], 2)
        # Only the 2D tail is propagated; the collapsing 3D region is not.
        self.assertEqual(len(result.params["crop_regions"]), 1)
        self.assertEqual(result.params["crop_regions"][0]["view"], "2d")

    def test_cropped_transient_slice_keeps_full_2d_chain(self):
        regions = [CropSelection("2d", ("Y", "E"), (0.0, 2.0, 3.0, 6.0)).to_dict()]
        spec = AnalysisPageSpec("crop-page", "裁剪", "home", "system",
                                params={"crop_regions": regions,
                                        "home_slice_info": {"axis": 0, "index": 1}})
        analyzer = _stub_analyzer(spec)
        analyzer.home_slice_info = {"axis": 0, "index": 1}
        analyzer.current_render_context = {
            "view": "2d",
            "slice_info": {"axis": 0, "index": 1},
        }
        result = analyzer._build_second_derivative_spec()
        self.assertIsNotNone(result)
        self.assertEqual(result.params["slice_axis"], 0)
        self.assertEqual(len(result.params["crop_regions"]), 1)

    def test_uncropped_home_keeps_legacy_3d_derivative(self):
        spec = AnalysisPageSpec("home", "原始视图", "home", "system", params={})
        analyzer = _stub_analyzer(spec)
        analyzer._get_display_state_for_spec = lambda _spec: (
            np.zeros((2, 2, 2, 1)),
            {"X": np.arange(2.0), "Y": np.arange(2.0), "E": np.arange(2.0)},
        )
        result = analyzer._build_second_derivative_spec()
        self.assertIsNotNone(result)
        self.assertEqual(result.params["source_view"], "3d")
        self.assertNotIn("crop_regions", result.params)

    def test_time_integral_derivative_propagates_regions(self):
        regions = [CropSelection("3d", ("X", "Y", "E"), (-1.0, 1.0, 0.0, 3.0, 2.0, 8.0)).to_dict()]
        spec = AnalysisPageSpec("ti", "时间积分", "time_integral", "data_process",
                                params={"t_low": 0, "t_up": 2, "crop_regions": regions})
        analyzer = _stub_analyzer(spec)
        analyzer._get_display_state_for_spec = lambda _spec: (
            np.zeros((2, 2, 2, 1)),
            {"X": np.arange(2.0), "Y": np.arange(2.0), "E": np.arange(2.0)},
        )
        result = analyzer._build_second_derivative_spec()
        self.assertIsNotNone(result)
        self.assertEqual(result.params["source_mode"], "time_integral")
        self.assertEqual(len(result.params.get("crop_regions", [])), 1)
        # Deep copy: mutating the source chain must not leak into the child.
        spec.params["crop_regions"][0]["bounds"] = (0, 0, 0, 0, 0, 0)
        self.assertNotEqual(result.params["crop_regions"][0]["bounds"], (0, 0, 0, 0, 0, 0))

    def test_axis_integral_derivative_uses_final_rect_without_regions(self):
        spec = AnalysisPageSpec(
            "aic", "裁剪", "axis_integral_crop", "data_process",
            params={
                "axis_index": 0, "low": 1, "up": 3, "mid": 2,
                "source_mode": "frame", "source_t_index": 0,
                "source_t_low": 0, "source_t_up": 2,
                "crop_k_low": 1, "crop_k_up": 4, "crop_e_low": 2, "crop_e_up": 6,
                "crop_regions": [CropSelection("2d", ("Y", "E"), (0.0, 2.0, 3.0, 6.0)).to_dict()],
                "crop_base_rect": None,
            },
        )
        analyzer = _stub_analyzer(spec)
        result = analyzer._build_second_derivative_spec()
        self.assertIsNotNone(result)
        # crop_k/e metadata carries the final rect; the display chain is not
        # copied (it would double-crop after the rect inheritance).
        self.assertEqual(result.params["crop_k_low"], 1)
        self.assertEqual(result.params["crop_e_up"], 6)
        self.assertNotIn("crop_regions", result.params)


class CropExportMetadataTests(unittest.TestCase):
    def _analyzer_for_export(self, spec):
        analyzer = _stub_analyzer(spec)
        analyzer._is_current_page = lambda _spec: True
        return analyzer

    def test_axis_integral_crop_metadata(self):
        spec = AnalysisPageSpec(
            "aic", "裁剪", "axis_integral_crop", "data_process",
            params={
                "axis_index": 0, "low": 1, "up": 3, "mid": 2,
                "source_mode": "time_integral", "source_t_low": 0, "source_t_up": 2,
                "crop_regions": [CropSelection("2d", ("Y", "E"), (0.0, 2.0, 3.0, 6.0)).to_dict()],
            },
        )
        analyzer = self._analyzer_for_export(spec)
        context = {"integral_params": {"axis_index": 0, "low": 1, "up": 3, "mid": 2}}
        coords = {"delay": np.asarray([0.5, 1.5, 2.5])}
        metadata = analyzer._crop_export_metadata(spec, context, coords)
        self.assertEqual(str(metadata["integrated_axis"][0]), "X")
        np.testing.assert_allclose(metadata["integrated_range"], [1.0, 3.0])
        np.testing.assert_allclose(metadata["integrated_center"], [2.0])
        self.assertEqual(str(metadata["source_mode"][0]), "time_integral")
        np.testing.assert_allclose(metadata["source_t_range"], [0.5, 2.5])

    def test_derivative_metadata_keeps_axis_and_frame_time(self):
        spec = AnalysisPageSpec(
            "sd", "二阶导", "second_derivative", "data_process",
            params={"source_view": "3d", "derivative_axis": 2, "source_mode": "frame",
                    "source_t_index": 1,
                    "crop_regions": [CropSelection("3d", ("X", "Y", "E"), (0, 1, 0, 1, 0, 1)).to_dict()]},
        )
        analyzer = self._analyzer_for_export(spec)
        context = {"derivative_axis": 2, "source_mode": "frame"}
        coords = {"delay": np.asarray([0.5, 1.5, 2.5])}
        metadata = analyzer._crop_export_metadata(spec, context, coords)
        self.assertEqual(str(metadata["derivative_axis"][0]), "E")
        np.testing.assert_allclose(metadata["time"], [1.5])

    def test_edc_metadata_keeps_spatial_ranges(self):
        spec = AnalysisPageSpec(
            "edc", "EDC", "edc_curve", "data_process",
            params={"source_mode": "frame", "source_t_index": 0,
                    "crop_regions": [CropSelection("1d", ("Energy (eV)", "I"), (0, 1, 0, 1)).to_dict()]},
        )
        analyzer = self._analyzer_for_export(spec)
        context = {"kx_range": (-0.5, 0.5), "ky_range": (1.0, 2.0), "source_mode": "frame"}
        metadata = analyzer._crop_export_metadata(spec, context, {"delay": np.arange(2.0)})
        np.testing.assert_allclose(metadata["kx_range"], [-0.5, 0.5])
        np.testing.assert_allclose(metadata["ky_range"], [1.0, 2.0])

    def test_slice_dos_time_axis_is_not_overwritten(self):
        spec = AnalysisPageSpec(
            "dos", "DOS", "slice_dos", "data_process",
            params={"clip_ranges": [0, 1, 0, 1, 0, 1],
                    "crop_regions": [CropSelection("1d", ("Delay (ps)", "I"), (0, 1, 0, 1)).to_dict()]},
        )
        analyzer = self._analyzer_for_export(spec)
        # The 1D context's x axis is the delay axis: no scalar frame time.
        context = {"view": "1d", "xlabel": "Delay (ps)"}
        metadata = analyzer._crop_export_metadata(spec, context, {"delay": np.arange(4.0)})
        self.assertNotIn("time", metadata)
        # An energy-axis 1D page (EDC/energy DOS) still gets the frame time.
        context_e = {"view": "1d", "xlabel": "Energy (eV)"}
        metadata_e = analyzer._crop_export_metadata(spec, context_e, {"delay": np.arange(4.0)})
        self.assertIn("time", metadata_e)

    def test_export_payload_merges_metadata_and_refuses_empty(self):
        region = CropSelection("2d", ("Y", "E"), (0.0, 2.0, 3.0, 6.0))
        spec = AnalysisPageSpec(
            "aic", "裁剪页", "axis_integral_crop", "data_process",
            params={
                "axis_index": 0, "low": 1, "up": 3, "mid": 2,
                "source_mode": "frame", "source_t_index": 1,
                "crop_regions": [region.to_dict()],
            },
        )
        analyzer = self._analyzer_for_export(spec)
        analyzer._compute_render_context = lambda _spec: apply_selection(_axis_integral_context(), region)
        payload = analyzer._build_export_payload(spec, None, {"delay": np.arange(3.0)})
        self.assertIsNotNone(payload)
        _, name, export_data = payload
        self.assertTrue(name.endswith(".mat"))
        self.assertIn("integrated_axis", export_data)
        self.assertIn("crop_range", export_data)
        self.assertEqual(
            export_data["sample"].shape,
            (len(export_data["ky"]), len(export_data["E"])),
        )

        analyzer._compute_render_context = lambda _spec: dict(
            apply_selection(_axis_integral_context(), region), crop_empty=True
        )
        self.assertIsNone(analyzer._build_export_payload(spec, None, {"delay": np.arange(3.0)}))


class OnCutTests(unittest.TestCase):
    """CropInteractionMixin.on_cut with a stubbed analyzer surface."""

    def _make_analyzer(self, spec, context, selection):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.left_workspace = SimpleNamespace(
            current_spec=lambda: spec,
            add_page=Mock(side_effect=lambda new_spec: setattr(analyzer, "added", new_spec)),
        )
        analyzer.added = None
        analyzer.crop_controller = SimpleNamespace(
            selection=selection,
            popup=SimpleNamespace(hide=Mock(), set_error=Mock()),
        )
        analyzer._render_exact_ready = True
        analyzer._compute_render_context = lambda _spec: context
        analyzer._persist_active_page_state = lambda: None
        analyzer._make_unique_page_title = lambda title: title
        analyzer._make_page_id = Mock(side_effect=["child1", "child2", "child3"])
        analyzer.rotation_angle = 0.0
        analyzer.errors = []
        return analyzer

    def test_child_params_are_independent_deepcopy(self):
        context = _axis_integral_context()
        selection = CropSelection("2d", ("Y", "E"), (0.0, 2.0, 3.0, 6.0))
        spec = AnalysisPageSpec(
            "axis", "X积分", "axis_integral", "data_process",
            params={"axis_index": 0, "low": 1, "up": 3, "mid": 2,
                    "source_mode": "frame", "source_t_index": 0,
                    "source_t_low": 0, "source_t_up": 2},
        )
        analyzer = self._make_analyzer(spec, context, selection)
        analyzer.on_cut()
        child = analyzer.added
        self.assertIsNotNone(child)
        self.assertEqual(child.page_kind, "axis_integral_crop")
        self.assertEqual(child.source_page_id, "axis")
        self.assertEqual(len(child.params["crop_regions"]), 1)
        self.assertIn("crop_base_rect", child.params)
        self.assertIsNone(child.params["crop_base_rect"])
        # Deep-copy independence in both directions.
        spec.params["low"] = 99
        self.assertEqual(child.params["low"], 1)
        child.params["mid"] = 42
        self.assertEqual(spec.params["mid"], 2)

    def test_repeated_crop_appends_chain_and_keeps_base_rect(self):
        context = _axis_integral_context()
        first = CropSelection("2d", ("Y", "E"), (0.0, 2.5, 3.0, 7.0))
        spec = AnalysisPageSpec(
            "axis", "X积分", "axis_integral", "data_process",
            params={"axis_index": 0, "low": 1, "up": 3, "mid": 2,
                    "source_mode": "frame", "source_t_index": 0,
                    "source_t_low": 0, "source_t_up": 2},
        )
        analyzer = self._make_analyzer(spec, context, first)
        analyzer.on_cut()
        child = analyzer.added
        base_rect = {"x_low": 0, "x_up": 2, "y_low": 1, "y_up": 5}
        child.params["crop_base_rect"] = base_rect

        # Second crop from the cropped page.
        analyzer2 = self._make_analyzer(
            child, apply_selection(context, first),
            CropSelection("2d", ("Y", "E"), (0.5, 2.0, 4.0, 6.0)),
        )
        analyzer2._make_page_id = Mock(side_effect=["grandchild"])
        analyzer2.on_cut()
        grandchild = analyzer2.added
        self.assertIsNotNone(grandchild)
        self.assertEqual(len(grandchild.params["crop_regions"]), 2)
        self.assertEqual(grandchild.params["crop_base_rect"], base_rect)

    def test_empty_crop_shows_error_and_adds_no_page(self):
        context = _axis_integral_context()
        # Outside the data range -> no samples -> crop_empty.
        selection = CropSelection("2d", ("Y", "E"), (100.0, 200.0, 3.0, 6.0))
        spec = AnalysisPageSpec("axis", "X积分", "axis_integral", "data_process",
                                params={"axis_index": 0, "low": 1, "up": 3})
        analyzer = self._make_analyzer(spec, context, selection)
        analyzer.on_cut()
        self.assertIsNone(analyzer.added)
        analyzer.crop_controller.popup.set_error.assert_called_once()

    def test_home_volume_roi_rejected_while_rotated(self):
        context = _grid_context()
        selection = CropSelection("3d", ("X", "Y", "E"), (-1.0, 0.5, 0.5, 2.5, 3.0, 8.0))
        spec = AnalysisPageSpec("home", "原始视图", "home", "system", params={})
        analyzer = self._make_analyzer(spec, context, selection)
        analyzer.rotation_angle = 15.0
        analyzer.on_cut()
        self.assertIsNone(analyzer.added)
        analyzer.crop_controller.popup.set_error.assert_called_once()

    def test_home_volume_roi_registers_scope_without_regions(self):
        context = _grid_context()
        selection = CropSelection("3d", ("X", "Y", "E"), (-1.0, 0.5, 0.5, 2.5, 3.0, 8.0))
        spec = AnalysisPageSpec("home", "原始视图", "home", "system", params={})
        analyzer = self._make_analyzer(spec, context, selection)
        analyzer.original_raw_data = np.zeros((6, 7, 8, 1), dtype=np.float32)
        analyzer.global_denoise_methods = {}
        analyzer._validate_denoise_for_descriptor = lambda *a: None
        descriptor = SimpleNamespace(scope_id="roi-1", label="ROI #1")
        analyzer._register_roi_scope = lambda bounds: descriptor
        analyzer._invalidate_scope_render_state = lambda: None
        analyzer._request_scope_denoise_if_needed = lambda _spec: None
        analyzer.global_refresh = lambda: None
        analyzer.on_cut()
        child = analyzer.added
        self.assertIsNotNone(child)
        self.assertEqual(child.page_kind, "home")
        self.assertEqual(child.data_scope_id, "roi-1")
        self.assertNotIn("crop_regions", child.params)
        self.assertEqual(child.params["clip_ranges"], list(child.params["precise_logical_bounds"]))


if __name__ == "__main__":
    unittest.main()
