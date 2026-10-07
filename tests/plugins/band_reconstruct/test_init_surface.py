# -*- coding: utf-8 -*-
"""初始化面：解析函数、网格重采样、对齐参数与外部导入。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from plugins.band_reconstruct.init_surface import (
    SurfaceGrid,
    align_surface,
    fractional_index,
    gaussian_surface,
    load_surface_grid,
    parabolic_surface,
    plane_surface,
    resample_surface,
)


class AnalyticSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.x = np.linspace(-1.0, 1.0, 11)
        self.y = np.linspace(-0.5, 0.5, 7)

    def test_plane_values(self):
        surface = plane_surface(self.x, self.y, e0=0.1, slope_x=0.2, slope_y=-0.4)
        self.assertEqual(surface.shape, (11, 7))
        self.assertAlmostEqual(float(surface[0, 0]), 0.1 - 0.2 + 0.4 * 0.5)
        self.assertAlmostEqual(float(surface[10, 6]), 0.1 + 0.2 - 0.2)

    def test_parabolic_minimum_at_center(self):
        surface = parabolic_surface(self.x, self.y, e0=-0.2, a_x=0.5, a_y=0.5)
        row, column = np.unravel_index(np.argmin(surface), surface.shape)
        self.assertAlmostEqual(float(self.x[row]), 0.0)
        self.assertAlmostEqual(float(self.y[column]), 0.0)
        self.assertAlmostEqual(float(surface.min()), -0.2)

    def test_parabolic_explicit_center(self):
        surface = parabolic_surface(
            self.x, self.y, e0=0.0, a_x=1.0, a_y=1.0, kx0=0.5, ky0=-0.25
        )
        # 中心不在网格点上：取最近的网格点，值应最接近 e0。
        row, column = np.unravel_index(np.argmin(surface), surface.shape)
        self.assertLess(abs(float(self.x[row]) - 0.5), 0.2)
        self.assertLess(abs(float(self.y[column]) + 0.25), 0.1)
        self.assertAlmostEqual(float(surface.min()), 0.0, delta=0.05)

    def test_gaussian_peak_and_width(self):
        surface = gaussian_surface(
            self.x, self.y, e0=0.0, amplitude=0.3, sigma_x=0.25, sigma_y=0.25
        )
        self.assertAlmostEqual(float(surface.max()), 0.3)
        # 角点 (x=-1, y=-0.5)：到中心 (0, 0) 的加权平方距离 = 1²/0.25² + 0.5²/0.25²。
        expected = 0.3 * np.exp(-0.5 * (1.0**2 / 0.25**2 + 0.5**2 / 0.25**2))
        self.assertAlmostEqual(float(surface[0, 0]), float(expected))

    def test_axes_must_be_monotonic(self):
        with self.assertRaises(ValueError):
            plane_surface(np.array([0.0, 1.0, 0.5]), self.y)
        with self.assertRaises(ValueError):
            plane_surface(np.array([0.0]), self.y)


class FractionalIndexTests(unittest.TestCase):
    def test_exact_at_grid_points(self):
        grid = np.array([-1.0, 0.0, 1.0])
        np.testing.assert_allclose(fractional_index(grid, grid), [0.0, 1.0, 2.0])

    def test_clamped_outside(self):
        grid = np.array([0.0, 1.0, 2.0])
        np.testing.assert_allclose(fractional_index([-5.0, 7.0], grid), [0.0, 2.0])

    def test_descending_grid(self):
        grid = np.array([2.0, 1.0, 0.0])
        np.testing.assert_allclose(fractional_index([2.0, 1.0, 0.0], grid), [0.0, 1.0, 2.0])

    def test_midpoint_interpolates(self):
        grid = np.array([0.0, 2.0])
        np.testing.assert_allclose(fractional_index([1.0], grid), [0.5])


class ResampleTests(unittest.TestCase):
    def test_identity_when_grids_match(self):
        x = np.linspace(-1.0, 1.0, 6)
        y = np.linspace(-1.0, 1.0, 5)
        grid_x, grid_y = np.meshgrid(x, y, indexing="ij")
        surface = grid_x + 2.0 * grid_y
        np.testing.assert_allclose(resample_surface(surface, x, y, x, y), surface, atol=1e-12)

    def test_bilinear_midpoint(self):
        x = np.array([0.0, 1.0])
        y = np.array([0.0, 1.0])
        surface = np.array([[0.0, 2.0], [4.0, 6.0]])
        result = resample_surface(surface, x, y, np.array([0.5]), np.array([0.5]))
        self.assertAlmostEqual(float(result[0, 0]), 3.0)

    def test_out_of_range_clamps_to_edge(self):
        x = np.array([0.0, 1.0])
        y = np.array([0.0, 1.0])
        surface = np.array([[0.0, 2.0], [4.0, 6.0]])
        result = resample_surface(surface, x, y, np.array([-3.0]), np.array([0.0]))
        self.assertAlmostEqual(float(result[0, 0]), 0.0)

    def test_shape_mismatch_raises(self):
        with self.assertRaises(ValueError):
            resample_surface(np.zeros((3, 4)), np.arange(2.0), np.arange(2.0), np.arange(2.0), np.arange(2.0))


class AlignTests(unittest.TestCase):
    def setUp(self):
        self.x = np.linspace(-1.0, 1.0, 41)
        self.y = np.linspace(-1.0, 1.0, 41)

    def test_identity(self):
        surface = plane_surface(self.x, self.y, e0=0.1, slope_x=0.3)
        np.testing.assert_allclose(
            align_surface(surface, self.x, self.y), surface, atol=1e-12
        )

    def test_energy_shift_is_rigid(self):
        surface = plane_surface(self.x, self.y, e0=0.1, slope_x=0.3)
        shifted = align_surface(surface, self.x, self.y, energy_shift=0.05)
        np.testing.assert_allclose(shifted - surface, 0.05, atol=1e-12)

    def test_momentum_scale_stretches_feature(self):
        """``scale=2`` 把中心处的特征沿动量方向拉宽一倍。"""
        surface = parabolic_surface(self.x, self.y, e0=0.0, a_x=1.0, a_y=1.0)
        stretched = align_surface(surface, self.x, self.y, momentum_scale=2.0)
        # 原面在 kx=0.5 处抬升 0.25；拉伸后同一抬升出现在 kx=1.0 处。
        self.assertAlmostEqual(float(stretched[np.argmin(abs(self.x - 1.0)), 20]), 0.25, delta=0.02)
        self.assertAlmostEqual(float(stretched[20, 20]), 0.0, delta=1e-9)

    def test_invalid_scale_and_shape(self):
        surface = plane_surface(self.x, self.y)
        with self.assertRaises(ValueError):
            align_surface(surface, self.x, self.y, momentum_scale=0.0)
        with self.assertRaises(ValueError):
            align_surface(np.zeros((3, 3)), self.x, self.y)


class LoadGridTests(unittest.TestCase):
    def test_npz_roundtrip(self):
        x = np.linspace(-1.0, 1.0, 5)
        y = np.linspace(-1.0, 1.0, 4)
        z = parabolic_surface(x, y, e0=0.0, a_x=1.0, a_y=1.0)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bands.npz"
            np.savez(path, z=z, x=x, y=y, label="DFT band 1")
            grid = load_surface_grid(path)
        self.assertEqual(grid.shape, (5, 4))
        self.assertEqual(grid.label, "DFT band 1")
        self.assertEqual(grid.source, "bands.npz")
        np.testing.assert_allclose(grid.z, z)

    def test_npz_aliases(self):
        x = np.linspace(-1.0, 1.0, 4)
        y = np.linspace(-1.0, 1.0, 3)
        z = parabolic_surface(x, y)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "aliases.npz"
            np.savez(path, E=z, kx=x, ky=y)
            grid = load_surface_grid(path)
        self.assertEqual(grid.shape, (4, 3))

    def test_npz_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            folder_path = Path(folder)
            missing = folder_path / "missing.npz"
            np.savez(missing, z=np.zeros((2, 2)), x=np.arange(2.0))
            with self.assertRaises(ValueError):
                load_surface_grid(missing)

            wrong_shape = folder_path / "wrong.npz"
            np.savez(wrong_shape, z=np.zeros((3, 3)), x=np.arange(2.0), y=np.arange(2.0))
            with self.assertRaises(ValueError):
                load_surface_grid(wrong_shape)

            not_2d = folder_path / "not2d.npz"
            np.savez(not_2d, z=np.zeros(4), x=np.arange(2.0), y=np.arange(2.0))
            with self.assertRaises(ValueError):
                load_surface_grid(not_2d)

    def test_text_points(self):
        x = np.linspace(-1.0, 1.0, 6)
        y = np.linspace(-1.0, 1.0, 5)
        z = parabolic_surface(x, y, e0=0.0, a_x=0.5, a_y=0.5)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "band.txt"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# kx ky E\n")
                for row, kx in enumerate(x):
                    for column, ky in enumerate(y):
                        handle.write(f"{kx:.6f} {ky:.6f} {z[row, column]:.6f}\n")
            grid = load_surface_grid(path)
        self.assertGreaterEqual(grid.shape[0], 5)
        # 重新采样回原始网格后应与真值一致（线性插值的节点值精确）
        restored = resample_surface(grid.z, grid.x, grid.y, x, y)
        np.testing.assert_allclose(restored, z, atol=1e-6)

    def test_missing_file_and_empty_text(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                load_surface_grid(Path(folder) / "nope.npz")
            empty = Path(folder) / "empty.txt"
            empty.write_text("# 只有注释\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_surface_grid(empty)


class SurfaceGridTests(unittest.TestCase):
    def test_describe(self):
        grid = SurfaceGrid(z=np.zeros((4, 3)), x=np.arange(4.0), y=np.arange(3.0), source="a.npz")
        self.assertEqual(grid.shape, (4, 3))
        self.assertIn("4×3", grid.describe())
        self.assertIn("a.npz", grid.describe())


if __name__ == "__main__":
    unittest.main()
