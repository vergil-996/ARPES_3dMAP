import copy
import unittest

import numpy as np
from scipy.ndimage import rotate as linear_rotate

from bandscope.app.edc_integration import EdcIntegrationMixin
from bandscope.core.crop_model import CropSelection
from bandscope.core.data_scope import (
    DataScopeDescriptor,
    ScopedDataVolume,
    ScopedSpatialArray,
)
from bandscope.ui.result_workspace import AnalysisPageSpec


def _rotated_frame(data, angle):
    return linear_rotate(
        np.asarray(data, dtype=np.float64),
        float(angle),
        axes=(0, 1),
        reshape=False,
        order=1,
        mode="constant",
        cval=0.0,
        prefilter=True,
    )


def _peak_boxes(data):
    remaining = np.max(np.asarray(data), axis=2)
    boxes = []
    for _ in range(2):
        x, y = np.unravel_index(np.argmax(remaining), remaining.shape)
        x_low, x_up = max(0, x - 1), min(data.shape[0] - 1, x + 1)
        y_low, y_up = max(0, y - 1), min(data.shape[1] - 1, y + 1)
        boxes.append((x_low, x_up, y_low, y_up))
        remaining[
            max(0, x - 2):min(data.shape[0], x + 3),
            max(0, y - 2):min(data.shape[1], y + 3),
        ] = -np.inf
    return tuple(boxes)


def _sum_mask_reference(frame, mask, valid_mask):
    mask = np.asarray(mask, dtype=bool) & np.asarray(valid_mask, dtype=bool)
    selected = np.where(mask, frame, np.nan)
    intensity = np.nansum(selected, axis=(0, 1), dtype=np.float64)
    return np.where(np.any(~np.isnan(selected), axis=(0, 1)), intensity, np.nan)


def _curve_reference(frame, box, valid_mask, *, normal_axis=None, normal_bounds=None):
    x_low, x_up, y_low, y_up = box
    mask = np.zeros(frame.shape, dtype=bool)
    mask[x_low:x_up + 1, y_low:y_up + 1, :] = True
    if normal_axis is not None:
        normal_mask = np.zeros(frame.shape, dtype=bool)
        low, up = normal_bounds
        slices = [slice(None)] * 3
        slices[normal_axis] = slice(low, up + 1)
        normal_mask[tuple(slices)] = True
        mask &= normal_mask
    return _sum_mask_reference(frame, mask, valid_mask)


def _creation_grid_mask(shape, box, creation_angle, current_angle):
    x_low, x_up, y_low, y_up = box
    x, y = np.meshgrid(
        np.arange(shape[0], dtype=np.float64),
        np.arange(shape[1], dtype=np.float64),
        indexing="ij",
    )
    center_x, center_y = (shape[0] - 1) / 2.0, (shape[1] - 1) / 2.0
    delta = np.deg2rad(creation_angle - current_angle)
    cosine, sine = np.cos(delta), np.sin(delta)
    creation_x = center_x + cosine * (x - center_x) - sine * (y - center_y)
    creation_y = center_y + sine * (x - center_x) + cosine * (y - center_y)
    xy = (
        (creation_x >= x_low - 1e-9)
        & (creation_x <= x_up + 1e-9)
        & (creation_y >= y_low - 1e-9)
        & (creation_y <= y_up + 1e-9)
    )
    return np.broadcast_to(xy[:, :, None], shape).copy()


def _source_canvas_reference(shape, bounds, angle):
    x_low, x_up, y_low, y_up, energy_low, energy_up = bounds
    x, y = np.meshgrid(
        np.arange(shape[0], dtype=np.float64),
        np.arange(shape[1], dtype=np.float64),
        indexing="ij",
    )
    center_x, center_y = (shape[0] - 1) / 2.0, (shape[1] - 1) / 2.0
    theta = np.deg2rad(angle)
    source_x = (
        center_x
        + np.cos(theta) * (x - center_x)
        + np.sin(theta) * (y - center_y)
    )
    source_y = (
        center_y
        - np.sin(theta) * (x - center_x)
        + np.cos(theta) * (y - center_y)
    )
    xy = (
        (source_x >= x_low - 1e-9)
        & (source_x <= x_up + 1e-9)
        & (source_y >= y_low - 1e-9)
        & (source_y <= y_up + 1e-9)
    )
    energy = np.zeros(shape[2], dtype=bool)
    energy[energy_low:energy_up + 1] = True
    return xy[:, :, None] & energy[None, None, :]


class _Workspace:
    def __init__(self, spec):
        self.spec = spec
        self.added = []

    def current_spec(self):
        return self.spec

    def add_page(self, spec):
        self.added.append(spec)
        self.spec = spec


class _Controller:
    def __init__(self, selections):
        self.selections = tuple(selections)
        self.committed = False

    def commit_edits(self):
        self.committed = True

    def selections_for_page(self, page_id):
        return self.selections


class _RotatedEdcHost(EdcIntegrationMixin):
    def __init__(self, spec, context, selections, data, coords, valid_mask, angle):
        self.left_workspace = _Workspace(spec)
        self.current_render_context = context
        self.crop_controller = _Controller(selections)
        self.raw_data = data
        self.coords = coords
        self.valid_mask = np.asarray(valid_mask, dtype=bool)
        self.rotation_angle = float(angle)
        self.messages = []
        self.compute_angles = []
        self.erased_voxels = []
        self.page_counter = 0

    def _time_indices_for_spec(self, spec):
        interval = (spec.params or {}).get("time_interval")
        if interval is not None:
            return int(interval["low"]), int(interval["up"])
        return (
            int(spec.params.get("source_t_low", 0)),
            int(spec.params.get("source_t_up", 0)),
        )

    def _source_context(self, spec):
        low, up = self._time_indices_for_spec(spec)
        if isinstance(self.raw_data, ScopedDataVolume):
            compact = self.raw_data.time_integral(low, up)
            source = np.zeros(self.raw_data.shape[:3], dtype=np.float64)
            source[self.raw_data.descriptor.spatial_slices] = np.asarray(compact)
        else:
            source = np.sum(
                self.raw_data[..., low:up + 1], axis=3, dtype=np.float64
            )
        rotated = _rotated_frame(source, self.rotation_angle)
        for x, y, energy, frame in self.erased_voxels:
            if low <= frame <= up:
                rotated[x, y, energy] = np.nan
        return {
            "view": "3d",
            "data": rotated,
            "coords": self.coords,
            "valid_mask": self.valid_mask.copy(),
            "rotation_angle": self.rotation_angle,
        }

    def _compute_render_context(self, spec):
        self.compute_angles.append(self.rotation_angle)
        return self._source_context(spec)

    def _get_display_state_for_spec(self, spec):
        return self.raw_data, self.coords

    def _resolved_axis_integral_params(self, spec):
        return copy.deepcopy(spec.params)

    def _build_axis_source_spec_from_params(self, params):
        return AnalysisPageSpec(
            "axis-source",
            "Axis source",
            "axis_integral",
            "test",
            params=copy.deepcopy(params),
        )

    def _get_3d_source_context_for_axis(self, spec, raw_data):
        self.compute_angles.append(self.rotation_angle)
        return self._source_context(spec)

    def _axis_plot_info(self, axis_index):
        keys = tuple(
            key for index, key in enumerate(("X", "Y", "E")) if index != axis_index
        )
        return {"x_key": keys[0], "y_key": keys[1]}

    def _make_page_id(self):
        self.page_counter += 1
        return f"edc-{self.page_counter}"

    @staticmethod
    def _make_unique_page_title(title):
        return title

    @staticmethod
    def _seed_control_state_for_spec(spec):
        pass

    def _show_message(self, title, text):
        self.messages.append((title, text))


class RotatedEdcTests(unittest.TestCase):
    angle = 30.0

    def setUp(self):
        shape = (9, 7, 4, 2)
        x = np.arange(shape[0], dtype=np.float64)[:, None] - 4.0
        y = np.arange(shape[1], dtype=np.float64)[None, :] - 3.0
        first = np.exp(-((x - (-1.7)) ** 2 + (y - 0.8) ** 2) / 0.35)
        second = np.exp(-((x - 1.8) ** 2 + (y - (-1.4)) ** 2) / 0.45)
        energy_first = np.array([0.7, 1.4, 2.1, 1.1])
        energy_second = np.array([1.3, 0.6, 1.8, 2.4])
        base = (
            4.0 * first[:, :, None] * energy_first
            + 3.0 * second[:, :, None] * energy_second
        )
        self.data = np.stack((base, 2.75 * base), axis=3)
        self.coords = {
            "X": np.arange(shape[0], dtype=np.float64) - 4.0,
            "Y": np.arange(shape[1], dtype=np.float64) - 3.0,
            "E": np.array([1.0, 0.5, 0.0, -0.5]),
            "delay": np.array([0.0, 1.0]),
        }
        self.frames = tuple(
            _rotated_frame(self.data[..., index], self.angle) for index in range(2)
        )
        self.boxes = _peak_boxes(self.frames[0])
        self.valid_mask = np.ones(shape[:3], dtype=bool)
        first_box = self.boxes[0]
        self.valid_mask[first_box[0], first_box[2], 1] = False
        second_box = self.boxes[1]
        self.frames[1][second_box[0], second_box[2], 2] = np.nan
        self.erased_voxels = [(second_box[0], second_box[2], 2, 1)]

    def _selection(
        self,
        box,
        *,
        view="3d",
        axes=("X", "Y", "E"),
        rotation_angle=None,
        logical_bounds=None,
    ):
        x_low, x_up, y_low, y_up = box
        ranges = {
            "X": (self.coords["X"][x_low], self.coords["X"][x_up]),
            "Y": (self.coords["Y"][y_low], self.coords["Y"][y_up]),
            "E": (self.coords["E"][-1], self.coords["E"][0]),
        }
        return CropSelection(
            view,
            axes,
            tuple(value for key in axes for value in ranges[key]),
            rotation_angle=rotation_angle,
            logical_bounds=logical_bounds,
        )

    def _home_host(self, selections, *, valid_mask=None):
        spec = AnalysisPageSpec(
            "home",
            "Home",
            "home",
            "test",
            params={
                "source_t_low": 0,
                "source_t_up": 0,
                "time_interval": {"low": 0.0, "up": 0.0},
            },
        )
        mask = self.valid_mask if valid_mask is None else valid_mask
        host = _RotatedEdcHost(
            spec,
            {"view": "3d"},
            selections,
            self.data,
            self.coords,
            mask,
            self.angle,
        )
        host.erased_voxels = list(self.erased_voxels)
        host.current_render_context = host._source_context(spec)
        return host

    def _live_scope_bounds(self, energy_bounds=(1, 2)):
        x_low, x_up, y_low, y_up = _peak_boxes(self.data[..., 0])[0]
        return (
            max(0, x_low - 1),
            min(self.data.shape[0] - 1, x_up + 1),
            max(0, y_low - 1),
            min(self.data.shape[1] - 1, y_up + 1),
            *energy_bounds,
        )

    def _scoped_home_host(
        self,
        bounds,
        selections,
        *,
        zero=False,
        use_context_bounds=False,
        spatial_mask=None,
    ):
        descriptor = DataScopeDescriptor("compact-live", self.data.shape, bounds)
        compact = self.data[descriptor.spatial_slices + (slice(None),)].copy()
        if zero:
            compact.fill(0.0)
        host = self._home_host(selections)
        host.raw_data = ScopedDataVolume(descriptor, compact)
        host.valid_mask = (
            np.ones(self.data.shape[:3], dtype=bool)
            if spatial_mask is None
            else np.asarray(spatial_mask, dtype=bool).copy()
        )
        context = host._source_context(host.left_workspace.spec)
        context.pop("valid_mask", None)
        if use_context_bounds:
            context["source_data_bounds"] = tuple(bounds)
        if spatial_mask is not None:
            context["spatial_valid_mask"] = np.asarray(spatial_mask, dtype=bool)
        host.current_render_context = context
        return host

    def _axis_host(self, selection, box, axis_index):
        plot_axes = self._plot_axes(axis_index)
        normal_axis = {0: 0, 1: 1, 2: 2}[axis_index]
        normal_bounds = (
            (box[0], box[1]),
            (box[2], box[3]),
            (0, self.data.shape[2] - 1),
        )[normal_axis]
        spec = AnalysisPageSpec(
            "axis",
            "Axis",
            "axis_integral",
            "test",
            params={
                "axis_index": axis_index,
                "low": normal_bounds[0],
                "up": normal_bounds[1],
                "mid": round(sum(normal_bounds) / 2),
                "source_t_low": 0,
                "source_t_up": 0,
                "time_interval": {"low": 0.0, "up": 0.0},
            },
        )
        context = {
            "view": "2d",
            "data": np.zeros((2, 2)),
            "coords": self.coords,
            "slice_info": {
                "axis": axis_index,
                "mode": "integral",
                "range": normal_bounds,
            },
            "plot_axes": plot_axes,
            "rotation_angle": self.angle,
        }
        host = _RotatedEdcHost(
            spec,
            context,
            [("axis-selection", selection)],
            self.data,
            self.coords,
            self.valid_mask,
            self.angle,
        )
        host.erased_voxels = list(self.erased_voxels)
        return host

    @staticmethod
    def _plot_axes(axis_index):
        keys = tuple(
            key for index, key in enumerate(("X", "Y", "E")) if index != axis_index
        )
        return {"x_key": keys[0], "y_key": keys[1]}

    @staticmethod
    def _set_frame(spec, frame):
        spec.params["source_t_low"] = frame
        spec.params["source_t_up"] = frame
        spec.params["time_interval"] = {"low": float(frame), "up": float(frame)}

    def test_home_multiple_selections_match_independent_rotated_grid_and_recompute(self):
        host = self._home_host(
            [
                ("first", self._selection(self.boxes[0])),
                ("second", self._selection(self.boxes[1])),
            ]
        )

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        self.assertEqual(page.page_kind, "curve_comparison_1d")
        curves = [page.params["base_curve"], *page.params["overlay_curves"]]
        for curve, box in zip(curves, self.boxes):
            np.testing.assert_allclose(
                curve["y_data"],
                _curve_reference(self.frames[0], box, self.valid_mask),
                rtol=1e-12,
                atol=1e-12,
            )
        self.assertEqual(page.params["source_rotation_angle"], self.angle)
        for metadata in page.params["curve_metadata"]:
            self.assertEqual(metadata["selection"]["rotation_angle"], self.angle)
            self.assertEqual(len(metadata["selection"]["logical_bounds"]), 6)

        self._set_frame(page, 1)
        host.rotation_angle = -25.0
        recomputed = host._recompute_edc_time_curves(page)

        self.assertIsNotNone(recomputed)
        self.assertEqual(host.rotation_angle, -25.0)
        self.assertTrue(all(angle == self.angle for angle in host.compute_angles))
        for curve, box in zip(recomputed["curves"], self.boxes):
            np.testing.assert_allclose(
                curve["y_data"],
                _curve_reference(self.frames[1], box, self.valid_mask),
                rtol=1e-12,
                atol=1e-12,
            )

    def test_xy_xz_and_yz_sources_use_selection_and_frozen_normal_interval(self):
        box = self.boxes[0]
        cases = (
            (2, self._selection(box, view="2d", axes=("X", "Y")), 2, (0, 3)),
            (1, self._selection(box, view="2d", axes=("X", "E")), 1, (box[2], box[3])),
            (0, self._selection(box, view="2d", axes=("E", "Y")), 0, (box[0], box[1])),
        )
        for axis_index, selection, normal_axis, normal_bounds in cases:
            with self.subTest(axis_index=axis_index):
                host = self._axis_host(selection, box, axis_index)
                page_id = host._create_edc_result_from_selections()
                self.assertIsNotNone(page_id)
                page = host.left_workspace.added[0]
                expected = _curve_reference(
                    self.frames[0],
                    box,
                    self.valid_mask,
                    normal_axis=normal_axis,
                    normal_bounds=normal_bounds,
                )
                np.testing.assert_allclose(
                    page.params["base_curve"]["y_data"], expected,
                    rtol=1e-12,
                    atol=1e-12,
                )

                self._set_frame(page, 1)
                host.rotation_angle = 65.0
                recomputed = host._recompute_edc_time_curves(page)
                self.assertIsNotNone(recomputed)
                expected = _curve_reference(
                    self.frames[1],
                    box,
                    self.valid_mask,
                    normal_axis=normal_axis,
                    normal_bounds=normal_bounds,
                )
                np.testing.assert_allclose(
                    recomputed["curves"][0]["y_data"], expected,
                    rtol=1e-12,
                    atol=1e-12,
                )
                self.assertEqual(host.rotation_angle, 65.0)

    def test_axis_crop_history_replays_creation_geometry_on_frozen_grid(self):
        original_box = _peak_boxes(self.data[..., 0])[0]
        crop = self._selection(
            original_box,
            view="2d",
            axes=("X", "Y"),
            rotation_angle=0.0,
            logical_bounds=original_box,
        )
        full_box = (0, self.data.shape[0] - 1, 0, self.data.shape[1] - 1)
        selection = self._selection(full_box, view="2d", axes=("X", "Y"))
        host = self._axis_host(selection, full_box, 2)
        host.left_workspace.spec.page_kind = "axis_integral_crop"
        host.left_workspace.spec.params.update(
            crop_base_rect=None,
            crop_regions=[crop.to_dict()],
        )
        crop_mask = _creation_grid_mask(
            self.data.shape[:3], original_box, 0.0, self.angle
        )

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        np.testing.assert_allclose(
            page.params["base_curve"]["y_data"],
            _sum_mask_reference(self.frames[0], crop_mask, self.valid_mask),
            rtol=1e-12,
            atol=1e-12,
        )
        samples = np.where(crop_mask)
        expected_bounds = tuple(
            int(value)
            for axis in range(3)
            for value in (samples[axis].min(), samples[axis].max())
        )
        self.assertEqual(page.params["source_data_bounds"], expected_bounds)

        self._set_frame(page, 1)
        host.rotation_angle = -40.0
        recomputed = host._recompute_edc_time_curves(page)
        self.assertIsNotNone(recomputed)
        np.testing.assert_allclose(
            recomputed["curves"][0]["y_data"],
            _sum_mask_reference(self.frames[1], crop_mask, self.valid_mask),
            rtol=1e-12,
            atol=1e-12,
        )
        self.assertEqual(host.rotation_angle, -40.0)
        self.assertTrue(all(angle == self.angle for angle in host.compute_angles))

    def test_full_rotated_grid_recovers_raw_scope_canvas_and_energy(self):
        bounds = self._live_scope_bounds()
        full_box = (0, self.data.shape[0] - 1, 0, self.data.shape[1] - 1)
        canvas = _source_canvas_reference(self.data.shape[:3], bounds, self.angle)
        spatial_mask = np.ones(self.data.shape[:3], dtype=bool)
        spatial_mask[tuple(np.argwhere(canvas)[0])] = False

        for use_context_bounds in (False, True):
            with self.subTest(use_context_bounds=use_context_bounds):
                host = self._scoped_home_host(
                    bounds,
                    [("all", self._selection(full_box))],
                    use_context_bounds=use_context_bounds,
                    spatial_mask=spatial_mask,
                )
                first_frame = np.asarray(host.current_render_context["data"]).copy()
                page_id = host._create_edc_result_from_selections()

                self.assertIsNotNone(page_id)
                page = host.left_workspace.added[0]
                np.testing.assert_allclose(
                    page.params["base_curve"]["y_data"],
                    _sum_mask_reference(first_frame, canvas, spatial_mask),
                    rtol=1e-12,
                    atol=1e-12,
                    equal_nan=True,
                )
                intensity = np.asarray(page.params["base_curve"]["y_data"])
                self.assertTrue(np.isnan(intensity[[0, 3]]).all())
                self.assertTrue(np.isfinite(intensity[1:3]).all())

                self._set_frame(page, 1)
                host.rotation_angle = -15.0
                recomputed = host._recompute_edc_time_curves(page)
                self.assertIsNotNone(recomputed)
                compact_second = host.raw_data.values[..., 1]
                full_second = np.zeros(self.data.shape[:3], dtype=np.float64)
                full_second[host.raw_data.descriptor.spatial_slices] = compact_second
                second_frame = _rotated_frame(full_second, self.angle)
                np.testing.assert_allclose(
                    recomputed["curves"][0]["y_data"],
                    _sum_mask_reference(second_frame, canvas, spatial_mask),
                    rtol=1e-12,
                    atol=1e-12,
                    equal_nan=True,
                )
                self.assertEqual(host.rotation_angle, -15.0)

    def test_full_rotated_grid_rejects_zero_only_selection_outside_raw_scope(self):
        bounds = self._live_scope_bounds(energy_bounds=(0, 3))
        canvas = _source_canvas_reference(self.data.shape[:3], bounds, self.angle)
        probe = self._scoped_home_host(bounds, [("probe", self._selection((0, 1, 0, 1)))])
        rotated = np.asarray(probe.current_render_context["data"])
        outside = None
        for x_low in range(self.data.shape[0] - 1):
            for y_low in range(self.data.shape[1] - 1):
                box = (x_low, x_low + 1, y_low, y_low + 1)
                selected = np.s_[x_low:x_low + 2, y_low:y_low + 2, :]
                if not canvas[selected].any() and not np.any(rotated[selected]):
                    outside = box
                    break
            if outside is not None:
                break
        self.assertIsNotNone(outside)
        host = self._scoped_home_host(
            bounds,
            [("outside", self._selection(outside))],
            use_context_bounds=True,
        )

        self.assertIsNone(host._create_edc_result_from_selections())
        self.assertEqual(host.left_workspace.added, [])
        self.assertTrue(host.messages)

    def test_full_rotated_grid_allows_real_zero_inside_raw_scope(self):
        bounds = self._live_scope_bounds(energy_bounds=(0, 3))
        full_box = (0, self.data.shape[0] - 1, 0, self.data.shape[1] - 1)
        host = self._scoped_home_host(
            bounds,
            [("zero", self._selection(full_box))],
            zero=True,
        )

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        np.testing.assert_array_equal(
            host.left_workspace.added[0].params["base_curve"]["y_data"],
            np.zeros(self.data.shape[2]),
        )

    def test_compact_rotated_scope_uses_absolute_indices_and_compact_mask(self):
        box = self.boxes[0]
        bounds = (box[0], box[1], box[2], box[3], 0, 3)
        descriptor = DataScopeDescriptor("rotated-roi", self.data.shape, bounds)
        compact = self.frames[0][descriptor.spatial_slices]
        compact_mask = self.valid_mask[descriptor.spatial_slices]
        selection = self._selection(box)
        host = self._home_host([("compact", selection)])
        host.current_render_context.update(
            data=ScopedSpatialArray(compact, descriptor),
            valid_mask=compact_mask,
            data_bounds=bounds,
            full_shape=self.data.shape[:3],
        )

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        page = host.left_workspace.added[0]
        np.testing.assert_allclose(
            page.params["base_curve"]["y_data"],
            _curve_reference(self.frames[0], box, self.valid_mask),
            rtol=1e-12,
            atol=1e-12,
        )
        self.assertEqual(page.params["source_data_bounds"], bounds)

    def test_real_zero_is_valid_but_fully_masked_selection_is_rejected(self):
        zero_box = (0, 1, 5, 6)
        frames = list(self.frames)
        for frame in frames:
            frame[0:2, 5:7, :] = 0.0
        self.frames = tuple(frames)
        host = self._home_host([("zero", self._selection(zero_box))])

        page_id = host._create_edc_result_from_selections()

        self.assertIsNotNone(page_id)
        np.testing.assert_array_equal(
            host.left_workspace.added[0].params["base_curve"]["y_data"],
            np.zeros(4),
        )

        empty_mask = self.valid_mask.copy()
        empty_mask[0:2, 5:7, :] = False
        host = self._home_host(
            [("empty", self._selection(zero_box))], valid_mask=empty_mask
        )
        self.assertIsNone(host._create_edc_result_from_selections())
        self.assertEqual(host.left_workspace.added, [])
        self.assertTrue(host.messages)


if __name__ == "__main__":
    unittest.main()
