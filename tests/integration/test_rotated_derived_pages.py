import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication
from scipy.ndimage import rotate as scipy_rotate

from bandscope.core.axis_interval import AxisInterval
from bandscope.core.crop_model import CropSelection
from bandscope.core.path_slice import PathNode, PathSliceModel, write_draft
from bandscope.processing.denoise_engines import DenoiseEngines
from bandscope.processing.path_slice import MomentumCalibration, PathSliceSampler
from tests.support.view_recipe import control_window


NX, NY, NE, NT = 7, 9, 5, 2
ANGLE = 30.0


def make_data():
    raw = np.zeros((NX, NY, NE, NT), dtype=np.float32)
    for frame in range(NT):
        for energy in range(NE):
            raw[1, 2, energy, frame] = (energy + 1) * (frame + 1)
            raw[4, 6, energy, frame] = (2 * energy + 1) * (2 * frame + 2)
    coords = {
        "X": np.linspace(-1.0, 1.0, NX),
        "Y": np.linspace(-2.0, 2.0, NY),
        "E": np.linspace(-0.4, 0.4, NE),
        "delay": np.asarray([0.0, 100.0]),
    }
    return raw, coords


def rotate_linear(frame, angle):
    return scipy_rotate(
        np.asarray(frame, dtype=np.float32), float(angle), axes=(0, 1),
        reshape=False, order=1, mode="constant", cval=0.0, prefilter=True,
    )


def selection(coords, axes, logical, angle, operation="crop"):
    bounds = []
    for key, (low, up) in zip(axes, zip(logical[::2], logical[1::2])):
        bounds.extend((float(coords[key][low]), float(coords[key][up])))
    view = "3d" if len(axes) == 3 else "2d"
    return CropSelection(
        view,
        tuple(axes),
        tuple(bounds),
        operation=operation,
        rotation_angle=float(angle),
        logical_bounds=tuple(logical),
    ).to_dict()


def explicit_mask(shape, region, current_angle, normal_index_bounds=None):
    axes = tuple(region["axes"])
    logical = tuple(region["logical_bounds"])
    ranges = dict(zip(axes, zip(logical[::2], logical[1::2])))
    missing = next((key for key in ("X", "Y", "E") if key not in axes), None)
    if missing is not None:
        if normal_index_bounds is None:
            normal = (0, shape["XYZ".index(missing)] - 1)
        else:
            normal = tuple(normal_index_bounds)
        ranges[missing] = normal
    mask = np.ones(shape, dtype=bool)
    creation = region.get("rotation_angle")
    rotated_xy = (
        creation is not None
        and "X" in axes
        and "Y" in axes
        and abs(float(current_angle) - float(creation)) >= 1e-12
    )
    for axis, key in enumerate(("X", "Y", "E")):
        if rotated_xy and key in {"X", "Y"}:
            continue
        low, up = ranges[key]
        axis_mask = np.zeros(shape[axis], dtype=bool)
        axis_mask[low:up + 1] = True
        reshape = [1, 1, 1]
        reshape[axis] = shape[axis]
        mask &= axis_mask.reshape(reshape)
    if rotated_xy:
        nx, ny = shape[:2]
        x, y = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
        cx, cy = (nx - 1) / 2.0, (ny - 1) / 2.0
        theta = np.deg2rad(float(creation) - float(current_angle))
        creation_x = cx + np.cos(theta) * (x - cx) - np.sin(theta) * (y - cy)
        creation_y = cy + np.sin(theta) * (x - cx) + np.cos(theta) * (y - cy)
        x_low, x_up = ranges["X"]
        y_low, y_up = ranges["Y"]
        mask &= (
            (creation_x >= x_low)
            & (creation_x <= x_up)
            & (creation_y >= y_low)
            & (creation_y <= y_up)
        )[..., None]
    return mask


def combined_mask(shape, regions, current_angle, normal_index_bounds=None):
    mask = np.ones(shape, dtype=bool)
    for region in regions:
        selected = explicit_mask(shape, region, current_angle, normal_index_bounds)
        if region.get("operation") == "erase":
            mask &= ~selected
        else:
            mask &= selected
    return mask


def canvas_reference(shape, angle, source_bounds):
    nx, ny = shape[:2]
    x, y = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    cx, cy = (nx - 1) / 2.0, (ny - 1) / 2.0
    theta = np.deg2rad(float(angle))
    source_x = cx + np.cos(theta) * (x - cx) + np.sin(theta) * (y - cy)
    source_y = cy - np.sin(theta) * (x - cx) + np.cos(theta) * (y - cy)
    x0, x1, y0, y1, e0, e1 = source_bounds
    xy = (
        (source_x >= x0) & (source_x <= x1)
        & (source_y >= y0) & (source_y <= y1)
    )
    energy = np.zeros(shape[2], dtype=bool)
    energy[e0:e1 + 1] = True
    return xy[:, :, None] & energy[None, None, :]


def cropped_2d_reference(source, regions, current_angle, normal_index_bounds, plot_axes):
    data = np.asarray(source, dtype=np.float64).copy()
    axis_by_key = {"X": 0, "Y": 1, "E": 2}
    remaining = [axis_by_key[plot_axes[0]], axis_by_key[plot_axes[1]]]
    rect = [0, data.shape[0] - 1, 0, data.shape[1] - 1]
    normal_axis = next(axis for axis in range(3) if axis not in remaining)
    for region in regions:
        full_mask = explicit_mask(
            (NX, NY, NE), region, current_angle, normal_index_bounds
        )
        mask = np.take(full_mask, normal_index_bounds[0], axis=normal_axis)
        selected = mask[rect[0]:rect[1] + 1, rect[2]:rect[3] + 1]
        if region.get("operation") == "erase":
            data[selected] = np.nan
            continue
        indices = np.where(selected)
        if not indices[0].size:
            return np.zeros((0, 0)), rect
        x0, x1 = int(indices[0].min()), int(indices[0].max())
        y0, y1 = int(indices[1].min()), int(indices[1].max())
        data = np.where(selected[x0:x1 + 1, y0:y1 + 1], data[x0:x1 + 1, y0:y1 + 1], 0.0)
        rect = [rect[0] + x0, rect[0] + x1, rect[2] + y0, rect[2] + y1]
    return data, rect


def second_derivative_reference(data, energy, axis=1):
    source = np.asarray(data, dtype=np.float64)
    first = np.gradient(source, energy, axis=axis, edge_order=2)
    result = -np.gradient(first, energy, axis=axis, edge_order=2)
    result[result < 0] = 0
    return result


class RotatedDerivedPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw, self.coords = make_data()
        path = Path(self.temp.name) / "two-peaks.npz"
        np.savez(
            path,
            sample=self.raw,
            kx=self.coords["X"],
            ky=self.coords["Y"],
            E=self.coords["E"],
            time=self.coords["delay"],
        )
        self.window = control_window()
        self.addCleanup(self.window.close)
        self.assertTrue(self.window.load_data(str(path)))
        identity = self.window.core.source_identity
        if identity is not None and identity.future is not None:
            identity.future.result(timeout=30)
        self.home = self.window.left_workspace.current_spec()
        self.crop = selection(
            self.coords, ("X", "Y", "E"), (1, 3, 0, 3, 0, NE - 1), ANGLE
        )
        self.erase = selection(
            self.coords, ("X", "Y", "E"), (2, 2, 1, 2, 0, NE - 1), -20.0,
            operation="erase",
        )

    def set_frame(self, spec, frame):
        spec.params["time_interval"] = AxisInterval.from_indices(
            self.coords["delay"], frame, frame, locked=True, axis_key="delay"
        ).as_dict()

    def set_angle(self, angle):
        self.window.timeline_bar.set_rotation_angle(angle)
        self.window.on_rotation_angle_changed()

    def context(self, spec):
        raw, coords = self.window._get_display_state_for_spec(spec)
        return self.window._compute_render_context(spec)

    def test_clip_ranges_slice_dos_energy_dos_and_log_use_creation_grid(self):
        clip = [1, 3, 0, 3, 0, NE - 1]
        self.home.params["clip_ranges"] = clip
        self.window.clip_ranges = clip
        self.window.rotation_angle = ANGLE
        self.set_frame(self.home, 0)
        slice_spec = self.window._build_slice_dos_spec()
        dos_spec = self.window._build_energy_dos_spec()
        self.assertEqual(slice_spec.params["source_rotation_angle"], ANGLE)
        self.assertEqual(dos_spec.params["source_rotation_angle"], ANGLE)
        self.window.rotation_angle = -20.0

        slice_curve = self.context(slice_spec)["y_data"]
        dos_curve = self.context(dos_spec)["y_data"]
        expected_slice = []
        expected_dos = []
        for frame in range(NT):
            rotated = rotate_linear(self.raw[..., frame], ANGLE)
            selected = rotated[1:4, 0:4, :]
            expected_slice.append(np.sum(selected, dtype=np.float64))
            expected_dos.append(np.sum(selected, axis=(0, 1), dtype=np.float64))
        np.testing.assert_allclose(slice_curve, expected_slice, rtol=2e-6, atol=2e-6)
        np.testing.assert_allclose(dos_curve, expected_dos[0], rtol=2e-6, atol=2e-6)

        self.window.left_workspace.add_page(slice_spec)
        self.window.current_render_context = self.context(slice_spec)
        self.assertTrue(self.window._apply_log_to_current_curves())
        log_spec = self.window.left_workspace.current_spec()
        np.testing.assert_allclose(
            self.context(log_spec)["y_data"], np.log10(expected_slice),
            rtol=2e-6, atol=2e-6,
        )

    def test_xy_xz_yz_axis_integrals_rebuild_at_current_angle_with_crop_history(self):
        self.home.params["crop_regions"] = [self.crop, self.erase]
        for axis in (0, 1, 2):
            with self.subTest(axis=axis):
                self.window.left_workspace.activate_page(self.home.page_id)
                self.set_angle(ANGLE)
                spec = self.window._build_canvas_axis_slice_spec(self.home, axis)
                self.window.left_workspace.add_page(spec)
                fixed_range = tuple(int(spec.params[key]) for key in ("low", "up", "mid"))
                fixed_interval = dict(spec.params["axis_interval"])
                self.assertEqual(spec.params["source_3d"]["rotation_angle"], ANGLE)
                results = []
                for current_angle in (-20.0, 0.0, 47.5):
                    with self.subTest(axis=axis, current_angle=current_angle):
                        self.set_angle(current_angle)
                        context = self.context(spec)
                        actual = context["data"]
                        rotated = rotate_linear(self.raw[..., 0], current_angle)
                        mask = combined_mask(
                            self.raw.shape[:3], [self.crop, self.erase], current_angle
                        )
                        low, up = fixed_range[:2]
                        normal = np.zeros(self.raw.shape[axis], dtype=bool)
                        normal[low:up + 1] = True
                        reshape = [1, 1, 1]
                        reshape[axis] = normal.size
                        mask &= normal.reshape(reshape)
                        expected = np.sum(np.where(mask, rotated, 0.0), axis=axis)
                        np.testing.assert_allclose(actual, expected, rtol=2e-6, atol=2e-6)
                        self.assertEqual(context["rotation_angle"], current_angle)
                        self.assertEqual(
                            tuple(int(spec.params[key]) for key in ("low", "up", "mid")),
                            fixed_range,
                        )
                        self.assertEqual(spec.params["axis_interval"], fixed_interval)
                        self.assertEqual(spec.params["source_3d"]["rotation_angle"], ANGLE)
                        results.append(actual)
                self.assertTrue(any(
                    not np.allclose(results[0], result) for result in results[1:]
                ))

    def test_selection_slice_dos_replays_frozen_source_per_frame(self):
        self.home.params["crop_regions"] = [self.crop, self.erase]
        self.window.rotation_angle = ANGLE
        self.assertTrue(self.window._integration_action_available("builtin:slice_dos", self.home))
        volume_spec = self.window._build_slice_dos_spec()
        self.window.rotation_angle = -20.0
        volume_actual = self.context(volume_spec)["y_data"]
        volume_expected = []
        for frame in range(NT):
            rotated = rotate_linear(self.raw[..., frame], ANGLE)
            mask = combined_mask(self.raw.shape[:3], [self.crop, self.erase], ANGLE)
            volume_expected.append(np.sum(np.where(mask, rotated, 0.0), dtype=np.float64))
        np.testing.assert_allclose(volume_actual, volume_expected, rtol=2e-6, atol=2e-6)

        self.set_angle(ANGLE)
        axis = self.window._build_canvas_axis_slice_spec(self.home, 1)
        self.window.left_workspace.add_page(axis)
        active = CropSelection.from_dict(selection(
            self.coords, ("X", "E"), (1, 3, 0, NE - 1), ANGLE
        ))
        context = self.window._compute_render_context(axis)
        self.window.crop_controller.activate(axis.page_id, context)
        self.window.crop_controller.set_selection(active, notify=False)
        spec = self.window._build_slice_dos_spec()
        frozen = spec.params["slice_source"]["source_page"]
        self.assertEqual(frozen["rotation_angle"], ANGLE)
        self.assertEqual(frozen["params"]["crop_regions"][-1]["rotation_angle"], ANGLE)
        self.window.rotation_angle = -20.0

        actual = self.context(spec)["y_data"]
        expected = []
        regions = [self.crop, self.erase, active.to_dict()]
        low, up = int(axis.params["low"]), int(axis.params["up"])
        for frame in range(NT):
            rotated = rotate_linear(self.raw[..., frame], ANGLE)
            mask = combined_mask(
                self.raw.shape[:3], regions, ANGLE, normal_index_bounds=(low, up)
            )
            normal = np.zeros(NY, dtype=bool)
            normal[low:up + 1] = True
            mask &= normal[None, :, None]
            expected.append(np.sum(np.where(mask, rotated, 0.0), dtype=np.float64))
        np.testing.assert_allclose(actual, expected, rtol=2e-6, atol=2e-6)

    def test_crop_erase_xz_energy_dos_and_second_derivative_share_source(self):
        self.home.params["crop_regions"] = [self.crop, self.erase]
        self.set_angle(ANGLE)
        axis = self.window._build_canvas_axis_slice_spec(self.home, 1)
        axis_erase = selection(
            self.coords, ("X", "E"), (2, 3, 1, 3), ANGLE, operation="erase"
        )
        axis.params["crop_regions"] = [axis_erase]
        self.window.left_workspace.add_page(axis)
        self.set_frame(axis, 0)
        dos_spec = self.window._build_energy_dos_spec()
        derivative_spec = self.window._build_second_derivative_spec()
        self.window.rotation_angle = -20.0

        low, up = int(axis.params["low"]), int(axis.params["up"])
        rotated = rotate_linear(self.raw[..., 0], ANGLE)
        mask = combined_mask(self.raw.shape[:3], [self.crop, self.erase], ANGLE)
        normal = np.zeros(NY, dtype=bool)
        normal[low:up + 1] = True
        mask &= normal[None, :, None]
        source = np.sum(np.where(mask, rotated, 0.0), axis=1, dtype=np.float64)
        cropped, rect = cropped_2d_reference(
            source, [axis_erase], ANGLE, (low, up), ("X", "E")
        )
        energy = self.coords["E"][rect[2]:rect[3] + 1]
        np.testing.assert_allclose(
            self.context(dos_spec)["y_data"], np.nansum(cropped, axis=0),
            rtol=2e-6, atol=2e-6,
        )
        np.testing.assert_allclose(
            self.context(derivative_spec)["data"],
            second_derivative_reference(cropped, energy),
            rtol=2e-6, atol=1e-5, equal_nan=True,
        )

    def test_3d_second_derivative_crops_before_differentiating(self):
        self.home.params["crop_regions"] = [self.crop, self.erase]
        self.window.rotation_angle = ANGLE
        self.set_frame(self.home, 0)
        self.window._ask_second_derivative_axis = lambda: 2
        spec = self.window._build_second_derivative_spec()
        self.window.rotation_angle = -20.0

        rotated = rotate_linear(self.raw[..., 0], ANGLE).astype(np.float64)
        crop_mask = explicit_mask(self.raw.shape[:3], self.crop, ANGLE)
        indices = np.where(crop_mask)
        bounds = tuple(
            (int(values.min()), int(values.max())) for values in indices
        )
        cropped = np.where(
            crop_mask[bounds[0][0]:bounds[0][1] + 1,
                      bounds[1][0]:bounds[1][1] + 1,
                      bounds[2][0]:bounds[2][1] + 1],
            rotated[bounds[0][0]:bounds[0][1] + 1,
                    bounds[1][0]:bounds[1][1] + 1,
                    bounds[2][0]:bounds[2][1] + 1],
            0.0,
        )
        erase_mask = explicit_mask(self.raw.shape[:3], self.erase, ANGLE)
        local_erase = erase_mask[bounds[0][0]:bounds[0][1] + 1,
                                 bounds[1][0]:bounds[1][1] + 1,
                                 bounds[2][0]:bounds[2][1] + 1]
        cropped[local_erase] = np.nan
        energy = self.coords["E"][bounds[2][0]:bounds[2][1] + 1]
        expected = second_derivative_reference(cropped, energy, axis=2)
        actual = np.asarray(self.context(spec)["data"])
        np.testing.assert_allclose(actual, expected, rtol=2e-6, atol=2e-6, equal_nan=True)

    def test_erase_xy_path_uses_frozen_angle_and_valid_samples(self):
        path_erase = selection(
            self.coords, ("X", "Y", "E"), (2, 4, 5, 8, 0, NE - 1), -20.0,
            operation="erase",
        )
        self.home.params["crop_regions"] = [path_erase]
        self.set_angle(ANGLE)
        axis = self.window._build_canvas_axis_slice_spec(self.home, 2)
        self.window.left_workspace.add_page(axis)
        write_draft(axis.params, [PathNode(2, 1, "P1"), PathNode(3, 6, "P2")])
        self.window.path_slice_controller.active_source_id = axis.page_id
        page_id = self.window.path_slice_controller.generate_page()
        self.assertIsNotNone(page_id)
        spec = self.window.left_workspace.page_by_id(page_id)
        source = spec.params["path_source"]
        self.assertEqual(source["rotation_angle"], ANGLE)
        self.window.rotation_angle = -20.0
        actual = self.window.path_slice_controller.compute_context(spec)["raw_intensity"]

        volume = rotate_linear(self.raw[..., 0], ANGLE)
        erased = explicit_mask(self.raw.shape[:3], path_erase, ANGLE)
        volume[erased] = np.nan
        model = PathSliceModel.from_params(spec.params)
        calibration = MomentumCalibration.create(
            self.coords["X"], self.coords["Y"],
            x_source=self.window.core.coord_sources.get("X", "index"),
            y_source=self.window.core.coord_sources.get("Y", "index"),
            x_unit=self.window.core.coord_units.get("X"),
            y_unit=self.window.core.coord_units.get("Y"),
        )
        sample = PathSliceSampler().sample(
            volume, self.coords["E"], model, calibration,
            valid_mask=np.ones(volume.shape, dtype=bool),
        )
        np.testing.assert_allclose(
            actual, sample.intensity, rtol=2e-6, atol=2e-6, equal_nan=True
        )

    def test_home_xy_path_replays_rotated_erasure_without_axis_snapshot(self):
        path_erase = selection(
            self.coords, ("X", "Y", "E"), (2, 4, 5, 8, 0, NE - 1), -20.0,
            operation="erase",
        )
        self.home.params["crop_regions"] = [path_erase]
        self.home.params["home_slice_info"] = {
            "axis": 2, "index": 0, "mode": "integral", "range": (0, NE - 1)
        }
        self.window.home_slice_info = dict(self.home.params["home_slice_info"])
        self.window.rotation_angle = ANGLE
        write_draft(self.home.params, [PathNode(2, 1, "P1"), PathNode(3, 6, "P2")])
        self.window.path_slice_controller.active_source_id = self.home.page_id
        page_id = self.window.path_slice_controller.generate_page()
        self.assertIsNotNone(page_id)
        spec = self.window.left_workspace.page_by_id(page_id)
        self.window.rotation_angle = -20.0
        actual = self.window.path_slice_controller.compute_context(spec)["raw_intensity"]

        volume = rotate_linear(self.raw[..., 0], ANGLE)
        erased = explicit_mask(self.raw.shape[:3], path_erase, ANGLE)
        volume[erased] = np.nan
        model = PathSliceModel.from_params(spec.params)
        calibration = MomentumCalibration.create(
            self.coords["X"], self.coords["Y"],
            x_source=self.window.core.coord_sources.get("X", "index"),
            y_source=self.window.core.coord_sources.get("Y", "index"),
            x_unit=self.window.core.coord_units.get("X"),
            y_unit=self.window.core.coord_units.get("Y"),
        )
        sample = PathSliceSampler().sample(
            volume, self.coords["E"], model, calibration,
            valid_mask=np.ones(volume.shape, dtype=bool),
        )
        np.testing.assert_allclose(
            actual, sample.intensity, rtol=2e-6, atol=2e-6, equal_nan=True
        )

    def test_compact_crop_axis_never_reuses_raw_background_prefix(self):
        self.home.params["crop_regions"] = [self.crop]
        self.window.rotation_angle = ANGLE
        parent = self.context(self.home)
        axis = self.window._build_canvas_axis_slice_spec(self.home, 2)
        request = SimpleNamespace(snapshot={"rotation_angle": ANGLE})
        descriptor = self.window._volume_source_descriptor(axis, request)
        self.assertIsNotNone(descriptor)
        self.assertNotEqual(descriptor[1].operation, "prepare_axis_prefix")

        raw, _coords = self.window._get_display_state_for_spec(axis)
        resolved = self.window._resolved_axis_integral_params(axis)
        key = self.window._axis_prefix_cache_key(axis, resolved, raw)
        self.window._axis_prefix_cache = {
            "key": key,
            "page_id": axis.page_id,
            "axis_index": 2,
            "prefix": np.zeros(raw.shape[:3], dtype=np.float32),
        }
        actual = self.context(axis)["data"]

        source = np.nan_to_num(np.asarray(parent["data"], dtype=np.float64), nan=0.0)
        bounds = parent["data_bounds"]
        low = max(int(axis.params["low"]), int(bounds[4]))
        up = min(int(axis.params["up"]), int(bounds[5]))
        expected = np.zeros((NX, NY), dtype=np.float64)
        compact = np.sum(source[:, :, low - bounds[4]:up - bounds[4] + 1], axis=2)
        expected[bounds[0]:bounds[1] + 1, bounds[2]:bounds[3] + 1] = compact
        np.testing.assert_allclose(actual, expected, rtol=2e-6, atol=2e-6)
        self.assertEqual(
            self.window._axis_prefix_cache["prefix"].shape,
            np.asarray(parent["data"]).shape,
        )

    def test_legacy_compact_roi_uses_source_canvas_after_rotation(self):
        bounds = (1, 4, 1, 5, 0, NE - 1)
        descriptor = self.window._register_roi_scope(bounds)
        self.home.data_scope_id = descriptor.scope_id
        self.home.params["clip_ranges"] = list(bounds)
        self.home.params["crop_regions"] = [self.crop]
        self.window.clip_ranges = list(bounds)
        self.window.rotation_angle = ANGLE
        context = self.context(self.home)

        self.assertEqual(context["source_data_bounds"], bounds)
        self.assertEqual(context["full_shape"], self.raw.shape[:3])
        self.assertNotEqual(context["data_bounds"], bounds)
        current = context["data_bounds"]
        slices = tuple(
            slice(current[2 * axis], current[2 * axis + 1] + 1)
            for axis in range(3)
        )
        expected_canvas = canvas_reference(self.raw.shape[:3], ANGLE, bounds)
        np.testing.assert_array_equal(context["canvas_mask"], expected_canvas[slices])

        legacy_axis = SimpleNamespace(
            page_id="legacy-axis",
            page_kind="axis_integral",
            source_page_id=None,
            params={
                "clip_ranges": list(bounds),
                "time_interval": self.home.params.get("time_interval"),
            },
        )
        raw, _coords = self.window._get_display_state_for_spec(self.home)
        source = self.window._get_3d_source_context_for_axis(legacy_axis, raw)
        self.assertEqual(source["source_data_bounds"], bounds)
        self.assertEqual(source["full_shape"], self.raw.shape[:3])
        self.assertNotIn("data_bounds", source)

    def test_rotated_volume_crop_denoises_only_original_local_domain(self):
        methods = [DenoiseEngines.GAUSSIAN_METHOD_NAME]
        self.window.global_denoise_methods = methods
        self.window.shared_denoise_signature = self.window._denoise_signature(methods)
        self.window.shared_denoised_raw_data = np.full(self.raw.shape, 9999.0, dtype=np.float32)
        self.window.canvas_tool_overlay.set_rotation_angle(ANGLE)
        self.window.on_rotation_angle_changed()
        self.window.current_render_context = self.window._compute_render_context(self.home)
        self.window.crop_controller.set_selection(
            CropSelection.from_dict(self.crop), notify=False
        )

        original_mask = explicit_mask(self.raw.shape[:3], self.crop, 0.0)
        samples = np.where(original_mask)
        bounds = tuple(
            int(value) for axis in samples for value in (axis.min(), axis.max())
        )
        slices = tuple(
            slice(bounds[2 * axis], bounds[2 * axis + 1] + 1)
            for axis in range(3)
        )
        real_pipeline = DenoiseEngines.apply_pipeline
        calls = []

        def observed_pipeline(data, pipeline, max_workers=None):
            calls.append(tuple(data.shape))
            return real_pipeline(data, pipeline, max_workers=max_workers)

        with patch(
            "bandscope.processing.denoise_engines.DenoiseEngines.apply_pipeline",
            side_effect=observed_pipeline,
        ):
            self.window.on_cut()
            spec = self.window.left_workspace.current_spec()
            self.assertTrue(spec.params["local_crop_denoise"])
            raw_local, _coords = self.window._get_display_state_for_spec(spec)
            self.assertEqual(raw_local.shape, self.raw.shape)
            outside = np.ones(self.raw.shape[:3], dtype=bool)
            outside[slices] = False
            self.assertFalse(np.any(np.asarray(raw_local)[outside]))
            slice_spec = self.window._build_slice_dos_spec()
            actual_slice = self.context(slice_spec)["y_data"]

        expected_local = real_pipeline(
            np.where(
                original_mask[slices][..., None],
                self.raw[slices + (slice(None),)],
                0.0,
            ),
            methods,
        )
        expected_volume = np.zeros(self.raw.shape, dtype=np.float32)
        expected_volume[slices + (slice(None),)] = expected_local
        for frame in range(NT):
            rotated = rotate_linear(expected_volume[..., frame], ANGLE)
            selected = explicit_mask(self.raw.shape[:3], self.crop, ANGLE)
            expected_sum = np.sum(rotated[selected], dtype=np.float64)
            self.assertAlmostEqual(actual_slice[frame], expected_sum, places=4)
            self.set_frame(spec, frame)
            actual = self.context(spec)
            np.testing.assert_allclose(
                np.sum(actual["data"], dtype=np.float64), expected_sum,
                rtol=2e-6, atol=2e-6,
            )

        self.set_frame(spec, 1)
        self.window.rotation_angle = 0.0
        actual_zero = self.context(spec)
        selected_zero = explicit_mask(self.raw.shape[:3], self.crop, 0.0)
        np.testing.assert_allclose(
            np.sum(actual_zero["data"], dtype=np.float64),
            np.sum(expected_volume[..., 1][selected_zero], dtype=np.float64),
            rtol=2e-6, atol=2e-6,
        )
        self.assertEqual(calls, [tuple(expected_local.shape)])
        self.assertTrue(np.all(self.window.shared_denoised_raw_data == 9999.0))
        self.assertIsNone(self.window._scope_data_cache.get(
            self.window._scope_cache_key("full", self.window.shared_denoise_signature)
        ))

    def test_multi_selection_edc_comparison_and_log_recompute_each_frame(self):
        self.window.rotation_angle = ANGLE
        self.window.current_render_context = self.window._compute_render_context(self.home)
        controller = self.window.crop_controller
        controller.activate(self.home.page_id, self.window.current_render_context)
        boxes = [
            CropSelection.from_dict(selection(
                self.coords, ("X", "Y", "E"), (1, 3, 0, 3, 0, NE - 1), ANGLE
            )),
            CropSelection.from_dict(selection(
                self.coords, ("X", "Y", "E"), (2, 4, 5, 8, 0, NE - 1), ANGLE
            )),
        ]
        for index, box in enumerate(boxes):
            if index:
                controller.add_selection(self.window.current_render_context)
            controller.set_selection(box, notify=False)
        page_id = self.window._create_edc_result_from_selections()
        self.assertIsNotNone(page_id)
        comparison = self.window.left_workspace.current_spec()
        self.assertEqual(comparison.page_kind, "curve_comparison_1d")
        self.set_frame(comparison, 1)
        self.window.rotation_angle = -20.0
        context = self.context(comparison)

        rotated = rotate_linear(self.raw[..., 1], ANGLE)
        expected = []
        for box in boxes:
            mask = explicit_mask(self.raw.shape[:3], box.to_dict(), ANGLE)
            expected.append(np.sum(np.where(mask, rotated, 0.0), axis=(0, 1)))
        for curve, reference in zip(context["curves"], expected):
            np.testing.assert_allclose(curve["y_data"], reference, rtol=2e-6, atol=2e-6)

        self.window.current_render_context = context
        self.assertTrue(self.window._apply_log_to_current_curves())
        log_spec = self.window.left_workspace.current_spec()
        logged = self.context(log_spec)
        for curve, reference in zip(logged["curves"], expected):
            np.testing.assert_allclose(
                curve["y_data"], np.log10(reference), rtol=2e-6, atol=2e-6
            )


if __name__ == "__main__":
    unittest.main()
