# -*- coding: utf-8 -*-
"""色带锁定（锁定数据范围归一化）回归测试。

覆盖：
1. _level_info 在锁定/解锁/含零/无渲染记录各状态下的行为；
2. 2D render_2d_slice 端到端：锁定后换帧 clim 与色带刻度保持不变；
3. 3D VolumeRenderSession 端到端：锁定后换数据 level_info 不变，
   解锁后同数据同参数必须重算（防守 style 签名未含锁定状态的回归）。

注意：锁定状态是 VisualEngine 类级全局，setUp/tearDown 必须复位，
避免污染同进程内其他测试。
"""
import unittest

import numpy as np
import pyvista as pv
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from bandscope.rendering.render_core import VisualEngine, VolumeRenderSession


def _reset_lock_state():
    VisualEngine.unlock_data_range()
    VisualEngine._last_data_range = None


class LevelInfoLockTests(unittest.TestCase):
    def setUp(self):
        _reset_lock_state()

    def tearDown(self):
        _reset_lock_state()

    def test_auto_normalization_records_last_range(self):
        info = VisualEngine._level_info(np.array([[1.0, 9.0]]), (0, 50, 100))
        self.assertEqual((info["data_min"], info["data_max"]), (1.0, 9.0))
        self.assertEqual(VisualEngine._last_data_range, (1.0, 9.0))

    def test_lock_freezes_range_for_new_data(self):
        VisualEngine._level_info(np.array([[1.0, 9.0]]), (0, 50, 100))
        self.assertTrue(VisualEngine.lock_data_range())
        self.assertEqual(VisualEngine.locked_data_range(), (1.0, 9.0))

        info = VisualEngine._level_info(np.array([[100.0, 200.0]]), (0, 50, 100))
        self.assertEqual((info["data_min"], info["data_max"]), (1.0, 9.0))
        self.assertEqual(info["black_value"], 1.0)
        self.assertEqual(info["white_value"], 9.0)

    def test_unlock_restores_auto_normalization(self):
        VisualEngine._level_info(np.array([[1.0, 9.0]]), (0, 50, 100))
        VisualEngine.lock_data_range()
        VisualEngine.unlock_data_range()
        self.assertIsNone(VisualEngine.locked_data_range())

        info = VisualEngine._level_info(np.array([[100.0, 200.0]]), (0, 50, 100))
        self.assertEqual((info["data_min"], info["data_max"]), (100.0, 200.0))

    def test_lock_captures_include_zero_range(self):
        VisualEngine._level_info(np.array([[2.0, 8.0]]), (0, 50, 100), include_zero=True)
        VisualEngine.lock_data_range()
        self.assertEqual(VisualEngine.locked_data_range(), (0.0, 8.0))

    def test_lock_without_prior_render_fails(self):
        self.assertFalse(VisualEngine.lock_data_range())
        self.assertIsNone(VisualEngine.locked_data_range())


class Render2DLockTests(unittest.TestCase):
    def setUp(self):
        _reset_lock_state()
        figure = Figure(figsize=(4, 3))
        self.canvas = FigureCanvasAgg(figure)
        self.axes = figure.add_subplot(111)
        self.coords = {
            "X": np.linspace(-1.0, 1.0, 4),
            "Y": np.linspace(-2.0, 2.0, 5),
            "E": np.linspace(-0.5, 0.5, 6),
        }
        self.slice_info = {"axis": 0, "mode": "integral", "range": (1, 1)}

    def tearDown(self):
        _reset_lock_state()

    def _render(self, data):
        VisualEngine.render_2d_slice(
            self.axes,
            self.canvas,
            data,
            self.slice_info,
            (0, 50, 100),
            self.coords,
        )
        image = self.axes._arpes_image
        return tuple(float(v) for v in image.get_clim())

    def test_locked_clim_fixed_across_frames(self):
        frame_a = np.full((5, 6), 10.0, dtype=np.float32)
        frame_b = np.full((5, 6), 80.0, dtype=np.float32)

        clim_a = self._render(frame_a)

        # 未锁定：换帧后 clim 跟随新数据
        clim_b = self._render(frame_b)
        self.assertNotEqual(clim_a, clim_b)

        # 回到 A 并锁定
        clim_a2 = self._render(frame_a)
        self.assertTrue(VisualEngine.lock_data_range())
        # 锁定后换到 B：clim 必须保持 A 的范围
        clim_b_locked = self._render(frame_b)
        self.assertEqual(clim_a2, clim_b_locked)

        # 解锁后再渲染 B：clim 恢复跟随 B
        VisualEngine.unlock_data_range()
        clim_b_unlocked = self._render(frame_b)
        self.assertEqual(clim_b, clim_b_unlocked)


class VolumeSessionLockTests(unittest.TestCase):
    def setUp(self):
        _reset_lock_state()
        self.plotter = pv.Plotter(off_screen=True, window_size=(240, 180))

    def tearDown(self):
        _reset_lock_state()
        self.plotter.close()

    def test_locked_level_info_fixed_and_unlock_recomputes(self):
        session = VolumeRenderSession(self.plotter)
        rng = np.random.default_rng(7)
        frame_a = rng.random((10, 9, 8), dtype=np.float32)
        frame_b = np.asfortranarray(frame_a * 100.0)

        session.render(frame_a, (0, 50, 100), "linear", show_axes=False)
        bw_a = (
            session.level_info["black_value"],
            session.level_info["white_value"],
        )

        session.render(frame_b, (0, 50, 100), "linear", show_axes=False)
        bw_b = (
            session.level_info["black_value"],
            session.level_info["white_value"],
        )
        self.assertNotEqual(bw_a, bw_b)

        session.render(frame_a, (0, 50, 100), "linear", show_axes=False)
        self.assertTrue(VisualEngine.lock_data_range())
        session.render(frame_b, (0, 50, 100), "linear", show_axes=False)
        self.assertEqual(
            bw_a,
            (session.level_info["black_value"], session.level_info["white_value"]),
        )

        # 解锁后同数据同参数必须重算（style 签名需包含锁定状态）
        VisualEngine.unlock_data_range()
        session.render(frame_b, (0, 50, 100), "linear", show_axes=False)
        self.assertEqual(
            bw_b,
            (session.level_info["black_value"], session.level_info["white_value"]),
        )


if __name__ == "__main__":
    unittest.main()
