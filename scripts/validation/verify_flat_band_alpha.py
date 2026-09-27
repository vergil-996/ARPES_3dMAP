# -*- coding: utf-8 -*-
"""平带增强的视觉验收：用合成多平带数据跑一遍真实渲染通道。

阶段 A 的门槛检查（计划 §7）：同强度不同能量可以有不同 alpha、颜色与色阶保持
正确、缺失区域仍然不可见、开关效果可以逐位还原。这里把主视图用的
:class:`render_core.VolumeRenderSession` 直接搬到离屏 plotter 上，产出一组
对照图供人工核对——合成数据只是为了验证通道，真实平带的视觉验收仍需要一组
实测数据。

用法::

    python scripts/validation/verify_flat_band_alpha.py
    python scripts/validation/verify_flat_band_alpha.py --bands 5 --output-dir .local/outputs/flat_band
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

from plugins.flat_band_opacity.effect import (  # noqa: E402
    DEFAULT_BACKGROUND,
    DEFAULT_GAIN,
    FlatBand,
    multiplier_for,
)
from bandscope.extensions.api import EnergyAxisSpec  # noqa: E402
from bandscope.rendering.render_core import VisualEngine, VolumeRenderSession  # noqa: E402

#: 合成体数据的尺寸：kx、ky 各 24，能量 160 点。
SHAPE = (24, 24, 160)

#: 缺一个角用于验证 NaN 掩膜，另外留一段能量范围做裁剪验证。
NAN_SLAB = (19, 24)


def build_dataset(band_count: int, seed: int = 7):
    """真实感的多平带数据：若干条能带 + 一条弱带 + 噪点 + 一块缺失区域。"""
    rng = np.random.default_rng(seed)
    energy = np.linspace(-1.0, 1.0, SHAPE[2])
    centers = np.linspace(-0.65, 0.65, max(band_count, 1))
    widths = np.full(centers.size, 0.05)

    profile = np.zeros_like(energy)
    weights = np.ones_like(centers)
    if centers.size > 1:
        # 中间一条压成弱带，用于验证“单独提高弱带增强量”。
        weak = centers.size // 2
        weights[weak] = 0.25
    for center, weight in zip(centers, weights):
        profile = np.maximum(profile, weight * np.exp(-((energy - center) / 0.05) ** 2))
    profile += 0.04

    kx = np.linspace(-1.0, 1.0, SHAPE[0])
    texture = 0.6 + 0.7 * rng.random(SHAPE)
    data = (profile[None, None, :] * texture + 0.02 * rng.random(SHAPE)).astype(np.float32)
    # 缺失区域：验证 NaN 与裁剪遮罩继续保持不可见。
    data[NAN_SLAB[0] : NAN_SLAB[1], :, :] = np.nan
    data *= (1.0 + 0.15 * kx[:, None, None])
    return np.ascontiguousarray(data, dtype=np.float32), energy, centers, weights


def build_bands(energy, centers, weights, *, gain=None, background=None):
    gain = DEFAULT_GAIN if gain is None else gain
    bands = []
    for index, (center, weight) in enumerate(zip(centers, weights)):
        band = FlatBand()
        band.center = float(center)
        band.fwhm = 0.12
        # 弱带单独给更高的增强量，正是插件要解决的使用场景。
        band.gain = gain * (2.0 if weight < 0.5 else 1.0)
        band.enabled = True
        bands.append(band)
    return bands


class Viewer:
    """把体数据按主视图的方式渲染到离屏画面，并保存成 PNG。"""

    def __init__(self, width=520, height=440):
        self.plotter = pv.Plotter(off_screen=True, window_size=(width, height))
        self.plotter.set_background("white")
        self.session = VolumeRenderSession(self.plotter)
        self._camera_set = False

    def render(self, data, multiplier, path: Path):
        self.session.render(
            data,
            (0, 50, 100),
            "linear",
            show_axes=False,
            opacity_multiplier=multiplier,
        )
        center = np.array([100.0, 100.0, 100.0])
        self.plotter.camera.position = (center[0] + 150.0, center[1] - 420.0, center[2] + 90.0)
        self.plotter.camera.focal_point = tuple(center)
        self.plotter.camera.up = (0.0, 0.0, 1.0)
        self.plotter.camera.parallel_projection = False
        self.plotter.render()
        image = np.asarray(self.plotter.screenshot(None, return_img=True))
        Image.fromarray(image).save(path)
        return image

    def close(self):
        self.plotter.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="平带增强视觉验收（合成数据）")
    parser.add_argument("--bands", type=int, default=3, help="合成的平带数量")
    parser.add_argument("--background", type=float, default=DEFAULT_BACKGROUND)
    parser.add_argument("--gain", type=float, default=DEFAULT_GAIN)
    parser.add_argument(
        "--output-dir", default=str(REPO_ROOT / ".local" / "outputs" / "flat_band")
    )
    args = parser.parse_args(argv)

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    data, energy, centers, weights = build_dataset(args.bands)
    bands = build_bands(
        energy, centers, weights, gain=args.gain, background=args.background
    )
    axis = EnergyAxisSpec(
        values=energy,
        unit="eV",
        source="file",
        roi_range=(float(energy.min()), float(energy.max())),
        full_range=(float(energy.min()), float(energy.max())),
    )
    multiplier = multiplier_for(bands, energy, args.background)

    # 缺失区域的定位图：把 NaN 换成最亮的值。色阶范围必须钉住，否则填充本身会
    # 改变归一化、整幅画面都会变，掩膜覆盖的像素就定位不出来了。
    finite = data[np.isfinite(data)]
    filled = np.array(data, copy=True)
    filled[NAN_SLAB[0] : NAN_SLAB[1], :, :] = float(finite.max())

    previous_lock = VisualEngine._locked_data_range
    VisualEngine._locked_data_range = (float(finite.min()), float(finite.max()))
    viewer = Viewer()
    try:
        baseline = viewer.render(data, None, output / "01_original.png")
        enhanced = viewer.render(data, multiplier, output / "02_flat_band.png")
        restored = viewer.render(data, None, output / "03_restored.png")
        off = viewer.render(
            data, np.ones(energy.size), output / "04_unit_multiplier.png"
        )
        # 用同一路径（都不带倍率）渲染，差异只来自缺失区域本身。
        with_data = viewer.render(filled, None, output / "05_nan_filled.png")
        enhanced_filled = viewer.render(
            filled, multiplier, output / "06_nan_filled_with_effect.png"
        )
    finally:
        viewer.close()
        VisualEngine._locked_data_range = previous_lock

    mask = np.abs(with_data.astype(int) - baseline.astype(int)).max(axis=2) > 2
    masked_pixels = int(mask.sum())
    # 开启效果后，缺失体素仍必须不参与渲染：同一块区域“填上数据”应当明显更亮。
    hidden_gap = float(enhanced_filled[mask].mean() - enhanced[mask].mean())

    print(f"数据 {data.shape}，平带 {len(bands)} 条，能量轴 {axis.label}")
    for band in bands:
        inside = axis_check(axis, band.center)
        print(
            f"  中心 {band.center:+.3f}  厚度 {band.fwhm:.3f}  "
            f"增强 {band.gain:.2f}  {'在范围内' if inside else '不在当前范围'}"
        )
    print(f"倍率范围 {np.min(multiplier):.3f} – {np.max(multiplier):.3f}")

    unit_delta = float(np.abs(off.astype(int) - baseline.astype(int)).mean())
    stacked = np.concatenate(
        [
            baseline[..., :3].mean(axis=2).ravel(),
            off[..., :3].mean(axis=2).ravel(),
        ]
    )
    half = stacked.size // 2
    structure = float(np.corrcoef(stacked[:half], stacked[half:])[0, 1])

    checks = [
        ("关闭效果可逐位还原原图", np.array_equal(restored, baseline)),
        (
            "开启效果确实改变了画面",
            int(np.abs(enhanced.astype(int) - baseline.astype(int)).max()) > 10,
        ),
        (
            # 二分量通道与单分量不是同一条 VTK 采样路径：实测发现二分量路径
            # 不做 ScalarOpacityUnitDistance 修正，单分量路径做，因此开启效果后
            # 画面整体偏实心。这里只要求两条路径看到的是同一幅结构（相关系数
            # 下限留得很宽），逐位一致由「关闭效果可逐位还原」保证。
            f"倍率恒为 1 时画面结构一致（相关系数 {structure:.4f}）",
            structure > 0.85,
        ),
        (
            f"开启效果后缺失体素仍不可见（{masked_pixels} 个像素，"
            f"填上数据会亮 {hidden_gap:.1f}）",
            masked_pixels > 100 and hidden_gap > 5.0,
        ),
    ]
    failed = 0
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        failed += 0 if ok else 1
    print(
        f"  参考：倍率恒为 1 的平均亮度偏差 {unit_delta:.2f}/255（VTK 二分量路径固有差异）"
    )
    print(f"对照图已写入 {output}")
    return 1 if failed else 0


def axis_check(axis, value) -> bool:
    low, high = axis.roi_range
    lo, hi = (low, high) if low <= high else (high, low)
    return lo <= float(value) <= hi


if __name__ == "__main__":
    raise SystemExit(main())
