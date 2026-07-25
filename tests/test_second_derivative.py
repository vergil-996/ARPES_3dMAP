import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from refactored_app import My3DAnalyzer


class _StubControl:
    def __init__(self, value=0):
        self._value = value
        self.enabled = True

    def value(self):
        return self._value

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)


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


class SecondDerivativeSliderControlTests(unittest.TestCase):
    @staticmethod
    def _analyzer_with_axis_controls(low=0, up=0, mid=0):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.core = SimpleNamespace(raw_data=np.zeros((10, 11, 12, 1)))
        analyzer.page_data = SimpleNamespace(
            combo_ax=_StubControl(),
            s_ax_low=_StubControl(low),
            s_ax_up=_StubControl(up),
            s_ax_mid=_StubControl(mid),
            input_ax_low=_StubControl(),
            input_ax_up=_StubControl(),
            input_ax_mid=_StubControl(),
        )
        return analyzer

    def test_slice_result_uses_only_center_slider(self):
        analyzer = self._analyzer_with_axis_controls(low=1, up=8, mid=6)
        spec = SimpleNamespace(
            page_kind="second_derivative",
            params={
                "source_view": "2d",
                "source_page_kind": "home",
                "slice_axis": 0,
                "slice_index": 2,
            },
        )

        analyzer._persist_second_derivative_page_state(spec)
        analyzer._configure_second_derivative_axis_controls(spec)

        self.assertEqual(spec.params["slice_index"], 6)
        self.assertFalse(analyzer.page_data.combo_ax.enabled)
        self.assertFalse(analyzer.page_data.s_ax_low.enabled)
        self.assertFalse(analyzer.page_data.s_ax_up.enabled)
        self.assertTrue(analyzer.page_data.s_ax_mid.enabled)
        self.assertFalse(analyzer.page_data.input_ax_low.enabled)
        self.assertFalse(analyzer.page_data.input_ax_up.enabled)
        self.assertTrue(analyzer.page_data.input_ax_mid.enabled)

    def test_integral_result_uses_all_three_sliders(self):
        analyzer = self._analyzer_with_axis_controls(low=9, up=2, mid=5)
        spec = SimpleNamespace(
            page_kind="second_derivative",
            params={
                "source_view": "2d",
                "source_page_kind": "axis_integral",
                "axis_index": 1,
                "integral_low": 1,
                "integral_up": 3,
                "integral_mid": 2,
            },
        )

        analyzer._persist_second_derivative_page_state(spec)
        analyzer._configure_second_derivative_axis_controls(spec)

        self.assertEqual(spec.params["integral_low"], 2)
        self.assertEqual(spec.params["integral_up"], 9)
        self.assertEqual(spec.params["integral_mid"], 5)
        self.assertFalse(analyzer.page_data.combo_ax.enabled)
        self.assertTrue(analyzer.page_data.s_ax_low.enabled)
        self.assertTrue(analyzer.page_data.s_ax_up.enabled)
        self.assertTrue(analyzer.page_data.s_ax_mid.enabled)

    def test_non_derivative_page_reenables_axis_controls(self):
        analyzer = self._analyzer_with_axis_controls()
        derivative_3d = SimpleNamespace(
            page_kind="second_derivative",
            params={"source_view": "3d", "source_page_kind": "home"},
        )
        regular_page = SimpleNamespace(page_kind="home", params={})

        analyzer._configure_second_derivative_axis_controls(derivative_3d)
        self.assertFalse(analyzer.page_data.s_ax_mid.enabled)
        analyzer._configure_second_derivative_axis_controls(regular_page)

        self.assertTrue(analyzer.page_data.combo_ax.enabled)
        self.assertTrue(analyzer.page_data.s_ax_low.enabled)
        self.assertTrue(analyzer.page_data.s_ax_up.enabled)
        self.assertTrue(analyzer.page_data.s_ax_mid.enabled)

    def test_new_slice_result_seeds_axis_and_center_control_state(self):
        analyzer = self._analyzer_with_axis_controls()
        analyzer.core.logical_to_physical = lambda _axis, value: float(value) * 0.25
        analyzer._axis_physical_range = lambda _axis: (-1.0, 1.0, 0.25)
        analyzer._capture_control_state = lambda: {"data_process": {}}
        spec = SimpleNamespace(
            page_kind="second_derivative",
            params={
                "source_view": "2d",
                "source_page_kind": "home",
                "slice_axis": 1,
                "slice_index": 4,
            },
        )

        analyzer._seed_second_derivative_axis_control_state(spec)

        data_state = spec.params["control_state"]["data_process"]
        self.assertEqual(data_state["combo_ax"]["index"], 1)
        self.assertEqual(data_state["s_ax_low"]["value"], 4)
        self.assertEqual(data_state["s_ax_up"]["value"], 4)
        self.assertEqual(data_state["s_ax_mid"]["value"], 4)
        self.assertEqual(data_state["input_ax_mid"]["value"], 1.0)
        self.assertEqual(data_state["locked_half_width"], 0)


if __name__ == "__main__":
    unittest.main()
