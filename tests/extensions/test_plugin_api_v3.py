# -*- coding: utf-8 -*-
"""API 3：三维快照与面结果契约（版本、能力组合、校验与进度回传）。"""
from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.extensions.analysis_host import AnalysisTaskRunner
from bandscope.extensions.api import (
    ANALYSIS_3D_CAPABILITIES,
    ANALYSIS_CAPABILITIES,
    API_VERSION,
    CAPABILITY_DATA_SNAPSHOT_3D,
    CAPABILITY_RENDER_OVERLAY_SURFACE,
    CAPABILITY_RESULT_SURFACE_2D,
    MAX_SURFACE_BANDS,
    SUPPORTED_API_VERSIONS,
    SUPPORTED_CAPABILITIES,
    AnalysisInput3D,
    AnalysisResultError,
    AnalysisSurface2D,
    BandSurface,
    CancelToken,
    evaluate_compatibility,
    read_only_array,
    validate_analysis_surface,
)


def make_input_3d(*, shape=(4, 5, 6)) -> AnalysisInput3D:
    volume = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
    return AnalysisInput3D(
        plugin_id="demo3d",
        page_id="p1",
        page_title="体数据",
        snapshot_id="s1",
        data_generation=7,
        volume=read_only_array(volume, dtype=np.float32),
        x=read_only_array(np.linspace(-1, 1, shape[0])),
        y=read_only_array(np.linspace(-1, 1, shape[1])),
        e=read_only_array(np.linspace(-0.5, 0.5, shape[2])),
        x_label="kx",
        x_unit="1/Å",
        e_label="E",
        e_unit="eV",
        frame_label="0 fs",
        scope_label="ROI #3",
        title="原始视图",
    )


class VersionTests(unittest.TestCase):
    def test_api_version_and_capability_sets(self):
        self.assertEqual(API_VERSION, 3)
        self.assertEqual(SUPPORTED_API_VERSIONS, frozenset({1, 2, 3}))
        self.assertEqual(
            set(ANALYSIS_3D_CAPABILITIES),
            {CAPABILITY_DATA_SNAPSHOT_3D, "analysis_task", CAPABILITY_RESULT_SURFACE_2D},
        )
        for capability in (*ANALYSIS_3D_CAPABILITIES, CAPABILITY_RENDER_OVERLAY_SURFACE):
            self.assertIn(capability, SUPPORTED_CAPABILITIES)
        # 旧的二维分析能力不受影响。
        for capability in ANALYSIS_CAPABILITIES:
            self.assertIn(capability, SUPPORTED_CAPABILITIES)

    def test_old_plugins_still_evaluate_as_before(self):
        verdict = evaluate_compatibility(
            name="老插件",
            requires_app=">=1.9.0,<2.0.0",
            api_version=2,
            capabilities=["opacity_multiplier"],
            app_version="1.12.3",
        )
        self.assertTrue(verdict.ok, verdict.reason)

    def test_api_3_plugin_is_rejected_by_an_old_host(self):
        verdict = evaluate_compatibility(
            name="能带重构",
            requires_app=">=1.12.3,<2.0.0",
            api_version=3,
            capabilities=list(ANALYSIS_3D_CAPABILITIES),
            app_version="1.12.3",
            supported_apis=(1, 2),
            supported_capabilities=SUPPORTED_CAPABILITIES,
        )
        self.assertFalse(verdict.ok)
        self.assertIn("3", verdict.reason)


class Input3DTests(unittest.TestCase):
    def test_shape_nbytes_and_describe(self):
        snapshot = make_input_3d()
        self.assertEqual(snapshot.shape, (4, 5, 6))
        self.assertEqual(snapshot.nbytes, 4 * 5 * 6 * 4)
        text = snapshot.describe()
        self.assertIn("4×5×6", text)
        self.assertIn("0 fs", text)
        self.assertIn("ROI #3", text)

    def test_arrays_are_read_only(self):
        snapshot = make_input_3d()
        for array in (snapshot.volume, snapshot.x, snapshot.y, snapshot.e):
            self.assertFalse(array.flags.writeable)


class SurfaceValidationTests(unittest.TestCase):
    def setUp(self):
        self.x = np.linspace(-1.0, 1.0, 5)
        self.y = np.linspace(-1.0, 1.0, 4)
        self.z = np.zeros((5, 4))

    def test_accepts_bands_and_copies_read_only(self):
        surface = validate_analysis_surface(
            AnalysisSurface2D(
                x=self.x,
                y=self.y,
                surfaces=[BandSurface(self.z, "Band 1", "#ff8800", 0.6)],
                z_unit="eV",
            )
        )
        self.assertEqual(len(surface.surfaces), 1)
        self.assertFalse(surface.surfaces[0].z.flags.writeable)
        self.assertEqual(surface.surfaces[0].color, "#ff8800")
        self.assertAlmostEqual(surface.surfaces[0].opacity, 0.6)
        self.assertEqual(surface.z_axis_label(), "E (eV)")

    def test_duplicate_labels_get_suffixes(self):
        surface = validate_analysis_surface(
            AnalysisSurface2D(
                x=self.x,
                y=self.y,
                surfaces=[BandSurface(self.z, "B"), BandSurface(self.z, "B"), BandSurface(self.z, "")],
            )
        )
        labels = [band.label for band in surface.surfaces]
        self.assertEqual(labels, ["B", "B (2)", "Band 3"])

    def test_nan_is_allowed_infinity_is_not(self):
        with_nan = self.z.copy()
        with_nan[0, 0] = np.nan
        validate_analysis_surface(
            AnalysisSurface2D(x=self.x, y=self.y, surfaces=[BandSurface(with_nan)])
        )
        with_inf = self.z.copy()
        with_inf[0, 0] = np.inf
        with self.assertRaises(AnalysisResultError):
            validate_analysis_surface(
                AnalysisSurface2D(x=self.x, y=self.y, surfaces=[BandSurface(with_inf)])
            )

    def test_rejects_bad_input(self):
        cases = {
            "不是面结果": "not a surface",
            "没有带": AnalysisSurface2D(x=self.x, y=self.y, surfaces=[]),
            "形状不符": AnalysisSurface2D(x=self.x, y=self.y, surfaces=[BandSurface(np.zeros((4, 5)))]),
            "坐标不单调": AnalysisSurface2D(
                x=np.array([0.0, 1.0, 1.0, 2.0, 3.0]), y=self.y, surfaces=[BandSurface(self.z)]
            ),
            "坐标含缺测": AnalysisSurface2D(
                x=np.array([0.0, 1.0, np.nan, 3.0, 4.0]), y=self.y, surfaces=[BandSurface(self.z)]
            ),
            "不是带对象": AnalysisSurface2D(x=self.x, y=self.y, surfaces=[self.z]),
            "带数超限": AnalysisSurface2D(
                x=self.x,
                y=self.y,
                surfaces=[BandSurface(self.z, f"B{index}") for index in range(MAX_SURFACE_BANDS + 1)],
            ),
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(AnalysisResultError):
                    validate_analysis_surface(payload)

    def test_descending_axis_is_allowed(self):
        surface = validate_analysis_surface(
            AnalysisSurface2D(
                x=self.x[::-1].copy(), y=self.y, surfaces=[BandSurface(self.z)]
            )
        )
        np.testing.assert_allclose(surface.x, self.x[::-1])


class ProgressTests(unittest.TestCase):
    def test_token_without_sink_is_silent(self):
        token = CancelToken()
        token.report_progress(0.5, "一半")  # 不抛异常即为通过

    def test_token_clamps_and_filters(self):
        seen = []
        token = CancelToken(progress_sink=lambda fraction, message: seen.append((fraction, message)))
        token.report_progress(1.5, "超界")
        token.report_progress(-1.0)
        token.report_progress(float("nan"), "非有限")
        token.report_progress("不是数字")
        self.assertEqual(seen, [(1.0, "超界"), (0.0, "")])


class RunnerProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_work_function_progress_reaches_the_signal(self):
        runner = AnalysisTaskRunner()
        self.addCleanup(runner.shutdown)
        events = []
        runner.progress.connect(lambda handle, fraction, message: events.append((fraction, message)))

        def work(snapshot, params, cancel):
            for step in range(1, 4):
                cancel.report_progress(step / 3.0, f"第 {step} 步")
                # 间隔大于节流窗口（0.2 s），三次上报都应当到达主线程。
                time.sleep(0.25)
            from bandscope.extensions.api import AnalysisCurve1D

            return AnalysisCurve1D(x=[0.0, 1.0], y=[0.0, 1.0])

        handle = runner.submit("demo", make_input_3d_for_runner(), work, title="进度")
        self.assertIsNotNone(handle)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(events) < 3:
            QApplication.processEvents()
            time.sleep(0.005)
        QApplication.processEvents()
        self.assertEqual([message for _fraction, message in events], ["第 1 步", "第 2 步", "第 3 步"])
        self.assertAlmostEqual(events[-1][0], 1.0)
        # 节流后仍按顺序、单调不减。
        fractions = [fraction for fraction, _message in events]
        self.assertEqual(fractions, sorted(fractions))


def make_input_3d_for_runner() -> AnalysisInput3D:
    return make_input_3d()


if __name__ == "__main__":
    unittest.main()
