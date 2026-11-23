import os
import tempfile
import unittest

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QDialogButtonBox

from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.axis_interval import (
    AUTO_DOCKING,
    CONTINUOUS_DOCKING,
    PAN_FRAMES,
    PAN_PHYSICAL,
    FourthAxisDragSettings,
)
from bandscope.ui.axis_assignment_dialog import AxisAssignmentDialog
from bandscope.ui.page_tree import SourceSummaryCard


class AxisAssignmentDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = np.arange(2 * 3 * 4 * 5, dtype=np.float32).reshape(2, 3, 4, 5)
        path = os.path.join(self.temp.name, "scan.npz")
        np.savez(
            path,
            sample=self.raw,
            kx=np.arange(4),
            ky=np.arange(5),
            E=np.arange(3),
            time=np.asarray([-1.0, 2.0]),
            stage=np.asarray([10.0, 30.0]),
            invalid=np.asarray([np.nan, 1.0]),
            axis_order=np.asarray(["T", "E", "X", "Y"]),
        )
        self.core = AnalyzerCore()
        success, info = self.core.load_npz(path)
        self.assertTrue(success, info)

    def dialog(self, *, as_new_source=False, fourth_axis_drag=None):
        dialog = AxisAssignmentDialog(
            self.core.axis_assignment,
            self.core.auto_axis_assignment,
            self.core.coordinate_candidates,
            candidate_units=self.core.coordinate_candidate_units,
            fourth_axis_drag=fourth_axis_drag,
            as_new_source=as_new_source,
        )
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_original_dimensions_candidates_and_preview_are_visible(self):
        dialog = self.dialog(as_new_source=True)
        self.assertIn("2 × 3 × 4 × 5", dialog.shape_label.text())
        self.assertEqual(
            dialog.button_box.button(QDialogButtonBox.Ok).text(), "作为新数据源打开"
        )
        coordinates = dialog._rows["T"]["coordinate"]
        items = [coordinates.itemText(index) for index in range(coordinates.count())]
        self.assertIn("stage", items)
        self.assertNotIn("invalid", items)
        self.assertEqual(dialog._rows["T"]["preview"].text(), "-1 … 2")

    def test_scan_type_edit_keeps_frame_order_and_intensity_view(self):
        dialog = self.dialog()
        index = dialog.scan_kind.findData("temperature")
        dialog.scan_kind.setCurrentIndex(index)
        dialog.scan_unit.setText("K")
        updated = dialog.assignment()
        reassigned = self.core.copy_with_assignment(updated)
        self.assertEqual(updated.scan_name, "温度")
        self.assertEqual(updated.scan_unit, "K")
        self.assertTrue(np.shares_memory(reassigned.raw_data, self.core.raw_data))
        np.testing.assert_array_equal(reassigned.raw_data, self.core.raw_data)

    def test_custom_scan_type_keeps_editable_name_unit_and_frames(self):
        dialog = self.dialog()
        dialog.scan_kind.setCurrentIndex(dialog.scan_kind.findData("custom"))
        dialog.scan_name.setText("磁场")
        dialog.scan_unit.setText("mT")
        updated = dialog.assignment()
        reassigned = self.core.copy_with_assignment(updated)
        self.assertEqual(updated.scan_kind, "custom")
        self.assertEqual(reassigned.scan_name, "磁场")
        self.assertEqual(reassigned.scan_unit, "mT")
        np.testing.assert_array_equal(reassigned.raw_data, self.core.raw_data)
        np.testing.assert_array_equal(reassigned.coords["delay"], [-1.0, 2.0])

    def test_duplicate_dimensions_are_rejected_and_automatic_can_be_restored(self):
        dialog = self.dialog()
        x_dimension = dialog._rows["X"]["dimension"]
        y_dimension = dialog._rows["Y"]["dimension"]
        y_dimension.setCurrentIndex(x_dimension.currentIndex())
        with self.assertRaisesRegex(ValueError, "不同"):
            dialog.assignment()
        dialog.restore_automatic()
        restored = dialog.assignment()
        self.assertEqual(restored.axis_roles, self.core.auto_axis_assignment.axis_roles)
        self.assertEqual(restored.coord_keys, self.core.auto_axis_assignment.coord_keys)

    def test_scan_slider_settings_live_in_dialog_without_marking_axes_dirty(self):
        dialog = self.dialog()
        self.assertEqual(dialog.docking_combo.currentData(), AUTO_DOCKING)
        self.assertIn("离散停靠", dialog.docking_combo.itemText(0))
        self.assertEqual(dialog.pan_combo.currentData(), PAN_PHYSICAL)
        self.assertFalse(dialog.assignment_changed())
        self.assertFalse(dialog.fourth_axis_drag_changed())

        dialog.pan_combo.setCurrentIndex(dialog.pan_combo.findData(PAN_FRAMES))
        dialog.docking_combo.setCurrentIndex(
            dialog.docking_combo.findData(CONTINUOUS_DOCKING)
        )
        self.assertEqual(
            dialog.fourth_axis_drag(),
            FourthAxisDragSettings(CONTINUOUS_DOCKING, PAN_FRAMES),
        )
        self.assertTrue(dialog.fourth_axis_drag_changed())
        self.assertFalse(dialog.assignment_changed())

        dialog.restore_automatic()
        self.assertEqual(dialog.docking_combo.currentData(), CONTINUOUS_DOCKING)
        self.assertEqual(dialog.pan_combo.currentData(), PAN_FRAMES)

    def test_slider_auto_mode_tracks_selected_scan_dimension_length(self):
        assignment = self.core.axis_assignment.clone()
        automatic = self.core.auto_axis_assignment.clone()
        assignment.original_shape = (30, 3, 4, 5)
        automatic.original_shape = (30, 3, 4, 5)
        candidates = dict(self.core.coordinate_candidates)
        candidates["time"] = np.arange(30)
        candidates["stage"] = np.arange(30)
        dialog = AxisAssignmentDialog(
            assignment,
            automatic,
            candidates,
            candidate_units=self.core.coordinate_candidate_units,
        )
        self.addCleanup(dialog.deleteLater)
        self.assertIn("连续拖动", dialog.docking_combo.itemText(0))

        scan_dimension = dialog._rows["T"]["dimension"]
        scan_dimension.setCurrentIndex(scan_dimension.findData(1))
        self.assertIn("离散停靠", dialog.docking_combo.itemText(0))

    def test_static_data_disables_scan_slider_settings(self):
        dialog = self.dialog()
        scan_dimension = dialog._rows["T"]["dimension"]
        scan_dimension.setCurrentIndex(scan_dimension.findData(-1))
        self.assertFalse(dialog.docking_combo.isEnabled())
        self.assertFalse(dialog.pan_combo.isEnabled())

    def test_slider_only_action_does_not_announce_a_new_source(self):
        dialog = self.dialog(as_new_source=True)
        ok = dialog.button_box.button(QDialogButtonBox.Ok)
        self.assertEqual(ok.text(), "作为新数据源打开")
        dialog.pan_combo.setCurrentIndex(dialog.pan_combo.findData(PAN_FRAMES))
        self.assertEqual(ok.text(), "应用")
        dialog.scan_kind.setCurrentIndex(dialog.scan_kind.findData("temperature"))
        self.assertEqual(ok.text(), "作为新数据源打开")

    def test_source_card_emits_for_mouse_and_keyboard(self):
        card = SourceSummaryCard()
        self.addCleanup(card.deleteLater)
        card.resize(220, 82)
        card.show()
        activated = []
        card.activated.connect(lambda: activated.append(True))
        QTest.mouseClick(card, Qt.LeftButton)
        QTest.keyClick(card, Qt.Key_Return)
        QTest.keyClick(card, Qt.Key_Space)
        self.assertEqual(len(activated), 3)


if __name__ == "__main__":
    unittest.main()
