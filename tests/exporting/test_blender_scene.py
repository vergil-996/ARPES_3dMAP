import copy
import json
import math
import unittest

import numpy as np
from matplotlib import colormaps

from bandscope.exporting.blender_scene import (
    ALPHA_CAP, AXIS_FRAME_BOUNDS, BlenderSceneError, DISPLAY_TO_BLENDER,
    GUIDE_FRAME_MARGIN, _render_resolution, guide_frame_bounds,
    prepare_blender_scene, project_point, separated_layout,
)
from bandscope.exporting.publication_models import PublicationSnapshot
from bandscope.exporting.publication_renderers import (
    _orientation_axis_order, _orientation_axis_vectors, compute_level_info,
)
from bandscope.processing.display_transfer import channels
from tests.support.blender import make_blender_snapshot


class BlenderSpatialTests(unittest.TestCase):
    def test_non_3d_snapshot_is_rejected(self):
        snapshot = make_blender_snapshot()
        snapshot.view = "2d"
        snapshot.view_family = "2d"
        with self.assertRaises(BlenderSceneError):
            prepare_blender_scene(snapshot)

    def test_nonzero_roi_and_nonuniform_display_spacing_are_preserved(self):
        snapshot = make_blender_snapshot()
        original = snapshot.payload["volume"].copy()
        scene = prepare_blender_scene(snapshot)
        self.assertEqual(scene.shape, (3, 4, 5))
        self.assertEqual(scene.full_shape, (6, 7, 8))
        self.assertEqual(scene.data_bounds, (1, 3, 2, 5, 1, 5))
        self.assertEqual(scene.display_origin, (40.0, 200.0 / 3.0, 200.0 / 7.0))
        self.assertEqual(scene.display_spacing, (40.0, 200.0 / 6.0, 200.0 / 7.0))
        np.testing.assert_allclose(scene.blender_origin, (-0.6, -1.0 / 3.0, -5.0 / 7.0))
        np.testing.assert_allclose(scene.blender_spacing, (0.4, 0.2 / 0.6, 2.0 / 7.0))
        np.testing.assert_array_equal(scene.intensity, original)

    def test_descending_coordinates_and_e_flip_do_not_reverse_arrays(self):
        snapshot = make_blender_snapshot()
        original_e = snapshot.coords["E"].copy()
        original_volume = snapshot.payload["volume"].copy()
        self.assertEqual(snapshot.camera["view_up"], (0.0, 0.0, -1.0))
        scene = prepare_blender_scene(snapshot)
        np.testing.assert_array_equal(scene.coords["E"], original_e)
        np.testing.assert_array_equal(scene.coords["Y"], snapshot.coords["Y"])
        np.testing.assert_array_equal(scene.intensity, original_volume)
        self.assertEqual(scene.coords["E"][0], 2.0)
        self.assertEqual(scene.coords["E"][-1], -1.0)
        np.testing.assert_allclose(
            scene.metadata()["coordinates"]["E"]["roi_values"],
            [original_e[1], original_e[5]], rtol=0.0, atol=1e-15,
        )

    def test_clipping_intersects_roi_and_masks_density_only(self):
        snapshot = make_blender_snapshot(clip_render_bounds=(50.0, 110.0, 0.0, 100.0, 0.0, 100.0))
        scene = prepare_blender_scene(snapshot)
        self.assertGreater(float(scene.alpha.max()), 0.0)
        self.assertEqual(tuple(scene.blender_bounds), (-0.5, 0.1, -1.0 / 3.0, 0.0, -5.0 / 7.0, 0.0))
        x, y, e = np.indices(scene.shape)
        expected_zero = (x == 0) | (x == 2) | (y >= 2) | (e >= 3)
        self.assertTrue(np.all(scene.density[expected_zero] == 0.0))
        self.assertTrue(np.all(scene.density[~expected_zero] > 0.0))
        self.assertTrue(np.all(scene.alpha >= 0.0))

    def test_metadata_is_json_serializable_and_has_absolute_roi(self):
        scene = prepare_blender_scene(make_blender_snapshot())
        metadata = scene.metadata()
        json.dumps(metadata, ensure_ascii=False)
        self.assertEqual(metadata["display_mapping"]["roi_absolute_start"], [1, 2, 1])
        self.assertEqual(metadata["blender_grid"]["density_space"], "OBJECT")
        self.assertEqual(metadata["blender_grid"]["weak_signal_clipping"], 0.0)
        self.assertEqual(metadata["camera"]["sensor_fit"], "VERTICAL")

    def test_guide_frame_is_larger_than_axis_frame_and_encloses_data(self):
        snapshot = make_blender_snapshot(
            clip_render_bounds=(50.0, 110.0, 0.0, 100.0, 0.0, 100.0),
        )
        metadata = prepare_blender_scene(snapshot).metadata()
        guide = metadata["guide_frame"]
        self.assertTrue(guide["always_generated"])
        self.assertEqual(guide["margin"], GUIDE_FRAME_MARGIN)
        self.assertEqual(guide["axis_frame_bounds"], list(AXIS_FRAME_BOUNDS))
        bounds = guide["bounds"]
        for axis in range(3):
            self.assertLess(bounds[axis * 2], AXIS_FRAME_BOUNDS[axis * 2])
            self.assertGreater(bounds[axis * 2 + 1], AXIS_FRAME_BOUNDS[axis * 2 + 1])
        data = metadata["blender_grid"]["material_clip_bounds"]
        for axis in range(3):
            self.assertLessEqual(bounds[axis * 2], data[axis * 2])
            self.assertGreaterEqual(bounds[axis * 2 + 1], data[axis * 2 + 1])

    def test_guide_frame_does_not_depend_on_axis_display(self):
        enabled = prepare_blender_scene(make_blender_snapshot(show_axes=True)).metadata()["guide_frame"]
        disabled = prepare_blender_scene(make_blender_snapshot(show_axes=False)).metadata()["guide_frame"]
        self.assertEqual(enabled, disabled)
        self.assertEqual(disabled["bounds"], [-1.1, 1.1, -1.1, 1.1, -1.1, 1.1])

    def test_axis_indicator_is_complete_visible_and_independent_of_axis_display(self):
        enabled = prepare_blender_scene(make_blender_snapshot(show_axes=True)).metadata()
        disabled = prepare_blender_scene(make_blender_snapshot(show_axes=False)).metadata()
        indicator = disabled["axis_indicator"]
        self.assertEqual(indicator, enabled["axis_indicator"])
        self.assertTrue(indicator["always_generated"])
        self.assertTrue(indicator["visible_by_default"])
        self.assertEqual(indicator["style"], "solid_world_axes")
        self.assertEqual(indicator["ink_color"], [0.0, 0.0, 0.0])
        self.assertEqual([axis["role"] for axis in indicator["axes"]], ["horizontal", "vertical", "depth"])
        self.assertEqual([axis["key"] for axis in indicator["axes"]], ["X", "E", "Y"])
        self.assertEqual([axis["label"] for axis in indicator["axes"]], ["kx", "E", "ky"])
        self.assertTrue(all("color" not in axis for axis in indicator["axes"]))
        self.assertEqual(len({axis["object"] for axis in indicator["axes"]}), 3)

    def test_indicator_has_orthogonal_world_axes_and_closed_solid_heads(self):
        for parallel in (False, True):
            snapshot = make_blender_snapshot()
            snapshot.camera["parallel_projection"] = parallel
            indicator = prepare_blender_scene(snapshot).metadata()["axis_indicator"]
            directions = []
            for axis in indicator["axes"]:
                direction = np.asarray(axis["tip_world"]) - indicator["origin_world"]
                np.testing.assert_allclose(direction, np.eye(3)[("X", "Y", "E").index(axis["key"])])
                directions.append(direction)
                vertices = np.asarray(axis["head_vertices_world"])
                self.assertEqual(np.linalg.matrix_rank(vertices - vertices[0]), 3)
                edges = {}
                for face in axis["head_faces"]:
                    for first, second in zip(face, face[1:] + face[:1]):
                        key = tuple(sorted((first, second)))
                        edges[key] = edges.get(key, 0) + 1
                self.assertTrue(all(count == 2 for count in edges.values()))
            np.testing.assert_allclose(np.asarray(directions) @ np.asarray(directions).T, np.eye(3))

    def test_end_on_depth_axis_retains_real_length(self):
        snapshot = make_blender_snapshot(camera={
            "position": (100.0, -400.0, 100.0), "focal_point": (100.0, 100.0, 100.0),
            "view_up": (0.0, 0.0, 1.0), "parallel_projection": True,
            "parallel_scale": 120.0, "view_angle": 35.0, "clipping_range": (1.0, 2000.0),
        })
        indicator = prepare_blender_scene(snapshot).metadata()["axis_indicator"]
        depth = next(axis for axis in indicator["axes"] if axis["key"] == "Y")
        self.assertEqual(depth["kind"], "arrow")
        np.testing.assert_allclose(np.asarray(depth["tip_world"]) - indicator["origin_world"], (0, 1, 0))
        self.assertEqual([part["body"] for part in depth["label_parts"]], ["k", "y"])

    def test_layout_places_three_intact_groups_on_distinct_world_sides(self):
        scene = prepare_blender_scene(make_blender_snapshot())
        metadata = scene.metadata()
        layout = metadata["layout"]
        self.assertEqual(layout["placements"], {
            "coordinates": "left", "guide_frame": "back", "orientation": "top",
            "text_presets": "bottom",
        })
        data = np.asarray(metadata["blender_grid"]["material_clip_bounds"]).reshape(3, 2)
        coordinates = np.asarray(AXIS_FRAME_BOUNDS).reshape(3, 2) + np.asarray(layout["coordinates"])[:, None]
        guide = np.asarray(metadata["guide_frame"]["bounds"]).reshape(3, 2) + np.asarray(layout["guide_frame"])[:, None]
        indicator = metadata["axis_indicator"]
        marker_points = [indicator["origin_world"]]
        for axis in indicator["axes"]:
            marker_points.extend(axis["head_vertices_world"])
            marker_points.extend(part["world"] for part in axis["label_parts"])
        marker_points = np.asarray(marker_points)
        marker = np.column_stack((marker_points.min(axis=0), marker_points.max(axis=0)))
        self.assertLess(coordinates[0, 1], data[0, 0])
        self.assertGreater(guide[1, 0], data[1, 1])
        self.assertGreater(marker[2, 0], data[2, 1])
        extents = {"data": data, "coordinates": coordinates, "guide": guide, "marker": marker}
        names = list(extents)
        for index, name in enumerate(names):
            for other_name in names[index + 1:]:
                first, second = extents[name], extents[other_name]
                with self.subTest(first=name, second=other_name):
                    self.assertTrue(np.any((first[:, 1] < second[:, 0]) | (second[:, 1] < first[:, 0])))
        self.assertEqual(layout["data"], [0, 0, 0])
        np.testing.assert_array_equal(metadata["blender_grid"]["origin"], scene.blender_origin)
        np.testing.assert_array_equal(metadata["blender_grid"]["material_clip_bounds"], scene.blender_bounds)

    def test_six_slots_are_opposed_and_centered_on_cropped_data(self):
        scene = prepare_blender_scene(make_blender_snapshot(
            clip_render_bounds=(50.0, 110.0, 0.0, 100.0, 0.0, 100.0),
        ))
        metadata = scene.metadata()
        layout = metadata["layout"]
        center = np.asarray((-0.2, -1.0 / 6.0, -5.0 / 14.0))
        np.testing.assert_allclose(layout["center"], center)
        self.assertEqual(set(layout["slots"]), {"left", "right", "front", "back", "bottom", "top"})
        for axis, (negative, positive) in enumerate((("left", "right"), ("front", "back"), ("bottom", "top"))):
            low, high = (np.asarray(layout["slots"][key]) for key in (negative, positive))
            np.testing.assert_allclose((low + high) / 2.0, center)
            np.testing.assert_allclose(np.delete(low, axis), np.delete(center, axis))
            np.testing.assert_allclose(np.delete(high, axis), np.delete(center, axis))
            self.assertLess(low[axis], scene.blender_bounds[2 * axis])
            self.assertGreater(high[axis], scene.blender_bounds[2 * axis + 1])
        np.testing.assert_allclose(layout["coordinates"], layout["slots"]["left"])
        local_frame_center = np.asarray(metadata["guide_frame"]["bounds"]).reshape(3, 2).mean(axis=1)
        np.testing.assert_allclose(np.asarray(layout["guide_frame"]) + local_frame_center, layout["slots"]["back"])
        np.testing.assert_allclose(np.asarray(layout["orientation"]) + 0.5, layout["slots"]["top"])
        np.testing.assert_allclose(metadata["axis_indicator"]["origin_world"], layout["orientation"])
        np.testing.assert_allclose(layout["viewport"]["center"], center)
        self.assertGreater(layout["viewport"]["distance"], 0.0)

    def test_layout_spacing_expands_independently_for_each_data_dimension(self):
        baseline = separated_layout((-1.0, 1.0, -1.0, 1.0, -1.0, 1.0))
        for axis, positive in enumerate(("right", "back", "top")):
            bounds = np.asarray(((-1.0, 1.0), (-1.0, 1.0), (-1.0, 1.0)))
            bounds[axis] = (-8.0, 12.0)
            layout = separated_layout(bounds.ravel())
            center = bounds.mean(axis=1)
            delta = np.asarray(layout["slots"][positive]) - center
            self.assertGreater(delta[axis], baseline["slots"][positive][axis])
            for other_axis, other_positive in enumerate(("right", "back", "top")):
                if other_axis != axis:
                    self.assertAlmostEqual(
                        layout["slots"][other_positive][other_axis] - center[other_axis],
                        baseline["slots"][other_positive][other_axis],
                    )
            self.assertGreater(layout["viewport"]["distance"], baseline["viewport"]["distance"])
            self.assertEqual(layout["data"], [0, 0, 0])

    def test_layout_does_not_follow_camera_rotation_or_projection(self):
        snapshot = make_blender_snapshot()
        expected = prepare_blender_scene(snapshot).metadata()["layout"]
        snapshot.camera["position"] = (450.0, 250.0, -300.0)
        snapshot.camera["view_up"] = (0.0, 0.0, 1.0)
        snapshot.camera["parallel_projection"] = not snapshot.camera["parallel_projection"]
        self.assertEqual(prepare_blender_scene(snapshot).metadata()["layout"], expected)

    def test_guide_frame_expands_if_data_exceeds_axis_frame(self):
        bounds = guide_frame_bounds((-1.5, 1.2, -0.4, 0.4, -0.2, 0.2))
        self.assertAlmostEqual(bounds[0], -1.6)
        self.assertAlmostEqual(bounds[1], 1.3)
        self.assertAlmostEqual(bounds[2], -1.1)
        self.assertAlmostEqual(bounds[3], 1.1)
        self.assertAlmostEqual(bounds[4], -1.1)
        self.assertAlmostEqual(bounds[5], 1.1)


class BlenderDisplayTests(unittest.TestCase):
    def test_gamma_locked_levels_and_plugin_multiplier_are_applied_once(self):
        volume = np.linspace(-5.0, 25.0, 60, dtype=np.float32).reshape(3, 4, 5)
        multiplier = np.linspace(0.1, 1.4, 5, dtype=np.float32)
        levels = (0.0, 75.0, 100.0)
        snapshot = make_blender_snapshot(
            volume=volume, levels=levels, locked_range=(0.0, 10.0),
            opacity_multiplier=multiplier,
        )
        scene = prepare_blender_scene(snapshot)
        info = compute_level_info(volume, levels, locked_range=(0.0, 10.0))
        color, alpha = channels(volume, info, "linear")
        expected_alpha = np.clip(alpha * multiplier[None, None, :], 0.0, 1.0)
        np.testing.assert_allclose(scene.alpha, expected_alpha, rtol=1e-6, atol=1e-6)
        srgb = colormaps.get_cmap("magma")(np.clip(color, 0.0, 1.0))[..., :3]
        expected_linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
        np.testing.assert_allclose(scene.display_color, expected_linear, rtol=2e-5, atol=2e-6)
        expected_density = -np.log1p(-np.minimum(expected_alpha, ALPHA_CAP)) / scene.unit_distance_blender
        np.testing.assert_allclose(scene.density, expected_density, rtol=2e-5, atol=2e-5)
        self.assertEqual(scene.level_info["data_min"], 0.0)
        self.assertEqual(scene.level_info["data_max"], 10.0)

    def test_local_enhancement_uses_frozen_channels_and_multiplier(self):
        shape = (3, 4, 5)
        coordinates = np.linspace(0.1, 0.9, 60, dtype=np.float32).reshape(shape)
        alpha = np.full(shape, 0.25, dtype=np.float32)
        multiplier = np.array([0.0, 0.5, 1.0, 2.0, 1.0], dtype=np.float32)
        enhancement = {
            "active": True, "color": coordinates, "alpha": alpha,
            "background_level_info": {"data_min": -100.0},
        }
        snapshot = make_blender_snapshot(
            levels=(0.0, 80.0, 100.0), opacity_multiplier=multiplier,
            enhancement=enhancement,
        )
        scene = prepare_blender_scene(snapshot)
        np.testing.assert_allclose(scene.alpha, alpha * multiplier[None, None, :])
        srgb = colormaps.get_cmap("magma")(coordinates)[..., :3]
        expected = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
        np.testing.assert_allclose(scene.display_color, expected, rtol=2e-5, atol=2e-6)

    def test_nan_inf_and_crop_are_transparent_and_density_is_finite(self):
        volume = np.ones((3, 4, 5), dtype=np.float32)
        volume[0, 0, 0] = np.nan
        volume[1, 1, 1] = np.inf
        volume[2, 2, 2] = -np.inf
        enhancement = {
            "active": True, "color": np.ones((3, 4, 5), dtype=np.float32),
            "alpha": np.ones((3, 4, 5), dtype=np.float32),
        }
        scene = prepare_blender_scene(make_blender_snapshot(volume=volume, enhancement=enhancement))
        for index in ((0, 0, 0), (1, 1, 1), (2, 2, 2)):
            self.assertEqual(scene.alpha[index], 0.0)
            self.assertEqual(scene.density[index], 0.0)
            np.testing.assert_array_equal(scene.display_color[index], (0.0, 0.0, 0.0))
        self.assertTrue(np.all(np.isfinite(scene.density)))
        finite_alpha = scene.alpha[scene.alpha > 0]
        self.assertTrue(np.all(finite_alpha <= 1.0))

    def test_invalid_multiplier_length_is_explicit(self):
        snapshot = make_blender_snapshot(opacity_multiplier=np.ones(4))
        with self.assertRaisesRegex(BlenderSceneError, "倍率"):
            prepare_blender_scene(snapshot)

    def test_prepare_does_not_mutate_snapshot(self):
        enhancement = {
            "active": True,
            "color": np.linspace(0, 1, 60, dtype=np.float32).reshape(3, 4, 5),
            "alpha": np.full((3, 4, 5), 0.5, dtype=np.float32),
        }
        snapshot = make_blender_snapshot(
            enhancement=enhancement, opacity_multiplier=np.linspace(0.2, 1.0, 5),
        )
        before = copy.deepcopy(snapshot.payload)
        coords_before = copy.deepcopy(snapshot.coords)
        camera_before = copy.deepcopy(snapshot.camera)
        prepare_blender_scene(snapshot)
        for key, value in before.items():
            if isinstance(value, np.ndarray):
                np.testing.assert_array_equal(snapshot.payload[key], value)
            elif key == "enhancement":
                for array_key in ("color", "alpha"):
                    np.testing.assert_array_equal(snapshot.payload[key][array_key], value[array_key])
            else:
                self.assertEqual(snapshot.payload[key], value, key)
        for key in coords_before:
            np.testing.assert_array_equal(snapshot.coords[key], coords_before[key])
        self.assertEqual(snapshot.camera, camera_before)


class BlenderCameraTests(unittest.TestCase):
    def test_four_projection_modes_use_long_edge_and_square_pixels(self):
        points = ((-1.0, -1.0, -1.0), (0.0, 0.0, 0.0), (0.4, -0.2, 0.8))
        for parallel in (False, True):
            for aspect in (1.6, 0.625):
                camera = {
                    "position": (250.0, -350.0, 280.0),
                    "focal_point": (100.0, 100.0, 100.0),
                    "view_up": (0.0, 0.0, 1.0),
                    "parallel_projection": parallel,
                    "parallel_scale": 120.0,
                    "view_angle": 35.0,
                    "clipping_range": (1.0, 2000.0),
                }
                scene = prepare_blender_scene(make_blender_snapshot(camera=camera, viewport_aspect=aspect))
                resolution = _render_resolution(aspect, 1024)
                self.assertEqual(max(resolution), 1024)
                center = project_point(scene.camera["focal_point"], scene.camera, resolution)
                self.assertAlmostEqual(center[0], resolution[0] / 2.0, places=6)
                self.assertAlmostEqual(center[1], resolution[1] / 2.0, places=6)
                projected = [project_point(point, scene.camera, resolution) for point in points]
                self.assertTrue(all(math.isfinite(value) for point in projected for value in point))
                if parallel:
                    self.assertAlmostEqual(scene.camera["ortho_scale"], 2.4)
                else:
                    expected_lens = 24.0 / (2.0 * math.tan(math.radians(35.0) / 2.0))
                    self.assertAlmostEqual(scene.camera["lens_mm"], expected_lens)

    def test_clipping_range_is_scaled_with_scene(self):
        scene = prepare_blender_scene(make_blender_snapshot())
        self.assertAlmostEqual(scene.camera["clip_start"], 0.01)
        self.assertAlmostEqual(scene.camera["clip_end"], 20.0)
        self.assertEqual(DISPLAY_TO_BLENDER, 0.01)


if __name__ == "__main__":
    unittest.main()
