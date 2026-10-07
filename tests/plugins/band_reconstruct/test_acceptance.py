# -*- coding: utf-8 -*-
"""阶段 1 验收：论文量级的重构精度与运行时间（合成数据，18 meV 间隔）。

验收标准（总体计划阶段 1）：

1. 单带无交叉：η_avg ≤ 50 meV；
2. 两带交叉（初始化带交叉信息正确）：η_avg ≤ 60 meV/带；
3. 强度失衡（一条带弱 10×）：经 MCLAHE 后仍能重构，记录开/关 MCLAHE 的差异；
4. 单带 64×64×200 CPU 运行时间 ≤ 2 min（实测值打印在测试输出里，并写入
   ``plugins/band_reconstruct/README.md``）。

所有数据在测试内现算，固定随机种子，不依赖本机实验文件。
"""
from __future__ import annotations

import time
import unittest

import numpy as np

from plugins.band_reconstruct.init_surface import parabolic_surface
from plugins.band_reconstruct.metrics import compare_surfaces
from plugins.band_reconstruct.optimize import reconstruct_bands
from plugins.band_reconstruct.preprocess import preprocess_volume
from plugins.band_reconstruct.synthetic import SyntheticBand, gaussian_edc_volume

#: 验收网格：64×64 动量点 × 200 个能量点，能量间隔 18 meV。
SHAPE = (64, 64, 200)
ENERGY = (-1.0, 0.018, 200)
ETA = 0.1
MAXITER = 200
RUNTIME_BUDGET_S = 120.0


def momentum_axes(shape=SHAPE):
    return np.linspace(-1.0, 1.0, shape[0]), np.linspace(-1.0, 1.0, shape[1])


def run_pipeline(dataset, initials, *, clahe=True, eta=ETA, maxiter=MAXITER):
    """预处理 + 逐带重构，返回 (结果列表, 预处理耗时, 重构耗时)。"""
    started = time.perf_counter()
    processed = preprocess_volume(dataset.volume, clahe=clahe)
    preprocess_time = time.perf_counter() - started

    started = time.perf_counter()
    results = reconstruct_bands(
        processed.volume, dataset.e, initials, eta=eta, maxiter=maxiter
    )
    reconstruct_time = time.perf_counter() - started
    return results, preprocess_time, reconstruct_time


class SingleBandAcceptanceTests(unittest.TestCase):
    """验收标准 1 + 4：单带精度与运行时间。"""

    def test_single_band_within_budget(self):
        x, y = momentum_axes()
        truth = parabolic_surface(x, y, e0=-0.25, a_x=0.6, a_y=0.6)
        initial = parabolic_surface(x, y, e0=-0.30, a_x=0.55, a_y=0.55)
        dataset = gaussian_edc_volume(
            x,
            y,
            ENERGY,
            [SyntheticBand("Band 1", truth, 1.0)],
            sigma_e=0.03,
            background=0.01,
            noise="poisson",
            noise_level=1e4,
            seed=1,
        )

        (result,), preprocess_time, reconstruct_time = run_pipeline(dataset, [initial])
        report = compare_surfaces(result.surface, truth)
        total = preprocess_time + reconstruct_time
        print(
            f"[验收 1] 单带 64×64×200：η_avg = {report.eta_avg * 1000:.1f} meV，"
            f"η_rel = {report.eta_rel:.4f}，最大偏差 = {report.max_abs_error * 1000:.1f} meV，"
            f"迭代 {result.n_iter}，预处理 {preprocess_time:.2f}s + 重构 {reconstruct_time:.2f}s"
            f" = {total:.2f}s"
        )
        self.assertLessEqual(report.eta_avg * 1000, 50.0)
        self.assertLessEqual(total, RUNTIME_BUDGET_S)
        self.assertGreater(result.n_iter, 0)

    def test_initialisation_actually_matters(self):
        """初始化面误差被修正：重构结果应显著优于初值。"""
        x, y = momentum_axes()
        truth = parabolic_surface(x, y, e0=-0.25, a_x=0.6, a_y=0.6)
        initial = parabolic_surface(x, y, e0=-0.30, a_x=0.55, a_y=0.55)
        dataset = gaussian_edc_volume(
            x,
            y,
            ENERGY,
            [SyntheticBand("Band 1", truth, 1.0)],
            sigma_e=0.03,
            background=0.01,
            noise="poisson",
            noise_level=1e4,
            seed=1,
        )
        (result,), _, _ = run_pipeline(dataset, [initial])
        before = compare_surfaces(initial, truth).eta_avg
        after = compare_surfaces(result.surface, truth).eta_avg
        self.assertLess(after, before / 3.0)


class CrossingBandsAcceptanceTests(unittest.TestCase):
    """验收标准 2：两带交叉。"""

    def test_crossing_bands_within_budget(self):
        x, y = momentum_axes()
        kx, ky = np.meshgrid(x, y, indexing="ij")
        upper = 0.6 * kx + 0.05 * (kx**2 + ky**2) + 0.1
        lower = -0.6 * kx + 0.05 * (kx**2 + ky**2) + 0.1
        # 初始化带正确的交叉信息（同一函数形式、参数略偏），两条带各偏一侧。
        start_upper = 0.55 * kx + 0.048 * (kx**2 + ky**2) + 0.11
        start_lower = -0.55 * kx + 0.048 * (kx**2 + ky**2) + 0.09
        dataset = gaussian_edc_volume(
            x,
            y,
            ENERGY,
            [SyntheticBand("Upper", upper, 1.0), SyntheticBand("Lower", lower, 1.0)],
            sigma_e=0.03,
            background=0.01,
            noise="poisson",
            noise_level=1e4,
            seed=2,
        )
        results, preprocess_time, reconstruct_time = run_pipeline(
            dataset, [start_upper, start_lower]
        )
        reports = [
            compare_surfaces(result.surface, truth)
            for result, truth in zip(results, (upper, lower))
        ]
        for name, report in zip(("上支", "下支"), reports):
            print(
                f"[验收 2] 交叉两带 {name}：η_avg = {report.eta_avg * 1000:.1f} meV，"
                f"η_rel = {report.eta_rel:.4f}"
            )
        print(
            f"[验收 2] 交叉两带总耗时：预处理 {preprocess_time:.2f}s + 重构 {reconstruct_time:.2f}s"
        )
        for report in reports:
            self.assertLessEqual(report.eta_avg * 1000, 60.0)


class ImbalancedBandsAcceptanceTests(unittest.TestCase):
    """验收标准 3：强度失衡 10×，MCLAHE 开/关对比。"""

    def _dataset(self):
        x, y = momentum_axes()
        kx, ky = np.meshgrid(x, y, indexing="ij")
        strong = 0.20 + 0.06 * (kx**2 + ky**2)
        weak = -0.20 + 0.06 * (kx**2 + ky**2)
        dataset = gaussian_edc_volume(
            x,
            y,
            ENERGY,
            [SyntheticBand("Strong", strong, 1.0), SyntheticBand("Weak", weak, 0.1)],
            sigma_e=0.03,
            background=0.01,
            background_slope=0.01,
            noise="poisson",
            noise_level=5e3,
            seed=3,
        )
        starts = [strong * 0.97 + 0.01, weak * 0.97 - 0.01]
        return dataset, (strong, weak), starts

    def test_weak_band_reconstructs_with_mclahe(self):
        dataset, truths, starts = self._dataset()
        measured = {}
        for clahe in (True, False):
            results, _, _ = run_pipeline(dataset, starts, clahe=clahe)
            measured[clahe] = [
                compare_surfaces(result.surface, truth).eta_avg * 1000
                for result, truth in zip(results, truths)
            ]
        print(
            "[验收 3] 弱带（幅度 0.1）η_avg：MCLAHE 开 = %.1f meV，关 = %.1f meV；"
            "强带：开 = %.1f meV，关 = %.1f meV"
            % (measured[True][1], measured[False][1], measured[True][0], measured[False][0])
        )
        self.assertLessEqual(measured[True][1], 60.0)
        self.assertLessEqual(measured[True][0], 60.0)

    def test_mclahe_does_not_degrade_strong_band(self):
        dataset, truths, starts = self._dataset()
        on, _, _ = run_pipeline(dataset, starts, clahe=True)
        off, _, _ = run_pipeline(dataset, starts, clahe=False)
        strong_on = compare_surfaces(on[0].surface, truths[0]).eta_avg
        strong_off = compare_surfaces(off[0].surface, truths[0]).eta_avg
        # 对比度均衡只应轻微影响精度（量级 1 meV），不允许出现明显退化。
        self.assertLess(strong_on, strong_off + 0.005)


if __name__ == "__main__":
    unittest.main()
