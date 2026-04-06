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
        kx_key = find_key(keys, ['kx', 'X'])
        ky_key = find_key(keys, ['ky', 'Y'])
        e_key  = find_key(keys, ['E', 'energy'])
        t_key  = find_key(keys, ['time', 't'])
        s_key  = find_key(keys, ['sample', 'binned', 'data'])

        if s_key is None:
            raise ValueError("❌ 未找到 sample/binned/data")

        # ===== 读取数据 =====
        def read_array(obj, key):
            if key is None:
                return None
            return np.array(obj[key]).squeeze()

        kx = read_array(data, kx_key)
        ky = read_array(data, ky_key)
        energy = read_array(data, e_key)

        if mode == "h5py":
            sample = np.array(data[s_key])
        else:
            sample = data[s_key]

        print("原始 sample shape:", sample.shape)

        # ===== 处理 time / 静谱 =====
        if t_key and t_key in keys:
            time = read_array(data, t_key)
            print("✅ 使用原始 time")
        else:
            print("⚠️ 未检测到 time，按静谱处理")
            time = np.array([0.0], dtype=np.float32)

            if sample.ndim == 3:
                pass
            elif sample.ndim == 2:
                sample = sample[:, :, np.newaxis]
            else:
                raise ValueError(f"❌ 不支持维度: {sample.ndim}")

        # ===== 类型优化 =====
        sample = sample.astype(np.float32)

        # ===== 保存 =====
        save_dict = {
            "sample": sample,
            "time": time,
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
