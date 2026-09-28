# -*- coding: utf-8 -*-
"""2D 轴标题的会话状态与命中/拖动交互。

覆盖设计意图：默认文字由调用方按坐标元信息给出，只有用户改过的轴才写入
覆盖（空串是"明确不要标题"，与"未覆盖"必须区分）；拖动位置用相对绘图区
坐标保存；标题命中优先于裁剪——按下标题时先拿画布 widget lock，裁剪用的
``RectangleSelector`` 会因此让路。
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from matplotlib.backend_bases import MouseButton, MouseEvent
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.widgets import RectangleSelector

from bandscope.ui.axis_title_controller import (
    RESTORE_DEFAULT_TEXT,
    AxisTitleController,
    AxisTitleState,
    axis_title_key,
)

PAGE = "page-1"
KEY_2D = axis_title_key(PAGE, "2d", ("X", "E"))
DEFAULTS = {"x": "kx (Å⁻¹)", "y": "E (eV)"}


class AxisTitleStateTests(unittest.TestCase):
    def setUp(self):
        self.state = AxisTitleState()

    def test_defaults_are_used_until_one_axis_is_renamed(self):
        self.assertEqual(self.state.resolve(KEY_2D, DEFAULTS), DEFAULTS)
        self.assertEqual(self.state.text(KEY_2D, "x"), None)
        self.assertTrue(self.state.set_text(KEY_2D, "x", "Binding energy"))
        self.assertEqual(
            self.state.resolve(KEY_2D, DEFAULTS),
            {"x": "Binding energy", "y": "E (eV)"},
        )

    def test_empty_text_means_hide_and_is_not_the_same_as_no_override(self):
        self.state.set_text(KEY_2D, "y", "   ")
        self.assertEqual(self.state.text(KEY_2D, "y"), "")
        self.assertEqual(self.state.resolve(KEY_2D, DEFAULTS)["y"], "")
        self.assertTrue(self.state.reset_text(KEY_2D, "y"))
        self.assertEqual(self.state.resolve(KEY_2D, DEFAULTS)["y"], "E (eV)")

    def test_text_is_trimmed_and_repeats_are_not_recorded(self):
        self.assertTrue(self.state.set_text(KEY_2D, "x", "  kx  "))
        self.assertEqual(self.state.text(KEY_2D, "x"), "kx")
        self.assertFalse(self.state.set_text(KEY_2D, "x", "kx"))

    def test_pages_views_and_axis_pairs_are_separate_settings(self):
        x_e = axis_title_key(PAGE, "2d", ("X", "E"))
        x_y = axis_title_key(PAGE, "2d", ("X", "Y"))
        three_d = axis_title_key(PAGE, "3d", ("X", "Y", "E"))
        other_page = axis_title_key("page-2", "2d", ("X", "E"))
        self.state.set_text(x_e, "x", "kx")
        self.state.set_text(x_y, "x", "kx'")
        self.state.set_text(three_d, "X", "Kx")

        self.assertEqual(self.state.text(x_e, "x"), "kx")
        self.assertEqual(self.state.text(x_y, "x"), "kx'")
        self.assertEqual(self.state.text(three_d, "X"), "Kx")
        self.assertIsNone(self.state.text(other_page, "x"))

    def test_position_is_stored_and_reset_per_axis(self):
        self.assertIsNone(self.state.position(KEY_2D, "x"))
        self.assertTrue(self.state.set_position(KEY_2D, "x", (0.2, -0.3)))
        self.assertEqual(self.state.position(KEY_2D, "x"), (0.2, -0.3))
        self.assertFalse(self.state.set_position(KEY_2D, "x", (0.2, -0.3)))
        self.assertTrue(self.state.reset_position(KEY_2D, "x"))
        self.assertTrue(self.state.set_position(KEY_2D, "x", (0.2, -0.3)))
        self.assertTrue(self.state.set_position(KEY_2D, "y", (0.1, 0.5)))
        self.assertTrue(self.state.reset_positions(KEY_2D))
        self.assertIsNone(self.state.position(KEY_2D, "y"))

    def test_forget_page_drops_only_that_page(self):
        other = axis_title_key("page-2", "2d", ("X", "E"))
        self.state.set_text(KEY_2D, "x", "kx")
        self.state.set_position(KEY_2D, "x", (0.1, 0.1))
        self.state.set_text(other, "x", "other")
        self.state.forget_page(PAGE)
        self.assertIsNone(self.state.text(KEY_2D, "x"))
        self.assertIsNone(self.state.position(KEY_2D, "x"))
        self.assertEqual(self.state.text(other, "x"), "other")

    def test_clear_drops_every_page(self):
        self.state.set_text(KEY_2D, "x", "kx")
        self.state.set_text(axis_title_key("page-2", "3d", ("X", "Y", "E")), "X", "Kx")
        self.state.clear()
        self.assertEqual(self.state.texts(KEY_2D), {})
        self.assertIsNone(self.state.position(KEY_2D, "x"))


class FontFallbackTests(unittest.TestCase):
    """主画布与导出图的中文回退字体必须一致。

    两处各存一份字面量（theme 给 matplotlib 主画布，截图样式给导出渲染），
    这里钉住它们同源：一边补了 CJK 字体而另一边忘了，中文就会一边显示方块。
    """

    def test_plot_font_family_matches_the_export_style(self):
        from bandscope.exporting.publication_models import styles_for_family
        from bandscope.ui import theme

        families = {
            tuple(style.params["font_family"])
            for family in ("2d", "3d", "1d")
            for style in styles_for_family(family)
        }
        self.assertTrue(families)
        self.assertEqual(
            families,
            {tuple(theme.MPL_FONT_FAMILIES)},
            "theme.MPL_FONT_FAMILIES 与截图样式的 font_family 已经不同源",
        )


class _Harness:
    """一张带标题的 2D 图 + 挂在它上面的控制器与裁剪选区。"""

    def __init__(self):
        self.figure = Figure(figsize=(6, 4), dpi=100)
        self.canvas = FigureCanvasAgg(self.figure)
        self.axes = self.figure.add_subplot(111)
        self.axes.imshow(np.zeros((6, 8)), extent=[-1.0, 1.0, -2.0, 2.0], origin="lower")
        self.axes.set_xlim(-1.0, 1.0)
        self.axes.set_ylim(-2.0, 2.0)
        self.state = AxisTitleState()
        self.edits = []
        self.changes = 0
        self.cursor = "unset"
        self.controller = AxisTitleController(
            self.canvas,
            self.axes,
            self.state,
            title_context=lambda: {"key": KEY_2D, "defaults": DEFAULTS},
            edit_request=self._edit_request,
            cursor_sink=self._cursor_sink,
            on_change=self._on_change,
        )
        self.apply_titles()
        self.selector = RectangleSelector(
            self.axes, lambda *_: None, useblit=True, button=[1], interactive=False
        )
        self.canvas.draw()

    # ------------------------------------------------------------- 辅助
    def _edit_request(self, axis, current, default):
        self.edits.append((axis, current, default))
        return self.edit_result

    edit_result = None

    def _cursor_sink(self, cursor):
        self.cursor = cursor

    def _on_change(self):
        self.changes += 1

    def apply_titles(self):
        """按会话状态重画标题，等价于主窗口的渲染路径。"""
        from bandscope.rendering.render_core import VisualEngine

        titles = self.state.resolve(KEY_2D, DEFAULTS)
        positions = {
            axis: self.state.position(KEY_2D, axis)
            for axis in ("x", "y")
            if self.state.position(KEY_2D, axis) is not None
        }
        VisualEngine.render_2d_slice(
            self.axes,
            self.canvas,
            np.zeros((6, 8)),
            {"axis": 1, "mode": "slice", "index": 0},
            (0, 50, 100),
            {
                "X": np.linspace(-1.0, 1.0, 8),
                "Y": np.linspace(1.0, 1.0, 1),
                "E": np.linspace(-2.0, 2.0, 6),
            },
            axis_titles=titles,
            axis_title_positions=positions,
        )
        self.canvas.draw()

    def label(self, axis):
        return self.axes.xaxis.label if axis == "x" else self.axes.yaxis.label

    def label_center(self, axis):
        box = self.label(axis).get_window_extent(self.canvas.get_renderer())
        return (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0

    def event(self, name, xy, *, button=MouseButton.LEFT, dblclick=False):
        return MouseEvent(name, self.canvas, xy[0], xy[1], button=button, dblclick=dblclick)

    def press(self, xy, **kwargs):
        self.canvas.callbacks.process("button_press_event", self.event("button_press_event", xy, **kwargs))

    def motion(self, xy):
        self.canvas.callbacks.process("motion_notify_event", self.event("motion_notify_event", xy))

    def release(self, xy):
        self.canvas.callbacks.process("button_release_event", self.event("button_release_event", xy))


class AxisTitleControllerTests(unittest.TestCase):
    def setUp(self):
        self.harness = _Harness()

    # ------------------------------------------------------------- 改名
    def test_double_click_asks_for_the_current_and_default_text(self):
        self.harness.edit_result = "Binding energy"
        self.harness.press(self.harness.label_center("x"), dblclick=True)
        self.assertEqual(self.harness.edits, [("x", "kx (Å⁻¹)", "kx (Å⁻¹)")])
        self.harness.apply_titles()
        self.assertEqual(self.harness.axes.get_xlabel(), "Binding energy")

    def test_cancelling_the_dialog_keeps_the_previous_title(self):
        self.harness.edit_result = None
        self.harness.press(self.harness.label_center("x"), dblclick=True)
        self.harness.apply_titles()
        self.assertEqual(self.harness.axes.get_xlabel(), "kx (Å⁻¹)")
        self.assertEqual(self.harness.state.texts(KEY_2D), {})

    def test_clearing_the_field_hides_the_title(self):
        self.harness.edit_result = ""
        self.harness.press(self.harness.label_center("y"), dblclick=True)
        self.harness.apply_titles()
        self.assertEqual(self.harness.axes.get_ylabel(), "")
        self.assertEqual(self.harness.state.text(KEY_2D, "y"), "")

    def test_restore_default_text_undoes_only_that_axis(self):
        self.harness.state.set_text(KEY_2D, "x", "custom x")
        self.harness.state.set_text(KEY_2D, "y", "custom y")
        self.harness.edit_result = RESTORE_DEFAULT_TEXT
        self.harness.press(self.harness.label_center("x"), dblclick=True)
        self.harness.apply_titles()
        self.assertEqual(self.harness.axes.get_xlabel(), "kx (Å⁻¹)")
        self.assertEqual(self.harness.axes.get_ylabel(), "custom y")

    def test_reset_positions_returns_to_the_automatic_placement(self):
        self.harness.state.set_position(KEY_2D, "x", (0.2, -0.4))
        self.harness.apply_titles()
        moved = self.harness.label_center("x")
        self.assertTrue(self.harness.controller.reset_positions())
        self.harness.apply_titles()
        self.assertNotAlmostEqual(moved[0], self.harness.label_center("x")[0])

    # ------------------------------------------------------------- 拖动
    def test_drag_moves_the_horizontal_label_without_rotating_it(self):
        start = self.harness.label_center("x")
        self.harness.press(start)
        self.harness.motion((start[0] + 40.0, start[1] + 25.0))
        self.harness.release((start[0] + 40.0, start[1] + 25.0))
        self.harness.apply_titles()
        moved = self.harness.label_center("x")
        self.assertAlmostEqual(moved[0] - start[0], 40.0, delta=2.0)
        self.assertAlmostEqual(moved[1] - start[1], 25.0, delta=2.0)
        self.assertEqual(self.harness.label("x").get_rotation(), 0.0)
        self.assertEqual(self.harness.label("y").get_rotation(), 90.0)

    def test_drag_position_is_stored_relative_to_the_plot_area(self):
        self.harness.press(self.harness.label_center("y"))
        self.harness.motion((self.harness.label_center("y")[0] + 30.0, 200.0))
        self.harness.release((self.harness.label_center("y")[0] + 30.0, 200.0))
        position = self.harness.state.position(KEY_2D, "y")
        self.assertIsNotNone(position)
        self.assertTrue(0.0 <= position[1] <= 1.0)

    def test_drag_keeps_the_title_inside_the_canvas(self):
        self.harness.press(self.harness.label_center("x"))
        self.harness.motion((-400.0, -400.0))
        self.harness.release((-400.0, -400.0))
        box = self.harness.label("x").get_window_extent(self.harness.canvas.get_renderer())
        figure_box = self.harness.figure.bbox
        self.assertGreaterEqual(box.x0, figure_box.x0 - 0.5)
        self.assertGreaterEqual(box.y0, figure_box.y0 - 0.5)

    def test_click_without_movement_does_not_store_a_position(self):
        self.harness.press(self.harness.label_center("x"))
        self.harness.release(self.harness.label_center("x"))
        self.assertIsNone(self.harness.state.position(KEY_2D, "x"))

    def test_resize_ends_the_drag(self):
        self.harness.press(self.harness.label_center("x"))
        self.harness.motion((self.harness.label_center("x")[0] + 20.0, 40.0))
        self.assertTrue(self.harness.controller.dragging)
        self.harness.figure.set_size_inches(7.0, 5.0)
        self.harness.canvas.callbacks.process("resize_event", None)
        self.assertFalse(self.harness.controller.dragging)

    def test_cancel_drag_puts_the_label_back_where_it_started(self):
        before = self.harness.label_center("x")
        self.harness.press(before)
        self.harness.motion((before[0] + 60.0, before[1] + 40.0))
        self.harness.controller.cancel_drag()
        self.assertFalse(self.harness.controller.dragging)
        self.assertIsNone(self.harness.state.position(KEY_2D, "x"))
        after = self.harness.label_center("x")
        # 画面与状态一致：位置没记下来，标签也不能停在拖动落点。
        self.assertAlmostEqual(after[0], before[0], delta=1.0)
        self.assertAlmostEqual(after[1], before[1], delta=1.0)
        self.assertTrue(self.harness.axes.xaxis._autolabelpos)

    def test_cancel_drag_keeps_an_already_committed_position(self):
        self.harness.press(self.harness.label_center("x"))
        self.harness.motion((self.harness.label_center("x")[0] + 30.0, 30.0))
        self.harness.release((self.harness.label_center("x")[0] + 30.0, 30.0))
        committed = self.harness.state.position(KEY_2D, "x")
        self.assertIsNotNone(committed)

        self.harness.press(self.harness.label_center("x"))
        self.harness.motion((self.harness.label_center("x")[0] - 80.0, 60.0))
        self.harness.controller.cancel_drag()
        self.assertFalse(self.harness.axes.xaxis._autolabelpos)
        self.assertEqual(self.harness.state.position(KEY_2D, "x"), committed)

    # -------------------------------------------------------- 与裁剪共存
    def test_pressing_a_title_takes_the_canvas_lock_from_the_crop_selector(self):
        self.harness.press(self.harness.label_center("x"))
        self.assertTrue(self.harness.selector.ignore(self.harness.event("button_press_event", (10, 10))))
        self.harness.release(self.harness.label_center("x"))

    def test_pressing_the_plot_area_leaves_the_crop_selector_alone(self):
        self.harness.press((300.0, 200.0))
        self.assertFalse(self.harness.selector.ignore(self.harness.event("button_press_event", (10, 10))))

    def test_hover_uses_the_move_cursor_only_over_a_title(self):
        self.harness.motion(self.harness.label_center("y"))
        self.assertIsNotNone(self.harness.cursor)
        self.harness.motion((300.0, 200.0))
        self.assertIsNone(self.harness.cursor)

    def test_controller_ignores_events_while_the_canvas_is_not_active(self):
        self.harness.controller.active_provider = lambda: False
        self.harness.edit_result = "should not apply"
        self.harness.press(self.harness.label_center("x"), dblclick=True)
        self.assertEqual(self.harness.edits, [])


if __name__ == "__main__":
    unittest.main()
