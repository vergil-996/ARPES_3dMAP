"""2D 图像比例：kx–ky 面按物理坐标 1:1，含能量轴的图继续铺满画布。

回归背景：图像曾统一用 aspect='auto' 绘制，形状完全跟随窗口宽高比，
小窗里看着是圆的 Fermi 面一进全屏就被拉成横椭圆。
"""
import unittest
from unittest.mock import Mock

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from bandscope.rendering.render_core import VisualEngine


def _figure(width_px, height_px, dpi=100):
    figure = Figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi, facecolor="#101014")
    return figure, FigureCanvasAgg(figure), figure.add_subplot(111)


def _coords(n_x=40, n_y=30, n_e=24):
    return {
        "X": np.linspace(-1.4, 1.4, n_x),
        "Y": np.linspace(-1.4, 1.4, n_y),
        "E": np.linspace(-0.4, 0.4, n_e),
    }


def _slice_for_axis(axis, coords):
    """按 _extract_slice_data 的排布返回 (data, slice_info)。"""
    shape = {
        0: (len(coords["E"]), len(coords["Y"])),
        1: (len(coords["E"]), len(coords["X"])),
        2: (len(coords["Y"]), len(coords["X"])),
    }[axis]
    data = np.random.rand(*shape)
    return data, {"axis": axis, "mode": "slice", "index": 0}


def _scale_per_unit(axes):
    """(每数据单位占的像素宽, 高)。"""
    x0, x1 = axes.get_xlim()
    y0, y1 = axes.get_ylim()
    box = axes.bbox
    return box.width / (x1 - x0), box.height / (y1 - y0)


class Render2DAspectTests(unittest.TestCase):
    def test_kx_ky_slice_keeps_physical_one_to_one_scale(self):
        coords = _coords()
        data, slice_info = _slice_for_axis(2, coords)

        for width_px, height_px in ((760, 560), (1900, 980), (820, 1000)):
            with self.subTest(window=f"{width_px}x{height_px}"):
                figure, canvas, axes = _figure(width_px, height_px)
                VisualEngine.render_2d_slice(
                    axes, canvas, data, slice_info, (0, 50, 100), coords
                )
                canvas.draw()
                scale_x, scale_y = _scale_per_unit(axes)
                self.assertAlmostEqual(scale_x, scale_y, delta=0.02 * scale_x)

    def test_kx_ky_slice_keeps_its_shape_when_the_window_grows(self):
        coords = _coords()
        data, slice_info = _slice_for_axis(2, coords)
        figure, canvas, axes = _figure(760, 560)
        VisualEngine.render_2d_slice(axes, canvas, data, slice_info, (0, 50, 100), coords)
        canvas.draw()
        small = axes.bbox.bounds

        figure.set_size_inches(19.0, 9.8)
        canvas.draw()
        wide = axes.bbox.bounds
        self.assertAlmostEqual(
            small[2] / small[3], wide[2] / wide[3], delta=0.01
        )
        # 全屏下只是变大，不再被横向拉扁
        self.assertGreater(wide[2], small[2])

    def test_energy_momentum_slice_still_fills_the_panel(self):
        coords = _coords()
        for axis in (0, 1):
            with self.subTest(axis=axis):
                data, slice_info = _slice_for_axis(axis, coords)
                figure, canvas, axes = _figure(1900, 980)
                VisualEngine.render_2d_slice(
                    axes, canvas, data, slice_info, (0, 50, 100), coords
                )
                canvas.draw()
                self.assertEqual(axes.get_aspect(), "auto")
                slot = axes.get_position(original=True)
                self.assertAlmostEqual(
                    axes.bbox.width, slot.width * figure.get_figwidth() * figure.dpi, delta=1.5
                )

    def test_colorbar_is_glued_to_the_image_in_both_modes(self):
        coords = _coords()
        for axis in (1, 2):
            with self.subTest(axis=axis):
                data, slice_info = _slice_for_axis(axis, coords)
                figure, canvas, axes = _figure(1900, 980)
                VisualEngine.render_2d_slice(
                    axes, canvas, data, slice_info, (0, 50, 100), coords
                )
                canvas.draw()
                colorbar = axes._arpes_colorbar
                self.assertIsNotNone(colorbar)
                image_box = axes.bbox
                colorbar_box = colorbar.ax.bbox
                gap = colorbar_box.x0 - image_box.x1
                self.assertGreater(gap, 0.0)
                self.assertLess(gap, image_box.width * 0.06)
                self.assertAlmostEqual(colorbar_box.height, image_box.height, delta=1.5)
                self.assertAlmostEqual(colorbar_box.y0, image_box.y0, delta=1.5)

    def test_colorbar_follows_the_image_after_a_resize(self):
        coords = _coords()
        data, slice_info = _slice_for_axis(2, coords)
        figure, canvas, axes = _figure(1200, 900)
        VisualEngine.render_2d_slice(axes, canvas, data, slice_info, (0, 50, 100), coords)
        canvas.draw()
        colorbar = axes._arpes_colorbar

        figure.set_size_inches(19.0, 9.8)
        canvas.draw()
        self.assertAlmostEqual(colorbar.ax.bbox.height, axes.bbox.height, delta=1.5)
        self.assertLess(colorbar.ax.bbox.x0 - axes.bbox.x1, axes.bbox.width * 0.06)

    def test_leaving_the_2d_view_releases_the_aspect(self):
        coords = _coords()
        data, slice_info = _slice_for_axis(2, coords)
        figure, canvas, axes = _figure(1200, 900)
        VisualEngine.render_2d_slice(axes, canvas, data, slice_info, (0, 50, 100), coords)
        canvas.draw()
        self.assertEqual(float(axes.get_aspect()), 1.0)

        # 1D 曲线页走的就是这条路径：撤销 2D 图像 → 重建画布
        VisualEngine.clear_2d_colorbar(axes)
        axes.clear()
        axes.plot([0.0, 1.0], [0.0, 1.0])
        canvas.draw()
        self.assertEqual(axes.get_aspect(), "auto")
        self.assertEqual(len(axes.child_axes), 0)
        self.assertEqual(len(figure.axes), 1)

    def test_switching_back_to_a_2d_page_restores_the_policy(self):
        coords = _coords()
        kx_ky_data, kx_ky_info = _slice_for_axis(2, coords)
        ek_data, ek_info = _slice_for_axis(1, coords)
        figure, canvas, axes = _figure(1400, 900)
        VisualEngine.render_2d_slice(
            axes, canvas, ek_data, ek_info, (0, 50, 100), coords
        )
        canvas.draw()
        self.assertEqual(axes.get_aspect(), "auto")

        VisualEngine.render_2d_slice(
            axes, canvas, kx_ky_data, kx_ky_info, (0, 50, 100), coords
        )
        canvas.draw()
        scale_x, scale_y = _scale_per_unit(axes)
        self.assertAlmostEqual(scale_x, scale_y, delta=0.02 * scale_x)

    def test_preview_blit_only_covers_the_letterboxed_image(self):
        coords = _coords(n_x=60, n_y=60, n_e=24)
        data, slice_info = _slice_for_axis(2, coords)
        figure, canvas, axes = _figure(1900, 980)
        VisualEngine.render_2d_slice(axes, canvas, data, slice_info, (0, 50, 100), coords)
        canvas.draw()
        image_box = axes.bbox
        outside_x = int(max(image_box.x0 - 30, 1))
        before_outside = np.asarray(canvas.buffer_rgba())[400, outside_x].copy()
        before_inside = np.asarray(canvas.buffer_rgba())[400, int(image_box.x1) - 30].copy()

        canvas.draw_idle = Mock()
        VisualEngine.render_2d_slice(
            axes,
            canvas,
            data * 0.0,
            {**slice_info, "index": 1},
            (0, 0, 1),
            coords,
            quality="preview",
        )

        frame = np.asarray(canvas.buffer_rgba())
        np.testing.assert_array_equal(frame[400, outside_x], before_outside)
        self.assertFalse(np.array_equal(frame[400, int(image_box.x1) - 30], before_inside))

    def test_aspect_policy_reads_the_slice_axis(self):
        self.assertTrue(VisualEngine._2d_uses_equal_aspect({"axis": 2}))
        self.assertFalse(VisualEngine._2d_uses_equal_aspect({"axis": 0}))
        self.assertFalse(VisualEngine._2d_uses_equal_aspect({"axis": 1}))
        self.assertFalse(VisualEngine._2d_uses_equal_aspect({}))


if __name__ == "__main__":
    unittest.main()
