import numpy as np
import h5py
from scipy.io import loadmat
import os
import tkinter as tk
from tkinter import filedialog


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


# ===== 自动识别MAT版本 =====
def load_mat_auto(file_path):
    try:
        f = h5py.File(file_path, 'r')
        print("✅ MATLAB v7.3 (HDF5)")
        return "h5py", f
    except OSError:
        print("✅ MATLAB v7.2 或更旧")
        data = loadmat(file_path)
        return "loadmat", data


# ===== 自动找变量 =====
def find_key(keys, candidates):
    for name in candidates:
        if name in keys:
            return name
    return None


AXIS_MAPPING_VERSION = "mat_keyed_v2"
COORD_CANDIDATES = {
    "X": ["kx", "X", "x"],
    "Y": ["ky", "Y", "y"],
    "E": ["E", "energy", "En", "Ef"],
    "T": ["time", "delay", "t", "T"],
}


def infer_axis_roles(shape, coord_lengths, has_time_coord):
    """Infer source array dimensions from coordinate key names and lengths."""
    roles = {}
    remaining = list(range(len(shape)))

    def assign_unique(role):
        length = coord_lengths.get(role)
        if length is None:
            return
        matches = [idx for idx in remaining if shape[idx] == length]
        if len(matches) == 1:
            roles[role] = matches[0]
            remaining.remove(matches[0])

    def assign_first(role):
        length = coord_lengths.get(role)
        if length is None:
            return
        matches = [idx for idx in remaining if shape[idx] == length]
        if matches:
            roles[role] = matches[0]
            remaining.remove(matches[0])

    # T/E are anchors when their lengths uniquely identify a dimension.
    for role in ("T", "E"):
        assign_unique(role)

    # Equal-length momentum axes are assigned by coordinate key identity.
    for role in ("X", "Y"):
        assign_first(role)

    output_roles = ["X", "Y", "E"]
    if has_time_coord or len(shape) == 4:
        output_roles.append("T")

    fallback_roles = []
    if ("X" in roles or "Y" in roles) and "E" not in roles:
        fallback_roles.append("E")
    fallback_roles.extend(role for role in output_roles if role not in roles and role not in fallback_roles)

    for role in fallback_roles:
        if not remaining:
            break
        roles[role] = remaining.pop(0)

    if remaining:
        raise ValueError(f"❌ 无法识别多余维度: shape={shape}, remaining={remaining}, roles={roles}")

    return roles


def canonicalize_sample(sample, roles, has_time_coord):
    has_time_axis = "T" in roles or (has_time_coord and sample.ndim == 4)
    output_roles = ["X", "Y", "E"] + (["T"] if has_time_axis else [])
    present_roles = [role for role in output_roles if role in roles]
    present_axes = [roles[role] for role in present_roles]

    if len(set(present_axes)) != len(present_axes):
        raise ValueError(f"❌ 轴映射重复: {roles}")
    if set(present_axes) != set(range(sample.ndim)):
        raise ValueError(f"❌ 轴映射不完整: shape={sample.shape}, roles={roles}")

    canonical = sample.transpose(present_axes) if present_axes else sample
    for axis, role in enumerate(output_roles):
        if role not in roles:
            canonical = np.expand_dims(canonical, axis=axis)

    return canonical, output_roles


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
        kx_key = find_key(keys, COORD_CANDIDATES["X"])
        ky_key = find_key(keys, COORD_CANDIDATES["Y"])
        e_key = find_key(keys, COORD_CANDIDATES["E"])
        t_key = find_key(keys, COORD_CANDIDATES["T"])
        s_key  = find_key(keys, ['sample', 'binned', 'data'])

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

        sample = np.array(data[s_key]) if mode == "h5py" else np.array(data[s_key])
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

        roles = infer_axis_roles(sample.shape, coord_lengths, raw_time is not None)
        sample, axis_order = canonicalize_sample(sample, roles, raw_time is not None)
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
        sample = sample.astype(np.float32)

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
        if mode == "h5py":
            data.close()


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
