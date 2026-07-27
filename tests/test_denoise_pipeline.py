import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from compute_backends import CpuComputeBackend
from denoise_engines import DenoiseEngines
from refresh_pipeline import ComputeJob, ComputeResult, RefreshCause, RenderQuality
from refactored_app import My3DAnalyzer


class DenoiseNumericsTests(unittest.TestCase):
    def test_vectorized_4d_savgol_matches_frame_by_frame_result(self):
        data = np.random.default_rng(12).normal(size=(9, 8, 11, 4)).astype(np.float32)
        params = {
            "window_length": 5,
            "polyorder": 2,
            "smoothing_axis": "e",
        }
        expected = np.stack(
            [
                DenoiseEngines.savitzky_golay_filter_3d(data[..., frame], **params)
                for frame in range(data.shape[-1])
            ],
            axis=-1,
        )

        actual = DenoiseEngines.savitzky_golay_filter_4d(data, **params)

        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(actual.dtype, np.float32)

    def test_parallel_wavelet_matches_single_worker_result(self):
        data = np.random.default_rng(21).normal(size=(12, 10, 8, 3)).astype(np.float32)
        params = {
            "wavelet": "db1",
            "level": 1,
            "threshold_rule": "universal",
            "threshold_mode": "soft",
            "strength": 0.8,
        }

        expected = DenoiseEngines.wavelet_denoise_4d(
            data,
            max_workers=1,
            **params,
        )
        actual = DenoiseEngines.wavelet_denoise_4d(
            data,
            max_workers=4,
            **params,
        )

        np.testing.assert_array_equal(actual, expected)

    def test_cpu_backend_exposes_denoise_as_a_compute_job(self):
        data = np.random.default_rng(3).normal(size=(7, 6, 9, 2)).astype(np.float32)
        methods = [
            {
                "label": DenoiseEngines.SG_METHOD_NAME,
                "params": {
                    "window_length": 5,
                    "polyorder": 2,
                    "smoothing_axis": "e",
                },
            }
        ]

        result = CpuComputeBackend(max_workers=2).execute(
            ComputeJob("denoise_pipeline", {"data": data, "methods": methods})
        )

        expected = DenoiseEngines.apply_pipeline(data, methods, max_workers=1)
        np.testing.assert_array_equal(result.value, expected)
        self.assertEqual(result.backend, "CPU")


class GlobalDenoiseStateTests(unittest.TestCase):
    def test_apply_submits_pipeline_without_running_it_on_the_ui_thread(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        source = np.zeros((4, 5, 7, 2), dtype=np.float32)
        methods = [DenoiseEngines.SG_METHOD_NAME]
        spec = SimpleNamespace(page_id="ordinary-1d", page_kind="energy_dos", params={})
        analyzer.original_raw_data = source
        analyzer.left_workspace = SimpleNamespace(current_spec=lambda: spec)
        analyzer.page_control_blank = SimpleNamespace(
            build_method_specs=lambda _selected, data_shape: methods
        )
        analyzer.page_render = SimpleNamespace(get_denoise_settings=lambda: methods)
        analyzer.active_page_spec = spec
        analyzer._persist_page_ui_state = lambda _spec: None
        analyzer._pending_denoise_signature = None
        analyzer._pending_denoise_methods = None
        analyzer.shared_denoise_signature = analyzer._denoise_signature([])
        analyzer.shared_denoised_raw_data = None
        analyzer._render_exact_ready = True
        analyzer._update_export_button_states = lambda: None
        submitted = []
        request = SimpleNamespace()
        analyzer.refresh_coordinator = SimpleNamespace(
            make_request=lambda *args, **kwargs: request,
            submit_compute=lambda req, work: submitted.append((req, work)),
        )
        analyzer.backend_manager = SimpleNamespace(
            execute=lambda _job: (_ for _ in ()).throw(
                AssertionError("backend work must not run synchronously")
            )
        )

        analyzer.on_apply_denoise()

        self.assertEqual(len(submitted), 1)
        self.assertIs(submitted[0][0], request)
        self.assertTrue(callable(submitted[0][1]))

    def test_all_live_page_specs_share_one_committed_array(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.original_raw_data = np.zeros((4, 5, 7, 2), dtype=np.float32)
        analyzer.original_coords = {
            "X": np.arange(4),
            "Y": np.arange(5),
            "E": np.arange(7),
            "delay": np.arange(2),
        }
        analyzer.global_denoise_methods = [DenoiseEngines.SG_METHOD_NAME]
        analyzer.shared_denoised_raw_data = np.ones_like(analyzer.original_raw_data)

        first, first_coords = analyzer._get_display_state_for_spec(
            SimpleNamespace(page_id="3d", params={})
        )
        second, second_coords = analyzer._get_display_state_for_spec(
            SimpleNamespace(page_id="1d", params={})
        )

        self.assertIs(first, analyzer.shared_denoised_raw_data)
        self.assertIs(second, analyzer.shared_denoised_raw_data)
        self.assertIsNot(first_coords, second_coords)
        self.assertFalse(np.shares_memory(first_coords["E"], second_coords["E"]))

    def test_page_restore_merge_keeps_global_denoise_controls(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        global_state = {
            "methods": ["小波去噪", "None", "Savitzky-Golay滤波"],
            "sg": {"window_length": 9},
            "wavelet": {"wavelet": "sym4"},
        }

        render = analyzer._render_state_with_global_denoise(
            {"combo_n1": "None", "combo_n2": "None", "combo_n3": "None", "combo_map": "A"},
            global_state,
        )
        detail = analyzer._detail_state_with_global_denoise(
            {
                "sg": {"window_length": 5},
                "wavelet": {"wavelet": "db4"},
                "waterfall": {"k_step": 0.1},
            },
            global_state,
        )

        self.assertEqual(
            [render["combo_n1"], render["combo_n2"], render["combo_n3"]],
            global_state["methods"],
        )
        self.assertEqual(detail["sg"], global_state["sg"])
        self.assertEqual(detail["wavelet"], global_state["wavelet"])
        self.assertEqual(detail["waterfall"], {"k_step": 0.1})

    def test_global_result_requests_exact_refresh_for_current_1d_page(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        source = np.zeros((4, 5, 7, 2), dtype=np.float32)
        methods = [DenoiseEngines.SG_METHOD_NAME]
        signature = analyzer._denoise_signature(methods)
        analyzer.original_raw_data = source
        analyzer._pending_denoise_methods = methods
        analyzer._pending_denoise_signature = signature
        analyzer._last_compute_backend = "CPU"
        analyzer.refresh_coordinator = SimpleNamespace(cancel_page=lambda page_id: cancelled.append(page_id))
        analyzer._active_refresh_page_id = lambda: "ordinary-1d"
        analyzer._commit_shared_denoise = lambda *args: committed.append(args)
        analyzer._update_export_button_states = lambda: None
        analyzer.request_refresh = lambda *args, **kwargs: refreshed.append((args, kwargs))
        committed = []
        cancelled = []
        refreshed = []
        request = SimpleNamespace(
            cause=RefreshCause.DENOISE,
            page_id=analyzer.GLOBAL_DENOISE_PAGE_ID,
            snapshot={"denoise_signature": signature, "source_id": id(source)},
        )
        result = ComputeResult(
            value=np.ones_like(source),
            backend="CPU",
            metadata={"denoise_methods": methods},
        )

        with patch("refactored_app.QTimer.singleShot", side_effect=lambda _delay, callback: callback()):
            analyzer._on_refresh_result(request, result)

        self.assertEqual(len(committed), 1)
        self.assertEqual(cancelled, ["ordinary-1d"])
        self.assertEqual(refreshed[0][0], (RefreshCause.DENOISE, RenderQuality.EXACT))
        self.assertTrue(refreshed[0][1]["immediate"])


if __name__ == "__main__":
    unittest.main()
