# -*- coding: utf-8 -*-
"""能带面叠加层：世界坐标换算、与体数据旋转的对齐、actor 生命周期。

对齐用真实后端核对：把体数据里一个孤立体素按显示旋转转过去，看它落在哪个索引，
和叠加层对同一个网格点的预测比较。这不需要 OpenGL，也不依赖真实窗口。
"""
from __future__ import annotations

import unittest

import numpy as np

from bandscope.processing.compute_backends import CpuComputeBackend
from bandscope.rendering.surface_overlay import (
    OverlaySurface,
    SurfaceOverlayManager,
    rotate_indices,
    surface_world_geometry,
)
from tests.support.overlay import FakePlotter

SHAPE = (24, 30, 40)
X = np.linspace(-1.0, 1.0, SHAPE[0])
Y = np.linspace(-0.5, 0.5, SHAPE[1])
E = -0.4 + 0.018 * np.arange(SHAPE[2])
COORDS = {"X": X, "Y": Y, "E": E}
SPACING = [200.0 / (size - 1) for size in SHAPE]


def surface(z=None, **overrides) -> OverlaySurface:
    payload = {
        "key": "k1",
        "label": "Band 1",
        "x": X,
        "y": Y,
        "z": np.linspace(-0.1, 0.1, SHAPE[0] * SHAPE[1]).reshape(SHAPE[0], SHAPE[1]) if z is None else z,
        "color": "#ff8a3d",
    }
    payload.update(overrides)
    return OverlaySurface(**payload)


class GeometryTests(unittest.TestCase):
    def test_points_live_on_the_voxel_grid(self):
        points, faces = surface_world_geometry(surface(), coords=COORDS, full_shape=SHAPE)
        self.assertEqual(points.shape, (SHAPE[0] * SHAPE[1], 3))
        self.assertEqual(faces.size // 5, (SHAPE[0] - 1) * (SHAPE[1] - 1))
        row, column = 3, 5
        index = row * SHAPE[1] + column
        expected_energy_index = float(np.interp(surface().z[row, column], E, np.arange(E.size)))
        np.testing.assert_allclose(
            points[index],
            [row * SPACING[0], column * SPACING[1], expected_energy_index * SPACING[2]],
            rtol=1e-6,
        )
        self.assertEqual(faces[0], 4)

    def test_nan_areas_are_skipped(self):
        z = surface().z.copy()
        z[:, 0] = np.nan  # 整列无解
        points, faces = surface_world_geometry(surface(z), coords=COORDS, full_shape=SHAPE)
        self.assertEqual(points.shape[0], SHAPE[0] * (SHAPE[1] - 1))
        self.assertEqual(faces.size // 5, (SHAPE[0] - 1) * (SHAPE[1] - 2))
        self.assertTrue(np.all(np.isfinite(points)))

    def test_all_nan_returns_empty_geometry(self):
        points, faces = surface_world_geometry(
            surface(np.full(SHAPE[:2], np.nan)), coords=COORDS, full_shape=SHAPE
        )
        self.assertEqual(points.shape, (0, 3))
        self.assertEqual(faces.size, 0)

    def test_shape_mismatch_raises(self):
        with self.assertRaises(ValueError):
            surface_world_geometry(surface(np.zeros((3, 3))), coords=COORDS, full_shape=SHAPE)

    def test_descending_axes_are_handled(self):
        """坐标轴递减表示体素顺序也递减：世界坐标应镜像，而不是照抄物理值。"""
        flipped = {"X": X[::-1].copy(), "Y": Y[::-1].copy(), "E": E[::-1].copy()}
        points, _ = surface_world_geometry(surface(), coords=flipped, full_shape=SHAPE)
        reference, _ = surface_world_geometry(surface(), coords=COORDS, full_shape=SHAPE)
        np.testing.assert_allclose(
            points[:, 0], SPACING[0] * (SHAPE[0] - 1) - reference[:, 0], atol=1e-4
        )
        np.testing.assert_allclose(
            points[:, 1], SPACING[1] * (SHAPE[1] - 1) - reference[:, 1], atol=1e-4
        )
        np.testing.assert_allclose(
            points[:, 2], SPACING[2] * (SHAPE[2] - 1) - reference[:, 2], atol=1e-3
        )

    def test_out_of_range_coordinates_clamp(self):
        narrow = {"X": np.array([0.0, 1.0]), "Y": np.array([0.0, 1.0]), "E": E}
        points, _ = surface_world_geometry(
            surface(np.zeros((2, 2)), x=np.array([-5.0, 5.0]), y=np.array([-5.0, 5.0])),
            coords=narrow,
            full_shape=SHAPE,
        )
        # 2×2 网格点数 4：越界的动量坐标夹到两端。
        np.testing.assert_allclose(np.unique(points[:, 0]), [0.0, SPACING[0]])
        np.testing.assert_allclose(np.unique(points[:, 1]), [0.0, SPACING[1]])


class RotationAlignmentTests(unittest.TestCase):
    """叠加层与体数据的旋转必须落在同一个位置（这是"对齐"的核心断言）。"""

    def test_rotation_matches_the_volume_backend(self):
        """孤立体素被后端转过去以后，落点必须和叠加层的预测一致。

        只挑旋转后仍在数组内的点：``mode="constant"`` 会把转出数组的特征丢掉，
        那种情况下 argmax 没有意义。
        """
        backend = CpuComputeBackend()
        for angle in (30.0, -45.0, 90.0, 137.0):
            for row, column in ((5, 23), (19, 4), (14, 18)):
                with self.subTest(angle=angle, row=row, column=column):
                    predicted = rotate_indices(
                        np.array([row]), np.array([column]), angle, SHAPE[:2]
                    )
                    expected = (float(predicted[0][0]), float(predicted[1][0]))
                    if not (
                        1 <= expected[0] <= SHAPE[0] - 2 and 1 <= expected[1] <= SHAPE[1] - 2
                    ):
                        continue  # 旋转后会出界，后端拿不到这个特征

                    volume = np.zeros(SHAPE, dtype=np.float32)
                    volume[row, column, :] = 1.0
                    rotated = backend.rotate_volume(volume, angle)
                    found = np.unravel_index(np.argmax(rotated[..., 0]), rotated.shape[:2])
                    self.assertLess(abs(expected[0] - found[0]), 1.0)
                    self.assertLess(abs(expected[1] - found[1]), 1.0)

    def test_rotation_clips_to_the_data_frame(self):
        """旋转后落回原数组框内的点才保留。

        体数据的旋转是 ``reshape=False``：框外没有数据。叠加层如果照画，画面的
        四个角会多出体数据根本不存在的尖角（合成数据时肉眼核对发现的）。
        """
        points, _ = surface_world_geometry(
            surface(), coords=COORDS, full_shape=SHAPE, rotation_angle=30.0
        )
        self.assertGreater(points.shape[0], 0)
        index_x = points[:, 0] / SPACING[0]
        index_y = points[:, 1] / SPACING[1]
        self.assertGreaterEqual(float(index_x.min()), -0.5 - 1e-4)
        self.assertLessEqual(float(index_x.max()), SHAPE[0] - 0.5 + 1e-4)
        self.assertGreaterEqual(float(index_y.min()), -0.5 - 1e-4)
        self.assertLessEqual(float(index_y.max()), SHAPE[1] - 0.5 + 1e-4)
        # 不旋转时不丢点：整幅网格都在框内。
        plain, _ = surface_world_geometry(surface(), coords=COORDS, full_shape=SHAPE)
        self.assertEqual(plain.shape[0], SHAPE[0] * SHAPE[1])

    def test_index_space_centre_maps_to_world_centre(self):
        """索引中心旋转后不动，而它在世界坐标里恒为 (100, 100)。"""
        center_row = (SHAPE[0] - 1) / 2.0
        center_column = (SHAPE[1] - 1) / 2.0
        for angle in (0.0, 37.0, 180.0, -90.0):
            rows, columns = rotate_indices(
                np.array([center_row]), np.array([center_column]), angle, SHAPE[:2]
            )
            self.assertAlmostEqual(float(rows[0]), center_row, places=9)
            self.assertAlmostEqual(float(columns[0]), center_column, places=9)
        self.assertAlmostEqual(center_row * SPACING[0], 100.0, places=9)
        self.assertAlmostEqual(center_column * SPACING[1], 100.0, places=9)

    def test_zero_rotation_is_identity(self):
        plain, _ = surface_world_geometry(surface(), coords=COORDS, full_shape=SHAPE)
        zero, _ = surface_world_geometry(
            surface(), coords=COORDS, full_shape=SHAPE, rotation_angle=0.0
        )
        np.testing.assert_allclose(plain, zero)


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.plotter = FakePlotter()
        self.manager = SurfaceOverlayManager(self.plotter)

    def sync(self, surfaces, **overrides):
        payload = {
            "coords": COORDS,
            "full_shape": SHAPE,
            "rotation_angle": 0.0,
            "actor_orientation": (0.0, 0.0, 0.0),
        }
        payload.update(overrides)
        self.manager.sync(surfaces, **payload)

    def test_sync_creates_updates_and_removes(self):
        first = surface()
        self.sync([first])
        self.assertEqual(self.plotter.actors["k1"].options["color"], "#ff8a3d")
        self.assertAlmostEqual(self.plotter.actors["k1"].options["opacity"], first.opacity)

        # 同一份数据重复同步：不重建 actor。
        actor = self.plotter.actors["k1"]
        self.sync([first])
        self.assertIs(self.plotter.actors["k1"], actor)

        # 换成别的面（不同数组）：重建。
        self.sync([surface(z=first.z + 0.05)])
        self.assertIsNot(self.plotter.actors["k1"], actor)
        self.assertIn("k1", self.plotter.removed)

        # 不在列表里：移除。
        self.sync([])
        self.assertEqual(self.manager.keys(), [])
        self.assertNotIn("k1", self.plotter.actors)

    def test_all_nan_surface_is_not_added(self):
        self.sync([surface(np.full(SHAPE[:2], np.nan))])
        self.assertEqual(self.manager.keys(), [])
        self.assertEqual(self.plotter.actors, {})

    def test_visibility_toggle_does_not_rebuild(self):
        self.sync([surface()])
        actor = self.plotter.actors["k1"]
        self.manager.set_visible("k1", False)
        self.assertFalse(actor.visible)
        self.assertFalse(self.manager.is_visible("k1"))
        self.manager.set_visible("k1", True)
        self.assertTrue(actor.visible)
        # 显隐切换后同步仍然不重建。
        self.sync([surface()])
        self.assertIs(self.plotter.actors["k1"], actor)

    def test_visibility_survives_sync(self):
        self.sync([surface()])
        self.manager.set_visible("k1", False)
        self.sync([surface()])
        self.assertFalse(self.manager.is_visible("k1"))
        self.assertFalse(self.plotter.actors["k1"].visible)

    def test_actor_orientation_and_origin_are_applied(self):
        self.sync([surface()], actor_orientation=(0.0, 0.0, 15.0))
        actor = self.plotter.actors["k1"]
        self.assertEqual(actor.orientation, (0.0, 0.0, 15.0))
        self.assertEqual(actor.origin, (100.0, 100.0, 100.0))

    def test_clear_removes_everything(self):
        self.sync([surface(), surface(key="k2", label="Band 2")])
        self.assertEqual(sorted(self.manager.keys()), ["k1", "k2"])
        self.manager.clear()
        self.assertEqual(self.manager.keys(), [])
        self.assertEqual(self.plotter.actors, {})

    def test_rotation_change_rebuilds_the_geometry(self):
        self.sync([surface()])
        before = self.plotter.actors["k1"].mesh.points.copy()
        self.sync([surface()], rotation_angle=45.0)
        after = self.plotter.actors["k1"].mesh.points
        # 旋转会重算点位，并裁掉转到数组框外的角：点数或坐标必然变化。
        self.assertTrue(
            before.shape != after.shape or not np.allclose(before, after),
            "旋转后几何没有重建",
        )


if __name__ == "__main__":
    unittest.main()
