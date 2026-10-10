"""真实 Qt 编辑器与截图面板的批量编辑、草稿、页面记忆及提交边界。"""
import copy
import unittest
from unittest.mock import patch

from PyQt5.QtCore import Qt, QItemSelectionModel
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.exporting.curve_presentation import publication_session, load_presentation, save_presets
from bandscope.exporting.publication_dialog import PublicationExportDialog
from tests.support.publication import _make_1d_comparison_snapshot, _make_2d_snapshot, MemoryPublicationSettings


class CurveStyleUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        self.window.settings = MemoryPublicationSettings()
        self.window._sanitize_save_path = lambda path, selected: path
        self.window._show_message = lambda *args: None
        self.window._update_screenshot_tooltip = lambda: None
        self.window._toast_success = lambda *args: None
        self.dialog = PublicationExportDialog(self.window)
        self.dialog._regenerate_previews = lambda: None
        self.snapshot = _make_1d_comparison_snapshot()
        for index, curve in enumerate(self.snapshot.payload["curves"]):
            curve.update(curve_id=f"curve-{index}", label="同名")
        self.dialog._adopt_snapshot(self.snapshot)
        self.editor = self.dialog.curve_editor

    def tearDown(self):
        self.dialog.close()
        self.dialog._pool.waitForDone(5000)
        self.dialog.deleteLater()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def select(self, *rows):
        model = self.editor.table.selectionModel()
        model.clearSelection()
        for row in rows:
            model.select(self.editor.table.model().index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)

    def test_batch_edit_is_one_undo_and_only_changes_requested_field(self):
        self.select(0, 1)
        spin = self.editor.fields["curve", "linewidth"][0]
        spin.setValue(2.3)
        self.assertEqual(self.dialog.presentation.curves, {"curve-0": {"linewidth": 2.3}, "curve-1": {"linewidth": 2.3}})
        self.editor.undo()
        self.assertEqual(self.dialog.presentation.curves, {})
        self.editor.redo()
        self.assertEqual(self.dialog.presentation.curves["curve-1"]["linewidth"], 2.3)

    def test_reorder_visibility_and_labels_remain_independent(self):
        self.select(0)
        edit = self.editor.fields["curve", "label"][0]
        edit.setText("基准新名")
        edit.editingFinished.emit()
        self.editor._move(1)
        self.assertEqual(self.dialog.presentation.order, ["curve-1", "curve-0"])
        self.assertEqual(self.editor.table.item(1, 0).data(Qt.UserRole), "curve-0")
        self.editor.table.item(1, 0).setCheckState(Qt.Unchecked)
        self.assertFalse(self.dialog.presentation.curves["curve-0"]["visible"])
        self.assertEqual(self.dialog.presentation.curves["curve-0"]["label"], "基准新名")

    def test_mixed_fields_and_unchanged_blur_do_not_create_overrides(self):
        self.dialog.presentation.curves = {"curve-0": {"linewidth": 2, "color": "#123456"},
                                          "curve-1": {"linewidth": 3, "color": "#654321"}}
        self.editor.set_snapshot(self.snapshot, self.dialog.presentation, self.dialog._current_style())
        self.select(0, 1)
        spin = self.editor.fields["curve", "linewidth"][0]
        self.assertEqual(spin.text(), "混合")
        color = self.editor.fields["curve", "color"][0]
        before = copy.deepcopy(self.dialog.presentation.curves)
        color.editingFinished.emit()
        self.assertEqual(self.dialog.presentation.curves, before)
        self.assertTrue(self.dialog._editor_valid)

    def test_template_switch_and_same_page_refresh_preserve_drafts(self):
        self.dialog.edit_title.setText("自定义标题")
        self.editor.fields["curve", "linewidth"][0].setValue(2)
        self.dialog._on_card_clicked("1d_boxed")
        self.dialog._on_card_clicked("1d_open")
        self.assertEqual(self.dialog.edit_title.text(), "自定义标题")
        self.assertEqual(self.dialog.presentation.curves["curve-0"]["linewidth"], 2)
        refreshed = copy.deepcopy(self.snapshot)
        refreshed.snapshot_id = "fresh"
        refreshed.payload["curves"].reverse()
        self.dialog._adopt_snapshot(refreshed)
        self.assertEqual(self.dialog.presentation.curves["curve-0"]["linewidth"], 2)
        self.assertEqual(self.dialog.edit_title.text(), "自定义标题")

    def test_close_reopen_and_another_page_do_not_share_curve_overrides(self):
        self.editor.fields["curve", "linewidth"][0].setValue(2)
        self.dialog.reject()
        self.dialog._adopt_snapshot(self.snapshot)
        self.assertEqual(self.dialog.presentation.curves["curve-0"]["linewidth"], 2)
        other = copy.deepcopy(self.snapshot)
        other.source_page_id = "another-page"
        self.dialog._adopt_snapshot(other)
        self.assertEqual(self.dialog.presentation.curves, {})
        self.dialog._adopt_snapshot(self.snapshot)
        self.assertEqual(self.dialog.presentation.curves["curve-0"]["linewidth"], 2)

    def test_undo_and_redo_include_title_and_template(self):
        self.dialog.edit_title.setText("new title")
        self.editor.undo()
        self.assertNotIn("title_text", self.dialog._draft_overrides)
        self.editor.redo()
        self.assertEqual(self.dialog.edit_title.text(), "new title")
        self.dialog._on_card_clicked("1d_boxed")
        self.editor.undo()
        self.assertEqual(self.dialog._draft_style_id, "1d_open")
        self.assertEqual(self.dialog.edit_title.text(), "new title")

    def test_invalid_ranges_block_export_and_survive_unrelated_edits(self):
        self.dialog._big_ready = True
        edit = self.editor.fields["figure", "xlim"][0]
        edit.setText("2, 1")
        edit.editingFinished.emit()
        self.assertFalse(self.dialog._editor_valid)
        self.assertFalse(self.dialog.btn_export.isEnabled())
        self.editor.fields["curve", "linewidth"][0].setValue(2)
        self.assertEqual(edit.text(), "2, 1")
        edit.setText("-1, 1")
        edit.editingFinished.emit()
        self.assertTrue(self.dialog._editor_valid)
        self.assertEqual(self.dialog.presentation.figure["xlim"], [-1, 1])
        edit.clear()
        edit.editingFinished.emit()
        self.assertNotIn("xlim", self.dialog.presentation.figure)

    def test_preview_signature_and_revision_change_before_debounce(self):
        old_key = self.dialog._preview_key("1d_open", 150)
        revision = self.dialog._preview_revision
        self.dialog._big_ready = True
        self.editor.fields["curve", "linewidth"][0].setValue(2)
        self.assertNotEqual(old_key, self.dialog._preview_key("1d_open", 150))
        self.assertGreater(self.dialog._preview_revision, revision)
        self.assertFalse(self.dialog._big_ready)
        self.assertFalse(self.dialog.btn_export.isEnabled())

    def test_common_controls_return_to_2d_and_back_without_losing_connections(self):
        self.assertGreaterEqual(self.editor.common_form.indexOf(self.dialog.edit_title), 0)
        self.dialog._adopt_snapshot(_make_2d_snapshot())
        self.assertGreaterEqual(self.dialog.title_row.indexOf(self.dialog.edit_title), 0)
        self.dialog.edit_title.setText("2d title")
        self.assertEqual(self.dialog._draft_overrides["title_text"], "2d title")
        self.dialog._adopt_snapshot(self.snapshot)
        self.dialog.edit_title.setText("1d title")
        self.assertEqual(self.dialog._draft_overrides["title_text"], "1d title")

    def test_reset_all_keeps_output_options_and_can_be_undone(self):
        self.dialog.combo_width.setCurrentIndex(1)
        self.dialog.edit_title.setText("changed")
        self.editor.fields["curve", "linewidth"][0].setValue(2)
        self.editor.reset_all()
        self.assertEqual(self.dialog.presentation.curves, {})
        self.assertEqual(self.dialog._draft_overrides, {})
        self.assertEqual(self.dialog._collect_output_options().width_mm, 183)
        self.editor.undo()
        self.assertEqual(self.dialog.presentation.curves["curve-0"]["linewidth"], 2)
        self.assertEqual(self.dialog.edit_title.text(), "changed")

    def test_cancelled_or_failed_export_does_not_commit_general_style(self):
        self.editor.fields["figure", "title_size"][0].setValue(12)
        self.dialog._big_ready = True
        with patch("bandscope.exporting.publication_dialog.QFileDialog.getSaveFileName", return_value=("", "")):
            self.dialog._on_export()
        self.assertEqual(load_presentation(self.window.settings, "1d_open").figure, {})
        with patch("bandscope.exporting.publication_dialog.QFileDialog.getSaveFileName", return_value=("figure.png", "PNG (*.png)")), \
             patch("bandscope.exporting.publication_dialog.render_and_save", side_effect=OSError("save failed")):
            self.dialog._on_export()
        self.assertEqual(load_presentation(self.window.settings, "1d_open").figure, {})

    def test_successful_export_commits_only_general_style(self):
        self.editor.fields["figure", "title_size"][0].setValue(12)
        self.editor.fields["curve", "label"][0].setText("scientific label")
        self.editor.fields["curve", "label"][0].editingFinished.emit()
        self.dialog._big_ready = True
        with patch("bandscope.exporting.publication_dialog.QFileDialog.getSaveFileName", return_value=("figure.png", "PNG (*.png)")), \
             patch("bandscope.exporting.publication_dialog.render_and_save") as renderer:
            self.dialog._on_export()
        committed = load_presentation(self.window.settings, "1d_open")
        self.assertEqual(committed.figure["title_size"], 12)
        self.assertEqual(committed.curves, {})
        self.assertEqual(renderer.call_args.kwargs["presentation"].curves["curve-0"]["label"], "scientific label")

    def test_preset_applies_as_one_undo_keeps_scientific_content(self):
        self.dialog.edit_title.setText("scientific title")
        self.editor._field_changed("figure", "xlim", [-1, 1])
        self.editor._field_changed("curve", "label", "scientific curve")
        presets = {"paper": {"style_id": "1d_boxed", "overrides": {"font_english": "Arial"},
                             "presentation": {"defaults": {"linewidth": 2}, "figure": {"title_size": 12}}}}
        save_presets(self.window.settings, presets)
        self.editor._reload_presets()
        self.editor._apply_preset()
        self.assertEqual(self.dialog._draft_style_id, "1d_boxed")
        self.assertEqual(self.dialog.edit_title.text(), "scientific title")
        self.assertEqual(self.dialog.presentation.figure["xlim"], [-1, 1])
        self.assertEqual(self.dialog.presentation.curves["curve-0"]["label"], "scientific curve")
        self.editor.undo()
        self.assertEqual(self.dialog._draft_style_id, "1d_open")
        self.assertEqual(self.dialog.presentation.defaults, {})

    def test_annotations_invalid_values_block_export_and_removal_recovers(self):
        self.editor._add_annotation()
        self.editor.annotation_fields["x"][0].setText("nan")
        self.editor._annotation_changed()
        self.assertFalse(self.dialog._editor_valid)
        self.editor._remove_annotation()
        self.assertTrue(self.dialog._editor_valid)

    def test_page_close_or_new_data_removes_memory_and_prevents_readding(self):
        self.editor.fields["curve", "linewidth"][0].setValue(2)
        self.assertIn(self.snapshot.source_page_id, publication_session(self.window).pages)
        self.dialog.forget_page(self.snapshot.source_page_id)
        self.assertEqual(publication_session(self.window).pages, {})
        self.dialog._adopt_snapshot(self.snapshot)
        self.assertEqual(self.dialog.presentation.curves, {})
        self.dialog.clear_session()
        self.dialog.close()
        self.assertEqual(publication_session(self.window).pages, {})
