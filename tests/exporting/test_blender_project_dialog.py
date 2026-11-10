import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QMessageBox, QWidget

from bandscope.exporting.blender_project_dialog import BlenderProjectDialog
from tests.support.blender import make_blender_snapshot, write_fake_blender_project
from tests.support.publication import MemoryPublicationSettings


class _Window(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = MemoryPublicationSettings()
        self.source_manager = None
        self.messages = []

    def _show_message(self, *args):
        self.messages.append(args)


class BlenderProjectEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        first = make_blender_snapshot(cmap_name="magma", levels=(0.0, 50.0, 100.0))
        second = make_blender_snapshot(cmap_name="viridis", levels=(10.0, 70.0, 90.0))
        self.blend, _ = write_fake_blender_project(self.temp.name, [first, second])
        self.window = _Window()
        self.dialog = BlenderProjectDialog(self.window)
        self.editor = self.dialog.editor
        self.editor.load_project(self.blend)

    def tearDown(self):
        self.editor.close_preview()
        self.dialog.reject()
        self.dialog.deleteLater()
        self.window.close()
        self.temp.cleanup()

    def test_entry_switch_keeps_display_state_isolated(self):
        self.assertEqual(self.editor.current_entry_id, "entry-001")
        self.editor.level_spins[0].setValue(12.0)
        self.editor.cmap_combo.setCurrentText("plasma")
        self.editor.entry_combo.setCurrentIndex(1)
        self.assertEqual(self.editor.level_spins[0].value(), 10.0)
        self.assertEqual(self.editor.cmap_combo.currentText(), "viridis")
        self.editor.level_spins[0].setValue(22.0)
        self.editor.entry_combo.setCurrentIndex(0)
        self.assertEqual(self.editor.level_spins[0].value(), 12.0)
        self.assertEqual(self.editor.cmap_combo.currentText(), "plasma")
        self.assertEqual(self.editor._snapshots["entry-002"].levels_params[0], 22.0)
        self.assertEqual(self.editor._dirty_ids, {"entry-001", "entry-002"})

    def test_baked_effect_warning_can_cancel_update(self):
        snapshot = self.editor._snapshots["entry-001"]
        snapshot.payload["blender_project"]["baked_effects"] = ["local_enhancement"]
        self.editor.level_spins[1].setValue(60.0)
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.Cancel) as warning:
            self.assertFalse(self.editor.start_update())
        warning.assert_called_once()
        self.assertIsNone(self.editor._task)

    def test_invalid_open_does_not_replace_current_project(self):
        previous = self.editor.project
        invalid = Path(self.temp.name) / "ordinary.blend"
        invalid.write_bytes(b"ordinary")
        with patch.object(QMessageBox, "warning"):
            self.assertFalse(self.dialog.open_existing(invalid))
        self.assertIs(self.editor.project, previous)

    def test_update_action_is_named_and_reports_reload_requirement(self):
        self.assertEqual(self.editor.update_button.text(), "更新工程")
        self.assertIn("重载 VDB", self.editor.status_label.text())
        self.editor.level_spins[0].setValue(3.0)
        self.assertIn("1 条目", self.editor.update_button.text())


if __name__ == "__main__":
    unittest.main()
