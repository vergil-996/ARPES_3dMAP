import unittest
from unittest.mock import Mock

import numpy as np
from matplotlib.backend_bases import KeyEvent, MouseEvent
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from bandscope.ui import theme
from bandscope.ui.interactive_crop_box import CropBoxGestureCoordinator, InteractiveCropBox


class InteractiveCropBoxTests(unittest.TestCase):
    def setUp(self):
        self.figure = Figure(figsize=(6, 5), dpi=100)
        self.canvas = FigureCanvasAgg(self.figure)
        self.axes = self.figure.add_subplot()
        self.axes.set(xlim=(0, 10), ylim=(0, 10))
        self.selected = []
        self.box = InteractiveCropBox(
            self.axes, lambda a, b: self.selected.append(
                (a.xdata, b.xdata, a.ydata, b.ydata)), (2, 8, 3, 7))
        self.canvas.draw()

    def tearDown(self):
        self.box.dispose()

    def event(self, name, xy):
        x, y = self.axes.transData.transform(xy)
        return MouseEvent(name, self.canvas, x, y, button=1)

    def drag(self, start, end):
        for name, xy in (("button_press_event", start),
                         ("motion_notify_event", end),
                         ("button_release_event", end)):
            self.canvas.callbacks.process(name, self.event(name, xy))

    def test_initial_box_and_all_handles_are_visible(self):
        self.assertEqual(self.box.extents, (2, 8, 3, 7))
        self.assertTrue(all(a.get_visible() for a in self.box.artists))
        self.assertEqual(len(self.box.corners[0]), 4)
        self.assertEqual(len(self.box.edge_centers[0]), 4)

    def test_full_range_corner_is_draggable_just_outside_axes_border(self):
        self.box.sync_bounds((0, 10, 0, 10))
        self.drag((-0.01, -0.01), (2, 2))
        np.testing.assert_allclose(self.box.extents, (2, 10, 2, 10))

    def test_each_corner_and_edge_resizes_existing_box(self):
        gestures = [
            ((2, 3), (1, 2), (1, 8, 2, 7)),
            ((8, 3), (9, 2), (2, 9, 2, 7)),
            ((8, 7), (9, 8), (2, 9, 3, 8)),
            ((2, 7), (1, 8), (1, 8, 3, 8)),
            ((2, 5), (1, 5), (1, 8, 3, 7)),
            ((5, 3), (5, 2), (2, 8, 2, 7)),
            ((8, 5), (9, 5), (2, 9, 3, 7)),
            ((5, 7), (5, 8), (2, 8, 3, 8)),
        ]
        for start, end, expected in gestures:
            with self.subTest(start=start):
                self.box.sync_bounds((2, 8, 3, 7))
                self.drag(start, end)
                np.testing.assert_allclose(self.box.extents, expected)
                np.testing.assert_allclose(self.selected[-1], expected)

    def test_interior_translation_and_limits_preserve_size(self):
        for end, expected in [((5, 5), (3, 9, 4, 8)),
                              ((10, 10), (4, 10, 6, 10)),
                              ((0, 0), (0, 6, 0, 4)),
                              ((14, 14), (4, 10, 6, 10))]:
            with self.subTest(end=end):
                self.box.sync_bounds((2, 8, 3, 7))
                self.drag((4, 4), end)
                np.testing.assert_allclose(self.box.extents, expected)

    def test_crossing_opposite_edge_keeps_handle_identity_on_next_drag(self):
        self.drag((2, 5), (9, 5))
        np.testing.assert_allclose(self.box.extents, (8, 9, 3, 7))
        self.drag((8, 3), (7, 2))
        np.testing.assert_allclose(self.box.extents, (7, 9, 2, 7))

    def test_outside_drag_and_click_do_not_replace_or_clear_box(self):
        self.drag((0.5, 0.5), (9.5, 9.5))
        self.drag((0.5, 0.5), (0.5, 0.5))
        self.assertEqual(self.box.extents, (2, 8, 3, 7))
        self.assertEqual(self.selected, [])
        self.assertIsNone(self.box._eventpress)

    def test_zero_width_or_height_restores_last_valid_draft(self):
        for start, end in [((2, 5), (8, 5)), ((5, 3), (5, 7))]:
            self.drag(start, end)
            np.testing.assert_allclose(self.box.extents, (2, 8, 3, 7))
            self.assertTrue(all(a.get_visible() for a in self.box.artists))
        self.assertEqual(self.selected, [])

    def test_numeric_overflow_is_preserved_and_can_be_resized_back(self):
        self.box.sync_bounds((-2, 8, -3, 7))
        self.assertEqual(self.box.extents, (-2, 8, -3, 7))
        self.drag((8, 7), (6, 6))
        np.testing.assert_allclose(self.box.extents, (-2, 6, -3, 6))

    def test_shortcuts_cannot_clear_rotate_or_force_outside_translation(self):
        for key in ("escape", "r", "shift", "control", " "):
            self.canvas.callbacks.process("key_press_event", KeyEvent(
                "key_press_event", self.canvas, key=key))
        self.drag((0.5, 0.5), (9.5, 9.5))
        self.assertEqual(self.box.extents, (2, 8, 3, 7))
        self.assertEqual(self.box.rotation, 0)
        self.assertTrue(self.box.get_visible())

    def test_reversed_axes_and_widget_lock(self):
        self.axes.set(xlim=(10, 0), ylim=(10, 0))
        self.canvas.draw()
        owner = object()
        self.canvas.widgetlock(owner)
        self.drag((2, 3), (1, 2))
        self.assertEqual(self.selected, [])
        self.canvas.widgetlock.release(owner)
        self.drag((2, 3), (1, 2))
        np.testing.assert_allclose(self.box.extents, (1, 8, 2, 7))

    def test_press_activates_before_release_callback(self):
        activated = []
        self.box.dispose()
        self.box = InteractiveCropBox(
            self.axes,
            lambda a, b: self.selected.append((a.xdata, b.xdata, a.ydata, b.ydata)),
            (2, 8, 3, 7),
            on_activate=lambda: activated.append(True),
        )

        self.drag((2, 3), (1, 2))

        self.assertEqual(activated, [True])
        self.assertEqual(len(self.selected), 1)

    def test_dispose_removes_artists_and_callbacks(self):
        artists = self.box.artists
        self.box.dispose()
        self.assertTrue(all(a.axes is None for a in artists))
        self.drag((2, 3), (1, 2))
        self.assertEqual(self.selected, [])

    def test_label_uses_standard_artist_draw_visibility_and_dispose_lifecycle(self):
        self.box.dispose()
        self.box = InteractiveCropBox(
            self.axes, lambda a, b: None, (2, 8, 3, 7), label="Z7"
        )
        label = self.box._label_artist
        label.draw = Mock(wraps=label.draw)
        self.canvas.draw()

        self.assertGreaterEqual(label.draw.call_count, 1)
        self.assertEqual(label.get_text(), "Z7")
        self.assertEqual(label.get_position(), (2, 7))
        self.assertIs(self.box.artists[0], self.box._selection_artist)
        self.assertIs(self.box.artists[-1], label)
        self.assertIn(label, self.axes.texts)
        self.assertIs(label.axes, self.axes)
        self.box.sync_bounds((-2, 9, -3, 11))
        self.assertEqual(label.get_position(), (-2, 11))
        self.box.set_label("Z8")
        self.box.update_theme()
        self.box.update()
        self.assertEqual(label.get_text(), "Z8")
        self.assertEqual(label.get_color(), theme.ACCENT)
        self.assertTrue(label.get_visible())

        self.box.set_visible(False)
        label.draw.reset_mock()
        self.canvas.draw()
        label.draw.assert_not_called()
        self.assertFalse(label.get_visible())

        self.box.dispose()
        self.assertNotIn(label, self.axes.texts)
        self.assertIsNone(label.axes)

    def test_label_is_blit_artist_but_is_hidden_for_background_capture(self):
        self.box.dispose()
        self.box = InteractiveCropBox(
            self.axes, lambda a, b: None, (2, 8, 3, 7), label="Z7"
        )
        label = self.box._label_artist
        self.canvas.draw()
        visibility_during_draw = []
        draw = self.canvas.draw

        def record_background_draw():
            visibility_during_draw.append(label.get_visible())
            return draw()

        self.canvas.draw = Mock(side_effect=record_background_draw)
        self.box.update_background(None)

        self.assertEqual(visibility_during_draw, [False])
        self.assertIn(label, self.box.artists)
        self.assertNotIn(label, self.box._get_animated_artists())
        # matplotlib 3.11 把捕获结果登记在画布上并用 _load_blit_background 取回，
        # 3.10 及更早版本直接保存在 background 属性上。
        loader = getattr(self.box, "_load_blit_background", None)
        background = loader() if callable(loader) else getattr(self.box, "background", None)
        self.assertIsNotNone(background)
        self.assertTrue(label.get_visible())

        self.canvas.restore_region(background)
        expected = np.asarray(self.canvas.buffer_rgba()).copy()
        self.box.update()
        self.assertTrue(np.any(np.asarray(self.canvas.buffer_rgba()) != expected))
        self.box.set_visible(False)
        self.box.update()
        np.testing.assert_array_equal(np.asarray(self.canvas.buffer_rgba()), expected)

    def test_display_handle_hit_query_excludes_plain_interior(self):
        for point, expected in [((2, 3), "SW"), ((5, 7), "N"), ((5, 5), "C")]:
            with self.subTest(point=point):
                x, y = self.axes.transData.transform(point)
                self.assertEqual(self.box.handle_at_display(x, y), expected)
        x, y = self.axes.transData.transform((3, 6))
        self.assertIsNone(self.box.handle_at_display(x, y))
        self.box.set_active(False)
        x, y = self.axes.transData.transform((5, 5))
        self.assertIsNone(self.box.handle_at_display(x, y))


class OverlappingInteractiveCropBoxTests(unittest.TestCase):
    def setUp(self):
        self.figure = Figure(figsize=(6, 5), dpi=100)
        self.canvas = FigureCanvasAgg(self.figure)
        self.axes = self.figure.add_subplot()
        self.axes.set(xlim=(0, 10), ylim=(0, 10))
        self.activated = []
        self.selected = {"lower": [], "upper": []}
        self.coordinator = CropBoxGestureCoordinator()
        self.lower = self.make_box("lower", (2, 8, 3, 7))
        self.upper = self.make_box("upper", (2, 8, 3, 7))
        self.coordinator.set_selectors([self.lower, self.upper])
        self.canvas.draw()

    def tearDown(self):
        self.upper.dispose()
        self.lower.dispose()

    def make_box(self, name, bounds):
        return InteractiveCropBox(
            self.axes,
            lambda a, b, name=name: self.selected[name].append(
                (a.xdata, b.xdata, a.ydata, b.ydata)),
            bounds,
            on_activate=lambda: self.activated.append(name),
            coordinator=self.coordinator,
        )

    def drag(self, start, end):
        for name, xy in (("button_press_event", start),
                         ("motion_notify_event", end),
                         ("button_release_event", end)):
            x, y = self.axes.transData.transform(xy)
            self.canvas.callbacks.process(
                name, MouseEvent(name, self.canvas, x, y, button=1))

    def test_identical_boxes_only_move_topmost_then_allow_next_gesture(self):
        self.drag((5, 5), (9, 9))

        np.testing.assert_allclose(self.lower.extents, (2, 8, 3, 7))
        np.testing.assert_allclose(self.upper.extents, (4, 10, 6, 10))
        self.assertEqual(self.activated, ["upper"])
        self.assertEqual(self.selected["lower"], [])
        self.assertEqual(len(self.selected["upper"]), 1)

        self.drag((2.5, 3.5), (1.5, 2.5))

        np.testing.assert_allclose(self.lower.extents, (1, 7, 2, 6))
        np.testing.assert_allclose(self.upper.extents, (4, 10, 6, 10))
        self.assertEqual(self.activated, ["upper", "lower"])
        self.assertEqual(len(self.selected["lower"]), 1)
        self.assertEqual(len(self.selected["upper"]), 1)

    def test_nested_boxes_only_move_topmost_then_allow_next_gesture(self):
        self.lower.sync_bounds((1, 9, 1, 9))
        self.upper.sync_bounds((3, 7, 3, 7))

        self.drag((5, 5), (6, 6))

        np.testing.assert_allclose(self.lower.extents, (1, 9, 1, 9))
        np.testing.assert_allclose(self.upper.extents, (4, 8, 4, 8))
        self.assertEqual(self.activated, ["upper"])
        self.assertEqual(self.selected["lower"], [])
        self.assertEqual(len(self.selected["upper"]), 1)

        self.drag((2, 2), (1, 1))

        np.testing.assert_allclose(self.lower.extents, (0, 8, 0, 8))
        np.testing.assert_allclose(self.upper.extents, (4, 8, 4, 8))
        self.assertEqual(self.activated, ["upper", "lower"])
        self.assertEqual(len(self.selected["lower"]), 1)
        self.assertEqual(len(self.selected["upper"]), 1)


if __name__ == "__main__":
    unittest.main()
