"""2D screenshot tick controls, persistence and rendered appearance."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from matplotlib.colors import to_hex
from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.exporting.publication_dialog import PublicationExportDialog
from bandscope.exporting.publication_export import commit_style
from bandscope.exporting.publication_models import (
    OutputOptions, load_overrides, resolve_style, validate_overrides,
)
from bandscope.exporting.publication_renderers import render_2d
from tests.support.publication import _make_2d_snapshot


class PublicationTickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_controls_persist_and_reset_for_each_style(self):
        with TemporaryDirectory() as directory:
            window = QWidget()
            window.settings = QSettings(str(Path(directory) / "ticks.ini"), QSettings.IniFormat)
            dialog = PublicationExportDialog(window)
            try:
                dialog.snapshot = _make_2d_snapshot()
                dialog._load_output_options_ui()
                for style_id in ("2d_open", "2d_boxed", "2d_topbar"):
                    dialog._draft_style_id = style_id
                    dialog._draft_overrides = {}
                    dialog._sync_tune_ui_from_draft()
                    self.assertEqual(dialog._collect_overrides_from_ui(), {})
                    default = resolve_style("2d", style_id).params["tick_direction"]
                    direction = "in" if default == "out" else "out"
                    dialog.combo_tick_direction.setCurrentIndex(dialog.combo_tick_direction.findData(direction))
                    dialog.combo_tick_color.setCurrentIndex(1)
                    expected = {"tick_direction": direction, "tick_color": "#ffffff"}
                    self.assertEqual(dialog._collect_overrides_from_ui(), expected)
                    commit_style(window.settings, "2d", style_id, expected)
                    dialog._draft_overrides = load_overrides(window.settings, "2d", style_id)
                    dialog._sync_tune_ui_from_draft()
                    self.assertEqual(dialog._collect_overrides_from_ui(), expected)
                    dialog._reset_style_overrides()
                    self.assertEqual(dialog._collect_overrides_from_ui(), {})
                for family in ("1d", "2d", "3d"):
                    dialog.snapshot.view_family = family
                    dialog._update_tune_availability()
                    self.assertEqual(dialog.tick_row.isHidden(), family != "2d")
            finally:
                dialog._debounce.stop()
                dialog.close()
                dialog.deleteLater()
                window.close()

    def test_validation_limits_options_to_2d(self):
        options = {"tick_direction": "in", "tick_color": "#ffffff"}
        self.assertEqual(validate_overrides("2d", options), options)
        for family in ("1d", "3d"):
            self.assertEqual(validate_overrides(family, options), {})
        self.assertEqual(validate_overrides("2d", {"tick_direction": "invalid", "tick_color": "red"}), {})

    def test_rendered_ticks_change_without_changing_labels_or_colorbar(self):
        for style_id in ("2d_open", "2d_boxed", "2d_topbar"):
            for direction in ("in", "out"):
                for color in ("#000000", "#ffffff"):
                    with self.subTest(style=style_id, direction=direction, color=color):
                        figure = render_2d(_make_2d_snapshot(), resolve_style("2d", style_id),
                                           {"tick_direction": direction, "tick_color": color}, OutputOptions(), 80)
                        try:
                            ax = figure.axes[0]
                            for axis in (ax.xaxis, ax.yaxis):
                                for tick in axis.get_major_ticks():
                                    self.assertEqual(tick._tickdir, direction)
                                    for line in (tick.tick1line, tick.tick2line):
                                        self.assertEqual(to_hex(line.get_color()), color)
                                    self.assertEqual(to_hex(tick.label1.get_color()), "#000000")
                                self.assertEqual(to_hex(axis.label.get_color()), "#000000")
                            for tick in figure.axes[1].yaxis.get_major_ticks():
                                self.assertEqual(to_hex(tick.tick1line.get_color()), "#000000")
                        finally:
                            figure.clear()
