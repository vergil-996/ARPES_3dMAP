# -*- coding: utf-8 -*-
"""渲染接口里的轴标题：2D 文字/位置与 3D 轴名刷新判定。

2D 标题画在坐标系外（不在 ``ax.bbox`` 里），所以它一变就必须让快速刷新的
底图作废，否则预览 blit 会把旧标签留在画布上。3D 侧不接真实 VTK/OpenGL
（CI runner 没有 GL）：只替身记录 ``show_bounds`` 的参数，验证轴名进了刷新
签名——坐标没变但改了名同样要重画。
"""
import unittest
from types import SimpleNamespace

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.transforms import IdentityTransform

from bandscope.rendering.render_core import VisualEngine, VolumeRenderSession

COORDS = {
    "X": np.linspace(-1.0, 1.0, 4),
    "Y": np.linspace(-2.0, 2.0, 5),
    "E": np.linspace(-0.5, 0.5, 6),
}
SLICE_INFO = {"axis": 0, "mode": "integral", "range": (1, 1)}


def _figure(width=4.0, height=3.0):
    figure = Figure(figsize=(width, height), dpi=100)
    return figure, FigureCanvasAgg(figure), figure.add_subplot(111)


def _render(axes, canvas, titles, positions=None, quality="exact", data=None):
    return VisualEngine.render_2d_slice(
        axes,
        canvas,
        np.ones((5, 6), dtype=np.float32) if data is None else data,
        SLICE_INFO,
        (0, 50, 100),
        COORDS,
        quality=quality,
        axis_titles=titles,
        axis_title_positions=positions,
    )


class Render2DAxisTitleTests(unittest.TestCase):
    def test_titles_are_written_to_both_axes(self):
        _, canvas, axes = _figure()
        _render(axes, canvas, {"x": "ky (Å⁻¹)", "y": "E (eV)"})
        canvas.draw()
        self.assertEqual(axes.get_xlabel(), "ky (Å⁻¹)")
        self.assertEqual(axes.get_ylabel(), "E (eV)")

    def test_without_titles_nothing_is_invented(self):
        # 只有 2D 图像渲染会写轴标题；不传标题时不替调用方编文字
        # （整帧重建走 ax.clear()，标签也就随之清空）。
        _, canvas, axes = _figure()
        _render(axes, canvas, None)
        canvas.draw()
        self.assertEqual(axes.get_xlabel(), "")
        self.assertEqual(axes.get_ylabel(), "")

    def test_empty_title_hides_the_axis_label(self):
        _, canvas, axes = _figure()
        _render(axes, canvas, {"x": "", "y": "E"})
        canvas.draw()
        self.assertEqual(axes.get_xlabel(), "")
        self.assertEqual(axes.get_ylabel(), "E")

    def test_position_survives_a_full_redraw_and_can_be_released(self):
        _, canvas, axes = _figure()
        _render(axes, canvas, {"x": "kx", "y": "ky"})
        canvas.draw()
        default = axes.xaxis.label.get_window_extent(canvas.get_renderer())

        _render(axes, canvas, {"x": "kx", "y": "ky"}, {"x": (0.2, 0.5)})
        canvas.draw()
        moved = axes.xaxis.label.get_window_extent(canvas.get_renderer())
        self.assertNotAlmostEqual(default.x0, moved.x0, delta=5.0)
        self.assertFalse(axes.xaxis._autolabelpos)

        _render(axes, canvas, {"x": "kx", "y": "ky"})
        canvas.draw()
        restored = axes.xaxis.label.get_window_extent(canvas.get_renderer())
        self.assertAlmostEqual(default.x0, restored.x0, delta=0.5)
        self.assertAlmostEqual(default.y0, restored.y0, delta=0.5)
        self.assertTrue(axes.xaxis._autolabelpos)
        self.assertIsInstance(axes.xaxis.label.get_transform(), type(
            axes.xaxis.label.get_transform()
        ))
        self.assertTrue(axes.yaxis._autolabelpos)

    def test_title_change_invalidates_the_preview_blit_background(self):
        _, canvas, axes = _figure(width=6.0, height=4.0)
        _render(axes, canvas, {"x": "kx", "y": "E"})
        canvas.draw()
        stale = axes._arpes_preview_background
        self.assertIsNotNone(stale)

        # 改了标题就不能再 blit 旧底图：走整帧重绘，标签跟着新文字。
        result = _render(axes, canvas, {"x": "binding energy", "y": "E"}, quality="preview")
        self.assertIsNone(result)
        self.assertEqual(axes.get_xlabel(), "binding energy")
        self.assertEqual(
            axes._arpes_preview_background_signature, axes._arpes_render_signature
        )

    def test_untouched_titles_still_take_the_preview_fast_path(self):
        _, canvas, axes = _figure(width=6.0, height=4.0)
        _render(axes, canvas, {"x": "kx", "y": "E"})
        canvas.draw()
        background = axes._arpes_preview_background

        result = _render(
            axes, canvas, {"x": "kx", "y": "E"}, quality="preview", data=np.zeros((5, 6))
        )
        self.assertTrue(result)
        self.assertIsNot(axes._arpes_preview_background, background)

    def test_reset_hands_the_labels_back_to_matplotlib(self):
        """1D 页复用同一个 ax_2d：2D 拖过的显式位置必须先交还。"""
        _, canvas, axes = _figure()
        _render(axes, canvas, {"x": "kx", "y": "ky"})
        canvas.draw()
        automatic = {
            key: (axes.xaxis if key == "x" else axes.yaxis)
            .label.get_window_extent(canvas.get_renderer())
            .bounds
            for key in ("x", "y")
        }

        _render(axes, canvas, {"x": "kx", "y": "ky"}, {"x": (0.2, 0.4), "y": (0.1, 0.5)})
        canvas.draw()
        self.assertFalse(axes.xaxis._autolabelpos)
        self.assertFalse(axes.yaxis._autolabelpos)

        VisualEngine.reset_2d_axis_labels(axes)
        canvas.draw()
        self.assertTrue(axes.xaxis._autolabelpos)
        self.assertTrue(axes.yaxis._autolabelpos)
        for key in ("x", "y"):
            label = (axes.xaxis if key == "x" else axes.yaxis).label
            np.testing.assert_allclose(
                label.get_window_extent(canvas.get_renderer()).bounds,
                automatic[key],
                atol=0.5,
                err_msg=f"{key} 轴标签没有回到自动位置",
            )

    def test_titles_do_not_disturb_the_colorbar_or_the_aspect_policy(self):
        _, canvas, axes = _figure(width=9.0, height=5.0)
        kx_ky = {**SLICE_INFO, "axis": 2, "mode": "slice", "index": 0}
        VisualEngine.render_2d_slice(
            axes,
            canvas,
            np.ones((6, 4), dtype=np.float32),
            kx_ky,
            (0, 50, 100),
            COORDS,
            axis_titles={"x": "kx", "y": "ky"},
        )
        canvas.draw()
        self.assertEqual(float(axes.get_aspect()), 1.0)
        self.assertIsNotNone(axes._arpes_colorbar)
        self.assertEqual(axes.get_xlabel(), "kx")


class _FakeAxisActor:
    n_xlabels = 4
    n_ylabels = 4
    n_zlabels = 4

    def __init__(self):
        self.axis_labels = {}

    def SetAxisLabels(self, axis, labels):
        self.axis_labels[axis] = labels


class _FakeBoundsPlotter:
    """只记录坐标轴调用的替身：CI 没有 OpenGL，真实 plotter 会带走整个进程。"""

    background_color = (0.1, 0.1, 0.1)

    def __init__(self):
        self.show_bounds_calls = []
        self.removed = 0
        self.actor = _FakeAxisActor()

    def remove_bounds_axes(self, *args, **kwargs):
        self.removed += 1

    def show_bounds(self, **kwargs):
        self.show_bounds_calls.append(kwargs)
        return self.actor


def _axes_session(plotter):
    session = VolumeRenderSession.__new__(VolumeRenderSession)
    session.plotter = plotter
    session.grid = SimpleNamespace(dimensions=(4, 4, 4))
    session._axes_signature = None
    return session


class ThreeDAxisTitleTests(unittest.TestCase):
    def test_render_axes_writes_the_given_titles(self):
        plotter = _FakeBoundsPlotter()
        VisualEngine.render_axes(plotter, (4, 4, 4), COORDS, axis_titles=("a", "b", "c"))
        call = plotter.show_bounds_calls[-1]
        self.assertEqual((call["xtitle"], call["ytitle"], call["ztitle"]), ("a", "b", "c"))

    def test_render_axes_keeps_the_legacy_titles_by_default(self):
        plotter = _FakeBoundsPlotter()
        VisualEngine.render_axes(plotter, (4, 4, 4), COORDS)
        call = plotter.show_bounds_calls[-1]
        self.assertEqual(
            (call["xtitle"], call["ytitle"], call["ztitle"]),
            VisualEngine.DEFAULT_3D_AXIS_TITLES,
        )

    def test_renaming_an_axis_redraws_even_though_the_coordinates_are_unchanged(self):
        plotter = _FakeBoundsPlotter()
        session = _axes_session(plotter)
        session._update_axes(True, COORDS, ("Kx", "Ky", "E (eV)"))
        self.assertEqual(len(plotter.show_bounds_calls), 1)

        session._update_axes(True, COORDS, ("kx (Å⁻¹)", "Ky", "E (eV)"))
        self.assertEqual(len(plotter.show_bounds_calls), 2)
        self.assertEqual(plotter.show_bounds_calls[-1]["xtitle"], "kx (Å⁻¹)")

    def test_repeating_the_same_titles_does_not_redraw(self):
        plotter = _FakeBoundsPlotter()
        session = _axes_session(plotter)
        session._update_axes(True, COORDS, ("Kx", "Ky", "E (eV)"))
        session._update_axes(True, COORDS, ("Kx", "Ky", "E (eV)"))
        self.assertEqual(len(plotter.show_bounds_calls), 1)

    def test_a_rename_while_hidden_applies_once_the_axes_reappear(self):
        plotter = _FakeBoundsPlotter()
        session = _axes_session(plotter)
        session._update_axes(True, COORDS, ("Kx", "Ky", "E (eV)"))
        session._update_axes(False, COORDS, ("Kx", "Ky", "E (eV)"))

        # 坐标轴关着也能改名：不画，但要记住新名字。
        session._update_axes(False, COORDS, ("Binding energy", "Ky", "E (eV)"))
        self.assertEqual(len(plotter.show_bounds_calls), 1)

        session._update_axes(True, COORDS, ("Binding energy", "Ky", "E (eV)"))
        self.assertEqual(len(plotter.show_bounds_calls), 2)
        self.assertEqual(plotter.show_bounds_calls[-1]["xtitle"], "Binding energy")


if __name__ == "__main__":
    unittest.main()
