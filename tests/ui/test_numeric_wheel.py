import unittest

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QDoubleValidator, QWheelEvent
from PyQt5.QtWidgets import QApplication, QDoubleSpinBox, QHBoxLayout, QLineEdit, QSpinBox, QWidget
from siui.components.editbox import SiDoubleSpinBox, SiSpinBox

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.ui import theme
from bandscope.ui.crop_controls import CropPopup
from bandscope.ui.numeric_wheel import install_numeric_wheel_filter


configure_qt_plugin_path()


def send_wheel(widget, delta=120):
    position = QPoint(10, 10)
    event = QWheelEvent(
        position,
        position,
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.NoScrollPhase,
        False,
    )
    accepted = QApplication.sendEvent(widget, event)
    return accepted, event


class _FocusedSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _HoverSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        if not self.hasFocus():
            self.setFocus(Qt.MouseFocusReason)
        super().wheelEvent(event)


class NumericWheelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply_siui_palette()
        cls.event_filter = install_numeric_wheel_filter(cls.app)

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def make_widget(self, widget, *, focus=True):
        self.widgets.append(widget)
        widget.show()
        self.app.processEvents()
        if focus:
            widget.setFocus(Qt.OtherFocusReason)
            self.app.processEvents()
        return widget

    def test_integer_wheel_uses_caret_digit_and_restores_single_step(self):
        box = self.make_widget(QSpinBox())
        box.setRange(-100000, 100000)
        box.setSingleStep(7)
        box.setValue(12345)
        box.lineEdit().setCursorPosition(3)

        accepted, _ = send_wheel(box)

        self.assertTrue(accepted)
        self.assertEqual(box.value(), 12445)
        self.assertEqual(box.singleStep(), 7)
        self.assertEqual(box.lineEdit().cursorPosition(), 3)

    def test_double_wheel_covers_integer_and_fraction_digits(self):
        box = self.make_widget(QDoubleSpinBox())
        box.setRange(-1000.0, 1000.0)
        box.setDecimals(2)
        cases = ((1, 223.45), (2, 133.45), (3, 124.45), (5, 123.55), (6, 123.46))
        for cursor, expected in cases:
            with self.subTest(cursor=cursor):
                box.setValue(123.45)
                box.lineEdit().setCursorPosition(cursor)
                accepted, _ = send_wheel(box)
                self.assertTrue(accepted)
                self.assertAlmostEqual(box.value(), expected)
                self.assertEqual(box.lineEdit().cursorPosition(), cursor)

    def test_wheel_down_subtracts_the_selected_digit(self):
        box = self.make_widget(QDoubleSpinBox())
        box.setRange(-1000.0, 1000.0)
        box.setDecimals(2)
        box.setValue(123.45)
        box.lineEdit().setCursorPosition(6)

        send_wheel(box, -120)

        self.assertAlmostEqual(box.value(), 123.44)
        self.assertEqual(box.lineEdit().cursorPosition(), 6)

    def test_carry_keeps_caret_on_the_same_decimal_place(self):
        box = self.make_widget(QSpinBox())
        box.setRange(0, 999)
        box.setValue(99)
        box.lineEdit().setCursorPosition(2)

        send_wheel(box)

        self.assertEqual(box.value(), 100)
        self.assertEqual(box.lineEdit().text(), "100")
        self.assertEqual(box.lineEdit().cursorPosition(), 3)

    def test_negative_value_steps_numerically_up(self):
        box = self.make_widget(QDoubleSpinBox())
        box.setRange(-100.0, 100.0)
        box.setDecimals(2)
        box.setValue(-12.34)
        box.lineEdit().setCursorPosition(2)

        send_wheel(box)

        self.assertAlmostEqual(box.value(), -2.34)
        self.assertEqual(box.lineEdit().cursorPosition(), 2)

    def test_prefix_and_suffix_do_not_change_digit_mapping(self):
        box = self.make_widget(QSpinBox())
        box.setRange(0, 999)
        box.setPrefix("n=")
        box.setSuffix(" px")
        box.setValue(120)
        box.lineEdit().setCursorPosition(4)

        send_wheel(box)

        self.assertEqual(box.value(), 130)
        self.assertEqual(box.lineEdit().cursorPosition(), 4)

    def test_siui_spin_boxes_use_digits_instead_of_configured_step(self):
        integer_box = self.make_widget(SiSpinBox())
        integer_box.setMaximum(1000)
        integer_box.setSingleStep(5)
        integer_box.setValue(15)
        integer_box.setCursorPosition(1)
        send_wheel(integer_box)
        self.assertEqual(integer_box.value(), 25)
        self.assertEqual(integer_box.singleStep(), 5)
        self.assertEqual(integer_box.cursorPosition(), 1)

        double_box = self.make_widget(SiDoubleSpinBox())
        double_box.setMaximum(1000.0)
        double_box.setSingleStep(0.5)
        double_box.setValue(1.25)
        double_box.setCursorPosition(3)
        send_wheel(double_box)
        self.assertAlmostEqual(double_box.value(), 1.35)
        self.assertEqual(double_box.singleStep(), 0.5)
        self.assertEqual(double_box.cursorPosition(), 3)

    def make_focus_pair(self, box_type):
        container = self.make_widget(QWidget())
        layout = QHBoxLayout(container)
        box = box_type()
        other = QLineEdit()
        layout.addWidget(box)
        layout.addWidget(other)
        other.setFocus(Qt.OtherFocusReason)
        self.app.processEvents()
        box.setValue(12.0)
        box.lineEdit().setCursorPosition(2)
        self.assertFalse(box.hasFocus())
        return box

    def test_existing_unfocused_ignore_behavior_is_preserved(self):
        box = self.make_focus_pair(_FocusedSpinBox)

        send_wheel(box)

        self.assertFalse(box.hasFocus())
        self.assertEqual(box.value(), 12.0)

    def test_existing_hover_focus_behavior_is_preserved(self):
        box = self.make_focus_pair(_HoverSpinBox)

        accepted, _ = send_wheel(box)

        self.assertTrue(accepted)
        self.assertTrue(box.hasFocus())
        self.assertEqual(box.value(), 13.0)

    def test_validated_line_edit_steps_and_emits_text_edited(self):
        editor = self.make_widget(QLineEdit())
        editor.setValidator(QDoubleValidator(editor))
        editor.setText("1.25")
        editor.setCursorPosition(3)
        edited = []
        editor.textEdited.connect(edited.append)

        accepted, _ = send_wheel(editor)

        self.assertTrue(accepted)
        self.assertEqual(editor.text(), "1.35")
        self.assertEqual(edited, ["1.35"])
        self.assertEqual(editor.cursorPosition(), 3)

    def test_scientific_notation_uses_the_mantissa_digit(self):
        editor = self.make_widget(QLineEdit())
        editor.setValidator(QDoubleValidator(editor))
        editor.setText("1.25e-05")
        editor.setCursorPosition(3)

        send_wheel(editor)

        self.assertEqual(editor.text(), "1.35e-05")
        self.assertEqual(editor.cursorPosition(), 3)

    def test_plain_text_edit_is_not_treated_as_numeric(self):
        editor = self.make_widget(QLineEdit())
        editor.setText("123")
        editor.setCursorPosition(2)

        send_wheel(editor)

        self.assertEqual(editor.text(), "123")

    def test_crop_edit_steps_and_commits_the_edit(self):
        popup = self.make_widget(CropPopup())
        editor = popup.edits[0]
        editor.setEnabled(True)
        editor.setText("12.5")
        editor.setFocus(Qt.OtherFocusReason)
        editor.setCursorPosition(4)
        edited = []
        popup.edited.connect(lambda: edited.append(True))

        accepted, _ = send_wheel(editor)

        self.assertTrue(accepted)
        self.assertEqual(editor.text(), "12.6")
        self.assertEqual(edited, [True])
        self.assertEqual(editor.cursorPosition(), 4)


if __name__ == "__main__":
    unittest.main()
