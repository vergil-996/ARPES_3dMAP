import copy
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PyQt5.QtWidgets import QApplication
from scipy.ndimage import rotate as linear_rotate

from bandscope.core.axis_interval import AxisInterval
from bandscope.core.crop_model import CropSelection
from bandscope.core.view_recipe import ViewRecipeDocument, plain
from bandscope.exporting.publication_export import capture_snapshot
from tests.support.view_recipe import control_window, recipe_document


def make_double_peak_npz(path):
    x = np.linspace(-4.0, 4.0, 9)
    y = np.linspace(-3.0, 3.0, 7)
    energy = np.linspace(-1.0, 1.0, 3)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    first = np.exp(-(((xx + 1.6) / 0.65) ** 2 + ((yy + 0.9) / 0.55) ** 2))
    second = np.exp(-(((xx - 1.5) / 0.6) ** 2 + ((yy - 1.1) / 0.7) ** 2))
    peaks = first + 0.72 * second
    frame_scales = np.array([1.0, 2.6], dtype=np.float32)
    energy_weights = np.array([0.4, 1.0, 0.25], dtype=np.float32)
    data = (
        peaks[:, :, None, None]
        * energy_weights[None, None, :, None]
        * frame_scales[None, None, None, :]
    ).astype(np.float32)
    np.savez(
        path,
        sample=data,
        kx=x,
        ky=y,
        E=energy,
        time=np.array([0.0, 1.0]),
        axis_mapping_version=1,
        axis_order=np.array(["X", "Y", "E", "T"]),
    )
    return data, {"X": x, "Y": y, "E": energy, "delay": np.array([0.0, 1.0])}


def rotate_reference(data, angle):
    if abs(float(angle)) < 1e-12:
        return np.array(data, dtype=np.float32, copy=True)
    return linear_rotate(
        np.asarray(data, dtype=np.float32),
        float(angle),
        axes=(0, 1),
        reshape=False,
        order=1,
        mode="constant",
        cval=0.0,
        prefilter=True,
    )


def creation_mask(shape, logical_bounds, creation_angle, current_angle):
    x_low, x_up, y_low, y_up, e_low, e_up = logical_bounds
    x, y = np.meshgrid(
        np.arange(shape[0], dtype=np.float64),
        np.arange(shape[1], dtype=np.float64),
        indexing="ij",
    )
    center_x = (shape[0] - 1) / 2.0
    center_y = (shape[1] - 1) / 2.0
    theta = np.deg2rad(float(creation_angle) - float(current_angle))
    cosine, sine = np.cos(theta), np.sin(theta)
    source_x = center_x + cosine * (x - center_x) - sine * (y - center_y)
    source_y = center_y + sine * (x - center_x) + cosine * (y - center_y)
    tolerance = 1e-9
    xy = (
        (source_x >= x_low - tolerance) & (source_x <= x_up + tolerance)
        & (source_y >= y_low - tolerance) & (source_y <= y_up + tolerance)
    )
    energy = np.zeros(shape[2], dtype=bool)
    energy[e_low:e_up + 1] = True
    return xy[:, :, None] & energy[None, None, :]


def mask_bounds(mask):
    indices = np.where(mask)
    return tuple(
        value
        for axis in indices
        for value in (int(axis.min()), int(axis.max()))
    )


def reference_chain(raw, regions, time_index, current_angle):
    data = rotate_reference(raw[..., time_index], current_angle).astype(np.float64)
    valid = np.ones(data.shape, dtype=bool)
    crop_bounds = None
    for region in regions:
        selected = creation_mask(
            data.shape, region["logical_bounds"],
            region["rotation_angle"], current_angle,
        )
        if region.get("operation", "crop") == "erase":
            data[selected] = np.nan
            valid &= ~selected
        else:
            data = np.where(selected, data, 0.0)
            valid &= selected
            if crop_bounds is None:
                crop_bounds = mask_bounds(selected)
    return data, valid, crop_bounds


class RotatedViewRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "double_peak.npz"
        self.raw, self.coords = make_double_peak_npz(self.path)
        self.window = control_window()
        self.addCleanup(self.window.close)
        self.assertTrue(self.window.load_data(str(self.path)))

    def set_angle(self, window, angle):
        window.timeline_bar.set_rotation_angle(angle)
        window.on_rotation_angle_changed()

    def save(self):
        snapshot = capture_snapshot(self.window)
        self.assertIsNotNone(snapshot.view_recipe, snapshot.recipe_error)
        path = self.path.parent / "figure.png.bsview.json"
        path.write_text(ViewRecipeDocument(snapshot.view_recipe).dumps(), encoding="utf-8")
        return path, snapshot

    @staticmethod
    def make_schema_one(path):
        document = json.loads(path.read_text(encoding="utf-8"))
        document["schema_version"] = 1
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    def restore(self, path):
        other = control_window()
        self.addCleanup(other.close)
        self.assertTrue(other._load_view_recipe(
            path, confirm=False, relocate=lambda _: str(self.path)
        ))
        return other

    def test_legacy_unangled_region_uses_each_current_grid_and_defaults_to_crop(self):
        region = CropSelection(
            "3d", ("X", "Y", "E"), (-1.5, 1.5, -1.5, 1.5, -1.0, 1.0)
        ).to_dict()
        for key in ("operation", "rotation_angle", "logical_bounds"):
            del region[key]
        home = self.window.left_workspace.current_spec()
        home.params["crop_regions"] = [region]
        self.set_angle(self.window, 30.0)
        path, _snapshot = self.save()
        self.make_schema_one(path)
        other = self.restore(path)
        restored = other.active_page_spec.params["crop_regions"][0]
        self.assertNotIn("operation", restored)
        self.assertNotIn("rotation_angle", restored)

        for time_index, angle in ((0, 30.0), (1, 30.0), (1, -20.0), (0, 30.0)):
            with self.subTest(time_index=time_index, angle=angle):
                other.timeline_bar.slider_time.setValue(time_index)
                self.set_angle(other, angle)
                expected = rotate_reference(self.raw[..., time_index], angle)[2:7, 1:6, :]
                np.testing.assert_allclose(
                    other.current_render_context["data"], expected,
                    rtol=2e-6, atol=2e-7, equal_nan=True,
                )

    def test_legacy_full_frame_roi_is_cropped_before_every_rotation(self):
        home = self.window.left_workspace.current_spec()
        scope = self.window._register_roi_scope((1, 7, 1, 5, 0, 2))
        home.data_scope_id = scope.scope_id
        self.set_angle(self.window, 30.0)
        path, _snapshot = self.save()
        self.make_schema_one(path)
        other = self.restore(path)

        for time_index, angle in ((0, 30.0), (1, -20.0), (0, 30.0)):
            with self.subTest(time_index=time_index, angle=angle):
                other.timeline_bar.slider_time.setValue(time_index)
                self.set_angle(other, angle)
                full = np.zeros(self.raw.shape[:3], dtype=np.float32)
                full[scope.spatial_slices] = self.raw[scope.spatial_slices + (time_index,)]
                expected = rotate_reference(full, angle)
                np.testing.assert_allclose(
                    other.current_render_context["data"], expected,
                    rtol=2e-6, atol=2e-7, equal_nan=True,
                )

    @staticmethod
    def angled_regions():
        crop = CropSelection(
            "3d", ("X", "Y", "E"), (-3.0, 2.0, -3.0, 2.0, -1.0, 1.0),
            rotation_angle=30.0, logical_bounds=(1, 6, 0, 5, 0, 2),
        )
        erase = CropSelection(
            "3d", ("X", "Y", "E"), (1.0, 3.0, -1.0, 1.0, -1.0, 1.0),
            operation="erase", rotation_angle=-20.0,
            logical_bounds=(5, 7, 2, 4, 0, 2),
        )
        return [crop.to_dict(), erase.to_dict()]

    def assert_chain_matches_reference(self, context, regions, time_index, angle):
        self.assertFalse(context.get("crop_empty"))
        expected, expected_valid, expected_bounds = reference_chain(
            self.raw, regions, time_index, angle,
        )
        self.assertEqual(tuple(context["data_bounds"]), expected_bounds)
        slices = tuple(
            slice(expected_bounds[2 * axis], expected_bounds[2 * axis + 1] + 1)
            for axis in range(3)
        )
        np.testing.assert_allclose(
            context["data"], expected[slices],
            rtol=3e-6, atol=3e-7, equal_nan=True,
        )
        np.testing.assert_array_equal(
            context["spatial_valid_mask"], expected_valid[slices],
        )

    def test_angled_crop_erase_chain_roundtrips_frames_and_rotation_without_accumulation(self):
        np.testing.assert_array_equal(
            rotate_reference(self.raw[..., 0], 0.0), self.raw[..., 0]
        )
        impulse = np.zeros(self.raw.shape[:2], dtype=np.float32)
        impulse[6, 4] = 1.0
        rotated = rotate_reference(impulse, 30.0)
        self.assertEqual(np.unravel_index(rotated.argmax(), rotated.shape), (5, 5))

        regions = self.angled_regions()
        home = self.window.left_workspace.current_spec()
        home.params["crop_regions"] = copy.deepcopy(regions)
        self.set_angle(self.window, 30.0)
        self.assert_chain_matches_reference(
            self.window.current_render_context, regions, 0, 30.0,
        )
        path, _snapshot = self.save()
        other = self.restore(path)
        self.assertEqual(
            other.active_page_spec.params["crop_regions"], plain(regions),
        )

        first = None
        for time_index, angle in ((0, 30.0), (1, -20.0), (1, 30.0), (0, 30.0)):
            with self.subTest(time_index=time_index, angle=angle):
                other.timeline_bar.slider_time.setValue(time_index)
                self.set_angle(other, angle)
                context = other.current_render_context
                self.assert_chain_matches_reference(context, regions, time_index, angle)
                if first is None:
                    first = (
                        np.array(context["data"], copy=True),
                        np.array(context["spatial_valid_mask"], copy=True),
                    )
                elif (time_index, angle) == (0, 30.0):
                    np.testing.assert_array_equal(context["data"], first[0])
                    np.testing.assert_array_equal(
                        context["spatial_valid_mask"], first[1],
                    )

    def test_edc_frozen_angled_source_roundtrips_and_recomputes_across_frames(self):
        regions = self.angled_regions()
        home = self.window.left_workspace.current_spec()
        home.params["crop_regions"] = copy.deepcopy(regions)
        home.params["time_interval"] = AxisInterval.from_indices(
            self.coords["delay"], 0, 0, locked=True, axis_key="delay",
        ).as_dict()
        self.window._bind_time_interval(home, animate=False)
        self.set_angle(self.window, 30.0)
        context = self.window.current_render_context
        self.window.crop_controller.activate(home.page_id, context)
        self.window.crop_controller.set_selection(
            CropSelection(
                "3d", ("X", "Y", "E"), (-2.0, 0.0, -3.0, -1.0, -1.0, 1.0),
            ),
            notify=False,
        )
        self.assertIsNotNone(self.window._create_edc_result_from_selections())
        edc = self.window.left_workspace.current_spec()
        self.assertEqual(edc.params["source_rotation_angle"], 30.0)
        self.assertEqual(
            edc.params["edc_source"]["params"]["crop_regions"], regions,
        )

        def expected(time_index):
            data, valid, _bounds = reference_chain(self.raw, regions, time_index, 30.0)
            selected = creation_mask(data.shape, (2, 4, 0, 2, 0, 2), 30.0, 30.0)
            values = np.where(selected & valid, data, np.nan)
            return np.nansum(values, axis=(0, 1), dtype=np.float64)

        np.testing.assert_allclose(
            self.window.current_render_context["y_data"], expected(0),
            rtol=3e-6, atol=3e-7, equal_nan=True,
        )
        path, _snapshot = self.save()
        other = self.restore(path)
        restored = other.active_page_spec
        self.assertEqual(restored.page_kind, "edc_curve")
        self.assertTrue(other._edc_time_recomputable(restored))
        self.assertEqual(restored.params["source_rotation_angle"], 30.0)
        self.assertEqual(restored.params["edc_source"]["rotation_angle"], 30.0)
        self.assertEqual(
            restored.params["edc_source"]["params"]["crop_regions"], plain(regions),
        )

        for time_index, angle in ((0, 30.0), (1, 80.0), (0, -20.0), (1, 30.0)):
            with self.subTest(time_index=time_index, window_angle=angle):
                self.set_angle(other, angle)
                other.timeline_bar.time_interval_control.endpointPreview.emit(
                    "low", float(self.coords["delay"][time_index]),
                )
                np.testing.assert_allclose(
                    other.current_render_context["y_data"], expected(time_index),
                    rtol=3e-6, atol=3e-7, equal_nan=True,
                )
                self.assertEqual(other.rotation_angle, angle)

    def test_angle_and_derived_freeze_fields_survive_existing_schema(self):
        angled = CropSelection(
            "3d", ("X", "Y", "E"), (-2.0, 0.5, -2.0, 1.0, -1.0, 1.0),
            operation="erase", rotation_angle=30.0,
            logical_bounds=(1, 6, 1, 5, 0, 2),
        ).to_dict()
        _core, document = recipe_document(
            self.path,
            kind="edc_curve",
            params={
                "crop_regions": [angled],
                "edc_source": {
                    "page_kind": "home",
                    "params": {"crop_regions": [copy.deepcopy(angled)]},
                    "data_scope_id": "full",
                    "rotation_angle": 30.0,
                },
                "curve_metadata": [{"selection": copy.deepcopy(angled)}],
            },
        )
        path = self.path.parent / "nested.bsview.json"
        path.write_text(ViewRecipeDocument(plain(document)).dumps(), encoding="utf-8")
        restored = ViewRecipeDocument.load(path).data
        self.assertEqual(restored["schema_version"], 6)
        target = next(page for page in restored["pages"] if page["page_id"] == "target")
        self.assertEqual(target["params"]["crop_regions"], [plain(angled)])
        self.assertEqual(
            target["params"]["edc_source"]["params"]["crop_regions"], [plain(angled)]
        )
        self.assertEqual(target["params"]["curve_metadata"][0]["selection"], plain(angled))
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8"))["pages"][1]["params"],
            target["params"],
        )


if __name__ == "__main__":
    unittest.main()
