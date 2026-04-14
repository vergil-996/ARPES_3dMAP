import numpy as np
import pywt
from scipy.ndimage import gaussian_filter, median_filter
from scipy.signal import savgol_filter
from skimage.restoration import denoise_nl_means, estimate_sigma


class DenoiseEngines:
    SG_METHOD_NAME = "Savitzky-Golay滤波"
    WAVELET_METHOD_NAME = "小波去噪"
    SG_METHOD_ALIASES = {"Savitzky-Golay滤波", "Savitzky-Golay婊ゆ尝"}
    WAVELET_METHOD_ALIASES = {"小波去噪", "灏忔尝鍘诲櫔"}
    DEFAULT_SG_PARAMS = {
        "window_length": 5,
        "polyorder": 2,
        "smoothing_axis": "e",
    }
    DEFAULT_WAVELET_PARAMS = {
        "wavelet": "db4",
        "level": 3,
        "threshold_rule": "universal",
        "threshold_mode": "soft",
        "strength": 1.0,
    }

    @staticmethod
    def apply_pipeline(data, methods):
        if data is None:
            return None

        processed_data = np.asarray(data, dtype=np.float32).copy()
        active_methods = []

        for method_spec in methods:
            method_name, params = DenoiseEngines._parse_method_spec(method_spec)
            if method_name in (None, "None"):
                continue
            active_methods.append((method_name, params))

        for method_name, params in active_methods:
            if method_name == "卡尔曼滤波" and processed_data.ndim == 4:
                processed_data = DenoiseEngines.kalman_filter_4d(processed_data)
                continue

            if DenoiseEngines._is_savgol_method(method_name) and processed_data.ndim == 4:
                processed_data = DenoiseEngines.savitzky_golay_filter_4d(processed_data, **params)
                continue

            if DenoiseEngines._is_wavelet_method(method_name) and processed_data.ndim == 4:
                processed_data = DenoiseEngines.wavelet_denoise_4d(processed_data, **params)
                continue

            if processed_data.ndim == 4:
                frames = [
                    DenoiseEngines.dispatch(processed_data[frame_index], method_name, params)
                    for frame_index in range(processed_data.shape[0])
                ]
                processed_data = np.stack(frames, axis=0).astype(np.float32, copy=False)
            else:
                processed_data = DenoiseEngines.dispatch(processed_data, method_name, params)

        return processed_data.astype(np.float32, copy=False)

    @staticmethod
    def _parse_method_spec(method_spec):
        if isinstance(method_spec, dict):
            method_name = (
                method_spec.get("label")
                or method_spec.get("method")
                or method_spec.get("name")
            )
            params = dict(method_spec.get("params") or {})
            return method_name, params

        return method_spec, {}

    @staticmethod
    def _is_savgol_method(method_name):
        return method_name in DenoiseEngines.SG_METHOD_ALIASES

    @staticmethod
    def _is_wavelet_method(method_name):
        return method_name in DenoiseEngines.WAVELET_METHOD_ALIASES

    @staticmethod
    def _normalize_wavelet_params(params=None):
        merged = dict(DenoiseEngines.DEFAULT_WAVELET_PARAMS)
        if params:
            for key, value in params.items():
                if value is not None:
                    merged[key] = value

        threshold_rule = str(merged.get("threshold_rule", "universal")).strip().lower()
        if threshold_rule not in {"universal", "sure", "bayes"}:
            threshold_rule = DenoiseEngines.DEFAULT_WAVELET_PARAMS["threshold_rule"]

        threshold_mode = str(merged.get("threshold_mode", "soft")).strip().lower()
        if threshold_mode not in {"soft", "hard"}:
            threshold_mode = DenoiseEngines.DEFAULT_WAVELET_PARAMS["threshold_mode"]

        try:
            strength = float(merged.get("strength", 1.0))
        except (TypeError, ValueError):
            strength = DenoiseEngines.DEFAULT_WAVELET_PARAMS["strength"]
        strength = max(0.0, strength)

        try:
            level = int(merged.get("level", DenoiseEngines.DEFAULT_WAVELET_PARAMS["level"]))
        except (TypeError, ValueError):
            level = DenoiseEngines.DEFAULT_WAVELET_PARAMS["level"]
        level = max(1, level)

        wavelet = merged.get("wavelet", DenoiseEngines.DEFAULT_WAVELET_PARAMS["wavelet"])
        if not wavelet:
            wavelet = DenoiseEngines.DEFAULT_WAVELET_PARAMS["wavelet"]

        return {
            "wavelet": str(wavelet),
            "level": level,
            "threshold_rule": threshold_rule,
            "threshold_mode": threshold_mode,
            "strength": strength,
        }

    @staticmethod
    def _estimate_wavelet_noise_sigma(values):
        flat_values = np.asarray(values, dtype=np.float32).ravel()
        if flat_values.size == 0:
            return 0.0

        return float(np.median(np.abs(flat_values)) / 0.6745)

    @staticmethod
    def _compute_wavelet_threshold(values, strength, threshold_rule):
        flat_values = np.asarray(values, dtype=np.float32).ravel()
        if flat_values.size == 0:
            return 0.0

        sigma = DenoiseEngines._estimate_wavelet_noise_sigma(flat_values)
        if sigma <= 0.0:
            return 0.0

        if threshold_rule == "universal":
            return float(strength * sigma * np.sqrt(2.0 * np.log(flat_values.size)))

        if threshold_rule == "sure":
            normalized_sq = np.sort((np.abs(flat_values) / sigma) ** 2)
            count = normalized_sq.size
            indices = np.arange(1, count + 1, dtype=np.float32)
            cumulative = np.cumsum(normalized_sq)
            risks = (count - 2.0 * indices + cumulative + (count - indices) * normalized_sq) / count
            best_index = int(np.argmin(risks))
            return float(strength * sigma * np.sqrt(normalized_sq[best_index]))

        if threshold_rule == "bayes":
            sigma_y = float(np.std(flat_values))
            sigma_x_sq = max(sigma_y ** 2 - sigma ** 2, 0.0)
            if sigma_x_sq <= 0.0:
                return float(strength * sigma)
            sigma_x = np.sqrt(sigma_x_sq)
            return float(strength * (sigma ** 2) / sigma_x)

        return 0.0

    @staticmethod
    def dispatch(data_3d, method, params=None):
        params = params or {}

        try:
            if method == "频域平滑":
                return gaussian_filter(data_3d, sigma=1.0).astype(np.float32, copy=False)

            if method == "滑动平均":
                return median_filter(data_3d, size=3).astype(np.float32, copy=False)

            if DenoiseEngines._is_savgol_method(method):
                return DenoiseEngines.savitzky_golay_filter_3d(data_3d, **params)

            if DenoiseEngines._is_wavelet_method(method):
                return DenoiseEngines.wavelet_denoise_3d(data_3d, **params)

            if method == "贝叶斯去噪":
                sigma_est = float(np.mean(estimate_sigma(data_3d)))
                return denoise_nl_means(
                    data_3d,
                    h=1.15 * sigma_est,
                    fast_mode=True,
                    patch_size=5,
                    patch_distance=3,
                ).astype(np.float32, copy=False)

            return np.asarray(data_3d, dtype=np.float32)
        except Exception as exc:
            print(f"去噪失败 [{method}]: {exc}")
            return np.asarray(data_3d, dtype=np.float32)

    @staticmethod
    def savitzky_golay_filter_3d(data_3d, window_length=5, polyorder=2, smoothing_axis="e"):
        frame = np.asanyarray(data_3d, dtype=np.float32)
        axis_aliases = {
            "e": 2,
            "energy": 2,
            "kx": 0,
            "ky": 1,
        }
        axis_key = str(smoothing_axis).strip().lower()
        if axis_key not in axis_aliases:
            raise ValueError("窗口滑动轴仅支持 kx、ky 或 e。")
        axis_index = axis_aliases[axis_key]
        axis_size = frame.shape[axis_index]

        window_length = int(window_length)
        polyorder = int(polyorder)

        if window_length <= 0:
            raise ValueError("滑动窗口长度必须为正整数。")
        if window_length % 2 == 0:
            raise ValueError("滑动窗口长度必须为奇数。")
        if window_length > axis_size:
            raise ValueError(
                f"滑动窗口长度 {window_length} 不能大于能量轴长度 {axis_size}。"
            )
        if polyorder < 0:
            raise ValueError("多项式阶数不能为负数。")
        if polyorder >= window_length:
            raise ValueError("多项式阶数必须小于滑动窗口长度。")

        filtered = savgol_filter(
            frame,
            window_length=window_length,
            polyorder=polyorder,
            axis=axis_index,
            mode="interp",
        )
        return np.asarray(filtered, dtype=np.float32)

    @staticmethod
    def savitzky_golay_filter_4d(data_4d, window_length=5, polyorder=2, smoothing_axis="e"):
        data_4d = np.asanyarray(data_4d, dtype=np.float32)
        frame_count = data_4d.shape[-1]
        frames = [
            DenoiseEngines.savitzky_golay_filter_3d(
                data_4d[..., frame_index],
                window_length=window_length,
                polyorder=polyorder,
                smoothing_axis=smoothing_axis,
            )
            for frame_index in range(frame_count)
        ]
        return np.stack(frames, axis=-1).astype(np.float32, copy=False)

    @staticmethod
    def wavelet_denoise_3d(
        data_3d,
        wavelet="db4",
        level=3,
        threshold_rule="universal",
        threshold_mode="soft",
        strength=1.0,
    ):
        frame = np.asanyarray(data_3d, dtype=np.float32)
        params = DenoiseEngines._normalize_wavelet_params(
            {
                "wavelet": wavelet,
                "level": level,
                "threshold_rule": threshold_rule,
                "threshold_mode": threshold_mode,
                "strength": strength,
            }
        )

        try:
            max_level = int(pywt.dwtn_max_level(frame.shape, params["wavelet"]))
        except Exception:
            max_level = params["level"]
        if max_level <= 0:
            return frame.astype(np.float32, copy=False)

        params["level"] = min(params["level"], max_level)

        coeffs = pywt.wavedecn(
            frame,
            wavelet=params["wavelet"],
            level=params["level"],
        )
        if len(coeffs) <= 1:
            return frame.astype(np.float32, copy=False)

        finest_detail_dict = coeffs[-1]
        finest_arrays = [
            np.asarray(values, dtype=np.float32).ravel()
            for values in finest_detail_dict.values()
            if np.asarray(values).size > 0
        ]
        if not finest_arrays:
            return frame.astype(np.float32, copy=False)

        universal_threshold = DenoiseEngines._compute_wavelet_threshold(
            np.concatenate(finest_arrays),
            params["strength"],
            params["threshold_rule"],
        )
        if universal_threshold <= 0.0:
            return frame.astype(np.float32, copy=False)

        new_coeffs = [coeffs[0]]
        for detail_dict in coeffs[1:]:
            thresholded_detail = {}
            for key, values in detail_dict.items():
                thresholded_detail[key] = pywt.threshold(
                    values,
                    value=universal_threshold,
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
    ):
        data_4d = np.asanyarray(data_4d, dtype=np.float32)
        frame_count = data_4d.shape[-1]
        frames = [
            DenoiseEngines.wavelet_denoise_3d(
                data_4d[..., frame_index],
                wavelet=wavelet,
                level=level,
                threshold_rule=threshold_rule,
                threshold_mode=threshold_mode,
                strength=strength,
            )
            for frame_index in range(frame_count)
        ]
        return np.stack(frames, axis=-1).astype(np.float32, copy=False)

    @staticmethod
    def kalman_filter_4d(data_4d):
        """
        真正的卡尔曼滤波：沿着时间轴 (Axis 0) 进行递归更新
        原理：X(t) = X(t-1) + K * (Z(t) - X(t-1))
        """

        nt, nx, ny, ne = data_4d.shape
        filtered_data = np.zeros_like(data_4d)

        p_prev = np.ones((nx, ny, ne)) * 0.1
        x_prev = data_4d[0].copy()
        filtered_data[0] = x_prev

        Q = 1e-4
        R = 0.1 ** 2

        for t in range(1, nt):
            p_mid = p_prev + Q
            k_gain = p_mid / (p_mid + R)
            x_curr = x_prev + k_gain * (data_4d[t] - x_prev)
            p_prev = (1 - k_gain) * p_mid

            filtered_data[t] = x_curr
            x_prev = x_curr

        return filtered_data.astype(np.float32, copy=False)
