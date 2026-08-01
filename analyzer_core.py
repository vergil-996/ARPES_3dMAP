import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

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

            search_map = {
                'X': ['kx', 'x', 'X'],
                'Y': ['ky', 'y', 'Y'],
                'E': ['E', 'energy', 'En', 'Ef'],
                'T': ['time', 'delay', 't', 'T'],
            }
            expected_axis_mapping_version = "mat_keyed_v2"

            def find_coord_key(role):
                return next((k for k in search_map[role] if k in data), None)

            def coord_length(role):
                key = find_coord_key(role)
                if key is None:
                    return None
                arr = safe_get_array(key)
                if arr is None:
                    return None
                return int(arr.size)

            def normalize_role_name(value):
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="ignore")
                value = str(value).strip()
                aliases = {
                    "kx": "X",
                    "x": "X",
                    "X": "X",
                    "ky": "Y",
                    "y": "Y",
                    "Y": "Y",
                    "E": "E",
                    "energy": "E",
                    "En": "E",
                    "Ef": "E",
                    "time": "T",
                    "delay": "T",
                    "t": "T",
                    "T": "T",
                }
                return aliases.get(value, value)

            def get_string_metadata(key):
                arr = safe_get_array(key)
                if arr is None:
                    return ""
                values = np.asarray(arr).reshape(-1)
                if values.size == 0:
                    return ""
                value = values[0]
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="ignore")
                return str(value)

            def roles_from_axis_order(role_order):
                if get_string_metadata("axis_mapping_version") != expected_axis_mapping_version:
                    return None

                axis_order_arr = safe_get_array("axis_order")
                if axis_order_arr is None:
                    return None

                axis_order = [
                    normalize_role_name(item)
                    for item in np.asarray(axis_order_arr).reshape(-1).tolist()
                ]
                if len(axis_order) != len(role_order):
                    return None
                if set(axis_order) != set(role_order):
                    return None
                return {role: axis_order.index(role) for role in role_order}

            def roles_from_legacy_hdf5_shape(shape, role_order):
                x_len = coord_length('X')
                y_len = coord_length('Y')
                e_len = coord_length('E')
                t_len = coord_length('T')

                if len(shape) == 3 and {'X', 'Y', 'E'} == set(role_order):
                    # Legacy converter saved MATLAB v7.3 HDF5 data as (E, X, Y).
                    if (
                        x_len is not None and y_len is not None and e_len is not None
                        and shape[0] == e_len and shape[1] == x_len and shape[2] == y_len
                        and not (shape[0] == x_len and shape[1] == y_len and shape[2] == e_len)
                    ):
                        return {'X': 1, 'Y': 2, 'E': 0}

                if len(shape) == 4 and {'X', 'Y', 'E', 'T'} == set(role_order):
                    # Legacy converter saved MATLAB v7.3 HDF5 data as (T, E, X, Y).
                    if (
                        x_len is not None and y_len is not None and t_len is not None
                        and shape[0] == t_len and shape[2] == x_len and shape[3] == y_len
                        and (e_len is None or shape[1] == e_len)
                        and not (shape[0] == x_len and shape[1] == y_len and (e_len is None or shape[2] == e_len) and shape[3] == t_len)
                    ):
                        return {'X': 2, 'Y': 3, 'E': 1, 'T': 0}

                return None

            def roles_from_coord_lengths(shape, role_order):
                roles = {}
                remaining = list(range(len(shape)))

                def assign_unique(role):
                    length = coord_length(role)
                    if length is None:
                        return
                    matches = [idx for idx in remaining if shape[idx] == length]
                    if len(matches) == 1:
                        roles[role] = matches[0]
                        remaining.remove(matches[0])

                def assign_first(role):
                    length = coord_length(role)
                    if length is None:
                        return
                    matches = [idx for idx in remaining if shape[idx] == length]
                    if matches:
                        roles[role] = matches[0]
                        remaining.remove(matches[0])

                for role in ('T', 'E'):
                    if role in role_order:
                        assign_unique(role)

                for role in ('X', 'Y'):
                    if role in role_order:
                        assign_first(role)

                fallback_roles = []
                if ('X' in roles or 'Y' in roles) and 'E' in role_order and 'E' not in roles:
                    fallback_roles.append('E')
                fallback_roles.extend(role for role in role_order if role not in roles and role not in fallback_roles)

                for role in fallback_roles:
                    if not remaining:
                        break
                    roles[role] = remaining.pop(0)

                return roles

            def canonicalize_raw(raw, roles, role_order):
                present_roles = [role for role in role_order if role in roles]
                present_axes = [roles[role] for role in present_roles]
                if len(set(present_axes)) != len(present_axes):
                    raise ValueError(f"轴映射重复: {roles}")
                if set(present_axes) != set(range(raw.ndim)):
                    raise ValueError(f"轴映射不完整: shape={raw.shape}, roles={roles}")

                canonical = raw.transpose(*present_axes) if present_axes else raw
                for axis_idx, role in enumerate(role_order):
                    if role not in roles:
                        canonical = np.expand_dims(canonical, axis=axis_idx)
                return canonical

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
            has_time_coord_axis = coord_length('T') in sh
            if main_ndim == 4 or has_time_coord_axis:
                role_order = ['X', 'Y', 'E', 'T']
            else:
                role_order = ['X', 'Y', 'E']

            roles = (
                roles_from_axis_order(role_order)
                or roles_from_legacy_hdf5_shape(sh, role_order)
                or roles_from_coord_lengths(sh, role_order)
            )
            self.has_time_axis = 'T' in role_order

            # 3. 重排与内存优化
            if self.has_time_axis:
                raw_4d = canonicalize_raw(raw, roles, role_order)
                self.raw_data = np.ascontiguousarray(raw_4d, dtype=np.float32)
            else:
                raw_3d = np.ascontiguousarray(canonicalize_raw(raw, roles, role_order), dtype=np.float32)
                self.raw_data = raw_3d[..., np.newaxis]

            # 4. 坐标映射与单位同步
            self.coords = {}

            # 助手函数：安全提取坐标
            def get_coord(role_key, keys, fallback_shape_idx):
                actual_key = next((k for k in keys if k in data), None)
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
            self.coords['X'] = get_coord('X', search_map['X'], 0)
            self.coords['Y'] = get_coord('Y', search_map['Y'], 1)
            self.coords['E'] = get_coord('E', search_map['E'], 2)
            if self.has_time_axis:
                self.coords['delay'] = get_coord('T', search_map['T'], 3)
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

