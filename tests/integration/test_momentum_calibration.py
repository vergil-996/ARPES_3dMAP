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


class FakeMomentumCalibrationDialog(QDialog):
    apply_requested = pyqtSignal(tuple, tuple, float, str)
    save_requested = pyqtSignal()

    actions = []
    instances = []

    def __init__(self, core, *, energy_index=0, scan_index=0, parent=None):
        super().__init__(parent)
        self.core = core
        self.energy_index = energy_index
        self.scan_index = scan_index
        self.applied_results = []
        self.applied_cores = []
        self.saved_results = []
        self.applied = False
        type(self).instances.append(self)

    def exec_(self):
        for action in self.actions:
            if action[0] == "apply":
                _, point_a, point_b, distance, unit = action
                self.apply_requested.emit(point_a, point_b, distance, unit)
            elif action[0] == "save":
                self.save_requested.emit()
        return QDialog.Rejected

    def set_applied(self, success, message="", core=None):
        self.applied = bool(success)
        self.applied_results.append((bool(success), message))
        self.applied_cores.append(core)
        if success and core is not None:
            self.core = core

    def set_saved(self, success, message=""):
        self.saved_results.append((bool(success), message))


class MomentumCalibrationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "momentum.npz"
        self.raw = np.arange(4 * 3 * 3 * 2, dtype=np.float32).reshape(4, 3, 3, 2)
        self.x = np.asarray([-2.0, 0.0, 2.0, 4.0])
        self.y = np.asarray([10.0, 20.0, 40.0])
        np.savez(
            self.path,
            sample=self.raw,
            kx=self.x,
            ky=self.y,
            E=np.asarray([-0.3, 0.0, 0.3]),
            time=np.asarray([-1.0, 2.0]),
            axis_order=np.asarray(["X", "Y", "E", "T"]),
        )
        self.window = control_window()
        self.assertTrue(self.window.load_data(str(self.path)))
        FakeMomentumCalibrationDialog.actions = []
        FakeMomentumCalibrationDialog.instances = []
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
        FakeMomentumCalibrationDialog.actions = list(actions)

        def exercise(dialog):
            dialog.calibration_requested.emit()
            return dialog.result()

        with patch.object(AxisAssignmentDialog, "exec_", exercise), patch(
            "bandscope.ui.momentum_calibration_dialog.MomentumCalibrationDialog",
            FakeMomentumCalibrationDialog,
        ):
            return self.window.on_source_assignment_requested()

    @staticmethod
    def apply_action(distance=4.0, unit="Å⁻¹"):
        return ("apply", (0.0, 0.0), (1.0, 0.0), distance, unit)

    def add_analysis(self):
        spec = AnalysisPageSpec(
            self.window._make_page_id(),
            "定标前分析",
            "energy_dos",
            "test",
            source_page_id=self.window.left_workspace.home_page_id,
            params={"t_index": 0, "axis_index": 0},
        )
        self.window.left_workspace.add_page(spec)
        self.window.left_workspace.activate_page(spec.page_id)
        return spec

    def test_axis_dialog_has_explicit_calibration_entry(self):
        dialog = AxisAssignmentDialog(
            self.window.core.axis_assignment,
            self.window.core.auto_axis_assignment,
            self.window.core.coordinate_candidates,
            candidate_units=self.window.core.coordinate_candidate_units,
        )
        self.addCleanup(dialog.deleteLater)
        requested = []
        dialog.calibration_requested.connect(lambda: requested.append(True))
        self.assertEqual(dialog.calibration_button.text(), "Kx / Ky 定标")
        self.assertEqual(AxisAssignmentDialog.CALIBRATION_APPLIED, QDialog.Accepted + 1)
        dialog.calibration_button.click()
        self.assertEqual(requested, [True])

    def test_current_energy_and_scan_indices_are_passed_to_dialog(self):
        home = self.window.left_workspace.home_spec()
        home.params["home_slice_info"] = {"axis": 2, "index": 1}
        self.window.home_slice_info = dict(home.params["home_slice_info"])
        self.window.timeline_bar.slider_time.setValue(1)
        self.assertFalse(self.run_calibration([]))
        dialog = FakeMomentumCalibrationDialog.instances[-1]
        self.assertIs(dialog.core, self.window.core)
        self.assertEqual((dialog.energy_index, dialog.scan_index), (1, 1))

    def test_cancel_and_invalid_apply_do_not_modify_workspace(self):
        original = self.window.core
        source_id = self.window.source_manager.active_id
        self.assertFalse(self.run_calibration([]))
        self.assertIs(self.window.core, original)

        self.assertFalse(self.run_calibration([self.apply_action(distance=-1.0)]))
        self.assertIs(self.window.core, original)
        self.assertEqual(self.window.source_manager.active_id, source_id)
        self.assertEqual(FakeMomentumCalibrationDialog.instances[-1].applied_results[-1][0], False)

    def test_apply_preserves_analysis_source_and_synchronizes_state(self):
        analysis = self.add_analysis()
        analysis.params["axis_interval"] = {
            "axis_key": "X",
            "minimum": -2.0,
            "maximum": 4.0,
            "low": 0.0,
            "up": 2.0,
            "locked": False,
        }
        analysis.params["axis_domain"] = {"minimum": -2.0, "maximum": 4.0}
        analysis.params["crop_regions"] = [
            {
                "view": "2d",
                "operation": "crop",
                "axes": ("X", "Y"),
                "bounds": (-2.0, 2.0, 10.0, 20.0),
            }
        ]
        w = self.window
        original = w.core
        source_id = w.source_manager.active_id
        page_ids = list(w.left_workspace.page_specs)
        current_page = w.left_workspace.current_page_id
        activation_count = w._source_activation_count

        applied = self.run_calibration([self.apply_action()])
        self.assertTrue(applied, FakeMomentumCalibrationDialog.instances[-1].applied_results)
        self.assertIsNot(w.core, original)
        self.assertIs(w.source_manager.active().state["core"], w.core)
        self.assertEqual(w.source_manager.active_id, source_id)
        self.assertEqual(len(w.source_manager), 1)
        self.assertEqual(list(w.left_workspace.page_specs), page_ids)
        self.assertEqual(w.left_workspace.current_page_id, current_page)
        self.assertEqual(w._source_activation_count, activation_count + 1)
        np.testing.assert_array_equal(w.core.raw_data, self.raw)
        np.testing.assert_array_equal(original.coords["X"], self.x)
        np.testing.assert_allclose(w.core.coords["X"], self.x * 2.0)
        np.testing.assert_allclose(w.core.coords["Y"], self.y * 2.0)
        np.testing.assert_allclose(w.original_coords["X"], self.x * 2.0)
        np.testing.assert_allclose(w.base_coords["Y"], self.y * 2.0)
        np.testing.assert_allclose(w.core.coordinate_candidates["kx"], self.x * 2.0)
        self.assertEqual(w.core.coord_sources["X"], "file")
        self.assertEqual(w.core.coord_sources["Y"], "file")
        self.assertEqual(w.core.coord_units["X"], "Å⁻¹")
        self.assertEqual(w.core.source_layout["coord_keys"]["X"], "kx")
        self.assertEqual(analysis.params["axis_interval"]["up"], 4.0)
        self.assertEqual(analysis.params["axis_domain"]["maximum"], 8.0)
        self.assertEqual(analysis.params["crop_regions"][0]["bounds"], [-4.0, 4.0, 20.0, 40.0])
        self.assertIn("Kx", w.left_workspace.page_tree.source_meta.text())

    def test_repeated_apply_uses_latest_core(self):
        original = self.window.core
        self.assertTrue(self.run_calibration([
            self.apply_action(distance=4.0),
            self.apply_action(distance=6.0),
        ]))
        dialog = FakeMomentumCalibrationDialog.instances[-1]
        self.assertEqual([item[0] for item in dialog.applied_results], [True, True])
        first_core, second_core = dialog.applied_cores
        self.assertIsNot(first_core, original)
        self.assertIsNot(second_core, first_core)
        self.assertIs(second_core, self.window.core)
        self.assertIs(dialog.core, second_core)
        np.testing.assert_allclose(first_core.coords["X"], self.x * 2.0)
        np.testing.assert_allclose(second_core.coords["X"], self.x * 3.0)
        np.testing.assert_allclose(self.window.core.coords["Y"], self.y * 3.0)
        np.testing.assert_array_equal(original.coords["X"], self.x)

    def test_restore_automatic_clears_calibration_only_when_applied(self):
        original = self.window.core
        source_id = self.window.source_manager.active_id
        self.assertTrue(self.run_calibration([
            self.apply_action(),
            self.apply_action(distance=6.0),
        ]))
        calibrated = self.window.core
        np.testing.assert_allclose(calibrated.coords["X"], self.x * 3.0)

        def reject_restore(dialog):
            dialog.restore_button.click()
            return QDialog.Rejected

        with patch.object(AxisAssignmentDialog, "exec_", reject_restore):
            self.assertFalse(self.window.on_source_assignment_requested())
        self.assertIs(self.window.core, calibrated)
        np.testing.assert_allclose(self.window.core.coords["X"], self.x * 3.0)

        previews = {}

        def accept_restore(dialog):
            dialog.restore_button.click()
            previews["X"] = dialog._rows["X"]["preview"].text()
            previews["Y"] = dialog._rows["Y"]["preview"].text()
            return QDialog.Accepted

        with patch.object(AxisAssignmentDialog, "exec_", accept_restore):
            self.assertTrue(self.window.on_source_assignment_requested())

        restored = self.window.core
        self.assertEqual(previews, {"X": "-2 … 4", "Y": "10 … 40"})
        self.assertEqual(self.window.source_manager.active_id, source_id)
        self.assertEqual(len(self.window.source_manager), 1)
        self.assertIs(self.window.source_manager.active().state["core"], restored)
        np.testing.assert_array_equal(restored.coords["X"], self.x)
        np.testing.assert_array_equal(restored.coords["Y"], self.y)
        np.testing.assert_array_equal(restored.coordinate_candidates["kx"], self.x)
        np.testing.assert_array_equal(restored.coordinate_candidates["ky"], self.y)
        np.testing.assert_array_equal(restored.raw_data, original.raw_data)
        np.testing.assert_array_equal(self.window.original_coords["X"], self.x)
        np.testing.assert_array_equal(self.window.base_coords["Y"], self.y)
        self.assertEqual(restored.coord_units["X"], original.coord_units["X"])
        self.assertEqual(restored.coord_units["Y"], original.coord_units["Y"])
        self.assertEqual(
            restored.axis_assignment.coord_keys,
            original.auto_axis_assignment.coord_keys,
        )

    def test_save_uses_selected_filter_and_default_calibrated_name(self):
        self.assertTrue(self.run_calibration([self.apply_action()]))
        calibrated_core = self.window.core
        dialog = FakeMomentumCalibrationDialog.instances[-1]
        for selected_filter, suffix in (
            ("NumPy Files (*.npz)", ".npz"),
            ("MATLAB Files (*.mat)", ".mat"),
        ):
            with self.subTest(selected_filter=selected_filter), patch(
                "bandscope.ui.file_dialogs.RememberingFileDialog.getSaveFileName",
                return_value=(str(self.root / ("wrong.mat" if suffix == ".npz" else "wrong.npz")), selected_filter),
            ) as choose, patch(
                "bandscope.exporting.calibrated_source.save_calibrated_source"
            ) as save:
                self.assertTrue(self.window._save_momentum_calibration(dialog))
                default_name = choose.call_args.args[2]
                self.assertIn("calibrated", default_name)
                save.assert_called_once_with(str(self.root / f"wrong{suffix}"), calibrated_core)
                self.assertIsNone(QApplication.overrideCursor())
        self.assertEqual(dialog.saved_results[-1][0], True)

    def test_save_npz_writes_a_loadable_calibrated_source(self):
        target = self.root / "roundtrip.mat"
        with patch(
            "bandscope.ui.file_dialogs.RememberingFileDialog.getSaveFileName",
            return_value=(str(target), "NumPy Files (*.npz)"),
        ):
            self.assertTrue(self.run_calibration([
                self.apply_action(),
                ("save",),
            ]))
        output = self.root / "roundtrip.npz"
        self.assertTrue(output.is_file())
        self.assertEqual(FakeMomentumCalibrationDialog.instances[-1].saved_results[-1][0], True)
        loaded = AnalyzerCore()
        success, info = loaded.load_npz(str(output))
        self.assertTrue(success, info)
        np.testing.assert_array_equal(loaded.source_array, self.raw)
        np.testing.assert_allclose(loaded.coords["X"], self.x * 2.0)
        np.testing.assert_allclose(loaded.coords["Y"], self.y * 2.0)
        np.testing.assert_allclose(loaded.coords["delay"], [-1.0, 2.0])
        self.assertEqual(loaded.coord_units["X"], "Å⁻¹")
        if loaded.source_identity is not None:
            loaded.source_identity.future.result(timeout=5)

    def test_save_cancel_and_failure_keep_calibrated_workspace(self):
        self.add_analysis()
        self.assertTrue(self.run_calibration([self.apply_action()]))
        calibrated_core = self.window.core
        source_id = self.window.source_manager.active_id
        pages = list(self.window.left_workspace.page_specs)
        dialog = FakeMomentumCalibrationDialog.instances[-1]

        with patch(
            "bandscope.ui.file_dialogs.RememberingFileDialog.getSaveFileName",
            return_value=("", "NumPy Files (*.npz)"),
        ), patch("bandscope.exporting.calibrated_source.save_calibrated_source") as save:
            self.assertFalse(self.window._save_momentum_calibration(dialog))
            save.assert_not_called()

        def fail_while_busy(path, core):
            self.assertEqual(path, str(self.root / "failed.npz"))
            self.assertIs(core, calibrated_core)
            self.assertIsNotNone(QApplication.overrideCursor())
            raise OSError("disk full")

        self.assertIsNone(QApplication.overrideCursor())
        with patch(
            "bandscope.ui.file_dialogs.RememberingFileDialog.getSaveFileName",
            return_value=(str(self.root / "failed.npz"), "NumPy Files (*.npz)"),
        ), patch(
            "bandscope.exporting.calibrated_source.save_calibrated_source",
            side_effect=fail_while_busy,
        ):
            self.assertFalse(self.window._save_momentum_calibration(dialog))
        self.assertIsNone(QApplication.overrideCursor())
        self.assertEqual(dialog.saved_results[-1], (False, "disk full"))
        self.assertIs(self.window.core, calibrated_core)
        self.assertEqual(self.window.source_manager.active_id, source_id)
        self.assertEqual(list(self.window.left_workspace.page_specs), pages)


if __name__ == "__main__":
    unittest.main()
