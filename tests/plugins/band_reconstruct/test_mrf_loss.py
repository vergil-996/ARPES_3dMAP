# -*- coding: utf-8 -*-
"""MRF 能量项：三线性取样、梯度与数值稳定性。"""
from __future__ import annotations

import unittest

import numpy as np

from plugins.band_reconstruct.mrf_loss import (
    BandProblem,
    TrilinearSampler,
    default_floor,
)


def sample_volume(shape=(9, 7, 25), seed=5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.random(shape) * 0.4 + 0.05).astype(np.float64)


class TrilinearSamplerTests(unittest.TestCase):
    def setUp(self):
        self.volume = sample_volume()
        self.e = np.linspace(-1.0, 1.0, self.volume.shape[2])
        self.sampler = TrilinearSampler(self.volume, self.e)

    def test_integer_momentum_matches_linear_interpolation(self):
        i = np.array([2.0, 5.0, 8.0])
        j = np.array([1.0, 3.0, 6.0])
        fe = np.array([-0.37, 0.04, 0.81])
        values, _ = self.sampler.sample(i, j, fe)
        expected = [
            np.interp(energy, self.e, self.volume[int(a), int(b), :])
            for a, b, energy in zip(i, j, fe)
        ]
        np.testing.assert_allclose(values, expected, rtol=1e-12)

    def test_fractional_momentum_interpolates_linearly(self):
        fe = np.array([0.2])
        mid, _ = self.sampler.sample(np.array([1.5]), np.array([2.0]), fe)
        left, _ = self.sampler.sample(np.array([1.0]), np.array([2.0]), fe)
        right, _ = self.sampler.sample(np.array([2.0]), np.array([2.0]), fe)
        self.assertAlmostEqual(float(mid[0]), float((left[0] + right[0]) / 2.0))

    def test_energy_derivative_matches_finite_difference(self):
        i = np.array([1.0, 4.0, 7.0])
        j = np.array([2.0, 5.0, 3.0])
        fe = np.array([-0.6, 0.13, 0.77])
        _, slope = self.sampler.sample(i, j, fe)
        step = 1e-6
        plus, _ = self.sampler.sample(i, j, fe + step)
        minus, _ = self.sampler.sample(i, j, fe - step)
        np.testing.assert_allclose(slope, (plus - minus) / (2 * step), rtol=1e-6)

    def test_descending_axis_matches_ascending(self):
        flipped = TrilinearSampler(
            np.ascontiguousarray(self.volume[:, :, ::-1]), self.e[::-1].copy()
        )
        i = np.array([3.0, 6.0])
        j = np.array([1.0, 4.0])
        fe = np.array([-0.2, 0.55])
        values, slope = self.sampler.sample(i, j, fe)
        flipped_values, flipped_slope = flipped.sample(i, j, fe)
        np.testing.assert_allclose(values, flipped_values, rtol=1e-12)
        np.testing.assert_allclose(slope, flipped_slope, rtol=1e-12)

    def test_non_uniform_axis(self):
        energy = np.array([-1.0, -0.9, -0.5, 0.0, 0.2, 0.21, 0.6, 1.0])
        volume = sample_volume((5, 4, energy.size))
        sampler = TrilinearSampler(volume, energy)
        fe = np.array([-0.45, 0.205])
        values, _ = sampler.sample(np.array([2.0, 2.0]), np.array([1.0, 1.0]), fe)
        expected = [np.interp(energy_value, energy, volume[2, 1, :]) for energy_value in fe]
        np.testing.assert_allclose(values, expected, rtol=1e-12)

    def test_out_of_range_energy_clamps_to_edges(self):
        i = np.array([1.0])
        j = np.array([1.0])
        below, _ = self.sampler.sample(i, j, np.array([-5.0]))
        above, _ = self.sampler.sample(i, j, np.array([5.0]))
        self.assertAlmostEqual(float(below[0]), float(self.volume[1, 1, 0]))
        self.assertAlmostEqual(float(above[0]), float(self.volume[1, 1, -1]))

    def test_constructor_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            TrilinearSampler(np.zeros((4, 4)), np.arange(4))
        with self.assertRaises(ValueError):
            TrilinearSampler(np.zeros((4, 4, 4)), np.arange(5))
        with self.assertRaises(ValueError):
            TrilinearSampler(np.zeros((4, 4, 4)), np.array([0.0, 1.0, 1.0, 2.0]))
        with self.assertRaises(ValueError):
            TrilinearSampler(np.full((4, 4, 4), np.nan), np.arange(4.0))


class BandProblemTests(unittest.TestCase):
    def setUp(self):
        self.volume = sample_volume((6, 5, 20), seed=9)
        self.e = np.linspace(-0.5, 0.5, 20)
        self.problem = BandProblem(self.volume, self.e, eta=0.05)

    def test_default_floor_is_relative_to_peak(self):
        self.assertAlmostEqual(self.problem.floor, 1e-6 * float(self.volume.max()))
        self.assertAlmostEqual(default_floor(self.volume), self.problem.floor)

    def test_energy_bounds_match_axis(self):
        self.assertEqual(self.problem.energy_bounds(), (-0.5, 0.5))

    def test_invalid_parameters(self):
        with self.assertRaises(ValueError):
            BandProblem(self.volume, self.e, eta=0.0)
        with self.assertRaises(ValueError):
            BandProblem(self.volume, self.e, floor=-1.0)

    def test_gradient_matches_finite_differences(self):
        """核心正确性检查：解析梯度必须与中心差分一致。"""
        surface = (
            -0.1
            + 0.08 * np.cos(np.linspace(0.0, 2.5, self.volume.shape[0]))[:, None]
            + 0.05 * np.sin(np.linspace(0.0, 1.8, self.volume.shape[1]))[None, :]
        )
        _, gradient = self.problem.energy(surface)
        step = 1e-6
        numeric = np.zeros_like(surface)
        for row in range(surface.shape[0]):
            for column in range(surface.shape[1]):
                plus = surface.copy()
                plus[row, column] += step
                minus = surface.copy()
                minus[row, column] -= step
                numeric[row, column] = (
                    self.problem.energy_terms(plus).total
                    - self.problem.energy_terms(minus).total
                ) / (2 * step)
        np.testing.assert_allclose(gradient, numeric, atol=1e-5, rtol=1e-5)

    def test_smoothness_term_matches_manual_sum(self):
        surface = np.array([[0.0, 0.1], [0.2, 0.05]])
        problem = BandProblem(np.ones((2, 2, 8)), np.linspace(-0.1, 0.1, 8), eta=0.1)
        terms = problem.energy_terms(surface)
        expected = (
            (surface[1, :] - surface[0, :]) ** 2
            + (surface[:, 1] - surface[:, 0]) ** 2
        ).sum() / (2 * 0.1**2)
        self.assertAlmostEqual(terms.smoothness, float(expected))

    def test_gradient_is_zero_where_intensity_is_clipped(self):
        """强度低于 floor 的区域没有强度信息：梯度只应来自平滑项。"""
        volume = np.zeros((4, 4, 10))
        volume[..., 5] = 1.0  # 只有中间一层有强度
        problem = BandProblem(volume, np.linspace(-1.0, 1.0, 10), eta=1.0, floor=0.5)
        surface = np.full((4, 4), 0.0)  # 远离强度层 → I=0 → 被截断
        terms = problem.energy_terms(surface)
        # 常量面没有邻域差 → 平滑项为 0，总梯度应为 0
        np.testing.assert_allclose(terms.gradient, 0.0, atol=1e-12)
        self.assertAlmostEqual(terms.intensity, -np.log(0.5) * 16)

    def test_energy_returns_same_values_as_terms(self):
        surface = np.zeros((6, 5))
        loss, gradient = self.problem.energy(surface)
        terms = self.problem.energy_terms(surface)
        self.assertAlmostEqual(loss, terms.total)
        np.testing.assert_array_equal(gradient, terms.gradient)
        self.assertEqual(set(terms.as_dict()), {"total", "intensity", "smoothness"})


if __name__ == "__main__":
    unittest.main()
