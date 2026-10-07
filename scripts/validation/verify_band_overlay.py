# -*- coding: utf-8 -*-
"""能带面三维叠加的视觉验收：合成体数据 → 重构 → 离屏渲染截图 + 对齐数值检查。

流程刻意走真实代码路径：合成体数据 → 插件的 ``run_reconstruction``（真实工作
函数）→ ``VolumeRenderSession`` 渲染体数据 + ``SurfaceOverlayManager`` 叠加能带面
→ 离屏截图。旋转对照用真实后端 ``CpuComputeBackend.rotate_volume``，和窗口里
最终渲染走的是同一个函数。

除了截图，脚本还会算两个**数值**对齐指标（不依赖肉眼）：

- ``命中率``：能带面在每个 (kx, ky) 上的能量，落在该体素 E 方向剖面峰值 ±1 个
  体素内的比例；
- ``对比度``：能带面所在体素强度相对该剖面最大值的平均比例。

用法::

    python scripts/validation/verify_band_overlay.py
    python scripts/validation/verify_band_overlay.py --grid 64x64x200 --output-dir .local/outputs/band_overlay
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pyvista as pv  # noqa: E402
from PIL import Image  # noqa: E402

from bandscope.extensions.api import AnalysisInput3D, CancelToken, read_only_array  # noqa: E402
from bandscope.processing.compute_backends import CpuComputeBackend  # noqa: E402
from bandscope.rendering.render_core import VolumeRenderSession  # noqa: E402
from bandscope.rendering.surface_overlay import (  # noqa: E402
    WORLD_SPAN,
    OverlaySurface,
    SurfaceOverlayManager,
    surface_world_geometry,
)
from plugins.band_reconstruct import init_surface as IS  # noqa: E402
from plugins.band_reconstruct.synthetic import SyntheticBand, gaussian_edc_volume  # noqa: E402
from plugins.band_reconstruct.worker import band_defaults, run_reconstruction  # noqa: E402


def parse_grid(text: str):
    try:
        sizes = tuple(int(item) for item in str(text).lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("网格格式形如 48x48x120") from None
    if len(sizes) != 3 or any(size < 8 for size in sizes):
        raise argparse.ArgumentTypeError("网格需要三个 ≥8 的维度，形如 48x48x120")
    return sizes


def build_dataset(shape, *, step=0.018, sigma_e=0.03, seed=11):
    x = np.linspace(-1.0, 1.0, shape[0])
    y = np.linspace(-1.0, 1.0, shape[1])
    e = -0.5 + step * np.arange(shape[2])
    truth = IS.parabolic_surface(x, y, e0=0.05, a_x=0.16, a_y=0.16)
    dataset = gaussian_edc_volume(
        x,
        y,
        e,
        [SyntheticBand("Band 1", truth, 1.0)],
        sigma_e=sigma_e,
        background=0.01,
        noise="poisson",
        noise_level=5e3,
        seed=seed,
    )
    return dataset, truth


def reconstruct(dataset):
    snap = AnalysisInput3D(
        plugin_id="band_reconstruct",
        page_id="validation",
        page_title="合成体数据",
        snapshot_id="validation",
        data_generation=1,
        volume=read_only_array(dataset.volume, dtype=np.float32),
        x=read_only_array(dataset.x),
        y=read_only_array(dataset.y),
        e=read_only_array(dataset.e),
        x_label="kx",
        x_unit="1/Å",
        y_label="ky",
        y_unit="1/Å",
        e_label="E",
        e_unit="eV",
    )
    settings = band_defaults(0, dataset.e)
    settings.update({"e0": 0.02, "curvature": 0.4})
    surface = run_reconstruction(
        snap, {"bands": [settings], "eta": 0.1, "maxiter": 120}, CancelToken()
    )
    return snap, surface.surfaces[0]


def alignment_metrics(volume, coords, band_z, *, full_shape=None, rotation_angle=0.0):
    """命中率与对比度：能带面是否落在体数据的亮脊上。

    面放在**旋转后**的体数据里评估：先算出每个面点在体素索引上的位置，再看该
    体素 E 方向剖面里，面点是不是峰值（±1 体素）以及强度有多接近峰值。

    注意：非轴对齐旋转会用线性插值把亮脊抹宽，峰值位置因此可能移动 1–2 个体素。
    所以判断"叠加层对不对"要拿**真值面**做对照（见 ``main``）：真值面与重构面
    的命中率应当基本一致，剩下的差距来自体数据的插值模糊，不来自叠加几何。
    """
    shape = tuple(full_shape or volume.shape)
    spacing = [WORLD_SPAN / (size - 1) for size in shape]
    surface = OverlaySurface(
        key="__metric__", label="metric", x=coords["X"], y=coords["Y"], z=band_z
    )
    points, _ = surface_world_geometry(
        surface, coords=coords, full_shape=shape, rotation_angle=rotation_angle
    )
    if points.shape[0] == 0:
        return {"hit_rate": 0.0, "contrast": 0.0, "points": 0}
    indices = np.rint(
        np.column_stack(
            [points[:, axis] / spacing[axis] for axis in range(3)]
        )
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
    return {
        "hit_rate": float(np.mean(np.abs(peaks - energy_index) <= 1)),
        "contrast": float(np.nanmean(ratios)),
        "points": int(rows.size),
    }


def rotated_volume(volume, angle):
    """按显示旋转把体数据转过去（与窗口渲染用的是同一个后端函数）。"""
    if abs(float(angle)) < 1e-9:
        return np.asarray(volume, dtype=np.float32)
    return CpuComputeBackend().rotate_volume(np.asarray(volume, dtype=np.float32), float(angle))


class Viewer:
    """离屏渲染体积 + 叠加层，并保存 PNG。"""

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
        # 偏俯视的长焦视角：能带面近似水平，斜视角会把一两体素的高度差投影成很大的
        # 横向错位，肉眼反而看不出真实对齐。
        self.plotter.camera.position = (center[0] + 90.0, center[1] - 170.0, center[2] + 330.0)
        self.plotter.camera.focal_point = tuple(center)
        self.plotter.camera.up = (0.0, 0.0, 1.0)
        self.plotter.render()
        image = np.asarray(self.plotter.screenshot(None, return_img=True))
        Image.fromarray(image).save(path)
        return image

    def close(self):
        self.plotter.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="能带面三维叠加视觉验收（合成数据，离屏）")
    parser.add_argument("--grid", type=parse_grid, default=(48, 48, 120), help="体数据网格，如 48x48x120")
    parser.add_argument("--rotation", type=float, default=30.0, help="旋转对照的角度（度）")
    parser.add_argument(
        "--output-dir", default=str(REPO_ROOT / ".local" / "outputs" / "band_overlay")
    )
    args = parser.parse_args(argv)

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    dataset, truth = build_dataset(tuple(args.grid))
    coords = {"X": dataset.x, "Y": dataset.y, "E": dataset.e}
    _snapshot, band = reconstruct(dataset)
    error = float(np.sqrt(np.nanmean((band.z - truth) ** 2)))

    # 对齐指标：重构面 vs 真值面，在各旋转角下都算一遍。真值面是"体数据本身的
    # 上限"，两者差距小就说明叠加几何没有问题（剩下的差距属于插值模糊）。
    checks = []
    for angle in (0.0, float(args.rotation)):
        rotated = rotated_volume(dataset.volume, angle)
        checks.append(
            (
                angle,
                alignment_metrics(rotated, coords, band.z, rotation_angle=angle),
                alignment_metrics(rotated, coords, truth, rotation_angle=angle),
            )
        )
    metrics = checks[0][1]

    # 截图里用醒目的绿色而不是插件默认的橙色：体数据的 magma 色带本身就偏橙红，
    # 同色系看不出叠加面到底压在哪儿。
    overlay = OverlaySurface(
        key="band-1",
        label="Band 1",
        x=dataset.x,
        y=dataset.y,
        z=band.z,
        color="#2ee66b",
        opacity=0.55,
    )

    viewer = Viewer()
    try:
        plain = viewer.render(dataset.volume, [], output / "01_volume_only.png", coords=coords)
        viewer.overlay.clear()
        with_overlay = viewer.render(
            dataset.volume, [overlay], output / "02_with_overlay.png", coords=coords
        )
        viewer.overlay.clear()
        rotated = viewer.render(
            dataset.volume,
            [overlay],
            output / f"03_rotated_{int(args.rotation)}deg.png",
            coords=coords,
            rotation_angle=float(args.rotation),
        )
        viewer.overlay.clear()
        rotated_plain = viewer.render(
            dataset.volume,
            [],
            output / f"04_rotated_{int(args.rotation)}deg_no_overlay.png",
            coords=coords,
            rotation_angle=float(args.rotation),
        )
    finally:
        viewer.close()

    changed = float(np.abs(with_overlay.astype(int) - plain.astype(int)).mean())
    rotated_changed = float(np.abs(rotated.astype(int) - rotated_plain.astype(int)).mean())

    print(f"体数据 {dataset.shape}，能量间隔 {dataset.params['energy_step'] * 1000:.0f} meV")
    print(f"重构精度 η_avg = {error * 1000:.1f} meV（真值面为已知抛物面）")
    for angle, reconstructed, reference in checks:
        print(
            f"  旋转 {angle:5.1f}°：重构面 命中率 {reconstructed['hit_rate'] * 100:5.1f}% / "
            f"对比度 {reconstructed['contrast'] * 100:5.1f}%   "
            f"真值面对照 {reference['hit_rate'] * 100:5.1f}% / {reference['contrast'] * 100:5.1f}%"
        )
    print(f"叠加前后画面差异 = {changed:.2f}（像素平均差，0 表示没有任何叠加）")
    print(f"旋转 {args.rotation:.0f}° 后叠加差异 = {rotated_changed:.2f}")
    print(f"截图已写入 {output}")

    worst_gap = max(
        reference["hit_rate"] - reconstructed["hit_rate"]
        for _angle, reconstructed, reference in checks
    )
    ok = (
        metrics["hit_rate"] >= 0.95
        and metrics["contrast"] >= 0.9
        and changed > 0.2
        and worst_gap <= 0.1
    )
    print(
        "结论："
        + (
            "对齐检查通过（重构面与真值面的命中率差距 "
            f"{worst_gap * 100:.1f} 个百分点，仍需人工看一眼截图）"
            if ok
            else "对齐检查未通过，请看截图"
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
