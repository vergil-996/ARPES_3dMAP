"""Screenshot axis tick controls, persistence and rendered appearance."""
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

    def test_axis_tick_count_validation_applies_to_every_family(self):
        for family in ("1d", "2d", "3d"):
            with self.subTest(family=family):
                self.assertEqual(
                    validate_overrides(family, {"axis_nticks": 7}), {"axis_nticks": 7}
                )
                self.assertEqual(
                    validate_overrides(family, {"axis_nticks": 99}), {"axis_nticks": 8}
                )
                self.assertEqual(
                    validate_overrides(family, {"axis_nticks": 1}), {"axis_nticks": 2}
                )
                self.assertEqual(validate_overrides(family, {"axis_nticks": "bad"}), {})

    def test_axis_tick_count_persists_and_resets_in_every_family(self):
        from tests.support.publication import _make_1d_comparison_snapshot, _make_3d_axes_snapshot

        factories = {
            "1d": _make_1d_comparison_snapshot,
            "2d": _make_2d_snapshot,
            "3d": _make_3d_axes_snapshot,
        }
        with TemporaryDirectory() as directory:
            window = QWidget()
            window.settings = QSettings(str(Path(directory) / "axis-ticks.ini"), QSettings.IniFormat)
            dialog = PublicationExportDialog(window)
            try:
                for family, style_id in (
                    ("1d", "1d_open"), ("2d", "2d_open"), ("3d", "3d_minimal"),
                ):
                    with self.subTest(family=family):
                        dialog.snapshot = factories[family]()
                        dialog._draft_style_id = style_id
                        dialog._draft_overrides = {}
                        dialog._sync_tune_ui_from_draft()
                        default = resolve_style(family, style_id).params.get(
                            "max_major_ticks", 3 if family == "3d" else 5
                        )
                        self.assertEqual(dialog.spin_axis_nticks.value(), default)
                        self.assertEqual(dialog._collect_overrides_from_ui(), {})

                        selected = 7 if default != 7 else 6
                        dialog.spin_axis_nticks.setValue(selected)
                        expected = {"axis_nticks": selected}
                        self.assertEqual(dialog._collect_overrides_from_ui(), expected)
                        commit_style(window.settings, family, style_id, expected)
                        dialog._draft_overrides = load_overrides(window.settings, family, style_id)
                        dialog._sync_tune_ui_from_draft()
                        self.assertEqual(dialog.spin_axis_nticks.value(), selected)
                        self.assertEqual(dialog._collect_overrides_from_ui(), expected)

                        dialog._reset_style_overrides()
                        self.assertEqual(dialog.spin_axis_nticks.value(), default)
                        self.assertEqual(dialog._collect_overrides_from_ui(), {})
            finally:
                dialog._debounce.stop()
                dialog.close()
                dialog.deleteLater()
                window.close()

    def test_axis_tick_count_controls_rendered_2d_and_1d_ticks(self):
        import numpy as np

        from bandscope.exporting.curve_presentation import Figure1DPresentation
        from bandscope.exporting.publication_renderers import nice_ticks, render_1d
        from tests.support.publication import _make_1d_comparison_snapshot

        for target in (2, 7):
            with self.subTest(family="2d", target=target):
                figure = render_2d(
                    _make_2d_snapshot(), resolve_style("2d", "2d_open"),
                    {"axis_nticks": target}, OutputOptions(), 80,
                )
                try:
                    ax = figure.axes[0]
                    for axis in (ax.xaxis, ax.yaxis):
                        lo, hi = axis.get_view_interval()
                        np.testing.assert_allclose(
                            axis.get_major_locator()(), nice_ticks(lo, hi, target)
                        )
                finally:
                    figure.clear()

            for presentation in (None, Figure1DPresentation()):
                with self.subTest(family="1d", target=target, presentation=presentation):
                    figure = render_1d(
                        _make_1d_comparison_snapshot(), resolve_style("1d", "1d_open"),
                        {"axis_nticks": target}, OutputOptions(), 80,
                        presentation=presentation,
                    )
                    try:
                        for axis in (figure.axes[0].xaxis, figure.axes[0].yaxis):
                            lo, hi = axis.get_view_interval()
                            np.testing.assert_allclose(
                                axis.get_major_locator()(), nice_ticks(lo, hi, target)
                            )
                    finally:
                        figure.clear()

    def test_1d_individual_axis_tick_count_still_takes_precedence(self):
        import numpy as np

        from bandscope.exporting.curve_presentation import Figure1DPresentation
        from bandscope.exporting.publication_renderers import nice_ticks, render_1d
        from tests.support.publication import _make_1d_comparison_snapshot

        presentation = Figure1DPresentation(figure={"x_ticks": 6})
        figure = render_1d(
            _make_1d_comparison_snapshot(), resolve_style("1d", "1d_open"),
            {"axis_nticks": 2}, OutputOptions(), 80, presentation=presentation,
        )
        try:
            ax = figure.axes[0]
            np.testing.assert_allclose(ax.xaxis.get_major_locator()(), nice_ticks(*ax.get_xlim(), 6))
            np.testing.assert_allclose(ax.yaxis.get_major_locator()(), nice_ticks(*ax.get_ylim(), 2))
        finally:
            figure.clear()

    def test_3d_axis_tick_count_drives_tick_labels_and_grid(self):
        from matplotlib.figure import Figure

        from bandscope.exporting.publication_renderers import _draw_3d_axes, nice_ticks
        from tests.support.publication import _FakeVTKRenderer, _make_3d_axes_snapshot

        for target in (2, 7):
            with self.subTest(target=target):
                snapshot = _make_3d_axes_snapshot()
                style = resolve_style("3d", "3d_minimal")
                figure = Figure(figsize=(3.0, 3.0), dpi=100)
                ax = figure.add_axes([0.0, 0.0, 1.0, 1.0])
                ax.set_xlim(0.0, 1000.0)
                ax.set_ylim(0.0, 1000.0)
                try:
                    _draw_3d_axes(
                        ax, _FakeVTKRenderer(), snapshot, style.params,
                        {"axis_nticks": target, "show_grid": True},
                    )
                    expected_count = len(nice_ticks(-1.0, 1.0, target))
                    tick_texts = [
                        text for text in ax.texts
                        if text.get_text() not in snapshot.payload["axis_titles"]
                    ]
                    self.assertEqual(len(tick_texts), 3 * expected_count)
                    self.assertEqual(len(ax.lines), 3 + 9 * expected_count)
                finally:
                    figure.clear()

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
