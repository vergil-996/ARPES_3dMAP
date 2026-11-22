import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QColor, QContextMenuEvent, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.ui import theme
from bandscope.ui.opacity_controls import OpacityCurveEditor


def drag_to(widget, position):
    event = QMouseEvent(
        QEvent.MouseMove, QPointF(position), Qt.NoButton, Qt.LeftButton, Qt.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def point_at(intensity, alpha, curve=None):
    curve = curve
    return QPoint(int(round(curve._x(intensity))), int(round(curve._y(alpha))))


class OpacityCurveEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_editor(self, points=None):
        editor = OpacityCurveEditor()
        editor.resize(320, editor.minimumHeight())
        editor.show()
        self.addCleanup(editor.close)
        self.addCleanup(editor.deleteLater)
        if points is not None:
            editor.set_points(points)
        QApplication.processEvents()
        return editor

    def start_rail(self, editor, intensity):
        curve = editor.curve
        position = QPoint(int(round(curve._x(intensity))), curve._rail_rect().center().y())
        QTest.mousePress(curve, Qt.LeftButton, pos=position)
        return curve, position

    def test_defaults_are_three_linear_points(self):
        editor = self.make_editor()
        self.assertEqual(editor.points(), [[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]])
        self.assertAlmostEqual(editor.curve_alpha(0.25), 0.25, places=9)
        self.assertAlmostEqual(editor.curve_alpha(0.75), 0.75, places=9)

    def test_dragging_an_internal_point_adjusts_intensity_and_alpha(self):
        editor = self.make_editor()
        curve = editor.curve
        changed = Mock()
        committed = Mock()
        editor.valueChanged.connect(changed)
        editor.valueCommitted.connect(committed)
        start = point_at(0.5, 0.5, curve)
        end = QPoint(int(round(curve._x(0.7))), int(round(curve._y(0.9))))
        QTest.mousePress(curve, Qt.LeftButton, pos=start)
        drag_to(curve, end)
        self.assertGreater(editor.points()[1][0], 0.65)
        self.assertGreater(editor.points()[1][1], 0.85)
        self.assertGreater(changed.call_count, 0)
        self.assertEqual(committed.call_count, 0)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=end)
        self.assertEqual(committed.call_count, 1)

    def test_curve_points_have_no_outer_or_focus_outline(self):
        original = theme.current_theme()
        self.addCleanup(theme.set_theme, original)
        theme.set_theme("light")
        editor = self.make_editor([[0.0, 0.0], [0.3, 0.4], [1.0, 1.0]])
        curve = editor.curve
        QApplication.processEvents()
        center = curve.point_position(1)
        image = curve.grab().toImage()
        ratio = image.devicePixelRatio() or 1.0
        x, y = int(round(center.x() * ratio)), int(round(center.y() * ratio))
        background = QColor(theme.BG_2)
        self.assertEqual(image.pixelColor(x, y - int(round(8 * ratio))), background)
        point = QPoint(int(round(center.x())), int(round(center.y())))
        QTest.mousePress(curve, Qt.LeftButton, pos=point)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=point)
        curve.setFocus(Qt.MouseFocusReason)
        QApplication.processEvents()
        image = curve.grab().toImage()
        self.assertEqual(image.pixelColor(x, y - int(round(11 * ratio))), background)
        self.assertEqual(image.pixelColor(x, y - int(round(8 * ratio))), background)

    def test_rail_has_no_center_line(self):
        editor = self.make_editor()
        curve = editor.curve
        image = curve.grab().toImage()
        ratio = image.devicePixelRatio() or 1.0
        rail = curve._rail_rect()
        x = int(round(curve._x(0.2) * ratio))
        for logical_y in (rail.center().y(), rail.center().y() + 1):
            y = int(round(logical_y * ratio))
            self.assertEqual(image.pixelColor(x, y), QColor(theme.BG_4))

    def test_rail_handle_stays_put_while_dragging_a_curve_point(self):
        editor = self.make_editor([[0.0, 0.0], [0.2, 0.3], [0.6, 0.6], [1.0, 1.0]])
        curve = editor.curve
        before = curve._rail_point()
        start = point_at(0.2, 0.3, curve)
        end = point_at(0.5, 0.8, curve)
        QTest.mousePress(curve, Qt.LeftButton, pos=start)
        drag_to(curve, end)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=end)
        self.assertEqual(curve._rail_point(), before)
        self.assertAlmostEqual(editor.points()[1][0], 0.5, delta=0.03)
        self.assertAlmostEqual(editor.points()[1][1], 0.8, delta=0.03)

    def test_upward_drag_detaches_the_dot_and_backfills_only_after_hitting(self):
        original = theme.current_theme()
        self.addCleanup(theme.set_theme, original)
        theme.set_theme("light")
        editor = self.make_editor()
        curve = editor.curve
        curve, _rail = self.start_rail(editor, 0.4)
        below = point_at(0.4, 0.15, curve)
        drag_to(curve, below)
        self.assertEqual(curve._mode, "insert")
        self.assertIsNone(curve.backfill_progress())
        dot = curve.moving_dot_position()
        self.assertIsNotNone(dot)
        self.assertAlmostEqual(dot.x(), curve._x(0.4), delta=1.0)
        self.assertAlmostEqual(dot.y(), curve._y(0.15), delta=2.0)
        # 外轮廓仍留在底轨原位，且内部已经空了。
        image = curve.grab().toImage()
        ratio = image.devicePixelRatio() or 1.0
        rail = curve._rail_rect()
        sample = (int(round((curve._x(0.4) + 3) * ratio)),
                  int(round((rail.center().y() - 3) * ratio)))
        self.assertEqual(image.pixelColor(*sample), QColor(theme.BG_4))
        # 横向移动不会改变锁定的候选强度，黑点只沿竖直虚线上下走。
        locked_x = dot.x()
        drag_to(curve, QPoint(int(round(curve._x(0.75))), below.y()))
        dot = curve.moving_dot_position()
        self.assertAlmostEqual(dot.x(), locked_x, delta=0.5)
        self.assertAlmostEqual(curve._insert_intensity, 0.4, delta=0.01)
        # 碰到曲线后才生成点，并开始在底轨原位回填。
        above = point_at(0.4, 0.85, curve)
        drag_to(curve, above)
        self.assertEqual(len(editor.points()), 4)
        self.assertEqual(curve._mode, "point")
        self.assertIsNone(curve.moving_dot_position())
        self.assertIsNotNone(curve.backfill_progress())
        QTest.mouseRelease(curve, Qt.LeftButton, pos=above)

    def test_dot_travels_through_the_rail_axis_gap_without_jumping(self):
        editor = self.make_editor([[0.0, 0.0], [0.5, 0.0], [1.0, 1.0]])
        curve = editor.curve
        curve, _rail = self.start_rail(editor, 0.25)
        plot = curve._plot_rect()
        gap_y = plot.bottom() + 12
        gap = QPoint(int(round(curve._x(0.25))), gap_y)
        drag_to(curve, gap)
        dot = curve.moving_dot_position()
        self.assertIsNotNone(dot)
        self.assertAlmostEqual(dot.y(), gap_y, delta=2.0)
        self.assertEqual(len(editor.points()), 3)
        self.assertIsNone(curve.backfill_progress())
        axis = QPoint(int(round(curve._x(0.25))), plot.bottom())
        drag_to(curve, axis)
        self.assertEqual(len(editor.points()), 4)
        self.assertAlmostEqual(editor.points()[1][1], 0.0, delta=0.02)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=axis)

    def test_dragging_back_into_the_rail_reattaches_and_permits_horizontal_drag(self):
        editor = self.make_editor()
        curve = editor.curve
        curve, rail = self.start_rail(editor, 0.3)
        drag_to(curve, point_at(0.3, 0.1, curve))
        self.assertEqual(curve._mode, "insert")
        drag_to(curve, QPoint(int(round(curve._x(0.3))), rail.y()))
        self.assertEqual(curve._mode, "rail")
        self.assertIsNone(curve.moving_dot_position())
        self.assertIsNone(curve.backfill_progress())
        target = QPoint(int(round(curve._x(0.7))), rail.y())
        drag_to(curve, target)
        self.assertAlmostEqual(curve._rail_intensity, 0.7, delta=0.02)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=target)
        self.assertEqual(len(editor.points()), 3)
        curve, rail = self.start_rail(editor, 0.2)
        drag_to(curve, QPoint(int(round(curve._x(0.6))), rail.y()))
        QTest.mouseRelease(curve, Qt.LeftButton, pos=QPoint(int(round(curve._x(0.6))), rail.y()))
        self.assertAlmostEqual(curve._rail_intensity, 0.6, delta=0.02)

    def test_release_before_hitting_restores_the_filled_rail_handle(self):
        original = theme.current_theme()
        self.addCleanup(theme.set_theme, original)
        theme.set_theme("light")
        editor = self.make_editor()
        curve = editor.curve
        curve, rail = self.start_rail(editor, 0.4)
        drag_to(curve, QPoint(int(round(curve._x(0.4))), rail.y() - 14))
        self.assertEqual(curve._mode, "insert")
        QTest.mouseRelease(curve, Qt.LeftButton, pos=QPoint(int(round(curve._x(0.4))), rail.y() - 14))
        self.assertIsNone(curve._mode)
        self.assertIsNone(curve.moving_dot_position())
        self.assertIsNone(curve.backfill_progress())
        self.assertEqual(len(editor.points()), 3)
        image = curve.grab().toImage()
        ratio = image.devicePixelRatio() or 1.0
        rail_rect = curve._rail_rect()
        center = curve._rail_point()
        sample = (int(round(center.x() * ratio)), int(round(rail_rect.center().y() * ratio)))
        self.assertEqual(image.pixelColor(*sample), QColor("#111111"))

    def test_internal_point_stays_between_neighbours_and_endpoints_keep_their_x(self):
        editor = self.make_editor([[0.0, 0.0], [0.3, 0.2], [0.6, 0.6], [1.0, 1.0]])
        curve = editor.curve
        start = point_at(0.3, 0.2, curve)
        far_right = QPoint(int(round(curve._x(0.95))), int(round(curve._y(0.9))))
        QTest.mousePress(curve, Qt.LeftButton, pos=start)
        drag_to(curve, far_right)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=far_right)
        self.assertLess(editor.points()[1][0], editor.points()[2][0])
        self.assertGreater(editor.points()[1][0], 0.3)

        curve = editor.curve
        start = point_at(0.0, 0.0, curve)
        end = QPoint(int(round(curve._x(0.4))), int(round(curve._y(0.5))))
        QTest.mousePress(curve, Qt.LeftButton, pos=start)
        drag_to(curve, end)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=end)
        self.assertEqual(editor.points()[0][0], 0.0)
        self.assertAlmostEqual(editor.points()[0][1], 0.5, delta=0.02)

        start = point_at(1.0, 1.0, curve)
        end = QPoint(int(round(curve._x(0.2))), int(round(curve._y(0.1))))
        QTest.mousePress(curve, Qt.LeftButton, pos=start)
        drag_to(curve, end)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=end)
        self.assertEqual(editor.points()[-1][0], 1.0)
        self.assertAlmostEqual(editor.points()[-1][1], 0.1, delta=0.02)

    def test_internal_point_dragged_below_zero_keeps_alpha_zero_and_stays(self):
        editor = self.make_editor()
        curve = editor.curve
        start = point_at(0.5, 0.5, curve)
        below = QPoint(int(round(curve._x(0.65))), curve._plot_rect().bottom() + 40)
        QTest.mousePress(curve, Qt.LeftButton, pos=start)
        drag_to(curve, below)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=below)
        points = editor.points()
        self.assertEqual(len(points), 3)
        self.assertEqual(points[1][1], 0.0)
        self.assertAlmostEqual(points[1][0], 0.65, delta=0.02)
        self.assertEqual(editor.selected_index(), 1)

    def test_rail_horizontal_drag_only_repaints(self):
        editor = self.make_editor()
        curve = editor.curve
        changed = Mock()
        committed = Mock()
        editor.valueChanged.connect(changed)
        editor.valueCommitted.connect(committed)
        curve, position = self.start_rail(editor, 0.30)
        end = QPoint(int(round(curve._x(0.62))), position.y())
        drag_to(curve, end)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=end)
        self.assertAlmostEqual(curve._rail_intensity, 0.62, delta=0.02)
        self.assertEqual(editor.points(), [[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]])
        self.assertEqual(changed.call_count, 0)
        self.assertEqual(committed.call_count, 0)

    def test_drag_up_from_rail_inserts_on_the_curve_and_keeps_dragging(self):
        editor = self.make_editor()
        curve = editor.curve
        changed = Mock()
        committed = Mock()
        editor.valueChanged.connect(changed)
        editor.valueCommitted.connect(committed)
        curve, rail = self.start_rail(editor, 0.30)
        on_curve = point_at(0.30, 0.30, curve)
        drag_to(curve, on_curve)
        self.assertEqual(len(editor.points()), 4)
        inserted = editor.selected_index()
        self.assertEqual(inserted, 1)
        self.assertAlmostEqual(editor.points()[1][0], 0.30, delta=0.02)
        self.assertAlmostEqual(editor.points()[1][1], 0.30, delta=0.02)
        self.assertEqual(committed.call_count, 0)

        higher = point_at(0.30, 0.70, curve)
        drag_to(curve, higher)
        self.assertGreater(editor.points()[1][1], 0.65)
        self.assertEqual(len(editor.points()), 4)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=higher)
        self.assertEqual(committed.call_count, 1)
        self.assertGreater(changed.call_count, 0)

    def test_releasing_before_hitting_the_curve_returns_to_the_rail(self):
        editor = self.make_editor()
        curve = editor.curve
        curve, rail = self.start_rail(editor, 0.30)
        slightly_up = QPoint(rail.x(), rail.y() - 14)
        drag_to(curve, slightly_up)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=slightly_up)
        self.assertEqual(len(editor.points()), 3)
        self.assertIsNone(editor.selected_index())

    def test_two_stage_rail_then_up_insert(self):
        editor = self.make_editor()
        curve = editor.curve
        curve, position = self.start_rail(editor, 0.40)
        located = QPoint(int(round(curve._x(0.55))), position.y())
        drag_to(curve, located)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=located)
        self.assertEqual(len(editor.points()), 3)

        press = QPoint(int(round(curve._x(0.55))), curve._rail_rect().center().y())
        QTest.mousePress(curve, Qt.LeftButton, pos=press)
        target = point_at(0.55, 0.62, curve)
        drag_to(curve, target)
        self.assertEqual(len(editor.points()), 4)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=target)
        self.assertAlmostEqual(editor.points()[-1][0], 1.0)

    def test_hitting_an_existing_point_selects_and_drags_it(self):
        editor = self.make_editor()
        curve = editor.curve
        curve, _rail = self.start_rail(editor, 0.50)
        target = point_at(0.50, 0.50, curve)
        drag_to(curve, target)
        self.assertEqual(len(editor.points()), 3)
        self.assertEqual(editor.selected_index(), 1)
        higher = point_at(0.50, 0.8, curve)
        drag_to(curve, higher)
        self.assertGreater(editor.points()[1][1], 0.75)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=higher)

    def test_insert_works_on_a_zero_alpha_curve_segment(self):
        editor = self.make_editor([[0.0, 0.0], [0.5, 0.0], [1.0, 1.0]])
        curve = editor.curve
        curve, _rail = self.start_rail(editor, 0.2)
        target = point_at(0.2, 0.4, curve)
        drag_to(curve, target)
        self.assertEqual(len(editor.points()), 4)
        self.assertAlmostEqual(editor.points()[1][0], 0.2, delta=0.02)
        self.assertAlmostEqual(editor.points()[1][1], 0.0, delta=0.02)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=target)

    def test_delete_key_removes_only_selected_internal_point_and_commits(self):
        editor = self.make_editor([[0.0, 0.0], [0.3, 0.4], [0.6, 0.8], [1.0, 1.0]])
        curve = editor.curve
        committed = Mock()
        editor.valueCommitted.connect(committed)
        curve.setFocus()
        first = point_at(0.0, 0.0, curve)
        QTest.mousePress(curve, Qt.LeftButton, pos=first)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=first)
        self.assertEqual(editor.selected_index(), 0)
        QTest.keyClick(curve, Qt.Key_Delete)
        self.assertEqual(len(editor.points()), 4)

        middle = point_at(0.3, 0.4, curve)
        QTest.mousePress(curve, Qt.LeftButton, pos=middle)
        QTest.mouseRelease(curve, Qt.LeftButton, pos=middle)
        self.assertEqual(editor.selected_index(), 1)
        self.assertEqual(committed.call_count, 0)
        QTest.keyClick(curve, Qt.Key_Delete)
        self.assertEqual(len(editor.points()), 3)
        self.assertEqual(editor.points()[0], [0.0, 0.0])
        self.assertEqual(editor.points()[-1], [1.0, 1.0])
        self.assertEqual(committed.call_count, 1)
        QTest.keyClick(curve, Qt.Key_Delete)
        self.assertEqual(len(editor.points()), 3)

    def test_context_menu_delete_action_removes_the_internal_point(self):
        editor = self.make_editor([[0.0, 0.0], [0.3, 0.4], [0.6, 0.8], [1.0, 1.0]])
        curve = editor.curve
        position = point_at(0.6, 0.8, curve)
        event = QContextMenuEvent(
            QContextMenuEvent.Mouse, position, curve.mapToGlobal(position),
        )
        with patch("bandscope.ui.opacity_controls.QMenu") as menu_class:
            menu = menu_class.return_value
            action = menu.addAction.return_value
            curve.contextMenuEvent(event)
            menu.addAction.assert_called_once_with("删除控制点")
            action.triggered.connect.assert_called_once()
            action.triggered.connect.call_args[0][0]()
        self.assertEqual(len(editor.points()), 3)
        self.assertEqual(editor.points()[1], [0.3, 0.4])

    def test_backfill_animation_runs_without_emitting_render_signals(self):
        editor = self.make_editor()
        curve = editor.curve
        changed = Mock()
        committed = Mock()
        editor.valueChanged.connect(changed)
        editor.valueCommitted.connect(committed)
        curve._start_backfill(0.4)
        self.assertIsNotNone(curve.backfill_progress())
        QApplication.processEvents()
        self.assertEqual(changed.call_count, 0)
        self.assertEqual(committed.call_count, 0)
        curve._backfill_timer.stop()
        curve._backfill_start = None

    def test_deleted_editor_does_not_receive_later_theme_changes(self):
        from bandscope.ui import theme

        original = theme.current_theme()
        self.addCleanup(theme.set_theme, original)
        editor = OpacityCurveEditor()
        editor.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        theme.set_theme("dark" if original == "light" else "light")
        theme.set_theme(original)

    def test_curve_renders_in_narrow_panel_and_tracks_theme(self):
        from bandscope.ui import theme

        original = theme.current_theme()
        self.addCleanup(theme.set_theme, original)
        editor = self.make_editor([[0.0, 0.0], [0.4, 0.6], [1.0, 1.0]])
        editor.resize(200, editor.minimumHeight())
        QApplication.processEvents()
        for name in ("light", "dark"):
            theme.set_theme(name)
            QApplication.processEvents()
            image = editor.grab()
            self.assertFalse(image.isNull())
            self.assertGreater(image.width(), 100)


if __name__ == "__main__":
    unittest.main()
