import unittest
from types import SimpleNamespace

import numpy as np
import pyvista as pv

from refactored_app import My3DAnalyzer
from render_core import VolumeRenderSession


class _PlotterStub:
    def __init__(self, camera_position):
        self.camera_position = camera_position


class CameraPersistenceTests(unittest.TestCase):
    def test_camera_snapshot_is_an_independent_float_tuple(self):
        source = [[1, 2, 3], [4, 5, 6], [0, 0, 1]]

        snapshot = My3DAnalyzer._copy_camera_position(source)
        source[0][0] = 99

        self.assertEqual(
            snapshot,
            ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0), (0.0, 0.0, 1.0)),
        )

    def test_mouse_camera_is_saved_and_restored_before_rebuild(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        position = ((10, 20, 30), (1, 2, 3), (0, 0, 1))
        plotter = _PlotterStub(position)
        analyzer.plotter = plotter
        analyzer.left_display_stack = SimpleNamespace(currentWidget=lambda: plotter)
        analyzer._saved_3d_camera_position = None

        analyzer._capture_3d_camera_position()
        plotter.camera_position = ((99, 99, 99), (0, 0, 0), (0, 1, 0))
        analyzer._restore_3d_camera_position()

        self.assertEqual(plotter.camera_position, position)

    def test_hidden_3d_view_does_not_overwrite_saved_camera(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        plotter = _PlotterStub(((10, 20, 30), (1, 2, 3), (0, 0, 1)))
        analyzer.plotter = plotter
        analyzer.left_display_stack = SimpleNamespace(currentWidget=lambda: object())
        analyzer._saved_3d_camera_position = ((7, 8, 9), (1, 1, 1), (0, 1, 0))

        analyzer._capture_3d_camera_position()

        self.assertEqual(
            analyzer._saved_3d_camera_position,
            ((7, 8, 9), (1, 1, 1), (0, 1, 0)),
        )

    def test_flipped_camera_survives_actor_reset_and_volume_rebuild(self):
        plotter = pv.Plotter(off_screen=True, window_size=(240, 180))
        try:
            session = VolumeRenderSession(plotter)
            session.render(
                np.ones((8, 9, 10), dtype=np.float32),
                (0, 50, 100),
                "linear",
                show_axes=False,
            )

            analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
            analyzer.plotter = plotter
            analyzer.volume_session = session
            analyzer.left_display_stack = SimpleNamespace(currentWidget=lambda: plotter)
            analyzer.page_image = SimpleNamespace(
                switch_flip=SimpleNamespace(isChecked=lambda: True)
            )
            analyzer._saved_3d_camera_position = None
            analyzer._camera_e_flip_applied = False

            plotter.camera_position = (
                (340.0, 280.0, 260.0),
                (100.0, 100.0, 100.0),
                (0.0, 0.0, 1.0),
            )
            analyzer._capture_3d_camera_position()
            analyzer._apply_pending_e_flip_camera()
            flipped_camera = analyzer._current_3d_camera_position()

            session.volume.SetOrientation(0.0, 0.0, 37.0)
            analyzer._reset_volume_actor_transform()
            analyzer._restore_3d_camera_position()
            session.render(
                np.ones((10, 11, 12), dtype=np.float32),
                (0, 50, 100),
                "linear",
                show_axes=False,
            )

            actual = analyzer._current_3d_camera_position()
            np.testing.assert_allclose(actual, flipped_camera, atol=1e-8)
        finally:
            plotter.close()


if __name__ == "__main__":
    unittest.main()
