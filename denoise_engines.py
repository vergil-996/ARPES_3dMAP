import numpy as np
import pywt
from scipy.ndimage import gaussian_filter, median_filter
from scipy.signal import savgol_filter
from skimage.restoration import denoise_nl_means, estimate_sigma


class DenoiseEngines:
    SG_METHOD_NAME = "Savitzky-Golay滤波"
    DEFAULT_SG_PARAMS = {
        "window_length": 5,
        "polyorder": 2,
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

            if method_name == DenoiseEngines.SG_METHOD_NAME and processed_data.ndim == 4:
                processed_data = DenoiseEngines.savitzky_golay_filter_4d(processed_data, **params)
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
    def dispatch(data_3d, method, params=None):
        params = params or {}

        try:
            if method == "频域平滑":
                return gaussian_filter(data_3d, sigma=1.0).astype(np.float32, copy=False)

            if method == "滑动平均":
                return median_filter(data_3d, size=3).astype(np.float32, copy=False)

            if method == DenoiseEngines.SG_METHOD_NAME:
                return DenoiseEngines.savitzky_golay_filter_3d(data_3d, **params)

            if method == "小波去噪":
                img_3d = np.asanyarray(data_3d, dtype=np.float32)
                wavelet_name = "db1"
                level = 1
                mode = "soft"
                coeffs = pywt.wavedecn(img_3d, wavelet=wavelet_name, level=level)

                sigma = np.std(coeffs[0])
                val_threshold = sigma * 0.2
                new_coeffs = [coeffs[0]]

                for detail_dict in coeffs[1:]:
                    th_dict = {}
                    for key, val in detail_dict.items():
                        th_dict[key] = pywt.threshold(val, value=val_threshold, mode=mode)
                    new_coeffs.append(th_dict)

                reconstructed = pywt.waverecn(new_coeffs, wavelet=wavelet_name)
                slices = tuple(slice(0, s) for s in img_3d.shape)
                return reconstructed[slices].astype(np.float32, copy=False)

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
    def savitzky_golay_filter_3d(data_3d, window_length=5, polyorder=2):
        frame = np.asanyarray(data_3d, dtype=np.float32)
        axis_size = frame.shape[-1]

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
            axis=-1,
            mode="interp",
        )
        return np.asarray(filtered, dtype=np.float32)

    @staticmethod
    def savitzky_golay_filter_4d(data_4d, window_length=5, polyorder=2):
        data_4d = np.asanyarray(data_4d, dtype=np.float32)
        frame_count = data_4d.shape[-1]
        frames = [
            DenoiseEngines.savitzky_golay_filter_3d(
                data_4d[..., frame_index],
                window_length=window_length,
                polyorder=polyorder,
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
