import os
import tempfile
import unittest

import h5py
import numpy as np
from scipy.io import savemat

from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.axis_assignment import validate_assignment
from bandscope.core.axis_units import resolve_axis_unit
from bandscope.core.data_trans import convert


class AxisAssignmentLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def path(self, name):
        return os.path.join(self.temp.name, name)

    def load(self, name, **arrays):
        path = self.path(name)
        np.savez(path, **arrays)
        core = AnalyzerCore()
        success, info = core.load_npz(path)
        self.assertTrue(success, info)
        core.source_identity.future.result(timeout=5)
        return core

    def dynamic_arrays(self, scan_key="time", scan_values=None, **metadata):
        raw = np.arange(2 * 3 * 4 * 5, dtype=np.float32).reshape(2, 3, 4, 5)
        arrays = {
            "sample": raw,
            "kx": np.arange(4),
            "ky": np.arange(5),
            "E": np.arange(3),
            scan_key: np.asarray([0.0, 1.0] if scan_values is None else scan_values),
            "axis_order": np.asarray(["T", "E", "X", "Y"]),
        }
        arrays.update(metadata)
        return raw, arrays

    def test_time_angle_temperature_and_bare_t(self):
        cases = (
            ("time", "time", None, "fs"),
            ("angle", "angle", "deg", "deg"),
            ("temperature", "temperature", "K", "K"),
            ("T", "unknown", None, ""),
        )
        for key, expected_kind, unit, displayed_unit in cases:
            with self.subTest(key=key):
                raw, arrays = self.dynamic_arrays(key)
                if unit is not None:
                    arrays[f"{key}_unit"] = np.asarray([unit])
                core = self.load(f"{key}.npz", **arrays)
                self.assertEqual(core.scan_kind, expected_kind)
                self.assertEqual(core.raw_data.shape, (4, 5, 3, 2))
                np.testing.assert_array_equal(core.raw_data, raw.transpose(2, 3, 1, 0))
                actual_unit = resolve_axis_unit(
                    "delay" if core.is_time_axis else "scan", core.coord_units.get("delay")
                )
                self.assertEqual(actual_unit, displayed_unit)

    def test_invalid_preferred_scan_key_falls_through_to_matching_coordinate(self):
        raw, arrays = self.dynamic_arrays("time", [0.0, 1.0])
        arrays["time"] = np.arange(99)
        arrays["temperature"] = np.asarray([300.0, 100.0])
        arrays["temperature_unit"] = np.asarray(["K"])
        core = self.load("coordinate-priority.npz", **arrays)
        self.assertEqual(core.scan_kind, "temperature")
        self.assertEqual(core.axis_assignment.coord_keys["T"], "temperature")
        np.testing.assert_array_equal(core.coords["delay"], [300.0, 100.0])

    def test_missing_scan_coordinate_uses_index_without_time_unit(self):
        raw = np.arange(2 * 3 * 4 * 5, dtype=np.float32).reshape(2, 3, 4, 5)
        core = self.load("missing.npz", sample=raw)
        self.assertEqual(core.scan_kind, "unknown")
        self.assertTrue(core.axis_assignment.needs_confirmation)
        self.assertEqual(core.coord_sources["delay"], "index")
        np.testing.assert_array_equal(core.coords["delay"], np.arange(5))
        self.assertEqual(resolve_axis_unit("scan", core.coord_units.get("delay")), "")

    def test_equal_momentum_lengths_keep_file_order_and_warn(self):
        raw = np.arange(3 * 3 * 2 * 4, dtype=np.float32).reshape(3, 3, 2, 4)
        core = self.load(
            "equal.npz", sample=raw, kx=np.arange(3), ky=np.arange(3),
            E=np.arange(2), time=np.arange(4),
        )
        self.assertEqual(core.axis_assignment.axis_roles, {"T": 3, "E": 2, "X": 0, "Y": 1})
        self.assertTrue(any("Kx、Ky 等长" in item for item in core.axis_assignment.warnings))
        self.assertTrue(core.axis_assignment.needs_confirmation)
        np.testing.assert_array_equal(core.raw_data, raw)

    def test_descending_nonuniform_coordinates_are_preserved(self):
        raw, arrays = self.dynamic_arrays(
            "temperature", [300.0, 100.0], temperature_unit=np.asarray(["K"])
        )
        core = self.load("descending.npz", **arrays)
        np.testing.assert_array_equal(core.coords["delay"], [300.0, 100.0])
        self.assertEqual(core.scan_name, "温度")

    def test_static_and_scanned_three_dimensional_arrays(self):
        static = np.arange(2 * 3 * 4, dtype=np.float32).reshape(2, 3, 4)
        core = self.load(
            "static.npz", sample=static, kx=np.arange(2), ky=np.arange(3), E=np.arange(4)
        )
        self.assertFalse(core.has_scan_axis)
        self.assertEqual(core.raw_data.shape, (2, 3, 4, 1))
        np.testing.assert_array_equal(core.raw_data[..., 0], static)

        scanned = np.arange(2 * 3 * 4, dtype=np.float32).reshape(2, 3, 4)
        core = self.load(
            "scanned-3d.npz", sample=scanned, kx=np.arange(2), ky=np.arange(3),
            time=np.asarray([0.0, 0.5, 2.0, 4.0]),
        )
        self.assertTrue(core.has_scan_axis)
        self.assertNotIn("E", core.axis_assignment.axis_roles)
        self.assertEqual(core.raw_data.shape, (2, 3, 1, 4))
        np.testing.assert_array_equal(core.raw_data[:, :, 0, :], scanned)

    def test_legacy_hdf5_order_is_preserved(self):
        raw = np.arange(2 * 3 * 4 * 5, dtype=np.float32).reshape(2, 3, 4, 5)
        core = self.load(
            "legacy-hdf5.npz", sample=raw, kx=np.arange(4), ky=np.arange(5),
            E=np.arange(3), time=np.arange(2),
        )
        self.assertEqual(core.axis_assignment.axis_roles, {"X": 2, "Y": 3, "E": 1, "T": 0})
        np.testing.assert_array_equal(core.raw_data, raw.transpose(2, 3, 1, 0))

    def test_manual_layout_roundtrip_and_legacy_layout_time_meaning(self):
        raw, arrays = self.dynamic_arrays("temperature", temperature_unit=np.asarray(["K"]))
        path = self.path("roundtrip.npz")
        np.savez(path, **arrays)
        first = AnalyzerCore()
        self.assertTrue(first.load_npz(path)[0])
        second = AnalyzerCore()
        self.assertTrue(second.load_npz(path, layout=first.source_layout)[0])
        self.assertEqual(second.source_layout, first.source_layout)
        self.assertEqual(second.scan_kind, "temperature")

        legacy = {
            key: value for key, value in first.source_layout.items()
            if key not in {"assignment_version", "scan_type", "scan_name", "warnings", "manual"}
        }
        old = AnalyzerCore()
        self.assertTrue(old.load_npz(path, layout=legacy)[0])
        self.assertEqual(old.scan_kind, "time")

    def test_reassignment_uses_views_and_validates_all_dimensions(self):
        raw, arrays = self.dynamic_arrays("temperature")
        core = self.load("views.npz", **arrays)
        updated = core.axis_assignment.clone()
        updated.axis_roles["X"], updated.axis_roles["Y"] = (
            updated.axis_roles["Y"], updated.axis_roles["X"]
        )
        updated.coord_keys["X"], updated.coord_keys["Y"] = (
            updated.coord_keys["Y"], updated.coord_keys["X"]
        )
        reassigned = core.copy_with_assignment(updated)
        np.testing.assert_array_equal(reassigned.raw_data, raw.transpose(3, 2, 1, 0))
        self.assertIs(reassigned.source_array, core.source_array)
        self.assertTrue(np.shares_memory(reassigned.raw_data, core.source_array))

        kind_only = core.axis_assignment.clone()
        kind_only.scan_kind = "angle"
        kind_only.scan_name = "角度"
        other = core.copy_with_assignment(kind_only)
        self.assertTrue(np.shares_memory(other.raw_data, core.raw_data))
        np.testing.assert_array_equal(other.raw_data, core.raw_data)

        invalid = core.axis_assignment.clone()
        invalid.axis_roles["Y"] = invalid.axis_roles["X"]
        with self.assertRaisesRegex(ValueError, "不同"):
            validate_assignment(invalid, core.coordinate_candidates)

    def test_non_float32_input_keeps_one_float32_source_allocation(self):
        raw = np.arange(2 * 3 * 4, dtype=np.float64).reshape(2, 3, 4)
        core = self.load("float64.npz", sample=raw)
        self.assertEqual(core.source_array.dtype, np.float32)
        self.assertEqual(core.raw_data.dtype, np.float32)
        self.assertTrue(np.shares_memory(core.raw_data, core.source_array))

    def test_source_identity_distinguishes_two_assignments_of_one_file(self):
        _raw, arrays = self.dynamic_arrays("temperature")
        core = self.load("identity.npz", **arrays)
        other = core.axis_assignment.clone()
        other.scan_kind = "angle"
        other.scan_name = "角度"
        reassigned = core.copy_with_assignment(other)
        first = core.source_identity.descriptor(core.source_layout)
        second = reassigned.source_identity.descriptor(reassigned.source_layout)
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertNotEqual(first["source_id"], second["source_id"])
        confirmed = core.copy_with_assignment(core.axis_assignment.clone())
        self.assertEqual(
            confirmed.source_identity.descriptor(confirmed.source_layout)["source_id"],
            first["source_id"],
        )

    def test_mat_conversion_preserves_original_order_vectors_and_scan_type(self):
        raw = np.arange(2 * 3 * 4 * 5, dtype=np.float32).reshape(2, 3, 4, 5)
        source = self.path("temperature.mat")
        target = self.path("temperature-converted.npz")
        savemat(source, {
            "binned": raw,
            "kx": np.arange(4).reshape(1, -1),
            "ky": np.arange(5).reshape(-1, 1),
            "E": np.arange(3),
            "temperature": np.asarray([[300.0, 100.0]]),
            "temperature_unit": "K",
            "stage": np.asarray([[10.0, 20.0]]),
            "axis_order": np.asarray(["T", "E", "X", "Y"]),
        })
        convert(source, target)
        with np.load(target, allow_pickle=False) as converted:
            self.assertEqual(converted["sample"].shape, raw.shape)
            self.assertEqual(converted["kx"].shape, (4,))
            self.assertEqual(converted["ky"].shape, (5,))
            self.assertIn("stage", converted)
        core = AnalyzerCore()
        success, info = core.load_npz(target)
        self.assertTrue(success, info)
        self.assertEqual(core.scan_kind, "temperature")
        self.assertEqual(core.coord_units["delay"], "K")
        self.assertIn("stage", core.coordinate_candidates)
        np.testing.assert_array_equal(core.raw_data, raw.transpose(2, 3, 1, 0))

    def test_hdf5_mat_conversion_keeps_legacy_permutation(self):
        source = self.path("legacy.mat")
        target = self.path("legacy-converted.npz")
        raw = np.arange(2 * 3 * 4 * 5, dtype=np.float32).reshape(2, 3, 4, 5)
        with h5py.File(source, "w") as handle:
            handle["binned"] = raw
            handle["kx"] = np.arange(4)
            handle["ky"] = np.arange(5)
            handle["E"] = np.arange(3)
            handle["time"] = np.arange(2)
        convert(source, target)
        core = AnalyzerCore()
        success, info = core.load_npz(target)
        self.assertTrue(success, info)
        self.assertEqual(core.axis_assignment.axis_roles, {"X": 2, "Y": 3, "E": 1, "T": 0})
        np.testing.assert_array_equal(core.raw_data, raw.transpose(2, 3, 1, 0))


if __name__ == "__main__":
    unittest.main()
