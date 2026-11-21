import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import bandscope.ui.theme as theme
from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.axis_interval import AxisInterval
from bandscope.ui.axis_interval_controller import IntervalEditMode


class _StubControl:
    def __init__(self, value=0):
        self._value = value
        self.enabled = True

    def value(self):
        return self._value

    def currentIndex(self):
        return int(self._value)

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
        )

        np.testing.assert_allclose(actual, 2.0, rtol=1e-11, atol=1e-11)

    def test_physical_energy_spacing_sets_derivative_scale(self):
        energy = np.arange(9, dtype=np.float64) * 0.25
        source = np.broadcast_to(-(energy[None, :] ** 2), (4, energy.size))

        actual = My3DAnalyzer._compute_energy_second_derivative(
            source,
            energy_axis=energy,
        )

        np.testing.assert_allclose(actual, 2.0, rtol=1e-11, atol=1e-11)


class VolumeSecondDerivativeTests(unittest.TestCase):
    @staticmethod
    def _bare_analyzer():
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.rotation_angle = 0.0
        analyzer._rotation_cache = {}
        analyzer._second_derivative_volume_cache = None
        analyzer.timeline_bar = SimpleNamespace(
            slider_time=SimpleNamespace(value=lambda: 0),
        )
        return analyzer

    def test_direction_dialog_maps_selection_and_cancel(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        with patch("bandscope.app.refactored_app.QInputDialog") as dialog_type:
            dialog = dialog_type.return_value
            dialog_type.Accepted = 1
            dialog.exec_.return_value = 1
            dialog.textValue.return_value = "Y 轴（Ky）"
            self.assertEqual(analyzer._ask_second_derivative_axis(), 1)
            style_sheet = dialog.setStyleSheet.call_args.args[0]
            self.assertIn(f"color: {theme.TEXT_1}", style_sheet)
            self.assertIn("QAbstractItemView", style_sheet)

        with patch("bandscope.app.refactored_app.QInputDialog") as dialog_type:
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
        )

        np.testing.assert_allclose(actual, 0.0, atol=1e-12)

    def test_home_frame_and_time_range_build_3d_contexts(self):
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
        analyzer.core = SimpleNamespace(
            raw_data=raw_data,
            coords=coordinates,
            coord_units={},
            coord_sources={},
            has_time_axis=True,
        )
        analyzer._computed_volume_cache = None

        home_context = analyzer._build_second_derivative_context_from_params(
            raw_data,
            coordinates,
            {
                "source_page_kind": "home",
                "source_view": "3d",
                "derivative_axis": 2,
                "time_interval": AxisInterval.from_indices(
                    coordinates["delay"], 0, 0, locked=True, axis_key="delay"
                ).as_dict(),
            },
        )
        integral_context = analyzer._build_second_derivative_context_from_params(
            raw_data,
            coordinates,
            {
                "source_page_kind": "home",
                "source_view": "3d",
                "derivative_axis": 2,
                "time_interval": AxisInterval.from_indices(
                    coordinates["delay"], 0, 1, axis_key="delay"
                ).as_dict(),
            },
        )

        self.assertEqual(home_context["view"], "3d")
        self.assertEqual(home_context["data"].shape, frame.shape)
        self.assertEqual(home_context["source_t_indices"], (0, 0))
        self.assertEqual(integral_context["view"], "3d")
        self.assertEqual(integral_context["source_t_indices"], (0, 1))
        np.testing.assert_allclose(integral_context["data"], home_context["data"] * 2)


class SecondDerivativeSliderControlTests(unittest.TestCase):
    """2D 二阶导页与来源切片共用可解锁加厚的物理区间。"""

    @staticmethod
    def _analyzer_with_axis_interval(*, axis_index=0, low=None, up=None, locked=False):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.core = SimpleNamespace(
            raw_data=np.zeros((10, 11, 12, 1)),
            coords={
                "X": np.linspace(-1.0, 1.0, 10),
                "Y": np.linspace(-2.0, 2.0, 11),
                "E": np.linspace(-3.0, 3.0, 12),
                "delay": np.array([0.0]),
            },
            coord_sources={"X": "file", "Y": "file", "E": "file", "delay": "index"},
            coord_units={},
        )
        analyzer.left_workspace = SimpleNamespace(current_spec=lambda: None)
        space = analyzer._axis_space_for_index(axis_index)
        analyzer.axis_space = space
        analyzer.axis_interval = space.as_interval(low=low, up=up, locked=locked)
        return analyzer

    def test_slice_result_preserves_position_and_allows_thickening(self):
        analyzer = self._analyzer_with_axis_interval(axis_index=0, low=0.4, up=0.4)
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
        _axis_index, mode, show_position = analyzer._axis_interval_context(spec)

        # 切片位置取最近采样点；区间仍是零长度。
        self.assertEqual(spec.params["slice_index"], 6)
        self.assertEqual(spec.params["integral_low"], 6)
        self.assertEqual(spec.params["integral_up"], 6)
        self.assertEqual(spec.params["axis_interval"]["low"], spec.params["axis_interval"]["up"])
        self.assertEqual(mode, IntervalEditMode.FULL)
        self.assertTrue(show_position)

    def test_integral_result_keeps_the_full_interval(self):
        # Y 轴步长 0.4：±1.2 正好落在采样点上，中心 0.0 是下标 5。
        analyzer = self._analyzer_with_axis_interval(axis_index=1, low=-1.2, up=1.2)
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
        _axis_index, mode, show_position = analyzer._axis_interval_context(spec)

        self.assertEqual(spec.params["integral_low"], 2)
        self.assertEqual(spec.params["integral_up"], 8)
        self.assertEqual(spec.params["integral_mid"], 5)
        self.assertEqual(mode, IntervalEditMode.FULL)
        self.assertTrue(show_position)

    def test_3d_derivative_page_disables_the_interval_controls(self):
        analyzer = self._analyzer_with_axis_interval()
        derivative_3d = SimpleNamespace(
            page_kind="second_derivative",
            params={"source_view": "3d", "source_page_kind": "home"},
        )
        regular_page = SimpleNamespace(page_kind="home", params={})

        self.assertEqual(
            analyzer._axis_interval_context(derivative_3d)[1], IntervalEditMode.DISABLED
        )
        _axis_index, mode, show_position = analyzer._axis_interval_context(regular_page)
        self.assertEqual(mode, IntervalEditMode.DISABLED)
        self.assertFalse(show_position)

    def test_new_slice_result_seeds_a_zero_length_interval(self):
        analyzer = self._analyzer_with_axis_interval()
        analyzer._capture_control_state = lambda: {"active_control_tab": 0}
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

        interval = spec.params["axis_interval"]
        self.assertEqual(interval["axis_key"], "Y")
        self.assertEqual(interval["low"], interval["up"])
        self.assertEqual((spec.params["low"], spec.params["up"], spec.params["mid"]), (4, 4, 4))
        self.assertEqual(spec.params["axis_interval"]["axis_key"], "Y")
        self.assertNotIn("data_process", spec.params["control_state"])

    def test_new_integral_result_seeds_the_integral_window(self):
        analyzer = self._analyzer_with_axis_interval()
        analyzer._capture_control_state = lambda: {"active_control_tab": 0}
        spec = SimpleNamespace(
            page_kind="second_derivative",
            params={
                "source_view": "2d",
                "source_page_kind": "axis_integral",
                "axis_index": 1,
                "integral_low": 2,
                "integral_up": 8,
                "integral_mid": 5,
            },
        )

        analyzer._seed_second_derivative_axis_control_state(spec)

        interval = spec.params["axis_interval"]
        self.assertEqual(interval["axis_key"], "Y")
        self.assertLess(interval["low"], interval["up"])
        self.assertEqual((spec.params["low"], spec.params["up"], spec.params["mid"]), (2, 8, 5))


if __name__ == "__main__":
    unittest.main()
