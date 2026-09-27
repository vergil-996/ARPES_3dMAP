import unittest
from types import SimpleNamespace

import numpy as np

from bandscope.app.refactored_app import My3DAnalyzer


class _StubControl:
    def __init__(self, value=0):
        self._value = value

    def value(self):
        return self._value


def _axis_page(**overrides):
    params = {
        "axis_index": 1,
        "axis_name": "Y轴",
        "low": 2,
        "up": 4,
        "mid": 3,
        "source_mode": "frame",
        "source_page_kind": "home",
        "source_t_index": 0,
        "source_t_low": 0,
        "source_t_up": 1,
    }
    params.update(overrides)
    return SimpleNamespace(
        page_id="axis-1",
        title="Y轴积分_3",
        page_kind="axis_integral",
        params=params,
    )


def _home_page(**params):
    return SimpleNamespace(
        page_id="home-1",
        title="主页",
        page_kind="home",
        params=dict(params),
    )


class TimeIntegralDerivationTests(unittest.TestCase):
    def _analyzer(self, current_spec, *, shape=(4, 5, 6, 4), t_low=1, t_up=3):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.core = SimpleNamespace(
            raw_data=np.zeros(shape, dtype=np.float32),
            has_time_axis=True,
            logical_to_physical=lambda axis, value: float(value) * 0.5,
        )
        analyzer.page_data = SimpleNamespace(
            s_t_low=_StubControl(t_low),
            s_t_up=_StubControl(t_up),
        )
        analyzer.timeline_bar = SimpleNamespace(slider_time=_StubControl(0))
        analyzer.axis_source_mode = "frame"
        analyzer.home_slice_info = None
        added = []
        analyzer.left_workspace = SimpleNamespace(
            current_spec=lambda: current_spec,
            page_specs={},
            home_spec=lambda: current_spec,
            page_by_id=lambda page_id: current_spec if page_id == current_spec.page_id else None,
            add_page=lambda spec: added.append(spec),
        )
        analyzer._make_page_id = lambda: "derived-1"
        analyzer._persist_axis_integral_page_state = lambda _spec: None
        analyzer._seed_control_state_for_spec = lambda _spec: None
        analyzer._axis_physical_range = lambda _axis: (-1.0, 1.0, 0.5)
        analyzer._time_physical_range = lambda: (0.0, 3.0, 0.5, 1)
        analyzer._capture_control_state = lambda: {"data_process": {}}
        return analyzer, added

    def test_2d_axis_page_derives_a_2d_time_integral_page(self):
        analyzer, added = self._analyzer(_axis_page())

        analyzer.on_apply_time_integral()

        self.assertEqual(len(added), 1)
        spec = added[0]
        self.assertEqual(spec.page_kind, "axis_integral")
        self.assertEqual(spec.source_page_id, "axis-1")
        self.assertEqual(spec.params["axis_index"], 1)
        self.assertEqual(spec.params["axis_name"], "Y轴")
        self.assertEqual(spec.params["low"], 2)
        self.assertEqual(spec.params["up"], 4)
        self.assertEqual(spec.params["mid"], 3)
        self.assertEqual(spec.params["source_mode"], "time_integral")
        self.assertEqual(spec.params["source_t_low"], 1)
        self.assertEqual(spec.params["source_t_up"], 3)
        self.assertEqual(spec.title, "时间积分_Y轴积分_3")

    def test_derived_page_seeds_axis_window_and_time_range(self):
        analyzer, added = self._analyzer(_axis_page())

        analyzer.on_apply_time_integral()

        control_state = added[0].params["control_state"]
        data_state = control_state["data_process"]
        self.assertEqual(control_state["axis_source_mode"], "time_integral")
        self.assertEqual(data_state["combo_ax"], {"index": 1, "text": "Y轴"})
        self.assertEqual(data_state["s_ax_low"], {"minimum": 0, "maximum": 4, "value": 2})
        self.assertEqual(data_state["s_ax_up"]["value"], 4)
        self.assertEqual(data_state["s_ax_mid"]["value"], 3)
        self.assertEqual(data_state["input_ax_mid"]["value"], 1.5)
        self.assertEqual(data_state["s_t_low"]["value"], 1)
        self.assertEqual(data_state["s_t_up"]["value"], 3)
        self.assertEqual(data_state["locked_half_width"], 1)

    def test_derived_2d_page_reads_the_time_integrated_volume(self):
        source = _axis_page()
        analyzer, added = self._analyzer(source)
        analyzer.on_apply_time_integral()

        integral_calls = []
        analyzer._get_rotated_time_integral = (
            lambda _raw, low, up: integral_calls.append((low, up))
            or np.zeros((4, 5, 6), dtype=np.float32)
        )
        context = analyzer._get_3d_source_context_for_axis(
            added[0],
            analyzer.core.raw_data,
        )

        self.assertEqual(integral_calls, [(1, 3)])
        self.assertEqual(context["view"], "3d")

    def test_crop_page_keeps_its_rectangle(self):
        source = _axis_page(
            crop_k_low=1,
            crop_k_up=3,
            crop_e_low=2,
            crop_e_up=5,
        )
        source.page_kind = "axis_integral_crop"
        analyzer, added = self._analyzer(source)

        analyzer.on_apply_time_integral()

        spec = added[0]
        self.assertEqual(spec.page_kind, "axis_integral_crop")
        self.assertEqual(spec.params["crop_k_low"], 1)
        self.assertEqual(spec.params["crop_k_up"], 3)
        self.assertEqual(spec.params["crop_e_low"], 2)
        self.assertEqual(spec.params["crop_e_up"], 5)
        self.assertEqual(spec.params["source_mode"], "time_integral")
        self.assertEqual(spec.title, "时间积分_Y轴积分裁剪_3")

    def test_home_slice_view_still_derives_a_2d_slice(self):
        analyzer, added = self._analyzer(_home_page())
        analyzer.home_slice_info = {"axis": 0, "index": 2}

        analyzer.on_apply_time_integral()

        spec = added[0]
        self.assertEqual(spec.page_kind, "axis_integral")
        self.assertEqual(spec.params["axis_index"], 0)
        self.assertEqual(spec.params["low"], 2)
        self.assertEqual(spec.params["up"], 2)
        self.assertEqual(spec.params["mid"], 2)
        self.assertEqual(spec.params["source_mode"], "time_integral")
        self.assertEqual(spec.source_page_id, "home-1")
        self.assertEqual(spec.title, "时间积分_X轴切片_3")

    def test_home_3d_view_still_derives_a_volume_page(self):
        analyzer, added = self._analyzer(_home_page())

        analyzer.on_apply_time_integral()

        spec = added[0]
        self.assertEqual(spec.page_kind, "time_integral")
        self.assertEqual(spec.params, {"t_low": 1, "t_up": 3})


if __name__ == "__main__":
    unittest.main()
