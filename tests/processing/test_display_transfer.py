"""Weak spectra must reach the transfer curve before display quantization."""
import unittest

import numpy as np

from bandscope.processing.display_transfer import OPACITY_MAPS, channels, level_info


class DisplayTransferTests(unittest.TestCase):
    def test_gamma_preserves_weak_samples_beside_a_bright_outlier(self):
        source = np.array([0., .01, 1., 100., 1000., 1e6], dtype=np.float32)
        original = source.copy()
        info = level_info((0., 1e6), (0, 100, 100))
        expected = (source.astype(np.float64) / 1e6) ** .1
        for view in ("2d", "3d"):
            with self.subTest(view=view):
                color, alpha = channels(source, info, "linear", view=view)
                np.testing.assert_allclose(color, expected, rtol=1e-6, atol=1e-8)
                self.assertTrue(np.all(np.diff(color) > 0))
                self.assertGreater(float(color[1]), .15)
                if view == "3d":
                    np.testing.assert_allclose(alpha, expected, rtol=1e-6, atol=1e-8)
                else:
                    np.testing.assert_array_equal(alpha, 1.)
        np.testing.assert_array_equal(source, original)

    def test_black_white_window_uses_the_full_palette(self):
        source = np.array([0., 20., 35., 50., 65., 80., 100.])
        info = level_info((0., 100.), (20, 50, 80))
        expected = [0., 0., .25, .5, .75, 1., 1.]
        for view in ("2d", "3d"):
            with self.subTest(view=view):
                color, _ = channels(source, info, "linear", view=view)
                np.testing.assert_allclose(color, expected, atol=1e-7)

    def test_opacity_curve_is_evaluated_after_gamma(self):
        source = np.array([0., 1e-8, 1e-6, 1e-4, .02, .5, 1.])
        info = level_info((0., 1.), (0, 100, 100))
        positions = source ** .1
        for mode in ("linear", "log", "power", "sigmoid", "线性", "对数", "幂函数"):
            with self.subTest(mode=mode):
                values = OPACITY_MAPS[mode]
                expected = np.interp(positions, np.linspace(0., 1., len(values)), values)
                _, alpha = channels(source, info, mode)
                np.testing.assert_allclose(alpha, expected, rtol=1e-6, atol=1e-8)

    def test_nonfinite_values_are_hidden_without_changing_source(self):
        source = np.array([-5., 0., 5., 10., np.nan, np.inf, -np.inf], dtype=np.float32)
        original = source.copy()
        info = level_info((0., 10.), (0, 100, 100))
        for view in ("2d", "3d"):
            with self.subTest(view=view):
                color, alpha = channels(source, info, "linear", view=view)
                self.assertTrue(np.isfinite(color).all())
                self.assertTrue(np.isfinite(alpha).all())
                np.testing.assert_array_equal(color[-3:], 0.)
                np.testing.assert_array_equal(alpha[-3:], 0.)
                self.assertEqual(float(color[0]), 0.)
                self.assertEqual(float(color[3]), 1.)
        np.testing.assert_array_equal(source, original)


if __name__ == "__main__":
    unittest.main()
