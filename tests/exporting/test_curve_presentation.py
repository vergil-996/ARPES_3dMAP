"""1D 出图外观的身份、科学数据隔离及实际绘制/保存行为。"""
import copy
import io
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

import numpy as np
from PIL import Image

from bandscope.exporting.curve_presentation import (
    Figure1DPresentation, snapshot_curves, ordered_curves, load_presentation,
    save_presentation, load_presets, save_presets, publication_session,
)
from bandscope.exporting.publication_export import render_and_save, _freeze_1d_comparison, _freeze_waterfall
from bandscope.exporting.publication_models import OutputOptions, resolve_style, PUB_CURVE_PALETTE
from bandscope.exporting.publication_renderers import render_snapshot, RenderError, background_kwargs
from tests.support.publication import (
    _make_1d_comparison_snapshot, make_single_curve_snapshot, make_waterfall_snapshot, MemoryPublicationSettings,
)


class CurvePresentationTests(unittest.TestCase):
    def render(self, snapshot, presentation=None, **kwargs):
        figure = render_snapshot(snapshot, resolve_style("1d", kwargs.pop("style", "1d_open")), {},
                                 OutputOptions(**kwargs), dpi=100, presentation=presentation)
        self.addCleanup(figure.clear)
        return figure

    def test_legacy_and_empty_presentation_keep_scientific_lines(self):
        for snapshot in (make_single_curve_snapshot(), _make_1d_comparison_snapshot(), make_waterfall_snapshot()):
            before = self.render(snapshot)
            after = self.render(snapshot, Figure1DPresentation())
            self.assertEqual(len(before.axes[0].lines), len(after.axes[0].lines))
            for old, new in zip(before.axes[0].lines, after.axes[0].lines):
                np.testing.assert_equal(old.get_xdata(), new.get_xdata())
                np.testing.assert_equal(old.get_ydata(), new.get_ydata())
                self.assertEqual(old.get_color(), new.get_color())
                self.assertEqual(old.get_linewidth(), new.get_linewidth())

    def test_same_names_and_reorder_bind_styles_and_base_role_to_ids(self):
        snapshot = _make_1d_comparison_snapshot()
        for index, curve in enumerate(snapshot.payload["curves"]):
            curve.update(curve_id=f"id-{index}", label="同名", is_base=index == 0)
        p = Figure1DPresentation(curves={"id-0": {"color": "#123456", "label": "修改后的基准"},
                                         "id-1": {"color": "#654321", "linewidth": 2.4}}, order=["id-1", "id-0"])
        fig = self.render(snapshot, p)
        lines = fig.axes[0].lines
        self.assertEqual([v.get_gid() for v in lines], ["id-1", "id-0"])
        self.assertEqual([v.get_color() for v in lines], ["#654321", "#123456"])
        self.assertEqual(lines[0].get_linewidth(), 2.4)
        self.assertAlmostEqual(lines[1].get_linewidth(), 1.2)
        self.assertEqual([v.get_text() for v in fig.axes[0].get_legend().get_texts()], ["同名", "修改后的基准"])

    def test_explicit_width_overrides_base_emphasis_and_curve_overrides_defaults(self):
        p = Figure1DPresentation(defaults={"linewidth": 2, "alpha": 0.3, "marker": "s"},
                                 curves={"main": {"linewidth": 3, "marker_filled": False, "markevery": 5}})
        line = self.render(make_single_curve_snapshot(), p).axes[0].lines[0]
        self.assertEqual(line.get_linewidth(), 3)
        self.assertEqual(line.get_alpha(), 0.3)
        self.assertEqual(line.get_marker(), "s")
        self.assertEqual(line.get_markerfacecolor(), "none")
        self.assertEqual(line.get_markevery(), 5)

    def test_hiding_does_not_shift_palette_or_mutate_arrays(self):
        snapshot = _make_1d_comparison_snapshot()
        original = copy.deepcopy(snapshot.payload)
        p = Figure1DPresentation(curves={"legacy:0": {"visible": False}})
        line = self.render(snapshot, p).axes[0].lines[0]
        self.assertEqual(line.get_color(), PUB_CURVE_PALETTE[1])
        np.testing.assert_equal(line.get_ydata(), original["curves"][1]["y"])
        for old, new in zip(original["curves"], snapshot.payload["curves"]):
            np.testing.assert_equal(old["x"], new["x"])
            np.testing.assert_equal(old["y"], new["y"])

    def test_log_values_and_nan_gaps_are_not_transformed_again(self):
        snapshot = make_single_curve_snapshot()
        snapshot.page_kind = "log_curve"
        snapshot.payload["curve"]["y"] = np.linspace(-4, 2, 200)
        snapshot.payload["curve"]["y"][80:90] = np.nan
        line = self.render(snapshot, Figure1DPresentation(defaults={"marker": "o", "markevery": 9})).axes[0].lines[0]
        np.testing.assert_equal(line.get_ydata(), snapshot.payload["curve"]["y"])

    def test_all_hidden_all_invalid_and_invisible_plot_mode_fail(self):
        snapshot = make_single_curve_snapshot()
        with self.assertRaisesRegex(RenderError, "至少显示"):
            self.render(snapshot, Figure1DPresentation(curves={"main": {"visible": False}}))
        snapshot.payload["curve"]["y"][:] = np.nan
        with self.assertRaisesRegex(RenderError, "至少显示"):
            self.render(snapshot, Figure1DPresentation())
        with self.assertRaisesRegex(RenderError, "线条或标记"):
            self.render(make_single_curve_snapshot(), Figure1DPresentation(defaults={"linestyle": "none"}))

    def test_waterfall_hidden_or_nan_slots_keep_original_offsets(self):
        snapshot = make_waterfall_snapshot()
        snapshot.payload["curves"][1] = np.nan
        p = Figure1DPresentation(curves={"X:0": {"visible": False}, "X:8": {"color": "#112233"}},
                                 order=["X:8", "X:4"], figure={"waterfall_labels": "none"})
        ax = self.render(snapshot, p).axes[0]
        self.assertEqual([v.get_gid() for v in ax.lines], ["X:8", "X:4", "X:6"])
        for line in ax.lines:
            row = int(line.get_gid().split(":")[1]) // 2
            np.testing.assert_equal(line.get_xdata(), snapshot.payload["curves"][row] + snapshot.payload["curve_offsets"][row])
        self.assertEqual(len(ax.texts), 0)
        self.assertEqual(ax.get_xlim(), (-0.1, 5.9))

    def test_waterfall_automatic_labels_do_not_overlap(self):
        snapshot = make_waterfall_snapshot(40)
        p = Figure1DPresentation(figure={"waterfall_labels": "auto", "waterfall_label_size": 8})
        fig = self.render(snapshot, p)
        texts = [v for v in fig.axes[0].texts if v.get_visible()]
        self.assertGreater(len(texts), 1)
        self.assertLess(len(texts), 40)
        boxes = [v.get_window_extent(fig.canvas.get_renderer()) for v in texts]
        for i, box in enumerate(boxes):
            self.assertFalse(any(box.overlaps(other) for other in boxes[i + 1:]))
        self.assertEqual(len(fig.axes[0].lines), 40)

    def test_manual_axes_reverse_grid_and_reference_annotations_preserve_limits(self):
        p = Figure1DPresentation(figure={"xlim": [-1, 1], "ylim": [0, 2], "x_reverse": True,
                                         "x_minor": True, "major_grid": True},
                                 annotations=[{"kind": "vline", "x": 100}, {"kind": "hspan", "y": 0.2, "y2": 0.4},
                                              {"kind": "arrow", "x": 0, "y": 1, "x2": 0.5, "y2": 1.5, "text": "注记"}])
        ax = self.render(make_single_curve_snapshot(), p).axes[0]
        self.assertEqual(ax.get_xlim(), (1, -1))
        self.assertEqual(ax.get_ylim(), (0, 2))
        self.assertTrue(any(v.get_visible() for v in ax.get_xgridlines()))
        self.assertEqual(len(ax.patches), 1)
        self.assertTrue(any(v.get_text() == "注记" for v in ax.texts))

    def test_scientific_exponent_survives_repeated_actual_draws(self):
        snapshot = make_single_curve_snapshot()
        snapshot.payload["curve"]["x"] = np.linspace(0, 1e-4, 200)
        for p in (None, Figure1DPresentation()):
            fig = self.render(snapshot, p)
            for _ in range(2):
                fig.canvas.draw()
                self.assertIn("1e", fig.axes[0].xaxis.get_offset_text().get_text())
                self.assertIn("4", fig.axes[0].xaxis.get_offset_text().get_text())

    def test_legend_order_and_fixed_external_positions(self):
        snapshot = _make_1d_comparison_snapshot()
        for position in ("outside_top", "outside_bottom", "outside_left", "outside_right"):
            p = Figure1DPresentation(legend_order=["legacy:1", "legacy:0"],
                                     figure={"legend_position": position, "legend_columns": 1})
            fig = self.render(snapshot, p, width_mm=183, height_mm=100)
            legend = fig.axes[0].get_legend()
            self.assertEqual(legend.get_texts()[0].get_text(), "曲线B")
            bbox = legend.get_window_extent(fig.canvas.get_renderer())
            self.assertGreaterEqual(bbox.x0, -1)
            self.assertLessEqual(bbox.x1, fig.bbox.width + 1)
            self.assertGreaterEqual(bbox.y0, -1)
            self.assertLessEqual(bbox.y1, fig.bbox.height + 1)

    def test_external_legend_avoids_scientific_exponent_and_axis_labels(self):
        snapshot = _make_1d_comparison_snapshot()
        for curve in snapshot.payload["curves"]:
            curve["y"] = curve["y"] * 1e5
        for position, decoration in (("outside_top", "offset"), ("outside_bottom", "xlabel"),
                                      ("outside_left", "ylabel")):
            fig = self.render(snapshot, Figure1DPresentation(figure={"legend_position": position}))
            ax = fig.axes[0]
            renderer = fig.canvas.get_renderer()
            legend = ax.get_legend().get_window_extent(renderer)
            artist = ax.yaxis.get_offset_text() if decoration == "offset" else ax.xaxis.label if decoration == "xlabel" else ax.yaxis.label
            self.assertFalse(legend.overlaps(artist.get_window_extent(renderer)))

    def test_palette_and_style_identity_survives_more_than_28_curves(self):
        snapshot = _make_1d_comparison_snapshot()
        source = snapshot.payload["curves"][0]
        snapshot.payload["curves"] = [dict(source, curve_id=f"id-{i}", label=f"Curve {i}") for i in range(32)]
        p = Figure1DPresentation(order=[f"id-{i}" for i in range(31, -1, -1)], figure={"legend_position": "none"})
        lines = self.render(snapshot, p).axes[0].lines
        self.assertEqual(lines[0].get_gid(), "id-31")
        self.assertEqual(lines[-1].get_gid(), "id-0")
        self.assertEqual(lines[-1].get_color(), PUB_CURVE_PALETTE[0])
        self.assertAlmostEqual(lines[-1].get_linewidth(), 1.2)

    def test_png_and_pdf_save_background_and_keep_requested_size(self):
        snapshot = make_single_curve_snapshot()
        p = Figure1DPresentation(figure={"canvas_color": "#112233", "ink_color": "#FFFFFF"})
        with TemporaryDirectory() as directory:
            png = Path(directory) / "figure.png"
            pdf = Path(directory) / "figure.pdf"
            for path, fmt in ((png, "png"), (pdf, "pdf")):
                render_and_save(snapshot, resolve_style("1d", "1d_open"), {}, OutputOptions(dpi=100, fmt=fmt), str(path), presentation=p)
                self.assertGreater(path.stat().st_size, 100)
            with Image.open(png) as image:
                self.assertEqual(image.getpixel((0, 0))[:3], (17, 34, 51))
                target = OutputOptions(dpi=100).target_pixels("1d")
                self.assertLessEqual(abs(image.width - target[0]), 1)
                self.assertLessEqual(abs(image.height - target[1]), 1)
            render_and_save(snapshot, resolve_style("1d", "1d_open"), {}, OutputOptions(dpi=100, transparent=True), str(png), presentation=p)
            with Image.open(png) as image:
                self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_validation_and_presets_exclude_page_scientific_content(self):
        p = Figure1DPresentation.from_dict({"defaults": {"linewidth": float("nan"), "alpha": 2, "color": "bad", "marker": "o"},
                                            "curves": {"id": {"label": "scientific name", "visible": False}},
                                            "figure": {"xlim": [2, 1], "ylim": [0, 5], "title_size": 10},
                                            "annotations": [{"kind": "vline", "x": 1}]})
        self.assertEqual(p.defaults, {"marker": "o"})
        settings = MemoryPublicationSettings()
        save_presentation(settings, "1d_open", p)
        loaded = load_presentation(settings, "1d_open")
        self.assertEqual(loaded.curves, {})
        self.assertNotIn("ylim", loaded.figure)
        self.assertEqual(loaded.annotations, [])
        p.apply_preset({"defaults": {"linewidth": 2}, "figure": {"title_size": 12}})
        self.assertEqual(p.figure["ylim"], [0, 5])
        self.assertFalse(p.curves["id"]["visible"])
        self.assertEqual(len(p.annotations), 1)
        save_presets(settings, {"my preset": p.preset()})
        self.assertEqual(load_presets(settings)["my preset"]["defaults"], {"linewidth": 2.0})

    def test_freeze_comparison_keeps_identity_when_invalid_rows_are_skipped(self):
        snapshot = _make_1d_comparison_snapshot()
        context = {"curves": [{"x_data": [], "y_data": []},
                               {"x_data": [2, 1], "y_data": [4, 3], "curve_id": "independent", "label": "same"}]}
        _freeze_1d_comparison(None, snapshot, context)
        curve = snapshot_curves(snapshot)[0]
        self.assertEqual(curve.curve_id, "independent")
        self.assertEqual(curve.palette_slot, 1)
        self.assertFalse(curve.is_base)
        np.testing.assert_equal(curve.x, [1, 2])

    def test_waterfall_freeze_identity_uses_absolute_samples(self):
        snapshot = make_waterfall_snapshot(3)
        context = dict(snapshot.payload, sample_indices=np.array([10, 14, 20]), k_axis_key="Y")
        _freeze_waterfall(None, snapshot, context)
        self.assertEqual([v.curve_id for v in snapshot_curves(snapshot)], ["Y:10", "Y:14", "Y:20"])

    def test_session_page_cleanup_does_not_affect_another_page(self):
        class Window:
            pass
        session = publication_session(Window())
        session.pages.update(a={}, b={})
        session.forget_page("a")
        self.assertEqual(set(session.pages), {"b"})
        session.clear()
        self.assertEqual(session.pages, {})
