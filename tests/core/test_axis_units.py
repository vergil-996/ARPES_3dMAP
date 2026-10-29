"""坐标单位约定：时间轴缺单位时默认 fs，其它轴仍不猜。"""
import unittest

from bandscope.core.axis_units import DEFAULT_TIME_UNIT, TIME_AXIS_KEY, resolve_axis_unit


class AxisUnitDefaultsTests(unittest.TestCase):
    def test_time_axis_falls_back_to_fs(self):
        self.assertEqual(resolve_axis_unit(TIME_AXIS_KEY, None), DEFAULT_TIME_UNIT)
        self.assertEqual(resolve_axis_unit("delay", ""), "fs")
        self.assertEqual(resolve_axis_unit("delay", "   "), "fs")

    def test_explicit_time_unit_wins(self):
        self.assertEqual(resolve_axis_unit("delay", "ps"), "ps")
        self.assertEqual(resolve_axis_unit("delay", " ns "), "ns")

    def test_other_axes_are_never_guessed(self):
        self.assertEqual(resolve_axis_unit("E", None), "")
        self.assertEqual(resolve_axis_unit("X", ""), "")
        self.assertEqual(resolve_axis_unit("E", " eV "), "eV")

    def test_unknown_axis_is_left_alone(self):
        self.assertEqual(resolve_axis_unit("T", None), "")


if __name__ == "__main__":
    unittest.main()
