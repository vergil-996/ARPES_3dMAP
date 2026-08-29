import unittest
from types import SimpleNamespace

import numpy as np

from refactored_app import My3DAnalyzer


class EnergyDosTimeIntegralTests(unittest.TestCase):
    @staticmethod
    def _analyzer():
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.page_data = SimpleNamespace(
            s_t_low=SimpleNamespace(value=lambda: 0),
            s_t_up=SimpleNamespace(value=lambda: 0),
        )
        analyzer._current_delay_text = lambda t: f"{t:.3f}"
        return analyzer

    def test_energy_dos_from_time_integral_uses_integrated_volume(self):
        analyzer = self._analyzer()
        coords = {
            "E": np.asarray([-0.2, -0.1, 0.0]),
            "delay": np.asarray([0.0, 1.0, 2.0]),
        }
        raw_data = np.zeros((2, 2, 3, 3), dtype=np.float32)
        integrated = np.full((2, 2, 3), 3.0, dtype=np.float32)
        calls = []

        def fake_integral(raw, low, up):
            calls.append((low, up))
            return integrated

        analyzer._get_rotated_time_integral = fake_integral
        spec = SimpleNamespace(
            params={
                "source_page_kind": "time_integral",
                "source_t_low": 0,
                "source_t_up": 1,
            }
        )

        context = analyzer._get_energy_dos_context(spec, raw_data, coords)

        self.assertEqual(calls, [(0, 1)])
        self.assertEqual(context["view"], "1d")
        np.testing.assert_allclose(context["x_data"], coords["E"])
        np.testing.assert_allclose(context["y_data"], [12.0, 12.0, 12.0])
        self.assertIn("Time-integrated", context["title"])

    def test_time_integral_energy_dos_locks_time_slider(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        locked = SimpleNamespace(
            page_kind="energy_dos", params={"source_page_kind": "time_integral"}
        )
        unlocked = SimpleNamespace(
            page_kind="energy_dos", params={"source_page_kind": "home"}
        )
        self.assertTrue(analyzer._is_time_locked_page(locked))
        self.assertFalse(analyzer._is_time_locked_page(unlocked))

    def test_build_energy_dos_spec_marks_time_integral_source(self):
        analyzer = self._analyzer()
        analyzer._make_page_id = lambda: "dos-1"
        analyzer._seed_control_state_for_spec = lambda spec: None
        current = SimpleNamespace(
            page_id="ti-1",
            page_kind="time_integral",
            params={"t_low": 1, "t_up": 3},
        )
        analyzer.left_workspace = SimpleNamespace(current_spec=lambda: current)
        analyzer.page_image = SimpleNamespace(
            slider_time=SimpleNamespace(value=lambda: 2)
        )

        spec = analyzer._build_energy_dos_spec()

        self.assertEqual(spec.page_kind, "energy_dos")
        self.assertEqual(spec.source_page_id, "ti-1")
        self.assertEqual(spec.params["source_page_kind"], "time_integral")
        self.assertEqual(spec.params["source_t_low"], 1)
        self.assertEqual(spec.params["source_t_up"], 3)
        self.assertIn("Time-integrated", spec.title)


if __name__ == "__main__":
    unittest.main()
