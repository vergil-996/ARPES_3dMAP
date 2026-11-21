import unittest
from unittest.mock import Mock

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.ui.opacity_controls import OpacityCurveEditor


def drag_to(widget, position):
    event = QMouseEvent(
        QEvent.MouseMove, QPointF(position), Qt.NoButton, Qt.LeftButton, Qt.NoModifier,
    )
    QApplication.sendEvent(widget, event)


class OpacityCurveEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_editor(self, **values):
        editor = OpacityCurveEditor()
        editor.resize(300, editor.minimumHeight())
        editor.show()
        self.addCleanup(editor.close)
        self.addCleanup(editor.deleteLater)
        if values:
            editor.set_values(editor.values() | values)
        QApplication.processEvents()
        return editor

    @staticmethod
    def point(editor, name):
        position = editor.curve._handle_positions()[name]
        return QPoint(int(round(position.x())), int(round(position.y())))

    def drag(self, editor, name, dx=0, dy=0):
        curve = editor.curve
        start = self.point(editor, name)
        end = QPoint(start.x() + dx, start.y() + dy)
        QTest.mousePress(curve, Qt.LeftButton, pos=start)
        drag_to(curve, end)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=end)

    def test_threshold_drag_preserves_width_and_zero_width_handles_are_separate(self):
        editor = self.make_editor(threshold=20.0, transition=0.0, gamma=1.4, density=12.0)
        positions = editor.curve._handle_positions()
        self.assertEqual(positions["threshold"].x(), positions["transition"].x())
        self.assertLess(positions["threshold"].y(), positions["transition"].y())

        self.drag(editor, "threshold", dx=22)
        self.assertGreater(editor.values()["threshold"], 20.0)
        self.assertEqual(editor.values()["transition"], 0.0)
        self.assertEqual(editor.values()["gamma"], 1.4)
        self.assertEqual(editor.values()["density"], 12.0)

        threshold = editor.values()["threshold"]
        self.drag(editor, "transition", dx=28)
        self.assertEqual(editor.values()["threshold"], threshold)
        self.assertGreater(editor.values()["transition"], 0.0)

        editor.set_values(editor.values() | {"threshold": 35.0, "transition": 25.0})
        self.drag(editor, "threshold", dx=12)
        self.assertEqual(editor.values()["transition"], 25.0)

    def test_gamma_formula_direction_and_numeric_precision(self):
        editor = self.make_editor(threshold=20.0, gamma=1.4)
        expected = 20.0 + 80.0 * 0.5 ** (1.0 / 1.4)
        self.assertAlmostEqual(editor.curve._gamma_intensity(), expected, places=10)
        original = editor.values().copy()

        self.drag(editor, "gamma", dx=-20)
        self.assertLess(editor.values()["gamma"], original["gamma"])
        self.assertEqual(editor.values()["threshold"], original["threshold"])
        self.assertEqual(editor.values()["transition"], original["transition"])
        self.assertEqual(editor.values()["density"], original["density"])
        weaker = editor.values()["gamma"]
        self.drag(editor, "gamma", dx=35)
        self.assertGreater(editor.values()["gamma"], weaker)

        slider, spin, scale = editor.display_parameters["gamma"]
        spin.setValue(2.25)
        self.assertEqual(slider.value(), int(round(2.25 * scale)))
        expected = 20.0 + 80.0 * 0.5 ** (1.0 / 2.25)
        self.assertAlmostEqual(editor.curve._gamma_intensity(), expected, places=10)
        self.assertEqual((slider.minimum(), slider.maximum()), (10, 400))

    def test_zero_density_curve_drag_moves_only_density_and_commits_on_release(self):
        editor = self.make_editor(threshold=25.0, transition=15.0, gamma=1.8, density=0.0)
        committed = Mock()
        editor.valueCommitted.connect(committed)
        curve = editor.curve
        start = QPoint(int(round(curve._x(35.0))), int(round(curve._y(0.0))))
        end = QPoint(start.x(), start.y() - 42)
        before = {name: position.x() for name, position in curve._handle_positions().items()}

        QTest.mousePress(curve, Qt.LeftButton, pos=start)
        drag_to(curve, end)
        self.assertGreater(editor.values()["density"], 0.0)
        self.assertEqual(committed.call_count, 0)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=end)
        self.assertEqual(committed.call_count, 1)

        values = editor.values()
        self.assertEqual(values["threshold"], 25.0)
        self.assertEqual(values["transition"], 15.0)
        self.assertEqual(values["gamma"], 1.8)
        after = {name: position.x() for name, position in curve._handle_positions().items()}
        self.assertEqual(after, before)

    def test_threshold_100_keeps_gamma_box_editable_and_density_draggable(self):
        editor = self.make_editor(threshold=100.0, density=0.0)
        _slider, spin, _scale = editor.display_parameters["gamma"]
        self.assertTrue(spin.isEnabled())
        spin.setValue(3.25)
        self.assertEqual(editor.values()["gamma"], 3.25)
        self.assertEqual(editor.curve._gamma_intensity(), 100.0)

        self.drag(editor, "density", dy=-35)
        self.assertGreater(editor.values()["density"], 0.0)
        self.assertEqual(editor.values()["threshold"], 100.0)
        self.assertEqual(editor.values()["gamma"], 3.25)

    def test_transition_endpoint_can_expand_beyond_100(self):
        editor = self.make_editor(threshold=60.0, transition=0.0)
        _slider, spin, _scale = editor.display_parameters["transition"]
        spin.setValue(50.0)
        self.assertEqual(editor.curve._axis_maximum(), 110.0)
        self.assertEqual(editor.values()["threshold"] + editor.values()["transition"], 110.0)

        plot = editor.curve._plot_rect()
        start = self.point(editor, "transition")
        self.assertAlmostEqual(start.x(), plot.right(), delta=1.0)
        end = QPoint(plot.right() + 15, start.y())
        QTest.mousePress(editor.curve, Qt.LeftButton, pos=start)
        drag_to(editor.curve, end)
        QTest.mouseRelease(editor.curve, Qt.LeftButton, pos=end)
        self.assertGreater(editor.values()["transition"], 50.0)
        self.assertLessEqual(editor.values()["transition"], 100.0)
        self.assertGreater(editor.values()["threshold"] + editor.values()["transition"], 100.0)

    def test_deleted_editor_does_not_receive_later_theme_changes(self):
        from bandscope.ui import theme

        original = theme.current_theme()
        self.addCleanup(theme.set_theme, original)
        editor = OpacityCurveEditor()
        editor.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        theme.set_theme("dark" if original == "light" else "light")
        theme.set_theme(original)

    def test_curve_and_compact_inputs_render_in_narrow_panel(self):
        editor = self.make_editor()
        editor.resize(220, editor.minimumHeight())
        QApplication.processEvents()
        self.assertFalse(editor.grab().isNull())
        for _name, (_slider, spin, _scale) in editor.display_parameters.items():
            self.assertTrue(spin.isVisible())
            self.assertGreater(spin.width(), 40)


if __name__ == "__main__":
    unittest.main()
