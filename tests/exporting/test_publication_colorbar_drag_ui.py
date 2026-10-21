"""Colorbar drag layers reach both worker previews and the style draft."""
import unittest

from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.exporting.publication_dialog import (
    LARGE_DPI, PublicationExportDialog, _PreviewTask,
)
from tests.support.publication import MemoryPublicationSettings, _make_2d_snapshot


class _Window(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = MemoryPublicationSettings()


class ColorbarDragControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = _Window()
        self.dialog = PublicationExportDialog(self.window)
        self.dialog.snapshot = _make_2d_snapshot()
        self.dialog._draft_style_id = "2d_boxed"
        self.dialog._committed_overrides = {"2d_boxed": {}}
        self.dialog._draft_overrides = {}
        self.dialog._load_output_options_ui()
        self.dialog._update_tune_availability()
        self.dialog._sync_tune_ui_from_draft()
        self.dialog._fit_to_window = False

    def tearDown(self):
        self.dialog._debounce.stop()
        self.dialog.close()
        self.dialog.deleteLater()
        self.window.close()

    def _render_worker_preview(self):
        task = _PreviewTask(
            self.dialog._preview_key("2d_boxed", LARGE_DPI),
            self.dialog._preview_revision, self.dialog.snapshot,
            self.dialog._current_style(), dict(self.dialog._draft_overrides),
            self.dialog._collect_output_options(), LARGE_DPI,
        )
        results, failures = [], []
        task.signals.done.connect(lambda *args: results.append(args))
        task.signals.failed.connect(lambda *args: failures.append(args))
        task.run()
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 1)
        self.dialog._on_preview_done(*results[0])
        return results[0]

    def test_orientation_row_has_no_hover_bubbles(self):
        for widget in (
            self.dialog.orientation_row, self.dialog.chk_orientation,
            self.dialog.spin_orientation_cx, self.dialog.spin_orientation_cy,
            self.dialog.btn_orientation_auto,
        ):
            self.assertEqual(widget.toolTip(), "")

    def test_worker_delivers_colorbar_layer_and_cached_preview_is_draggable(self):
        key, _revision, payload, layers = self._render_worker_preview()
        self.assertIn("colorbar", layers)
        self.assertNotIn("orientation", layers)
        self.assertIsNotNone(self.dialog.preview_label.colorbar_rect())
        self.assertIsNone(self.dialog.preview_label.orientation_rect())
        self.assertEqual(self.dialog._collect_overrides_from_ui(), {})
        self.dialog.preview_label.clear_colorbar()
        self.dialog._apply_preview("2d_boxed", LARGE_DPI, payload,
                                   self.dialog._orientation_previews[key])
        self.assertIsNotNone(self.dialog.preview_label.colorbar_rect())

    def test_drag_updates_decimal_controls_and_defers_render_until_release(self):
        self._render_worker_preview()
        self.dialog.preview_label.set_colorbar_center(62.35, 50.15)
        rect = self.dialog.preview_label.colorbar_rect()
        center = ((rect[0] + rect[2] / 2.) * 100.,
                  (rect[1] + rect[3] / 2.) * 100.)
        self.dialog.preview_label.colorbarMoved.emit(*center)
        self.assertAlmostEqual(self.dialog.spin_cbar_cx.value(), center[0], places=2)
        self.assertAlmostEqual(self.dialog.spin_cbar_cy.value(), center[1], places=2)
        self.assertEqual(self.dialog._draft_overrides["colorbar_cx"],
                         self.dialog.spin_cbar_cx.value())
        self.assertFalse(self.dialog._debounce.isActive())
        self.dialog.preview_label.colorbarDragFinished.emit()
        self.assertTrue(self.dialog._debounce.isActive())
        self.assertFalse(self.dialog._big_ready)

    def test_hidden_colorbar_has_no_drag_layer_or_hitbox(self):
        self.dialog.chk_cbar.setChecked(False)
        self._render_worker_preview()
        self.assertIsNone(self.dialog.preview_label.colorbar_rect())

    def test_manual_decimal_position_survives_ui_roundtrip(self):
        self.dialog.spin_cbar_cx.setValue(63.27)
        self.dialog.spin_cbar_cy.setValue(45.81)
        expected = {"colorbar_cx": 63.27, "colorbar_cy": 45.81}
        self.assertEqual(self.dialog._draft_overrides, expected)
        self.dialog._sync_tune_ui_from_draft()
        self.assertEqual(self.dialog._collect_overrides_from_ui(), expected)


if __name__ == "__main__":
    unittest.main()
