import unittest
from dataclasses import FrozenInstanceError

import numpy as np

from bandscope.processing.energy_calibration import calibrate_energy_axis


class EnergyCalibrationTests(unittest.TestCase):
    def test_nonuniform_source_coordinates_use_logical_indices(self):
        energy = np.array([100.0, 102.0, 110.0, 130.0, 200.0])
        original = energy.copy()

        result = calibrate_energy_axis(
            energy, reference_index=1, second_index=4, target_spacing=1.5, unit="eV"
        )

        np.testing.assert_allclose(result.energy, [-0.5, 0.0, 0.5, 1.0, 1.5])
        self.assertEqual(result.energy[1], 0.0)
        self.assertEqual(result.energy[4], 1.5)
        self.assertEqual(result.source_spacing, 3.0)
        self.assertEqual(result.target_spacing, 1.5)
        self.assertEqual(result.scale, 0.5)
        self.assertEqual(result.reference_index, 1.0)
        self.assertEqual(result.second_index, 4.0)
        self.assertEqual(result.unit, "eV")
        np.testing.assert_array_equal(energy, original)

    def test_descending_source_coordinates_do_not_reverse_index_direction(self):
        result = calibrate_energy_axis([12.0, 5.0, 1.0, -4.0], 0, 3, 0.3)

        np.testing.assert_allclose(result.energy, [0.0, 0.1, 0.2, 0.3])
        self.assertTrue(np.all(np.diff(result.energy) > 0.0))
        self.assertEqual(result.source_spacing, 3.0)
        self.assertGreater(result.scale, 0.0)
        self.assertEqual(result.unit, "")

    def test_negative_line_order_preserves_direction_and_zero_reference(self):
        result = calibrate_energy_axis([-3.0, 0.0, 8.0, 20.0], 2, 0, 1.2)

        np.testing.assert_allclose(result.energy, [1.2, 0.6, 0.0, -0.6])
        self.assertEqual(result.energy[2], 0.0)
        self.assertEqual(result.energy[0], 1.2)
        self.assertEqual(result.source_spacing, -2.0)
        self.assertLess(result.scale, 0.0)
        self.assertTrue(np.all(np.diff(result.energy) < 0.0))

    def test_fractional_indices_calibrate_interpolated_logical_positions(self):
        result = calibrate_energy_axis([5.0, 9.0, 20.0, 50.0], 0.5, 2.5, 4.0)

        np.testing.assert_allclose(result.energy, [-1.0, 1.0, 3.0, 5.0])
        self.assertEqual(result.source_spacing, 2.0)
        self.assertEqual(result.reference_index, 0.5)
        self.assertEqual(result.second_index, 2.5)

    def test_result_and_energy_coordinates_are_immutable(self):
        result = calibrate_energy_axis([0, 1, 2], 0, 2, 2.0)

        self.assertFalse(result.energy.flags.writeable)
        with self.assertRaises(FrozenInstanceError):
            result.scale = 3.0
        with self.assertRaises(ValueError):
            result.energy[0] = 3.0

    def test_invalid_energy_axes_are_rejected(self):
        cases = (
            [],
            1.0,
            [[0.0, 1.0]],
            [0.0, np.nan],
            [0.0, np.inf],
            [0.0, -np.inf],
            [0.0, "invalid"],
            [0.0, 10**1000],
            [0.0, 1.0 + 2.0j],
        )
        for energy in cases:
            with self.subTest(energy=energy), self.assertRaises(ValueError):
                calibrate_energy_axis(energy, 0, 1, 1.0)

    def test_invalid_or_out_of_bounds_indices_are_rejected(self):
        index_pairs = (
            (0, 0),
            (1, 1),
            (-0.01, 1),
            (3, 1),
            (0, -0.01),
            (0, 3),
            (np.nan, 1),
            (0, np.inf),
            (0, -np.inf),
            ([0], 1),
            (0, [1]),
            ("invalid", 1),
            (0, None),
            (1 + 0j, 2),
        )
        for reference, second in index_pairs:
            with self.subTest(reference=reference, second=second), self.assertRaises(ValueError):
                calibrate_energy_axis([0, 1, 2], reference, second, 1.0)

    def test_invalid_target_spacings_are_rejected(self):
        cases = (
            np.nan,
            np.inf,
            -np.inf,
            0.0,
            -1.0,
            "invalid",
            None,
            [1.0],
            1.0 + 0j,
            np.nextafter(0.0, 1.0),
        )
        for target in cases:
            with self.subTest(target=target), self.assertRaises(ValueError):
                calibrate_energy_axis([0, 1, 2], 0, 2, target)

    def test_unit_must_be_text_or_none(self):
        for unit in (1, b"eV", object()):
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                calibrate_energy_axis([0, 1], 0, 1, 1.0, unit=unit)

        result = calibrate_energy_axis([0, 1], 0, 1, 1.0, unit=None)
        self.assertEqual(result.unit, "")


if __name__ == "__main__":
    unittest.main()
