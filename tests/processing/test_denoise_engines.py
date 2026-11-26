import unittest
from unittest.mock import patch

import numpy as np

from bandscope.processing.denoise_config import (
    DEFAULT_GAUSSIAN_PARAMS,
    DEFAULT_LOW_RANK_PARAMS,
    DEFAULT_MEDIAN_PARAMS,
    DEFAULT_NLMEANS_PARAMS,
    DEFAULT_SG_PARAMS,
    DEFAULT_WAVELET_PARAMS,
    DENOISE_METHODS,
    GAUSSIAN_METHOD_NAME,
    LOW_RANK_METHOD_NAME,
    MAX_MEDIAN_SIZE,
    MAX_NLM_PATCH_DISTANCE,
    MAX_NLM_PATCH_SIZE,
    MAX_SG_POLYORDER,
    MEDIAN_METHOD_NAME,
    NLM_METHOD_NAME,
    SG_METHOD_NAME,
    WAVELET_METHOD_NAME,
    canonical_method_name,
)
from bandscope.processing.denoise_engines import DenoiseEngines


class DenoiseConfigTests(unittest.TestCase):
    def test_final_menu_order_and_default_parameters(self):
        self.assertEqual(
            DENOISE_METHODS,
            (
                "None",
                "高斯平滑",
                "中值滤波",
                "Savitzky-Golay滤波",
                "小波去噪",
                "非局部均值",
                "低秩 SVD",
            ),
        )
        self.assertEqual(
            DEFAULT_GAUSSIAN_PARAMS,
            {"sigma_kx": 1.0, "sigma_ky": 1.0, "sigma_e": 1.0},
        )
        self.assertEqual(
            DEFAULT_MEDIAN_PARAMS,
            {"size_kx": 3, "size_ky": 3, "size_e": 3},
        )
        self.assertEqual(
            DEFAULT_SG_PARAMS,
            {"window_length": 5, "polyorder": 2, "smoothing_axis": "e"},
        )
        self.assertEqual(
            DEFAULT_WAVELET_PARAMS,
            {
                "wavelet": "db4",
                "level": 3,
                "threshold_rule": "universal",
                "threshold_mode": "soft",
                "strength": 1.0,
                "noise_sigma": 0.0,
            },
        )
        self.assertEqual(
            DEFAULT_NLMEANS_PARAMS,
            {
                "patch_size": 5,
                "patch_distance": 3,
                "h_scale": 1.15,
                "noise_sigma": 0.0,
            },
        )
        self.assertEqual(DEFAULT_LOW_RANK_PARAMS, {"rank": 3})
        self.assertEqual(MAX_MEDIAN_SIZE, 31)
        self.assertEqual(MAX_NLM_PATCH_SIZE, 15)
        self.assertEqual(MAX_NLM_PATCH_DISTANCE, 20)
        self.assertEqual(MAX_SG_POLYORDER, 4)

    def test_legacy_aliases_resolve_to_canonical_names(self):
        cases = {
            "频域平滑": GAUSSIAN_METHOD_NAME,
            "滑动平均": MEDIAN_METHOD_NAME,
            "Savitzky-Golay婊ゆ尝": SG_METHOD_NAME,
            "灏忔尝鍘诲櫔": WAVELET_METHOD_NAME,
            "贝叶斯去噪": NLM_METHOD_NAME,
            None: "None",
        }
        for alias, expected in cases.items():
            with self.subTest(alias=alias):
                self.assertEqual(canonical_method_name(alias), expected)
        for name in DENOISE_METHODS:
            with self.subTest(name=name):
                self.assertEqual(canonical_method_name(name), name)

    def test_engine_exposes_configuration_constants(self):
        for name in (
            "GAUSSIAN_METHOD_NAME",
            "MEDIAN_METHOD_NAME",
            "SG_METHOD_NAME",
            "WAVELET_METHOD_NAME",
            "NLM_METHOD_NAME",
            "LOW_RANK_METHOD_NAME",
            "DEFAULT_GAUSSIAN_PARAMS",
            "DEFAULT_MEDIAN_PARAMS",
            "DEFAULT_SG_PARAMS",
            "DEFAULT_WAVELET_PARAMS",
            "DEFAULT_NLMEANS_PARAMS",
            "DEFAULT_LOW_RANK_PARAMS",
            "DENOISE_METHODS",
            "MAX_MEDIAN_SIZE",
            "MAX_NLM_PATCH_SIZE",
            "MAX_NLM_PATCH_DISTANCE",
            "MAX_SG_POLYORDER",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(DenoiseEngines, name))


class DenoiseAlgorithmTests(unittest.TestCase):
    def test_all_algorithms_preserve_a_constant_volume(self):
        data = np.full((6, 5, 7), 2.5, dtype=np.float32)
        cases = (
            (GAUSSIAN_METHOD_NAME, {}),
            (MEDIAN_METHOD_NAME, {}),
            (SG_METHOD_NAME, {}),
            (WAVELET_METHOD_NAME, {"wavelet": "db1", "level": 2}),
            (NLM_METHOD_NAME, {"patch_size": 3, "patch_distance": 1}),
            (LOW_RANK_METHOD_NAME, {"rank": 1}),
        )
        for method, params in cases:
            with self.subTest(method=method):
                result = DenoiseEngines.dispatch(data, method, params)
                self.assertEqual(result.dtype, np.float32)
                np.testing.assert_allclose(result, data, rtol=0.0, atol=2e-6)

    def test_gaussian_and_median_smooth_an_impulse(self):
        data = np.zeros((7, 7, 7), dtype=np.float32)
        data[3, 3, 3] = 1.0

        gaussian = DenoiseEngines.dispatch(data, GAUSSIAN_METHOD_NAME)
        median = DenoiseEngines.dispatch(data, MEDIAN_METHOD_NAME)

        self.assertGreater(gaussian[3, 3, 3], 0.0)
        self.assertLess(gaussian[3, 3, 3], 1.0)
        self.assertAlmostEqual(float(gaussian.sum()), 1.0, places=5)
        np.testing.assert_array_equal(median, np.zeros_like(data))

    def test_zero_gaussian_sigmas_and_unit_median_sizes_disable_filtering(self):
        data = np.random.default_rng(10).normal(size=(5, 4, 6)).astype(np.float32)
        gaussian = DenoiseEngines.dispatch(
            data,
            GAUSSIAN_METHOD_NAME,
            {"sigma_kx": 0.0, "sigma_ky": 0.0, "sigma_e": 0.0},
        )
        median = DenoiseEngines.dispatch(
            data,
            MEDIAN_METHOD_NAME,
            {"size_kx": 1, "size_ky": 1, "size_e": 1},
        )
        np.testing.assert_array_equal(gaussian, data)
        np.testing.assert_array_equal(median, data)

    def test_savgol_reconstructs_a_quadratic_energy_curve(self):
        energy = np.linspace(-1.0, 1.0, 9, dtype=np.float32)
        curve = 3.0 * energy**2 - 2.0 * energy + 1.0
        data = np.broadcast_to(curve, (4, 5, energy.size)).copy()

        result = DenoiseEngines.dispatch(data, SG_METHOD_NAME)

        np.testing.assert_allclose(result, data, rtol=0.0, atol=2e-6)

    def test_wavelet_reduces_impulse_energy_with_known_noise_sigma(self):
        data = np.zeros((16, 16, 16), dtype=np.float32)
        data[8, 8, 8] = 1.0
        params = {
            "wavelet": "db1",
            "level": 2,
            "threshold_rule": "universal",
            "threshold_mode": "soft",
            "strength": 1.0,
            "noise_sigma": 0.05,
        }

        result = DenoiseEngines.dispatch(data, WAVELET_METHOD_NAME, params)

        self.assertLess(float(np.sum(result**2)), float(np.sum(data**2)))
        self.assertEqual(result.shape, data.shape)

    def test_sure_and_bayes_use_a_threshold_for_each_detail_subband(self):
        data = np.random.default_rng(11).normal(size=(8, 8, 8)).astype(np.float32)
        original = DenoiseEngines._compute_wavelet_threshold
        for rule in ("sure", "bayes"):
            with self.subTest(rule=rule):
                with patch.object(
                    DenoiseEngines,
                    "_compute_wavelet_threshold",
                    side_effect=original,
                ) as compute_threshold:
                    DenoiseEngines.dispatch(
                        data,
                        WAVELET_METHOD_NAME,
                        {
                            "wavelet": "db1",
                            "level": 2,
                            "threshold_rule": rule,
                            "noise_sigma": 0.1,
                        },
                    )
                self.assertEqual(compute_threshold.call_count, 14)
                for call in compute_threshold.call_args_list:
                    self.assertEqual(call.kwargs["noise_sigma"], 0.1)

    def test_nlmeans_passes_estimated_sigma_and_required_options(self):
        data = np.random.default_rng(12).random((5, 5, 5), dtype=np.float32)
        filtered = np.zeros_like(data)
        with (
            patch(
                "bandscope.processing.denoise_engines.estimate_sigma",
                return_value=np.array([0.2, 0.3]),
            ),
            patch(
                "bandscope.processing.denoise_engines.denoise_nl_means",
                return_value=filtered,
            ) as nlmeans,
        ):
            result = DenoiseEngines.dispatch(
                data,
                NLM_METHOD_NAME,
                {"patch_size": 3, "patch_distance": 2, "h_scale": 1.2},
            )

        kwargs = nlmeans.call_args.kwargs
        self.assertAlmostEqual(kwargs["sigma"], 0.25)
        self.assertAlmostEqual(kwargs["h"], 1.2 * 0.25)
        self.assertEqual(kwargs["patch_size"], 3)
        self.assertEqual(kwargs["patch_distance"], 2)
        self.assertTrue(kwargs["preserve_range"])
        self.assertTrue(kwargs["fast_mode"])
        np.testing.assert_array_equal(result, filtered)

    def test_low_rank_reconstruction_recovers_known_rank_and_rejects_noise(self):
        rng = np.random.default_rng(13)
        spatial = rng.normal(size=(12, 2))
        energy = rng.normal(size=(2, 6))
        clean = (spatial @ energy).reshape(3, 4, 6).astype(np.float32)

        reconstructed = DenoiseEngines.dispatch(clean, LOW_RANK_METHOD_NAME, {"rank": 2})
        np.testing.assert_allclose(reconstructed, clean, rtol=0.0, atol=2e-6)

        noisy = clean + rng.normal(0.0, 0.05, clean.shape).astype(np.float32)
        denoised = DenoiseEngines.dispatch(noisy, LOW_RANK_METHOD_NAME, {"rank": 2})
        noisy_error = np.linalg.norm(noisy - clean)
        denoised_error = np.linalg.norm(denoised - clean)
        self.assertLess(denoised_error, noisy_error)

    def test_low_rank_float64_gram_matches_svd_with_separated_singular_values(self):
        rng = np.random.default_rng(23)
        left, _ = np.linalg.qr(rng.normal(size=(4, 4)))
        right, _ = np.linalg.qr(rng.normal(size=(6, 6)))
        matrix = (left[:, :2] * np.array([1.0e4, 1.0])) @ right[:, :2].T
        data = matrix.reshape(2, 2, 6).astype(np.float32)

        reference_matrix = data.reshape(4, 6).astype(np.float64)
        left_svd, singular_values, right_svd = np.linalg.svd(
            reference_matrix, full_matrices=False
        )
        expected = (
            (left_svd[:, :2] * singular_values[:2]) @ right_svd[:2]
        )

        actual = DenoiseEngines.low_rank_svd_3d(data, rank=2)

        self.assertEqual(actual.dtype, np.float32)
        self.assertGreater((singular_values[0] / singular_values[1]) ** 2, 1.0e6)
        np.testing.assert_allclose(
            actual.reshape(4, 6), expected, rtol=0.0, atol=2.0e-4
        )
        weak_component = left_svd[:, 1] @ actual.reshape(4, 6) @ right_svd[1]
        self.assertAlmostEqual(float(weak_component), float(singular_values[1]), places=3)

    def test_all_legacy_aliases_match_their_canonical_algorithm(self):
        data = np.random.default_rng(14).random((6, 5, 7), dtype=np.float32)
        cases = (
            ("频域平滑", GAUSSIAN_METHOD_NAME, {"sigma_kx": 0.7}),
            ("滑动平均", MEDIAN_METHOD_NAME, {"size_e": 3}),
            (
                "Savitzky-Golay婊ゆ尝",
                SG_METHOD_NAME,
                {"window_length": 5, "polyorder": 2},
            ),
            (
                "灏忔尝鍘诲櫔",
                WAVELET_METHOD_NAME,
                {"wavelet": "db1", "level": 1, "noise_sigma": 0.1},
            ),
            (
                "贝叶斯去噪",
                NLM_METHOD_NAME,
                {"patch_size": 3, "patch_distance": 1, "noise_sigma": 0.1},
            ),
        )
        for alias, canonical, params in cases:
            with self.subTest(alias=alias):
                expected = DenoiseEngines.dispatch(data, canonical, params)
                actual = DenoiseEngines.dispatch(data, alias, params)
                np.testing.assert_array_equal(actual, expected)


class DenoiseValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = np.random.default_rng(15).normal(size=(5, 4, 7)).astype(np.float32)

    def test_unknown_method_removed_kalman_and_unknown_params_raise(self):
        cases = (
            "未知方法",
            "卡尔曼滤波",
            "Gaussian",
        )
        for method in cases:
            with self.subTest(method=method):
                with self.assertRaises(ValueError):
                    DenoiseEngines.dispatch(self.data, method)
        with self.assertRaises(ValueError):
            DenoiseEngines.dispatch(self.data, GAUSSIAN_METHOD_NAME, {"unknown": 1})
        with self.assertRaises(ValueError):
            DenoiseEngines.apply_pipeline(
                self.data,
                [{"label": MEDIAN_METHOD_NAME, "params": {"size": 3}}],
            )
        with self.assertRaises(ValueError):
            DenoiseEngines.apply_pipeline(
                self.data,
                [{"label": GAUSSIAN_METHOD_NAME, "params": [1.0]}],
            )

    def test_invalid_parameters_raise_value_error(self):
        cases = (
            (GAUSSIAN_METHOD_NAME, {"sigma_kx": -0.1}),
            (GAUSSIAN_METHOD_NAME, {"sigma_e": np.nan}),
            (MEDIAN_METHOD_NAME, {"size_kx": 2}),
            (MEDIAN_METHOD_NAME, {"size_ky": 0}),
            (MEDIAN_METHOD_NAME, {"size_e": 3.5}),
            (MEDIAN_METHOD_NAME, {"size_kx": MAX_MEDIAN_SIZE + 2}),
            (SG_METHOD_NAME, {"window_length": 1, "polyorder": 0}),
            (SG_METHOD_NAME, {"window_length": 4}),
            (SG_METHOD_NAME, {"window_length": 5, "polyorder": 5}),
            (SG_METHOD_NAME, {"window_length": 7, "polyorder": 5}),
            (SG_METHOD_NAME, {"smoothing_axis": "t"}),
            (WAVELET_METHOD_NAME, {"level": 0}),
            (WAVELET_METHOD_NAME, {"threshold_rule": "invalid"}),
            (WAVELET_METHOD_NAME, {"threshold_mode": "invalid"}),
            (WAVELET_METHOD_NAME, {"strength": -1.0}),
            (WAVELET_METHOD_NAME, {"noise_sigma": -0.1}),
            (WAVELET_METHOD_NAME, {"wavelet": "not-a-wavelet"}),
            (NLM_METHOD_NAME, {"patch_size": 4}),
            (NLM_METHOD_NAME, {"patch_size": MAX_NLM_PATCH_SIZE + 2}),
            (NLM_METHOD_NAME, {"patch_distance": 0}),
            (NLM_METHOD_NAME, {"patch_distance": MAX_NLM_PATCH_DISTANCE + 1}),
            (NLM_METHOD_NAME, {"h_scale": 0.0}),
            (NLM_METHOD_NAME, {"noise_sigma": -0.1}),
            (LOW_RANK_METHOD_NAME, {"rank": 0}),
            (LOW_RANK_METHOD_NAME, {"rank": 1.5}),
            (LOW_RANK_METHOD_NAME, {"rank": 8}),
        )
        for method, params in cases:
            with self.subTest(method=method, params=params):
                with self.assertRaises(ValueError):
                    DenoiseEngines.validate_method(method, params, self.data.shape)

    def test_direct_apis_enforce_workload_upper_bounds(self):
        for key in ("size_kx", "size_ky", "size_e"):
            with self.subTest(method="median", key=key):
                with self.assertRaisesRegex(ValueError, str(MAX_MEDIAN_SIZE)):
                    DenoiseEngines.median_filter_3d(
                        self.data, **{key: MAX_MEDIAN_SIZE + 2}
                    )
        with self.assertRaisesRegex(ValueError, str(MAX_NLM_PATCH_SIZE)):
            DenoiseEngines.nlmeans_denoise_3d(
                self.data, patch_size=MAX_NLM_PATCH_SIZE + 2
            )
        with self.assertRaisesRegex(ValueError, str(MAX_NLM_PATCH_DISTANCE)):
            DenoiseEngines.nlmeans_denoise_3d(
                self.data, patch_distance=MAX_NLM_PATCH_DISTANCE + 1
            )

        DenoiseEngines.validate_method(
            MEDIAN_METHOD_NAME,
            {"size_kx": MAX_MEDIAN_SIZE, "size_ky": MAX_MEDIAN_SIZE, "size_e": MAX_MEDIAN_SIZE},
            self.data.shape,
        )
        DenoiseEngines.validate_method(
            NLM_METHOD_NAME,
            {
                "patch_size": MAX_NLM_PATCH_SIZE,
                "patch_distance": MAX_NLM_PATCH_DISTANCE,
            },
            self.data.shape,
        )

    def test_savgol_window_and_polyorder_limits_apply_to_direct_api(self):
        data = np.full_like(self.data, 1.25)
        for window_length, polyorder in ((3, 0), (3, 2), (5, 4)):
            with self.subTest(window_length=window_length, polyorder=polyorder):
                result = DenoiseEngines.savitzky_golay_filter_3d(
                    data, window_length=window_length, polyorder=polyorder
                )
                np.testing.assert_allclose(result, data, rtol=0.0, atol=2.0e-6)

        invalid_cases = (
            {"window_length": 1, "polyorder": 0},
            {"window_length": 3, "polyorder": 3},
            {"window_length": 7, "polyorder": MAX_SG_POLYORDER + 1},
        )
        for params in invalid_cases:
            with self.subTest(params=params):
                with self.assertRaises(ValueError):
                    DenoiseEngines.savitzky_golay_filter_3d(data, **params)

    def test_wavelet_rejects_thin_roi_levels_and_zero_available_level(self):
        cases = (
            ((2, 8, 8, 0), "db1", 2, 1),
            ((2, 8, 8), "db4", 1, 0),
        )
        for shape, wavelet, level, max_level in cases:
            with self.subTest(shape=shape, wavelet=wavelet, level=level):
                pattern = rf"层数 {level}.*上限 {max_level}"
                with self.assertRaisesRegex(ValueError, pattern):
                    DenoiseEngines.validate_method(
                        WAVELET_METHOD_NAME,
                        {"wavelet": wavelet, "level": level},
                        shape,
                    )
                with self.assertRaisesRegex(ValueError, pattern):
                    DenoiseEngines.wavelet_denoise_3d(
                        np.zeros(shape[:3], dtype=np.float32),
                        wavelet=wavelet,
                        level=level,
                    )

    def test_savgol_window_error_names_the_selected_axis(self):
        with self.assertRaisesRegex(ValueError, "Kx 轴") as context:
            DenoiseEngines.validate_method(
                SG_METHOD_NAME,
                {"window_length": 7, "smoothing_axis": "kx"},
                (5, 8, 11),
            )
        self.assertNotIn("能量轴", str(context.exception))

    def test_invalid_data_shapes_raise_value_error(self):
        for shape in ((4, 5), (2, 3, 4, 5, 6), (0, 3, 4)):
            with self.subTest(shape=shape):
                with self.assertRaises(ValueError):
                    DenoiseEngines.apply_pipeline(np.zeros(shape, dtype=np.float32), [])
        with self.assertRaises(ValueError):
            DenoiseEngines.dispatch(np.zeros((3, 4, 5, 2), dtype=np.float32), "None")
        with self.assertRaises(ValueError):
            DenoiseEngines.validate_method(LOW_RANK_METHOD_NAME, {"rank": 2}, (3, 4))

    def test_validate_method_checks_rank_against_compact_roi_shape(self):
        DenoiseEngines.validate_method(LOW_RANK_METHOD_NAME, {"rank": 6}, (2, 3, 6, 2))
        with self.assertRaises(ValueError):
            DenoiseEngines.validate_method(LOW_RANK_METHOD_NAME, {"rank": 7}, (2, 3, 6, 2))

    def test_backend_exceptions_are_not_silently_replaced_by_input(self):
        with patch(
            "bandscope.processing.denoise_engines.gaussian_filter",
            side_effect=RuntimeError("fixed failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "fixed failure"):
                DenoiseEngines.dispatch(self.data, GAUSSIAN_METHOD_NAME)


class Denoise4DTests(unittest.TestCase):
    def test_every_method_matches_independent_frames_and_returns_float32(self):
        data = np.random.default_rng(16).random((6, 5, 8, 2), dtype=np.float32)
        cases = (
            (GAUSSIAN_METHOD_NAME, {"sigma_kx": 0.6, "sigma_ky": 0.8}),
            (MEDIAN_METHOD_NAME, {"size_kx": 3, "size_ky": 3, "size_e": 3}),
            (SG_METHOD_NAME, {"window_length": 5, "polyorder": 2}),
            (
                WAVELET_METHOD_NAME,
                {"wavelet": "db1", "level": 1, "noise_sigma": 0.1},
            ),
            (
                NLM_METHOD_NAME,
                {"patch_size": 3, "patch_distance": 1, "noise_sigma": 0.1},
            ),
            (LOW_RANK_METHOD_NAME, {"rank": 2}),
        )
        for method, params in cases:
            with self.subTest(method=method):
                expected = np.stack(
                    [
                        DenoiseEngines.dispatch(data[..., index], method, params)
                        for index in range(data.shape[-1])
                    ],
                    axis=-1,
                )
                actual = DenoiseEngines.apply_pipeline(
                    data,
                    [{"label": method, "params": params}],
                    max_workers=2,
                )
                self.assertEqual(actual.dtype, np.float32)
                self.assertEqual(actual.shape, data.shape)
                # 逐帧与管线两条路径的浮点运算顺序可能不同（相邻切片是否连续、
                # scipy 版本间的实现差异），允许 float32 量级的舍入误差。
                np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-6)

    def test_all_six_methods_allow_an_empty_time_axis(self):
        data = np.empty((8, 8, 8, 0), dtype=np.float32)
        cases = (
            (GAUSSIAN_METHOD_NAME, {}),
            (MEDIAN_METHOD_NAME, {}),
            (SG_METHOD_NAME, {"window_length": 5, "polyorder": 2}),
            (WAVELET_METHOD_NAME, {"wavelet": "db1", "level": 1}),
            (NLM_METHOD_NAME, {"patch_size": 3, "patch_distance": 1}),
            (LOW_RANK_METHOD_NAME, {"rank": 2}),
        )
        for method, params in cases:
            with self.subTest(method=method):
                result = DenoiseEngines.apply_pipeline(
                    data,
                    [{"label": method, "params": params}],
                    max_workers=2,
                )
                self.assertIsNot(result, data)
                self.assertEqual(result.shape, data.shape)
                self.assertEqual(result.dtype, np.float32)
                np.testing.assert_array_equal(result, data)

        with patch(
            "bandscope.processing.denoise_engines.savgol_filter"
        ) as savgol:
            result = DenoiseEngines.savitzky_golay_filter_4d(data)
        savgol.assert_not_called()
        self.assertIsNot(result, data)
        np.testing.assert_array_equal(result, data)

        with self.assertRaisesRegex(ValueError, r"层数 2.*上限 1"):
            DenoiseEngines.wavelet_denoise_4d(
                np.empty((2, 8, 8, 0), dtype=np.float32),
                wavelet="db1",
                level=2,
            )

    def test_frame_pipeline_preallocates_without_np_stack(self):
        data = np.random.default_rng(17).normal(size=(4, 3, 5, 2)).astype(np.float32)
        with patch(
            "bandscope.processing.denoise_engines.np.stack",
            side_effect=AssertionError("4D pipeline must not stack a frame list"),
        ):
            result = DenoiseEngines.apply_pipeline(data, [GAUSSIAN_METHOD_NAME])
        self.assertEqual(result.shape, data.shape)
        self.assertEqual(result.dtype, np.float32)


if __name__ == "__main__":
    unittest.main()
