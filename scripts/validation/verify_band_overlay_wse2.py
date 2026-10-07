# -*- coding: utf-8 -*-
"""能带面三维叠加的视觉验收：真实 WSe2 时间分辨 ARPES 数据（离屏）。

与合成版 ``verify_band_overlay.py`` 走同一条真实代码路径：宿主的
``AnalyzerCore.load_npz`` 读数据 → 取单帧体数据 → 插件的真实工作函数
``run_reconstruction``（预处理 → 逐带初始化/对齐 → L-BFGS-B 重构）→
``VolumeRenderSession`` + ``SurfaceOverlayManager`` 离屏截图 → 亮脊命中率/对比度
数值指标。不同点（真实数据没有真值面，本脚本也不伪造）：

- **数据**：``.local/data/WSe2_step.npz``，``sample`` 形状 [X=150, Y=150, E=200,
  T=25]；kx/ky 为物理坐标（Å⁻¹），**E 轴无物理坐标**（宿主按索引坐标 0–199，
  步长 1），时间轴 −300…1000 fs。
- **取帧**：默认取 t=0 帧（索引 4）及其相邻两帧（t=−50, 0, +50 fs）的**平均**
  ——单帧峰值计数只有几十，散粒噪声很重，三帧平均是最轻量的降噪，且 pump 诱导
  的变化主要在更高的能带/更晚时刻，三帧内价带色散基本不变。``--frame single``
  可退回只用 t=0 单帧。
- **能量窗口**：默认 E 索引 [90, 199]。全局 EDC（对 kx,ky 求和）在索引 90 以下
  基本为零，价带信号集中在 90–199（见输出的 ``00_edc_window.png``）；裁掉空白
  段让 MCLAHE 的对比度分配与优化边界都集中在有信号的区间。
- **指标**：体数据是真实计数数据，单个体素剖面的 argmax 容易被噪声尖峰抢走，
  所以指标在**轻度高斯平滑**（σ=(0.8, 0.8, 1.0) 体素，与预处理一致、不做
  CLAHE）后的体数据上计算；渲染截图用的仍是原始体数据。除 ±1 体素命中率外
  增加 ±2 体素命中率；并单独报告**信号区**指标（剖面峰值 ≥ 全局峰值 3% 的
  动量点）——探测器圆孔之外的角落没有数据，那里"命中率"没有定义。多带数据
  里一条 E 剖面常有多条亮脊而 argmax 只有一个，因此另报告**局部脊距**（到
  最近局部极大值的距离）与**带间距**诊断（带间无 deflation，需防塌并）。
- **eta 标定**：eta 单位 = E 索引。实测扫描（0.3 / 1 / 2 / 3 / 5）：eta≤1 时
  平滑项把面压得近乎平面、跟不上色散（信号区命中 ±1 仅 4–14%）；eta=3 时各带
  明显骑上亮脊且保持光滑（默认）；eta=5 指标再高一点但面开始抖、上带顶到窗口
  边界。合成验收里 eta=0.1 eV ≈ 5.6 个体素（能量步长 18 meV），与本数据集
  eta≈3 索引同一量级——**eta 要按体素刻度理解，不是越小越平滑**（恰恰相反：
  平滑项是 diff²/2η²，eta 越小平滑越强）。
- **判定**：没有真值面，命中率阈值是经验参考，脚本总是以 0 退出（除非流程本身
  失败），结论需人工核对截图（重点是 ``05_ek_slice_bands.png``）。

用法::

    python scripts/validation/verify_band_overlay_wse2.py
    python scripts/validation/verify_band_overlay_wse2.py --eta 0.3 --window 90 199
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pyvista as pv  # noqa: E402
from PIL import Image  # noqa: E402

from bandscope.core.analyzer_core import AnalyzerCore  # noqa: E402
from bandscope.extensions.api import AnalysisInput3D, CancelToken, read_only_array  # noqa: E402
from bandscope.processing.compute_backends import CpuComputeBackend  # noqa: E402
from bandscope.rendering.render_core import VolumeRenderSession  # noqa: E402
from bandscope.rendering.surface_overlay import (  # noqa: E402
    WORLD_SPAN,
    OverlaySurface,
    SurfaceOverlayManager,
    surface_world_geometry,
)
from plugins.band_reconstruct.preprocess import DEFAULT_SIGMA, gaussian_smooth  # noqa: E402
from plugins.band_reconstruct.worker import band_defaults, run_reconstruction  # noqa: E402

#: 三条价带的初始化参数（物理能量单位 = E 索引）。初值形状按探查切片估计：
#: 下带是明显的色散带（带底 ~105–115，向边缘抬升到 ~150），中带/上带色散较弱。
BAND_SETTINGS = (
    {"init": "parabolic", "e0": 112.0, "curvature": 35.0, "label": "Band 1 (lower)"},
    {"init": "parabolic", "e0": 143.0, "curvature": 12.0, "label": "Band 2 (middle)"},
    {"init": "parabolic", "e0": 176.0, "curvature": 12.0, "label": "Band 3 (upper)"},
)

#: 叠加层颜色：避开体数据 magma 色带的橙红段。
BAND_COLORS = ("#2ee66b", "#4dd2ff", "#ffd166")

#: 信号区阈值：剖面峰值 ≥ 全局峰值 × 该比例才参与"信号区"指标。
SIGNAL_FRACTION = 0.03


def load_frame(path: Path, frame: str):
    """走宿主真实加载路径，返回 ``(volume[X,Y,E], coords, delay, frame_desc)``。"""
    core = AnalyzerCore()
    ok, info = core.load_npz(path)
    if not ok:
        raise RuntimeError(f"宿主加载失败：{info}")
    delay = np.asarray(core.coords["delay"], dtype=np.float64).reshape(-1)
    i0 = int(np.argmin(np.abs(delay)))
    if frame == "avg3":
        lo, hi = max(0, i0 - 1), min(core.raw_data.shape[3], i0 + 2)
        volume = core.raw_data[..., lo:hi].mean(axis=3).astype(np.float32)
        desc = f"帧 {lo}…{hi - 1} 平均（t = {delay[lo]:.0f}…{delay[hi - 1]:.0f} fs，含 t=0）"
    else:
        volume = np.ascontiguousarray(core.raw_data[..., i0], dtype=np.float32)
        desc = f"单帧 {i0}（t = {delay[i0]:.0f} fs，最接近 t=0）"
    coords = {
        "X": np.asarray(core.coords["X"], dtype=np.float64).reshape(-1),
        "Y": np.asarray(core.coords["Y"], dtype=np.float64).reshape(-1),
        # 该数据集没有物理能量轴：与宿主一致，按索引坐标（步长 1）。
        "E": np.arange(volume.shape[2], dtype=np.float64),
    }
    return volume, coords, delay, desc


def downsample(volume, coords, stride: int):
    """动量两维按步长抽稀（能量维不动）；stride=1 时原样返回。"""
    if stride <= 1:
        return volume, coords
    sliced = volume[::stride, ::stride, :]
    new_coords = dict(coords)
    new_coords["X"] = coords["X"][::stride]
    new_coords["Y"] = coords["Y"][::stride]
    return np.ascontiguousarray(sliced), new_coords


def reconstruct(volume, coords, window, eta, maxiter, smooth, clahe):
    """调用插件的真实工作函数；返回 ``(AnalysisSurface2D, 预处理统计, 耗时)``。"""
    snap = AnalysisInput3D(
        plugin_id="band_reconstruct",
        page_id="validation-wse2",
        page_title="WSe2 tr-ARPES",
        snapshot_id="validation-wse2",
        data_generation=1,
        volume=read_only_array(volume, dtype=np.float32),
        x=read_only_array(coords["X"]),
        y=read_only_array(coords["Y"]),
        e=read_only_array(coords["E"]),
        x_label="kx",
        x_unit="1/Å",
        y_label="ky",
        y_unit="1/Å",
        e_label="E",
        e_unit="index",
    )
    e_window = coords["E"][(coords["E"] >= window[0]) & (coords["E"] <= window[1])]
    bands = []
    for index, preset in enumerate(BAND_SETTINGS):
        settings = band_defaults(index, e_window)
        settings.update(preset)
        settings["color"] = BAND_COLORS[index % len(BAND_COLORS)]
        bands.append(settings)
    started = time.perf_counter()
    result = run_reconstruction(
        snap,
        {
            "bands": bands,
            "eta": float(eta),
            "maxiter": int(maxiter),
            "smooth": bool(smooth),
            "clahe": bool(clahe),
            "energy_window": [float(window[0]), float(window[1])],
            "title": "WSe2 价带面",
        },
        CancelToken(),
    )
    elapsed = time.perf_counter() - started
    return result, elapsed


def ridge_metrics(volume, coords, band_z, *, rotation_angle=0.0, signal_fraction=SIGNAL_FRACTION):
    """命中率（±1 / ±2 体素）与对比度：与合成版同一几何换算。

    在传入的（轻度平滑后的）体数据上评估：面点落在该体素 E 剖面**全局**峰值
    ±1 / ±2 个体素内的比例，以及面点强度相对剖面峰值的平均比例。另给两个
    真实数据特有的口径：

    - **信号区**：只统计剖面峰值 ≥ 全局峰值 × ``signal_fraction`` 的点（探测器
      视场外的角落没有数据，命中率在那里没有定义）；
    - **局部脊距**：多带数据里一条 E 剖面常有两三条亮脊，而全局 argmax 只有
      一个——次亮的带按定义永远"脱靶"。因此补充"面点到**最近的**局部极大值
      （大于两侧邻居且 ≥ 阈值）的距离"，±1 / ±2 命中率与平均距离才是多带数据
      上"面是否骑在脊上"的公平度量；搜索半径 15 体素，找不到记为未命中。
    """
    shape = tuple(volume.shape)
    spacing = [WORLD_SPAN / (size - 1) for size in shape]
    surface = OverlaySurface(
        key="__metric__", label="metric", x=coords["X"], y=coords["Y"], z=band_z
    )
    points, _ = surface_world_geometry(
        surface, coords=coords, full_shape=shape, rotation_angle=rotation_angle
    )
    if points.shape[0] == 0:
        return {"points": 0}
    indices = np.rint(
        np.column_stack([points[:, axis] / spacing[axis] for axis in range(3)])
    ).astype(np.int64)
    for axis in range(3):
        np.clip(indices[:, axis], 0, shape[axis] - 1, out=indices[:, axis])

    rows, columns, energy_index = indices[:, 0], indices[:, 1], indices[:, 2]
    profiles = volume[rows, columns, :]
    peaks = np.argmax(profiles, axis=1)
    peak_values = profiles.max(axis=1)
    picked = profiles[np.arange(rows.size), energy_index]
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = np.where(peak_values > 0, picked / peak_values, np.nan)
    distance = np.abs(peaks - energy_index)
    threshold = float(signal_fraction) * float(volume.max())
    signal = peak_values >= threshold

    # 局部极大值掩码：严格大于右邻、不弱于左邻、且高于信号阈值；端点只看单侧邻居。
    n_energy = profiles.shape[1]
    local_max = np.zeros_like(profiles, dtype=bool)
    center = profiles[:, 1:-1]
    local_max[:, 1:-1] = (center >= profiles[:, :-2]) & (center > profiles[:, 2:])
    local_max[:, 0] = profiles[:, 0] > profiles[:, 1]
    local_max[:, -1] = profiles[:, -1] >= profiles[:, -2]
    local_max &= profiles >= threshold

    # 面点到最近局部脊的距离（向量化：逐个偏移查掩码）。
    max_dist = 15
    nearest = np.full(rows.size, max_dist + 1, dtype=np.int64)
    order = np.arange(rows.size)
    for shift in range(-max_dist, max_dist + 1):
        position = np.clip(energy_index + shift, 0, n_energy - 1)
        hit = local_max[order, position]
        nearest = np.where(hit, np.minimum(nearest, abs(shift)), nearest)

    def pack(mask):
        if not np.any(mask):
            return {"hit1": float("nan"), "hit2": float("nan"), "contrast": float("nan"),
                    "ridge1": float("nan"), "ridge2": float("nan"),
                    "ridge_mean": float("nan"), "points": 0}
        found = nearest[mask] <= max_dist
        return {
            "hit1": float(np.mean(distance[mask] <= 1)),
            "hit2": float(np.mean(distance[mask] <= 2)),
            "contrast": float(np.nanmean(ratios[mask])),
            "ridge1": float(np.mean(nearest[mask] <= 1)),
            "ridge2": float(np.mean(nearest[mask] <= 2)),
            "ridge_mean": float(np.mean(nearest[mask][found])) if np.any(found) else float("nan"),
            "ridge_missing": float(np.mean(~found)),
            "points": int(np.count_nonzero(mask)),
        }

    return {
        "all": pack(np.ones_like(signal, dtype=bool)),
        "signal": pack(signal),
        "signal_fraction_of_points": float(np.mean(signal)),
        "points": int(rows.size),
    }


def band_separation(surfaces):
    """逐对带面的 |Δz| 统计：带间无 deflation，靠得太近说明可能塌到同一条脊上。"""
    report = []
    for first in range(len(surfaces)):
        for second in range(first + 1, len(surfaces)):
            gap = np.abs(
                np.asarray(surfaces[first].z, dtype=np.float64)
                - np.asarray(surfaces[second].z, dtype=np.float64)
            )
            report.append(
                {
                    "pair": (surfaces[first].label, surfaces[second].label),
                    "mean": float(np.nanmean(gap)),
                    "min": float(np.nanmin(gap)),
                    "close_fraction": float(np.nanmean(gap <= 2.0)),
                }
            )
    return report


def rotated_volume(volume, angle):
    """按显示旋转把体数据转过去（与窗口渲染用的是同一个后端函数）。"""
    if abs(float(angle)) < 1e-9:
        return np.asarray(volume, dtype=np.float32)
    return CpuComputeBackend().rotate_volume(np.asarray(volume, dtype=np.float32), float(angle))


class Viewer:
    """离屏渲染体积 + 叠加层，并保存 PNG（与合成版同一套设置）。"""

    def __init__(self, width=620, height=520):
        self.plotter = pv.Plotter(off_screen=True, window_size=(width, height))
        self.plotter.set_background("white")
        self.session = VolumeRenderSession(self.plotter)
        self.overlay = SurfaceOverlayManager(self.plotter)

    def render(self, volume, overlays, path: Path, *, coords, rotation_angle=0.0):
        data = rotated_volume(volume, rotation_angle)
        self.session.render(data, (0, 45, 100), "linear", show_axes=True)
        if overlays:
            self.overlay.sync(
                overlays,
                coords=coords,
                full_shape=data.shape,
                rotation_angle=rotation_angle,
            )
        center = np.array([100.0, 100.0, 100.0])
        self.plotter.camera.position = (center[0] + 90.0, center[1] - 170.0, center[2] + 330.0)
        self.plotter.camera.focal_point = tuple(center)
        self.plotter.camera.up = (0.0, 0.0, 1.0)
        self.plotter.render()
        image = np.asarray(self.plotter.screenshot(None, return_img=True))
        Image.fromarray(image).save(path)
        return image

    def close(self):
        self.plotter.close()


def save_edc_plot(volume, coords, window, path: Path):
    """全局 EDC（对 kx,ky 求和）+ 所选能量窗口与初值位置，用于记录窗口选择。"""
    edc = volume.sum(axis=(0, 1))
    e_axis = coords["E"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(e_axis, edc / edc.max(), color="black", lw=1.2)
    ax.axvspan(window[0], window[1], color="#4dd2ff", alpha=0.18, label="energy window")
    for preset, color in zip(BAND_SETTINGS, BAND_COLORS):
        ax.axvline(preset["e0"], color=color, ls="--", lw=1.0,
                   label=f"init {preset['label']} e0={preset['e0']:.0f}")
    ax.set_xlabel("E (index, no physical axis in file)")
    ax.set_ylabel("summed intensity (normalized)")
    ax.set_title("Global EDC, t≈0 frame average")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def save_ek_slice_plot(metric_volume, coords, result, window, path: Path):
    """E-k 切片 + 重构能带曲线：能带是否压住色散亮脊的关键二维核对。"""
    x, y, e_axis = coords["X"], coords["Y"], coords["E"]
    icx = int(np.argmin(np.abs(x - np.mean(x))))
    icy = int(np.argmin(np.abs(y - np.mean(y))))
    panels = (
        (metric_volume[:, icy, :], x, f"E–kx slice @ ky={y[icy]:.2f} Å⁻¹", "kx", slice(None), icy),
        (metric_volume[icx, :, :], y, f"E–ky slice @ kx={x[icx]:.2f} Å⁻¹", "ky", icx, slice(None)),
    )
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for ax, (plane, axis, title, _label, ix, iy) in zip(axes, panels):
        ax.imshow(
            plane.T,
            origin="lower",
            aspect="auto",
            cmap="magma",
            extent=[axis[0], axis[-1], e_axis[0], e_axis[-1]],
        )
        for band, color in zip(result.surfaces, BAND_COLORS):
            z = np.asarray(band.z, dtype=np.float64)
            curve = z[:, iy] if isinstance(ix, slice) else z[ix, :]
            ax.plot(axis, curve, color=color, lw=1.6, label=band.label)
        ax.set_ylim(window[0] - 5, e_axis[-1])
        ax.set_title(title)
        ax.set_xlabel("k (1/Å)")
        ax.set_ylabel("E (index)")
        ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="能带面三维叠加视觉验收（WSe2 真实数据，离屏）")
    parser.add_argument("--data", default=str(REPO_ROOT / ".local" / "data" / "WSe2_step.npz"))
    parser.add_argument("--frame", choices=("avg3", "single"), default="avg3",
                        help="t=0 帧取法：avg3 = t=-50/0/+50 fs 三帧平均（默认），single = 只用 t=0 帧")
    parser.add_argument("--stride", type=int, default=1,
                        help="动量抽稀步长（1 = 150×150 全分辨率）")
    parser.add_argument("--window", type=float, nargs=2, default=(90.0, 199.0),
                        metavar=("E_LO", "E_HI"), help="能量窗口（E 索引坐标）")
    parser.add_argument("--eta", type=float, default=3.0, help="平滑先验强度（E 索引单位）")
    parser.add_argument("--maxiter", type=int, default=200)
    parser.add_argument("--rotation", type=float, default=30.0)
    parser.add_argument("--smooth", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--clahe", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--output-dir", default=str(REPO_ROOT / ".local" / "outputs" / "band_overlay_wse2")
    )
    args = parser.parse_args(argv)

    started_all = time.perf_counter()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    window = (float(min(args.window)), float(max(args.window)))
    volume_raw, coords, delay, frame_desc = load_frame(Path(args.data), args.frame)
    volume_raw, coords = downsample(volume_raw, coords, int(args.stride))
    print(f"数据 {Path(args.data).name}：体数据 {volume_raw.shape}，{frame_desc}")
    print(f"能量轴：索引坐标 0…{coords['E'][-1]:.0f}（文件无物理能量轴）；窗口 [{window[0]:.0f}, {window[1]:.0f}]")
    if int(args.stride) > 1:
        print(f"动量抽稀：stride={args.stride} → {volume_raw.shape[0]}×{volume_raw.shape[1]}")

    save_edc_plot(volume_raw, coords, window, output / "00_edc_window.png")

    result, reconstruct_seconds = reconstruct(
        volume_raw, coords, window, args.eta, args.maxiter, args.smooth, args.clahe
    )

    # 指标体数据：轻度高斯平滑（与预处理同参数、不做 CLAHE）；渲染仍用原始体数据。
    metric_volume = gaussian_smooth(volume_raw, DEFAULT_SIGMA)

    per_band = []
    for band in result.surfaces:
        per_band.append(
            ridge_metrics(metric_volume, coords, np.asarray(band.z, dtype=np.float64))
        )

    overlays = [
        OverlaySurface(
            key=f"band-{index + 1}",
            label=band.label,
            x=np.asarray(result.x, dtype=np.float64),
            y=np.asarray(result.y, dtype=np.float64),
            z=np.asarray(band.z, dtype=np.float64),
            color=BAND_COLORS[index % len(BAND_COLORS)],
            opacity=0.55,
        )
        for index, band in enumerate(result.surfaces)
    ]

    viewer = Viewer()
    try:
        plain = viewer.render(volume_raw, [], output / "01_volume_only.png", coords=coords)
        viewer.overlay.clear()
        with_overlay = viewer.render(
            volume_raw, overlays, output / "02_with_overlay.png", coords=coords
        )
        viewer.overlay.clear()
        viewer.render(
            volume_raw,
            overlays,
            output / f"03_rotated_{int(args.rotation)}deg.png",
            coords=coords,
            rotation_angle=float(args.rotation),
        )
        viewer.overlay.clear()
        viewer.render(
            volume_raw,
            [],
            output / f"04_rotated_{int(args.rotation)}deg_no_overlay.png",
            coords=coords,
            rotation_angle=float(args.rotation),
        )
    finally:
        viewer.close()

    save_ek_slice_plot(
        metric_volume, coords, result, window, output / "05_ek_slice_bands.png"
    )

    changed = float(np.abs(with_overlay.astype(int) - plain.astype(int)).mean())
    total_seconds = time.perf_counter() - started_all

    band_params = {item["label"]: item for item in result.params.get("bands", [])}
    print("\n================ 验收汇总（WSe2 真实数据） ================")
    print(f"数据帧      : {frame_desc}")
    print(f"能量窗口    : E 索引 [{window[0]:.0f}, {window[1]:.0f}]（全局 EDC 见 00_edc_window.png）")
    print(f"网格        : {volume_raw.shape[0]}×{volume_raw.shape[1]}（stride={args.stride}），"
          f"窗口内能量采样 {int(window[1] - window[0]) + 1}")
    print(f"预处理      : smooth={args.smooth} clahe={args.clahe}；eta={args.eta}（E 索引单位），"
          f"maxiter={args.maxiter}")
    print(f"重构总耗时  : {reconstruct_seconds:.1f} s（含预处理）；脚本总耗时 {total_seconds:.1f} s")
    for index, band in enumerate(result.surfaces):
        preset = BAND_SETTINGS[index]
        info = band_params.get(band.label, {})
        metrics = per_band[index]
        all_m, sig_m = metrics["all"], metrics["signal"]
        print(
            f"{band.label:<16}: init={preset['init']}(e0={preset['e0']:.0f}, "
            f"curvature={preset['curvature']:.0f})  "
            f"z∈[{np.nanmin(band.z):.1f}, {np.nanmax(band.z):.1f}]"
        )
        print(
            f"                优化: n_iter={info.get('n_iter', '?')} "
            f"loss={info.get('loss', float('nan')):.4f} "
            f"tail_delta={info.get('loss_tail_delta', float('nan')):.2e} "
            f"time={info.get('seconds', float('nan')):.1f}s"
        )
        print(
            f"                全部点   : 命中±1 {all_m['hit1'] * 100:5.1f}%  "
            f"命中±2 {all_m['hit2'] * 100:5.1f}%  对比度 {all_m['contrast'] * 100:5.1f}%"
        )
        print(
            f"                信号区   : 命中±1 {sig_m['hit1'] * 100:5.1f}%  "
            f"命中±2 {sig_m['hit2'] * 100:5.1f}%  对比度 {sig_m['contrast'] * 100:5.1f}%"
            f"（{sig_m['points']}/{metrics['points']} 点）"
        )
        print(
            f"                局部脊距 : ±1 {all_m['ridge1'] * 100:5.1f}%  "
            f"±2 {all_m['ridge2'] * 100:5.1f}%  平均 {all_m['ridge_mean']:.2f} 体素  "
            f"| 信号区 ±1 {sig_m['ridge1'] * 100:5.1f}%  ±2 {sig_m['ridge2'] * 100:5.1f}%"
        )
    for gap in band_separation(result.surfaces):
        print(
            f"带间距 {gap['pair'][0]} ~ {gap['pair'][1]}: 平均 {gap['mean']:.1f} 体素，"
            f"最小 {gap['min']:.1f}，≤2 体素的点占 {gap['close_fraction'] * 100:.1f}%"
        )
    print(f"叠加前后画面差异 = {changed:.2f}（像素平均差，0 表示没有任何叠加）")
    print("截图与图：")
    for name in (
        "00_edc_window.png",
        "01_volume_only.png",
        "02_with_overlay.png",
        f"03_rotated_{int(args.rotation)}deg.png",
        f"04_rotated_{int(args.rotation)}deg_no_overlay.png",
        "05_ek_slice_bands.png",
    ):
        print(f"  {output / name}")
    print("说明：真实数据无真值面，命中率/对比度为经验参考；结论请人工核对截图，"
          "重点看 05_ek_slice_bands.png 里能带曲线是否压在色散亮脊上。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
