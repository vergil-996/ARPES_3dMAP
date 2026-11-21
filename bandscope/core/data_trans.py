import math
import os
import struct
import tkinter as tk

import numpy as np
import h5py
from scipy.io import loadmat
from tkinter import filedialog

from bandscope.core.axis_mapping import AXIS_MAPPING_VERSION, MAT_COORD_CANDIDATES
from bandscope.core.axis_assignment import (
    ROLES,
    SCAN_UNKNOWN,
    infer_assignment,
    numeric_coordinate_candidates,
    scalar_text,
)


# ===== 文件选择 =====
def select_input_file():
    root = tk.Tk()
    root.withdraw()
    return filedialog.askopenfilename(
        title="选择 MATLAB 文件",
        filetypes=[("MATLAB files", "*.mat")]
    )


def select_output_file(default_name):
    root = tk.Tk()
    root.withdraw()
    return filedialog.asksaveasfilename(
        title="保存为 npz",
        defaultextension=".npz",
        initialfile=default_name,
        filetypes=[("NumPy files", "*.npz")]
    )


_MAT5_MI_MATRIX = 14
_MAT5_NUMERIC_DTYPES = {
    1: "i1",
    2: "u1",
    3: "i2",
    4: "u2",
    5: "i4",
    6: "u4",
    7: "f4",
    9: "f8",
    12: "i8",
    13: "u8",
}


class _MappedMatData(dict):
    def close(self):
        for value in self.values():
            mmap = getattr(value, "_mmap", None)
            if mmap is not None:
                mmap.close()


def _read_exact(stream, size):
    value = stream.read(size)
    if len(value) != size:
        raise ValueError("MAT 文件头不完整。")
    return value


def _read_v5_tag(stream, byte_order):
    raw = _read_exact(stream, 8)
    small_type, small_count = struct.unpack(byte_order + "HH", raw[:4])
    if small_type in _MAT5_NUMERIC_DTYPES and 0 < small_count <= 4:
        return small_type, small_count, raw[4:4 + small_count]
    data_type, byte_count = struct.unpack(byte_order + "II", raw)
    return data_type, byte_count, None


def _read_v5_payload(stream, byte_count, inline):
    if inline is not None:
        return inline
    if byte_count > 1024 * 1024:
        raise ValueError("MAT 矩阵元数据异常。")
    payload = _read_exact(stream, byte_count)
    _read_exact(stream, (-byte_count) % 8)
    return payload


def _load_oversized_v5_mat(file_path):
    with open(file_path, "rb") as stream:
        stream.seek(126)
        endian = _read_exact(stream, 2)
        if endian == b"IM":
            byte_order = "<"
        elif endian == b"MI":
            byte_order = ">"
        else:
            raise ValueError("不是 MATLAB v5 文件。")

        stream.seek(128)
        matrix_type, matrix_count, matrix_inline = _read_v5_tag(stream, byte_order)
        if matrix_type != _MAT5_MI_MATRIX or matrix_count != 0 or matrix_inline is not None:
            raise ValueError("不是需要回退读取的超大 MAT 矩阵。")

        flags_type, flags_count, flags_inline = _read_v5_tag(stream, byte_order)
        flags_payload = _read_v5_payload(stream, flags_count, flags_inline)
        if flags_type != 6 or len(flags_payload) != 8:
            raise ValueError("MAT 数组标志不受支持。")
        flags = struct.unpack(byte_order + "I", flags_payload[:4])[0]
        matrix_class = flags & 0xFF
        if matrix_class not in range(6, 16) or flags & 0x0800:
            raise ValueError("超大 MAT 回退读取仅支持非复数数值矩阵。")

        dims_type, dims_count, dims_inline = _read_v5_tag(stream, byte_order)
        dims_payload = _read_v5_payload(stream, dims_count, dims_inline)
        if dims_type not in (5, 6) or not dims_payload or len(dims_payload) % 4:
            raise ValueError("MAT 数组维度不受支持。")
        shape = tuple(struct.unpack(
            byte_order + str(len(dims_payload) // 4) + "i",
            dims_payload,
        ))
        if any(size <= 0 for size in shape):
            raise ValueError("MAT 数组维度必须为正数。")

        name_type, name_count, name_inline = _read_v5_tag(stream, byte_order)
        name_payload = _read_v5_payload(stream, name_count, name_inline)
        if name_type not in (1, 2) or not name_payload:
            raise ValueError("MAT 变量名不受支持。")
        name = name_payload.decode("latin1")

        data_type, stored_count, data_inline = _read_v5_tag(stream, byte_order)
        if data_type not in _MAT5_NUMERIC_DTYPES or data_inline is not None:
            raise ValueError("超大 MAT 回退读取仅支持未压缩数值数据。")
        dtype = np.dtype(_MAT5_NUMERIC_DTYPES[data_type]).newbyteorder(byte_order)
        data_offset = stream.tell()

    expected_count = math.prod(shape) * dtype.itemsize
    if expected_count <= 0xFFFFFFFF or stored_count != (expected_count & 0xFFFFFFFF):
        raise ValueError("MAT 数据长度与超大矩阵回退规则不匹配。")
    data_end = data_offset + expected_count
    file_size = os.path.getsize(file_path)
    if file_size not in (data_end, data_end + (-expected_count) % 8):
        raise ValueError("超大 MAT 回退读取仅支持文件末尾的单个矩阵。")

    mapped = np.memmap(
        file_path,
        dtype=dtype,
        mode="r",
        offset=data_offset,
        shape=shape,
        order="F",
    )
    return _MappedMatData({name: mapped})


# ===== 自动识别MAT版本 =====
def load_mat_auto(file_path):
    try:
        f = h5py.File(file_path, 'r')
        print("✅ MATLAB v7.3 (HDF5)")
        return "h5py", f
    except OSError:
        print("✅ MATLAB v7.2 或更旧")
        try:
            data = loadmat(file_path)
        except ValueError as exc:
            try:
                data = _load_oversized_v5_mat(file_path)
            except (OSError, ValueError, struct.error) as recovery_error:
                raise exc from recovery_error
            print("⚠️ 超大 MATLAB v5 长度字段已回绕，使用只读内存映射")
            return "mapped", data
        return "loadmat", data


# ===== 核心转换 =====
def convert(src, dst):
    mode, data = load_mat_auto(src)
    try:
        keys = [
            key for key in data.keys()
            if mode == "h5py" or not str(key).startswith("__")
        ]
        print("变量列表:", keys)
        main_key = next(
            (key for key in ("sample", "binned", "data") if key in keys),
            None,
        )
        if main_key is None:
            raise ValueError("❌ 未找到 sample/binned/data")

        sample = np.asarray(data[main_key])
        if sample.ndim not in (3, 4):
            raise ValueError(f"❌ 不支持维度: {sample.ndim}")
        variables = {key: data[key] for key in keys if key != main_key}
        assignment = infer_assignment(
            main_key,
            sample,
            variables,
            search_map=MAT_COORD_CANDIDATES,
        )
        candidates = numeric_coordinate_candidates(variables)
        sample = sample.astype(np.float32, copy=False)
        print("原始 sample shape:", sample.shape)
        print("轴映射:", assignment.axis_roles)

        save_dict = {
            "sample": sample,
            "axis_order": np.asarray([
                next(
                    role for role, axis in assignment.axis_roles.items()
                    if axis == original_axis
                )
                for original_axis in range(sample.ndim)
            ]),
            "axis_mapping_version": np.asarray([AXIS_MAPPING_VERSION]),
            "axis_coordinate_keys": np.asarray([
                assignment.coord_keys.get(role) or "" for role in ROLES
            ]),
            "scan_type": np.asarray([assignment.scan_kind]),
            "scan_name": np.asarray([assignment.display_scan_name]),
        }
        reserved = set(save_dict)
        for key, values in candidates.items():
            if key not in reserved:
                save_dict[key] = np.asarray(values).reshape(-1)
        for key in keys:
            lower = str(key).lower()
            if key in save_dict or not (
                lower.endswith("_unit") or lower.endswith("_units") or lower.endswith("unit")
            ):
                continue
            text = scalar_text(variables.get(key))
            if text:
                save_dict[key] = np.asarray([text])
        for key in ("axis_types", "scan_type", "scan_name"):
            if key in variables and key not in {"scan_type", "scan_name"}:
                save_dict[key] = np.asarray(variables[key])
        if assignment.is_time:
            time_key = assignment.coord_keys.get("T")
            if time_key in candidates:
                save_dict["time"] = np.asarray(candidates[time_key]).reshape(-1)
            else:
                save_dict["time"] = np.arange(assignment.axis_length("T"), dtype=np.float32)
        elif "T" not in assignment.role_order:
            save_dict["time"] = np.asarray([0.0], dtype=np.float32)

        np.savez(dst, **save_dict)
        print("\n--- 转换完成 ---")
        print("坐标变量:", assignment.coord_keys)
        print("扫描类型:", assignment.scan_kind or SCAN_UNKNOWN)
        print("sample:", sample.shape)
        print("✅ 保存路径:", dst)
    finally:
        close = getattr(data, "close", None)
        if close is not None:
            close()


# ===== 主程序 =====
if __name__ == "__main__":
    print("=== MAT → NPZ 转换工具 ===")

    src = select_input_file()
    if not src:
        print("❌ 未选择文件")
        exit()

    default_name = os.path.splitext(os.path.basename(src))[0] + ".npz"
    dst = select_output_file(default_name)

    if not dst:
        print("❌ 未选择保存路径")
        exit()

    convert(src, dst)
