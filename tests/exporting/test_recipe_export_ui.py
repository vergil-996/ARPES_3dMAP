import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.exporting.publication_dialog import PublicationExportDialog
from bandscope.exporting.publication_models import OutputOptions, resolve_style
from bandscope.exporting.recipe_export import publication_record
from tests.support.publication import _make_2d_snapshot


class RecipeExportUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = QWidget()
        self.window.settings = QSettings(str(Path(self.temp.name) / "settings.ini"), QSettings.IniFormat)
        self.dialog = PublicationExportDialog(self.window)
        self.addCleanup(self.window.close)
        self.addCleanup(self.dialog.close)

    def test_enabled_by_default_and_choice_remembered_only_in_this_window(self):
        self.assertTrue(self.dialog.chk_view_recipe.isChecked())
        self.dialog.chk_view_recipe.setChecked(False)
        second = PublicationExportDialog(self.window)
        self.addCleanup(second.close)
        self.assertFalse(second.chk_view_recipe.isChecked())
        other = QWidget()
        other.settings = self.window.settings
        third = PublicationExportDialog(other)
        self.addCleanup(other.close)
        self.addCleanup(third.close)
        self.assertTrue(third.chk_view_recipe.isChecked())

    def test_restored_layout_is_page_local_and_does_not_write_global_preferences(self):
        snapshot = _make_2d_snapshot()
        style = resolve_style("2d", "2d_boxed")
        options = OutputOptions(width_mm=183, height_mm=97, dpi=300, fmt="pdf", transparent=True)
        pub = publication_record(style, {"title_text": "Restored title"}, options)
        pub["style_params"]["tick_label_size"] = 12
        self.window._restored_publication = {snapshot.source_page_id: pub}
        before = self.window.settings.allKeys()
        with patch.object(self.dialog, "_regenerate_previews"):
            self.dialog._adopt_snapshot(snapshot)
        self.assertEqual(self.dialog._collect_output_options(), options)
        self.assertEqual(self.dialog._draft_overrides["title_text"], "Restored title")
        self.assertEqual(self.dialog._current_style().params["tick_label_size"], 12)
        self.assertEqual(self.window.settings.allKeys(), before)

    def test_unchecking_exports_picture_without_a_recipe(self):
        dialog = self.dialog
        dialog.snapshot = _make_2d_snapshot()
        dialog._draft_style_id = "2d_boxed"
        dialog._draft_overrides = {}
        dialog._big_ready = dialog._editor_valid = True
        dialog.cards = {"2d_boxed": Mock()}
        dialog._load_output_options_ui()
        dialog.chk_view_recipe.setChecked(False)
        self.window._sanitize_save_path = lambda path, _: path
        self.window._update_screenshot_tooltip = Mock()
        self.window._show_message = Mock()
        self.window._toast_success = Mock()
        output = str(Path(self.temp.name) / "picture.png")
        with patch("bandscope.exporting.publication_dialog.QFileDialog.getSaveFileName", return_value=(output, "PNG (*.png)")), \
             patch("bandscope.exporting.publication_dialog.render_and_save") as render, \
             patch("bandscope.exporting.publication_dialog.export_pair") as pair:
            dialog._on_export()
        render.assert_called_once()
        pair.assert_not_called()
        self.window._show_message.assert_not_called()
