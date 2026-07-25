import unittest
from types import SimpleNamespace

import numpy as np

from refactored_app import My3DAnalyzer


class _CheckedSwitch:
    def isChecked(self):
        return True


class EAxisFlipTests(unittest.TestCase):
    @staticmethod
    def _analyzer(coords=None):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.page_image = SimpleNamespace(switch_flip=_CheckedSwitch())
        analyzer.core = SimpleNamespace(coords=coords or {})
        return analyzer

    def test_3d_flip_changes_only_e_data_and_e_coordinates(self):
        coords = {
            "X": np.asarray([-1.0, 1.0]),
            "Y": np.asarray([-2.0, 0.0, 2.0]),
            "E": np.asarray([-0.3, -0.2, -0.1, 0.0]),
            "delay": np.asarray([0.0]),
        }
        data = np.arange(2 * 3 * 4, dtype=np.float32).reshape(2, 3, 4)
        actual = self._analyzer(coords)._render_context_for_visual_flip(
            {"view": "3d", "data": data, "coords": coords}
        )
        np.testing.assert_array_equal(actual["data"], data[:, :, ::-1])
        np.testing.assert_array_equal(actual["coords"]["X"], coords["X"])
        np.testing.assert_array_equal(actual["coords"]["Y"], coords["Y"])
        np.testing.assert_array_equal(actual["coords"]["E"], coords["E"][::-1])
        self.assertNotIn("actor_flip", actual)

    def test_logical_box_mirrors_only_e_bounds(self):
        analyzer = self._analyzer()
        actual = analyzer._map_visual_flip_logical_bounds(
            [1, 5, 2, 6, 3, 7],
            (10, 11, 12),
        )
        self.assertEqual(actual, [1, 5, 2, 6, 4, 8])

    def test_energy_dos_and_waterfall_reverse_energy_samples(self):
        analyzer = self._analyzer()
        curve = analyzer._render_context_for_visual_flip(
            {
                "view": "1d",
                "x_data": np.asarray([-0.2, -0.1, 0.0]),
                "y_data": np.asarray([1.0, 2.0, 3.0]),
                "xlabel": "Energy (eV)",
            }
        )
        np.testing.assert_array_equal(curve["y_data"], [3.0, 2.0, 1.0])

        waterfall = analyzer._render_context_for_visual_flip(
            {
                "view": "waterfall",
                "energy_axis": np.asarray([-0.2, -0.1, 0.0]),
                "curves": np.asarray([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]),
                "raw_curves": np.asarray([[10.0, 20.0, 30.0]]),
            }
        )
        np.testing.assert_array_equal(waterfall["curves"], [[3.0, 2.0, 1.0], [6.0, 5.0, 4.0]])
        np.testing.assert_array_equal(waterfall["raw_curves"], [[30.0, 20.0, 10.0]])

    def test_non_energy_1d_curve_is_unchanged(self):
        analyzer = self._analyzer()
        context = {
            "view": "1d",
            "x_data": np.asarray([0.0, 1.0]),
            "y_data": np.asarray([2.0, 3.0]),
            "xlabel": "Delay (ps)",
        }
        actual = analyzer._render_context_for_visual_flip(context)
        np.testing.assert_array_equal(actual["y_data"], context["y_data"])


if __name__ == "__main__":
    unittest.main()
