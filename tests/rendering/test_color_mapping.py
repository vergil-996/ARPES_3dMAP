import unittest

import numpy as np
import pyvista as pv
from matplotlib import colormaps
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize

from bandscope.rendering.color_mapping import LeveledColormap


class LeveledColormapTests(unittest.TestCase):
    def test_gamma_preserves_weak_signals_before_palette_lookup(self):
        source = np.array([0.0, 1e-6, 1e-5, 1e-4, 1e-3])
        cmap = LeveledColormap("gray", {"gamma_power": 0.1})
        actual = cmap(source, bytes=True)
        expected = colormaps["gray"](source ** 0.1, bytes=True)
        np.testing.assert_array_equal(actual, expected)
        self.assertTrue(np.all(np.diff(actual[:, 0].astype(int)) > 0))

    def test_linear_default_matches_the_original_palette(self):
        source = np.r_[np.linspace(-0.1, 1.1, 1001), -np.inf, np.inf, np.nan]
        for name in ("magma", "gray", "viridis"):
            with self.subTest(palette=name):
                cmap = LeveledColormap(name, {"gamma_power": 1.0})
                for as_bytes in (False, True):
                    np.testing.assert_array_equal(
                        cmap(source, bytes=as_bytes), colormaps[name](source, bytes=as_bytes),
                    )

    def test_black_and_white_positions_do_not_truncate_the_palette(self):
        cmap = LeveledColormap("magma", {
            "gamma_power": 1.0, "black_pos": 0.2, "white_pos": 0.8,
        })
        np.testing.assert_array_equal(cmap([0.0, 1.0]), colormaps["magma"]([0.0, 1.0]))

    def test_masks_nans_and_alpha_match_palette_behavior(self):
        source = np.ma.array([0.0, 1e-6, 0.3, np.nan], mask=[False, False, True, False])
        alpha = np.array([0.0, 0.3, 0.5, 1.0])
        for bad in ((0, 0, 0, 0), (1, 0, 0, 0.4)):
            palette = colormaps["magma"].with_extremes(bad=bad)
            cmap = LeveledColormap(palette, {"gamma_power": 0.1})
            expected_source = np.ma.masked_invalid(
                np.ma.array(np.asarray(source.data) ** 0.1, mask=source.mask),
            )
            for as_bytes in (False, True):
                np.testing.assert_array_equal(
                    cmap(source, alpha=alpha, bytes=as_bytes),
                    palette(expected_source, alpha=alpha, bytes=as_bytes),
                )

    def test_extreme_colors_and_copies_remain_independent(self):
        palette = colormaps["gray"].with_extremes(under="red", over="blue", bad="yellow")
        cmap = LeveledColormap(palette, {"gamma_power": 0.1})
        source = [-np.inf, -0.01, 1.01, np.inf, np.nan]
        np.testing.assert_array_equal(cmap(source), palette(source))
        copied = cmap.with_extremes(under="green", over="white", bad="cyan")
        self.assertNotEqual(copied(-1.0), cmap(-1.0))
        self.assertNotEqual(copied(2.0), cmap(2.0))
        self.assertNotEqual(copied(np.nan), cmap(np.nan))
        np.testing.assert_array_equal(cmap(source), palette(source))
        cmap.set_under("black")
        cmap.set_over("red")
        cmap.set_bad("green")
        np.testing.assert_array_equal(cmap(-1.0), cmap.get_under())
        np.testing.assert_array_equal(cmap(2.0), cmap.get_over())
        np.testing.assert_array_equal(cmap(np.nan), cmap.get_bad())
        np.testing.assert_array_equal(palette(source), colormaps["gray"].with_extremes(
            under="red", over="blue", bad="yellow",
        )(source))

    def test_scalar_integer_indices_and_alpha_validation(self):
        palette = colormaps["magma"]
        cmap = LeveledColormap(palette, {"gamma_power": 1.0})
        self.assertIsInstance(cmap(0.5), tuple)
        self.assertIsInstance(cmap(0.5, bytes=True)[0], np.uint8)
        indices = np.array([-1, 0, 1, 127, 254, 255, 256])
        np.testing.assert_array_equal(cmap(indices), palette(indices))
        with self.assertRaises(ValueError):
            cmap([0.0, 0.5], alpha=[0.1, 0.2, 0.3])
        np.testing.assert_array_equal(cmap([0.0, 1.0], alpha=[-1, 2])[:, 3], [0, 1])

    def test_resampling_and_reversal_keep_the_curve_analytic(self):
        cmap = LeveledColormap("gray", {"gamma_power": 0.1})
        source = np.array([0.0, 1e-6, 0.25, 0.75, 1.0])
        reversed_cmap = cmap.reversed()
        np.testing.assert_array_equal(reversed_cmap(source), cmap(1.0 - source))
        np.testing.assert_array_equal(reversed_cmap.reversed()(source), cmap(source))
        resampled = cmap.resampled(32)
        self.assertEqual(resampled.N, 32)
        np.testing.assert_array_equal(
            resampled(source), colormaps["gray"].resampled(32)(source ** 0.1),
        )

    def test_colormap_works_with_matplotlib_and_pyvista(self):
        cmap = LeveledColormap("magma", {"gamma_power": 0.1})
        source = np.array([0.0, 1e-6, 1.0])
        mappable = ScalarMappable(norm=Normalize(0.0, 1.0), cmap=cmap)
        np.testing.assert_array_equal(mappable.to_rgba(source), cmap(source))
        lut = pv.LookupTable(cmap=cmap, n_values=64)
        np.testing.assert_array_equal(lut.values, (cmap(np.linspace(0, 1, 64)) * 255).astype(np.uint8))


if __name__ == "__main__":
    unittest.main()
