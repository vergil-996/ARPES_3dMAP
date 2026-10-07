# -*- coding: utf-8 -*-
"""能带重构插件：工作函数与面板行为（替身宿主，不依赖主窗口）。"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.extensions.api import (
    AnalysisCancelled,
    AnalysisInput3D,
    AnalysisSurface2D,
    AnalysisUnavailable,
    CancelToken,
    read_only_array,
)
from plugins.band_reconstruct import init_surface as IS
from plugins.band_reconstruct.entry import BandReconstructPlugin
from plugins.band_reconstruct.metrics import eta_avg
from plugins.band_reconstruct.panel import BandReconstructPanel
from plugins.band_reconstruct.synthetic import SyntheticBand, gaussian_edc_volume
from plugins.band_reconstruct.worker import (
    DEFAULT_BAND_COLORS,
    band_defaults,
    build_initial_surface,
    run_reconstruction,
)

SHAPE = (20, 20, 80)
ENERGY = (-0.4, 0.018, 80)
X = np.linspace(-1.0, 1.0, SHAPE[0])
Y = np.linspace(-1.0, 1.0, SHAPE[1])
E = ENERGY[0] + ENERGY[1] * np.arange(SHAPE[2])
TRUTH = IS.parabolic_surface(X, Y, e0=0.0, a_x=0.12, a_y=0.12)


def make_snapshot(**overrides) -> AnalysisInput3D:
    dataset = gaussian_edc_volume(
        X,
        Y,
        E,
        [SyntheticBand("Band 1", TRUTH, 1.0)],
        sigma_e=0.03,
        background=0.01,
        noise="poisson",
        noise_level=5e3,
        seed=8,
    )
    volume = dataset.volume
    payload = dict(
        plugin_id="band_reconstruct",
        page_id="page-1",
        page_title="kx-ky 体数据",
        snapshot_id="snap-1",
        data_generation=5,
        volume=read_only_array(volume, dtype=np.float32),
        x=read_only_array(X),
        y=read_only_array(Y),
        e=read_only_array(E),
        x_label="kx",
        y_label="ky",
        e_label="E",
        e_unit="eV",
        frame_label="0 fs",
        scope_label="完整数据",
    )
    payload.update(overrides)
    return AnalysisInput3D(**payload)


def one_band_params(**overrides):
    settings = band_defaults(0, E)
    settings.update({"e0": -0.02, "curvature": 0.24})
    params = {"bands": [settings], "eta": 0.1, "maxiter": 100}
    params.update(overrides)
    return params


class WorkerTests(unittest.TestCase):
    def test_reconstruction_is_close_to_truth(self):
        snapshot = make_snapshot()
        surface = run_reconstruction(snapshot, one_band_params(), CancelToken())
        self.assertIsInstance(surface, AnalysisSurface2D)
        self.assertEqual(len(surface.surfaces), 1)
        self.assertEqual(surface.surfaces[0].z.shape, SHAPE[:2])
        self.assertLess(eta_avg(surface.surfaces[0].z, TRUTH), 0.05)
        self.assertEqual(surface.surfaces[0].color, DEFAULT_BAND_COLORS[0])

    def test_result_metadata_round_trips(self):
        snapshot = make_snapshot()
        surface = run_reconstruction(snapshot, one_band_params(), CancelToken())
        self.assertEqual(surface.e_unit if hasattr(surface, "e_unit") else surface.z_unit, "eV")
        self.assertEqual(surface.params["data_generation"], 5)
        self.assertEqual(surface.params["source_page"], "page-1")
        self.assertEqual(surface.params["source_shape"], list(SHAPE))
        band = surface.params["bands"][0]
        self.assertEqual(band["label"], "Band 1")
        self.assertGreater(band["n_iter"], 0)
        self.assertIn("loss_tail_delta", band)

    def test_snapshot_is_not_modified(self):
        snapshot = make_snapshot()
        before = snapshot.volume.tobytes()
        run_reconstruction(snapshot, one_band_params(), CancelToken())
        self.assertEqual(snapshot.volume.tobytes(), before)

    def test_progress_is_reported(self):
        seen = []
        token = CancelToken(progress_sink=lambda fraction, message: seen.append((fraction, message)))
        run_reconstruction(make_snapshot(), one_band_params(maxiter=30), token)
        self.assertGreater(len(seen), 3)
        self.assertAlmostEqual(seen[-1][0], 1.0)
        fractions = [fraction for fraction, _message in seen]
        self.assertEqual(fractions, sorted(fractions))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in fractions))

    def test_cancel_stops_before_work(self):
        token = CancelToken()
        token.cancel()
        with self.assertRaises(AnalysisCancelled):
            run_reconstruction(make_snapshot(), one_band_params(), token)

    def test_energy_window_restricts_the_result(self):
        surface = run_reconstruction(
            make_snapshot(), one_band_params(energy_window=[-0.2, 0.2]), CancelToken()
        )
        self.assertGreaterEqual(float(surface.surfaces[0].z.min()), -0.2 - 1e-9)
        self.assertLessEqual(float(surface.surfaces[0].z.max()), 0.2 + 1e-9)

    def test_invalid_parameters(self):
        with self.assertRaises(ValueError):
            run_reconstruction(make_snapshot(), {"bands": []}, CancelToken())
        with self.assertRaises(ValueError):
            run_reconstruction(make_snapshot(), one_band_params(eta=0.0), CancelToken())
        with self.assertRaises(ValueError):
            run_reconstruction(make_snapshot(), one_band_params(energy_window=[0.0, 0.01]), CancelToken())

    def test_two_bands_produce_two_surfaces_with_distinct_colors(self):
        upper = 0.15 + 0.05 * (X[:, None] ** 2 + Y[None, :] ** 2)
        lower = -0.15 + 0.05 * (X[:, None] ** 2 + Y[None, :] ** 2)
        dataset = gaussian_edc_volume(
            X,
            Y,
            E,
            [SyntheticBand("Upper", upper, 1.0), SyntheticBand("Lower", lower, 1.0)],
            sigma_e=0.03,
            background=0.01,
            noise="poisson",
            noise_level=5e3,
            seed=2,
        )
        snapshot = make_snapshot(volume=read_only_array(dataset.volume, dtype=np.float32))
        first = band_defaults(0, E)
        first.update({"e0": 0.14, "curvature": 0.1})
        second = band_defaults(1, E)
        second.update({"e0": -0.14, "curvature": 0.1})
        surface = run_reconstruction(
            snapshot, {"bands": [first, second], "eta": 0.1, "maxiter": 80}, CancelToken()
        )
        self.assertEqual([band.label for band in surface.surfaces], ["Band 1", "Band 2"])
        self.assertNotEqual(surface.surfaces[0].color, surface.surfaces[1].color)
        self.assertLess(eta_avg(surface.surfaces[0].z, upper), 0.06)
        self.assertLess(eta_avg(surface.surfaces[1].z, lower), 0.06)


class InitialSurfaceTests(unittest.TestCase):
    def test_kinds(self):
        parabolic = build_initial_surface({"init": "parabolic", "e0": 0.1, "curvature": 0.4}, X, Y, E)
        self.assertEqual(parabolic.shape, (X.size, Y.size))
        # 网格中心不一定落在动量坐标的原点上，最小值只近似等于 e0。
        center_x = float(np.mean(X))
        expected_min = 0.1 + 0.4 * center_x**2 / (float(np.max(np.abs(X - center_x))) ** 2 * 2)
        self.assertAlmostEqual(float(parabolic.min()), expected_min, delta=0.02)
        self.assertAlmostEqual(float(parabolic.max()), 0.5, delta=0.02)

        plane = build_initial_surface({"init": "plane", "e0": 0.0, "slope_x": 0.2}, X, Y, E)
        self.assertGreater(float(plane[-1, 0]), float(plane[0, 0]))

        gaussian = build_initial_surface({"init": "gaussian", "e0": 0.0, "amplitude": 0.3}, X, Y, E)
        self.assertAlmostEqual(float(gaussian.max()), 0.3, delta=0.02)

    def test_unknown_kind_raises_and_bad_values_fall_back(self):
        with self.assertRaises(ValueError):
            build_initial_surface({"init": "spiral"}, X, Y, E)
        surface = build_initial_surface({"init": "parabolic", "e0": float("nan")}, X, Y, E)
        self.assertTrue(np.all(np.isfinite(surface)))

    def test_imported_grid(self):
        # 网格越粗，双线性重采样与原抛物面的差距越大（误差 ~ 步长²·曲率/8）；
        # 这里用 9×9 网格，误差应远小于重构的验收线。
        coarse_x = np.linspace(-1.0, 1.0, 9)
        coarse_y = np.linspace(-1.0, 1.0, 9)
        grid = IS.SurfaceGrid(
            z=IS.parabolic_surface(coarse_x, coarse_y, e0=0.0, a_x=0.5, a_y=0.5),
            x=coarse_x,
            y=coarse_y,
            label="DFT",
        )
        surface = build_initial_surface({"init": "import", "grid": grid}, X, Y, E)
        self.assertEqual(surface.shape, (X.size, Y.size))
        # 双线性重采样在粗网格上不会逐点复现抛物面，只要求整体贴近。
        reference = IS.parabolic_surface(X, Y, e0=0.0, a_x=0.5, a_y=0.5)
        self.assertLess(float(np.max(np.abs(surface - reference))), 0.03)


class FakeHost:
    """替身宿主：只实现面板用到的 v3 接口。"""

    def __init__(self, snapshot=None, *, unavailable: str = ""):
        self.snapshot = snapshot
        self.unavailable = unavailable
        self.submitted = []
        self.cancelled = []

    def capture_analysis_input_3d(self):
        if self.unavailable:
            raise AnalysisUnavailable(self.unavailable)
        if self.snapshot is None:
            raise AnalysisUnavailable("没有数据")
        return self.snapshot

    def submit_analysis(self, snapshot, work, *, title="", params=None):
        self.submitted.append((snapshot, work, title, params))
        return f"handle-{len(self.submitted)}"

    def cancel_analysis(self, handle):
        self.cancelled.append(handle)


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_submits_current_settings(self):
        host = FakeHost(make_snapshot())
        panel = BandReconstructPanel(host)
        self.addCleanup(panel.deleteLater)
        panel.spin_bands.setValue(2)
        panel.box_eta.setValue(0.2)
        panel.spin_maxiter.setValue(150)
        panel.check_clahe.setChecked(False)

        panel.on_run()
        self.assertEqual(len(host.submitted), 1)
        _snapshot, work, title, params = host.submitted[0]
        self.assertIs(work, run_reconstruction)
        self.assertIn("能带重构", title)
        self.assertEqual(len(params["bands"]), 2)
        self.assertAlmostEqual(params["eta"], 0.2)
        self.assertEqual(params["maxiter"], 150)
        self.assertFalse(params["clahe"])
        self.assertTrue(params["smooth"])
        self.assertEqual([band["label"] for band in params["bands"]], ["Band 1", "Band 2"])
        self.assertIn("已提交", panel.status.text())
        self.assertFalse(panel.btn_run.isEnabled())

    def test_unavailable_snapshot_is_explained(self):
        host = FakeHost(unavailable="当前结果正在计算中，请等计算完成后重试。")
        panel = BandReconstructPanel(host)
        self.addCleanup(panel.deleteLater)
        panel.on_run()
        self.assertEqual(host.submitted, [])
        self.assertIn("计算中", panel.status.text())

    def test_run_busy_keeps_panel_usable(self):
        host = FakeHost(make_snapshot())
        host.submit_analysis = lambda *args, **kwargs: None
        panel = BandReconstructPanel(host)
        self.addCleanup(panel.deleteLater)
        panel.on_run()
        self.assertIn("稍后再试", panel.status.text())
        self.assertTrue(panel.btn_run.isEnabled())

    def test_progress_and_finish_update_the_panel(self):
        panel = BandReconstructPanel(FakeHost(make_snapshot()))
        self.addCleanup(panel.deleteLater)
        panel.on_run()
        panel.on_progress(0.42, "Band 1：第 12 次迭代")
        self.assertEqual(panel.progress.value(), 420)
        self.assertIn("第 12 次迭代", panel.status.text())
        panel.on_progress(1.0, "重构完成")
        self.assertEqual(panel.progress.value(), 1000)

        panel.on_task_finished("重构完成：结果页已加入左侧结果树。")
        self.assertTrue(panel.btn_run.isEnabled())
        self.assertEqual(panel.progress.value(), 0)

    def test_settings_snapshot_round_trip(self):
        panel = BandReconstructPanel(FakeHost(make_snapshot()))
        self.addCleanup(panel.deleteLater)
        panel.spin_bands.setValue(3)
        panel._band_rows[1].combo_init.setCurrentIndex(
            panel._band_rows[1].combo_init.findData("gaussian")
        )
        panel._band_rows[1].box_shape.setValue(0.25)
        panel.box_eta.setValue(0.3)
        state = panel.settings_snapshot()

        other = BandReconstructPanel(FakeHost(make_snapshot()))
        self.addCleanup(other.deleteLater)
        other.apply_settings(state)
        self.assertEqual(other.spin_bands.value(), 3)
        self.assertAlmostEqual(other.box_eta.value(), 0.3)
        restored = other.settings_snapshot()
        self.assertEqual(restored["bands"][1]["init"], "gaussian")
        self.assertAlmostEqual(restored["bands"][1]["amplitude"], 0.25)

    def test_shape_parameter_follows_init_kind(self):
        panel = BandReconstructPanel(FakeHost(make_snapshot()))
        self.addCleanup(panel.deleteLater)
        row = panel._band_rows[0]
        row.combo_init.setCurrentIndex(row.combo_init.findData("plane"))
        row.box_shape.setValue(0.33)
        settings = panel.band_settings()[0]
        self.assertEqual(settings["init"], "plane")
        self.assertAlmostEqual(settings["slope_x"], 0.33)
        self.assertAlmostEqual(settings["slope_y"], 0.0)

    def test_cancel_forwards_the_handle(self):
        host = FakeHost(make_snapshot())
        panel = BandReconstructPanel(host)
        self.addCleanup(panel.deleteLater)
        panel.on_run()
        panel.on_cancel()
        self.assertEqual(host.cancelled, ["handle-1"])
        self.assertIn("取消", panel.status.text())


class EntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_panel_lifecycle_and_callbacks(self):
        plugin = BandReconstructPlugin()
        host = FakeHost(make_snapshot())
        panel = plugin.create_panel(host)
        self.addCleanup(panel.deleteLater)
        self.assertIsInstance(panel, BandReconstructPanel)

        plugin.on_analysis_progress("handle-1", 0.5, "一半")
        self.assertEqual(panel.progress.value(), 500)
        plugin.on_analysis_finished("handle-1", "succeeded")
        self.assertTrue(panel.btn_run.isEnabled())
        plugin.on_analysis_finished("handle-1", "cancelled", "数据已更新，结果已作废。")
        self.assertIn("作废", panel.status.text())
        plugin.on_analysis_finished("handle-1", "failed", "boom")
        self.assertIn("boom", panel.status.text())

    def test_state_survives_panel_recreation(self):
        plugin = BandReconstructPlugin()
        first = plugin.create_panel(FakeHost(make_snapshot()))
        self.addCleanup(first.deleteLater)
        first.spin_bands.setValue(2)
        first.check_clahe.setChecked(False)
        state = plugin.export_state()
        self.assertEqual(len(state["bands"]), 2)

        plugin.release()
        second = plugin.create_panel(FakeHost(make_snapshot()))
        self.addCleanup(second.deleteLater)
        self.assertEqual(second.spin_bands.value(), 2)
        self.assertFalse(second.check_clahe.isChecked())


if __name__ == "__main__":
    unittest.main()
