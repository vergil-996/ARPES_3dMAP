import unittest

import numpy as np
import pyvista as pv

from render_core import VolumeRenderSession


class VolumeRenderSessionTests(unittest.TestCase):
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
            self.assertEqual(session.rebuild_count, 1)
            self.assertEqual(session.data_update_count, uploads + 1)
            self.assertEqual(session.render_count, 4)
        finally:
            plotter.close()


if __name__ == "__main__":
    unittest.main()
