from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Mapping, Optional

import numpy as np

from bandscope.core.axis_mapping import (
    AXIS_MAPPING_VERSION,
    NPZ_COORD_CANDIDATES,
    canonicalize_array,
    find_first_key,
    infer_axis_roles,
    legacy_hdf5_axis_roles,
    normalize_role_name,
)


ASSIGNMENT_VERSION = "source_axes_v1"
ANALYSIS_ROLES = ("X", "Y", "E")
ROLES = ("X", "Y", "E", "T")
ROLE_LABELS = {"X": "Kx", "Y": "Ky", "E": "E", "T": "扫描轴"}
SCAN_TIME = "time"
SCAN_ANGLE = "angle"
SCAN_TEMPERATURE = "temperature"
SCAN_CUSTOM = "custom"
SCAN_UNKNOWN = "unknown"
SCAN_KINDS = (SCAN_TIME, SCAN_ANGLE, SCAN_TEMPERATURE, SCAN_CUSTOM, SCAN_UNKNOWN)
SCAN_LABELS = {
    SCAN_TIME: "时间",
    SCAN_ANGLE: "角度",
    SCAN_TEMPERATURE: "温度",
    SCAN_CUSTOM: "自定义",
    SCAN_UNKNOWN: "待确认",
}
SCAN_COORD_CANDIDATES = (
    "time", "delay", "t",
    "temperature", "temp", "Temp", "Temperature",
    "angle", "theta", "phi",
    "scan", "scan_axis", "T",
)

_SCAN_TYPE_ALIASES = {
    "time": SCAN_TIME,
    "delay": SCAN_TIME,
    "t": SCAN_TIME,
    "时间": SCAN_TIME,
    "angle": SCAN_ANGLE,
    "theta": SCAN_ANGLE,
    "phi": SCAN_ANGLE,
    "角度": SCAN_ANGLE,
    "temperature": SCAN_TEMPERATURE,
    "temp": SCAN_TEMPERATURE,
    "温度": SCAN_TEMPERATURE,
    "custom": SCAN_CUSTOM,
    "自定义": SCAN_CUSTOM,
    "unknown": SCAN_UNKNOWN,
    "scan": SCAN_UNKNOWN,
    "待确认": SCAN_UNKNOWN,
}


def scalar_text(value) -> str:
    if value is None:
        return ""
    try:
        items = np.asarray(value).reshape(-1)
        if not items.size:
            return ""
        value = items[0]
    except (TypeError, ValueError):
        pass
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").strip()
    return str(value).strip()


def normalize_scan_kind(value, default=None):
    text = scalar_text(value)
    if not text:
        return default
    return _SCAN_TYPE_ALIASES.get(text.lower(), _SCAN_TYPE_ALIASES.get(text, default))


def scan_kind_for_key(key):
    text = scalar_text(key)
    if text == "T":
        return SCAN_UNKNOWN
    return normalize_scan_kind(text, SCAN_CUSTOM if text else SCAN_UNKNOWN)


def default_scan_name(kind, coordinate_key=None) -> str:
    if kind == SCAN_CUSTOM and coordinate_key:
        return scalar_text(coordinate_key) or "扫描轴"
    return {
        SCAN_TIME: "时间",
        SCAN_ANGLE: "角度",
        SCAN_TEMPERATURE: "温度",
    }.get(kind, "扫描轴")


def numeric_coordinate_candidates(variables: Mapping, *, exclude=()) -> dict:
    excluded = {key for key in exclude if key is not None}
    result = {}
    for key in variables:
        if key in excluded:
            continue
        try:
            values = np.asarray(variables[key])
        except (KeyError, TypeError, ValueError):
            continue
        if values.dtype.kind not in "iuf" or values.size == 0:
            continue
        if values.ndim == 1 or (values.ndim == 2 and 1 in values.shape):
            result[str(key)] = np.asarray(values).reshape(-1)
    return result


def _metadata_sequence(value):
    if value is None:
        return []
    return [scalar_text(item) for item in np.asarray(value).reshape(-1).tolist()]


def _declared_scan_kind(variables, roles, shape, coord_key):
    explicit = normalize_scan_kind(
        variables.get("scan_type", variables.get("scan_kind")), None
    )
    if explicit is not None:
        return explicit, True

    scan_axis = roles.get("T")
    axis_types = _metadata_sequence(variables.get("axis_types"))
    if scan_axis is not None and len(axis_types) == len(shape):
        kind = normalize_scan_kind(axis_types[scan_axis], None)
        if kind is not None:
            return kind, True

    axis_order = _metadata_sequence(variables.get("axis_order"))
    if scan_axis is not None and len(axis_order) == len(shape):
        declared = axis_order[scan_axis]
        if normalize_role_name(declared) == "T" and declared not in {"T", "t", "time", "delay"}:
            kind = normalize_scan_kind(declared, None)
            if kind is not None:
                return kind, True

    kind = scan_kind_for_key(coord_key)
    if kind != SCAN_CUSTOM or coord_key:
        return kind, False
    return SCAN_UNKNOWN, False


def _unit_value(variables, keys):
    for key in keys:
        if key not in variables:
            continue
        text = scalar_text(variables[key])
        if text:
            return text
    return None


def coordinate_candidate_unit(variables, key, role, scan_kind=None) -> Optional[str]:
    if not key:
        return None
    role_keys = {
        "X": ("kx_unit", "X_unit", "x_unit"),
        "Y": ("ky_unit", "Y_unit", "y_unit"),
        "E": ("E_unit", "energy_unit"),
        "T": (),
    }
    keys = list(role_keys.get(role, ()))
    if role == "T":
        if scan_kind == SCAN_TIME:
            keys.extend(("time_unit", "delay_unit"))
        elif scan_kind == SCAN_ANGLE:
            keys.append("angle_unit")
        elif scan_kind == SCAN_TEMPERATURE:
            keys.extend(("temperature_unit", "temp_unit"))
        keys.append("scan_unit")
    keys.extend((f"{key}_unit", f"{key}_units", f"{key}Unit"))
    return _unit_value(variables, keys)


@dataclass
class AxisAssignment:
    array_key: str
    original_shape: tuple
    axis_roles: dict
    role_order: list
    coord_keys: dict = field(default_factory=dict)
    coord_units: dict = field(default_factory=dict)
    scan_kind: str = SCAN_UNKNOWN
    scan_name: str = "扫描轴"
    warnings: tuple = ()
    manual: bool = False

    def clone(self):
        return copy.deepcopy(self)

    @property
    def has_scan(self):
        return "T" in self.role_order and self.axis_roles.get("T") is not None

    @property
    def is_time(self):
        return self.has_scan and self.scan_kind == SCAN_TIME

    @property
    def scan_unit(self):
        return self.coord_units.get("T") if self.has_scan else None

    @property
    def display_scan_name(self):
        return scalar_text(self.scan_name) or default_scan_name(self.scan_kind)

    @property
    def needs_confirmation(self):
        return bool(self.warnings) or (self.has_scan and self.scan_kind == SCAN_UNKNOWN)

    def axis_length(self, role):
        axis = self.axis_roles.get(role)
        if axis is None:
            return 1
        return int(self.original_shape[int(axis)])

    @property
    def canonical_shape(self):
        shape = [self.axis_length(role) for role in self.role_order]
        if "T" not in self.role_order:
            shape.append(1)
        return tuple(int(value) for value in shape)

    def summary(self):
        parts = []
        for role in self.role_order:
            axis = self.axis_roles.get(role)
            label = self.display_scan_name if role == "T" else ROLE_LABELS[role]
            parts.append(f"{label}:{'单例' if axis is None else f'维{int(axis) + 1}'}")
        return " · ".join(parts)

    def to_layout(self, coord_sources):
        units = {
            key: self.coord_units.get(key)
            for key in ANALYSIS_ROLES
        }
        units["delay"] = self.coord_units.get("T")
        return {
            "array_key": str(self.array_key),
            "original_shape": [int(value) for value in self.original_shape],
            "axis_roles": {key: int(value) for key, value in self.axis_roles.items()},
            "role_order": list(self.role_order),
            "coord_keys": {role: self.coord_keys.get(role) for role in ROLES},
            "canonical_shape": list(self.canonical_shape),
            "coord_sources": dict(coord_sources),
            "coord_units": units,
            "assignment_version": ASSIGNMENT_VERSION,
            "scan_type": str(self.scan_kind),
            "scan_name": str(self.display_scan_name),
            "warnings": [str(item) for item in self.warnings],
            "manual": bool(self.manual),
        }

    @classmethod
    def from_layout(cls, layout):
        role_order = [str(role) for role in layout["role_order"]]
        coord_units = {
            role: (layout.get("coord_units") or {}).get(role)
            for role in ANALYSIS_ROLES
        }
        coord_units["T"] = (layout.get("coord_units") or {}).get("delay")
        has_scan = "T" in role_order
        legacy = layout.get("assignment_version") != ASSIGNMENT_VERSION
        kind = (
            SCAN_TIME
            if legacy and has_scan
            else normalize_scan_kind(layout.get("scan_type"), SCAN_UNKNOWN)
        )
        coord_keys = {role: (layout.get("coord_keys") or {}).get(role) for role in ROLES}
        name = scalar_text(layout.get("scan_name")) or default_scan_name(
            kind, coord_keys.get("T")
        )
        return cls(
            array_key=str(layout["array_key"]),
            original_shape=tuple(int(value) for value in layout["original_shape"]),
            axis_roles={str(role): int(axis) for role, axis in layout["axis_roles"].items()},
            role_order=role_order,
            coord_keys=coord_keys,
            coord_units=coord_units,
            scan_kind=kind if has_scan else SCAN_UNKNOWN,
            scan_name=name,
            warnings=tuple(str(item) for item in layout.get("warnings") or ()),
            manual=bool(layout.get("manual", not legacy)),
        )


def _declared_axis_roles(variables, shape, role_order):
    axis_order = _metadata_sequence(variables.get("axis_order"))
    if len(axis_order) != len(shape):
        return None
    normalized = [normalize_role_name(item) for item in axis_order]
    if len(set(normalized)) != len(normalized) or any(
        role not in role_order for role in normalized
    ):
        return None
    return {role: axis for axis, role in enumerate(normalized)}


def infer_assignment(array_key, raw, variables: Mapping, *, search_map=None) -> AxisAssignment:
    raw = np.asarray(raw)
    shape = tuple(int(value) for value in raw.shape)
    search_map = search_map or NPZ_COORD_CANDIDATES
    candidates = numeric_coordinate_candidates(variables, exclude=(array_key,))
    coord_keys = {
        role: find_first_key(variables, keys)
        for role, keys in search_map.items()
        if role != "T"
    }
    coord_keys["T"] = next(
        (
            key for key in SCAN_COORD_CANDIDATES
            if key in candidates and candidates[key].size in shape
        ),
        find_first_key(variables, SCAN_COORD_CANDIDATES),
    )
    declared_coord_keys = _metadata_sequence(variables.get("axis_coordinate_keys"))
    if len(declared_coord_keys) == len(ROLES):
        for role, key in zip(ROLES, declared_coord_keys):
            if key in candidates:
                coord_keys[role] = key
    coord_lengths = {
        role: int(candidates[key].size)
        for role, key in coord_keys.items()
        if key in candidates
    }
    has_scan_coord = any(
        coord_keys.get("T") == key and values.size in shape
        for key, values in candidates.items()
    )
    role_order = list(ANALYSIS_ROLES) + (
        ["T"] if raw.ndim == 4 or has_scan_coord else []
    )
    declared_roles = _declared_axis_roles(variables, shape, role_order)
    roles = (
        declared_roles
        or legacy_hdf5_axis_roles(shape, coord_lengths, role_order)
        or infer_axis_roles(shape, coord_lengths, role_order)
    )

    kind, explicit_kind = _declared_scan_kind(
        variables, roles, shape, coord_keys.get("T")
    )
    if "T" in role_order and not explicit_kind:
        preferred = {
            SCAN_TIME: ("time", "delay", "t"),
            SCAN_ANGLE: ("angle", "theta", "phi"),
            SCAN_TEMPERATURE: ("temperature", "temp", "Temperature", "Temp"),
        }.get(kind, ())
        matching = next(
            (
                key for key in preferred
                if key in candidates and candidates[key].size == raw.shape[roles["T"]]
            ),
            None,
        )
        if matching is not None:
            coord_keys["T"] = matching
        elif kind == SCAN_TIME and scalar_text(
            variables.get("axis_mapping_version")
        ) == AXIS_MAPPING_VERSION and coord_keys.get("T") in NPZ_COORD_CANDIDATES["T"]:
            kind = SCAN_TIME
        elif coord_keys.get("T") == "T":
            kind = SCAN_UNKNOWN

    warnings = []
    if roles.get("X") is not None and roles.get("Y") is not None:
        if shape[roles["X"]] == shape[roles["Y"]]:
            warnings.append("Kx、Ky 等长，按文件顺序分配")
    for role in role_order:
        key = coord_keys.get(role)
        if key is None:
            continue
        values = candidates.get(key)
        axis = roles.get(role)
        expected = 1 if axis is None else shape[axis]
        if values is None or values.size != expected or not np.isfinite(values).all():
            coord_keys[role] = None
            warnings.append(f"{ROLE_LABELS[role]} 坐标 {key} 无效或长度不匹配，已使用索引")
    if "T" in role_order and kind == SCAN_UNKNOWN:
        warnings.append("第四轴类型待确认，按扫描帧号浏览")

    units = {
        role: coordinate_candidate_unit(variables, coord_keys.get(role), role, kind)
        for role in ROLES
    }
    if "T" in role_order and units.get("T") is None:
        keys = ["scan_unit"]
        if kind == SCAN_TIME:
            keys.insert(0, "time_unit")
        elif kind == SCAN_ANGLE:
            keys.insert(0, "angle_unit")
        elif kind == SCAN_TEMPERATURE:
            keys.insert(0, "temperature_unit")
        units["T"] = _unit_value(variables, keys)
    name = scalar_text(variables.get("scan_name")) or default_scan_name(
        kind, coord_keys.get("T")
    )
    return AxisAssignment(
        array_key=str(array_key),
        original_shape=shape,
        axis_roles=dict(roles),
        role_order=role_order,
        coord_keys=coord_keys,
        coord_units=units,
        scan_kind=kind if "T" in role_order else SCAN_UNKNOWN,
        scan_name=name,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def validate_assignment(assignment, candidates: Mapping):
    shape = tuple(int(value) for value in assignment.original_shape)
    if len(shape) not in (3, 4) or any(value <= 0 for value in shape):
        raise ValueError("原始强度数组必须是形状有效的三维或四维数组。")
    if assignment.role_order[:3] != list(ANALYSIS_ROLES) or set(assignment.role_order) - set(ROLES):
        raise ValueError("分析轴顺序无效。")
    if len(assignment.role_order) not in (3, 4):
        raise ValueError("分析轴定义不完整。")
    if "T" in assignment.role_order and assignment.axis_roles.get("T") is None:
        raise ValueError("扫描轴必须对应一个原始维度；静态数据请移除扫描轴。")

    assigned = []
    for role, axis in assignment.axis_roles.items():
        if role not in assignment.role_order:
            raise ValueError(f"维度定义含有未启用的轴：{role}")
        if type(axis) is not int or axis < 0 or axis >= len(shape):
            raise ValueError(f"{ROLE_LABELS.get(role, role)} 的原始维度无效。")
        assigned.append(axis)
    if len(set(assigned)) != len(assigned):
        raise ValueError("每个分析槽位必须使用不同的原始维度。")
    if set(assigned) != set(range(len(shape))):
        raise ValueError("每个原始维度必须且只能分配一次。")

    for role in assignment.role_order:
        key = assignment.coord_keys.get(role)
        if key is None:
            continue
        if key not in candidates:
            raise ValueError(f"{ROLE_LABELS[role]} 的文件坐标 {key} 不存在。")
        values = np.asarray(candidates[key], dtype=np.float64).reshape(-1)
        if values.size != assignment.axis_length(role):
            raise ValueError(
                f"{ROLE_LABELS[role]} 坐标长度 {values.size} 与轴长 {assignment.axis_length(role)} 不匹配。"
            )
        if not values.size or not np.isfinite(values).all():
            raise ValueError(f"{ROLE_LABELS[role]} 坐标必须是非空有限数值。")
    assignment.scan_kind = normalize_scan_kind(assignment.scan_kind, SCAN_UNKNOWN)
    assignment.scan_name = scalar_text(assignment.scan_name) or default_scan_name(
        assignment.scan_kind, assignment.coord_keys.get("T")
    )
    return assignment


def canonical_data(assignment, raw):
    canonical = canonicalize_array(raw, assignment.axis_roles, assignment.role_order)
    if "T" not in assignment.role_order:
        canonical = canonical[..., np.newaxis]
    return np.asarray(canonical, dtype=np.float32)


def assignment_coordinates(assignment, candidates: Mapping):
    coords = {}
    sources = {}
    for role in assignment.role_order:
        key = assignment.coord_keys.get(role)
        values = candidates.get(key) if key is not None else None
        if values is None:
            values = np.arange(assignment.axis_length(role), dtype=np.float64)
            sources[role] = "index"
        else:
            values = np.asarray(values).reshape(-1)
            sources[role] = "file"
        coords[role] = values
    if "T" not in assignment.role_order:
        coords["T"] = np.array([0.0], dtype=np.float32)
        sources["T"] = "index"
    return coords, sources
