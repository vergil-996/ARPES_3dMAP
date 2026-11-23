import os
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pywt
from scipy.ndimage import gaussian_filter, median_filter
from scipy.signal import savgol_filter
from skimage.restoration import denoise_nl_means, estimate_sigma

from bandscope.processing.denoise_config import (
    DEFAULT_GAUSSIAN_PARAMS as CONFIG_DEFAULT_GAUSSIAN_PARAMS,
    DEFAULT_LOW_RANK_PARAMS as CONFIG_DEFAULT_LOW_RANK_PARAMS,
    DEFAULT_MEDIAN_PARAMS as CONFIG_DEFAULT_MEDIAN_PARAMS,
    DEFAULT_NLMEANS_PARAMS as CONFIG_DEFAULT_NLMEANS_PARAMS,
    DEFAULT_SG_PARAMS as CONFIG_DEFAULT_SG_PARAMS,
    DEFAULT_WAVELET_PARAMS as CONFIG_DEFAULT_WAVELET_PARAMS,
    DENOISE_METHODS as CONFIG_DENOISE_METHODS,
    GAUSSIAN_METHOD_ALIASES as CONFIG_GAUSSIAN_METHOD_ALIASES,
    GAUSSIAN_METHOD_NAME as CONFIG_GAUSSIAN_METHOD_NAME,
    LOW_RANK_METHOD_ALIASES as CONFIG_LOW_RANK_METHOD_ALIASES,
    LOW_RANK_METHOD_NAME as CONFIG_LOW_RANK_METHOD_NAME,
    MAX_MEDIAN_SIZE as CONFIG_MAX_MEDIAN_SIZE,
    MAX_NLM_PATCH_DISTANCE as CONFIG_MAX_NLM_PATCH_DISTANCE,
    MAX_NLM_PATCH_SIZE as CONFIG_MAX_NLM_PATCH_SIZE,
    MAX_SG_POLYORDER as CONFIG_MAX_SG_POLYORDER,
    MEDIAN_METHOD_ALIASES as CONFIG_MEDIAN_METHOD_ALIASES,
    MEDIAN_METHOD_NAME as CONFIG_MEDIAN_METHOD_NAME,
    NLM_METHOD_ALIASES as CONFIG_NLM_METHOD_ALIASES,
    NLM_METHOD_NAME as CONFIG_NLM_METHOD_NAME,
    NLMEANS_METHOD_ALIASES as CONFIG_NLMEANS_METHOD_ALIASES,
    SG_METHOD_ALIASES as CONFIG_SG_METHOD_ALIASES,
    SG_METHOD_NAME as CONFIG_SG_METHOD_NAME,
    WAVELET_METHOD_ALIASES as CONFIG_WAVELET_METHOD_ALIASES,
    WAVELET_METHOD_NAME as CONFIG_WAVELET_METHOD_NAME,
    canonical_method_name,
)


class DenoiseEngines:
    GAUSSIAN_METHOD_NAME = CONFIG_GAUSSIAN_METHOD_NAME
    MEDIAN_METHOD_NAME = CONFIG_MEDIAN_METHOD_NAME
    SG_METHOD_NAME = CONFIG_SG_METHOD_NAME
    WAVELET_METHOD_NAME = CONFIG_WAVELET_METHOD_NAME
    NLM_METHOD_NAME = CONFIG_NLM_METHOD_NAME
    LOW_RANK_METHOD_NAME = CONFIG_LOW_RANK_METHOD_NAME
    GAUSSIAN_METHOD_ALIASES = CONFIG_GAUSSIAN_METHOD_ALIASES
    MEDIAN_METHOD_ALIASES = CONFIG_MEDIAN_METHOD_ALIASES
    SG_METHOD_ALIASES = CONFIG_SG_METHOD_ALIASES
    WAVELET_METHOD_ALIASES = CONFIG_WAVELET_METHOD_ALIASES
    NLM_METHOD_ALIASES = CONFIG_NLM_METHOD_ALIASES
    NLMEANS_METHOD_ALIASES = CONFIG_NLMEANS_METHOD_ALIASES
    LOW_RANK_METHOD_ALIASES = CONFIG_LOW_RANK_METHOD_ALIASES
    DENOISE_METHODS = CONFIG_DENOISE_METHODS
    MAX_MEDIAN_SIZE = CONFIG_MAX_MEDIAN_SIZE
    MAX_NLM_PATCH_SIZE = CONFIG_MAX_NLM_PATCH_SIZE
    MAX_NLM_PATCH_DISTANCE = CONFIG_MAX_NLM_PATCH_DISTANCE
    MAX_SG_POLYORDER = CONFIG_MAX_SG_POLYORDER
    DEFAULT_GAUSSIAN_PARAMS = CONFIG_DEFAULT_GAUSSIAN_PARAMS
    DEFAULT_MEDIAN_PARAMS = CONFIG_DEFAULT_MEDIAN_PARAMS
    DEFAULT_SG_PARAMS = CONFIG_DEFAULT_SG_PARAMS
    DEFAULT_WAVELET_PARAMS = CONFIG_DEFAULT_WAVELET_PARAMS
    DEFAULT_NLMEANS_PARAMS = CONFIG_DEFAULT_NLMEANS_PARAMS
    DEFAULT_LOW_RANK_PARAMS = CONFIG_DEFAULT_LOW_RANK_PARAMS

    @staticmethod
    def _float_param(value, label, *, minimum=0.0, strict_minimum=False):
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{label}必须是数值。")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{label}必须是数值。") from exc
        if not np.isfinite(result):
            raise ValueError(f"{label}必须是有限数值。")
        if strict_minimum and result <= minimum:
            raise ValueError(f"{label}必须大于 {minimum}。")
        if not strict_minimum and result < minimum:
            raise ValueError(f"{label}不能小于 {minimum}。")
        return result

    @staticmethod
    def _int_param(value, label, *, minimum=None, maximum=None):
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{label}必须是整数。")
        try:
            result = int(value)
            exact = float(value) == result
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{label}必须是整数。") from exc
        if not exact:
            raise ValueError(f"{label}必须是整数。")
        if minimum is not None and result < minimum:
            raise ValueError(f"{label}不能小于 {minimum}。")
        if maximum is not None and result > maximum:
            raise ValueError(f"{label}不能大于 {maximum}。")
        return result

    @staticmethod
    def _validated_shape(shape, ndim=None):
        try:
            dimensions = tuple(shape)
        except TypeError as exc:
            raise ValueError("去噪数据形状必须是维度大小的序列。") from exc
        if ndim is not None and len(dimensions) != ndim:
            raise ValueError(f"去噪数据必须是 {ndim}D 数组。")
        if ndim is None and len(dimensions) not in (3, 4):
            raise ValueError("去噪数据必须是 [kx, ky, e] 3D 或 [kx, ky, e, t] 4D 数组。")
        if not dimensions:
            raise ValueError("去噪数据形状不能为空。")
        validated = tuple(
            DenoiseEngines._int_param(size, "数据轴长度", minimum=0)
            for size in dimensions
        )
        if any(size <= 0 for size in validated[:3]):
            raise ValueError("kx、ky、e 轴长度必须大于零。")
        return validated

    @staticmethod
    def _as_array(data, ndim=None):
        try:
            array = np.asarray(data, dtype=np.float32)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("去噪数据必须能转换为 float32 数值数组。") from exc
        DenoiseEngines._validated_shape(array.shape, ndim=ndim)
        return array

    @staticmethod
    def _merged_params(defaults, params, method_name):
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            raise ValueError(f"{method_name} 参数必须是键值映射。")
        unknown = set(params) - set(defaults)
        if unknown:
            names = ", ".join(sorted(repr(key) for key in unknown))
            raise ValueError(f"{method_name} 包含未知参数：{names}。")
        merged = dict(defaults)
        merged.update(params)
        return merged

    @staticmethod
    def _parse_method_spec(method_spec):
        if isinstance(method_spec, Mapping):
            method_name = (
                method_spec.get("label")
                or method_spec.get("method")
                or method_spec.get("name")
            )
            if method_name is None:
                raise ValueError("去噪方法规格缺少 label、method 或 name。")
            params = method_spec.get("params")
            return canonical_method_name(method_name), dict(params) if isinstance(params, Mapping) else params

        return canonical_method_name(method_spec), {}

    @staticmethod
    def _is_savgol_method(method_name):
        return isinstance(method_name, str) and method_name in DenoiseEngines.SG_METHOD_ALIASES

    @staticmethod
    def _is_wavelet_method(method_name):
        return isinstance(method_name, str) and method_name in DenoiseEngines.WAVELET_METHOD_ALIASES

    @staticmethod
    def _normalize_wavelet_params(params=None):
        merged = DenoiseEngines._merged_params(
            DenoiseEngines.DEFAULT_WAVELET_PARAMS,
            params,
            DenoiseEngines.WAVELET_METHOD_NAME,
        )
        wavelet = merged["wavelet"]
        if not isinstance(wavelet, str) or not wavelet.strip():
            raise ValueError("小波名称必须是非空字符串。")
        wavelet = wavelet.strip()
        try:
            pywt.Wavelet(wavelet)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"未知或不可用于离散分解的小波：{wavelet!r}。") from exc

        threshold_rule = merged["threshold_rule"]
        if not isinstance(threshold_rule, str):
            raise ValueError("小波阈值规则必须是 universal、sure 或 bayes。")
        threshold_rule = threshold_rule.strip().lower()
        if threshold_rule not in {"universal", "sure", "bayes"}:
            raise ValueError("小波阈值规则仅支持 universal、sure 或 bayes。")

        threshold_mode = merged["threshold_mode"]
        if not isinstance(threshold_mode, str):
            raise ValueError("小波阈值模式必须是 soft 或 hard。")
        threshold_mode = threshold_mode.strip().lower()
        if threshold_mode not in {"soft", "hard"}:
            raise ValueError("小波阈值模式仅支持 soft 或 hard。")

        return {
            "wavelet": wavelet,
            "level": DenoiseEngines._int_param(merged["level"], "小波分解层数", minimum=1),
            "threshold_rule": threshold_rule,
            "threshold_mode": threshold_mode,
            "strength": DenoiseEngines._float_param(merged["strength"], "小波阈值强度"),
            "noise_sigma": DenoiseEngines._float_param(
                merged["noise_sigma"], "小波噪声标准差"
            ),
        }

    @staticmethod
    def _validated_method(method_name, params, shape):
        canonical = canonical_method_name(method_name)
        shape = DenoiseEngines._validated_shape(shape)
        if canonical == "None":
            if params:
                raise ValueError("None 去噪方法不接受参数。")
            return canonical, {}

        defaults = {
            DenoiseEngines.GAUSSIAN_METHOD_NAME: DenoiseEngines.DEFAULT_GAUSSIAN_PARAMS,
            DenoiseEngines.MEDIAN_METHOD_NAME: DenoiseEngines.DEFAULT_MEDIAN_PARAMS,
            DenoiseEngines.SG_METHOD_NAME: DenoiseEngines.DEFAULT_SG_PARAMS,
            DenoiseEngines.WAVELET_METHOD_NAME: DenoiseEngines.DEFAULT_WAVELET_PARAMS,
            DenoiseEngines.NLM_METHOD_NAME: DenoiseEngines.DEFAULT_NLMEANS_PARAMS,
            DenoiseEngines.LOW_RANK_METHOD_NAME: DenoiseEngines.DEFAULT_LOW_RANK_PARAMS,
        }[canonical]
        merged = DenoiseEngines._merged_params(defaults, params, canonical)

        if canonical == DenoiseEngines.GAUSSIAN_METHOD_NAME:
            normalized = {
                key: DenoiseEngines._float_param(merged[key], f"高斯 {key}")
                for key in defaults
            }
        elif canonical == DenoiseEngines.MEDIAN_METHOD_NAME:
            normalized = {
                key: DenoiseEngines._int_param(
                    merged[key],
                    f"中值 {key}",
                    minimum=1,
                    maximum=DenoiseEngines.MAX_MEDIAN_SIZE,
                )
                for key in defaults
            }
            for key, value in normalized.items():
                if value % 2 == 0:
                    raise ValueError(f"中值 {key} 必须为正奇数，1 表示关闭该轴。")
        elif canonical == DenoiseEngines.SG_METHOD_NAME:
            _axis, window_length, polyorder = DenoiseEngines._validated_savgol_params(
                shape,
                merged["window_length"],
                merged["polyorder"],
                merged["smoothing_axis"],
            )
            normalized = {
                "window_length": window_length,
                "polyorder": polyorder,
                "smoothing_axis": str(merged["smoothing_axis"]).strip().lower(),
            }
        elif canonical == DenoiseEngines.WAVELET_METHOD_NAME:
            normalized = DenoiseEngines._normalize_wavelet_params(merged)
            max_level = int(pywt.dwtn_max_level(shape[:3], normalized["wavelet"]))
            if normalized["level"] > max_level:
                raise ValueError(
                    f"小波分解层数 {normalized['level']} 超出数据形状 {shape[:3]} "
                    f"使用 {normalized['wavelet']} 小波时的可用上限 {max_level}。"
                )
        elif canonical == DenoiseEngines.NLM_METHOD_NAME:
            patch_size = DenoiseEngines._int_param(
                merged["patch_size"],
                "非局部均值 patch_size",
                minimum=1,
                maximum=DenoiseEngines.MAX_NLM_PATCH_SIZE,
            )
            if patch_size % 2 == 0:
                raise ValueError("非局部均值 patch_size 必须为正奇数。")
            normalized = {
                "patch_size": patch_size,
                "patch_distance": DenoiseEngines._int_param(
                    merged["patch_distance"],
                    "非局部均值 patch_distance",
                    minimum=1,
                    maximum=DenoiseEngines.MAX_NLM_PATCH_DISTANCE,
                ),
                "h_scale": DenoiseEngines._float_param(
                    merged["h_scale"], "非局部均值 h_scale", strict_minimum=True
                ),
                "noise_sigma": DenoiseEngines._float_param(
                    merged["noise_sigma"], "非局部均值 noise_sigma"
                ),
            }
        else:
            rank_limit = min(shape[0] * shape[1], shape[2])
            normalized = {
                "rank": DenoiseEngines._int_param(
                    merged["rank"], "低秩重构秩", minimum=1, maximum=rank_limit
                )
            }
        return canonical, normalized

    @staticmethod
    def validate_method(method_name, params, shape):
        DenoiseEngines._validated_method(method_name, params, shape)

    @staticmethod
    def apply_pipeline(data, methods, *, max_workers=None):
        if data is None:
            return None

        source_data = DenoiseEngines._as_array(data)
        worker_count = max(1, int(max_workers or (os.cpu_count() or 1)))
        active_methods = []
        for method_spec in methods or ():
            method_name, params = DenoiseEngines._parse_method_spec(method_spec)
            canonical, normalized = DenoiseEngines._validated_method(
                method_name, params, source_data.shape
            )
            if canonical != "None":
                active_methods.append((canonical, normalized))

        if not active_methods:
            return source_data.copy()

        processed_data = source_data
        for method_name, params in active_methods:
            if method_name == DenoiseEngines.SG_METHOD_NAME and processed_data.ndim == 4:
                processed_data = DenoiseEngines.savitzky_golay_filter_4d(
                    processed_data,
                    **params,
                )
                continue

            if method_name == DenoiseEngines.WAVELET_METHOD_NAME and processed_data.ndim == 4:
                processed_data = DenoiseEngines.wavelet_denoise_4d(
                    processed_data,
                    max_workers=worker_count,
                    **params,
                )
                continue

            if processed_data.ndim == 4:
                output = np.empty(processed_data.shape, dtype=np.float32)
                for frame_index in range(processed_data.shape[-1]):
                    output[..., frame_index] = DenoiseEngines.dispatch(
                        processed_data[..., frame_index], method_name, params
                    )
                processed_data = output
            else:
                processed_data = DenoiseEngines.dispatch(processed_data, method_name, params)

        processed_data = processed_data.astype(np.float32, copy=False)
        if np.shares_memory(processed_data, source_data):
            processed_data = processed_data.copy()
        return processed_data

    @staticmethod
    def dispatch(data_3d, method, params=None):
        frame = DenoiseEngines._as_array(data_3d, ndim=3)
        method_name, normalized = DenoiseEngines._validated_method(method, params, frame.shape)

        if method_name == "None":
            return frame.copy()
        if method_name == DenoiseEngines.GAUSSIAN_METHOD_NAME:
            return DenoiseEngines.gaussian_filter_3d(frame, **normalized)
        if method_name == DenoiseEngines.MEDIAN_METHOD_NAME:
            return DenoiseEngines.median_filter_3d(frame, **normalized)
        if method_name == DenoiseEngines.SG_METHOD_NAME:
            return DenoiseEngines.savitzky_golay_filter_3d(frame, **normalized)
        if method_name == DenoiseEngines.WAVELET_METHOD_NAME:
            return DenoiseEngines.wavelet_denoise_3d(frame, **normalized)
        if method_name == DenoiseEngines.NLM_METHOD_NAME:
            return DenoiseEngines.nlmeans_denoise_3d(frame, **normalized)
        if method_name == DenoiseEngines.LOW_RANK_METHOD_NAME:
            return DenoiseEngines.low_rank_svd_3d(frame, **normalized)
        raise ValueError(f"未知去噪方法：{method!r}。")

    @staticmethod
    def gaussian_filter_3d(data_3d, sigma_kx=1.0, sigma_ky=1.0, sigma_e=1.0):
        frame = DenoiseEngines._as_array(data_3d, ndim=3)
        _method, params = DenoiseEngines._validated_method(
            DenoiseEngines.GAUSSIAN_METHOD_NAME,
            {"sigma_kx": sigma_kx, "sigma_ky": sigma_ky, "sigma_e": sigma_e},
            frame.shape,
        )
        filtered = gaussian_filter(
            frame,
            sigma=(params["sigma_kx"], params["sigma_ky"], params["sigma_e"]),
            mode="reflect",
        )
        return np.asarray(filtered, dtype=np.float32)

    @staticmethod
    def median_filter_3d(data_3d, size_kx=3, size_ky=3, size_e=3):
        frame = DenoiseEngines._as_array(data_3d, ndim=3)
        _method, params = DenoiseEngines._validated_method(
            DenoiseEngines.MEDIAN_METHOD_NAME,
            {"size_kx": size_kx, "size_ky": size_ky, "size_e": size_e},
            frame.shape,
        )
        filtered = median_filter(
            frame,
            size=(params["size_kx"], params["size_ky"], params["size_e"]),
            mode="reflect",
        )
        return np.asarray(filtered, dtype=np.float32)

    @staticmethod
    def savitzky_golay_filter_3d(data_3d, window_length=5, polyorder=2, smoothing_axis="e"):
        frame = DenoiseEngines._as_array(data_3d, ndim=3)
        axis_index, window_length, polyorder = DenoiseEngines._validated_savgol_params(
            frame.shape,
            window_length,
            polyorder,
            smoothing_axis,
        )
        filtered = savgol_filter(
            frame,
            window_length=window_length,
            polyorder=polyorder,
            axis=axis_index,
            mode="interp",
        )
        return np.asarray(filtered, dtype=np.float32)

    @staticmethod
    def _validated_savgol_params(shape, window_length=5, polyorder=2, smoothing_axis="e"):
        shape = DenoiseEngines._validated_shape(shape)
        axis_aliases = {
            "e": 2,
            "energy": 2,
            "kx": 0,
            "ky": 1,
        }
        axis_key = str(smoothing_axis).strip().lower()
        if axis_key not in axis_aliases:
            raise ValueError("Savitzky-Golay 平滑轴仅支持 kx、ky 或 e。")
        axis_index = axis_aliases[axis_key]
        axis_size = int(shape[axis_index])

        window_length = DenoiseEngines._int_param(
            window_length, "Savitzky-Golay 滑动窗口长度", minimum=3
        )
        polyorder = DenoiseEngines._int_param(
            polyorder,
            "Savitzky-Golay 多项式阶数",
            minimum=0,
            maximum=DenoiseEngines.MAX_SG_POLYORDER,
        )
        if window_length % 2 == 0:
            raise ValueError("Savitzky-Golay 滑动窗口长度必须为奇数。")
        if window_length > axis_size:
            axis_label = {0: "Kx 轴", 1: "Ky 轴", 2: "E 轴"}[axis_index]
            raise ValueError(
                f"Savitzky-Golay 滑动窗口长度 {window_length} 不能大于{axis_label}长度 {axis_size}。"
            )
        if polyorder >= window_length:
            raise ValueError("Savitzky-Golay 多项式阶数必须小于滑动窗口长度。")
        return axis_index, window_length, polyorder

    @staticmethod
    def savitzky_golay_filter_4d(data_4d, window_length=5, polyorder=2, smoothing_axis="e"):
        data_4d = DenoiseEngines._as_array(data_4d, ndim=4)
        axis_index, window_length, polyorder = DenoiseEngines._validated_savgol_params(
            data_4d.shape,
            window_length,
            polyorder,
            smoothing_axis,
        )
        if data_4d.shape[-1] == 0:
            return data_4d.copy()
        filtered = savgol_filter(
            data_4d,
            window_length=window_length,
            polyorder=polyorder,
            axis=axis_index,
            mode="interp",
        )
        return np.asarray(filtered, dtype=np.float32)

    @staticmethod
    def _estimate_wavelet_noise_sigma(values):
        flat_values = np.asarray(values, dtype=np.float64).ravel()
        if flat_values.size == 0:
            return 0.0
        median = float(np.median(flat_values))
        mad = float(np.median(np.abs(flat_values - median)))
        return mad / 0.6744897501960817

    @staticmethod
    def _compute_wavelet_threshold(values, strength, threshold_rule, noise_sigma=None):
        flat_values = np.asarray(values, dtype=np.float64).ravel()
        if flat_values.size == 0:
            return 0.0
        strength = DenoiseEngines._float_param(strength, "小波阈值强度")
        if noise_sigma is None:
            sigma = DenoiseEngines._estimate_wavelet_noise_sigma(flat_values)
        else:
            sigma = DenoiseEngines._float_param(noise_sigma, "小波噪声标准差")
            if sigma == 0.0:
                sigma = DenoiseEngines._estimate_wavelet_noise_sigma(flat_values)
        if not np.isfinite(sigma):
            raise ValueError("小波噪声标准差估计不是有限数值。")
        if sigma <= 0.0 or strength == 0.0:
            return 0.0

        if threshold_rule == "universal":
            return float(strength * sigma * np.sqrt(2.0 * np.log(flat_values.size)))

        if threshold_rule == "sure":
            normalized_sq = np.sort((flat_values / sigma) ** 2)
            count = normalized_sq.size
            indices = np.arange(1, count + 1, dtype=np.float64)
            cumulative = np.cumsum(normalized_sq)
            risks = (
                count - 2.0 * indices + cumulative + (count - indices) * normalized_sq
            ) / count
            best_index = int(np.argmin(risks))
            return float(strength * sigma * np.sqrt(normalized_sq[best_index]))

        if threshold_rule == "bayes":
            sigma_y_sq = float(np.var(flat_values))
            sigma_x_sq = max(sigma_y_sq - sigma ** 2, 0.0)
            if sigma_x_sq <= 0.0:
                return float("inf")
            return float(strength * (sigma ** 2) / np.sqrt(sigma_x_sq))

        raise ValueError("小波阈值规则仅支持 universal、sure 或 bayes。")

    @staticmethod
    def wavelet_denoise_3d(
        data_3d,
        wavelet="db4",
        level=3,
        threshold_rule="universal",
        threshold_mode="soft",
        strength=1.0,
        noise_sigma=0.0,
    ):
        frame = DenoiseEngines._as_array(data_3d, ndim=3)
        _method, params = DenoiseEngines._validated_method(
            DenoiseEngines.WAVELET_METHOD_NAME,
            {
                "wavelet": wavelet,
                "level": level,
                "threshold_rule": threshold_rule,
                "threshold_mode": threshold_mode,
                "strength": strength,
                "noise_sigma": noise_sigma,
            },
            frame.shape,
        )
        coeffs = pywt.wavedecn(
            frame,
            wavelet=params["wavelet"],
            level=params["level"],
        )
        if len(coeffs) <= 1:
            return frame.copy()

        finest_detail_dict = coeffs[-1]
        finest_arrays = [
            np.asarray(values, dtype=np.float64).ravel()
            for values in finest_detail_dict.values()
            if np.asarray(values).size > 0
        ]
        if not finest_arrays:
            return frame.copy()

        sigma = params["noise_sigma"]
        if sigma == 0.0:
            sigma = DenoiseEngines._estimate_wavelet_noise_sigma(
                np.concatenate(finest_arrays)
            )
        if not np.isfinite(sigma):
            raise ValueError("小波噪声标准差估计不是有限数值。")
        if sigma <= 0.0 or params["strength"] == 0.0:
            return frame.copy()

        universal_threshold = None
        if params["threshold_rule"] == "universal":
            universal_threshold = float(
                params["strength"]
                * sigma
                * np.sqrt(2.0 * np.log(frame.size))
            )

        new_coeffs = [coeffs[0]]
        for detail_dict in coeffs[1:]:
            thresholded_detail = {}
            for key, values in detail_dict.items():
                if universal_threshold is None:
                    threshold = DenoiseEngines._compute_wavelet_threshold(
                        values,
                        params["strength"],
                        params["threshold_rule"],
                        noise_sigma=sigma,
                    )
                else:
                    threshold = universal_threshold
                thresholded_detail[key] = pywt.threshold(
                    values,
                    value=threshold,
                    mode=params["threshold_mode"],
                )
            new_coeffs.append(thresholded_detail)

        reconstructed = pywt.waverecn(
            new_coeffs,
            wavelet=params["wavelet"],
        )
        slices = tuple(slice(0, axis_size) for axis_size in frame.shape)
        return np.asarray(reconstructed[slices], dtype=np.float32)

    @staticmethod
    def wavelet_denoise_4d(
        data_4d,
        wavelet="db4",
        level=3,
        threshold_rule="universal",
        threshold_mode="soft",
        strength=1.0,
        noise_sigma=0.0,
        max_workers=None,
    ):
        data_4d = DenoiseEngines._as_array(data_4d, ndim=4)
        _method, params = DenoiseEngines._validated_method(
            DenoiseEngines.WAVELET_METHOD_NAME,
            {
                "wavelet": wavelet,
                "level": level,
                "threshold_rule": threshold_rule,
                "threshold_mode": threshold_mode,
                "strength": strength,
                "noise_sigma": noise_sigma,
            },
            data_4d.shape,
        )
        frame_count = data_4d.shape[-1]
        if frame_count == 0:
            return data_4d.copy()

        workers = max(1, min(int(max_workers or (os.cpu_count() or 1)), frame_count, 4))

        def denoise_frame(frame_index):
            return DenoiseEngines.wavelet_denoise_3d(
                data_4d[..., frame_index],
                **params,
            )

        output = np.empty(data_4d.shape, dtype=np.float32)
        if workers == 1:
            for frame_index in range(frame_count):
                output[..., frame_index] = denoise_frame(frame_index)
            return output

        with ThreadPoolExecutor(max_workers=workers) as executor:
            for frame_index, frame in enumerate(executor.map(denoise_frame, range(frame_count))):
                output[..., frame_index] = frame
        return output

    @staticmethod
    def nlmeans_denoise_3d(
        data_3d,
        patch_size=5,
        patch_distance=3,
        h_scale=1.15,
        noise_sigma=0.0,
    ):
        frame = DenoiseEngines._as_array(data_3d, ndim=3)
        _method, params = DenoiseEngines._validated_method(
            DenoiseEngines.NLM_METHOD_NAME,
            {
                "patch_size": patch_size,
                "patch_distance": patch_distance,
                "h_scale": h_scale,
                "noise_sigma": noise_sigma,
            },
            frame.shape,
        )
        sigma = params["noise_sigma"]
        if sigma == 0.0 and np.all(frame == frame.flat[0]):
            return frame.copy()
        if sigma == 0.0:
            sigma = float(np.mean(estimate_sigma(frame)))
        if not np.isfinite(sigma) or sigma < 0.0:
            raise ValueError("非局部均值噪声标准差估计必须为非负有限数值。")
        if sigma == 0.0:
            return frame.copy()

        filtered = denoise_nl_means(
            frame,
            patch_size=params["patch_size"],
            patch_distance=params["patch_distance"],
            h=params["h_scale"] * sigma,
            sigma=sigma,
            preserve_range=True,
            fast_mode=True,
        )
        return np.asarray(filtered, dtype=np.float32)

    @staticmethod
    def low_rank_svd_3d(data_3d, rank=3):
        frame = DenoiseEngines._as_array(data_3d, ndim=3)
        _method, params = DenoiseEngines._validated_method(
            DenoiseEngines.LOW_RANK_METHOD_NAME,
            {"rank": rank},
            frame.shape,
        )
        rank = params["rank"]
        matrix = frame.reshape(frame.shape[0] * frame.shape[1], frame.shape[2]).astype(
            np.float64, copy=False
        )
        row_count, column_count = matrix.shape
        if rank == min(row_count, column_count):
            return frame.copy()

        if row_count <= column_count:
            gram = matrix @ matrix.T
            _eigenvalues, eigenvectors = np.linalg.eigh(gram)
            basis = eigenvectors[:, -rank:]
            reconstructed = basis @ (basis.T @ matrix)
        else:
            gram = matrix.T @ matrix
            _eigenvalues, eigenvectors = np.linalg.eigh(gram)
            basis = eigenvectors[:, -rank:]
            reconstructed = (matrix @ basis) @ basis.T
        return np.asarray(reconstructed.reshape(frame.shape), dtype=np.float32)
