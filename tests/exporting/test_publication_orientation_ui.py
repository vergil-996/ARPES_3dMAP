"""Optional screen-direction marker controls and persisted style preferences."""
import io
import unittest

from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.exporting.publication_dialog import (
    CACHE_LIMIT, LARGE_DPI, PublicationExportDialog, _orientation_preview_layers,
)
from bandscope.exporting.publication_export import commit_style, committed_style_for
from bandscope.exporting.publication_models import OutputOptions, load_overrides, resolve_style, validate_overrides
from tests.support.publication import (
    MemoryPublicationSettings,
    _make_2d_snapshot,
    _make_3d_axes_snapshot,
    make_single_curve_snapshot,
)


class _Window(QWidget):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings


class OrientationControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = MemoryPublicationSettings()
        self.window = _Window(self.settings)
        self.dialog = PublicationExportDialog(self.window)
        self._load(_make_3d_axes_snapshot(), "3d_minimal")

    def tearDown(self):
        self.dialog._debounce.stop()
        self.dialog.close()
        self.dialog.deleteLater()
        self.window.close()

    def _load(self, snapshot, style_id, overrides=None):
        self.dialog.snapshot = snapshot
        self.dialog._draft_style_id = style_id
        self.dialog._committed_overrides = {style_id: dict(overrides or {})}
        self.dialog._draft_overrides = dict(overrides or {})
        self.dialog._load_output_options_ui()
        self.dialog._update_tune_availability()
        self.dialog._sync_tune_ui_from_draft()

    def test_marker_defaults_off_and_does_not_change_existing_style(self):
        self.assertFalse(self.dialog.chk_orientation.isHidden())
        self.assertFalse(self.dialog.chk_orientation.isChecked())
        self.assertEqual(self.dialog._collect_overrides_from_ui(), {})

    def test_checkbox_updates_draft_and_reset_disables_marker(self):
        self.dialog.chk_orientation.setChecked(True)
        self.assertEqual(self.dialog._draft_overrides, {"show_orientation": True})
        self.dialog._reset_style_overrides()
        self.assertFalse(self.dialog.chk_orientation.isChecked())
        self.assertEqual(self.dialog._collect_overrides_from_ui(), {})

    def test_marker_can_be_enabled_with_main_axes_off(self):
        snapshot = _make_3d_axes_snapshot()
        snapshot.payload["show_axes"] = False
        self._load(snapshot, "3d_minimal")
        self.assertTrue(self.dialog.chk_orientation.isEnabled())
        self.dialog.chk_orientation.setChecked(True)
        self.assertEqual(self.dialog._draft_overrides, {"show_orientation": True})
        self.assertIn("三轴方向标仍可单独开启", self.dialog.label_axis_hint.text())

    def test_marker_is_hidden_and_discarded_for_other_families(self):
        for snapshot, style_id in (
            (_make_2d_snapshot(), "2d_open"),
            (make_single_curve_snapshot(), "1d_open"),
        ):
            with self.subTest(family=snapshot.view_family):
                self._load(snapshot, style_id, {"show_orientation": True})
                self.assertTrue(self.dialog.chk_orientation.isHidden())
                self.assertNotIn("show_orientation", self.dialog._collect_overrides_from_ui())
                self.assertEqual(
                    validate_overrides(snapshot.view_family, {"show_orientation": True}), {}
                )

    def test_committed_marker_reopens_and_is_scoped_to_style(self):
        self.dialog.chk_orientation.setChecked(True)
        commit_style(self.settings, "3d", "3d_minimal", self.dialog._draft_overrides)
        style, saved = committed_style_for(self.settings, "3d")
        self.assertEqual(saved, {"show_orientation": True})
        self.assertEqual(load_overrides(self.settings, "3d", "3d_boxed"), {})
        self._load(_make_3d_axes_snapshot(), style.style_id, saved)
        self.assertTrue(self.dialog.chk_orientation.isChecked())
        self.dialog.chk_orientation.setChecked(False)
        commit_style(self.settings, "3d", style.style_id, self.dialog._draft_overrides)
        self.assertEqual(load_overrides(self.settings, "3d", style.style_id), {})

    def test_position_inputs_enable_with_marker_and_persist_while_hidden(self):
        self.assertFalse(self.dialog.spin_orientation_cx.isEnabled())
        self.dialog.chk_orientation.setChecked(True)
        self.assertTrue(self.dialog.spin_orientation_cx.isEnabled())
        self.assertTrue(self.dialog.spin_orientation_cy.isEnabled())
        self.dialog.spin_orientation_cx.setValue(70.25)
        self.dialog.spin_orientation_cy.setValue(30.5)
        self.assertEqual(self.dialog._draft_overrides, {
            "show_orientation": True, "orientation_cx": 70.25, "orientation_cy": 30.5,
        })
        self.dialog.chk_orientation.setChecked(False)
        self.assertFalse(self.dialog.spin_orientation_cx.isEnabled())
        saved = {"orientation_cx": 70.25, "orientation_cy": 30.5}
        self.assertEqual(self.dialog._draft_overrides, saved)
        commit_style(self.settings, "3d", "3d_minimal", saved)
        self._load(_make_3d_axes_snapshot(), "3d_minimal",
                   load_overrides(self.settings, "3d", "3d_minimal"))
        self.assertEqual(self.dialog.spin_orientation_cx.value(), 70.25)
        self.assertEqual(self.dialog.spin_orientation_cy.value(), 30.5)
        self.dialog.chk_orientation.setChecked(True)
        self.assertEqual(self.dialog._draft_overrides["orientation_cx"], 70.25)

    def test_auto_and_reset_remove_manual_position(self):
        self._load(_make_3d_axes_snapshot(), "3d_minimal", {
            "show_orientation": True, "orientation_cx": 70., "orientation_cy": 30.,
        })
        self.dialog._on_orientation_auto()
        self.assertEqual(self.dialog._draft_overrides, {"show_orientation": True})
        self.dialog.spin_orientation_cx.setValue(60.)
        self.dialog._reset_style_overrides()
        self.assertEqual(self.dialog._draft_overrides, {})
        self.assertFalse(self.dialog.spin_orientation_cx.isEnabled())

    def _make_preview(self):
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from bandscope.exporting.publication_renderers import _draw_3d_orientation

        figure = Figure(figsize=(89. / 25.4, 85. / 25.4), dpi=LARGE_DPI)
        FigureCanvasAgg(figure)
        title = figure.text(.5, .95, "preview title")
        marker = _draw_3d_orientation(
            figure, self.dialog.snapshot, resolve_style("3d", "3d_minimal").params,
            {}, (10., 9., 73., 78.), 89., 85.,
        )
        options = OutputOptions(width_mm=89., dpi=LARGE_DPI)
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png")
        payload = buffer.getvalue()
        layers = _orientation_preview_layers(figure, options)
        self.assertTrue(marker.get_visible())
        self.assertTrue(title.get_visible())
        self.assertNotEqual(layers[1], payload)
        figure.clear()
        return payload, layers

    def test_preview_drag_updates_inputs_draft_and_defers_render_until_release(self):
        self.dialog.chk_orientation.setChecked(True)
        payload, layers = self._make_preview()
        self.dialog._fit_to_window = False
        self.dialog._apply_preview("3d_minimal", LARGE_DPI, payload, layers)
        rect = self.dialog.preview_label.orientation_rect()
        self.assertIsNotNone(rect)
        self.assertAlmostEqual(self.dialog.spin_orientation_cx.value(),
                               (rect[0] + rect[2] / 2.) * 100., places=2)
        self.dialog.preview_label.set_orientation_center(70., 30.)
        self.dialog.preview_label.orientationMoved.emit(70., 30.)
        self.assertEqual(self.dialog.spin_orientation_cx.value(), 70.)
        self.assertEqual(self.dialog.spin_orientation_cy.value(), 30.)
        self.assertEqual(self.dialog._draft_overrides, {
            "show_orientation": True, "orientation_cx": 70., "orientation_cy": 30.,
        })
        self.assertFalse(self.dialog._debounce.isActive())
        self.dialog.preview_label.orientationDragFinished.emit()
        self.assertTrue(self.dialog._debounce.isActive())
        self.assertFalse(self.dialog._big_ready)

    def test_position_input_clamps_to_visible_marker_bounds(self):
        self.dialog.chk_orientation.setChecked(True)
        payload, layers = self._make_preview()
        self.dialog._fit_to_window = False
        self.dialog._apply_preview("3d_minimal", LARGE_DPI, payload, layers)
        self.dialog.spin_orientation_cx.setValue(0.)
        self.dialog.spin_orientation_cy.setValue(100.)
        rect = self.dialog.preview_label.orientation_rect()
        self.assertGreaterEqual(rect[0], 0.)
        self.assertLessEqual(rect[1] + rect[3], 1.)
        self.assertAlmostEqual(self.dialog.spin_orientation_cx.value(), rect[2] * 50., places=2)
        self.assertAlmostEqual(self.dialog.spin_orientation_cy.value(), 100. - rect[3] * 50., places=2)

    def test_cached_preview_retains_drag_layers_and_eviction_removes_them(self):
        payload, layers = self._make_preview()
        for index in range(CACHE_LIMIT + 1):
            self.dialog._cache_put((index,), payload, layers)
        self.assertNotIn((0,), self.dialog._orientation_previews)
        self.assertEqual(len(self.dialog._orientation_previews), CACHE_LIMIT)
        self.dialog._fit_to_window = False
        self.dialog._apply_preview("3d_minimal", LARGE_DPI, payload,
                                   self.dialog._orientation_previews[(CACHE_LIMIT,)])
        self.assertIsNotNone(self.dialog.preview_label.orientation_rect())
        self.dialog._apply_preview("3d_minimal", LARGE_DPI, payload)
        self.assertIsNone(self.dialog.preview_label.orientation_rect())


if __name__ == "__main__":
    unittest.main()
