"""能级态密度对当前页时间区间的数值与导出元数据测试。"""
import unittest
from types import SimpleNamespace

import numpy as np

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.axis_interval import AxisInterval


def _analyzer():
    analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
    analyzer.core = SimpleNamespace(
        raw_data=np.ones((2, 2, 3, 3), dtype=np.float32),
        coords={
            "X": np.arange(2),
            "Y": np.arange(2),
            "E": np.asarray([-0.2, -0.1, 0.0]),
            "delay": np.asarray([0.0, 1.0, 2.0]),
        },
        coord_units={},
        coord_sources={},
        has_time_axis=True,
    )
    analyzer.rotation_angle = 0.0
    analyzer._computed_volume_cache = None
    analyzer._current_delay_text = lambda index: f"{index:.0f} fs"
    return analyzer


def _spec(low, up):
    delay = np.asarray([0.0, 1.0, 2.0])
    return SimpleNamespace(
        page_id="dos",
        page_kind="energy_dos",
        source_page_id=None,
        params={
            "source_page_kind": "home",
            "time_interval": AxisInterval.from_indices(
                delay, low, up, locked=low == up, axis_key="delay"
            ).as_dict(),
        },
    )


class EnergyDosTimeIntervalTests(unittest.TestCase):
    def test_energy_dos_uses_the_current_page_time_range(self):
        analyzer = _analyzer()
        context = analyzer._get_energy_dos_context(
            _spec(0, 1), analyzer.core.raw_data, analyzer.core.coords
        )

        np.testing.assert_allclose(context["y_data"], [8.0, 8.0, 8.0])
        self.assertIn("0 fs~1 fs", context["title"])

    def test_energy_dos_export_uses_the_same_range(self):
        analyzer = _analyzer()
        _title, _name, payload = analyzer._build_energy_dos_export_payload(
            _spec(1, 2), analyzer.core.raw_data, analyzer.core.coords
        )

        np.testing.assert_allclose(payload["time_range"], [1.0, 2.0])
        np.testing.assert_allclose(payload["intensity"], [8.0, 8.0, 8.0])


if __name__ == "__main__":
    unittest.main()
