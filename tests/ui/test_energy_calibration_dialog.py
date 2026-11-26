import os
import tempfile
import unittest

import numpy as np
import matplotlib.patheffects as path_effects
from matplotlib.colors import to_hex
from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.ui import theme
from bandscope.ui.axis_interval_bar import DualRangeSlider
from bandscope.ui.energy_calibration_dialog import EnergyCalibrationDialog


class EnergyCalibrationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self._process_deferred_deletes)
        self.raw = np.arange(4 * 3 * 5 * 2, dtype=np.float32).reshape(4, 3, 5, 2)
        self.x = np.asarray([-2.0, -0.5, 0.25, 2.0])
        self.y = np.asarray([3.0, 1.0, -2.0])
        self.energy = np.asarray([-1.0, -0.4, 0.2, 1.1, 2.4])
        self.delay = np.asarray([-1.0, 2.0])
        self.core = self.make_core()

    def _process_deferred_deletes(self):
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def make_core(self, *, energy=None, unit="eV", name="energy"):
        path = os.path.join(self.temp.name, f"{name}.npz")
        np.savez(
            path,
            sample=self.raw,
            kx=self.x,
            ky=self.y,
            energy=self.energy if energy is None else energy,
            delay=self.delay,
            axis_order=np.asarray(["X", "Y", "E", "T"]),
            scan_type=np.asarray(["time"]),
            kx_unit=np.asarray(["Å⁻¹"]),
            ky_unit=np.asarray(["Å⁻¹"]),
            E_unit=np.asarray([unit]),
            time_unit=np.asarray(["fs"]),
        )
        core = AnalyzerCore()
        success, info = core.load_npz(path)
        self.assertTrue(success, info)
        return core

    def dialog(self, **kwargs):
        dialog = EnergyCalibrationDialog(self.core, **kwargs)
        dialog.show()
        self.app.processEvents()
        dialog.canvas.draw()
        self.app.processEvents()
        self.addCleanup(dialog.scan_slider.coordinate_popup.close)
        self.addCleanup(dialog.normal_slider.coordinate_popup.close)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        return dialog

    def canvas_point(self, dialog, axes_x, logical_index):
        display_x, display_y = dialog._line_transform.transform(
            (axes_x, dialog._physical_energy(logical_index))
        )
        ratio = dialog.canvas.devicePixelRatioF()
        return QPoint(
            int(round(display_x / ratio)),
            int(round(dialog.canvas.height() - display_y / ratio)),
        )

    def drag_energy_line(self, dialog, name, target_index):
        start = self.canvas_point(
            dialog,
            1.045,
            dialog.reference_index if name == "reference" else dialog.second_index,
        )
        end = self.canvas_point(dialog, 1.045, target_index)
        press = QMouseEvent(
            QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
        )
        move = QMouseEvent(
            QEvent.MouseMove, end, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
        )
        release = QMouseEvent(
            QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton, Qt.NoModifier
        )
        QApplication.sendEvent(dialog.canvas, press)
        QApplication.sendEvent(dialog.canvas, move)
        QApplication.sendEvent(dialog.canvas, release)
        self.app.processEvents()

    def test_default_xz_slice_shared_sliders_and_no_colorbar(self):
        dialog = self.dialog(normal_index=1, scan_index=1)
        self.assertEqual(dialog.plane, "XZ")
        self.assertEqual(dialog.normal_axis, "Y")
        self.assertEqual(dialog.normal_index, 1)
        self.assertEqual(dialog.scan_index, 1)
        np.testing.assert_array_equal(dialog.image.get_array(), self.raw[:, 1, :, 1].T)
        self.assertEqual(dialog.unit_edit.text(), "eV")
        for slider in (dialog.normal_slider, dialog.scan_slider):
            self.assertIsInstance(slider, DualRangeSlider)
            self.assertTrue(slider.interval.locked)
            self.assertEqual(slider.interval.length, 0.0)
            self.assertTrue(slider.lock_badge.isVisible())
            self.assertFalse(hasattr(slider, "input_length"))
        self.assertFalse(hasattr(dialog, "colorbar"))
        self.assertEqual(len(dialog.figure.axes), 1)
        self.assertEqual(dialog.image.get_cmap().name, "magma")
        self.assertIn("不做区间平均或求和", dialog.slice_hint_label.text())
        for line, handle, annotation, logical in (
            (
                dialog.reference_line,
                dialog.reference_handle,
                dialog.reference_annotation,
                dialog.reference_index,
            ),
            (
                dialog.second_line,
                dialog.second_handle,
                dialog.second_annotation,
                dialog.second_index,
            ),
        ):
            self.assertEqual(line.get_linestyle(), "--")
            self.assertLess(line.get_xdata()[0], 0.0)
            self.assertGreater(line.get_xdata()[-1], 1.0)
            self.assertTrue(
                any(isinstance(effect, path_effects.withStroke) for effect in line.get_path_effects())
            )
            handle_x, _handle_y = handle.get_transform().transform(
                (1.045, dialog._physical_energy(logical))
            )
            self.assertGreater(handle_x, dialog.axes.bbox.x1)
            self.assertIs(annotation.get_transform(), dialog.axes.transAxes)
            axes_x, axes_y = annotation.get_position()
            self.assertGreater(axes_x, 0.0)
            self.assertLess(axes_x, 1.0)
            self.assertGreater(axes_y, 0.0)
            self.assertLess(axes_y, 1.0)
            label_bounds = annotation.get_window_extent(dialog.canvas.get_renderer())
            self.assertGreaterEqual(label_bounds.x0, dialog.axes.bbox.x0)
            self.assertGreaterEqual(label_bounds.y0, dialog.axes.bbox.y0)
            self.assertLessEqual(label_bounds.x1, dialog.axes.bbox.x1)
            self.assertLessEqual(label_bounds.y1, dialog.axes.bbox.y1)

    def test_plane_slice_and_flip_use_real_data_without_changing_intensity(self):
        dialog = self.dialog(normal_index=1, scan_index=1)
        original = self.raw.copy()
        dialog.plane_combo.setCurrentText("YZ")
        self.app.processEvents()
        self.assertEqual(dialog.plane, "YZ")
        self.assertEqual(dialog.normal_axis, "X")
        np.testing.assert_array_equal(dialog.image.get_array(), self.raw[1, :, :, 1].T)

        dialog.normal_slider.setValue(2)
        dialog.scan_slider.setValue(0)
        self.app.processEvents()
        np.testing.assert_array_equal(dialog.image.get_array(), self.raw[2, :, :, 0].T)
        dialog.plane_combo.setCurrentText("XZ")
        self.app.processEvents()
        self.assertEqual(dialog.normal_index, 1)
        np.testing.assert_array_equal(dialog.image.get_array(), self.raw[:, 1, :, 0].T)

        before = np.asarray(dialog.image.get_array()).copy()
        dialog.flip_checkbox.setChecked(True)
        self.app.processEvents()
        self.assertTrue(dialog.axes.yaxis_inverted())
        np.testing.assert_array_equal(dialog.image.get_array(), before)
        dialog.flip_checkbox.setChecked(False)
        self.assertFalse(dialog.axes.yaxis_inverted())
        np.testing.assert_array_equal(self.raw, original)

    def test_real_mouse_drags_outside_handles_before_and_after_energy_flip(self):
        dialog = self.dialog()
        self.drag_energy_line(dialog, "reference", 2.25)
        self.assertAlmostEqual(dialog.reference_index, 2.25, delta=0.04)
        self.assertIn("逻辑 2.2", dialog.reference_value_label.text())

        dialog.flip_checkbox.setChecked(True)
        self.app.processEvents()
        dialog.canvas.draw()
        self.drag_energy_line(dialog, "second", 0.5)
        self.assertAlmostEqual(dialog.second_index, 0.5, delta=0.04)
        self.assertTrue(dialog.axes.yaxis_inverted())
        self.assertNotEqual(dialog.reference_index, dialog.second_index)

    def test_fractional_logical_indices_produce_uniform_readonly_axis_and_signal(self):
        dialog = self.dialog()
        applies = []
        dialog.apply_requested.connect(lambda *args: applies.append(args))
        dialog.set_reference_index(1.5)
        dialog.set_second_index(4.0)
        dialog.spacing_input.setValue(0.25)
        dialog.unit_edit.setText("meV")
        result = dialog._calibration()
        expected = (np.arange(self.raw.shape[2], dtype=np.float64) - 1.5) * 0.25 / 2.5
        np.testing.assert_allclose(result.energy, expected)
        self.assertFalse(result.energy.flags.writeable)
        self.assertEqual(result.source_spacing, 2.5)
        self.assertEqual(result.target_spacing, 0.25)
        self.assertAlmostEqual(result.scale, 0.1)
        self.assertIn("2.5", dialog.source_spacing_label.text())
        self.assertIn("0.1", dialog.scale_label.text())

        QTest.mouseClick(dialog.apply_button, Qt.LeftButton)
        self.assertEqual(applies, [(1.5, 4.0, 0.25, "meV")])
        self.assertFalse(dialog.save_button.isEnabled())

    def test_reverse_line_order_keeps_signed_energy_orientation(self):
        dialog = self.dialog()
        dialog.set_reference_index(4.0)
        dialog.set_second_index(1.0)
        dialog.spacing_input.setValue(0.5)
        result = dialog._calibration()
        expected = (np.arange(self.raw.shape[2], dtype=np.float64) - 4.0) * 0.5 / -3.0
        np.testing.assert_allclose(result.energy, expected)
        self.assertLess(result.scale, 0.0)

    def test_coincident_lines_are_rejected_without_emitting(self):
        dialog = self.dialog()
        applies = []
        dialog.apply_requested.connect(lambda *args: applies.append(args))
        dialog.set_second_index(dialog.reference_index)
        dialog.apply_button.click()
        self.assertEqual(applies, [])
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertTrue(dialog.status_label.text())
        self.assertEqual(dialog.source_spacing_label.text(), "–")

    def test_apply_save_and_every_relevant_change_invalidates_application(self):
        dialog = self.dialog(normal_index=1, scan_index=1)
        saves = []
        dialog.save_requested.connect(lambda: saves.append(True))
        dialog.set_applied(True)
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.save_button.click()
        self.assertEqual(saves, [True])
        dialog.set_saved(True, "已写入测试路径")
        self.assertEqual(dialog.status_label.text(), "已写入测试路径")
        self.assertTrue(dialog.save_button.isEnabled())

        changes = (
            ("reference", lambda: dialog.set_reference_index(1.25)),
            ("second", lambda: dialog.set_second_index(3.5)),
            ("spacing", lambda: dialog.spacing_input.setValue(2.0)),
            ("unit", lambda: dialog.unit_edit.setText("meV")),
            ("plane", lambda: dialog.plane_combo.setCurrentText("YZ")),
            ("normal", lambda: dialog.normal_slider.setValue(2)),
            ("scan", lambda: dialog.scan_slider.setValue(0)),
            ("flip", lambda: dialog.flip_checkbox.setChecked(True)),
        )
        for name, change in changes:
            with self.subTest(change=name):
                dialog.set_applied(True)
                self.assertTrue(dialog.save_button.isEnabled())
                change()
                self.app.processEvents()
                self.assertFalse(dialog.save_button.isEnabled())
                self.assertIn("重新应用", dialog.status_label.text())

    def test_success_adopts_calibrated_core_without_touching_raw_data(self):
        dialog = self.dialog(normal_index=1, scan_index=1)
        dialog.set_reference_index(1.0)
        dialog.set_second_index(4.0)
        dialog.spacing_input.setValue(0.3)
        dialog.unit_edit.setText("meV")
        result = dialog._calibration()
        calibrated = self.make_core(energy=result.energy, unit="meV", name="calibrated")
        old_mesh = dialog.image

        dialog.set_applied(False, "尚未应用", core=calibrated)
        self.assertIs(dialog.core, self.core)
        self.assertIs(dialog.image, old_mesh)
        dialog.set_applied(True, core=calibrated)
        self.assertIs(dialog.core, calibrated)
        self.assertIsNot(dialog.image, old_mesh)
        np.testing.assert_array_equal(dialog.energy, result.energy)
        np.testing.assert_array_equal(dialog.image.get_array(), self.raw[:, 1, :, 1].T)
        np.testing.assert_array_equal(dialog.raw_data, self.raw)
        self.assertIn("E (meV)", dialog.axes.get_ylabel())
        self.assertTrue(dialog.save_button.isEnabled())

    def test_fixed_action_area_and_theme_changes(self):
        previous = theme.current_theme()
        dialog = self.dialog()
        self.assertIs(dialog.apply_button.parentWidget(), dialog.action_area)
        self.assertIs(dialog.save_button.parentWidget(), dialog.action_area)
        try:
            for name in ("dark", "light"):
                theme.set_theme(name)
                self.app.processEvents()
                self.assertEqual(
                    to_hex(dialog.figure.get_facecolor()).upper(), theme.BG_1.upper()
                )
                self.assertIn(theme.BG_2, dialog.styleSheet())
                self.assertIn(theme.TEXT_1, dialog.title_label.styleSheet())
                for axis_label in (dialog.axes.xaxis.label, dialog.axes.yaxis.label):
                    self.assertEqual(
                        to_hex(axis_label.get_color()).upper(), theme.TEXT_2.upper()
                    )
                self.assertEqual(
                    to_hex(dialog.axes.spines["bottom"].get_edgecolor()).upper(),
                    theme.BORDER_HEX.upper(),
                )
                for annotation in (dialog.reference_annotation, dialog.second_annotation):
                    self.assertEqual(
                        to_hex(annotation.get_bbox_patch().get_facecolor()).upper(),
                        theme.BG_1.upper(),
                    )
            dialog.resize(680, 620)
            self.app.processEvents()
            for button in (dialog.apply_button, dialog.save_button):
                origin = button.mapTo(dialog, QPoint(0, 0))
                self.assertGreaterEqual(origin.x(), 0)
                self.assertGreaterEqual(origin.y(), 0)
                self.assertLessEqual(origin.x() + button.width(), dialog.width())
                self.assertLessEqual(origin.y() + button.height(), dialog.height())
        finally:
            theme.set_theme(previous)


if __name__ == "__main__":
    unittest.main()
