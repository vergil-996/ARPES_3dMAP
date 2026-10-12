"""Directory history is shared across operations and survives new instances."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.ui.file_dialogs import LAST_DIRECTORY_KEY, RememberingFileDialog


class FileDialogHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.ini = str(self.root / "settings.ini")
        self.window = QWidget()
        self.window.settings = QSettings(self.ini, QSettings.IniFormat)
        self.addCleanup(self.window.close)

    def test_open_then_save_uses_chosen_directory_and_keeps_filename(self):
        folder = self.root / "实验数据"
        folder.mkdir()
        chosen = str(folder / "input.npz")
        with patch("bandscope.ui.file_dialogs.QtFileDialog.getOpenFileName", return_value=(chosen, "NPZ")):
            self.assertEqual(RememberingFileDialog.getOpenFileName(self.window)[0], chosen)
        with patch("bandscope.ui.file_dialogs.QtFileDialog.getSaveFileName", return_value=("", "")) as dialog:
            RememberingFileDialog.getSaveFileName(self.window, "图片", "figure.png", "PNG")
        self.assertEqual(dialog.call_args.args[2], str(folder / "figure.png"))

    def test_save_then_open_and_new_settings_instance_share_history(self):
        chosen = str(self.root / "export.npz")
        with patch("bandscope.ui.file_dialogs.QtFileDialog.getSaveFileName", return_value=(chosen, "NPZ")):
            RememberingFileDialog.getSaveFileName(self.window)
        self.window.settings = QSettings(self.ini, QSettings.IniFormat)
        with patch("bandscope.ui.file_dialogs.QtFileDialog.getOpenFileName", return_value=("", "")) as dialog:
            RememberingFileDialog.getOpenFileName(self.window)
        self.assertEqual(dialog.call_args.args[2], str(self.root))

    def test_cancellation_does_not_change_history(self):
        self.window.settings.setValue(LAST_DIRECTORY_KEY, str(self.root))
        for method in ("getOpenFileName", "getSaveFileName"):
            with self.subTest(method=method), patch(f"bandscope.ui.file_dialogs.QtFileDialog.{method}", return_value=("", "")):
                getattr(RememberingFileDialog, method)(self.window)
            self.assertEqual(self.window.settings.value(LAST_DIRECTORY_KEY), str(self.root))

    def test_missing_history_or_deleted_directory_keeps_default(self):
        for remembered in ("", str(self.root / "deleted")):
            self.window.settings.setValue(LAST_DIRECTORY_KEY, remembered)
            with patch("bandscope.ui.file_dialogs.QtFileDialog.getSaveFileName", return_value=("", "")) as dialog:
                RememberingFileDialog.getSaveFileName(self.window, directory="default.npz")
            self.assertEqual(dialog.call_args.args[2], "default.npz")

    def test_child_dialog_uses_parent_settings_and_passes_qt_options(self):
        child = QWidget(self.window)
        self.window.settings.setValue(LAST_DIRECTORY_KEY, str(self.root))
        with patch("bandscope.ui.file_dialogs.QtFileDialog.getSaveFileName", return_value=("", "")) as dialog:
            RememberingFileDialog.getSaveFileName(child, "保存", "file.csv", "CSV", initialFilter="CSV")
        self.assertEqual(dialog.call_args.args[2], str(self.root / "file.csv"))
        self.assertEqual(dialog.call_args.kwargs, {"initialFilter": "CSV"})

    def test_absolute_suggested_filename_uses_latest_directory(self):
        other = self.root / "other"
        other.mkdir()
        self.window.settings.setValue(LAST_DIRECTORY_KEY, str(other))
        with patch("bandscope.ui.file_dialogs.QtFileDialog.getSaveFileName", return_value=("", "")) as dialog:
            RememberingFileDialog.getSaveFileName(self.window, directory=str(self.root / "converted.npz"))
        self.assertEqual(dialog.call_args.args[2], str(other / "converted.npz"))

    def test_all_existing_entry_points_use_shared_dialog(self):
        from bandscope.app.refactored_app import QFileDialog as data_dialog
        from bandscope.exporting.publication_dialog import QFileDialog as image_dialog
        from bandscope.extensions.plugin_dialog import QFileDialog as install_dialog
        from bandscope.extensions.ui import RememberingFileDialog as public_dialog
        from plugins.flat_band_opacity.entry import QFileDialog as preset_dialog

        for dialog in (data_dialog, image_dialog, install_dialog, public_dialog, preset_dialog):
            self.assertIs(dialog, RememberingFileDialog)
