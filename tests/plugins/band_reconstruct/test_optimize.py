# -*- coding: utf-8 -*-
"""逐带重构：收敛、上下界、取消与逐带顺序。"""
from __future__ import annotations

import unittest

import numpy as np

from bandscope.extensions.api import AnalysisCancelled, CancelToken
from plugins.band_reconstruct.init_surface import parabolic_surface
from plugins.band_reconstruct.metrics import eta_avg
from plugins.band_reconstruct.mrf_loss import BandProblem
from plugins.band_reconstruct.optimize import reconstruct_band, reconstruct_bands
from plugins.band_reconstruct.synthetic import SyntheticBand, gaussian_edc_volume


def small_problem(*, amplitude: float = 1.0):
    """24×24×80 的小问题：单条抛物带，真值面与初始化面故意不一致。"""
    x = np.linspace(-1.0, 1.0, 24)
    y = np.linspace(-1.0, 1.0, 24)
    e = (-0.4, 0.018, 80)
    truth = parabolic_surface(x, y, e0=-0.05, a_x=0.12, a_y=0.12)
    initial = parabolic_surface(x, y, e0=-0.09, a_x=0.11, a_y=0.11)
    dataset = gaussian_edc_volume(
        x,
        y,
        e,
        [SyntheticBand("Band 1", truth, amplitude)],
        sigma_e=0.03,
        background=0.01,
        noise="poisson",
        noise_level=1e4,
        seed=4,
    )
    problem = BandProblem(dataset.volume, dataset.e, eta=0.1)
    return problem, dataset, truth, initial


class CountingToken:
    """在第 ``limit`` 次检查之后开始取消；用于验证检查点确实被调用。"""

    def __init__(self, limit: int):
        self.limit = int(limit)
        self.calls = 0

    def raise_if_cancelled(self) -> None:
        self.calls += 1
        if self.calls > self.limit:
            raise AnalysisCancelled("分析任务已取消。")


class ReconstructBandTests(unittest.TestCase):
    def test_reconstruction_improves_on_initial_guess(self):
        problem, dataset, truth, initial = small_problem()
        result = reconstruct_band(problem, initial, maxiter=60)
        self.assertLess(eta_avg(result.surface, truth), eta_avg(initial, truth))
        self.assertLess(eta_avg(result.surface, truth), 0.03)
        self.assertGreater(len(result.loss_history), 0)
        self.assertGreater(result.n_eval, 0)
        self.assertGreaterEqual(result.elapsed, 0.0)
        self.assertEqual(result.surface.shape, initial.shape)
        self.assertAlmostEqual(result.loss, problem.energy_terms(result.surface).total)

    def test_result_metadata(self):
        problem, _, _, initial = small_problem()
        result = reconstruct_band(problem, initial, index=2, label="Band 3", maxiter=5)
        self.assertEqual(result.index, 2)
        self.assertEqual(result.label, "Band 3")
        # 能量轴为 (-0.4, 0.018, 80)：末点 = -0.4 + 0.018×79。
        self.assertEqual(result.bounds[0], -0.4)
        self.assertAlmostEqual(result.bounds[1], -0.4 + 0.018 * 79)
        payload = result.as_dict()
        self.assertEqual(payload["label"], "Band 3")
        self.assertNotIn("surface", payload)

    def test_bounds_are_respected(self):
        problem, _, _, initial = small_problem()
        result = reconstruct_band(problem, initial, bounds=(-0.05, 0.05), maxiter=20)
        self.assertGreaterEqual(float(result.surface.min()), -0.05 - 1e-9)
        self.assertLessEqual(float(result.surface.max()), 0.05 + 1e-9)

    def test_initial_surface_is_clipped_into_bounds(self):
        problem, _, _, initial = small_problem()
        far = initial + 5.0
        result = reconstruct_band(problem, far, bounds=(-0.1, 0.1), maxiter=5)
        self.assertLessEqual(float(result.initial.max()), 0.1 + 1e-9)

    def test_on_iteration_reports_progress(self):
        problem, _, _, initial = small_problem()
        seen = []
        reconstruct_band(
            problem, initial, maxiter=10, on_iteration=lambda i, loss: seen.append((i, loss))
        )
        self.assertGreater(len(seen), 0)
        self.assertEqual([item[0] for item in seen], list(range(1, len(seen) + 1)))
        self.assertTrue(all(np.isfinite(loss) for _, loss in seen))

    def test_cancel_token_stops_optimisation(self):
        problem, _, _, initial = small_problem()
        token = CancelToken()
        token.cancel()
        with self.assertRaises(AnalysisCancelled):
            reconstruct_band(problem, initial, maxiter=50, cancel=token)

    def test_cancel_from_iteration_callback(self):
        problem, _, _, initial = small_problem()
        token = CountingToken(limit=3)
        with self.assertRaises(AnalysisCancelled):
            reconstruct_band(problem, initial, maxiter=200, cancel=token)
        self.assertGreater(token.calls, 3)

    def test_cancel_object_must_expose_protocol(self):
        problem, _, _, initial = small_problem()
        with self.assertRaises(TypeError):
            reconstruct_band(problem, initial, maxiter=1, cancel=object())

    def test_invalid_inputs(self):
        problem, _, _, initial = small_problem()
        with self.assertRaises(ValueError):
            reconstruct_band(problem, np.full(initial.shape, np.nan), maxiter=1)
        # 上下界反了不猜方向，直接报错（面板负责把输入整理成正序）。
        with self.assertRaises(ValueError):
            reconstruct_band(problem, initial, bounds=(0.3, -0.3), maxiter=1)
        with self.assertRaises(ValueError):
            reconstruct_band(problem, initial, bounds=(0.1, 0.1), maxiter=1)
        with self.assertRaises(ValueError):
            reconstruct_band(problem, initial, bounds=(0.0, np.inf), maxiter=1)


class ReconstructBandsTests(unittest.TestCase):
    def test_sequential_order_and_labels(self):
        x = np.linspace(-1.0, 1.0, 20)
        y = np.linspace(-1.0, 1.0, 20)
        e = (-0.4, 0.018, 80)
        upper = parabolic_surface(x, y, e0=0.12, a_x=0.1, a_y=0.1)
        lower = parabolic_surface(x, y, e0=-0.12, a_x=0.1, a_y=0.1)
        dataset = gaussian_edc_volume(
            x,
            y,
            e,
            [SyntheticBand("Upper", upper), SyntheticBand("Lower", lower)],
            sigma_e=0.03,
            background=0.01,
            noise="none",
            seed=1,
        )
        starts = [upper + 0.01, lower - 0.01]
        started = []
        results = reconstruct_bands(
            dataset.volume,
            dataset.e,
            starts,
            eta=0.1,
            maxiter=30,
            labels=["Upper", "Lower"],
            on_band_start=lambda index, label: started.append((index, label)),
        )
        self.assertEqual([item.index for item in results], [0, 1])
        self.assertEqual([item.label for item in results], ["Upper", "Lower"])
        self.assertEqual(started, [(0, "Upper"), (1, "Lower")])
        for result, truth in zip(results, (upper, lower)):
            self.assertLess(eta_avg(result.surface, truth), 0.03)

    def test_default_labels_and_validation(self):
        x = np.linspace(-1.0, 1.0, 16)
        y = np.linspace(-1.0, 1.0, 16)
        e = (-0.3, 0.018, 60)
        truth = parabolic_surface(x, y, e0=0.0, a_x=0.1, a_y=0.1)
        dataset = gaussian_edc_volume(
            x, y, e, [SyntheticBand("Band 1", truth)], sigma_e=0.03, background=0.01, noise="none"
        )
        results = reconstruct_bands(dataset.volume, dataset.e, [truth], maxiter=5)
        self.assertEqual(results[0].label, "Band 1")

        with self.assertRaises(ValueError):
            reconstruct_bands(dataset.volume, dataset.e, [], maxiter=5)
        with self.assertRaises(ValueError):
            reconstruct_bands(dataset.volume, dataset.e, [np.zeros((3, 3))], maxiter=5)

    def test_on_iteration_receives_band_index(self):
        x = np.linspace(-1.0, 1.0, 16)
        y = np.linspace(-1.0, 1.0, 16)
        e = (-0.3, 0.018, 60)
        truth = parabolic_surface(x, y, e0=0.0, a_x=0.1, a_y=0.1)
        dataset = gaussian_edc_volume(
            x, y, e, [SyntheticBand("Band 1", truth)], sigma_e=0.03, background=0.01, noise="none"
        )
        seen = []
        reconstruct_bands(
            dataset.volume,
            dataset.e,
            [truth, truth + 0.02],
            maxiter=8,
            on_iteration=lambda band, iteration, loss: seen.append(band),
        )
        self.assertEqual(set(seen), {0, 1})


if __name__ == "__main__":
    unittest.main()
