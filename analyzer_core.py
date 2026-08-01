import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

from axis_mapping import (
    NPZ_COORD_CANDIDATES,
    canonicalize_array,
    find_first_key,
    infer_axis_roles,
    legacy_hdf5_axis_roles,
    metadata_axis_roles,
    role_order_for_shape,
)


class AnalyzerCore(QObject):
    progress_changed = pyqtSignal(int)
    data_loaded = pyqtSignal(tuple)
    DISPLAY_LABEL_MAP = {
        "X轴下限": ("X", 0),
        "X轴上限": ("X", 1),
        "Y轴下限": ("Y", 2),
        "Y轴上限": ("Y", 3),
        "Z轴下限": ("E", 4),
        "Z轴上限": ("E", 5),
    }
    AXIS_INDEX_MAP = {0: "X", 1: "Y", 2: "E"}

    def __init__(self):
        super().__init__()
        self.raw_data = None
        self.coords = {'X': None, 'Y': None, 'E': None, 'delay': None}
        self.has_time_axis = True

    def load_npz(self, path):
        try:
            data = np.load(path)

            def safe_get_array(key):
                try:
                    return data[key]
                except (KeyError, ValueError):
                    return None

            search_map = NPZ_COORD_CANDIDATES
            coord_keys = {
                role: find_first_key(data, candidates)
                for role, candidates in search_map.items()
            }
            coord_lengths = {}
            for role, key in coord_keys.items():
                values = safe_get_array(key) if key is not None else None
                if values is not None:
                    coord_lengths[role] = int(values.size)

            # 1. 自动寻找 4 维或 3 维数据矩阵
            main_key = None
            main_ndim = None
            for key in data.keys():
                arr = safe_get_array(key)
                if hasattr(arr, 'ndim') and arr.ndim == 4:
                    main_key = key
                    main_ndim = 4
                    break
            if main_key is None:
                for key in data.keys():
                    arr = safe_get_array(key)
                    if hasattr(arr, 'ndim') and arr.ndim == 3:
                        main_key = key
                        main_ndim = 3
                        break
            if main_key is None:
                return False, "文件内未找到 3 维或 4 维数据矩阵"

            raw = data[main_key]
            sh = list(raw.shape)
            has_time_coord_axis = coord_lengths.get("T") in sh
            role_order = role_order_for_shape(sh, main_ndim == 4 or has_time_coord_axis)

            roles = (
                metadata_axis_roles(
                    safe_get_array("axis_mapping_version"),
                    safe_get_array("axis_order"),
                    role_order,
                )
                or legacy_hdf5_axis_roles(sh, coord_lengths, role_order)
                or infer_axis_roles(sh, coord_lengths, role_order)
            )
            self.has_time_axis = 'T' in role_order

            # 3. 重排与内存优化
            if self.has_time_axis:
                raw_4d = canonicalize_array(raw, roles, role_order)
                self.raw_data = np.ascontiguousarray(raw_4d, dtype=np.float32)
            else:
                raw_3d = np.ascontiguousarray(
                    canonicalize_array(raw, roles, role_order),
                    dtype=np.float32,
                )
                self.raw_data = raw_3d[..., np.newaxis]

            # 4. 坐标映射与单位同步
            self.coords = {}

            # 助手函数：安全提取坐标
            def get_coord(role_key, fallback_shape_idx):
                actual_key = coord_keys[role_key]
                if actual_key:
                    loaded_arr = safe_get_array(actual_key)
                    if loaded_arr is not None:
                        arr = loaded_arr.flatten()
                    else:
                        arr = np.arange(self.raw_data.shape[fallback_shape_idx])
                else:
                    # 如果没找到坐标，按像素索引生成
                    arr = np.arange(self.raw_data.shape[fallback_shape_idx])#根据此轴长度生成相应的等差数列（默认步长为1）
                return arr

            # 获取各轴坐标
            self.coords['X'] = get_coord('X', 0)
            self.coords['Y'] = get_coord('Y', 1)
            self.coords['E'] = get_coord('E', 2)
            if self.has_time_axis:
                self.coords['delay'] = get_coord('T', 3)
            else:
                self.coords['delay'] = np.array([0.0], dtype=np.float32)

            return True, self.raw_data.shape

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"智能加载失败: {e}")
            return False, str(e)

    def _resolve_axis_key(self, axis_ref):
        if isinstance(axis_ref, int):
            return self.AXIS_INDEX_MAP.get(axis_ref, "X")

        if axis_ref in self.coords:
            return axis_ref

        if axis_ref in self.DISPLAY_LABEL_MAP:
            return self.DISPLAY_LABEL_MAP[axis_ref][0]

        return "X"

    def _get_axis_coords(self, axis_ref):
        axis_key = self._resolve_axis_key(axis_ref)
        coords = self.coords.get(axis_key)

        if coords is None:
            return np.array([], dtype=np.float32)

        return np.asarray(coords, dtype=np.float64).flatten()

    def logical_to_physical(self, axis_ref, logical_value):
        coords = self._get_axis_coords(axis_ref)
        if coords.size == 0:
            return float(logical_value)

        if coords.size == 1:
            return float(coords[0])

        logical_value = float(np.clip(logical_value, 0, coords.size - 1))
        logical_axis = np.arange(coords.size, dtype=np.float64)
        return float(np.interp(logical_value, logical_axis, coords))

    def physical_to_logical(self, axis_ref, physical_value):
        coords = self._get_axis_coords(axis_ref)
        if coords.size <= 1:
            return 0.0

        physical_value = float(physical_value)
        logical_axis = np.arange(coords.size, dtype=np.float64)

        diffs = np.diff(coords)
        if np.all(diffs >= 0):
            x_axis = coords
            y_axis = logical_axis
        elif np.all(diffs <= 0):
            x_axis = coords[::-1]
            y_axis = logical_axis[::-1]
        else:
            nearest_idx = int(np.argmin(np.abs(coords - physical_value)))
            return float(nearest_idx)

        clipped_value = float(np.clip(physical_value, np.min(x_axis), np.max(x_axis)))
        return float(np.interp(clipped_value, x_axis, y_axis))

    def logical_bounds_to_physical_bounds(self, bounds):
        if bounds is None:
            return None

        physical_bounds = list(bounds)
        for label, (_, idx) in self.DISPLAY_LABEL_MAP.items():
            physical_bounds[idx] = self.logical_to_physical(label, bounds[idx])
        return physical_bounds

    def physical_texts_to_logical_texts(self, texts):
        logical_texts = dict(texts)

        for label, (_, _) in self.DISPLAY_LABEL_MAP.items():
            raw_text = texts.get(label, "").strip()
            if not raw_text:
                logical_texts[label] = ""
                continue

            try:
                logical_texts[label] = str(self.physical_to_logical(label, float(raw_text)))
            except ValueError:
                logical_texts[label] = raw_text

        return logical_texts

    @staticmethod
    def logical_to_render_bounds(bounds, shape, target_size=200.0):
        if bounds is None:
            return None

        render_bounds = []
        for axis_idx in range(3):
            axis_size = shape[axis_idx]
            axis_max = max(axis_size - 1, 0)
            scale = target_size / axis_max if axis_max > 0 else 0.0

            low = float(np.clip(bounds[axis_idx * 2], 0, axis_max))
            up = float(np.clip(bounds[axis_idx * 2 + 1], 0, axis_max))

            render_bounds.extend([low * scale, up * scale])

        return render_bounds

    @staticmethod
    def render_to_logical_bounds(bounds, shape, target_size=200.0):
        logical_bounds = []
        for axis_idx in range(3):
            axis_size = shape[axis_idx]
            axis_max = max(axis_size - 1, 0)
            scale = target_size / axis_max if axis_max > 0 else 0.0

            low = float(bounds[axis_idx * 2])
            up = float(bounds[axis_idx * 2 + 1])

            if scale == 0:
                logical_bounds.extend([0.0, 0.0])
                continue

            logical_bounds.extend([
                float(np.clip(low / scale, 0, axis_max)),
                float(np.clip(up / scale, 0, axis_max)),
            ])

        return logical_bounds

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
                self.slice_info = {"axis": 0, "index": int(x_min)}
                return {"is_2d_mode": True, "slice_info": self.slice_info, "clip_ranges": None,
                    "logical_bounds": [x_min, x_max, y_min, y_max, z_min, z_max]}

            if y_min == y_max:
                self.slice_info = {"axis": 1, "index": int(y_min)}
                return {"is_2d_mode": True, "slice_info": self.slice_info, "clip_ranges": None,
                    "logical_bounds": [x_min, x_max, y_min, y_max, z_min, z_max]}

            if z_min == z_max:
                self.slice_info = {"axis": 2, "index": int(z_min)}
                return {"is_2d_mode": True, "slice_info": self.slice_info, "clip_ranges": None,
                    "logical_bounds": [x_min, x_max, y_min, y_max, z_min, z_max]}

            # 3. 否则判定为 3D 裁剪模式
            self.slice_info = None
            clip_ranges = [x_min, x_max, y_min, y_max, z_min, z_max]
            return {"is_2d_mode": False, "slice_info": None, "clip_ranges": clip_ranges,
                "logical_bounds": clip_ranges}

        except Exception as e:
            print(f"数据解析失败: {e}")
            return None

    @staticmethod
    def build_axis_prefix_sum(data_3d, axis_index):
        """Build a float32 prefix sum for repeated inclusive range queries."""
        source = np.asarray(data_3d, dtype=np.float32)
        axis_index = int(axis_index)
        if source.ndim != 3:
            raise ValueError("Axis prefix sums require a 3D data volume.")
        if axis_index not in (0, 1, 2):
            raise ValueError(f"Unsupported integration axis: {axis_index}")
        if source.shape[axis_index] == 0:
            raise ValueError("Cannot integrate an empty axis.")
        return np.cumsum(source, axis=axis_index, dtype=np.float32)

    @staticmethod
    def range_sum_from_axis_prefix(prefix, axis_index, low_idx, up_idx):
        """Return an inclusive axis range sum from a 3D prefix-sum volume."""
        prefix = np.asarray(prefix, dtype=np.float32)
        axis_index = int(axis_index)
        if prefix.ndim != 3:
            raise ValueError("Axis range queries require a 3D prefix-sum volume.")
        if axis_index not in (0, 1, 2):
            raise ValueError(f"Unsupported integration axis: {axis_index}")

        axis_max = prefix.shape[axis_index] - 1
        if axis_max < 0:
            raise ValueError("Cannot integrate an empty axis.")

        low = int(np.clip(int(low_idx), 0, axis_max))
        up = int(np.clip(int(up_idx), 0, axis_max))
        if low > up:
            low, up = up, low

        plane = [slice(None)] * prefix.ndim
        plane[axis_index] = up
        result = np.array(prefix[tuple(plane)], copy=True)
        if low > 0:
            plane[axis_index] = low - 1
            result -= prefix[tuple(plane)]
        return result

