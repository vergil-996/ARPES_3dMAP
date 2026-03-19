import numpy as np
from scipy.ndimage import gaussian_filter, median_filter
from scipy.signal import savgol_filter
# 导入新算法库
import pywt
from skimage.restoration import denoise_nl_means, estimate_sigma


class DenoiseEngines:
    @staticmethod
    def apply_pipeline(data, methods):
        if data is None: return None
        processed_data = data.copy().astype(np.float32)  # 统一转为 float32 防止溢出

        active_methods = [m for m in methods if m != "None" and m is not None]

        for method in active_methods:

            if method == "卡尔曼滤波" and processed_data.ndim == 4:
                processed_data = DenoiseEngines.kalman_filter_4d(processed_data)
                continue  # 处理完跳过，进入下一个方法


            if processed_data.ndim == 4:
                for t in range(processed_data.shape[0]):
                    processed_data[t] = DenoiseEngines.dispatch(processed_data[t], method)
            else:
                processed_data = DenoiseEngines.dispatch(processed_data, method)

        return processed_data

    @staticmethod
    def dispatch(data_3d, method):
        try:
            if method == "频域平滑":
                return gaussian_filter(data_3d, sigma=1.0)

            elif method == "滑动平均":
                return median_filter(data_3d, size=3)

            elif method == "Savitzky-Golay滤波":
                return savgol_filter(data_3d, window_length=5, polyorder=2, axis=-1)



            elif method == "小波去噪":

                import pywt
                img_3d = np.asanyarray(data_3d, dtype=np.float32)
                wavelet_name = 'db1'
                level = 1
                mode = 'soft'
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

                return reconstructed[slices].astype(np.float32)

            elif method == "贝叶斯去噪":

                sigma_est = np.mean(estimate_sigma(data_3d))
                return denoise_nl_means(data_3d, h=1.15 * sigma_est, fast_mode=True,
                                        patch_size=5, patch_distance=3)

            return data_3d
        except Exception as e:
            print(f"去噪失败 [{method}]: {e}")
            return data_3d

    @staticmethod
    def kalman_filter_4d(data_4d):
        """
        真正的卡尔曼滤波：沿着时间轴 (Axis 0) 进行递归更新
        原理：X(t) = X(t-1) + K * (Z(t) - X(t-1))
        """

        nt, nx, ny, ne = data_4d.shape
        filtered_data = np.zeros_like(data_4d)

        # 初始状态
        p_prev = np.ones((nx, ny, ne)) * 0.1  # 初始误差协方差
        x_prev = data_4d[0].copy()  # 初始估计值
        filtered_data[0] = x_prev

        # 过程噪声和测量噪声 (ARPES 经验值，可根据信噪比调整)
        Q = 1e-4  # Process variance
        R = 0.1 ** 2  # Measurement variance

        for t in range(1, nt):
            # 1. 预测阶段
            p_mid = p_prev + Q
            # 2. 更新阶段 (计算卡尔曼增益)
            k_gain = p_mid / (p_mid + R)
            # 3. 修正估计值
            x_curr = x_prev + k_gain * (data_4d[t] - x_prev)
            # 4. 更新协方差
            p_prev = (1 - k_gain) * p_mid

            filtered_data[t] = x_curr
            x_prev = x_curr

        return filtered_data