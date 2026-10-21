"""Mouse interaction follows the displayed image, including letterboxing and DPR."""
import unittest

from PyQt5.QtCore import QPoint, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPixmap
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication

from bandscope.exporting.orientation_preview import OrientationPreviewLabel


class OrientationPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.label = OrientationPreviewLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.resize(400, 300)
        self.moved = QSignalSpy(self.label.orientationMoved)
        self.finished = QSignalSpy(self.label.orientationDragFinished)
        self.colorbar_moved = QSignalSpy(self.label.colorbarMoved)
        self.colorbar_finished = QSignalSpy(self.label.colorbarDragFinished)
        self._install()
        self.label.show()
        self.app.processEvents()

    def tearDown(self):
        self.label.close()
        self.label.deleteLater()

    def _install(self, *, width=200, height=100, dpr=1.):
        background = QPixmap(width, height)
        background.fill(Qt.white)
        background.setDevicePixelRatio(dpr)
        self.label.setPixmap(background)
        layer = QPixmap(width, height)
        layer.fill(Qt.transparent)
        painter = QPainter(layer)
        painter.fillRect(QRectF(width * .10, height * .60, width * .20, height * .20),
                         QColor("#ff0000"))
        painter.end()
        layer.setDevicePixelRatio(dpr)
        self.label.set_orientation_preview(layer, (.10, .20, .20, .20))

    def _drag(self, start, finish):
        QTest.mousePress(self.label, Qt.LeftButton, pos=QPoint(*start))
        QTest.mouseMove(self.label, QPoint(*finish))
        QTest.mouseRelease(self.label, Qt.LeftButton, pos=QPoint(*finish))

    def _install_colorbar(self, *, width=200, height=100, dpr=1., overlapping=False):
        overlay = QPixmap(width, height)
        overlay.fill(Qt.transparent)
        painter = QPainter(overlay)
        if overlapping:
            rect = bounds = (.10, .20, .20, .20)
        else:
            rect, bounds = (.60, .30, .10, .40), (.55, .20, .30, .60)
        x, y, w, h = bounds
        painter.fillRect(QRectF(width * x, height * (1. - y - h), width * w, height * h),
                         QColor("blue"))
        if not overlapping:
            painter.fillRect(QRectF(width * .75, height * .30, width * .10, height * .10),
                             QColor("yellow"))
        painter.end()
        overlay.setDevicePixelRatio(dpr)
        self.label.set_colorbar_preview(overlay, rect, bounds)

    def _assert_colorbar_center(self, cx, cy):
        x, y, width, height = self.label.colorbar_rect()
        self.assertAlmostEqual((x + width / 2.) * 100., cx)
        self.assertAlmostEqual((y + height / 2.) * 100., cy)

    def _pixel(self, x, y):
        capture = self.label.grab()
        ratio = capture.devicePixelRatioF()
        return capture.toImage().pixelColor(int(x * ratio), int(y * ratio))

    def _assert_center(self, cx, cy):
        x, y, width, height = self.label.orientation_rect()
        self.assertAlmostEqual((x + width / 2.) * 100., cx)
        self.assertAlmostEqual((y + height / 2.) * 100., cy)

    def test_drag_uses_letterboxed_image_and_preserves_grab_offset(self):
        # A 200x100 image is centered at (100,100) inside a 400x300 label.
        # Press 10px left of the marker's center, then move 40px right/20px up.
        self._drag((130, 170), (170, 150))
        self._assert_center(40., 50.)
        self.assertEqual(len(self.finished), 1)
        self.assertAlmostEqual(self.moved[-1][0], 40.)
        self.assertAlmostEqual(self.moved[-1][1], 50.)

    def test_drag_renders_marker_at_new_location_without_changing_background(self):
        self._drag((130, 170), (170, 150))
        capture = self.label.grab()
        image = capture.toImage()
        ratio = capture.devicePixelRatioF()
        self.assertEqual(image.pixelColor(int(180 * ratio), int(150 * ratio)), QColor("#ff0000"))
        self.assertEqual(image.pixelColor(int(130 * ratio), int(170 * ratio)), QColor("white"))

    def test_drag_clamps_whole_marker_to_figure_edges(self):
        self._drag((130, 170), (-300, -300))
        self._assert_center(10., 90.)
        self.label.set_orientation_center(200., -200.)
        self._assert_center(90., 10.)

    def test_other_clicks_and_buttons_do_not_move_marker(self):
        self._drag((20, 20), (200, 150))
        QTest.mousePress(self.label, Qt.RightButton, pos=QPoint(130, 170))
        QTest.mouseRelease(self.label, Qt.RightButton, pos=QPoint(170, 150))
        self._assert_center(20., 30.)
        self.assertEqual(len(self.moved), 0)
        self.assertEqual(len(self.finished), 0)

    def test_disabled_and_cleared_layers_cannot_be_dragged(self):
        self.label.set_orientation_interactive(False)
        self._drag((130, 170), (170, 150))
        self._assert_center(20., 30.)
        capture = self.label.grab()
        ratio = capture.devicePixelRatioF()
        self.assertEqual(capture.toImage().pixelColor(int(140 * ratio), int(170 * ratio)),
                         QColor("#ff0000"))
        self.label.set_orientation_interactive(True)
        self.label.setEnabled(False)
        self._drag((130, 170), (170, 150))
        self._assert_center(20., 30.)
        self.label.setEnabled(True)
        self.label.clear_orientation()
        self._drag((130, 170), (170, 150))
        self.assertIsNone(self.label.orientation_rect())
        self.assertEqual(len(self.moved), 0)

    def test_hidden_preview_cannot_start_a_drag(self):
        self.label.hide()
        self._drag((130, 170), (170, 150))
        self._assert_center(20., 30.)
        self.assertEqual(len(self.moved), 0)
        self.assertEqual(len(self.finished), 0)

    def test_preview_scaling_changes_pixel_delta_not_figure_percent(self):
        self._install(width=400, height=200)
        # This displayed image fills the width and starts at y=50.
        self._drag((60, 190), (140, 150))
        self._assert_center(40., 50.)

    def test_high_dpi_pixmap_uses_device_independent_mouse_coordinates(self):
        self._install(width=400, height=200, dpr=2.)
        # A DPR=2 pixmap occupies 200x100 logical pixels, as in the first test.
        self._drag((130, 170), (170, 150))
        self._assert_center(40., 50.)
        capture = self.label.grab()
        ratio = capture.devicePixelRatioF()
        self.assertEqual(capture.toImage().pixelColor(int(180 * ratio), int(150 * ratio)),
                         QColor("#ff0000"))

    def test_hover_cursor_and_disabled_interaction(self):
        QTest.mouseMove(self.label, QPoint(130, 170))
        self.assertEqual(self.label.cursor().shape(), Qt.OpenHandCursor)
        QTest.mousePress(self.label, Qt.LeftButton, pos=QPoint(130, 170))
        self.assertEqual(self.label.cursor().shape(), Qt.ClosedHandCursor)
        QTest.mouseRelease(self.label, Qt.LeftButton, pos=QPoint(130, 170))
        self.label.set_orientation_interactive(False)
        self.assertEqual(self.label.cursor().shape(), Qt.ArrowCursor)

    def test_colorbar_drag_moves_title_and_ticks_without_a_background_residue(self):
        self._install_colorbar()
        self._drag((230, 150), (190, 140))
        self._assert_colorbar_center(45., 60.)
        self._assert_center(20., 30.)
        self.assertEqual(len(self.colorbar_finished), 1)
        self.assertEqual(len(self.finished), 0)
        self.assertAlmostEqual(self.colorbar_moved[-1][0], 45.)
        self.assertAlmostEqual(self.colorbar_moved[-1][1], 60.)
        self.assertEqual(self._pixel(220, 125), QColor("yellow"))
        self.assertEqual(self._pixel(260, 135), QColor("white"))

    def test_colorbar_title_can_be_grabbed_and_preserves_its_offset(self):
        self._install_colorbar()
        self._drag((260, 135), (280, 145))
        self._assert_colorbar_center(75., 40.)
        self.assertEqual(len(self.colorbar_finished), 1)

    def test_colorbar_clamps_complete_annotation_bounds_not_only_the_narrow_bar(self):
        self._install_colorbar()
        self.label.set_colorbar_center(0., 100.)
        self._assert_colorbar_center(10., 70.)
        self.assertEqual(self._pixel(105, 105), QColor("blue"))
        self.label.set_colorbar_center(100., 0.)
        self._assert_colorbar_center(80., 30.)
        self.assertEqual(self._pixel(295, 195), QColor("blue"))

    def test_overlapping_layers_draw_and_hit_orientation_first(self):
        self._install_colorbar(overlapping=True)
        self.assertEqual(self._pixel(140, 170), QColor("#ff0000"))
        self._drag((130, 170), (170, 150))
        self._assert_center(40., 50.)
        self._assert_colorbar_center(20., 30.)
        self.assertEqual(len(self.colorbar_moved), 0)
        self.assertEqual(self._pixel(130, 170), QColor("blue"))
        self.assertEqual(self._pixel(180, 150), QColor("#ff0000"))

    def test_colorbar_disable_and_clear_do_not_disable_orientation(self):
        self._install_colorbar()
        self.label.set_colorbar_interactive(False)
        self._drag((230, 150), (190, 140))
        self._assert_colorbar_center(65., 50.)
        self._drag((130, 170), (170, 150))
        self._assert_center(40., 50.)
        self.label.set_colorbar_interactive(True)
        self.label.clear_colorbar()
        self._drag((230, 150), (190, 140))
        self.assertIsNone(self.label.colorbar_rect())
        self.assertEqual(len(self.colorbar_moved), 0)
        self.assertEqual(len(self.colorbar_finished), 0)

    def test_clearing_orientation_keeps_colorbar_draggable(self):
        self._install_colorbar()
        self.label.clear_orientation()
        self._drag((230, 150), (190, 140))
        self._assert_colorbar_center(45., 60.)
        self.assertIsNone(self.label.orientation_rect())

    def test_colorbar_mapping_uses_current_preview_scale_and_dpr(self):
        for width, height, dpr, start, end in (
            (400, 200, 1., (260, 150), (180, 130)),
            (400, 200, 2., (230, 150), (190, 140)),
        ):
            with self.subTest(dpr=dpr):
                self._install(width=width, height=height, dpr=dpr)
                self._install_colorbar(width=width, height=height, dpr=dpr)
                self._drag(start, end)
                self._assert_colorbar_center(45., 60.)

    def test_disabled_orientation_allows_colorbar_under_it_to_be_dragged(self):
        self._install_colorbar(overlapping=True)
        self.label.set_orientation_interactive(False)
        self._drag((130, 170), (170, 150))
        self._assert_colorbar_center(40., 50.)
        self._assert_center(20., 30.)
        self.assertEqual(len(self.moved), 0)

    def test_hidden_or_disabled_widget_cannot_start_colorbar_drag(self):
        self._install_colorbar()
        self.label.hide()
        self._drag((230, 150), (190, 140))
        self._assert_colorbar_center(65., 50.)
        self.label.show()
        self.label.setEnabled(False)
        self._drag((230, 150), (190, 140))
        self._assert_colorbar_center(65., 50.)
        self.assertEqual(len(self.colorbar_moved), 0)


if __name__ == "__main__":
    unittest.main()
