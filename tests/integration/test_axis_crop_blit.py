import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from matplotlib.colors import to_rgb
from bandscope.ui import theme

from matplotlib.backend_bases import MouseButton, MouseEvent
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from bandscope.ui.plot_coordinate_tooltip import PlotCoordinateTooltip
from bandscope.core.crop_model import CropSelection
from bandscope.app.refactored_app import My3DAnalyzer


class _CheckedSwitch:
    def isChecked(self):
        return True


class _CanvasStack:
    def currentIndex(self):
        return 1


class AxisCropBlitTests(unittest.TestCase):
    def setUp(self):
        self.figure = Figure(figsize=(4, 3), dpi=100)
        self.canvas = FigureCanvasAgg(self.figure)
        self.axes = self.figure.add_subplot(111)
        self.axes.set_xlim(0.0, 10.0)
        self.axes.set_ylim(0.0, 10.0)
        self.canvas.draw()

        self.analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        self.analyzer.ax_2d = self.axes
        self.analyzer.canvas_2d = self.canvas
        self.analyzer.axis_crop_selector = None
        self.analyzer.axis_crop_overlay = None
        self.analyzer.left_display_stack = _CanvasStack()
        self.analyzer.crop_controller = SimpleNamespace(
            enabled=True, selection=CropSelection("2d", ("X", "E"), (2., 7., 3., 8.)))
        self.analyzer._supports_axis_crop_context = lambda _spec, _context: True
        self.selected = []
        self.analyzer._on_axis_crop_selected = (
            lambda press, release: self.selected.append((press, release))
        )
        self.spec = SimpleNamespace(page_id="axis-page", page_kind="axis_integral")
        self.context = {"view": "2d"}
        self.tooltip = PlotCoordinateTooltip(
            self.canvas,
            self.axes,
            context_provider=lambda: {
                "view": "2d",
                "plot_axes": {"x_label": "kx", "y_label": "E"},
            },
            external_blit_provider=self.analyzer._axis_crop_selector_will_blit,
            overlay_artists_provider=self.analyzer._active_2d_blit_artists,
        )
        self.analyzer.plot_coordinate_tooltip = self.tooltip

        self.analyzer._refresh_axis_crop_interaction(self.spec, self.context)
        self.canvas.draw()

    def tearDown(self):
        self.analyzer._clear_axis_crop_selector()

    def _mouse_event(self, name, x_data, y_data, button=MouseButton.LEFT):
        x_pixel, y_pixel = self.axes.transData.transform((x_data, y_data))
        return MouseEvent(
            name,
            self.canvas,
            x_pixel,
            y_pixel,
            button=button,
        )

    def test_rectangle_selector_uses_blit_during_drag(self):
        selector = self.analyzer.axis_crop_selector
        self.assertTrue(selector.useblit)
        self.canvas.draw_idle = Mock()
        self.canvas.blit = Mock(wraps=self.canvas.blit)

        press = self._mouse_event("button_press_event", 2.0, 3.0)
        move = self._mouse_event("motion_notify_event", 1.0, 2.0)
        selector.press(press)
        selector.onmove(move)

        self.canvas.blit.assert_called()
        self.canvas.draw_idle.assert_not_called()
        self.assertTrue(self.analyzer._axis_crop_selector_will_blit(move))

    def test_tooltip_and_rectangle_share_one_blit_pass_while_dragging(self):
        selector = self.analyzer.axis_crop_selector
        press = self._mouse_event("button_press_event", 2.0, 3.0)
        move = self._mouse_event("motion_notify_event", 1.0, 2.0)
        selector.press(press)
        self.canvas.draw_idle = Mock()
        self.canvas.blit = Mock(wraps=self.canvas.blit)

        self.tooltip._on_motion(move)
        self.canvas.blit.assert_not_called()
        selector.onmove(move)

        self.canvas.blit.assert_called_once()
        self.canvas.draw_idle.assert_not_called()
        self.assertAlmostEqual(self.tooltip.annotation.xy[0], 1.0)
        self.assertAlmostEqual(self.tooltip.annotation.xy[1], 2.0)
        self.assertIn(self.tooltip.annotation, selector._get_animated_artists())

    def test_selection_callback_still_runs_only_on_release(self):
        selector = self.analyzer.axis_crop_selector
        press = self._mouse_event("button_press_event", 2.0, 3.0)
        move = self._mouse_event("motion_notify_event", 1.0, 2.0)
        release = self._mouse_event("button_release_event", 1.0, 2.0)

        selector.press(press)
        selector.onmove(move)
        self.assertEqual(self.selected, [])

        selector.release(release)
        self.assertEqual(len(self.selected), 1)

    def _assert_all_nine_handles_painted(self):
        selector = self.analyzer.axis_crop_selector
        centers = list(zip(*selector.corners)) + list(zip(*selector.edge_centers)) + [selector.center]
        pixels = np.asarray(self.canvas.buffer_rgba())
        accent = np.asarray(to_rgb(theme.ACCENT)) * 255
        for center in centers:
            x, y = self.axes.transData.transform(center)
            x, y = round(x), pixels.shape[0] - round(y)
            patch = pixels[y-4:y+5, x-4:x+5, :3]
            with self.subTest(center=center):
                self.assertGreater(np.count_nonzero(np.max(np.abs(patch - accent), axis=2) < 4), 5)

    def test_nine_handles_stay_painted_after_release_hover_leave_and_redraw(self):
        selector = self.analyzer.axis_crop_selector
        selector.press(self._mouse_event("button_press_event", 2, 3))
        selector.onmove(self._mouse_event("motion_notify_event", 1, 2))
        selector.release(self._mouse_event("button_release_event", 1, 2))
        self._assert_all_nine_handles_painted()
        # Hover away from the rectangle: no selector blit will follow.
        self.tooltip._on_motion(self._mouse_event("motion_notify_event", 9, 5, button=None))
        self._assert_all_nine_handles_painted()
        self.tooltip._on_leave(None)
        self._assert_all_nine_handles_painted()
        self.canvas.draw()
        self.tooltip._on_motion(self._mouse_event("motion_notify_event", 9, 5, button=None))
        self._assert_all_nine_handles_painted()


if __name__ == "__main__":
    unittest.main()
