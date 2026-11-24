import tempfile
import unittest
from pathlib import Path

import numpy as np

from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.axis_mapping import AXIS_MAPPING_VERSION
from bandscope.core.data_trans import convert
from bandscope.exporting.calibrated_source import (
    build_calibrated_source_payload,
    save_calibrated_source,
)


class CalibratedSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _load(self, path):
        core = AnalyzerCore()
        success, info = core.load_npz(path)
        self.assertTrue(success, info)
        core.source_identity.future.result(timeout=5)
        return core

    def _make_core(self, dimensions):
        if dimensions == 3:
            shape = (4, 2, 3)
            axis_order = ["E", "X", "Y"]
            scan_key = ""
        else:
            shape = (2, 4, 2, 3)
            axis_order = ["T", "E", "X", "Y"]
            scan_key = "stage"
        raw = np.arange(np.prod(shape), dtype=np.float32).reshape(shape) / 7.0
        arrays = {
            "sample": raw,
            "kx": np.asarray([-0.4, 0.1]),
            "ky": np.asarray([-2.0, 0.0, 3.0]),
            "E": np.asarray([0.2, 0.1, -0.2, -0.8]),
            "x_lab": np.asarray([10.0, 20.0]),
            "axis_order": np.asarray(axis_order),
            "axis_mapping_version": np.asarray([AXIS_MAPPING_VERSION]),
            "axis_coordinate_keys": np.asarray(["kx", "ky", "E", scan_key]),
            "kx_unit": np.asarray(["1/Å"]),
            "ky_unit": np.asarray(["1/Å"]),
            "E_unit": np.asarray(["eV"]),
        }
        if dimensions == 4:
            arrays.update({
                "stage": np.asarray([100.0, 250.0]),
                "scan_type": np.asarray(["custom"]),
                "scan_name": np.asarray(["磁场"]),
                "scan_unit": np.asarray(["mT"]),
            })
        source = self.root / f"source-{dimensions}d.npz"
        np.savez(source, **arrays)
        core = self._load(source)

        source_distance = float(np.hypot(
            core.coords["X"][1] - core.coords["X"][0],
            core.coords["Y"][2] - core.coords["Y"][0],
        ))
        calibrated = core.copy_with_momentum_calibration(
            (0.0, 0.0),
            (1.0, 2.0),
            source_distance * 2.5,
            unit="Å⁻¹",
        )
        return calibrated, raw

    def _assert_roundtrip(self, actual, expected, raw):
        np.testing.assert_array_equal(actual.source_array, raw)
        self.assertEqual(actual.source_array.shape, raw.shape)
        self.assertEqual(actual.axis_assignment.axis_roles, expected.axis_assignment.axis_roles)
        self.assertEqual(actual.axis_assignment.role_order, expected.axis_assignment.role_order)
        for key in ("X", "Y", "E", "delay"):
            np.testing.assert_allclose(actual.coords[key], expected.coords[key])
            self.assertEqual(actual.coord_units.get(key), expected.coord_units.get(key))
        self.assertEqual(actual.scan_kind, expected.scan_kind)
        self.assertEqual(actual.scan_name, expected.scan_name)
        self.assertEqual(actual.scan_unit, expected.scan_unit)
        for key, values in expected.coordinate_candidates.items():
            expected_values = {
                "kx": expected.coords["X"],
                "ky": expected.coords["Y"],
                "E": expected.coords["E"],
            }.get(key, values)
            self.assertIn(key, actual.coordinate_candidates)
            np.testing.assert_allclose(actual.coordinate_candidates[key], expected_values)

    def _assert_payload(self, core, raw, dimensions):
        payload = build_calibrated_source_payload(core)
        np.testing.assert_array_equal(payload["sample"], raw)
        expected_order = ["E", "X", "Y"] if dimensions == 3 else ["T", "E", "X", "Y"]
        self.assertEqual(payload["axis_order"].tolist(), expected_order)
        self.assertEqual(payload["axis_mapping_version"].tolist(), [AXIS_MAPPING_VERSION])
        self.assertEqual(
            payload["axis_coordinate_keys"].tolist(),
            ["kx", "ky", "E", "" if dimensions == 3 else "stage"],
        )
        np.testing.assert_allclose(payload["kx"], core.coords["X"])
        np.testing.assert_allclose(payload["ky"], core.coords["Y"])
        np.testing.assert_allclose(payload["E"], core.coords["E"])
        self.assertEqual(payload["kx_unit"].tolist(), ["Å⁻¹"])
        self.assertEqual(payload["ky_unit"].tolist(), ["Å⁻¹"])

    def _test_roundtrip(self, dimensions, suffix):
        core, raw = self._make_core(dimensions)
        self._assert_payload(core, raw, dimensions)
        target = self.root / f"calibrated-{dimensions}d{suffix}"
        save_calibrated_source(target, core)
        if suffix == ".mat":
            converted = self.root / f"converted-{dimensions}d.npz"
            convert(target, converted)
            target = converted
        actual = self._load(target)
        self._assert_roundtrip(actual, core, raw)

    def test_three_dimensional_npz_roundtrip(self):
        self._test_roundtrip(3, ".npz")

    def test_four_dimensional_npz_roundtrip(self):
        self._test_roundtrip(4, ".npz")

    def test_three_dimensional_mat_convert_roundtrip(self):
        self._test_roundtrip(3, ".mat")

    def test_four_dimensional_mat_convert_roundtrip(self):
        self._test_roundtrip(4, ".mat")


if __name__ == "__main__":
    unittest.main()
