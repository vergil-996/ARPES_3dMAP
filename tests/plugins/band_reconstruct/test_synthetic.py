# -*- coding: utf-8 -*-
"""合成数据生成：峰值位置、噪声可复现性与参数校验。"""
from __future__ import annotations

import unittest

import numpy as np

from plugins.band_reconstruct.init_surface import parabolic_surface, plane_surface
from plugins.band_reconstruct.synthetic import (
    DEFAULT_ENERGY_STEP,
    SyntheticBand,
    gaussian_edc_volume,
)


def axes(shape=(16, 12, 60)):
    x = np.linspace(-1.0, 1.0, shape[0])
    y = np.linspace(-1.0, 1.0, shape[1])
    e = (-0.5, DEFAULT_ENERGY_STEP, shape[2])
    return x, y, e


class VolumeShapeTests(unittest.TestCase):
    def test_shape_dtype_and_truth(self):
        x, y, e = axes()
        surface = parabolic_surface(x, y, e0=0.1, a_x=0.3, a_y=0.3)
        dataset = gaussian_edc_volume(
            x, y, e, [SyntheticBand("Band 1", surface)], sigma_e=0.03, noise="none"
        )
        self.assertEqual(dataset.shape, (16, 12, 60))
        self.assertEqual(dataset.volume.dtype, np.float32)
        np.testing.assert_allclose(dataset.truth(0), surface)
        self.assertEqual(dataset.truth_stack().shape, (1, 16, 12))
        self.assertIn("Band 1", dataset.describe())
        self.assertAlmostEqual(dataset.params["energy_step"], DEFAULT_ENERGY_STEP)

    def test_peak_follows_dispersion(self):
        x, y, e = axes()
        surface = plane_surface(x, y, e0=0.1, slope_x=0.2, slope_y=0.0)
        dataset = gaussian_edc_volume(
            x, y, e, [SyntheticBand("Band 1", surface)], sigma_e=0.03, noise="none"
        )
        axis = np.asarray(dataset.e)
        for row in (2, 8, 14):
            profile = dataset.clean[row, 6, :]
            self.assertAlmostEqual(float(axis[int(np.argmax(profile))]), float(surface[row, 6]), delta=0.02)

    def test_amplitude_controls_peak_height(self):
        x, y, e = axes()
        strong = np.full((16, 12), 0.0)
        weak = np.full((16, 12), 0.3)
        dataset = gaussian_edc_volume(
            x,
            y,
            e,
            [SyntheticBand("S", strong, 1.0), SyntheticBand("W", weak, 0.1)],
            sigma_e=0.02,
            background=0.0,
            noise="none",
        )
        axis = np.asarray(dataset.e)
        strong_index = int(np.argmin(abs(axis - 0.0)))
        weak_index = int(np.argmin(abs(axis - 0.3)))
        self.assertAlmostEqual(float(dataset.clean[5, 5, strong_index]), 1.0, delta=0.02)
        self.assertAlmostEqual(float(dataset.clean[5, 5, weak_index]), 0.1, delta=0.02)

    def test_background_slope(self):
        x, y, e = axes()
        flat = np.zeros((16, 12))
        dataset = gaussian_edc_volume(
            x,
            y,
            e,
            [SyntheticBand("B", flat, 0.0)],
            sigma_e=0.02,
            background=0.01,
            background_slope=0.02,
            noise="none",
        )
        profile = dataset.clean[3, 3, :]
        self.assertGreater(float(profile[-1]), float(profile[0]))
        self.assertAlmostEqual(float(profile[0]), 0.01, delta=1e-6)


class NoiseTests(unittest.TestCase):
    def test_none_matches_clean(self):
        x, y, e = axes()
        surface = parabolic_surface(x, y, e0=0.0, a_x=0.2, a_y=0.2)
        dataset = gaussian_edc_volume(
            x, y, e, [SyntheticBand("B", surface)], sigma_e=0.03, noise="none"
        )
        np.testing.assert_array_equal(dataset.volume, dataset.clean)
        self.assertEqual(dataset.noise, "none")

    def test_poisson_is_reproducible_and_noisy(self):
        x, y, e = axes()
        surface = parabolic_surface(x, y, e0=0.0, a_x=0.2, a_y=0.2)
        band = SyntheticBand("B", surface)
        first = gaussian_edc_volume(
            x, y, e, [band], sigma_e=0.03, noise="poisson", noise_level=100.0, seed=3
        )
        second = gaussian_edc_volume(
            x, y, e, [band], sigma_e=0.03, noise="poisson", noise_level=100.0, seed=3
        )
        other = gaussian_edc_volume(
            x, y, e, [band], sigma_e=0.03, noise="poisson", noise_level=100.0, seed=4
        )
        np.testing.assert_array_equal(first.volume, second.volume)
        self.assertFalse(np.array_equal(first.volume, other.volume))
        self.assertGreater(float(np.std(first.volume - first.clean)), 0.0)

    def test_gaussian_noise_scaled_by_peak(self):
        x, y, e = axes()
        surface = parabolic_surface(x, y, e0=0.0, a_x=0.2, a_y=0.2)
        band = SyntheticBand("B", surface)
        low = gaussian_edc_volume(
            x, y, e, [band], sigma_e=0.03, noise="gaussian", noise_level=0.001, seed=1
        )
        high = gaussian_edc_volume(
            x, y, e, [band], sigma_e=0.03, noise="gaussian", noise_level=0.05, seed=1
        )
        low_sigma = float(np.std(low.volume - low.clean))
        high_sigma = float(np.std(high.volume - high.clean))
        # 噪声强度按满强度的比例缩放；负值被截到 0（强度不能为负），所以实测
        # 标准差会略小于设定的 σ，这里只比较量级关系。
        self.assertLess(low_sigma, high_sigma / 5.0)
        self.assertGreater(high_sigma, 0.02)
        self.assertLessEqual(high_sigma, 0.05)


class ValidationTests(unittest.TestCase):
    def test_shape_mismatch(self):
        x, y, e = axes()
        with self.assertRaises(ValueError):
            gaussian_edc_volume(x, y, e, [SyntheticBand("B", np.zeros((3, 3)))], noise="none")

    def test_unknown_noise_and_empty_bands(self):
        x, y, e = axes()
        surface = parabolic_surface(x, y, e0=0.0, a_x=0.2, a_y=0.2)
        with self.assertRaises(ValueError):
            gaussian_edc_volume(x, y, e, [SyntheticBand("B", surface)], noise="salt")
        with self.assertRaises(ValueError):
            gaussian_edc_volume(x, y, e, [], noise="none")

    def test_bad_energy_axis(self):
        x, y = np.linspace(-1.0, 1.0, 8), np.linspace(-1.0, 1.0, 8)
        surface = np.zeros((8, 8))
        with self.assertRaises(ValueError):
            gaussian_edc_volume(x, y, (0.0, 0.0, 10), [SyntheticBand("B", surface)], noise="none")
        with self.assertRaises(ValueError):
            gaussian_edc_volume(x, y, np.array([0.0, 0.0, 0.1]), [SyntheticBand("B", surface)], noise="none")

    def test_bad_sigma(self):
        x, y, e = axes()
        surface = np.zeros((16, 12))
        with self.assertRaises(ValueError):
            gaussian_edc_volume(x, y, e, [SyntheticBand("B", surface)], sigma_e=0.0, noise="none")


if __name__ == "__main__":
    unittest.main()
