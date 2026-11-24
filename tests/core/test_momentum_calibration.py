import os
import tempfile
import unittest

import numpy as np

from bandscope.core.analyzer_core import AnalyzerCore


class AnalyzerCoreMomentumCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = np.arange(2 * 3 * 4 * 5, dtype=np.float32).reshape(2, 3, 4, 5)
        path = os.path.join(self.temp.name, "momentum.npz")
        np.savez(
            path,
            sample=self.raw,
            kx=np.array([-2.0, 0.0, 3.0, 9.0]),
            ky=np.array([5.0, 2.0, -1.0, -4.0, -10.0]),
            E=np.array([-0.5, 0.0, 1.5]),
            temperature=np.array([300.0, 100.0]),
            axis_order=np.array(["T", "E", "X", "Y"]),
            kx_unit=np.array(["old-x-unit"]),
            ky_unit=np.array(["old-y-unit"]),
            E_unit=np.array(["eV"]),
            temperature_unit=np.array(["K"]),
        )
        self.core = AnalyzerCore()
        success, info = self.core.load_npz(path)
        self.assertTrue(success, info)
        self.core.source_identity.future.result(timeout=5)

    def test_copy_scales_only_momentum_coordinates_and_shares_intensity(self):
        original_x = self.core.coords["X"].copy()
        original_y = self.core.coords["Y"].copy()
        original_candidate_x = self.core.coordinate_candidates["kx"].copy()
        original_candidate_y = self.core.coordinate_candidates["ky"].copy()

        calibrated = self.core.copy_with_momentum_calibration(
            (0.25, 0.5), (2.5, 3.25), 42.0, unit="Å⁻¹"
        )

        expected_scale = 42.0 / np.hypot(7.5, -9.0)
        np.testing.assert_allclose(calibrated.coords["X"], original_x * expected_scale)
        np.testing.assert_allclose(calibrated.coords["Y"], original_y * expected_scale)
        np.testing.assert_allclose(
            calibrated.coordinate_candidates["kx"], original_x * expected_scale
        )
        np.testing.assert_allclose(
            calibrated.coordinate_candidates["ky"], original_y * expected_scale
        )
        self.assertEqual(calibrated.axis_assignment.coord_keys["X"], "kx")
        self.assertEqual(calibrated.axis_assignment.coord_keys["Y"], "ky")
        self.assertEqual(calibrated.coord_sources["X"], "file")
        self.assertEqual(calibrated.coord_sources["Y"], "file")
        self.assertEqual(calibrated.coord_units["X"], "Å⁻¹")
        self.assertEqual(calibrated.coord_units["Y"], "Å⁻¹")
        self.assertEqual(calibrated.coordinate_candidate_units["X"]["kx"], "Å⁻¹")
        self.assertEqual(calibrated.coordinate_candidate_units["Y"]["ky"], "Å⁻¹")
        self.assertTrue(calibrated.axis_assignment.manual)

        np.testing.assert_array_equal(self.core.coords["X"], original_x)
        np.testing.assert_array_equal(self.core.coords["Y"], original_y)
        np.testing.assert_array_equal(
            self.core.coordinate_candidates["kx"], original_candidate_x
        )
        np.testing.assert_array_equal(
            self.core.coordinate_candidates["ky"], original_candidate_y
        )
        self.assertEqual(self.core.coord_units["X"], "old-x-unit")
        self.assertEqual(self.core.coord_units["Y"], "old-y-unit")
        np.testing.assert_array_equal(calibrated.raw_data, self.core.raw_data)
        np.testing.assert_array_equal(calibrated.raw_data, self.raw.transpose(2, 3, 1, 0))
        self.assertIs(calibrated.source_array, self.core.source_array)
        self.assertTrue(np.shares_memory(calibrated.raw_data, self.core.raw_data))

    def test_copy_preserves_energy_scan_metadata_and_original_dimension_order(self):
        calibrated = self.core.copy_with_momentum_calibration(
            (0, 0), (3, 4), 10.0, unit="nm⁻¹"
        )

        self.assertEqual(
            calibrated.axis_assignment.axis_roles, self.core.axis_assignment.axis_roles
        )
        self.assertEqual(
            calibrated.axis_assignment.role_order, self.core.axis_assignment.role_order
        )
        self.assertEqual(
            calibrated.axis_assignment.original_shape,
            self.core.axis_assignment.original_shape,
        )
        np.testing.assert_array_equal(calibrated.coords["E"], self.core.coords["E"])
        np.testing.assert_array_equal(calibrated.coords["delay"], self.core.coords["delay"])
        self.assertEqual(calibrated.coord_sources["E"], self.core.coord_sources["E"])
        self.assertEqual(calibrated.coord_sources["delay"], self.core.coord_sources["delay"])
        self.assertEqual(calibrated.coord_units["E"], "eV")
        self.assertEqual(calibrated.coord_units["delay"], "K")
        self.assertEqual(calibrated.scan_kind, "temperature")
        self.assertEqual(calibrated.scan_name, "温度")
        self.assertEqual(calibrated.has_time_axis, self.core.has_time_axis)
        self.assertIs(calibrated.source_identity, self.core.source_identity)

    def test_index_momentum_coordinates_become_file_candidates(self):
        assignment = self.core.axis_assignment.clone()
        assignment.coord_keys["X"] = None
        assignment.coord_keys["Y"] = None
        assignment.coord_units["X"] = None
        assignment.coord_units["Y"] = None
        index_core = self.core.copy_with_assignment(assignment)
        self.assertEqual(index_core.coord_sources["X"], "index")
        self.assertEqual(index_core.coord_sources["Y"], "index")

        calibrated = index_core.copy_with_momentum_calibration((0, 0), (3, 4), 10.0)

        np.testing.assert_allclose(calibrated.coords["X"], [0.0, 2.0, 4.0, 6.0])
        np.testing.assert_allclose(calibrated.coords["Y"], [0.0, 2.0, 4.0, 6.0, 8.0])
        self.assertEqual(calibrated.coord_sources["X"], "file")
        self.assertEqual(calibrated.coord_sources["Y"], "file")
        self.assertIsNone(calibrated.coord_units["X"])
        self.assertIsNone(calibrated.coord_units["Y"])
        self.assertEqual(index_core.coord_sources["X"], "index")
        self.assertEqual(index_core.coord_sources["Y"], "index")
        np.testing.assert_array_equal(index_core.coords["X"], np.arange(4))
        np.testing.assert_array_equal(index_core.coords["Y"], np.arange(5))

    def test_invalid_requests_do_not_modify_original_core(self):
        original_x = self.core.coords["X"].copy()
        original_y = self.core.coords["Y"].copy()
        for point_a, point_b, target in (
            ((0, 0), (3, 4), 0.0),
            ((0, 0), (0, 0), 1.0),
            ((-1, 0), (3, 4), 1.0),
        ):
            with self.subTest(point_a=point_a, point_b=point_b, target=target):
                with self.assertRaises(ValueError):
                    self.core.copy_with_momentum_calibration(point_a, point_b, target)
                np.testing.assert_array_equal(self.core.coords["X"], original_x)
                np.testing.assert_array_equal(self.core.coords["Y"], original_y)

        with self.assertRaises(ValueError):
            AnalyzerCore().copy_with_momentum_calibration((0, 0), (1, 1), 1.0)


if __name__ == "__main__":
    unittest.main()
