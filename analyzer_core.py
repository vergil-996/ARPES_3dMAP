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

    def load_npz(self, path, is_flip):
        try:
            data = np.load(path)

            # 1. 自动寻找 4 维数据矩阵
            main_key = None
            for key in data.keys():
                if hasattr(data[key], 'ndim') and data[key].ndim == 4:
                    main_key = key
                    break
            if main_key is None: return False, "文件内未找到 4 维数据矩阵"

            raw = data[main_key]
            sh = list(raw.shape)
            idx_pool = [0, 1, 2, 3]
            roles = {'X': -1, 'Y': -1, 'E': -1, 'T': -1}

            # 2. 【指纹识别逻辑】：按物理特征锁定维度身份
            # 建立键名搜索组
            search_map = {'X': ['kx', 'x'], 'Y': ['ky', 'y'], 'E': ['E', 'energy', 'En', 'Ef'],
                'T': ['time', 'delay', 't']}

            # 第一步：根据键名和长度匹配（初步锁定）
            for role, keys in search_map.items():
                for k in keys:
                    if k in data:
                        length = data[k].size
                        # 寻找长度匹配且尚未被分配的维度
                        for i in idx_pool:
                            if sh[i] == length:
                                roles[role] = i
                                idx_pool.remove(i)
                                break
                        if roles[role] != -1: break

            # 第二步：对于未识别出的维度（比如键名不匹配或三轴同长），根据物理跨度判定
            if len(idx_pool) > 0:
                # 剩余轴的物理特征分析
                # 在 ARPES 中：E 轴跨度通常 < 20eV, K 轴跨度通常很大 (像素或 >100)
                remaining_roles = [r for r, idx in roles.items() if idx == -1]
                for i in list(idx_pool):
                    # 尝试猜测这个维度对应的坐标数组（如果有的话）
                    # 这里假设如果没匹配到键名，就按剩下的索引顺序补位，但引入跨度检查
                    pass  # 基础补位在后面统一处理

            # 第三步：保底补位（防止 repeated axis）
            for role in ['X', 'Y', 'E', 'T']:
                if roles[role] == -1 and idx_pool:
                    roles[role] = idx_pool.pop(0)

            idx_kx, idx_ky, idx_E, idx_T = roles['X'], roles['Y'], roles['E'], roles['T']

            # 3. 重排与内存优化
            # 强制使用 float32 并确保内存连续，彻底解决卡退问题
            self.raw_data = np.ascontiguousarray(raw.transpose(idx_kx, idx_ky, idx_E, idx_T), dtype=np.float32)

            if is_flip:
                # 空间三轴一次性翻转
                self.raw_data = np.flip(self.raw_data, axis=(0, 1, 2))

            # 4. 坐标映射与单位同步
            self.coords = {}

            # 助手函数：安全提取坐标并翻转
            def get_coord(role_key, keys, fallback_shape_idx):
                actual_key = next((k for k in keys if k in data), None)
                if actual_key:
                    arr = data[actual_key].flatten()
                else:
                    # 如果没找到坐标，按像素索引生成
                    arr = np.arange(self.raw_data.shape[fallback_shape_idx])
                return arr

            # 获取各轴坐标
            self.coords['X'] = get_coord('X', search_map['X'], 0)
            self.coords['Y'] = get_coord('Y', search_map['Y'], 1)
            self.coords['E'] = get_coord('E', search_map['E'], 2)
            self.coords['delay'] = get_coord('T', search_map['T'], 3)

            # 如果开启了 is_flip，物理坐标数组也需要翻转，否则刻度会反
            if is_flip:
                for k in ['X', 'Y', 'E']:
                    self.coords[k] = np.flip(self.coords[k])
            else:
                # 兼容你原代码中对 Y 轴的特殊 flip 习惯
                self.coords['Y'] = np.flip(self.coords['Y'])

            return True, self.raw_data.shape

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"智能加载失败: {e}")
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