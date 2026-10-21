"""Colorbars move as an annotated overlay without changing the scientific panel."""
import unittest

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from bandscope.exporting.publication_models import OutputOptions, resolve_style, validate_overrides
from bandscope.exporting.publication_renderers import (
    _colorbar_layout_for,
    _compute_rects,
    _draw_colorbar,
    _fit_layout_3d,
    _fit_manual_colorbar_to_canvas,
    _initial_margins,
    _measure_overflow,
    build_display_cmap,
    colorbar_preview_geometry,
    compute_level_info,
    render_snapshot,
)
from tests.support.publication import _make_2d_snapshot


class ColorbarPositionTests(unittest.TestCase):
    def setUp(self):
        self.figures = []

    def tearDown(self):
        for figure in self.figures:
            figure.clear()

    def _render(self, overrides, dpi=150):
        snapshot = _make_2d_snapshot()
        figure = render_snapshot(
            snapshot, resolve_style("2d", "2d_boxed"), overrides,
            OutputOptions(width_mm=89., dpi=dpi), dpi=dpi,
        )
        self.figures.append(figure)
        return figure

    def _assert_complete_colorbar_inside(self, figure):
        figure.canvas.draw()
        axis = next(axis for axis in figure.axes
                    if axis.get_label() == "publication-colorbar")
        box = axis.get_tightbbox(figure.canvas.get_renderer())
        canvas = figure.bbox
        self.assertGreaterEqual(box.x0, canvas.x0 - .1)
        self.assertGreaterEqual(box.y0, canvas.y0 - .1)
        self.assertLessEqual(box.x1, canvas.x1 + .1)
        self.assertLessEqual(box.y1, canvas.y1 + .1)
        return axis

    def test_centers_accept_fractional_and_legacy_integer_values(self):
        for family in ("2d", "3d"):
            with self.subTest(family=family):
                self.assertEqual(validate_overrides(family, {
                    "colorbar_cx": 42.125, "colorbar_cy": 30,
                }), {"colorbar_cx": 42.12, "colorbar_cy": 30.})
                self.assertEqual(validate_overrides(family, {
                    "colorbar_cx": -5, "colorbar_cy": 105,
                }), {"colorbar_cx": 0., "colorbar_cy": 100.})
        self.assertEqual(validate_overrides("1d", {"colorbar_cx": 50.}), {})

    def test_free_centers_do_not_resize_main_rect_for_any_side(self):
        for family in ("2d", "3d"):
            style = resolve_style(family, None)
            for side in ("left", "right", "top", "bottom"):
                with self.subTest(family=family, side=side):
                    overrides = {"colorbar_position": side, "colorbar_length": 60}
                    baseline = _colorbar_layout_for(style.params, overrides, family)
                    moved = _colorbar_layout_for(style.params, dict(
                        overrides, colorbar_cx=42.5, colorbar_cy=57.5,
                    ), family)
                    margins = _initial_margins(style.params)
                    original_main, original_bar = _compute_rects(89., 85., margins, baseline)
                    moved_main, moved_bar = _compute_rects(89., 85., margins, moved)
                    np.testing.assert_allclose(moved_main, original_main, atol=1e-12)
                    np.testing.assert_allclose(
                        np.subtract(moved_bar[2:], moved_bar[:2]),
                        np.subtract(original_bar[2:], original_bar[:2]), atol=1e-12,
                    )

    def test_rendered_center_and_panel_are_stable_inside_figure(self):
        original = self._render({})
        moved = self._render({"colorbar_cx": 52.25, "colorbar_cy": 50.5})
        np.testing.assert_allclose(
            original.axes[0].get_position(original=True).bounds,
            moved.axes[0].get_position(original=True).bounds, atol=1e-10,
        )
        rect, bounds = colorbar_preview_geometry(moved)
        np.testing.assert_allclose(
            ((rect[0] + rect[2] / 2.) * 100., (rect[1] + rect[3] / 2.) * 100.),
            (52.25, 50.5), atol=1e-8,
        )
        self.assertGreater(bounds[2], rect[2])  # Includes ticks and the intensity label.
        self._assert_complete_colorbar_inside(moved)

    def test_all_sides_and_corners_keep_labels_inside_without_resizing_panel(self):
        for side in ("left", "right", "top", "bottom"):
            original = self._render({"colorbar_position": side})
            for x, y in ((0., 0.), (100., 100.)):
                with self.subTest(side=side, center=(x, y)):
                    moved = self._render({
                        "colorbar_position": side, "colorbar_cx": x, "colorbar_cy": y,
                    })
                    self._assert_complete_colorbar_inside(moved)
                    np.testing.assert_allclose(
                        original.axes[0].get_position(original=True).bounds,
                        moved.axes[0].get_position(original=True).bounds, atol=1e-10,
                    )

    def test_geometry_is_normalized_and_dpi_independent(self):
        overrides = {"colorbar_position": "bottom", "colorbar_length": 60,
                     "colorbar_cx": 57.25, "colorbar_cy": 35.5,
                     "colorbar_tick_mode": "lowhigh"}
        low = self._render(overrides, dpi=100)
        high = self._render(overrides, dpi=600)
        low_rect, low_bounds = colorbar_preview_geometry(low)
        high_rect, high_bounds = colorbar_preview_geometry(high)
        np.testing.assert_allclose(low_rect, high_rect, atol=1e-10)
        for bounds in (low_bounds, high_bounds):
            self.assertTrue(all(0. <= value <= 1. for value in bounds))
        self.assertGreater(low_bounds[3], low_rect[3])
        self.assertGreater(high_bounds[3], high_rect[3])

    def test_hidden_colorbar_has_no_drag_geometry(self):
        self.assertIsNone(colorbar_preview_geometry(self._render({"colorbar_visible": False})))

    def test_3d_layout_overlay_moves_without_changing_image_position(self):
        style = resolve_style("3d", "3d_minimal")
        positions = []
        for overrides in ({}, {"colorbar_cx": 40., "colorbar_cy": 45.}):
            figure = Figure(figsize=(89. / 25.4, 85. / 25.4), dpi=150)
            FigureCanvasAgg(figure)
            self.figures.append(figure)
            image = figure.add_axes([.15, .15, .60, .65])
            image.set_axis_off()
            bar = figure.add_axes([.8, .15, .05, .65])
            info = compute_level_info(np.arange(100.), (0., 50., 100.))
            _draw_colorbar(
                figure, bar, build_display_cmap("magma", info), info, (0., 99.),
                "right", "values", 3, False, "Intensity (a.u.)", style.params,
            )
            layout = _colorbar_layout_for(style.params, overrides, "3d")
            _fit_layout_3d(figure, image, bar, 89., 85., _initial_margins(style.params), layout)
            _fit_manual_colorbar_to_canvas(figure, overrides)
            positions.append(image.get_position(original=True).bounds)
            self._assert_complete_colorbar_inside(figure)
        np.testing.assert_allclose(positions[0], positions[1], atol=1e-10)

    def test_hidden_image_axes_do_not_create_phantom_layout_overflow(self):
        figure = Figure(figsize=(3., 3.), dpi=150)
        FigureCanvasAgg(figure)
        self.figures.append(figure)
        axis = figure.add_axes([.2, .2, .6, .6])
        axis.imshow(np.zeros((304, 372)), extent=(0, 372, 0, 304))
        axis.set_axis_off()
        self.assertEqual(_measure_overflow(figure, [axis]), {
            "left": 0., "right": 0., "bottom": 0., "top": 0.,
        })
        # Explicit scientific annotations are still painted when axes are off.
        axis.text(-.2, .5, "custom axis label", transform=axis.transAxes)
        self.assertGreater(_measure_overflow(figure, [axis])["left"], 0.)

    def test_individually_hidden_axis_decorations_are_not_measured(self):
        figure = Figure(figsize=(3., 3.), dpi=150)
        FigureCanvasAgg(figure)
        self.figures.append(figure)
        axis = figure.add_axes([.2, .2, .6, .6])
        axis.set_xlabel("hidden xlabel")
        axis.set_ylabel("hidden ylabel")
        axis.xaxis.set_visible(False)
        axis.yaxis.set_visible(False)
        self.assertEqual(_measure_overflow(figure, [axis]), {
            "left": 0., "right": 0., "bottom": 0., "top": 0.,
        })


if __name__ == "__main__":
    unittest.main()
