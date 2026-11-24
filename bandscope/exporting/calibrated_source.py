from pathlib import Path

import numpy as np
from scipy.io import savemat

from bandscope.core.axis_assignment import ROLES
from bandscope.core.axis_mapping import AXIS_MAPPING_VERSION


def _text(value):
    return np.asarray([str(value)])


def _scan_coordinate_key(core):
    key = core.axis_assignment.coord_keys.get("T")
    if key:
        return str(key)
    return {
        "time": "time",
        "angle": "angle",
        "temperature": "temperature",
    }.get(core.scan_kind, "scan")


def build_calibrated_source_payload(core):
    if core.source_array is None or core.axis_assignment is None:
        raise ValueError("当前数据源没有可导出的完整数据体。")

    source = np.asarray(core.source_array)
    assignment = core.axis_assignment
    if source.ndim not in (3, 4) or tuple(source.shape) != tuple(assignment.original_shape):
        raise ValueError("完整数据体与原始轴分配不一致。")

    axis_order = [
        next(role for role in assignment.role_order if assignment.axis_roles.get(role) == axis)
        for axis in range(source.ndim)
    ]
    scan_key = _scan_coordinate_key(core) if assignment.has_scan else ""
    coordinate_keys = {"X": "kx", "Y": "ky", "E": "E", "T": scan_key}
    payload = {
        "sample": source,
        "axis_order": np.asarray(axis_order),
        "axis_mapping_version": _text(AXIS_MAPPING_VERSION),
        "axis_coordinate_keys": np.asarray([coordinate_keys[role] for role in ROLES]),
        "scan_type": _text(core.scan_kind),
        "scan_name": _text(core.scan_name),
    }

    for key, values in core.coordinate_candidates.items():
        key = str(key)
        if key not in payload:
            payload[key] = np.asarray(values).reshape(-1)

    payload["kx"] = np.asarray(core.coords["X"]).reshape(-1)
    payload["ky"] = np.asarray(core.coords["Y"]).reshape(-1)
    payload["E"] = np.asarray(core.coords["E"]).reshape(-1)
    if assignment.has_scan:
        payload[scan_key] = np.asarray(core.coords["delay"]).reshape(-1)

    for role in ROLES:
        for key, unit in core.coordinate_candidate_units.get(role, {}).items():
            if unit is not None:
                payload[f"{key}_unit"] = _text(unit)

    for key, unit in (
        ("kx_unit", core.coord_units.get("X")),
        ("ky_unit", core.coord_units.get("Y")),
        ("E_unit", core.coord_units.get("E")),
    ):
        if unit is not None:
            payload[key] = _text(unit)
    if assignment.has_scan and core.coord_units.get("delay") is not None:
        payload["scan_unit"] = _text(core.coord_units["delay"])
    return payload


def save_calibrated_source(path, core):
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix not in {".npz", ".mat"}:
        raise ValueError("完整数据体仅支持保存为 .npz 或 .mat 文件。")
    payload = build_calibrated_source_payload(core)
    if suffix == ".npz":
        with target.open("wb") as stream:
            np.savez(stream, **payload)
    else:
        savemat(target, payload)
