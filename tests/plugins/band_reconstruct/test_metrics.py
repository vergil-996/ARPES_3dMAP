# -*- coding: utf-8 -*-
"""验收指标：η_avg / η_rel 与 NaN 语义。"""
from __future__ import annotations

import unittest

import numpy as np

from plugins.band_reconstruct.metrics import (
    compare_surfaces,
    eta_avg,
    eta_rel,
)


class MetricsTests(unittest.TestCase):
    def test_known_values(self):
        truth = np.zeros((2, 2))
        recon = np.full((2, 2), 0.05)
        self.assertAlmostEqual(eta_avg(recon, truth), 0.05)
        self.assertTrue(np.isnan(eta_rel(recon, truth)))  # 真值范数为 0 → 相对误差无定义

        truth = np.full((2, 2), 0.5)
        self.assertAlmostEqual(eta_avg(recon, truth), 0.45)
        self.assertAlmostEqual(eta_rel(recon, truth), 0.9)

    def test_nan_points_are_dropped_not_zeroed(self):
        truth = np.array([[0.0, 0.0], [0.0, 0.0]])
        recon = np.array([[0.1, np.nan], [0.1, 0.1]])
        self.assertAlmostEqual(eta_avg(recon, truth), 0.1)
        report = compare_surfaces(recon, truth)
        self.assertEqual(report.valid_points, 3)
        self.assertEqual(report.total_points, 4)
        self.assertAlmostEqual(report.coverage, 0.75)

    def test_all_nan_returns_nan(self):
        recon = np.full((2, 2), np.nan)
        truth = np.zeros((2, 2))
        self.assertTrue(np.isnan(eta_avg(recon, truth)))
        self.assertTrue(np.isnan(eta_rel(recon, truth)))
        self.assertEqual(compare_surfaces(recon, truth).valid_points, 0)

    def test_mask_restricts_statistics(self):
        truth = np.zeros((2, 2))
        recon = np.array([[9.0, 0.0], [0.0, 0.0]])
        mask = np.array([[False, True], [True, True]])
        self.assertAlmostEqual(eta_avg(recon, truth, mask=mask), 0.0)

    def test_shape_mismatch_raises(self):
        with self.assertRaises(ValueError):
            eta_avg(np.zeros((2, 3)), np.zeros((3, 2)))
        with self.assertRaises(ValueError):
            eta_avg(np.zeros((2, 2)), np.zeros((2, 2)), mask=np.zeros((3, 3)))

    def test_report_dict_scales_units(self):
        report = compare_surfaces(np.full((2, 2), 0.005), np.zeros((2, 2)))
        payload = report.to_dict(unit_scale=1000.0, unit="meV")
        self.assertAlmostEqual(payload["eta_avg"], 5.0)
        self.assertEqual(payload["unit"], "meV")
        self.assertEqual(payload["valid_points"], 4)


if __name__ == "__main__":
    unittest.main()
