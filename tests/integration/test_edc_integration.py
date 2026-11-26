import copy
import unittest

import numpy as np

from bandscope.app.edc_integration import EdcIntegrationMixin
from bandscope.core.crop_model import CropSelection
from bandscope.core.data_scope import DataScopeDescriptor, ScopedSpatialArray
from bandscope.ui.result_workspace import AnalysisPageSpec


class _Workspace:
    def __init__(self, spec):
        self.spec = spec
        self.added = []

    def current_spec(self):
        return self.spec

    def add_page(self, spec):
        self.added.append(spec)


class _CropController:
    def __init__(self, selections, events):
        self.selections = selections
        self.events = events

    def commit_edits(self):
        self.events.append("commit")

    def selections_for_page(self, page_id):
        self.events.append(("selections", page_id))
        return tuple(self.selections)


class _EdcHost(EdcIntegrationMixin):
    def __init__(self, spec, context, selections, raw_data, coords):
        self.left_workspace = _Workspace(spec)
        self.current_render_context = context
        self.events = []
        self.crop_controller = _CropController(selections, self.events)
        self.raw_data = raw_data
        self.coords = coords
        self.messages = []
        self.seeded = []
        self.axis_build_calls = []
        self.axis_source_calls = 0
        self.display_calls = 0
        self.page_calls = 0
        self.prepared_axis_context = None
        self.rotation_angle = 0.0
        self.scope_descriptor = None
        self.scope_metadata_calls = []

    def _get_display_state_for_spec(self, spec):
        self.display_calls += 1
        return self.raw_data, self.coords

    def _resolved_axis_integral_params(self, spec):
        return copy.deepcopy(spec.params)

    def _rotation_angle_for_spec(self, spec):
        angle = (spec.params or {}).get("source_rotation_angle")
        return float(self.rotation_angle if angle is None else angle)

    def _build_axis_source_spec_from_params(self, params):
        self.axis_build_calls.append(copy.deepcopy(params))
        return AnalysisPageSpec(
            "axis-source",
            "axis-source",
            "axis_integral_crop" if "crop_k_low" in params else "axis_integral",
            "test",
            params=copy.deepcopy(params),
        )

    @staticmethod
    def _axis_plot_info(axis_index):
        keys = tuple(
            key for index, key in enumerate(("X", "Y", "E")) if index != axis_index
        )
        return {"x_key": keys[0], "y_key": keys[1]}

    def _get_3d_source_context_for_axis(self, spec, raw_data):
        self.axis_source_calls += 1
        if self.prepared_axis_context is not None:
            return self.prepared_axis_context
        low = int(spec.params.get("source_t_low", 0))
        up = int(spec.params.get("source_t_up", low))
        return {
            "view": "3d",
            "data": np.sum(raw_data[..., low:up + 1], axis=3, dtype=np.float64),
        }

    def _time_indices_for_spec(self, spec):
        low = int(spec.params.get("source_t_low", 0))
        up = int(spec.params.get("source_t_up", low))
        return low, up

    def _make_page_id(self):
        self.page_calls += 1
        return f"edc-page-{self.page_calls}"

    @staticmethod
    def _make_unique_page_title(title):
        return title

    def _seed_control_state_for_spec(self, spec):
        self.seeded.append(spec.page_id)

    def _show_message(self, title, text):
        self.messages.append((title, text))

    def _scope_for_spec(self, spec):
        return self.scope_descriptor

    def _scope_export_metadata(self, descriptor, coords):
        self.scope_metadata_calls.append((descriptor, coords))
        if descriptor is None or descriptor.is_full:
            return {
                "data_scope_id": np.asarray(["full"]),
                "data_scope_label": np.asarray(["完整数据"]),
            }
        return {
            "data_scope_id": np.asarray([descriptor.scope_id]),
            "data_scope_label": np.asarray([descriptor.label]),
            "roi_index_bounds": np.asarray(descriptor.spatial_bounds, dtype=np.int32),
            "roi_physical_bounds": descriptor.physical_bounds(coords),
        }

    @staticmethod
    def _format_filename_number(value):
        return f"{float(value):g}"


class EdcIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.raw_data = np.arange(4 * 3 * 5 * 3, dtype=np.float64).reshape(4, 3, 5, 3)
        self.coords = {
            "X": np.asarray([-1.0, -0.5, 0.5, 1.0]),
            "Y": np.asarray([0.0, 1.0, 3.0]),
            "E": np.asarray([2.0, 0.5, 0.0, -0.5, -2.0]),
            "delay": np.asarray([0.0, 100.0, 300.0]),
        }
        self.time_interval = {
            "axis_key": "delay",
            "low": 100.0,
            "up": 300.0,
            "center": 200.0,
            "locked": True,
        }

    def _axis_host(
        self, selections, *, axis_index=2, normal_bounds=None, page_kind="axis_integral_crop"
    ):
        plot_keys = tuple(
            key for index, key in enumerate(("X", "Y", "E")) if index != axis_index
        )
        if normal_bounds is None:
            normal_bounds = (0, self.raw_data.shape[axis_index] - 1)
        low, up = sorted(normal_bounds)
        spec = AnalysisPageSpec(
            "axis-page",
            f"{plot_keys[0]}-{plot_keys[1]} integral",
            page_kind,
            "test",
            source_page_id="home",
            params={
                "axis_index": axis_index,
                "low": low,
                "up": up,
                "mid": round((low + up) / 2),
                "source_t_low": 1,
                "source_t_up": 2,
                "time_interval": copy.deepcopy(self.time_interval),
                "crop_k_low": 0,
                "crop_k_up": self.raw_data.shape[
                    ("X", "Y", "E").index(plot_keys[0])
                ] - 1,
                "crop_e_low": 0,
                "crop_e_up": self.raw_data.shape[
                    ("X", "Y", "E").index(plot_keys[1])
                ] - 1,
            },
        )
        if page_kind == "axis_integral":
            for key in ("crop_k_low", "crop_k_up", "crop_e_low", "crop_e_up"):
                spec.params.pop(key)
        context = {
            "view": "2d",
            "data": np.zeros(
                tuple(
                    self.raw_data.shape[("X", "Y", "E").index(key)]
                    for key in plot_keys
                )
            ),
            "coords": self.coords,
            "slice_info": {
                "axis": axis_index,
                "mode": "integral",
                "range": (low, up),
            },
            "plot_axes": {"x_key": plot_keys[0], "y_key": plot_keys[1]},
        }
        return _EdcHost(spec, context, selections, self.raw_data, self.coords)

    def _home_host(self, selections, *, context_data=None, data_bounds=None):
        spec = AnalysisPageSpec(
            "home",
            "Home",
            "home",
            "test",
            params={
                "source_t_low": 1,
                "source_t_up": 2,
                "time_interval": copy.deepcopy(self.time_interval),
            },
        )
        if context_data is None:
            context_data = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        context = {"view": "3d", "data": context_data, "coords": self.coords}
        if data_bounds is not None:
            context.update(data_bounds=data_bounds, full_shape=self.raw_data.shape[:3])
        return _EdcHost(spec, context, selections, self.raw_data, self.coords)

    @staticmethod
    def _curves(spec):
        return [spec.params["base_curve"], *spec.params["overlay_curves"]]

    def test_2d_multiple_selections_share_source_and_create_one_comparison(self):
        first = CropSelection("2d", ("X", "Y"), (-1.0, -0.5, 0.0, 1.0))
        second = CropSelection(
            "2d", ("X", "Y"), (0.5, 1.0, 1.0, 3.0), operation="erase"
        )
        host = self._axis_host([("sel-a", first), ("sel-b", second)])

        page_id = host._create_edc_result_from_selections()

        self.assertEqual(page_id, "edc-page-1")
        self.assertEqual(host.events[:2], ["commit", ("selections", "axis-page")])
        self.assertEqual(host.axis_source_calls, 1)
        self.assertEqual(len(host.axis_build_calls), 1)
        self.assertEqual(len(host.left_workspace.added), 1)
        page = host.left_workspace.added[0]
        self.assertEqual(page.page_kind, "curve_comparison_1d")
        self.assertEqual(page.source_page_id, "axis-page")
        self.assertEqual(page.params["comparison_kind"], "edc_curve")
        self.assertEqual(page.params["x_match_policy"], "per_curve")
        self.assertEqual(host.seeded, [page_id])

        curves = self._curves(page)
        self.assertEqual(len(curves), 2)
        source = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        np.testing.assert_allclose(
            curves[0]["y_data"], np.sum(source[0:2, 0:2, :], axis=(0, 1))
        )
        np.testing.assert_allclose(
            curves[1]["y_data"], np.sum(source[2:4, 1:3, :], axis=(0, 1))
        )
        self.assertEqual(len({curve["curve_id"] for curve in curves}), 2)
        self.assertEqual([curve["palette_slot"] for curve in curves], [0, 1])
        self.assertEqual([curve["title"] for curve in curves], ["EDC #1", "EDC #2"])
        self.assertEqual(
            page.params["curve_metadata"][0]["kx_range"], (-1.0, -0.5)
        )
        self.assertEqual(
            page.params["curve_metadata"][0]["ky_range"], (0.0, 1.0)
        )
        self.assertEqual(page.params["curve_metadata"][1]["operation"], "erase")
        self.assertEqual(
            page.params["curve_metadata"][0]["source_t_range"], (100.0, 300.0)
        )
        self.assertEqual(
            page.params["curve_metadata"][0]["time_interval"], self.time_interval
        )

    def test_xz_selection_uses_selected_energy_and_normal_y_interval(self):
        selection = CropSelection("2d", ("X", "E"), (-0.5, 1.0, 0.0, 0.5))
        host = self._axis_host(
            [("xz", selection)],
            axis_index=1,
            normal_bounds=(1, 2),
            page_kind="axis_integral",
        )

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        curve = page.params["base_curve"]
        source = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        np.testing.assert_array_equal(curve["x_data"], [0.5, 0.0])
        np.testing.assert_allclose(
            curve["y_data"], np.sum(source[1:4, 1:3, 1:3], axis=(0, 1))
        )
        self.assertEqual(page.params["source_data_bounds"], (0, 3, 0, 2, 0, 4))
        metadata = page.params["curve_metadata"][0]
        self.assertEqual(metadata["index_bounds"], (1, 3, 1, 2, 1, 2))
        self.assertEqual(metadata["kx_range"], (-0.5, 1.0))
        self.assertEqual(metadata["ky_range"], (1.0, 3.0))
        self.assertEqual(metadata["energy_range"], (0.5, 0.0))
        self.assertIn("E 0.5~0", curve["title"])

    def test_yz_selection_with_swapped_axes_uses_normal_x_interval(self):
        selection = CropSelection("2d", ("E", "Y"), (-0.5, 2.0, 1.0, 3.0))
        host = self._axis_host(
            [("yz", selection)],
            axis_index=0,
            normal_bounds=(1, 2),
            page_kind="axis_integral",
        )

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        curve = page.params["base_curve"]
        source = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        np.testing.assert_array_equal(curve["x_data"], [2.0, 0.5, 0.0, -0.5])
        np.testing.assert_allclose(
            curve["y_data"], np.sum(source[1:3, 1:3, 0:4], axis=(0, 1))
        )
        self.assertEqual(page.params["source_data_bounds"], (0, 3, 0, 2, 0, 4))
        metadata = page.params["curve_metadata"][0]
        self.assertEqual(metadata["index_bounds"], (1, 2, 1, 2, 0, 3))
        self.assertEqual(metadata["kx_range"], (-0.5, 0.5))
        self.assertEqual(metadata["ky_range"], (1.0, 3.0))
        self.assertEqual(metadata["energy_range"], (2.0, -0.5))

    def test_xy_selection_keeps_full_energy_when_e_slider_is_narrowed(self):
        selection = CropSelection("2d", ("X", "Y"), (-1.0, -0.5, 0.0, 1.0))
        host = self._axis_host(
            [("xy", selection)],
            axis_index=2,
            normal_bounds=(1, 2),
            page_kind="axis_integral",
        )

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        curve = page.params["base_curve"]
        source = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        np.testing.assert_array_equal(curve["x_data"], self.coords["E"])
        np.testing.assert_allclose(
            curve["y_data"], np.sum(source[0:2, 0:2, :], axis=(0, 1))
        )
        self.assertEqual(page.params["source_data_bounds"], (0, 3, 0, 2, 0, 4))
        self.assertEqual(
            page.params["curve_metadata"][0]["index_bounds"], (0, 1, 0, 1, 0, 4)
        )

    def test_xz_crop_and_erase_regions_replay_in_order_with_normal_interval(self):
        selection = CropSelection("2d", ("X", "E"), (-1.0, 1.0, -2.0, 2.0))
        host = self._axis_host(
            [("xz", selection)], axis_index=1, normal_bounds=(1, 2)
        )
        host.left_workspace.spec.params.update(
            crop_base_rect=None,
            crop_regions=[
                CropSelection(
                    "2d", ("X", "E"), (-0.5, 1.0, -0.5, 2.0)
                ).to_dict(),
                CropSelection(
                    "2d",
                    ("X", "E"),
                    (0.5, 1.0, -0.5, 0.0),
                    operation="erase",
                ).to_dict(),
            ],
        )

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        source = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        expected = np.sum(source[1:4, 1:3, 0:4], axis=(0, 1))
        expected[2:4] -= np.sum(source[2:4, 1:3, 2:4], axis=(0, 1))
        np.testing.assert_allclose(page.params["base_curve"]["y_data"], expected)
        np.testing.assert_array_equal(
            page.params["base_curve"]["x_data"], [2.0, 0.5, 0.0, -0.5]
        )
        self.assertEqual(page.params["source_data_bounds"], (1, 3, 1, 2, 0, 3))

    def test_2d_crop_and_erase_regions_replay_on_the_3d_source_in_order(self):
        selection = CropSelection("2d", ("X", "Y"), (-1.0, 1.0, 0.0, 3.0))
        host = self._axis_host([("sel", selection)])
        host.left_workspace.spec.params.update(
            crop_base_rect=None,
            crop_regions=[
                CropSelection(
                    "2d", ("X", "Y"), (-0.5, 1.0, 0.0, 3.0)
                ).to_dict(),
                CropSelection(
                    "2d",
                    ("X", "Y"),
                    (0.5, 1.0, 1.0, 3.0),
                    operation="erase",
                ).to_dict(),
            ],
        )

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        source = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        expected = np.sum(source[1:4, :, :], axis=(0, 1)) - np.sum(
            source[2:4, 1:3, :], axis=(0, 1)
        )
        np.testing.assert_allclose(page.params["base_curve"]["y_data"], expected)
        self.assertEqual(page.params["source_data_bounds"], (1, 3, 0, 2, 0, 4))
        self.assertEqual(
            page.params["curve_metadata"][0]["index_bounds"], (1, 3, 0, 2, 0, 4)
        )

    def test_legacy_axis_crop_parameters_limit_the_restored_xy_source(self):
        selection = CropSelection("2d", ("X", "Y"), (-1.0, 1.0, 0.0, 3.0))
        host = self._axis_host([("sel", selection)])
        host.left_workspace.spec.params.update(
            crop_k_low=1,
            crop_k_up=1,
            crop_e_low=1,
            crop_e_up=1,
        )

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        source = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        np.testing.assert_allclose(
            page.params["base_curve"]["y_data"],
            np.sum(source[1:2, 1:2, :], axis=(0, 1)),
        )
        self.assertEqual(page.params["source_data_bounds"], (1, 1, 1, 1, 0, 4))

    def test_legacy_axis_crop_supports_descending_x_coordinates(self):
        selection = CropSelection("2d", ("X", "Y"), (-1.0, 1.0, 0.0, 3.0))
        host = self._axis_host([("sel", selection)])
        host.coords = dict(self.coords, X=self.coords["X"][::-1].copy())
        host.left_workspace.spec.params.update(
            crop_k_low=1,
            crop_k_up=2,
            crop_e_low=0,
            crop_e_up=4,
        )

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        source = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        np.testing.assert_allclose(
            page.params["base_curve"]["y_data"],
            np.sum(source[1:3, :, :], axis=(0, 1)),
        )
        self.assertEqual(page.params["source_data_bounds"], (1, 2, 0, 2, 0, 4))

    def test_legacy_axis_crop_supports_descending_y_coordinates(self):
        selection = CropSelection("2d", ("X", "Y"), (-1.0, 1.0, 0.0, 3.0))
        host = self._axis_host([("sel", selection)])
        host.coords = dict(self.coords, Y=self.coords["Y"][::-1].copy())
        host.left_workspace.spec.params.update(
            crop_k_low=0,
            crop_k_up=3,
            crop_e_low=0,
            crop_e_up=1,
        )

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        source = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        np.testing.assert_allclose(
            page.params["base_curve"]["y_data"],
            np.sum(source[:, 0:2, :], axis=(0, 1)),
        )
        self.assertEqual(page.params["source_data_bounds"], (0, 3, 0, 1, 0, 4))

    def test_crop_base_rect_and_regions_support_descending_coordinates(self):
        selection = CropSelection("2d", ("X", "Y"), (-1.0, 1.0, 0.0, 3.0))
        host = self._axis_host([("sel", selection)])
        host.coords = dict(
            self.coords,
            X=self.coords["X"][::-1].copy(),
            Y=self.coords["Y"][::-1].copy(),
        )
        host.left_workspace.spec.params.update(
            crop_base_rect={"x_low": 1, "x_up": 3, "y_low": 0, "y_up": 2},
            crop_regions=[
                CropSelection(
                    "2d", ("X", "Y"), (-0.5, 0.5, 1.0, 3.0)
                ).to_dict()
            ],
        )

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        source = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        np.testing.assert_allclose(
            page.params["base_curve"]["y_data"],
            np.sum(source[1:3, 0:2, :], axis=(0, 1)),
        )
        self.assertEqual(page.params["source_data_bounds"], (1, 2, 0, 1, 0, 4))
        self.assertEqual(
            page.params["curve_metadata"][0]["index_bounds"], (1, 2, 0, 1, 0, 4)
        )

    def test_3d_home_builds_directly_and_retains_different_energy_ranges(self):
        first = CropSelection(
            "3d", ("X", "Y", "E"), (-1.0, -0.5, 0.0, 1.0, 0.0, 2.0)
        )
        second = CropSelection(
            "3d", ("X", "Y", "E"), (0.5, 1.0, 1.0, 3.0, -2.0, -0.5)
        )
        host = self._home_host([("box-a", first), ("box-b", second)])

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        self.assertEqual(host.axis_build_calls, [])
        self.assertEqual(host.axis_source_calls, 0)
        page = host.left_workspace.added[0]
        self.assertEqual(page.page_kind, "curve_comparison_1d")
        self.assertEqual(page.source_page_id, "home")
        curves = self._curves(page)
        np.testing.assert_array_equal(curves[0]["x_data"], [2.0, 0.5, 0.0])
        np.testing.assert_array_equal(curves[1]["x_data"], [-0.5, -2.0])
        self.assertEqual(len(curves[0]["y_data"]), 3)
        self.assertEqual(len(curves[1]["y_data"]), 2)
        self.assertEqual([curve["title"] for curve in curves], ["EDC #1", "EDC #2"])
        self.assertEqual(
            page.params["curve_metadata"][0]["energy_range"], (2.0, 0.0)
        )
        self.assertEqual(
            page.params["curve_metadata"][1]["energy_range"], (-0.5, -2.0)
        )
        source = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        np.testing.assert_allclose(
            curves[0]["y_data"], np.sum(source[0:2, 0:2, 0:3], axis=(0, 1))
        )

    def test_single_3d_selection_freezes_standard_snapshot_and_export_metadata(self):
        selection = CropSelection(
            "3d", ("X", "Y", "E"), (-1.0, -0.5, 0.0, 1.0, 0.5, 0.5)
        )
        host = self._home_host([("box", selection)])

        page_id = host._create_edc_result_from_selections()

        page = host.left_workspace.added[0]
        self.assertEqual(page.page_id, page_id)
        self.assertEqual(page.page_kind, "edc_curve")
        self.assertEqual(page.params["overlay_curves"], [])
        curve = page.params["base_curve"]
        self.assertEqual(curve["curve_kind"], "edc_curve")
        self.assertEqual(curve["xlabel"], "Energy (eV)")
        self.assertEqual(curve["x_data"], [0.5])
        self.assertEqual(len(curve["y_data"]), 1)
        self.assertTrue(curve["is_base"])
        metadata = page.params["curve_metadata"][0]
        self.assertEqual(metadata["curve_id"], curve["curve_id"])
        self.assertEqual(metadata["selection_id"], "box")
        self.assertEqual(metadata["kx_range"], (-1.0, -0.5))
        self.assertEqual(metadata["ky_range"], (0.0, 1.0))
        self.assertEqual(metadata["energy_range"], (0.5, 0.5))
        self.assertEqual(metadata["source_t_indices"], (1, 2))
        self.assertEqual(metadata["source_t_range"], (100.0, 300.0))
        self.assertEqual(metadata["index_bounds"], (0, 1, 0, 1, 1, 1))

    def test_compact_data_bounds_and_scoped_array_use_absolute_indices(self):
        bounds = (1, 3, 0, 1, 1, 4)
        full = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        compact = full[1:4, 0:2, 1:5]
        descriptor = DataScopeDescriptor("roi", self.raw_data.shape, bounds)
        sources = (
            ("data_bounds", compact),
            ("scoped", ScopedSpatialArray(compact, descriptor)),
        )
        selection = CropSelection(
            "3d", ("X", "Y", "E"), (-1.0, -0.5, 0.0, 3.0, -2.0, 2.0)
        )
        expected = np.sum(full[1:2, 0:2, 1:5], axis=(0, 1))
        for kind, data in sources:
            with self.subTest(kind=kind):
                host = self._home_host(
                    [("box", selection)],
                    context_data=data,
                    data_bounds=bounds if kind == "data_bounds" else None,
                )
                page_id = host._create_edc_result_from_selections()
                self.assertIsNotNone(page_id)
                page = host.left_workspace.added[0]
                curve = page.params["base_curve"]
                np.testing.assert_array_equal(curve["x_data"], [0.5, 0.0, -0.5, -2.0])
                np.testing.assert_allclose(curve["y_data"], expected)
                metadata = page.params["curve_metadata"][0]
                self.assertEqual(metadata["index_bounds"], (1, 1, 0, 1, 1, 4))
                self.assertEqual(page.params["source_data_bounds"], bounds)

    def test_axis_page_source_angle_overrides_live_window_angle(self):
        selection = CropSelection("2d", ("X", "Y"), (-1.0, -0.5, 0.0, 1.0))
        host = self._axis_host([("xy", selection)], page_kind="axis_integral")
        host.left_workspace.spec.params["source_rotation_angle"] = 17.5
        host.rotation_angle = 62.0

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        self.assertEqual(page.params["source_rotation_angle"], 17.5)
        frozen = page.params["curve_metadata"][0]["selection"]
        self.assertEqual(frozen["rotation_angle"], 17.5)
        self.assertEqual(frozen["logical_bounds"], (0, 1, 0, 1))

    def test_legacy_saved_selection_without_angle_keeps_rectangular_recompute(self):
        selection = CropSelection("2d", ("X", "Y"), (-1.0, -0.5, 0.0, 1.0))
        host = self._axis_host([("xy", selection)], page_kind="axis_integral")
        page_id = host._create_edc_result_from_selections()
        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        metadata = page.params["curve_metadata"][0]
        metadata["selection"].pop("rotation_angle")
        metadata["selection"].pop("logical_bounds")
        expected = np.asarray(page.params["base_curve"]["y_data"])

        recomputed = host._recompute_edc_time_curves(page)

        self.assertIsNotNone(recomputed)
        np.testing.assert_allclose(recomputed["curves"][0]["y_data"], expected)
        self.assertNotIn("rotation_angle", recomputed["curve_metadata"][0]["selection"])

    def test_source_scope_and_rotation_are_frozen_in_page_params(self):
        bounds = (1, 3, 0, 2, 0, 4)
        descriptor = DataScopeDescriptor(
            "roi-frozen", self.raw_data.shape, bounds, generation=4
        )
        full = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        compact = full[1:4, 0:3, :]
        selection = CropSelection(
            "3d", ("X", "Y", "E"), (-0.5, 1.0, 0.0, 3.0, -2.0, 2.0)
        )
        host = self._home_host(
            [("box", selection)],
            context_data=ScopedSpatialArray(compact, descriptor),
        )
        host.scope_descriptor = descriptor
        host.rotation_angle = 23.5

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        self.assertEqual(page.params["source_rotation_angle"], 23.5)
        self.assertEqual(len(host.scope_metadata_calls), 1)
        self.assertIs(host.scope_metadata_calls[0][0], descriptor)
        self.assertIs(host.scope_metadata_calls[0][1], self.coords)
        metadata = page.params["source_scope_metadata"]
        np.testing.assert_array_equal(metadata["data_scope_id"], ["roi-frozen"])
        np.testing.assert_array_equal(metadata["data_scope_label"], ["ROI #4"])
        np.testing.assert_array_equal(metadata["roi_index_bounds"], bounds)
        np.testing.assert_allclose(
            metadata["roi_physical_bounds"],
            [-0.5, 1.0, 0.0, 3.0, 2.0, -2.0],
        )
        host.rotation_angle = 80.0
        host.scope_descriptor = None
        self.assertEqual(page.params["source_rotation_angle"], 23.5)
        np.testing.assert_array_equal(metadata["data_scope_id"], ["roi-frozen"])

    def test_disjoint_coordinate_or_roi_selection_never_creates_partial_page(self):
        valid = CropSelection("2d", ("X", "Y"), (-1.0, -0.5, 0.0, 1.0))
        disjoint = CropSelection("2d", ("X", "Y"), (10.0, 11.0, 0.0, 1.0))
        host = self._axis_host([("valid", valid), ("disjoint", disjoint)])

        self.assertIsNone(host._create_edc_result_from_selections())
        self.assertEqual(host.left_workspace.added, [])
        self.assertEqual(host.page_calls, 0)
        self.assertEqual(host.axis_source_calls, 1)

        descriptor = DataScopeDescriptor("roi", self.raw_data.shape, (1, 2, 0, 2, 0, 4))
        roi_data = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)[1:3, 0:3, :]
        outside_roi = CropSelection(
            "3d", ("X", "Y", "E"), (1.0, 1.0, 0.0, 1.0, 0.0, 2.0)
        )
        host = self._home_host(
            [("outside", outside_roi)],
            context_data=ScopedSpatialArray(roi_data, descriptor),
        )
        self.assertIsNone(host._create_edc_result_from_selections())
        self.assertEqual(host.left_workspace.added, [])
        self.assertEqual(host.page_calls, 0)

    def test_all_nan_selection_fails_atomically_but_partial_energy_nans_are_retained(self):
        invalid = CropSelection(
            "3d", ("X", "Y", "E"), (-1.0, -0.5, 0.0, 1.0, -2.0, 2.0)
        )
        valid = CropSelection(
            "3d", ("X", "Y", "E"), (0.5, 1.0, 0.0, 1.0, -2.0, 2.0)
        )
        source = np.sum(self.raw_data[..., 1:3], axis=3, dtype=np.float64)
        invalid_source = source.copy()
        invalid_source[0:2, 0:2, :] = np.nan
        host = self._home_host(
            [("invalid", invalid), ("valid", valid)], context_data=invalid_source
        )

        self.assertIsNone(host._create_edc_result_from_selections())
        self.assertEqual(host.left_workspace.added, [])
        self.assertEqual(host.page_calls, 0)

        partial_source = source.copy()
        partial_source[0:2, 0:2, 1:3] = np.nan
        host = self._home_host([("partial", invalid)], context_data=partial_source)
        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        intensity = np.asarray(
            host.left_workspace.added[0].params["base_curve"]["y_data"]
        )
        self.assertTrue(np.isnan(intensity[1:3]).all())
        self.assertTrue(np.isfinite(intensity[[0, 3, 4]]).all())
        np.testing.assert_allclose(
            intensity[[0, 3, 4]], np.sum(source[0:2, 0:2, :], axis=(0, 1))[[0, 3, 4]]
        )

    def test_rejects_mismatched_axes_or_unsupported_pages_before_building_source(self):
        selection = CropSelection("2d", ("X", "Y"), (-1.0, -0.5, 0.0, 1.0))
        host = self._axis_host([("sel", selection)])
        host.left_workspace.spec.params["axis_index"] = 1
        host.current_render_context["plot_axes"] = {"x_key": "Y", "y_key": "E"}

        self.assertIsNone(host._create_edc_result_from_selections())
        self.assertEqual(host.axis_build_calls, [])
        self.assertEqual(host.axis_source_calls, 0)
        self.assertEqual(host.left_workspace.added, [])

        home_host = self._home_host([("sel", selection)])
        self.assertIsNone(home_host._create_edc_result_from_selections())
        self.assertEqual(home_host.left_workspace.added, [])

    def test_repeated_creation_has_unique_page_and_curve_ids_with_real_parent(self):
        selection = CropSelection(
            "3d", ("X", "Y", "E"), (-1.0, -0.5, 0.0, 1.0, 0.0, 2.0)
        )
        host = self._home_host([("box", selection)])

        first_id = host._create_edc_result_from_selections()
        second_id = host._create_edc_result_from_selections()

        self.assertNotEqual(first_id, second_id)
        first, second = host.left_workspace.added
        self.assertEqual(first.source_page_id, "home")
        self.assertEqual(second.source_page_id, "home")
        self.assertNotEqual(
            first.params["base_curve"]["curve_id"],
            second.params["base_curve"]["curve_id"],
        )
        self.assertEqual(host.seeded, [first_id, second_id])


if __name__ == "__main__":
    unittest.main()
