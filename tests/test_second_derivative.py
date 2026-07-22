import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from refactored_app import My3DAnalyzer


class EnergySecondDerivativeTests(unittest.TestCase):
    def test_momentum_only_variation_has_no_response(self):
        momentum = np.linspace(-1.0, 1.0, 21)
        source = np.broadcast_to(-(momentum[:, None] ** 2), (21, 31))
        energy = np.linspace(-0.5, 0.5, source.shape[1])

        actual = My3DAnalyzer._compute_energy_second_derivative(
            source,
            energy_axis=energy,
        )

        np.testing.assert_allclose(actual, 0.0, atol=1e-12)

    def test_energy_variation_produces_negative_energy_second_derivative(self):
        energy = np.linspace(-0.5, 0.5, 31)
        source = np.broadcast_to(-(energy[None, :] ** 2), (21, energy.size))

        actual = My3DAnalyzer._compute_energy_second_derivative(
            source,
            energy_axis=energy,
            sigma=0.0,
        )

        np.testing.assert_allclose(actual, 2.0, rtol=1e-11, atol=1e-11)

    def test_physical_energy_spacing_sets_derivative_scale(self):
        energy = np.arange(9, dtype=np.float64) * 0.25
        source = np.broadcast_to(-(energy[None, :] ** 2), (4, energy.size))

        actual = My3DAnalyzer._compute_energy_second_derivative(
            source,
            energy_axis=energy,
            sigma=0.0,
        )

        np.testing.assert_allclose(actual, 2.0, rtol=1e-11, atol=1e-11)


class VolumeSecondDerivativeTests(unittest.TestCase):
    @staticmethod
    def _bare_analyzer():
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.rotation_angle = 0.0
        analyzer._rotation_cache = {}
        analyzer._second_derivative_volume_cache = None
        analyzer.page_image = SimpleNamespace(
            slider_time=SimpleNamespace(value=lambda: 0),
        )
        return analyzer

    def test_direction_dialog_maps_selection_and_cancel(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        with patch("refactored_app.QInputDialog") as dialog_type:
            dialog = dialog_type.return_value
            dialog_type.Accepted = 1
            dialog.exec_.return_value = 1
            dialog.textValue.return_value = "Y 轴（Ky）"
            self.assertEqual(analyzer._ask_second_derivative_axis(), 1)
            style_sheet = dialog.setStyleSheet.call_args.args[0]
            self.assertIn("color: #FFFFFF", style_sheet)
            self.assertIn("QAbstractItemView", style_sheet)

        with patch("refactored_app.QInputDialog") as dialog_type:
            dialog_type.Accepted = 1
            dialog_type.return_value.exec_.return_value = 0
            self.assertIsNone(analyzer._ask_second_derivative_axis())

    def test_selected_axis_is_used_for_3d_data(self):
        shape = (7, 8, 9)
        for derivative_axis, axis_size in enumerate(shape):
            with self.subTest(axis=derivative_axis):
                coordinates = np.linspace(-1.0, 1.0, axis_size)
                reshape = [1, 1, 1]
                reshape[derivative_axis] = axis_size
                source = np.broadcast_to(
                    -(coordinates.reshape(reshape) ** 2),
                    shape,
                )

                actual = My3DAnalyzer._compute_axis_second_derivative(
                    source,
                    coordinate_axis=coordinates,
                    derivative_axis=derivative_axis,
                    sigma=0.0,
                )

                np.testing.assert_allclose(actual, 2.0, rtol=1e-11, atol=1e-11)

    def test_unselected_axis_variation_has_no_response(self):
        x = np.linspace(-1.0, 1.0, 7)
        y = np.linspace(-2.0, 2.0, 8)
        source = np.broadcast_to(-(y[None, :, None] ** 2), (x.size, y.size, 9))

        actual = My3DAnalyzer._compute_axis_second_derivative(
            source,
            coordinate_axis=x,
            derivative_axis=0,
            sigma=0.0,
        )

        np.testing.assert_allclose(actual, 0.0, atol=1e-12)

    def test_home_and_time_integral_sources_build_3d_contexts(self):
        analyzer = self._bare_analyzer()
        coordinates = {
            "X": np.linspace(-1.0, 1.0, 5),
            "Y": np.linspace(-1.0, 1.0, 6),
            "E": np.linspace(-0.5, 0.5, 7),
            "delay": np.asarray([0.0, 1.0]),
        }
        energy_profile = -(coordinates["E"][None, None, :] ** 2)
        frame = np.broadcast_to(energy_profile, (5, 6, 7)).astype(np.float32)
        raw_data = np.stack([frame, frame], axis=3)

        home_context = analyzer._build_second_derivative_context_from_params(
            raw_data,
            coordinates,
            {
                "source_page_kind": "home",
                "source_view": "3d",
                "derivative_axis": 2,
                "source_t_index": 0,
            },
        )
        integral_context = analyzer._build_second_derivative_context_from_params(
            raw_data,
            coordinates,
            {
                "source_page_kind": "time_integral",
                "source_view": "3d",
                "derivative_axis": 2,
                "source_t_low": 0,
                "source_t_up": 1,
            },
        )

        self.assertEqual(home_context["view"], "3d")
        self.assertEqual(home_context["data"].shape, frame.shape)
        self.assertEqual(home_context["source_mode"], "frame")
        self.assertEqual(integral_context["view"], "3d")
        self.assertEqual(integral_context["source_mode"], "time_integral")
        np.testing.assert_allclose(integral_context["data"], home_context["data"] * 2)


if __name__ == "__main__":
    unittest.main()
