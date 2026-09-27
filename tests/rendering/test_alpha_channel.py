"""阶段 A：独立透明度渲染通道。

验证宿主确实能让“相同强度、不同能量”的体素获得不同不透明度，同时颜色、
NaN/裁剪遮罩和原图开关都不受影响。这些检查是平带插件其余部分的技术前提：
如果这条通道不成立，插件只能退化成改写强度数组，那会破坏颜色与数据导出。
"""
import unittest

import numpy as np
import pyvista as pv
from vtkmodules.util.numpy_support import vtk_to_numpy

from bandscope.rendering.render_core import (
    VisualEngine,
    VolumeAlphaChannel,
    VolumeRenderSession,
    normalize_opacity_multiplier,
    opacity_ramp_points,
)

# 合成的体数据很小，但三轴都 > 1，体积渲染才有可采样厚度。
SHAPE = (4, 4, 32)
Z_VOXEL_TARGET_PX = 8.0


def energy_axis(count=SHAPE[2]):
    """-1.0 ~ 1.0 eV 的能量轴，用于把索引换算成用户看到的坐标。"""
    return np.linspace(-1.0, 1.0, count)


def gaussian_multiplier(axis, centers, fwhm, background, gain):
    """计划 §3 的倍率：m(E) = b + max_i a_i * G_i(E)。"""
    multiplier = np.full(np.shape(axis), float(background), dtype=np.float64)
    for center in centers:
        gauss = np.exp(
            -4.0 * np.log(2.0) * ((axis - float(center)) / float(fwhm)) ** 2
        )
        multiplier = np.maximum(multiplier, background + gain * gauss)
    return multiplier


class ProjectedVolume:
    """沿 -Y 的平行投影视图，附带世界坐标 ↔ 屏幕像素的换算。

    体渲染按像素检查必须有确定的坐标映射，否则“亮/暗”断言只是在碰运气。
    """

    def __init__(self, plotter, session, energy_count):
        self.plotter = plotter
        self.session = session
        self.energy_count = max(int(energy_count), 2)
        bounds = session.grid.bounds
        self.x0, self.x1, self.y0, self.y1, self.z0, self.z1 = bounds
        self.cx = (self.x0 + self.x1) / 2.0
        self.cy = (self.y0 + self.y1) / 2.0
        self.cz = (self.z0 + self.z1) / 2.0
        self.z_voxel = (self.z1 - self.z0) / (self.energy_count - 1)

    #: 抓图时固定使用的投影尺度，读图时不能再去问相机（相机随时可能被渲染改回）。
    scale = None
    height_px = 0
    width_px = 0

    def grab(self):
        plotter = self.plotter
        height_px = max(int(plotter.window_size[1]), 1)
        z_span = max(self.z1 - self.z0, 1e-9)
        # 让一个能量体素稳定落在若干像素上，剖面检查才有足够分辨率。
        self.scale = height_px * self.z_voxel / (2.0 * Z_VOXEL_TARGET_PX)
        plotter.camera.parallel_scale = self.scale
        plotter.camera.position = (self.cx, self.y0 - z_span * 3.0, self.cz)
        plotter.camera.focal_point = (self.cx, self.cy, self.cz)
        plotter.camera.up = (0, 0, 1)
        plotter.camera.parallel_projection = True
        plotter.render()
        image = np.asarray(plotter.screenshot(None, return_img=True))
        self.height_px = image.shape[0]
        self.width_px = image.shape[1]
        return image

    def row_of(self, world_z):
        fraction = (world_z - self.cz) / (2.0 * self.scale)
        return int(round(self.height_px / 2.0 - fraction * self.height_px))

    def column_of(self, world_x):
        aspect = self.width_px / max(self.height_px, 1)
        fraction = (world_x - self.cx) / (2.0 * self.scale * aspect)
        return int(round(self.width_px / 2.0 + fraction * self.width_px))

    def energy_profile(self, image, energy_values):
        """中心列亮度按能量坐标重采样（能量轴线性铺满体积的 z 范围）。

        取的是每个能量体素的中心位置，避免首尾采样落在体积边界面上。
        屏幕行号自上而下，这里翻转成 z 递增后再插值。
        """
        energies = np.asarray(energy_values, dtype=np.float64)
        count = int(energies.size)
        column = image[..., :3].mean(axis=2)[:, self.width_px // 2][::-1]
        rows = np.arange(self.height_px)
        world_z = (
            self.cz
            + (self.height_px / 2.0 - rows - 0.5) / self.height_px * (2.0 * self.scale)
        )[::-1]
        span = float(energies[-1] - energies[0])
        if abs(span) < 1e-12:
            span = 1.0
        fractions = (energies - energies[0]) / span
        target_z = self.z0 + (0.5 + fractions * (count - 1)) / count * (self.z1 - self.z0)
        return np.interp(target_z, world_z, column)


def make_session(plotter, data, **kwargs):
    session = VolumeRenderSession(plotter)
    session.render(data, (0, 50, 100), "linear", show_axes=False, **kwargs)
    return session


def unpack(channel):
    """(N, 2) 交错数组 → (intensity, alpha) 两个 (nx, ny, ne) 数组。"""
    values = vtk_to_numpy(channel.vtk_array)
    shape = channel.grid.dimensions
    return (
        values[:, 0].reshape(shape, order="F"),
        values[:, 1].reshape(shape, order="F"),
    )


class MultiplierValidationTests(unittest.TestCase):
    def test_normalizes_negative_and_non_finite_values(self):
        values = normalize_opacity_multiplier([np.nan, -3.0, 2.0, np.inf], 4)
        np.testing.assert_allclose(values, [0.0, 0.0, 2.0, 0.0])

    def test_length_must_match_energy_axis(self):
        with self.assertRaises(ValueError):
            normalize_opacity_multiplier([1.0, 2.0], 3)

    def test_none_means_no_effect(self):
        self.assertIsNone(normalize_opacity_multiplier(None, 5))


class AlphaMathTests(unittest.TestCase):
    """alpha = clip(alpha_base(I) * m(E), 0, 1)，逐体素对齐能量倍率。"""

    LEVEL_INFO = None

    def setUp(self):
        self.level_info = VisualEngine._level_info(
            np.array([0.0, 1.0]), (0, 50, 100)
        )

    def _channel(self, intensity, finite=None):
        plotter = pv.Plotter(off_screen=True, window_size=(32, 32))
        self.addCleanup(plotter.close)
        session = make_session(plotter, np.zeros(SHAPE, dtype=np.float32))
        session.level_info = self.level_info
        channel = VolumeAlphaChannel(session.volume, session.grid, intensity)
        if finite is not None:
            channel.finite_flat = finite
        return channel

    def test_alpha_is_the_product_of_base_opacity_and_energy_multiplier(self):
        intensity = np.full(SHAPE, 0.5, dtype=np.float32)
        channel = self._channel(intensity)
        multiplier = gaussian_multiplier(
            energy_axis(), centers=[0.0], fwhm=0.4, background=0.2, gain=0.8
        )
        channel.apply(multiplier, "linear", self.level_info, ("test",))
        _, alpha = unpack(channel)

        ramp_x, ramp_y = opacity_ramp_points("linear", self.level_info)
        base = np.interp(0.5, ramp_x, ramp_y)
        expected = np.clip(base * multiplier, 0.0, 1.0)
        for index in range(SHAPE[2]):
            np.testing.assert_allclose(
                alpha[:, :, index], expected[index], rtol=1e-6, atol=1e-6
            )

    def test_intensity_component_is_bit_identical_to_the_source(self):
        rng = np.random.default_rng(7)
        # 传三维 Fortran 序数组：VTK 点序是 x 最快，这里最容易写错成 C 序展开。
        intensity = np.asfortranarray(rng.random(SHAPE).astype(np.float32))
        channel = self._channel(intensity)
        channel.apply(np.ones(SHAPE[2]), "linear", self.level_info, ("test",))
        packed_intensity, _ = unpack(channel)
        np.testing.assert_array_equal(packed_intensity, intensity)

    def test_component_order_survives_a_non_symmetric_shape(self):
        shape = (3, 2, 5)
        rng = np.random.default_rng(3)
        intensity = np.asfortranarray(rng.random(shape).astype(np.float32))
        plotter = pv.Plotter(off_screen=True, window_size=(32, 32))
        self.addCleanup(plotter.close)
        session = make_session(plotter, intensity)
        session.level_info = self.level_info
        channel = VolumeAlphaChannel(session.volume, session.grid, intensity)
        multiplier = np.arange(shape[2], dtype=np.float64) / shape[2]
        channel.apply(multiplier, "linear", self.level_info, ("test",))
        packed_intensity, alpha = unpack(channel)
        np.testing.assert_array_equal(packed_intensity, intensity)
        ramp_x, ramp_y = opacity_ramp_points("linear", self.level_info)
        base = np.interp(intensity, ramp_x, ramp_y)
        expected = np.clip(base * multiplier[None, None, :], 0.0, 1.0)
        np.testing.assert_allclose(alpha, expected, rtol=1e-6, atol=1e-6)

    def test_non_finite_voxels_are_forced_invisible(self):
        intensity = np.full(SHAPE, 0.9, dtype=np.float32)
        finite = np.ones(SHAPE, dtype=bool)
        finite[1, 2, 3] = False
        channel = self._channel(intensity.ravel(order="F"), finite.ravel(order="F"))
        channel.apply(np.full(SHAPE[2], 5.0), "linear", self.level_info, ("test",))
        _, alpha = unpack(channel)
        self.assertEqual(float(alpha[1, 2, 3]), 0.0)
        self.assertGreater(float(alpha[0, 0, 0]), 0.0)

    def test_alpha_is_clipped_to_unit_range(self):
        intensity = np.full(SHAPE, 1.0, dtype=np.float32)
        channel = self._channel(intensity.ravel(order="F"))
        channel.apply(np.full(SHAPE[2], 5.0), "linear", self.level_info, ("test",))
        _, alpha = unpack(channel)
        self.assertEqual(float(alpha.max()), 1.0)
        self.assertGreaterEqual(float(alpha.min()), 0.0)

    def test_reapplying_the_same_parameters_is_a_no_op(self):
        intensity = np.full(SHAPE, 0.5, dtype=np.float32)
        channel = self._channel(intensity.ravel(order="F"))
        multiplier = np.linspace(0.0, 2.0, SHAPE[2])
        channel.apply(multiplier, "linear", self.level_info, ("a",))
        _, first = unpack(channel)
        channel.apply(multiplier.copy(), "linear", self.level_info, ("a",))
        _, second = unpack(channel)
        np.testing.assert_array_equal(first, second)


class MaskUpdateTests(unittest.TestCase):
    """同一形状、不同帧的缺失掩膜必须跟着数据一起换。

    逐帧变化的接收角掩膜会让某些体素这一帧有值、下一帧又没有。若沿用上一帧的
    掩膜，新帧里已经有效的体素会被继续压成透明，表现成“时间轴拖动时有个洞跟着
    上一帧走”。
    """

    SHAPE = (3, 3, 6)

    @staticmethod
    def _frame(missing_flat_index):
        """按 VTK 点序（x 最快）挖掉一个体素。

        强度沿 kx 变化：强度恒定时数据范围为零、色阶落到最低点，基础不透明度
        本来就是 0，测不出掩膜是否生效。
        """
        shape = MaskUpdateTests.SHAPE
        ramp = np.linspace(0.3, 0.6, shape[0], dtype=np.float32)
        frame = np.broadcast_to(ramp[:, None, None], shape).copy()
        frame[np.unravel_index(missing_flat_index, shape, order="F")] = np.nan
        return frame

    def test_new_frame_mask_replaces_the_previous_one(self):
        plotter = pv.Plotter(off_screen=True, window_size=(32, 32))
        self.addCleanup(plotter.close)
        session = VolumeRenderSession(plotter)

        # 效果始终开着（时间轴拖动时的真实情形）：通道不会因为倍率变 None 被
        # 丢掉重建，掩膜只能靠 _attach_data 一起更新。
        ones = np.ones(self.SHAPE[2])
        first = self._frame(13)
        session.render(
            first, (0, 50, 100), "linear", show_axes=False, opacity_multiplier=ones
        )
        _, alpha = unpack(session.alpha_channel)
        self.assertEqual(float(alpha.ravel(order="F")[13]), 0.0)

        second = self._frame(26)
        session.render(
            second, (0, 50, 100), "linear", show_axes=False, opacity_multiplier=ones
        )
        _, alpha = unpack(session.alpha_channel)
        flat = alpha.ravel(order="F")
        # 新帧里 13 已经有效、26 才是缺失；沿用旧掩膜会让这两条同时错。
        self.assertGreater(float(flat[13]), 0.0)
        self.assertEqual(float(flat[26]), 0.0)


class RenderChannelTests(unittest.TestCase):
    """离屏渲染：同强度不同能量必须得到不同不透明度与相同颜色。

    倍率按计划建议的 20% / 0.8 比例整体缩小：体渲染的 alpha 沿视线累积，而宿主
    的体数据在世界上固定占据 200 个单位，默认量级的 alpha 会让画面直接饱和，
    测不出任何差异。缩小倍率只是把同一条通道放进可测量的线性区间，比例关系、
    颜色和通道接线都与实际使用一致。
    """

    INTENSITY = 0.75
    BAND = 0.0
    FWHM = 0.35
    MULTIPLIER_SCALE = 1.0 / 80.0
    BACKGROUND = 0.2 * MULTIPLIER_SCALE
    GAIN = 0.8 * MULTIPLIER_SCALE

    def setUp(self):
        self.plotter = pv.Plotter(off_screen=True, window_size=(120, 260))
        self.plotter.set_background("black")
        # 强度只沿 kx 变化：色阶有真实动态范围，而同一条能量扫描线上强度处处
        # 相同，才能只比较“同一强度在不同能量处的透明度”。
        ramp = np.linspace(0.25, 0.75, SHAPE[0], dtype=np.float32)
        self.data = np.broadcast_to(ramp[:, None, None], SHAPE).copy()
        self.axis = energy_axis()
        self.multiplier = gaussian_multiplier(
            self.axis, [self.BAND], self.FWHM, self.BACKGROUND, self.GAIN
        )
        self.session = make_session(self.plotter, self.data)
        self.view = ProjectedVolume(self.plotter, self.session, SHAPE[2])
        self.addCleanup(self.plotter.close)
        self.grab_baseline()
        self.band_row = self.view.row_of(self.view.cz)

    def grab_baseline(self):
        self.baseline = self.view.grab()
        return self.baseline

    def _render_with(self, multiplier, data=None):
        self.session.render(
            self.data if data is None else data,
            (0, 50, 100),
            "linear",
            show_axes=False,
            opacity_multiplier=multiplier,
        )
        return self.view.grab()

    def _profiles(self):
        enhanced = self._render_with(self.multiplier)
        return (
            self.view.energy_profile(self.baseline, self.axis),
            self.view.energy_profile(enhanced, self.axis),
        )

    def test_effect_dims_the_background_and_keeps_the_band(self):
        baseline_profile, enhanced_profile = self._profiles()
        band_index = int(np.argmin(np.abs(self.axis - self.BAND)))
        # 峰中心倍率 ≈ 1：能带基本保留，对比度来自压低背景。
        self.assertLess(enhanced_profile[-1], baseline_profile[-1] * 0.5)
        self.assertGreater(
            enhanced_profile[band_index], baseline_profile[band_index] * 0.6
        )
        self.assertGreater(
            enhanced_profile[band_index], enhanced_profile[-1] * 2.0
        )

    def test_profile_follows_the_gaussian_multiplier(self):
        baseline_profile, enhanced_profile = self._profiles()
        lit = baseline_profile > 10.0
        self.assertGreater(int(lit.sum()), 16)
        correlation = np.corrcoef(
            enhanced_profile[lit], self.multiplier[lit]
        )[0, 1]
        self.assertGreater(correlation, 0.98)

    def test_unit_multiplier_keeps_the_image_close_to_the_original(self):
        """倍率恒为 1 时效果应接近原图。

        VTK 对二分量体数据的采样与单分量不完全等价（实测亮度差在 20% 以内，
        集中在强度梯度大的位置），所以这里给的是容差而不是逐位相等；关闭效果
        必须逐位还原，由 test_disabling_restores_the_original_render 保证。
        """
        neutral = self._render_with(np.ones(SHAPE[2]))
        difference = np.abs(
            neutral.astype(np.int16) - self.baseline.astype(np.int16)
        )
        self.assertLess(float(difference.mean()), 6.0)
        self.assertLess(float(np.percentile(difference, 95)), 16.0)

    def test_same_intensity_keeps_the_same_colour(self):
        enhanced = self._render_with(self.multiplier)
        column = self.view.width_px // 2
        baseline_rgb = self.baseline[self.band_row, column, :3].astype(np.float64)
        enhanced_rgb = enhanced[self.band_row, column, :3].astype(np.float64)
        self.assertGreater(float(enhanced_rgb.mean()), 10.0)
        ratio = enhanced_rgb / np.maximum(baseline_rgb, 1.0)
        self.assertLess(float(ratio.std()), 0.05)

    def test_disabling_restores_the_original_render(self):
        self._render_with(self.multiplier)
        restored = self._render_with(None)
        np.testing.assert_array_equal(restored, self.baseline)

    def test_zero_multiplier_hides_the_data(self):
        hidden = self._render_with(np.zeros(SHAPE[2]))
        column = self.view.width_px // 2
        self.assertEqual(float(hidden[:, column, :3].max()), 0.0)

    def test_two_component_wiring_is_scoped_to_the_effect(self):
        self._render_with(self.multiplier)
        scalars = self.session.volume.mapper.GetInput().GetPointData().GetScalars()
        self.assertEqual(scalars.GetNumberOfComponents(), 2)
        self.assertEqual(
            self.session.volume.GetProperty().GetIndependentComponents(), 0
        )

        self._render_with(None)
        scalars = self.session.volume.mapper.GetInput().GetPointData().GetScalars()
        self.assertEqual(scalars.GetNumberOfComponents(), 1)
        self.assertEqual(
            self.session.volume.GetProperty().GetIndependentComponents(), 1
        )

    def test_nan_voxels_stay_invisible_in_both_modes(self):
        data = np.array(self.data, copy=True)
        data[:, :, 0] = np.nan  # 最低能面整体缺失
        without = self._render_with(None, data=data)
        with_effect = self._render_with(np.full(SHAPE[2], 5.0), data=data)

        bottom_row = self.view.row_of(self.view.z0 + 0.5)
        top_row = self.view.row_of(self.view.z1 - 0.5)
        column = self.view.width_px // 2
        for image in (without, with_effect):
            self.assertEqual(float(image[bottom_row, column, :3].max()), 0.0)
        self.assertGreater(float(with_effect[top_row, column, :3].max()), 10.0)

    def test_clipping_planes_survive_the_alpha_channel(self):
        clip = (self.view.x0, self.view.x1, self.view.y0, self.view.y1,
                self.view.z0, self.view.cz)
        clipped = self._render_with_clip(clip)
        unclipped = self._render_with(self.multiplier)
        self.assertLess(
            float(clipped[..., :3].mean()), float(unclipped[..., :3].mean()) * 0.8
        )

    def _render_with_clip(self, clip_ranges):
        self.session.render(
            self.data,
            (0, 50, 100),
            "linear",
            show_axes=False,
            clip_ranges=clip_ranges,
            opacity_multiplier=self.multiplier,
        )
        return self.view.grab()


if __name__ == "__main__":
    unittest.main()
