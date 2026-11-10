import threading
import time
import unittest
from pathlib import Path
import tempfile

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.exporting.blender_batch import (
    BlenderFreezeError, capture_blender_snapshots, list_blender_pages,
)
from bandscope.ui.result_workspace import AnalysisPageSpec
from tests.support.view_recipe import control_window, synthetic_npz


class BlenderBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.first_path = Path(self.temp.name) / "first.npz"
        self.second_path = Path(self.temp.name) / "second.npz"
        synthetic_npz(self.first_path, seed=1)
        synthetic_npz(self.second_path, seed=2)
        self.window = control_window()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self._settle)
        self.addCleanup(self.window.close)

    def _settle(self):
        for session in self.window.source_manager:
            identity = getattr((session.state or {}).get("core"), "source_identity", None)
            if identity is not None:
                try:
                    identity.future.result(timeout=2)
                except Exception:
                    pass
        time.sleep(0.05)

    def _load_two_sources(self):
        self.assertTrue(self.window.load_data(str(self.first_path)))
        first_id = self.window.source_manager.active_id
        home = self.window.left_workspace.home_page_id
        page = AnalysisPageSpec(
            self.window._make_page_id(), "第一时间积分", "time_integral", "test",
            source_page_id=home, params={"t_low": 0, "t_up": 1},
        )
        self.window.left_workspace.add_page(page)
        first_page = page.page_id
        self.assertTrue(self.window.load_data(str(self.second_path)))
        second_id = self.window.source_manager.active_id
        second_home = self.window.left_workspace.home_page_id
        return first_id, first_page, second_id, second_home

    def test_cross_source_and_same_source_pages_are_listed_and_current_is_marked(self):
        first_id, first_page, second_id, second_home = self._load_two_sources()
        candidates = list_blender_pages(self.window)
        keys = [candidate.key for candidate in candidates]
        self.assertIn((first_id, first_page), keys)
        self.assertIn((second_id, second_home), keys)
        current = [candidate for candidate in candidates if candidate.current]
        self.assertEqual([candidate.key for candidate in current], [(second_id, second_home)])

    def test_batch_freezes_exact_independent_pages_and_restores_workspace(self):
        first_id, first_page, second_id, second_home = self._load_two_sources()
        before_camera = np.asarray(self.window._current_3d_camera_position(), dtype=float)
        snapshots = capture_blender_snapshots(
            self.window,
            [(first_id, first_page), (second_id, second_home), (second_id, second_home)],
        )
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0].source_session_id, first_id)
        self.assertEqual(snapshots[1].source_session_id, second_id)
        self.assertEqual(snapshots[0].view, "3d")
        self.assertEqual(snapshots[1].view, "3d")
        self.assertEqual(snapshots[0].payload["volume"].shape, (6, 7, 8))
        self.assertFalse(np.shares_memory(snapshots[0].payload["volume"], snapshots[1].payload["volume"]))
        self.assertEqual(self.window.source_manager.active_id, second_id)
        self.assertEqual(self.window.left_workspace.current_page_id, second_home)
        np.testing.assert_allclose(
            np.asarray(self.window._current_3d_camera_position(), dtype=float), before_camera,
        )
        self.assertIsNone(self.window._blender_freeze_widget_states)

    def test_failure_and_cancel_restore_controls_and_current_page(self):
        first_id, first_page, second_id, second_home = self._load_two_sources()
        with self.assertRaises(BlenderFreezeError):
            capture_blender_snapshots(self.window, [(first_id, "missing")])
        self.assertEqual(self.window.source_manager.active_id, second_id)
        self.assertEqual(self.window.left_workspace.current_page_id, second_home)
        self.assertIsNone(self.window._blender_freeze_widget_states)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(BlenderFreezeError, "取消"):
            capture_blender_snapshots(
                self.window, [(first_id, first_page)], cancel_event=cancelled,
            )
        self.assertEqual(self.window.source_manager.active_id, second_id)
        self.assertEqual(self.window.left_workspace.current_page_id, second_home)

    def test_top_blender_action_opens_independent_dialog(self):
        self.assertTrue(hasattr(self.window, "btn_tb_blender"))
        self.assertTrue(self.window.open_blender_project_dialog())
        dialog = self.window._blender_project_dialog
        self.assertIsNotNone(dialog)
        self.assertNotIn("Blender", self.window.open_publication_dialog.__doc__)
        dialog.reject()


if __name__ == "__main__":
    unittest.main()
