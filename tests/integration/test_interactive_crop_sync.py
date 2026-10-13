import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from matplotlib.backend_bases import MouseEvent
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.crop_model import CropSelection
from bandscope.ui.crop_controls import CropController


class InteractiveCropSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = QWidget()
        self.host = My3DAnalyzer.__new__(My3DAnalyzer)
        self.host.canvas_2d = FigureCanvasAgg(Figure(figsize=(6, 5)))
        self.host.ax_2d = self.host.canvas_2d.figure.add_subplot()
        self.host.ax_2d.set(xlim=(0, 10), ylim=(0, 10))
        self.host.axis_crop_selector = None
        self.host.axis_crop_overlay = None
        self.host.left_display_stack = SimpleNamespace(currentIndex=lambda: 1)
        self.spec = SimpleNamespace(page_id="a")
        self.host.left_workspace = SimpleNamespace(current_spec=lambda: self.spec)
        self.host.current_render_context = {"view": "2d"}
        self.controller = self.host.crop_controller = CropController(self.parent)
        self.controller.enabled = True
        self.controller.page_id = "a"
        self.controller.set_selection(CropSelection("2d", ("X", "E"), (2, 8, 3, 7)), notify=False)
        # The host is an uninitialized QObject test double; keep delivery on
        # this thread rather than asking Qt to resolve its receiver thread.
        self.controller.selection_changed.connect(lambda: self.host._on_crop_selection_changed())
        self.applied = Mock()
        self.controller.apply_requested.connect(self.applied)
        self.refresh()

    def tearDown(self):
        self.host._clear_axis_crop_interaction()
        self.controller.clear()
        self.parent.close()

    def refresh(self):
        self.host._refresh_axis_crop_interaction(self.spec, self.host.current_render_context)
        self.host.canvas_2d.draw()

    def test_mouse_release_syncs_popup_in_place_and_requires_apply(self):
        selector = self.host.axis_crop_selector
        for name, point in (("button_press_event", (2, 3)),
                            ("motion_notify_event", (1, 2))):
            x, y = self.host.ax_2d.transData.transform(point)
            self.host.canvas_2d.callbacks.process(name, MouseEvent(
                name, self.host.canvas_2d, x, y, button=1))
        self.assertEqual(self.controller.selection.bounds, (2, 8, 3, 7))
        name = "button_release_event"
        self.host.canvas_2d.callbacks.process(name, MouseEvent(
            name, self.host.canvas_2d, x, y, button=1))
        np.testing.assert_allclose(self.controller.selection.bounds, (1, 8, 2, 7))
        self.assertIs(selector, self.host.axis_crop_selector)
        self.assertEqual([e.text() for e in self.controller.popup.edits[:4]], ["1", "8", "2", "7"])
        self.applied.assert_not_called()
        self.controller.popup.apply_button.click()
        self.applied.assert_called_once()

    def test_numeric_edits_update_existing_box_and_keep_overflow(self):
        selector = self.host.axis_crop_selector
        for edit, value in zip(self.controller.popup.edits, (-2, 9, 1, 12)):
            edit.setText(str(value))
        self.assertTrue(self.controller.commit_edits())
        self.assertIs(selector, self.host.axis_crop_selector)
        self.assertEqual(selector.extents, (-2, 9, 1, 12))
        self.controller.popup.edits[1].setText("nan")
        self.assertFalse(self.controller.commit_edits())
        self.assertEqual(selector.extents, (-2, 9, 1, 12))

    def test_all_low_dimensional_modes_share_visible_box(self):
        for view in ("2d", "1d", "1d_comparison", "waterfall"):
            for operation in ("crop", "erase"):
                with self.subTest(view=view, operation=operation):
                    self.host.current_render_context = {"view": view}
                    self.controller.set_selection(replace(
                        self.controller.selection, view=view, operation=operation))
                    self.assertTrue(self.host.axis_crop_selector.get_visible())
                    self.assertEqual(self.host.axis_crop_selector.extents, (2, 8, 3, 7))

    def test_repeated_toggle_refresh_and_axes_clear_do_not_leak(self):
        baseline = len(self.host.canvas_2d.callbacks.callbacks["button_press_event"])
        for _ in range(8):
            self.controller.enabled = False
            self.refresh()
            self.assertEqual(len(self.host.ax_2d.patches), 0)
            self.assertEqual(len(self.host.ax_2d.lines), 0)
            self.controller.enabled = True
            self.refresh()
            self.refresh()
            self.assertEqual(len(self.host.ax_2d.patches), 1)
            self.assertEqual(len(self.host.ax_2d.lines), 3)
            self.assertEqual(len(self.host.canvas_2d.callbacks.callbacks["button_press_event"]), baseline)
        old = self.host.axis_crop_selector
        self.host.ax_2d.clear()
        self.host.ax_2d.set(xlim=(0, 10), ylim=(0, 10))
        self.refresh()
        self.assertIsNot(self.host.axis_crop_selector, old)
        self.assertFalse(old.active)
        self.assertEqual(self.host.axis_crop_selector.extents, (2, 8, 3, 7))

    def test_page_switch_restores_saved_draft_and_empty_context_removes_box(self):
        context = {"view": "1d", "x_data": np.arange(11.), "y_data": np.arange(11.)}
        self.controller.activate("b", context)
        self.spec.page_id = "b"
        self.host.current_render_context = context
        self.refresh()
        self.assertEqual(self.host.axis_crop_selector.extents, (0, 10, 0, 10))
        self.controller.activate("a", {"view": "2d", "data": np.ones((11, 11)),
            "slice_info": {"axis": 1}, "coords": {"X": np.arange(11.), "E": np.arange(11.)}}, e_flip=True)
        self.spec.page_id = "a"
        self.host.current_render_context = {"view": "2d"}
        self.refresh()
        self.assertEqual(self.host.axis_crop_selector.extents, (2, 8, 3, 7))
        self.assertTrue(self.controller.selection.e_flip)
        self.host.current_render_context["crop_empty"] = True
        self.refresh()
        self.assertIsNone(self.host.axis_crop_selector)

    def test_new_2d_page_defaults_to_inset_box_and_restores_exact_draft(self):
        context = {"view": "2d", "data": np.ones((11, 11)),
            "slice_info": {"axis": 1}, "coords": {"X": np.arange(10., -1., -1.), "E": np.arange(11.)}}
        self.controller.activate("new", context)
        self.assertEqual(self.controller.selection.bounds, (1, 9, 1, 9))
        self.spec.page_id = "new"
        self.host.current_render_context = context
        self.refresh()
        box = self.host.axis_crop_selector
        for x, y in list(zip(*box.corners)) + list(zip(*box.edge_centers)) + [box.center]:
            pixel = self.host.ax_2d.transData.transform((x, y))
            self.assertTrue(self.host.ax_2d.bbox.contains(*pixel))
            self.assertGreater(min(pixel[0] - self.host.ax_2d.bbox.x0,
                                   self.host.ax_2d.bbox.x1 - pixel[0],
                                   pixel[1] - self.host.ax_2d.bbox.y0,
                                   self.host.ax_2d.bbox.y1 - pixel[1]), 10)
        saved = replace(self.controller.selection, bounds=(-2, 12, -3, 13))
        self.controller.set_selection(saved)
        self.controller.activate("new", context)
        self.assertEqual(self.controller.selection.bounds, saved.bounds)


if __name__ == "__main__":
    unittest.main()
