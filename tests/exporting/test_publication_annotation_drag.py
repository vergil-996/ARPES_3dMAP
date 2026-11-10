"""Draggable figure and axis titles: rendering, layers and style drafts."""
import io
import unittest

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.exporting.curve_presentation import Figure1DPresentation
from bandscope.exporting.publication_dialog import (
    LARGE_DPI,
    PublicationExportDialog,
    _draggable_preview_layers,
)
from bandscope.exporting.publication_export import commit_style
from bandscope.exporting.publication_models import (
    OutputOptions,
    load_overrides,
    resolve_style,
    validate_overrides,
)
from bandscope.exporting.publication_renderers import (
    _draw_3d_axes,
    apply_manual_annotation_positions,
    render_1d,
    render_2d,
    render_snapshot,
)
from tests.support.publication import (
    MemoryPublicationSettings,
    _FakeVTKRenderer,
    _make_1d_comparison_snapshot,
    _make_2d_snapshot,
    _make_3d_axes_snapshot,
)


def _visible_annotation(fig, name):
    gid = f"publication-{name}"
    return next(
        artist for artist in fig.texts
        if artist.get_gid() == gid and artist.get_visible()
    )


class AnnotationValidationTests(unittest.TestCase):
    def test_positions_are_clamped_and_z_axis_is_3d_only(self):
        for family in ("1d", "2d", "3d"):
            with self.subTest(family=family):
                result = validate_overrides(family, {
                    "title_cx": 120.0,
                    "xlabel_cy": -5.0,
                    "zlabel_cx": 40.0,
                })
                self.assertEqual(result["title_cx"], 100.0)
                self.assertEqual(result["xlabel_cy"], 0.0)
                self.assertEqual("zlabel_cx" in result, family == "3d")


class AnnotationRenderTests(unittest.TestCase):
    def test_2d_manual_positions_survive_draw_and_defaults_stay_on_axes(self):
        automatic = render_2d(
            _make_2d_snapshot(), resolve_style("2d", "2d_open"), {}, OutputOptions(), 100,
        )
        try:
            self.assertTrue(automatic.axes[0].xaxis.label.get_visible())
            self.assertTrue(automatic.axes[0].yaxis.label.get_visible())
            self.assertFalse([
                artist for artist in automatic.texts
                if artist.get_gid() == "publication-xlabel"
            ])
        finally:
            automatic.clear()

        expected = {
            "title": (0.6225, 0.725),
            "xlabel": (0.42, 0.18),
            "ylabel": (0.12, 0.52),
        }
        overrides = {
            key: value * 100.0
            for name, center in expected.items()
            for key, value in zip((f"{name}_cx", f"{name}_cy"), center)
        }
        figure = render_2d(
            _make_2d_snapshot(), resolve_style("2d", "2d_open"),
            overrides, OutputOptions(), 100,
        )
        try:
            figure.canvas.draw()
            for name, center in expected.items():
                artist = _visible_annotation(figure, name)
                self.assertAlmostEqual(artist.get_position()[0], center[0], places=4)
                self.assertAlmostEqual(artist.get_position()[1], center[1], places=4)
            self.assertFalse(figure.axes[0].xaxis.label.get_visible())
            self.assertFalse(figure.axes[0].yaxis.label.get_visible())
        finally:
            figure.clear()

    def test_text_layers_have_geometry_and_restore_original_artists(self):
        options = OutputOptions(dpi=LARGE_DPI)
        figure = render_2d(
            _make_2d_snapshot(), resolve_style("2d", "2d_boxed"), {}, options, LARGE_DPI,
        )
        try:
            title = next(artist for artist in figure.texts if artist.get_gid() == "publication-title")
            layers = _draggable_preview_layers(figure, options)
            self.assertTrue({"title", "xlabel", "ylabel", "colorbar"} <= set(layers))
            for name in ("title", "xlabel", "ylabel"):
                rect, overlay = layers[name]
                self.assertEqual(len(rect), 4)
                self.assertGreater(rect[2], 0.0)
                self.assertGreater(rect[3], 0.0)
                self.assertGreater(len(overlay), 100)
            self.assertTrue(title.get_visible())
            self.assertTrue(figure.axes[0].xaxis.label.get_visible())
            self.assertTrue(figure.axes[0].yaxis.label.get_visible())
        finally:
            figure.clear()

    def test_1d_presentation_uses_the_same_manual_positions(self):
        figure = render_1d(
            _make_1d_comparison_snapshot(), resolve_style("1d", "1d_open"),
            {"title_cx": 58.0, "title_cy": 76.0, "xlabel_cx": 44.0, "xlabel_cy": 16.0},
            OutputOptions(), 100, presentation=Figure1DPresentation(),
        )
        try:
            self.assertEqual(_visible_annotation(figure, "title").get_position(), (0.58, 0.76))
            self.assertEqual(_visible_annotation(figure, "xlabel").get_position(), (0.44, 0.16))
        finally:
            figure.clear()

    def test_3d_energy_title_keeps_rotation_when_manually_positioned(self):
        snapshot = _make_3d_axes_snapshot()
        figure = Figure(figsize=(3.0, 3.0), dpi=100)
        FigureCanvasAgg(figure)
        ax = figure.add_axes([0.0, 0.0, 1.0, 1.0])
        ax.set_xlim(0.0, 1000.0)
        ax.set_ylim(0.0, 1000.0)
        try:
            _draw_3d_axes(ax, _FakeVTKRenderer(), snapshot, resolve_style("3d", None).params, {})
            original = next(
                artist for artist in ax.texts if artist.get_gid() == "publication-zlabel"
            )
            rotation = original.get_rotation()
            apply_manual_annotation_positions(figure, {"zlabel_cx": 65.0, "zlabel_cy": 35.0})
            moved = _visible_annotation(figure, "zlabel")
            self.assertEqual(moved.get_position(), (0.65, 0.35))
            self.assertEqual(moved.get_rotation(), rotation)
            self.assertFalse(original.get_visible())
        finally:
            figure.clear()


class _Window(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = MemoryPublicationSettings()


class AnnotationDialogTests(unittest.TestCase):
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

    def _install_preview(self):
        options = self.dialog._collect_output_options().with_updates(dpi=LARGE_DPI)
        figure = render_snapshot(
            self.dialog.snapshot, self.dialog._current_style(),
            dict(self.dialog._draft_overrides), options, dpi=LARGE_DPI,
        )
        try:
            buffer = io.BytesIO()
            figure.savefig(buffer, format="png", dpi=LARGE_DPI)
            layers = _draggable_preview_layers(figure, options)
            payload = buffer.getvalue()
        finally:
            figure.clear()
        self.dialog._apply_preview("2d_boxed", LARGE_DPI, payload, layers)
        return layers

    def test_preview_drag_defers_render_and_persists_style_position(self):
        layers = self._install_preview()
        self.assertIn("title", layers)
        self.assertIsNotNone(self.dialog.preview_label.annotation_rect("title"))
        self.dialog.preview_label.set_annotation_center("title", 62.25, 70.5)
        rect = self.dialog.preview_label.annotation_rect("title")
        center = ((rect[0] + rect[2] / 2.0) * 100.0, (rect[1] + rect[3] / 2.0) * 100.0)
        self.dialog.preview_label.annotationMoved.emit("title", *center)
        self.assertAlmostEqual(self.dialog._draft_overrides["title_cx"], center[0])
        self.assertAlmostEqual(self.dialog._draft_overrides["title_cy"], center[1])
        self.assertFalse(self.dialog._debounce.isActive())
        self.dialog.preview_label.annotationDragFinished.emit("title")
        self.assertTrue(self.dialog._debounce.isActive())
        self.assertFalse(self.dialog._big_ready)

        commit_style(
            self.window.settings, "2d", "2d_boxed", self.dialog._draft_overrides,
        )
        saved = load_overrides(self.window.settings, "2d", "2d_boxed")
        self.assertAlmostEqual(saved["title_cx"], center[0], places=2)
        self.assertAlmostEqual(saved["title_cy"], center[1], places=2)
        self.dialog._reset_style_overrides()
        self.assertNotIn("title_cx", self.dialog._draft_overrides)
        self.assertNotIn("title_cy", self.dialog._draft_overrides)

    def test_title_layout_controls_restore_automatic_position(self):
        self.dialog._draft_overrides = {"title_cx": 60.0, "title_cy": 70.0}
        self.dialog._sync_tune_ui_from_draft()
        self.dialog.combo_title_pos.setCurrentIndex(1)
        self.assertNotIn("title_cx", self.dialog._draft_overrides)
        self.assertNotIn("title_cy", self.dialog._draft_overrides)
        self.assertEqual(self.dialog._draft_overrides["title_position"], "bottom")

    def test_hidden_title_and_axis_label_have_no_drag_layer(self):
        self.dialog.chk_title.setChecked(False)
        self.dialog.edit_axis_x.setText("")
        layers = self._install_preview()
        self.assertNotIn("title", layers)
        self.assertNotIn("xlabel", layers)
        self.assertIn("ylabel", layers)
        self.assertIsNone(self.dialog.preview_label.annotation_rect("title"))
        self.assertIsNone(self.dialog.preview_label.annotation_rect("xlabel"))


if __name__ == "__main__":
    unittest.main()
