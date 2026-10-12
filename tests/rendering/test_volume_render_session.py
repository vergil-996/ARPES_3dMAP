import unittest

import numpy as np
import pyvista as pv
from vtkmodules.util.numpy_support import vtk_to_numpy

from bandscope.rendering.render_core import VolumeRenderSession
from tests.support.environment import requires_opengl


class VolumeRenderSessionTests(unittest.TestCase):
    @requires_opengl
    def test_time_preview_and_exact_reuse_scene_and_opacity_mapping(self):
        plotter = pv.Plotter(off_screen=True, window_size=(240, 180))
        try:
            session = VolumeRenderSession(plotter)
            data = np.ones((14, 12, 10), dtype=np.float32)
            data[1, 1, 1] = 70.0
            weights = np.linspace(0.5, 1.5, 10, dtype=np.float32)
            session.render(data, (0, 50, 100), "linear", show_axes=False,
                           opacity_multiplier=weights)
            actor, mapper, grid = session.volume, session.volume.mapper, session.grid
            replacement = np.asfortranarray(data * 0.8)
            session.render(replacement, (0, 50, 100), "linear", show_axes=False,
                           quality="preview", opacity_multiplier=weights)
            levels = dict(session.level_info)
            alpha = session.alpha_channel.buffer.copy()
            unit_distance = actor.GetProperty().GetScalarOpacityUnitDistance()
            uploads = session.data_update_count
            session.render(replacement, (0, 50, 100), "linear", show_axes=False,
                           quality="exact", opacity_multiplier=weights)
            self.assertIs(session.volume, actor)
            self.assertIs(session.volume.mapper, mapper)
            self.assertIs(session.grid, grid)
            self.assertEqual(session.rebuild_count, 1)
            self.assertEqual(session.data_update_count, uploads)
            self.assertEqual(session.level_info, levels)
            np.testing.assert_array_equal(session.alpha_channel.buffer, alpha)
            self.assertEqual(actor.GetProperty().GetScalarOpacityUnitDistance(), unit_distance)
        finally:
            plotter.close()

    def test_same_shape_reuses_scene_and_style_does_not_upload_scalars(self):
        plotter = pv.Plotter(off_screen=True, window_size=(240, 180))
        try:
            session = VolumeRenderSession(plotter)
            data = np.random.default_rng(12).random((14, 12, 10), dtype=np.float32)
            session.render(data, (0, 50, 100), "linear", show_axes=False)
            actor = session.volume
            mapper = session.volume.mapper
            grid = session.grid
            uploads = session.data_update_count

            session.render(data, (5, 60, 95), "sigmoid", show_axes=False, cmap="viridis")
            self.assertIs(session.volume, actor)
            self.assertIs(session.volume.mapper, mapper)
            self.assertIs(session.grid, grid)
            self.assertEqual(session.data_update_count, uploads)

            session.render(
                data,
                (5, 60, 95),
                "sigmoid",
                show_axes=False,
                cmap="viridis",
                clip_ranges=(10, 180, 20, 170, 5, 195),
            )
            self.assertEqual(session.data_update_count, uploads)

            replacement = np.asfortranarray(data * 2)
            session.render(replacement, (0, 50, 100), "linear", show_axes=False)
            self.assertIs(session.volume, actor)
            mapper_values = vtk_to_numpy(
                session.volume.mapper.GetInput().GetPointData().GetScalars()
            )
            np.testing.assert_array_equal(
                mapper_values,
                replacement.ravel(order="F"),
            )
            self.assertEqual(session.rebuild_count, 1)
            self.assertEqual(session.data_update_count, uploads + 1)
            self.assertEqual(session.render_count, 4)
        finally:
            plotter.close()


class MaskedVolumeGridTests(unittest.TestCase):
    """掩膜体的网格表示：必须零基 extent，ROI 起点由 origin 表达。

    非零起始索引的网格配上 ``SetMaskInput`` 会让 NVIDIA 驱动在 ``render()``
    阶段访问越界（先拖动裁剪、再裁空就稳定闪退）。这里固定两条不变量：
    有掩膜时 extent 从 0 开始，且世界坐标与非零 extent 表示逐个采样点一致。
    """

    FULL_SHAPE = (40, 30, 24)
    #: 裁剪后的 ROI：X/Z 下界非零，正是拖动裁剪留下的形态。
    BOUNDS = (5, 34, 0, 29, 4, 23)

    def _session_data(self, hole):
        data = np.ones((30, 30, 20), dtype=np.float32)
        if hole:
            data[3:9, 3:9, :] = np.nan
        return data

    @staticmethod
    def _world_bounds(extent, spacing, origin):
        return tuple(
            origin[axis] + extent[2 * axis + side] * spacing[axis]
            for axis in range(3)
            for side in (0, 1)
        )

    def _render(self, hole):
        plotter = pv.Plotter(off_screen=True, window_size=(160, 160))
        self.addCleanup(plotter.close)
        session = VolumeRenderSession(plotter)
        session.render(
            self._session_data(hole),
            (0, 50, 100),
            "linear",
            show_axes=False,
            data_bounds=self.BOUNDS,
            full_shape=self.FULL_SHAPE,
        )
        return session

    def test_masked_grid_is_zero_based_and_preserves_world_bounds(self):
        session = self._render(hole=True)
        extent = tuple(int(value) for value in session.grid.extent)
        spacing = tuple(float(value) for value in session.grid.spacing)
        origin = tuple(float(value) for value in session.grid.origin)

        self.assertEqual(extent[:1] + extent[2:3] + extent[4:5], (0, 0, 0))
        self.assertEqual(extent, (0, 29, 0, 29, 0, 19))
        self.assertTrue(any(abs(value) > 0 for value in origin))
        self.assertEqual(
            self._world_bounds(extent, spacing, origin),
            self._world_bounds(self.BOUNDS, spacing, (0.0, 0.0, 0.0)),
        )

        mask = session.volume.mapper.GetMaskInput()
        self.assertIsNotNone(mask)
        np.testing.assert_allclose(mask.GetBounds(), session.grid.GetBounds())

    def test_unmasked_grid_keeps_absolute_extent_representation(self):
        session = self._render(hole=False)
        self.assertEqual(tuple(int(value) for value in session.grid.extent), self.BOUNDS)
        self.assertEqual(tuple(float(value) for value in session.grid.origin), (0.0, 0.0, 0.0))
        # 无缺失体素时不切 GPU mapper，也就没有掩膜接口可挂。
        self.assertFalse(hasattr(session.volume.mapper, "GetMaskInput"))


if __name__ == "__main__":
    unittest.main()
