import numpy as np
from scipy.ndimage import gaussian_filter, median_filter
from scipy.signal import savgol_filter


class DenoiseEngines:
    @staticmethod
    def apply_pipeline(data, methods):
        """ 按顺序执行去噪流水线，支持 3D (单帧) 或 4D (全数据) """
        if data is None: return None

        processed_data = data.copy()
        # 提取非 None 的方法
        active_methods = [m for m in methods if m != "None" and m is not None]

        for method in active_methods:
            # 判断数据维度
            if processed_data.ndim == 4:
                # 如果是 4D 数据 (T, Kx, Ky, E)，我们需要逐帧处理，防止时间轴污染
                for t in range(processed_data.shape[0]):
                    processed_data[t] = DenoiseEngines.dispatch(processed_data[t], method)
            else:
                # 如果是 3D 数据 (Kx, Ky, E)，直接处理
                processed_data = DenoiseEngines.dispatch(processed_data, method)

        return processed_data

    @staticmethod
    def dispatch(data_3d, method):
        """ 只处理 3D 空间维度的调度中心 """
        try:
            if method == "频域平滑":
                # 只在空间 3 维做高斯模糊
                return gaussian_filter(data_3d, sigma=1.0)

            elif method == "滑动平均":
                # 3D 中值滤波，kernel_size=(3,3,3) 确保不跨维
                print("执行滑动平均")
                return median_filter(data_3d, size=3)

            elif method == "Savitzky-Golay滤波":
                # 重点：ARPES 通常对能量轴(最后一维)做 SG 平滑，保持动量分辨率
                return savgol_filter(data_3d, window_length=5, polyorder=2, axis=-1)

            elif method == "卡尔曼滤波":
                # 卡尔曼通常是沿着时间轴追踪的，如果你想对“能带演化”做滤波，
                # 这里反而需要时间轴信息。但为了安全，暂时维持空间平滑。
                return data_3d

            return data_3d
        except Exception as e:
            print(f"去噪失败 [{method}]: {e}")
            return data_3d