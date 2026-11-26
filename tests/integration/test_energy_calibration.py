import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QApplication, QDialog

from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.ui.axis_assignment_dialog import AxisAssignmentDialog
from bandscope.ui.result_workspace import AnalysisPageSpec
from tests.support.view_recipe import control_window


class FakeEnergyCalibrationDialog(QDialog):
    apply_requested = pyqtSignal(float, float, float, str)
    save_requested = pyqtSignal()

    actions = []
    instances = []

    def __init__(self, core, *, plane="XZ", normal_index=0, scan_index=0, parent=None):
        super().__init__(parent)
        self.core = core
        self.plane = plane
        self.normal_index = normal_index
        self.scan_index = scan_index
        self.applied_results = []
        self.applied_cores = []
        self.saved_results = []
        type(self).instances.append(self)

    def exec_(self):
        for action in self.actions:
            if action[0] == "apply":
                _, reference, second, spacing, unit = action
                self.apply_requested.emit(reference, second, spacing, unit)
            elif action[0] == "save":
                self.save_requested.emit()
        return QDialog.Rejected

    def set_applied(self, success, message="", core=None):
        self.applied_results.append((bool(success), message))
        self.applied_cores.append(core)
        if success and core is not None:
            self.core = core

    def set_saved(self, success, message=""):
        self.saved_results.append((bool(success), message))


class EnergyCalibrationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "energy.npz"
        self.raw = np.arange(4 * 3 * 4 * 2, dtype=np.float32).reshape(4, 3, 4, 2)
        self.x = np.asarray([-2.0, 0.0, 2.0, 4.0])
        self.y = np.asarray([10.0, 20.0, 40.0])
        self.energy = np.arange(4, dtype=np.float64)
        np.savez(
            self.path,
            sample=self.raw,
            kx=self.x,
            ky=self.y,
            E=self.energy,
            time=np.asarray([-1.0, 2.0]),
            axis_order=np.asarray(["X", "Y", "E", "T"]),
        )
        self.window = control_window()
        self.assertTrue(self.window.load_data(str(self.path)))
        FakeEnergyCalibrationDialog.actions = []
        FakeEnergyCalibrationDialog.instances = []
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self._settle_background_readers)
        self.addCleanup(self.window.close)

    def _settle_background_readers(self):
        pending = []
        for session in self.window.source_manager:
            identity = getattr((session.state or {}).get("core"), "source_identity", None)
            if identity is not None:
                pending.append(identity.future)
        identity = getattr(self.window.core, "source_identity", None)
        if identity is not None:
            pending.append(identity.future)
        for future in pending:
            try:
                future.result(timeout=5)
            except Exception:
                pass
        time.sleep(0.05)

    def run_calibration(self, actions):
        FakeEnergyCalibrationDialog.actions = list(actions)

        def exercise(dialog):
            dialog.energy_calibration_requested.emit()
            return dialog.result()

        with patch.object(AxisAssignmentDialog, "exec_", exercise), patch(
            "bandscope.ui.energy_calibration_dialog.EnergyCalibrationDialog",
            FakeEnergyCalibrationDialog,
        ):
            return self.window.on_source_assignment_requested()

    @staticmethod
    def apply_action(reference=1.0, second=3.0, spacing=0.4, unit="eV"):
        return ("apply", reference, second, spacing, unit)

    def add_analysis(self, axis_index=2):
        spec = AnalysisPageSpec(
            self.window._make_page_id(),
            "定标前分析",
            "energy_dos",
            "test",
            source_page_id=self.window.left_workspace.home_page_id,
            params={"t_index": 0, "axis_index": axis_index},
        )
        self.window.left_workspace.add_page(spec)
        self.window.left_workspace.activate_page(spec.page_id)
        return spec

    @staticmethod
    def set_energy_geometry(spec):
        spec.params["axis_interval"] = {
            "axis_key": "E",
            "minimum": 0.0,
            "maximum": 3.0,
            "low": 0.0,
            "up": 2.0,
            "locked": True,
        }
        spec.params["axis_domain"] = {"minimum": 0.0, "maximum": 3.0}
        spec.params["crop_regions"] = [
            {
                "view": "3d",
                "operation": "crop",
                "axes": ("X", "Y", "E"),
                "bounds": (-2.0, 2.0, 10.0, 20.0, 0.0, 2.0),
            }
        ]

    def test_axis_dialog_has_independent_energy_calibration_entry(self):
        dialog = AxisAssignmentDialog(
            self.window.core.axis_assignment,
            self.window.core.auto_axis_assignment,
            self.window.core.coordinate_candidates,
            candidate_units=self.window.core.coordinate_candidate_units,
        )
        self.addCleanup(dialog.deleteLater)
        momentum_requested = []
        energy_requested = []
        dialog.calibration_requested.connect(lambda: momentum_requested.append(True))
        dialog.energy_calibration_requested.connect(lambda: energy_requested.append(True))

        self.assertEqual(dialog.energy_calibration_button.text(), "能量定标")
        dialog.energy_calibration_button.click()
        self.assertEqual(momentum_requested, [])
        self.assertEqual(energy_requested, [True])
        dialog.calibration_button.click()
        self.assertEqual(momentum_requested, [True])
        self.assertEqual(energy_requested, [True])

    def test_dialog_defaults_to_xz_and_current_scan_frame(self):
        self.window.timeline_bar.slider_time.setValue(1)

        self.assertFalse(self.run_calibration([]))

        dialog = FakeEnergyCalibrationDialog.instances[-1]
        self.assertIs(dialog.core, self.window.core)
        self.assertEqual(dialog.plane, "XZ")
        self.assertEqual(dialog.normal_index, 0)
        self.assertEqual(dialog.scan_index, 1)

    def test_core_copy_regenerates_energy_and_preserves_source_metadata(self):
        original = self.window.core
        calibrated = original.copy_with_energy_calibration(1.0, 3.0, 0.4, unit="meV")

        np.testing.assert_allclose(calibrated.coords["E"], [-0.2, 0.0, 0.2, 0.4])
        np.testing.assert_array_equal(original.coords["E"], self.energy)
        np.testing.assert_array_equal(original.coordinate_candidates["E"], self.energy)
        np.testing.assert_array_equal(calibrated.original_coordinate_candidates["E"], self.energy)
        np.testing.assert_array_equal(calibrated.coords["X"], self.x)
        np.testing.assert_array_equal(calibrated.coords["Y"], self.y)
        np.testing.assert_array_equal(calibrated.coords["delay"], [-1.0, 2.0])
        np.testing.assert_array_equal(calibrated.raw_data, self.raw)
        self.assertIs(calibrated.source_array, original.source_array)
        self.assertIs(calibrated.source_identity, original.source_identity)
        self.assertEqual(calibrated.axis_assignment.coord_keys["E"], "E")
        self.assertEqual(calibrated.coord_sources["E"], "file")
        self.assertEqual(calibrated.coord_units["E"], "meV")
        self.assertFalse(calibrated.coords["E"].flags.writeable)

        restored = calibrated.copy_with_assignment(
            calibrated.auto_axis_assignment, restore_original_coordinates=True
        )
        np.testing.assert_array_equal(restored.coords["E"], self.energy)

    def test_cancel_and_invalid_apply_do_not_modify_workspace(self):
        original = self.window.core
        source_id = self.window.source_manager.active_id

        self.assertFalse(self.run_calibration([]))
        self.assertIs(self.window.core, original)
        self.assertFalse(self.run_calibration([
            self.apply_action(reference=1.0, second=1.0),
            self.apply_action(spacing=0.0),
        ]))

        self.assertIs(self.window.core, original)
        self.assertEqual(self.window.source_manager.active_id, source_id)
        self.assertEqual(
            [result[0] for result in FakeEnergyCalibrationDialog.instances[-1].applied_results],
            [False, False],
        )

    def test_apply_preserves_pages_and_remaps_only_energy_geometry(self):
        energy_page = self.add_analysis(axis_index=2)
        self.set_energy_geometry(energy_page)
        momentum_page = self.add_analysis(axis_index=0)
        momentum_page.params["axis_interval"] = {
            "axis_key": "X",
            "minimum": -2.0,
            "maximum": 4.0,
            "low": -2.0,
            "up": 2.0,
            "locked": False,
        }
        momentum_page.params["axis_domain"] = {"minimum": -2.0, "maximum": 4.0}
        momentum_interval = dict(momentum_page.params["axis_interval"])
        momentum_domain = dict(momentum_page.params["axis_domain"])
        self.window.left_workspace.activate_page(energy_page.page_id)
        original = self.window.core
        source_id = self.window.source_manager.active_id
        page_ids = list(self.window.left_workspace.page_specs)
        activation_count = self.window._source_activation_count

        applied = self.run_calibration([self.apply_action()])

        self.assertTrue(applied, FakeEnergyCalibrationDialog.instances[-1].applied_results)
        self.assertIsNot(self.window.core, original)
        self.assertIs(self.window.source_manager.active().state["core"], self.window.core)
        self.assertEqual(self.window.source_manager.active_id, source_id)
        self.assertEqual(len(self.window.source_manager), 1)
        self.assertEqual(list(self.window.left_workspace.page_specs), page_ids)
        self.assertEqual(self.window._source_activation_count, activation_count + 1)
        np.testing.assert_array_equal(self.window.core.raw_data, self.raw)
        np.testing.assert_allclose(self.window.core.coords["E"], [-0.2, 0.0, 0.2, 0.4])
        np.testing.assert_allclose(self.window.original_coords["E"], [-0.2, 0.0, 0.2, 0.4])
        np.testing.assert_allclose(self.window.core.coordinate_candidates["E"], [-0.2, 0.0, 0.2, 0.4])
        self.assertEqual(energy_page.params["axis_interval"], {
            "axis_key": "E",
            "minimum": -0.2,
            "maximum": 0.4,
            "low": -0.2,
            "up": 0.2,
            "locked": True,
        })
        self.assertEqual(
            energy_page.params["axis_domain"], {"minimum": -0.2, "maximum": 0.4}
        )
        self.assertEqual(
            energy_page.params["crop_regions"][0]["bounds"],
            [-2.0, 2.0, 10.0, 20.0, -0.2, 0.2],
        )
        self.assertEqual(momentum_page.params["axis_interval"], momentum_interval)
        self.assertEqual(momentum_page.params["axis_domain"], momentum_domain)

    def test_reversed_calibration_sorts_energy_interval_domain_and_crop_bounds(self):
        analysis = self.add_analysis()
        self.set_energy_geometry(analysis)

        self.assertTrue(self.run_calibration([
            self.apply_action(reference=3.0, second=1.0)
        ]))

        np.testing.assert_allclose(self.window.core.coords["E"], [0.6, 0.4, 0.2, 0.0])
        self.assertAlmostEqual(analysis.params["axis_interval"]["minimum"], 0.0)
        self.assertAlmostEqual(analysis.params["axis_interval"]["maximum"], 0.6)
        self.assertAlmostEqual(analysis.params["axis_interval"]["low"], 0.2)
        self.assertAlmostEqual(analysis.params["axis_interval"]["up"], 0.6)
        self.assertAlmostEqual(analysis.params["axis_domain"]["minimum"], 0.0)
        self.assertAlmostEqual(analysis.params["axis_domain"]["maximum"], 0.6)
        np.testing.assert_allclose(
            analysis.params["crop_regions"][0]["bounds"][-2:], [0.2, 0.6]
        )

    def test_repeated_apply_uses_latest_core_and_remaps_current_geometry(self):
        analysis = self.add_analysis()
        self.set_energy_geometry(analysis)
        original = self.window.core

        self.assertTrue(self.run_calibration([
            self.apply_action(),
            self.apply_action(reference=0.0, second=1.0, spacing=0.2, unit="meV"),
        ]))

        dialog = FakeEnergyCalibrationDialog.instances[-1]
        first_core, second_core = dialog.applied_cores
        self.assertIsNot(first_core, original)
        self.assertIs(second_core, self.window.core)
        self.assertIs(dialog.core, second_core)
        np.testing.assert_allclose(first_core.coords["E"], [-0.2, 0.0, 0.2, 0.4])
        np.testing.assert_allclose(second_core.coords["E"], [0.0, 0.2, 0.4, 0.6])
        self.assertEqual(second_core.coord_units["E"], "meV")
        self.assertAlmostEqual(analysis.params["axis_interval"]["low"], 0.0)
        self.assertAlmostEqual(analysis.params["axis_interval"]["up"], 0.4)
        self.assertAlmostEqual(analysis.params["axis_domain"]["minimum"], 0.0)
        self.assertAlmostEqual(analysis.params["axis_domain"]["maximum"], 0.6)
        np.testing.assert_allclose(
            analysis.params["crop_regions"][0]["bounds"][-2:], [0.0, 0.4]
        )

    def test_real_dialog_applies_and_closes_axis_dialog(self):
        from bandscope.ui.energy_calibration_dialog import EnergyCalibrationDialog

        real_path = self.root / "energy-real.npz"
        np.savez(
            real_path,
            sample=np.arange(3 * 3 * 4 * 2, dtype=np.float32).reshape(3, 3, 4, 2),
            kx=np.asarray([-1.0, 0.0, 1.0]),
            ky=self.y,
            E=self.energy,
            time=np.asarray([-1.0, 2.0]),
            axis_order=np.asarray(["X", "Y", "E", "T"]),
        )
        self.assertTrue(self.window.load_data(str(real_path)))

        def exercise_axis(dialog):
            dialog.energy_calibration_requested.emit()
            return dialog.result()

        def exercise_energy(dialog):
            dialog.set_reference_index(1.0)
            dialog.set_second_index(3.0)
            dialog.spacing_input.setValue(0.4)
            dialog.unit_edit.setText("meV")
            dialog.apply_button.click()
            return QDialog.Rejected

        original = self.window.core
        with patch.object(AxisAssignmentDialog, "exec_", exercise_axis), patch.object(
            EnergyCalibrationDialog, "exec_", exercise_energy
        ):
            self.assertTrue(self.window.on_source_assignment_requested())

        self.assertIsNot(self.window.core, original)
        np.testing.assert_allclose(self.window.core.coords["E"], [-0.2, 0.0, 0.2, 0.4])
        self.assertEqual(self.window.core.coord_units["E"], "meV")

    def test_save_reuses_complete_source_export_and_roundtrips_energy(self):
        target = self.root / "roundtrip.mat"
        with patch(
            "bandscope.ui.file_dialogs.RememberingFileDialog.getSaveFileName",
            return_value=(str(target), "NumPy Files (*.npz)"),
        ):
            self.assertTrue(self.run_calibration([
                self.apply_action(unit="meV"),
                ("save",),
            ]))

        output = self.root / "roundtrip.npz"
        self.assertTrue(output.is_file())
        self.assertEqual(FakeEnergyCalibrationDialog.instances[-1].saved_results[-1][0], True)
        loaded = AnalyzerCore()
        success, info = loaded.load_npz(str(output))
        self.assertTrue(success, info)
        np.testing.assert_array_equal(loaded.source_array, self.raw)
        np.testing.assert_allclose(loaded.coords["E"], [-0.2, 0.0, 0.2, 0.4])
        np.testing.assert_allclose(loaded.coords["X"], self.x)
        np.testing.assert_allclose(loaded.coords["Y"], self.y)
        np.testing.assert_allclose(loaded.coords["delay"], [-1.0, 2.0])
        self.assertEqual(loaded.coord_units["E"], "meV")
        if loaded.source_identity is not None:
            loaded.source_identity.future.result(timeout=5)


if __name__ == "__main__":
    unittest.main()
