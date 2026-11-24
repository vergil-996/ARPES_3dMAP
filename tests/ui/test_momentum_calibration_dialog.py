import os
import tempfile
import unittest

import numpy as np
from matplotlib.colors import to_hex
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.ui import theme
from bandscope.ui.momentum_calibration_dialog import MomentumCalibrationDialog


class MomentumCalibrationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = np.arange(4 * 3 * 2 * 2, dtype=np.float32).reshape(4, 3, 2, 2)
        self.x = np.asarray([-2.0, -0.5, 0.25, 2.0])
        self.y = np.asarray([3.0, 1.0, -2.0])
        path = os.path.join(self.temp.name, "momentum.npz")
        np.savez(
            path,
            sample=self.raw,
            kx=self.x,
            ky=self.y,
            energy=np.asarray([-0.2, 0.4]),
            delay=np.asarray([-1.0, 2.0]),
            axis_order=np.asarray(["X", "Y", "E", "T"]),
            scan_type=np.asarray(["time"]),
            kx_unit=np.asarray(["Å⁻¹"]),
            ky_unit=np.asarray(["Å⁻¹"]),
            E_unit=np.asarray(["eV"]),
            time_unit=np.asarray(["fs"]),
        )
        self.core = AnalyzerCore()
        success, info = self.core.load_npz(path)
        self.assertTrue(success, info)

    def dialog(self, **kwargs):
        dialog = MomentumCalibrationDialog(self.core, **kwargs)
        dialog.show()
        self.app.processEvents()
        dialog.canvas.draw()
        self.app.processEvents()
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        return dialog

    def click_point(self, dialog, point):
        physical = (
            self.core.logical_to_physical("X", point[0], clamp=False),
            self.core.logical_to_physical("Y", point[1], clamp=False),
        )
        display_x, display_y = dialog.axes.transData.transform(physical)
        ratio = dialog.canvas.devicePixelRatioF()
        position = QPoint(
            int(round(display_x / ratio)),
            int(round(dialog.canvas.height() - display_y / ratio)),
        )
        QTest.mouseClick(dialog.canvas, Qt.LeftButton, pos=position)
        self.app.processEvents()

    def select_points(self, dialog, first=(0, 0), second=(3, 2)):
        self.click_point(dialog, first)
        self.click_point(dialog, second)
        np.testing.assert_allclose(dialog.point_a, first, atol=0.005)
        np.testing.assert_allclose(dialog.point_b, second, atol=0.005)

    def test_slice_uses_canonical_xy_transpose_and_physical_slice_controls(self):
        dialog = self.dialog(energy_index=1, scan_index=1)
        self.assertEqual(dialog.energy_index, 1)
        self.assertEqual(dialog.scan_index, 1)
        self.assertEqual(dialog.unit_edit.text(), "Å⁻¹")
        self.assertIn("0.4 eV", dialog.energy_value_label.text())
        self.assertIn("2 fs", dialog.scan_value_label.text())
        np.testing.assert_array_equal(
            dialog.image.get_array(), self.raw[:, :, 1, 1].T
        )

        self.click_point(dialog, (1, 2))
        dialog.energy_slider.setValue(0)
        self.app.processEvents()
        np.testing.assert_array_equal(
            dialog.image.get_array(), self.raw[:, :, 0, 1].T
        )
        np.testing.assert_allclose(dialog.point_a, (1.0, 2.0), atol=0.005)
        dialog.scan_slider.setValue(0)
        self.app.processEvents()
        np.testing.assert_array_equal(
            dialog.image.get_array(), self.raw[:, :, 0, 0].T
        )
        np.testing.assert_allclose(dialog.point_a, (1.0, 2.0), atol=0.005)
        self.assertIn("-0.2 eV", dialog.energy_value_label.text())
        self.assertIn("-1 fs", dialog.scan_value_label.text())

    def test_real_mouse_selection_replaces_p2_and_undo_clear_redraw(self):
        dialog = self.dialog()
        self.click_point(dialog, (1, 2))
        np.testing.assert_allclose(dialog.point_a, (1.0, 2.0), atol=0.005)
        self.assertIsNone(dialog.point_b)
        self.click_point(dialog, (3, 0))
        np.testing.assert_allclose(dialog.point_b, (3.0, 0.0), atol=0.005)
        self.assertEqual(len(dialog._selection_line.get_xdata()), 2)
        self.assertEqual(len(dialog._markers.get_offsets()), 2)

        self.click_point(dialog, (2, 1))
        np.testing.assert_allclose(dialog.point_a, (1.0, 2.0), atol=0.005)
        np.testing.assert_allclose(dialog.point_b, (2.0, 1.0), atol=0.005)
        self.assertIn("P2", dialog.point_b_label.text())
        self.assertNotIn("尚未选择", dialog.point_b_label.text())

        dialog.undo_button.click()
        np.testing.assert_allclose(dialog.point_a, (1.0, 2.0), atol=0.005)
        self.assertIsNone(dialog.point_b)
        self.assertEqual(len(dialog._selection_line.get_xdata()), 0)
        dialog.undo_button.click()
        self.assertIsNone(dialog.point_a)
        self.assertFalse(dialog.undo_button.isEnabled())
        self.assertFalse(dialog.clear_button.isEnabled())

        self.select_points(dialog)
        dialog.clear_button.click()
        self.assertIsNone(dialog.point_a)
        self.assertIsNone(dialog.point_b)
        self.assertEqual(len(dialog._markers.get_offsets()), 0)

    def test_apply_signal_and_repeated_apply_save_contract(self):
        dialog = self.dialog()
        applies = []
        saves = []
        dialog.apply_requested.connect(lambda *args: applies.append(args))
        dialog.save_requested.connect(lambda: saves.append(True))
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.apply_button.click()
        self.assertEqual(applies, [])

        self.select_points(dialog)
        physical_a = tuple(
            self.core.logical_to_physical(axis, value, clamp=False)
            for axis, value in zip(("X", "Y"), dialog.point_a)
        )
        physical_b = tuple(
            self.core.logical_to_physical(axis, value, clamp=False)
            for axis, value in zip(("X", "Y"), dialog.point_b)
        )
        source_distance = np.hypot(
            physical_b[0] - physical_a[0], physical_b[1] - physical_a[1]
        )
        self.assertAlmostEqual(dialog.distance_input.value(), source_distance, places=8)
        self.assertIn(f"{source_distance:.7g}", dialog.source_distance_label.text())
        self.assertEqual(dialog.scale_label.text(), "1 ×")
        dialog.distance_input.setValue(12.5)
        dialog.unit_edit.setText("1/Å")
        self.assertAlmostEqual(
            float(dialog.scale_label.text().removesuffix(" ×")),
            12.5 / source_distance,
            places=6,
        )
        QTest.mouseClick(dialog.apply_button, Qt.LeftButton)
        self.assertEqual(len(applies), 1)
        point_a, point_b, target, unit = applies[0]
        self.assertEqual(point_a, dialog.point_a)
        self.assertEqual(point_b, dialog.point_b)
        np.testing.assert_allclose(point_a, (0.0, 0.0), atol=0.005)
        np.testing.assert_allclose(point_b, (3.0, 2.0), atol=0.005)
        self.assertEqual((target, unit), (12.5, "1/Å"))
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.set_applied(False, "应用未成功")
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertEqual(dialog.status_label.text(), "应用未成功")

        dialog.apply_button.click()
        self.assertEqual(len(applies), 2)
        dialog.set_applied(True)
        self.assertTrue(dialog.save_button.isEnabled())
        QTest.mouseClick(dialog.save_button, Qt.LeftButton)
        self.assertEqual(saves, [True])
        dialog.set_saved(True, "已写入测试路径")
        self.assertEqual(dialog.status_label.text(), "已写入测试路径")
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.set_saved(False)
        self.assertTrue(dialog.save_button.isEnabled())

    def test_every_relevant_change_invalidates_successful_application(self):
        dialog = self.dialog()
        self.select_points(dialog)
        changes = (
            ("point", lambda: self.click_point(dialog, (2, 1))),
            ("distance", lambda: dialog.distance_input.setValue(7.0)),
            ("unit", lambda: dialog.unit_edit.setText("nm⁻¹")),
            ("energy", lambda: dialog.energy_slider.setValue(1)),
            ("scan", lambda: dialog.scan_slider.setValue(1)),
        )
        for name, change in changes:
            with self.subTest(change=name):
                dialog.set_applied(True)
                self.assertTrue(dialog.save_button.isEnabled())
                change()
                self.app.processEvents()
                self.assertFalse(dialog.save_button.isEnabled())
                self.assertIn("重新应用", dialog.status_label.text())

    def test_coincident_points_are_rejected_without_emitting(self):
        dialog = self.dialog()
        applies = []
        dialog.apply_requested.connect(lambda *args: applies.append(args))
        self.click_point(dialog, (1, 1))
        dialog._points.append(dialog.point_a)
        dialog._refresh_points()
        dialog.apply_button.click()
        self.assertEqual(applies, [])
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertTrue(dialog.status_label.text())

    def test_success_adopts_host_core_and_next_preview_uses_new_axes(self):
        dialog = self.dialog()
        self.select_points(dialog)
        point_a = dialog.point_a
        point_b = dialog.point_b
        dialog.distance_input.setValue(12.5)
        dialog.unit_edit.setText("nm⁻¹")
        calibrated = self.core.copy_with_momentum_calibration(
            point_a, point_b, 12.5, "nm⁻¹"
        )
        old_mesh = dialog.image

        dialog.set_applied(False, "尚未应用", core=calibrated)
        self.assertIs(dialog.core, self.core)
        np.testing.assert_array_equal(dialog.x, self.core.coords["X"])
        self.assertIs(dialog.image, old_mesh)

        dialog.set_applied(True, core=calibrated)
        self.assertIs(dialog.core, calibrated)
        np.testing.assert_array_equal(dialog.x, calibrated.coords["X"])
        np.testing.assert_array_equal(dialog.y, calibrated.coords["Y"])
        self.assertEqual(dialog.point_a, point_a)
        self.assertEqual(dialog.point_b, point_b)
        self.assertEqual(dialog.distance_input.value(), 12.5)
        self.assertIsNot(dialog.image, old_mesh)
        self.assertIn("Kx (nm⁻¹)", dialog.axes.get_xlabel())
        self.assertIn("Ky (nm⁻¹)", dialog.axes.get_ylabel())
        expected_x = calibrated.logical_to_physical("X", point_a[0], clamp=False)
        self.assertIn(f"{expected_x:.5g}", dialog.point_a_label.text())
        self.assertAlmostEqual(float(dialog.source_distance_label.text().split()[0]), 12.5)
        self.assertEqual(dialog.scale_label.text(), "1 ×")
        self.assertTrue(dialog.save_button.isEnabled())

        dialog.distance_input.setValue(25.0)
        self.assertAlmostEqual(float(dialog.scale_label.text().removesuffix(" ×")), 2.0)
        self.assertFalse(dialog.save_button.isEnabled())

    def test_fixed_action_area_keeps_both_buttons_visible_at_supported_sizes(self):
        dialog = self.dialog()
        self.assertIs(dialog.apply_button.parentWidget(), dialog.action_area)
        self.assertIs(dialog.save_button.parentWidget(), dialog.action_area)
        self.assertNotEqual(dialog.apply_button.parentWidget(), dialog.controls)
        for width, height in ((920, 720), (640, 600)):
            with self.subTest(size=(width, height)):
                dialog.resize(width, height)
                self.app.processEvents()
                scrollbar = dialog.scroll_area.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
                self.app.processEvents()
                for button in (dialog.apply_button, dialog.save_button):
                    self.assertTrue(button.isVisible())
                    self.assertGreater(button.visibleRegion().boundingRect().width(), 0)
                    origin = button.mapTo(dialog, QPoint(0, 0))
                    self.assertGreaterEqual(origin.x(), 0)
                    self.assertGreaterEqual(origin.y(), 0)
                    self.assertLessEqual(origin.x() + button.width(), dialog.width())
                    self.assertLessEqual(origin.y() + button.height(), dialog.height())

    def test_missing_common_unit_suggests_inverse_angstrom(self):
        self.core.coord_units["X"] = None
        self.core.coord_units["Y"] = None
        dialog = self.dialog()
        self.assertEqual(dialog.unit_edit.text(), "Å⁻¹")

    def test_theme_changes_update_qt_and_matplotlib_colors(self):
        previous = theme.current_theme()
        dialog = self.dialog()
        try:
            for name in ("dark", "light"):
                theme.set_theme(name)
                self.app.processEvents()
                self.assertEqual(
                    to_hex(dialog.figure.get_facecolor()).upper(), theme.BG_1.upper()
                )
                self.assertIn(theme.BG_2, dialog.styleSheet())
                self.assertIn(theme.TEXT_2, dialog.point_hint_label.styleSheet())
                self.assertIn(theme.TEXT_1, dialog.title_label.styleSheet())
        finally:
            theme.set_theme(previous)


if __name__ == "__main__":
    unittest.main()
