import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QCloseEvent, QDesktopServices, QPixmap
from PyQt5.QtWidgets import QApplication, QGroupBox, QWidget

from bandscope.exporting.blender_batch import BlenderPageCandidate
from bandscope.exporting.blender_export import BlenderExportResult
from bandscope.exporting.blender_materials import MATERIAL_SPECS
from bandscope.exporting.blender_project import BOX_GROUP_PRESET, PRESET_KEYS
from bandscope.exporting.blender_project_dialog import BlenderProjectDialog
from bandscope.exporting.publication_dialog import PublicationExportDialog
from tests.support.blender import make_blender_snapshot
from tests.support.publication import MemoryPublicationSettings, _make_2d_snapshot


class _Window(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = MemoryPublicationSettings()
        self.messages = []
        self.toasts = []
        self.source_manager = None

    def _show_message(self, *args):
        self.messages.append(args)

    def _toast_success(self, *args):
        self.toasts.append(args)


class _Pool:
    def __init__(self):
        self.tasks = []

    def start(self, task):
        self.tasks.append(task)


class BlenderPanelUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = _Window()
        self.dialog = BlenderProjectDialog(self.window)
        self.panel = self.dialog.export_panel
        self.snapshot = make_blender_snapshot()
        self.panel.set_snapshot(self.snapshot)
        self.tempdir = tempfile.TemporaryDirectory()
        self.executable = Path(self.tempdir.name) / "Blender 中文" / "blender.exe"
        self.executable.parent.mkdir()
        self.executable.write_bytes(b"placeholder")

    def tearDown(self):
        if self.panel.busy:
            self.panel._on_failed("cancelled", True)
        if self.dialog.editor.busy:
            self.dialog.editor._on_failed("cancelled", True)
        self.dialog.editor.close_preview()
        self.dialog.reject()
        self.dialog.deleteLater()
        self.window.close()
        self.tempdir.cleanup()

    def test_publication_dialog_has_no_blender_mode(self):
        dialog = PublicationExportDialog(self.window)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(hasattr(dialog, "combo_export_type"))
        self.assertFalse(hasattr(dialog, "blender_panel"))
        with patch.object(dialog, "_regenerate_previews"):
            dialog._adopt_snapshot(_make_2d_snapshot())
        self.assertEqual(dialog.btn_export.text(), "导出此预览")
        self.assertEqual(dialog.title_label.text(), "图片导出")
        self.assertIn("截图样式", dialog.windowTitle())
        dialog.reject()

    def test_page_tree_defaults_to_current_and_presets_all_selected(self):
        candidates = [
            BlenderPageCandidate("source-a", "page-a", "数据 A", "页面 A"),
            BlenderPageCandidate("source-a", "page-b", "数据 A", "页面 B", current=True),
            BlenderPageCandidate("source-b", "page-c", "数据 B", "页面 C"),
        ]
        with patch("bandscope.exporting.blender_panel.list_blender_pages", return_value=candidates):
            self.panel.refresh_pages()
        self.assertEqual(self.panel.selected_keys(), [("source-a", "page-b")])
        self.assertTrue(all(check.isChecked() for check in self.panel.preset_checks.values()))
        self.assertEqual(self.panel.page_tree.topLevelItemCount(), 2)
        current = self.panel.page_tree.topLevelItem(0).child(1)
        current.setCheckState(0, Qt.Unchecked)
        self.assertEqual(self.panel.selected_keys(), [])
        self.assertFalse(self.panel.can_export)
        self.assertIn("空选择", self.panel.status_label.text())

    def test_reference_thumbnail_is_explicitly_not_render_preview(self):
        pixmap = QPixmap(40, 30)
        pixmap.fill()
        self.panel.set_reference_pixmap(pixmap)
        self.assertFalse(self.panel.reference_label.pixmap().isNull())
        labels = [child.text() for child in self.panel.findChildren(type(self.panel.source_label))]
        self.assertTrue(any("不是 Blender 渲染预览" in text for text in labels))

    def test_unified_box_preset_replaces_colors_and_removes_material_layout_group(self):
        self.assertEqual(set(self.panel.preset_checks), set(PRESET_KEYS))
        self.assertEqual(self.panel.preset_checks[BOX_GROUP_PRESET].text(), "薄层长方体")
        labels = [check.text() for check in self.panel.preset_checks.values()]
        for old_label in ("绿色长方体", "橙色长方体", "蓝色长方体"):
            self.assertNotIn(old_label, labels)
        for attribute in (
            "material_combos", "parameter_spins", "layout_combo", "material_note",
        ):
            self.assertFalse(hasattr(self.panel, attribute))
        group_titles = [group.title() for group in self.panel.findChildren(QGroupBox)]
        self.assertFalse(any("薄层长方体材质与布局" in title for title in group_titles))

    def test_export_panel_is_scrollable_without_material_group(self):
        self.assertIs(self.dialog.stack.widget(0), self.dialog.export_scroll)
        self.assertIs(self.dialog.export_scroll.widget(), self.panel)
        self.assertTrue(self.dialog.export_scroll.widgetResizable())

    def test_busy_disables_all_preset_controls(self):
        self.panel._task = object()
        self.panel._set_busy_widgets(True)
        self.assertFalse(any(check.isEnabled() for check in self.panel.preset_checks.values()))
        self.panel._task = None
        self.panel._set_busy_widgets(False)
        self.assertTrue(all(check.isEnabled() for check in self.panel.preset_checks.values()))

    def test_started_task_keeps_original_request_and_cancel_is_cooperative(self):
        self.panel.path_edit.setText(str(self.executable))
        pool = _Pool()
        jobs = []

        class FakeJob:
            def __init__(self, request, _blender, output, **kwargs):
                self.request = request
                self.output_path = output
                self.cancel_event = kwargs["cancel_event"]
                jobs.append(self)

        with patch("bandscope.exporting.blender_panel.BlenderProjectExportJob", FakeJob), \
                patch.object(self.panel, "_update_reference"), \
                patch("bandscope.exporting.blender_panel.detect_blender", return_value=self.executable), \
                patch("bandscope.exporting.blender_panel.blender_version", return_value="5.2.3 LTS"), \
                patch("bandscope.exporting.blender_panel.QThreadPool.globalInstance", return_value=pool):
            started = self.panel.start_export(Path(self.tempdir.name) / "输出 中文.blend", overwrite=False)
        self.assertTrue(started)
        self.assertTrue(self.panel.busy)
        self.assertEqual(len(pool.tasks), 1)
        self.assertIs(jobs[0].request.snapshots[0], self.snapshot)
        self.assertEqual(jobs[0].request.box_layout, "right_front_groups")
        self.assertEqual(len(jobs[0].request.enabled_boxes()), 8)
        for spec in MATERIAL_SPECS:
            box = jobs[0].request.boxes[spec.material_id]
            self.assertEqual(box.material_id, spec.material_id)
            self.assertEqual(box.parameters, spec.default_parameters())
        replacement = make_blender_snapshot()
        replacement.snapshot_id = "replacement"
        self.panel.set_snapshot(replacement)
        self.assertIs(jobs[0].request.snapshots[0], self.snapshot)
        self.assertEqual(jobs[0].request.snapshots[0].snapshot_id, "blender-synthetic")
        self.panel.preset_checks[BOX_GROUP_PRESET].setChecked(False)
        self.assertTrue(jobs[0].request.presets[BOX_GROUP_PRESET])
        self.assertEqual(len(jobs[0].request.enabled_boxes()), 8)
        self.assertEqual(jobs[0].request.box_layout, "right_front_groups")
        self.panel.cancel()
        self.assertTrue(jobs[0].cancel_event.is_set())
        pool.tasks[0].signals.failed.emit("cancelled", True)
        self.assertFalse(self.panel.busy)
        self.assertIn("已取消", self.panel.status_label.text())

    def test_export_slot_ignores_button_checked_flag(self):
        # QPushButton.clicked 会把 checked(bool) 作为第一个位置参数传入；
        # 若它落到 output_path 上，Path(False) 的 TypeError 会让 PyQt5 qFatal 闪退。
        self.panel.path_edit.setText(str(self.executable))
        with patch("bandscope.exporting.blender_panel.detect_blender", return_value=self.executable), \
                patch("bandscope.exporting.blender_panel.blender_version", return_value="5.2.3 LTS"), \
                patch("bandscope.exporting.blender_panel.QFileDialog.getSaveFileName",
                      return_value=("", "")) as save_dialog:
            started = self.panel.start_export(False)
        self.assertFalse(started)
        save_dialog.assert_called_once()
        self.assertFalse(self.panel.busy)

    def test_missing_blender_cannot_start(self):
        with patch("bandscope.exporting.blender_panel.detect_blender", return_value=None):
            self.assertFalse(self.panel.start_export(Path(self.tempdir.name) / "scene.blend", overwrite=False))
        self.assertFalse(self.panel.busy)
        self.assertIn("禁止开始导出", self.panel.detection_label.text())

    def test_invalid_blender_version_cannot_start_or_enable_export(self):
        self.panel.path_edit.setText(str(self.executable))
        with patch("bandscope.exporting.blender_panel.detect_blender", return_value=self.executable), \
                patch("bandscope.exporting.blender_panel.blender_version", side_effect=RuntimeError("not Blender")):
            self.assertFalse(self.panel.start_export(Path(self.tempdir.name) / "scene.blend", overwrite=False))
        self.assertFalse(self.panel.busy)
        self.assertFalse(self.panel.can_export)
        self.assertIn("未开始导出", self.panel.detection_label.text())

    def test_success_offers_project_and_folder_actions(self):
        final = Path(self.tempdir.name) / "scene.blend"
        result = BlenderExportResult(final, Path(self.tempdir.name) / "scene.assets", {})
        with patch("bandscope.exporting.blender_panel.QDesktopServices.openUrl") as open_url:
            self.panel._on_succeeded(result)
            self.assertFalse(self.panel.open_project_button.isHidden())
            self.assertFalse(self.panel.open_folder_button.isHidden())
            self.panel.open_project()
            self.panel.open_folder()
            self.assertEqual(open_url.call_count, 2)
        self.assertIn("scene.assets", self.panel.status_label.text())

    def test_dialog_close_cancels_busy_task_first(self):
        self.panel._task = object()
        self.panel._cancel_event = threading.Event()
        event = QCloseEvent()
        self.dialog.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertTrue(self.panel._cancel_event.is_set())
        self.assertTrue(self.dialog._close_after_task)
        self.panel._on_failed("cancelled", True)


if __name__ == "__main__":
    unittest.main()
