import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

import numpy as np

from bandscope.processing.compute_backends import CpuComputeBackend
from bandscope.processing.denoise_config import SG_METHOD_ALIASES, WAVELET_METHOD_ALIASES
from bandscope.processing.denoise_engines import DenoiseEngines
from bandscope.rendering.refresh_pipeline import ComputeJob, ComputeResult, RefreshCause, RenderQuality
from bandscope.app.refactored_app import My3DAnalyzer


def _denoise_parameter_state():
    return {
        "gaussian": {"sigma_kx": 0.75, "sigma_ky": 1.25, "sigma_e": 0.0},
        "median": {"size_kx": 3, "size_ky": 5, "size_e": 1},
        "sg": {"window_length": 7, "polyorder": 3, "smoothing_axis": "kx"},
        "wavelet": {
            "wavelet": "sym4",
            "level": 2,
            "threshold_rule": "bayes",
            "threshold_mode": "hard",
            "strength": 0.8,
            "noise_sigma": 0.15,
        },
        "nlmeans": {
            "patch_size": 5,
            "patch_distance": 4,
            "h_scale": 1.3,
            "noise_sigma": 0.2,
        },
        "low_rank": {"rank": 2},
    }


class DenoiseNumericsTests(unittest.TestCase):
    def test_historical_mojibake_method_aliases_remain_supported(self):
        self.assertIn("Savitzky-Golay婊ゆ尝", SG_METHOD_ALIASES)
        self.assertIn("灏忔尝鍘诲櫔", WAVELET_METHOD_ALIASES)
        self.assertTrue(DenoiseEngines._is_savgol_method("Savitzky-Golay婊ゆ尝"))
        self.assertTrue(DenoiseEngines._is_wavelet_method("灏忔尝鍘诲櫔"))

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

    def test_global_capture_includes_every_denoise_parameter_group(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        parameter_state = _denoise_parameter_state()
        analyzer.page_control_blank = SimpleNamespace(
            export_state=lambda: {
                **parameter_state,
                "second_derivative": {"threshold": 0.35},
            }
        )
        analyzer.page_render = SimpleNamespace(
            get_denoise_settings=lambda: ["高斯平滑", "低秩 SVD", "None"]
        )

        captured = analyzer._capture_global_denoise_control_state()

        self.assertEqual(captured["methods"], ["高斯平滑", "低秩 SVD", "None"])
        self.assertNotIn("second_derivative", captured)
        for key, expected in parameter_state.items():
            with self.subTest(parameter_group=key):
                self.assertEqual(captured[key], expected)
                self.assertIsNot(captured[key], expected)

    def test_page_restore_merge_keeps_global_denoise_controls(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        global_state = {
            "methods": ["小波去噪", "None", "Savitzky-Golay滤波"],
            **_denoise_parameter_state(),
        }
        page_detail = {
            key: {"legacy": key} for key in My3DAnalyzer.DENOISE_CONTROL_STATE_KEYS
        }
        page_detail["second_derivative"] = {"threshold": 0.35}
        page_detail["waterfall"] = {"k_step": 0.1}

        render = analyzer._render_state_with_global_denoise(
            {"combo_n1": "None", "combo_n2": "None", "combo_n3": "None", "combo_map": "A"},
            global_state,
        )
        detail = analyzer._detail_state_with_global_denoise(page_detail, global_state)

        self.assertEqual(
            [render["combo_n1"], render["combo_n2"], render["combo_n3"]],
            global_state["methods"],
        )
        self.assertEqual(render["combo_map"], "A")
        for key in My3DAnalyzer.DENOISE_CONTROL_STATE_KEYS:
            with self.subTest(parameter_group=key):
                self.assertEqual(detail[key], global_state[key])
        self.assertEqual(detail["second_derivative"], {"threshold": 0.35})
        self.assertEqual(detail["waterfall"], {"k_step": 0.1})

    def test_every_canonical_method_is_validated_against_compact_roi_shape(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        descriptor = SimpleNamespace(label="ROI #2", compact_shape=(8, 9, 11, 2))
        method_specs = (
            (DenoiseEngines.GAUSSIAN_METHOD_NAME, DenoiseEngines.DEFAULT_GAUSSIAN_PARAMS),
            (DenoiseEngines.MEDIAN_METHOD_NAME, DenoiseEngines.DEFAULT_MEDIAN_PARAMS),
            (DenoiseEngines.SG_METHOD_NAME, DenoiseEngines.DEFAULT_SG_PARAMS),
            (DenoiseEngines.WAVELET_METHOD_NAME, DenoiseEngines.DEFAULT_WAVELET_PARAMS),
            (DenoiseEngines.NLM_METHOD_NAME, DenoiseEngines.DEFAULT_NLMEANS_PARAMS),
            (DenoiseEngines.LOW_RANK_METHOD_NAME, DenoiseEngines.DEFAULT_LOW_RANK_PARAMS),
        )
        methods = [
            {"label": name, "params": dict(params)} for name, params in method_specs
        ]

        with patch.object(DenoiseEngines, "validate_method") as validate_method:
            analyzer._validate_denoise_for_descriptor(methods, descriptor)

        self.assertEqual(
            validate_method.call_args_list,
            [
                call(name, dict(params), descriptor.compact_shape)
                for name, params in method_specs
            ],
        )

    def test_unknown_method_raises_with_scope_and_method_context(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        descriptor = SimpleNamespace(label="完整数据", compact_shape=(8, 9, 11, 2))

        with self.assertRaises(ValueError) as caught:
            analyzer._validate_denoise_for_descriptor(["不存在的方法"], descriptor)

        self.assertIn("完整数据", str(caught.exception))
        self.assertIn("不存在的方法", str(caught.exception))

    def test_invalid_compact_roi_parameters_are_not_silent(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        descriptor = SimpleNamespace(label="ROI #4", compact_shape=(5, 5, 2, 2))
        methods = [
            {
                "label": DenoiseEngines.SG_METHOD_NAME,
                "params": {"window_length": 5, "polyorder": 2, "smoothing_axis": "e"},
            }
        ]

        with self.assertRaises(ValueError) as caught:
            analyzer._validate_denoise_for_descriptor(methods, descriptor)

        self.assertIn("ROI #4", str(caught.exception))
        self.assertIn(DenoiseEngines.SG_METHOD_NAME, str(caught.exception))

    def test_wavelet_validation_error_keeps_level_context(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        descriptor = SimpleNamespace(label="ROI #5", compact_shape=(8, 8, 8, 1))
        methods = [
            {
                "label": DenoiseEngines.WAVELET_METHOD_NAME,
                "params": {"level": 6},
            }
        ]

        with patch.object(
            DenoiseEngines,
            "validate_method",
            side_effect=ValueError("分解级别无效"),
        ):
            with self.assertRaises(ValueError) as caught:
                analyzer._validate_denoise_for_descriptor(methods, descriptor)

        message = str(caught.exception)
        self.assertIn("ROI #5", message)
        self.assertIn(DenoiseEngines.WAVELET_METHOD_NAME, message)
        self.assertIn("级别 6", message)

    def test_non_mapping_params_reach_engine_validation_without_preconversion(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        descriptor = SimpleNamespace(label="完整数据", compact_shape=(8, 8, 8, 1))
        methods = [
            {
                "label": DenoiseEngines.WAVELET_METHOD_NAME,
                "params": [("level", 1)],
            }
        ]

        with self.assertRaises(ValueError) as caught:
            analyzer._validate_denoise_for_descriptor(methods, descriptor)

        self.assertIn("完整数据", str(caught.exception))
        self.assertIn("参数必须是键值映射", str(caught.exception))

    def _rejection_analyzer(self, methods, *, build_error=None, validation_error=None):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        spec = SimpleNamespace(page_id="ordinary-1d", page_kind="energy_dos", params={})
        messages = []
        submitted = []
        analyzer.original_raw_data = np.zeros((8, 9, 11, 2), dtype=np.float32)
        analyzer.data_scopes = {"full": SimpleNamespace()}
        analyzer.left_workspace = SimpleNamespace(current_spec=lambda: spec)

        def build_method_specs(_selected, data_shape=None):
            if build_error is not None:
                raise build_error
            return methods

        def validate_denoise(_methods):
            if validation_error is not None:
                raise validation_error

        analyzer.page_control_blank = SimpleNamespace(build_method_specs=build_method_specs)
        analyzer.page_render = SimpleNamespace(get_denoise_settings=lambda: methods)
        analyzer._validate_denoise_for_scopes = validate_denoise
        analyzer._show_message = lambda *args: messages.append(args)
        analyzer.refresh_coordinator = SimpleNamespace(
            submit_compute=lambda request, work: submitted.append((request, work))
        )
        return analyzer, messages, submitted

    def test_apply_build_error_uses_generic_title_and_skips_background_work(self):
        analyzer, messages, submitted = self._rejection_analyzer(
            [],
            build_error=ValueError("非法参数"),
        )

        analyzer.on_apply_denoise()

        self.assertEqual(messages[0][:2], ("去噪参数无效", "非法参数"))
        self.assertEqual(submitted, [])

    def test_apply_scope_error_uses_generic_title_and_skips_background_work(self):
        analyzer, messages, submitted = self._rejection_analyzer(
            [DenoiseEngines.SG_METHOD_NAME],
            validation_error=ValueError("ROI #1: 参数超出范围"),
        )

        analyzer.on_apply_denoise()

        self.assertEqual(
            messages[0][:2],
            ("去噪参数与数据范围不兼容", "ROI #1: 参数超出范围"),
        )
        self.assertEqual(submitted, [])

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

        with patch("bandscope.app.refactored_app.QTimer.singleShot", side_effect=lambda _delay, callback: callback()):
            analyzer._on_refresh_result(request, result)

        self.assertEqual(len(committed), 1)
        self.assertEqual(cancelled, ["ordinary-1d"])
        self.assertEqual(refreshed[0][0], (RefreshCause.DENOISE, RenderQuality.EXACT))
        self.assertTrue(refreshed[0][1]["immediate"])


if __name__ == "__main__":
    unittest.main()
