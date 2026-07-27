import unittest
from unittest.mock import Mock

from matplotlib.backend_bases import MouseEvent
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from plot_coordinate_tooltip import PlotCoordinateTooltip


class PlotCoordinateTooltipTests(unittest.TestCase):
    def setUp(self):
        self.figure = Figure(figsize=(4, 3), dpi=100)
        self.canvas = FigureCanvasAgg(self.figure)
        self.axes = self.figure.add_subplot(111)
        self.axes.set_xlim(-2.0, 2.0)
        self.axes.set_ylim(-3.0, 3.0)
        self.context = {"view": "1d", "xlabel": "Energy (eV)"}
        self.active = True
        self.tooltip = PlotCoordinateTooltip(
            self.canvas,
            self.axes,
            context_provider=lambda: self.context,
            active_provider=lambda: self.active,
        )
        self.canvas.draw()

    def _motion_event(self, x_data=0.5, y_data=-1.25):
        x_pixel, y_pixel = self.axes.transData.transform((x_data, y_data))
        return MouseEvent("motion_notify_event", self.canvas, x_pixel, y_pixel)

    def test_every_2d_and_1d_view_type_shows_white_coordinate_bubble(self):
        contexts = (
            {"view": "2d", "plot_axes": {"x_label": "kx", "y_label": "E"}},
            {"view": "1d", "xlabel": "Delay (ps)"},
            {"view": "1d_comparison", "xlabel": "Energy (eV)", "ylabel": "Intensity (a.u.)"},
            {"view": "waterfall", "xlabel": "Intensity", "ylabel": "Energy (eV)"},
        )

        for context in contexts:
            with self.subTest(view=context["view"]):
                self.context = context
                self.tooltip._on_motion(self._motion_event())
                annotation = self.tooltip.annotation
                self.assertTrue(annotation.get_visible())
                self.assertEqual(annotation.get_color(), "#FFFFFF")
                self.assertIn("0.5", annotation.get_text())
                self.assertIn("-1.25", annotation.get_text())

    def test_bubble_hides_outside_plot_or_when_left_canvas_is_inactive(self):
        self.tooltip._on_motion(self._motion_event())
        self.assertTrue(self.tooltip.annotation.get_visible())

        outside_event = MouseEvent("motion_notify_event", self.canvas, 0, 0)
        self.tooltip._on_motion(outside_event)
        self.assertFalse(self.tooltip.annotation.get_visible())

        self.active = False
        self.tooltip._on_motion(self._motion_event())
        self.assertFalse(self.tooltip.annotation.get_visible())

    def test_bubble_is_recreated_after_axes_are_cleared(self):
        self.tooltip._on_motion(self._motion_event())
        old_annotation = self.tooltip.annotation

        self.axes.clear()
        self.axes.set_xlim(-2.0, 2.0)
        self.axes.set_ylim(-3.0, 3.0)
        self.canvas.draw()
        self.tooltip._on_motion(self._motion_event())

        self.assertIsNot(self.tooltip.annotation, old_annotation)
        self.assertTrue(self.tooltip.annotation.get_visible())

    def test_motion_uses_blit_without_scheduling_a_full_canvas_draw(self):
        self.assertIsNotNone(self.tooltip._background)
        self.canvas.draw_idle = Mock()
        self.canvas.blit = Mock()

        self.tooltip._on_motion(self._motion_event())

        self.canvas.blit.assert_called_once()
        self.canvas.draw_idle.assert_not_called()
        self.assertTrue(self.tooltip.annotation.get_animated())

    def test_hide_restores_cached_background_with_blit(self):
        self.tooltip._on_motion(self._motion_event())
        self.canvas.draw_idle = Mock()
        self.canvas.blit = Mock()

        self.tooltip.hide()

        self.assertFalse(self.tooltip.annotation.get_visible())
        self.canvas.blit.assert_called_once()
        self.canvas.draw_idle.assert_not_called()

    def test_external_blit_updates_annotation_without_drawing_twice(self):
        self.tooltip.external_blit_provider = lambda _event: True
        self.canvas.draw_idle = Mock()
        self.canvas.blit = Mock()

        self.tooltip._on_motion(self._motion_event(1.25, 2.5))

        self.assertAlmostEqual(self.tooltip.annotation.xy[0], 1.25)
        self.assertAlmostEqual(self.tooltip.annotation.xy[1], 2.5)
        self.assertIn("1.25", self.tooltip.annotation.get_text())
        self.assertIn("2.5", self.tooltip.annotation.get_text())
        self.canvas.blit.assert_not_called()
        self.canvas.draw_idle.assert_not_called()

    def test_resize_invalidates_background_until_the_next_draw(self):
        self.assertIsNotNone(self.tooltip._background)

        self.tooltip._on_resize(None)
        self.assertIsNone(self.tooltip._background)

        self.canvas.draw()
        self.assertIsNotNone(self.tooltip._background)


if __name__ == "__main__":
    unittest.main()
