"""Direction markers retain camera projection angles and choose the requested half-planes."""
import unittest

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Circle, FancyArrowPatch

from bandscope.exporting.publication_models import (
    load_overrides, resolve_style, save_overrides, validate_overrides,
)
from bandscope.exporting.publication_renderers import (
    _draw_3d_orientation,
    _orientation_axis_order,
    _orientation_axis_vectors,
    default_orientation_center,
    orientation_rect,
)
from tests.support.publication import MemoryPublicationSettings, _make_3d_axes_snapshot


def _camera(position, up=(0.0, 0.0, 1.0), focal=(0.0, 0.0, 0.0)):
    return {"position": position, "focal_point": focal, "view_up": up}


def _camera_projected_vectors(camera):
    """Project world unit axes independently of the marker's implementation."""
    view = np.asarray(camera["focal_point"]) - np.asarray(camera["position"])
    view = view / np.linalg.norm(view)
    right = np.cross(view, camera["view_up"])
    right = right / np.linalg.norm(right)
    up = np.cross(right, view)
    projection = np.stack((right, up, -view))
    projected = []
    for slot, axis in enumerate(_orientation_axis_order(camera)):
        vector = projection[:, axis]
        projected.append(vector if vector[slot] >= 0.0 else -vector)
    return np.asarray(projected)


class OrientationAxisOrderTests(unittest.TestCase):
    def test_front_back_and_upside_down_use_the_same_axis_roles(self):
        for camera in (
            _camera((0.0, -10.0, 0.0)),
            _camera((0.0, 10.0, 0.0)),
            _camera((0.0, 10.0, 0.0), (0.0, 0.0, -1.0)),
        ):
            with self.subTest(camera=camera):
                self.assertEqual(_orientation_axis_order(camera), (0, 2, 1))

    def test_axis_aligned_views_and_roll_reassign_labels(self):
        cases = (
            (_camera((10.0, 0.0, 0.0)), (1, 2, 0)),
            (_camera((0.0, 0.0, 10.0), (0.0, 1.0, 0.0)), (0, 1, 2)),
            (_camera((0.0, -10.0, 0.0), (1.0, 0.0, 0.0)), (2, 0, 1)),
        )
        for camera, expected in cases:
            with self.subTest(camera=camera):
                self.assertEqual(_orientation_axis_order(camera), expected)

    def test_isometric_tie_is_stable_under_translation_and_distance(self):
        self.assertEqual(_orientation_axis_order(_camera((1.0, 1.0, 1.0))), (0, 2, 1))
        self.assertEqual(
            _orientation_axis_order(_camera((102.0, 103.0, 104.0), focal=(2.0, 3.0, 4.0))),
            (0, 2, 1),
        )

    def test_degenerate_camera_still_assigns_every_axis_once(self):
        for camera in (
            _camera((0.0, 0.0, 10.0)),  # view-up is parallel to the view direction
            _camera((0.0, 0.0, 0.0)),
        ):
            with self.subTest(camera=camera):
                order = _orientation_axis_order(camera)
                self.assertEqual(sorted(order), [0, 1, 2])
                self.assertEqual(order, _orientation_axis_order(camera))


class OrientationProjectionTests(unittest.TestCase):
    def test_vectors_keep_camera_slopes_and_point_into_required_half_planes(self):
        for camera in (
            _camera((4.0, -7.0, 3.0)),
            _camera((-4.0, 7.0, -3.0)),
            _camera((4.0, -7.0, 3.0), (1.0, 0.0, 0.0)),
            _camera((6.0, 3.0, 4.0)),
        ):
            with self.subTest(camera=camera):
                vectors = _orientation_axis_vectors(camera)
                np.testing.assert_allclose(vectors, _camera_projected_vectors(camera), atol=1e-12)
                np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-12)
                self.assertGreater(vectors[0, 0], 0.0)
                self.assertGreater(vectors[1, 1], 0.0)
                self.assertGreater(vectors[2, 2], 0.0)

    def test_projection_is_shortened_when_an_axis_points_toward_the_viewer(self):
        # Looking along (3,4,0), X supplies the horizontal role and Y the depth role.
        vectors = _orientation_axis_vectors(_camera((3.0, 4.0, 0.0)))
        np.testing.assert_allclose(vectors, ((0.8, 0.0, -0.6), (0.0, 1.0, 0.0),
                                             (0.6, 0.0, 0.8)), atol=1e-12)
        np.testing.assert_allclose(np.linalg.norm(vectors[:, :2], axis=1), (0.8, 1.0, 0.6))

    def test_degenerate_camera_has_finite_usable_vectors(self):
        for camera in (_camera((0.0, 0.0, 0.0)), _camera((0.0, 0.0, 10.0))):
            with self.subTest(camera=camera):
                vectors = _orientation_axis_vectors(camera)
                self.assertEqual(vectors.shape, (3, 3))
                self.assertTrue(np.all(np.isfinite(vectors)))
                np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-12)


class OrientationPositionTests(unittest.TestCase):
    def test_center_overrides_are_only_accepted_for_3d_and_keep_precision(self):
        values = {"orientation_cx": 71.2345, "orientation_cy": 42.6789}
        self.assertEqual(validate_overrides("3d", values),
                         {"orientation_cx": 71.23, "orientation_cy": 42.68})
        for family in ("1d", "2d"):
            self.assertEqual(validate_overrides(family, values), {})
        self.assertEqual(validate_overrides("3d", {
            "orientation_cx": -4, "orientation_cy": 105,
        }), {"orientation_cx": 0., "orientation_cy": 100.})

    def test_position_is_saved_even_when_marker_is_disabled(self):
        settings = MemoryPublicationSettings()
        overrides = {"show_orientation": False, "orientation_cx": 72.25,
                     "orientation_cy": 35.75}
        save_overrides(settings, "3d", "3d_minimal", overrides)
        self.assertEqual(load_overrides(settings, "3d", "3d_minimal"), overrides)
        self.assertEqual(load_overrides(settings, "3d", "3d_boxed"), {})

    def test_missing_position_keeps_original_auto_placement(self):
        np.testing.assert_allclose(
            orientation_rect((10., 9., 73., 78.), 89., 85., {}),
            (11. / 89., 10. / 85., 15. / 89., 15. / 85.), atol=1e-12,
        )

    def test_default_center_does_not_use_explicit_position(self):
        params = resolve_style("3d", "3d_minimal").params
        auto = default_orientation_center(params, {}, 89., 85.)
        requested = default_orientation_center(
            params, {"orientation_cx": 99., "orientation_cy": 1.}, 89., 85.,
        )
        np.testing.assert_allclose(requested, auto, atol=1e-12)
        self.assertLess(auto[0], 30.)
        self.assertLess(auto[1], 30.)


class OrientationDrawingTests(unittest.TestCase):
    def setUp(self):
        self.figures = []
        self.snapshot = _make_3d_axes_snapshot()
        self.params = dict(resolve_style("3d", "3d_minimal").params)
        self.params["font_family"] = "DejaVu Sans"
        self.main_rect = (10.0, 9.0, 73.0, 78.0)

    def tearDown(self):
        for figure in self.figures:
            figure.clear()

    def _draw(self, *, dpi=150, overrides=None, main_rect=None):
        figure = Figure(figsize=(89.0 / 25.4, 85.0 / 25.4), dpi=dpi)
        FigureCanvasAgg(figure)
        figure.patch.set_alpha(0.0)
        self.figures.append(figure)
        axes = _draw_3d_orientation(
            figure, self.snapshot, self.params, overrides or {},
            main_rect or self.main_rect, 89.0, 85.0,
        )
        figure.canvas.draw()
        return figure, axes

    def _assert_projected_directions(self, axes):
        arrows = {patch.get_gid(): patch for patch in axes.patches
                  if patch.get_gid() in ("orientation-horizontal", "orientation-vertical",
                                         "orientation-depth")}
        self.assertEqual(set(arrows), {
            "orientation-horizontal", "orientation-vertical", "orientation-depth",
        })
        projected = _camera_projected_vectors(self.snapshot.camera)
        magnifications = []
        for slot, name in enumerate(("orientation-horizontal", "orientation-vertical",
                                     "orientation-depth")):
            artist = arrows[name]
            expected = projected[slot, :2]
            if np.linalg.norm(expected) < 1e-10:
                self.assertEqual(name, "orientation-depth")
                self.assertIsInstance(artist, Circle)
                self.assertTrue(any(line.get_gid() == "orientation-depth-dot" for line in axes.lines))
                continue
            self.assertIsInstance(artist, FancyArrowPatch)
            start, end = np.asarray(artist._posA_posB)
            actual = end - start
            # Preserve both projection slope and the chosen end of the original axis.
            self.assertAlmostEqual(actual[0] * expected[1] - actual[1] * expected[0], 0.0)
            self.assertGreater(np.dot(actual, expected), 0.0)
            magnifications.append(np.linalg.norm(actual) / np.linalg.norm(expected))
        np.testing.assert_allclose(magnifications, magnifications[0], atol=1e-12)

    def test_arrows_keep_projection_and_half_planes_with_reversed_coordinates_and_roll(self):
        for descending in (False, True):
            for camera in (
                _camera((0.0, -10.0, 0.0)),
                _camera((0.0, 10.0, 0.0), (0.0, 0.0, -1.0)),
                _camera((0.0, -10.0, 0.0), (1.0, 0.0, 0.0)),
                _camera((4.0, -7.0, 3.0)),
                _camera((-4.0, 7.0, -3.0)),
            ):
                with self.subTest(descending=descending, camera=camera):
                    self.snapshot.camera = camera
                    self.snapshot.coords = {
                        key: np.linspace(1.0, -1.0, 10) if descending
                        else np.linspace(-1.0, 1.0, 10)
                        for key in ("X", "Y", "E")
                    }
                    _, axes = self._draw()
                    self._assert_projected_directions(axes)

    def test_non_axis_aligned_camera_changes_the_visible_arrow_slopes(self):
        slopes = []
        for camera in (_camera((4.0, -7.0, 3.0)), _camera((4.0, -7.0, 6.0))):
            self.snapshot.camera = camera
            _, axes = self._draw()
            self._assert_projected_directions(axes)
            arrow = next(patch for patch in axes.patches
                         if patch.get_gid() == "orientation-horizontal")
            start, end = np.asarray(arrow._posA_posB)
            vector = end - start
            slopes.append(vector[1] / vector[0])
        self.assertGreater(abs(slopes[1] - slopes[0]), 0.05)

    def test_end_on_depth_is_a_circle_and_dot_instead_of_a_fabricated_slanted_arrow(self):
        self.snapshot.camera = _camera((0.0, -10.0, 0.0))
        _, axes = self._draw()
        depth = next(patch for patch in axes.patches if patch.get_gid() == "orientation-depth")
        self.assertIsInstance(depth, Circle)
        dot = next(line for line in axes.lines if line.get_gid() == "orientation-depth-dot")
        np.testing.assert_allclose((dot.get_xdata()[0], dot.get_ydata()[0]), depth.center)
        self.assertEqual(len(axes.texts), 3)

    def test_coincident_axis_projections_keep_their_geometry_but_separate_labels(self):
        self.snapshot.camera = _camera((1.0, 1.0, 0.0))
        figure, axes = self._draw()
        self._assert_projected_directions(axes)
        arrows = {patch.get_gid(): patch for patch in axes.patches
                  if isinstance(patch, FancyArrowPatch)}
        horizontal = np.asarray(arrows["orientation-horizontal"]._posA_posB)
        depth = np.asarray(arrows["orientation-depth"]._posA_posB)
        np.testing.assert_allclose(horizontal[1] - horizontal[0], depth[1] - depth[0], atol=1e-12)
        labels = {text.get_text(): text for text in axes.texts}
        self.assertEqual(set(labels), {r"$k_x$", r"$k_y$", r"$E$"})
        renderer = figure.canvas.get_renderer()
        self.assertFalse(labels[r"$k_x$"].get_window_extent(renderer).overlaps(
            labels[r"$k_y$"].get_window_extent(renderer)))

    def test_labels_follow_axis_roles_instead_of_arrow_signs(self):
        self.snapshot.camera = _camera((0.0, -10.0, 0.0))
        _, axes = self._draw()
        self.assertEqual([text.get_text() for text in axes.texts], [r"$k_x$", r"$E$", r"$k_y$"])
        self.snapshot.camera = _camera((0.0, -10.0, 0.0), (1.0, 0.0, 0.0))
        _, axes = self._draw()
        self.assertEqual([text.get_text() for text in axes.texts], [r"$E$", r"$k_x$", r"$k_y$"])

    def test_explicit_axis_names_and_hidden_labels_are_honoured(self):
        self.snapshot.camera = _camera((0.0, -10.0, 0.0))
        self.snapshot.payload["show_axes"] = False
        _, axes = self._draw(overrides={"xlabel_text": "horizontal", "zlabel_text": ""})
        self.assertEqual([text.get_text() for text in axes.texts], ["horizontal", r"$k_y$"])
        self._assert_projected_directions(axes)

    def test_marker_is_transparent_and_keeps_its_physical_size_across_dpi(self):
        sizes = []
        for dpi in (100, 600):
            figure, axes = self._draw(dpi=dpi)
            bounds = axes.get_position()
            sizes.append(np.array((bounds.width * 89.0, bounds.height * 85.0)))
            self.assertFalse(axes.patch.get_visible())
            self.assertEqual(figure.patch.get_alpha(), 0.0)
        np.testing.assert_allclose(sizes[0], sizes[1], rtol=0.0, atol=1e-10)
        self.assertAlmostEqual(sizes[0][0], sizes[0][1])

    def test_position_moves_marker_in_whole_figure_without_changing_projection_or_size(self):
        self.snapshot.camera = _camera((4.0, -7.0, 3.0))
        _, original = self._draw()
        for dpi in (100, 600):
            with self.subTest(dpi=dpi):
                _, moved = self._draw(dpi=dpi, overrides={
                    "orientation_cx": 72.25, "orientation_cy": 68.75,
                })
                bounds = moved.get_position()
                self.assertAlmostEqual((bounds.x0 + bounds.width / 2.) * 100., 72.25)
                self.assertAlmostEqual((bounds.y0 + bounds.height / 2.) * 100., 68.75)
                np.testing.assert_allclose((bounds.width, bounds.height),
                                           original.get_position().size, atol=1e-12)
                self._assert_projected_directions(moved)

    def test_edge_positions_clamp_entire_marker_and_labels_inside_figure(self):
        for x, y in ((0., 0.), (100., 100.), (0., 100.), (100., 0.)):
            with self.subTest(x=x, y=y):
                figure, axes = self._draw(overrides={"orientation_cx": x, "orientation_cy": y})
                rect = axes.get_position()
                self.assertGreaterEqual(rect.x0, -1e-12)
                self.assertGreaterEqual(rect.y0, -1e-12)
                self.assertLessEqual(rect.x1, 1. + 1e-12)
                self.assertLessEqual(rect.y1, 1. + 1e-12)
                renderer = figure.canvas.get_renderer()
                frame = figure.bbox
                for text in axes.texts:
                    bounds = text.get_window_extent(renderer)
                    self.assertGreaterEqual(bounds.x0, frame.x0 - 1.)
                    self.assertGreaterEqual(bounds.y0, frame.y0 - 1.)
                    self.assertLessEqual(bounds.x1, frame.x1 + 1.)
                    self.assertLessEqual(bounds.y1, frame.y1 + 1.)

    def test_default_and_long_names_fit_inside_marker_at_small_and_normal_sizes(self):
        for rect in (self.main_rect, (10.0, 9.0, 25.0, 24.0)):
            for overrides in ({}, {"xlabel_text": "horizontal axis name with forty letters!"}):
                with self.subTest(rect=rect, overrides=overrides):
                    figure, axes = self._draw(main_rect=rect, overrides=overrides)
                    renderer = figure.canvas.get_renderer()
                    frame = axes.get_window_extent(renderer)
                    outlines = [patch for patch in axes.patches
                                if patch.get_gid() == "orientation-label"]
                    self.assertEqual(len(axes.texts) + len(outlines), 3)
                    label_bounds = [text.get_window_extent(renderer) for text in axes.texts]
                    label_bounds.extend(patch.get_path().get_extents(patch.get_transform())
                                        for patch in outlines)
                    for bounds in label_bounds:
                        self.assertGreaterEqual(bounds.x0, frame.x0 - 1.0)
                        self.assertGreaterEqual(bounds.y0, frame.y0 - 1.0)
                        self.assertLessEqual(bounds.x1, frame.x1 + 1.0)
                        self.assertLessEqual(bounds.y1, frame.y1 + 1.0)


if __name__ == "__main__":
    unittest.main()
