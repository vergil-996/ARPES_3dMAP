import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal
from bandscope.core.view_recipe import SourceIdentity

from bandscope.core.axis_mapping import interpolate_axis
from bandscope.core.axis_assignment import (
    AxisAssignment,
    ROLES,
    assignment_coordinates,
    canonical_data,
    coordinate_candidate_unit,
    infer_assignment,
    numeric_coordinate_candidates,
    validate_assignment,
)
from bandscope.processing.energy_calibration import calibrate_energy_axis
from bandscope.processing.momentum_calibration import calibrate_momentum_axes


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
        self.source_array = None
        self.coords = {'X': None, 'Y': None, 'E': None, 'delay': None}
        self.has_time_axis = True
        self.axis_assignment = None
        self.auto_axis_assignment = None
        self.coordinate_candidates = {}
        self.coordinate_candidate_units = {}
        self.original_coordinate_candidates = {}
        self.original_coordinate_candidate_units = {}
        # 坐标来源与单位可信度（科研导出用）：
        # coord_sources[axis] = 'file'（文件提供坐标数组）/ 'index'（索引回退）；
        # coord_units[axis] = 显式单位元数据（如 'eV'、'Å⁻¹'），没有则为 None。
        # 时间轴缺单位时不在这里补 fs：source_layout 要与历史复现记录逐字一致，
        # 默认单位由 axis_units.resolve_axis_unit 在显示、导出与插件边界补齐。
        self.coord_sources = {}
        self.coord_units = {}
        self.source_identity = None
        self.source_layout = {}

    def load_npz(self, path, *, layout=None):
        """Prepare independently; failed loads never corrupt the active dataset."""
        candidate = AnalyzerCore()
        try:
            candidate.source_identity = SourceIdentity(path)
        except OSError as exc:
            return False, str(exc)
        success, info = candidate._read_npz(path, layout=layout)
        if success:
            for name in (
                "raw_data", "source_array", "coords", "has_time_axis",
                "axis_assignment", "auto_axis_assignment", "coordinate_candidates",
                "coordinate_candidate_units", "original_coordinate_candidates",
                "original_coordinate_candidate_units", "coord_sources", "coord_units",
                "source_identity", "source_layout",
            ):
                setattr(self, name, getattr(candidate, name))
        return success, info

    def _read_npz(self, path, *, layout=None):
        data = None
        try:
            data = np.load(path, allow_pickle=False)

            def safe_get_array(key):
                try:
                    return data[key]
                except (KeyError, ValueError):
                    return None

            main_key = layout.get("array_key") if layout is not None else None
            if main_key is None:
                for ndim in (4, 3):
                    main_key = next(
                        (
                            key for key in data.keys()
                            if getattr(safe_get_array(key), "ndim", None) == ndim
                        ),
                        None,
                    )
                    if main_key is not None:
                        break
            if main_key is None:
                return False, "文件内未找到 3 维或 4 维数据矩阵"

            raw = data[main_key]
            variables = {
                key: safe_get_array(key)
                for key in data.keys()
                if key != main_key
            }
            candidates = numeric_coordinate_candidates(variables)
            automatic = infer_assignment(main_key, raw, variables)
            assignment = automatic if layout is None else AxisAssignment.from_layout(layout)
            candidate_units = {
                role: {
                    key: coordinate_candidate_unit(variables, key, role, assignment.scan_kind)
                    for key in candidates
                }
                for role in ROLES
            }
            self._apply_assignment(
                raw,
                assignment,
                candidates=candidates,
                automatic=automatic,
                candidate_units=candidate_units,
            )

            if layout is not None:
                if layout.get("assignment_version") is not None:
                    matches = self.source_layout == layout
                else:
                    matches = all(
                        key not in layout or self.source_layout.get(key) == layout[key]
                        for key in (
                            "array_key", "original_shape", "axis_roles", "role_order",
                            "coord_keys", "canonical_shape", "coord_sources", "coord_units",
                        )
                    )
                if not matches:
                    raise ValueError("NPZ 坐标或轴解析结果与复现记录不同。")
            if self.source_identity is not None:
                from bandscope.core.view_recipe import file_stamp
                if file_stamp(path) != self.source_identity.stamp:
                    raise ValueError("NPZ 在加载过程中发生变化。")
            return True, self.raw_data.shape

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"智能加载失败: {e}")
            return False, str(e)
        finally:
            if data is not None:
                data.close()

    def _apply_assignment(
        self,
        raw,
        assignment,
        *,
        candidates,
        automatic=None,
        candidate_units=None,
        original_candidates=None,
        original_candidate_units=None,
    ):
        assignment = validate_assignment(assignment.clone(), candidates)
        source_array = np.asarray(raw, dtype=np.float32)
        canonical = canonical_data(assignment, source_array)
        coordinates, sources = assignment_coordinates(assignment, candidates)
        self.source_array = source_array
        self.raw_data = canonical
        self.axis_assignment = assignment
        self.auto_axis_assignment = (
            automatic.clone() if automatic is not None else assignment.clone()
        )
        self.coordinate_candidates = dict(candidates)
        self.coordinate_candidate_units = {
            role: dict(values) for role, values in (candidate_units or {}).items()
        }
        original_candidates = candidates if original_candidates is None else original_candidates
        original_candidate_units = (
            candidate_units if original_candidate_units is None else original_candidate_units
        )
        self.original_coordinate_candidates = dict(original_candidates)
        self.original_coordinate_candidate_units = {
            role: dict(values) for role, values in (original_candidate_units or {}).items()
        }
        self.has_time_axis = assignment.has_scan
        self.coords = {
            "X": coordinates["X"],
            "Y": coordinates["Y"],
            "E": coordinates["E"],
            "delay": coordinates["T"],
        }
        self.coord_sources = {
            role if role != "T" else "delay": source
            for role, source in sources.items()
        }
        self.coord_units = {
            "X": assignment.coord_units.get("X"),
            "Y": assignment.coord_units.get("Y"),
            "E": assignment.coord_units.get("E"),
            "delay": assignment.coord_units.get("T"),
        }
        self.source_layout = assignment.to_layout(self.coord_sources)

    def copy_with_assignment(self, assignment, *, restore_original_coordinates=False):
        if self.source_array is None or self.axis_assignment is None:
            raise ValueError("当前数据源没有可重新分配的原始数组。")
        candidate = AnalyzerCore()
        candidate.source_identity = self.source_identity
        updated = assignment.clone()
        updated.manual = True
        candidates = (
            self.original_coordinate_candidates
            if restore_original_coordinates
            else self.coordinate_candidates
        )
        candidate_units = (
            self.original_coordinate_candidate_units
            if restore_original_coordinates
            else self.coordinate_candidate_units
        )
        candidate._apply_assignment(
            self.source_array,
            updated,
            candidates=candidates,
            automatic=self.auto_axis_assignment,
            candidate_units=candidate_units,
            original_candidates=self.original_coordinate_candidates,
            original_candidate_units=self.original_coordinate_candidate_units,
        )
        return candidate

    def copy_with_momentum_calibration(self, point_a, point_b, target_distance, unit=""):
        if self.source_array is None or self.axis_assignment is None:
            raise ValueError("当前数据源没有可定标的原始数组。")
        calibration = calibrate_momentum_axes(
            self.coords["X"],
            self.coords["Y"],
            point_a,
            point_b,
            target_distance,
            unit=unit,
        )
        candidate = AnalyzerCore()
        candidate.source_identity = self.source_identity
        updated = self.axis_assignment.clone()
        updated.manual = True
        updated.coord_keys["X"] = "kx"
        updated.coord_keys["Y"] = "ky"
        updated.coord_units["X"] = calibration.unit or None
        updated.coord_units["Y"] = calibration.unit or None
        candidates = dict(self.coordinate_candidates)
        candidates["kx"] = calibration.x
        candidates["ky"] = calibration.y
        candidate_units = {
            role: dict(values)
            for role, values in self.coordinate_candidate_units.items()
        }
        for role, key in (("X", "kx"), ("Y", "ky")):
            candidate_units.setdefault(role, {})[key] = calibration.unit or None
        candidate._apply_assignment(
            self.source_array,
            updated,
            candidates=candidates,
            automatic=self.auto_axis_assignment,
            candidate_units=candidate_units,
            original_candidates=self.original_coordinate_candidates,
            original_candidate_units=self.original_coordinate_candidate_units,
        )
        return candidate

    def copy_with_energy_calibration(
        self, reference_index, second_index, target_spacing, unit=""
    ):
        if self.source_array is None or self.axis_assignment is None:
            raise ValueError("当前数据源没有可定标的原始数组。")
        calibration = calibrate_energy_axis(
            self.coords["E"],
            reference_index,
            second_index,
            target_spacing,
            unit=unit,
        )
        candidate = AnalyzerCore()
        candidate.source_identity = self.source_identity
        updated = self.axis_assignment.clone()
        updated.manual = True
        updated.coord_keys["E"] = "E"
        updated.coord_units["E"] = calibration.unit or None
        candidates = dict(self.coordinate_candidates)
        candidates["E"] = calibration.energy
        candidate_units = {
            role: dict(values)
            for role, values in self.coordinate_candidate_units.items()
        }
        candidate_units.setdefault("E", {})["E"] = calibration.unit or None
        candidate._apply_assignment(
            self.source_array,
            updated,
            candidates=candidates,
            automatic=self.auto_axis_assignment,
            candidate_units=candidate_units,
            original_candidates=self.original_coordinate_candidates,
            original_candidate_units=self.original_coordinate_candidate_units,
        )
        return candidate

    @property
    def has_scan_axis(self):
        return bool(self.axis_assignment is not None and self.axis_assignment.has_scan)

    @property
    def is_time_axis(self):
        return bool(self.axis_assignment is not None and self.axis_assignment.is_time)

    @property
    def scan_kind(self):
        return self.axis_assignment.scan_kind if self.axis_assignment is not None else "unknown"

    @property
    def scan_name(self):
        return self.axis_assignment.display_scan_name if self.axis_assignment is not None else "扫描轴"

    @property
    def scan_unit(self):
        return self.coord_units.get("delay") if self.has_scan_axis else None

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

    def logical_to_physical(self, axis_ref, logical_value, *, clamp=True):
        coords = self._get_axis_coords(axis_ref)
        if coords.size == 0:
            return float(logical_value)

        if coords.size == 1:
            return float(coords[0])

        logical_axis = np.arange(coords.size, dtype=np.float64)
        return float(interpolate_axis(logical_value, logical_axis, coords, extrapolate=not clamp))

    def physical_to_logical(self, axis_ref, physical_value, *, clamp=True):
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

        return float(interpolate_axis(physical_value, x_axis, y_axis, extrapolate=not clamp))

    def logical_bounds_to_physical_bounds(self, bounds, *, clamp=True):
        if bounds is None:
            return None

        physical_bounds = list(bounds)
        for label, (_, idx) in self.DISPLAY_LABEL_MAP.items():
            physical_bounds[idx] = self.logical_to_physical(label, bounds[idx], clamp=clamp)
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
    def logical_to_render_bounds(bounds, shape, target_size=200.0, *, clamp=True):
        if bounds is None:
            return None

        render_bounds = []
        for axis_idx in range(3):
            axis_size = shape[axis_idx]
            axis_max = max(axis_size - 1, 0)
            scale = target_size / axis_max if axis_max > 0 else 0.0

            low, up = bounds[axis_idx * 2:axis_idx * 2 + 2]
            if clamp:
                low, up = np.clip([low, up], 0, axis_max)

            render_bounds.extend([low * scale, up * scale])

        return render_bounds

    @staticmethod
    def render_to_logical_bounds(bounds, shape, target_size=200.0, *, clamp=True):
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

            low, up = low / scale, up / scale
            if clamp:
                low, up = np.clip([low, up], 0, axis_max)
            logical_bounds.extend([float(low), float(up)])

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

