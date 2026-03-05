import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

class AnalyzerCore(QObject):
    progress_changed = pyqtSignal(int)
    data_loaded = pyqtSignal(tuple)

    def __init__(self):
        super().__init__()
        self.raw_data = None
        self.coords = {'X': None, 'Y': None, 'E': None, 'delay': None}
        self.is_2d_mode = False

    def load_npz(self, path):
        try:
            npz = np.load(path)
            self.coords['X'] = npz["X"].flatten()
            self.coords['Y'] = npz["Y"].flatten()
            self.coords['E'] = npz["E"].flatten()
            self.coords['delay'] = npz["delay"].flatten() if "delay" in npz else np.arange(npz["binned"].shape[3])
            self.raw_data = npz["binned"]
            return True, self.raw_data.shape
        except Exception as e:
            return False, str(e)

    def get_integrated_dynamics(self, r):
        """ 计算给定索引范围内的强度积分 """
        if self.raw_data is None: return None
        # 提取 ROI 并对 X(0), Y(1), E(2) 三个空间轴求和，保留时间轴
        roi = self.raw_data[int(r[0]):int(r[1])+1,
                            int(r[2]):int(r[3])+1,
                            int(r[4]):int(r[5])+1, :]
        return np.sum(roi, axis=(0, 1, 2))

    def get_data_for_t(self, t_idx):
        return self.raw_data[:, :, :, int(t_idx)]

    def process_cut_logic(self, texts):
        """
        处理逻辑：将 Page1 的文本字典转化为渲染参数
        """
        if self.raw_data is None:
            return None

        try:
            shape = self.raw_data.shape

            # 内部辅助函数：处理空字符串，防止 float("") 崩溃
            def safe_float(key, default_val):
                val = texts.get(key, "").strip()
                return float(val) if val else float(default_val)

            # 1. 解析坐标 (如果用户没填，自动按数据最大范围补全)
            x_min = safe_float("X轴下限", 0)
            x_max = safe_float("X轴上限", shape[0])
            y_min = safe_float("Y轴下限", 0)
            y_max = safe_float("Y轴上限", shape[1])
            z_min = safe_float("Z轴下限", 0)
            z_max = safe_float("Z轴上限", shape[2])

            # 2. 判断模式：如果某一对上下限相等，判定为 2D 切片模式
            # 逻辑顺序：X -> Y -> Z
            if x_min == x_max:
                self.is_2d_mode = True
                self.slice_info = {"axis": 0, "index": int(x_min)}
                return {"is_2d_mode": True, "slice_info": self.slice_info, "clip_ranges": None}

            if y_min == y_max:
                self.is_2d_mode = True
                self.slice_info = {"axis": 1, "index": int(y_min)}
                return {"is_2d_mode": True, "slice_info": self.slice_info, "clip_ranges": None}

            if z_min == z_max:
                self.is_2d_mode = True
                self.slice_info = {"axis": 2, "index": int(z_min)}
                return {"is_2d_mode": True, "slice_info": self.slice_info, "clip_ranges": None}

            # 3. 否则判定为 3D 裁剪模式
            self.is_2d_mode = False
            self.slice_info = None
            clip_ranges = [x_min, x_max, y_min, y_max, z_min, z_max]
            return {"is_2d_mode": False, "slice_info": None, "clip_ranges": clip_ranges}

        except Exception as e:
            print(f"数据解析失败: {e}")
            return None

    # 在 AnalyzerCore 类中添加
    def get_time_integrated_data(self, t_start, t_end):
        """
        对 raw_data 的第 4 维 (Delay 轴) 进行积分
        raw_data shape: (Kx, Ky, E, Time)
        """
        if self.raw_data is None:
            return None

        # 确保索引合法
        t_start = max(0, t_start)
        t_end = min(self.raw_data.shape[3] - 1, t_end)

        # 提取区间数据并求和 (对最后一个维度积分)
        # np.sum 会导致强度增加，这正是积分的物理意义
        integrated = np.sum(self.raw_data[:, :, :, t_start:t_end + 1], axis=3)
        return integrated

    # 在 AnalyzerCore 类中添加
    def get_axis_integrated_data(self, data_3d, axis_name, low_idx, up_idx):
        """
        对 3D 数据在指定轴的区间内进行积分
        axis_name: "X轴", "Y轴", "Z轴"
        """
        if data_3d is None: return None

        # 转换轴名称为 numpy axis 索引 (注意：data_3d shape 是 (Kx, Ky, E))
        axis_map = {"X轴": 0, "Y轴": 1, "Z轴": 2}
        ax = axis_map.get(axis_name, 0)

        # 确保区间合法
        low = max(0, low_idx)
        up = min(data_3d.shape[ax] - 1, up_idx)

        # 提取切片
        if ax == 0:
            sub_data = data_3d[low: up + 1, :, :]
        elif ax == 1:
            sub_data = data_3d[:, low: up + 1, :]
        else:
            sub_data = data_3d[:, :, low: up + 1]

        # 执行积分 (求和)
        return np.sum(sub_data, axis=ax)

    def get_slice_dos_dynamics(self, clip_ranges):
        """
        切片态密度：给定 XYZ 范围，计算每一帧 T 的总强度
        clip_ranges: [x_min, x_max, y_min, y_max, z_min, z_max]
        """
        if self.raw_data is None: return None
        r = [int(x) for x in clip_ranges]

        # 截取空间立方体：(Kx, Ky, E, T) -> (sub_Kx, sub_Ky, sub_E, T)
        sub_cube = self.raw_data[r[0]:r[1], r[2]:r[3], r[4]:r[5], :]

        # 对空间三维求和，保留时间轴：结果 shape 为 (T,)
        return np.sum(sub_cube, axis=(0, 1, 2))

    def get_energy_dos(self, t_idx):
        """
        能级态密度：给定某一帧，对所有 X, Y 求和，得到强度随 E 的分布
        """
        if self.raw_data is None: return None

        # 获取当前帧 (Kx, Ky, E)
        data_3d = self.get_data_for_t(t_idx)

        # 对 X, Y 求和，保留 E 轴：结果 shape 为 (E,)
        return np.sum(data_3d, axis=(0, 1))