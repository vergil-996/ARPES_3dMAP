import unittest

from PyQt5.QtWidgets import QApplication

from bandscope.ui.path_slice_controls import PathSegmentBar


class PathSegmentBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.bar = PathSegmentBar()
        self.bar.set_state(
            names=["Γ", "K", "M", "Γ"], lengths=[1.0, 0.05, 2.0],
            selected=[0], thickness=0.0, multi_thickness=False,
            unit="Å⁻¹", step=0.1,
        )
        self.addCleanup(self.bar.deleteLater)

    def test_shift_click_supports_noncontiguous_selection_and_removal(self):
        selections = []
        self.bar.selection_changed.connect(selections.append)
        self.bar.overview.segment_clicked.emit(2, True)
        self.bar._selected = [0, 2]
        self.bar.overview.segment_clicked.emit(0, True)
        self.assertEqual(selections, [[0, 2], [2]])

    def test_multi_value_thickness_does_not_overwrite_until_user_edits(self):
        self.bar.set_state(
            names=["A", "B", "C"], lengths=[1, 1], selected=[0, 1],
            thickness=None, multi_thickness=True, unit="index", step=1,
        )
        self.assertEqual(self.bar.thickness.text(), "多值")
        values = []
        self.bar.thickness_changed.connect(values.append)
        self.bar.thickness.setValue(0.5)
        self.assertEqual(values, [])
        self.bar.thickness.lineEdit().textEdited.emit("0.75")
        self.bar.thickness.setValue(0.75)
        self.assertEqual(values, [0.75])

    def test_empty_selection_disables_segment_editor(self):
        self.bar.set_state(
            names=["A", "B"], lengths=[1], selected=[], thickness=None,
            multi_thickness=False, unit="index", step=1,
        )
        self.assertFalse(self.bar.editor.isEnabled())

    def test_narrow_window_gives_path_an_independent_row(self):
        self.bar.resize(760, 100)
        self.bar._apply_wrap(False)
        wide_height = self.bar.height()
        self.bar.resize(480, 100)
        self.bar._apply_wrap(True)
        self.assertFalse(self.bar.secondary_row.isHidden())
        self.assertGreater(self.bar.height(), wide_height)
        self.bar.resize(760, 100)
        self.bar._apply_wrap(False)
        self.assertTrue(self.bar.secondary_row.isHidden())

    def test_scrollbar_appears_only_for_partial_viewport(self):
        self.bar.set_state(
            names=["A", "B"], lengths=[1], selected=[0], thickness=0,
            multi_thickness=False, unit="index", step=1,
            viewport=(0, 0.5), full_viewport=(0, 1),
        )
        self.assertFalse(self.bar.scrollbar.isHidden())
        starts = []
        self.bar.viewport_start_changed.connect(starts.append)
        self.bar.scrollbar.setValue(500)
        self.assertEqual(starts, [0.5])


if __name__ == "__main__":
    unittest.main()
