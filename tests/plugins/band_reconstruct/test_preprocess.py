# -*- coding: utf-8 -*-
"""预处理：清理、高斯平滑、归一化与 MCLAHE。"""
from __future__ import annotations

import unittest

import numpy as np

from plugins.band_reconstruct.preprocess import (
    DEFAULT_MCLAHE_PARAMS,
    DEFAULT_SIGMA,
    gaussian_smooth,
    mclahe,
    normalize_volume,
    preprocess_volume,
    sanitize_volume,
)


def blob_volume(*, weak_amplitude: float = 0.1, shape=(24, 24, 48)) -> np.ndarray:
    """背景 + 一个强斑（E 中部）与一个弱斑（E 边缘），用于对比度测试。"""
    volume = np.full(shape, 0.02, dtype=np.float32)
    i, j, k = np.indices(shape, dtype=np.float32)
    strong = np.exp(-((i - 6) ** 2 + (j - 6) ** 2) / 8.0) * np.exp(
        -((k - 24) ** 2) / 20.0
    )
    weak = np.exp(-((i - 17) ** 2 + (j - 17) ** 2) / 8.0) * np.exp(
        -((k - 40) ** 2) / 20.0
    )
    return np.asarray(volume + strong + weak_amplitude * weak, dtype=np.float32)


class SanitizeTests(unittest.TestCase):
    def test_non_finite_becomes_zero(self):
        values = np.array([[[np.nan, np.inf, -np.inf, 1.0]]], dtype=np.float32)
        clean = sanitize_volume(values)
        np.testing.assert_array_equal(clean, np.array([[[0.0, 0.0, 0.0, 1.0]]], dtype=np.float32))
        self.assertEqual(clean.dtype, np.float32)

    def test_requires_3d(self):
        with self.assertRaises(ValueError):
            sanitize_volume(np.zeros((4, 4)))
        with self.assertRaises(ValueError):
            sanitize_volume(np.zeros((0, 4, 4), dtype=np.float32))


class GaussianTests(unittest.TestCase):
    def test_shape_and_dtype_preserved(self):
        volume = np.ones((5, 6, 7), dtype=np.float32)
        smoothed = gaussian_smooth(volume)
        self.assertEqual(smoothed.shape, volume.shape)
        self.assertEqual(smoothed.dtype, np.float32)
        np.testing.assert_allclose(smoothed, 1.0, atol=1e-5)

    def test_zero_sigma_is_copy(self):
        volume = np.arange(24, dtype=np.float32).reshape(2, 3, 4)
        smoothed = gaussian_smooth(volume, sigma=(0.0, 0.0, 0.0))
        np.testing.assert_array_equal(smoothed, volume)
        self.assertIsNot(smoothed, volume)

    def test_impulse_is_symmetrised(self):
        volume = np.zeros((5, 5, 5), dtype=np.float32)
        volume[2, 2, 2] = 1.0
        smoothed = gaussian_smooth(volume, sigma=(1.0, 1.0, 1.0))
        self.assertGreater(smoothed[2, 2, 2], smoothed[2, 2, 3])
        self.assertAlmostEqual(float(smoothed[1, 2, 2]), float(smoothed[3, 2, 2]), places=6)

    def test_sigma_length_mismatch(self):
        with self.assertRaises(ValueError):
            gaussian_smooth(np.zeros((4, 4, 4), dtype=np.float32), sigma=(1.0, 1.0))


class NormalizeTests(unittest.TestCase):
    def test_scales_to_unit_peak(self):
        values = np.array([[[0.0, 5.0], [10.0, -3.0]]], dtype=np.float32)
        normalized, scale = normalize_volume(values)
        self.assertAlmostEqual(scale, 10.0)
        self.assertAlmostEqual(float(normalized.max()), 1.0)
        self.assertAlmostEqual(float(normalized.min()), 0.0)

    def test_all_zero_is_unchanged(self):
        values = np.zeros((2, 2, 2), dtype=np.float32)
        normalized, scale = normalize_volume(values)
        self.assertEqual(scale, 1.0)
        np.testing.assert_array_equal(normalized, values)


class MclaheTests(unittest.TestCase):
    def test_shape_dtype_and_nan(self):
        volume = blob_volume()
        result = mclahe(volume)
        self.assertEqual(result.shape, volume.shape)
        self.assertEqual(result.dtype, np.float32)
        self.assertTrue(np.all(np.isfinite(result)))

        with_nan = volume.copy()
        with_nan[3, 3, 3] = np.nan
        result = mclahe(with_nan)
        self.assertTrue(np.isnan(result[3, 3, 3]))

    def test_uniform_volume_stays_flat(self):
        volume = np.full((8, 8, 16), 0.5, dtype=np.float32)
        result = mclahe(volume)
        np.testing.assert_allclose(result, 0.5, atol=1e-5)

    def test_below_threshold_is_zeroed(self):
        volume = np.full((8, 8, 16), 1e-9, dtype=np.float32)
        volume[0, 0, 0] = 1.0
        result = mclahe(volume, threshold=1e-6)
        self.assertEqual(float(result[4, 4, 4]), 0.0)

    def test_weak_feature_gains_local_contrast(self):
        """弱斑相对自身背景的对比度在均衡后应被抬高（这是 MCLAHE 的目的）。"""
        volume = blob_volume(weak_amplitude=0.05)
        result = mclahe(volume)
        weak_center = (17, 17, 40)
        weak_bg = (17, 17, 20)  # 同一块内的背景位置
        raw_gain = float(volume[weak_center]) / max(float(volume[weak_bg]), 1e-12)
        out_gain = float(result[weak_center]) / max(float(result[weak_bg]), 1e-12)
        self.assertGreater(out_gain, raw_gain)

    def test_clip_limit_controls_equalisation_strength(self):
        """``clip_limit`` 越小，直方图均衡越弱（映射越接近恒等）。

        90% 的体素取 0.5、10% 取 1.0（散开以覆盖所有分块）：

        - ``clip_limit`` 小 → 主箱被压掉，0.5 映射回 ~0.5（几乎不增强）；
        - ``clip_limit`` 大 → 0.5 按秩映射到 ~0.9（接近全局均衡）。

        最高值在两种情况下都映射到峰值（CDF 的最右端恒为 1）。
        """
        rng = np.random.default_rng(0)
        volume = np.where(rng.random((16, 16, 32)) < 0.9, 0.5, 1.0).astype(np.float32)
        gentle = mclahe(volume, clip_limit=0.01)
        strong = mclahe(volume, clip_limit=0.9)
        low = volume == 0.5
        self.assertAlmostEqual(float(gentle[low].mean()), 0.5, delta=0.05)
        self.assertGreater(float(strong[low].mean()), 0.8)
        self.assertAlmostEqual(float(gentle[volume == 1.0].mean()), 1.0, delta=1e-4)
        self.assertAlmostEqual(float(strong[volume == 1.0].mean()), 1.0, delta=1e-4)

    def test_small_volume_with_many_tiles(self):
        volume = np.random.default_rng(0).random((3, 3, 5), dtype=np.float32)
        result = mclahe(volume, tiles=8)
        self.assertEqual(result.shape, volume.shape)
        self.assertTrue(np.all(np.isfinite(result)))

    def test_chunking_does_not_change_result(self):
        volume = blob_volume()
        whole = mclahe(volume, chunk_slices=None)
        chunked = mclahe(volume, chunk_slices=1)
        np.testing.assert_allclose(whole, chunked, atol=1e-6)

    def test_requires_3d(self):
        with self.assertRaises(ValueError):
            mclahe(np.zeros((4, 4), dtype=np.float32))


class PreprocessPipelineTests(unittest.TestCase):
    def test_steps_and_stats(self):
        volume = blob_volume()
        result = preprocess_volume(volume, clahe=True)
        self.assertEqual(result.steps, ("sanitize", "gaussian", "mclahe"))
        self.assertEqual(result.shape, volume.shape)
        self.assertAlmostEqual(result.stats["input_peak"], float(volume.max()))
        self.assertAlmostEqual(result.stats["output_peak"], 1.0, places=5)
        self.assertEqual(result.stats["sigma"], tuple(float(item) for item in DEFAULT_SIGMA))
        self.assertEqual(
            result.stats["mclahe"],
            {key: DEFAULT_MCLAHE_PARAMS[key] for key in DEFAULT_MCLAHE_PARAMS},
        )

    def test_switches_disable_steps(self):
        volume = blob_volume()
        plain = preprocess_volume(volume, smooth=False, clahe=False)
        self.assertEqual(plain.steps, ("sanitize",))
        np.testing.assert_allclose(
            plain.volume, normalize_volume(sanitize_volume(volume))[0], atol=1e-6
        )

        smooth_only = preprocess_volume(volume, clahe=False)
        self.assertEqual(smooth_only.steps, ("sanitize", "gaussian"))

    def test_nan_input_is_handled_end_to_end(self):
        volume = blob_volume()
        volume[1, 1, 1] = np.nan
        result = preprocess_volume(volume)
        self.assertTrue(np.all(np.isfinite(result.volume)))


if __name__ == "__main__":
    unittest.main()
