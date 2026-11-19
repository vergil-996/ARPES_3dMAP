import math
import os
import struct
import tkinter as tk

import numpy as np
import h5py
from scipy.io import loadmat
from tkinter import filedialog

from bandscope.core.axis_mapping import (
    AXIS_MAPPING_VERSION,
    MAT_COORD_CANDIDATES as COORD_CANDIDATES,
    canonicalize_array,
    find_first_key,
    infer_axis_roles,
    role_order_for_shape,
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
        if mode == "h5py":
            keys = list(data.keys())
        else:
            keys = [k for k in data.keys() if not k.startswith('__')]

        print("变量列表:", keys)

        # 自动匹配
        kx_key = find_first_key(keys, COORD_CANDIDATES["X"])
        ky_key = find_first_key(keys, COORD_CANDIDATES["Y"])
        e_key = find_first_key(keys, COORD_CANDIDATES["E"])
        t_key = find_first_key(keys, COORD_CANDIDATES["T"])
        s_key = find_first_key(keys, ("sample", "binned", "data"))

        if s_key is None:
            raise ValueError("❌ 未找到 sample/binned/data")

        # ===== 读取数据 =====
        def read_array(obj, key):
            if key is None:
                return None
            return np.array(obj[key]).squeeze().reshape(-1)

        kx = read_array(data, kx_key)
        ky = read_array(data, ky_key)
        energy = read_array(data, e_key)

        sample = np.asarray(data[s_key])
        print("原始 sample shape:", sample.shape)

        coord_lengths = {}
        for role, arr in (("X", kx), ("Y", ky), ("E", energy)):
            if arr is not None:
                coord_lengths[role] = int(arr.size)
        if t_key and t_key in keys:
            raw_time = read_array(data, t_key)
            coord_lengths["T"] = int(raw_time.size)
        else:
            raw_time = None

        if sample.ndim not in (2, 3, 4):
            raise ValueError(f"❌ 不支持维度: {sample.ndim}")

        axis_order = role_order_for_shape(sample.shape, raw_time is not None)
        roles = infer_axis_roles(sample.shape, coord_lengths, axis_order)
        sample = canonicalize_array(sample, roles, axis_order)
        print("轴映射:", roles)
        print("规范化 sample shape:", sample.shape)

        if "T" in axis_order:
            if raw_time is not None and raw_time.size == sample.shape[3]:
                time = raw_time
                print("✅ 使用原始 time")
            else:
                time = np.arange(sample.shape[3], dtype=np.float32)
                print("⚠️ time 与数据维度不匹配，按索引生成")
        else:
            time = np.array([0.0], dtype=np.float32)
            if raw_time is None:
                print("⚠️ 未检测到 time，按静谱处理")

        # ===== 类型优化 =====
        sample = sample.astype(np.float32, copy=False)

        # ===== 保存 =====
        save_dict = {
            "sample": sample,
            "time": time,
            "axis_order": np.array(axis_order),
            "axis_mapping_version": np.array([AXIS_MAPPING_VERSION]),
        }
        if kx is not None:
            save_dict["kx"] = kx
        if ky is not None:
            save_dict["ky"] = ky
        if energy is not None:
            save_dict["E"] = energy

        np.savez(dst, **save_dict)

        # ===== 输出信息 =====
        print("\n--- 转换完成 ---")
        if kx is not None:
            print("kx:", kx.shape)
        if ky is not None:
            print("ky:", ky.shape)
        if energy is not None:
            print("E :", energy.shape)
        print("time:", time.shape)
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
