import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PyQt5.QtWidgets import QApplication, QDialog

from bandscope.core.axis_interval import CONTINUOUS_DOCKING, PAN_FRAMES
from bandscope.core.view_recipe import ViewRecipeDocument
from bandscope.exporting.publication_export import capture_snapshot, render_and_save
from bandscope.exporting.publication_models import OutputOptions, resolve_style
from bandscope.ui.axis_assignment_dialog import AxisAssignmentDialog
from bandscope.ui.result_workspace import AnalysisPageSpec
from tests.support.view_recipe import control_window


class AxisAssignmentIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "temperature.npz"
        self.raw = np.arange(2 * 3 * 4 * 5, dtype=np.float32).reshape(2, 3, 4, 5)
        np.savez(
            self.path,
            sample=self.raw,
            kx=np.arange(4),
            ky=np.arange(5),
            E=np.arange(3),
            time=np.asarray([-1.0, 2.0]),
            axis_order=np.asarray(["T", "E", "X", "Y"]),
        )
        self.window = control_window()
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

    def load(self):
        self.assertTrue(self.window.load_data(str(self.path)))
        return self.raw.transpose(2, 3, 1, 0)

    def make_temperature(self):
        np.savez(
            self.path,
            sample=self.raw,
            kx=np.arange(4),
            ky=np.arange(5),
            E=np.arange(3),
            temperature=np.asarray([300.0, 100.0]),
            temperature_unit=np.asarray(["K"]),
            axis_order=np.asarray(["T", "E", "X", "Y"]),
        )
        self.assertTrue(self.window.load_data(str(self.path)))
        return self.raw.transpose(2, 3, 1, 0)

    def test_card_cancel_is_non_destructive_and_reports_new_source_mode(self):
        w = self.window
        self.load()
        original = w.core
        calls = []

        class CancelDialog:
            Accepted = QDialog.Accepted

            def __init__(self, *args, **kwargs):
                calls.append(kwargs.get("as_new_source"))

            def exec_(self):
                return QDialog.Rejected

        with patch("bandscope.ui.axis_assignment_dialog.AxisAssignmentDialog", CancelDialog):
            self.assertFalse(w.on_source_assignment_requested())
            self.assertIs(w.core, original)
            w.left_workspace.add_page(AnalysisPageSpec(
                w._make_page_id(), "分析", "energy_dos", "test",
                source_page_id=w.left_workspace.home_page_id, params={"t_index": 0},
            ))
            self.assertFalse(w.on_source_assignment_requested())
        self.assertEqual(calls, [False, True])
        self.assertIs(w.core, original)
        self.assertEqual(len(w.source_manager), 1)

    def test_slider_only_changes_keep_source_and_pages_when_analysis_exists(self):
        w = self.window
        self.load()
        source_id = w.source_manager.active_id
        original_core = w.core
        before_interval = w.time_interval.as_dict()
        w.left_workspace.add_page(AnalysisPageSpec(
            w._make_page_id(), "分析", "energy_dos", "test",
            source_page_id=w.left_workspace.home_page_id, params={"t_index": 0},
        ))

        def accept_slider_changes(dialog):
            dialog.pan_combo.setCurrentIndex(dialog.pan_combo.findData(PAN_FRAMES))
            dialog.docking_combo.setCurrentIndex(
                dialog.docking_combo.findData(CONTINUOUS_DOCKING)
            )
            return QDialog.Accepted

        with patch.object(AxisAssignmentDialog, "exec_", accept_slider_changes), patch.object(
            w, "apply_axis_assignment"
        ) as apply_assignment:
            self.assertTrue(w.on_source_assignment_requested())
        apply_assignment.assert_not_called()
        self.assertEqual(w.source_manager.active_id, source_id)
        self.assertEqual(len(w.source_manager), 1)
        self.assertIs(w.core, original_core)
        self.assertEqual(w.time_interval.as_dict(), before_interval)
        self.assertEqual(w.source_manager.active().fourth_axis_drag.docking, CONTINUOUS_DOCKING)
        self.assertEqual(w.source_manager.active().fourth_axis_drag.continuous_pan, PAN_FRAMES)

    def test_combined_axis_and_slider_changes_apply_to_the_new_source(self):
        w = self.window
        self.load()
        first_id = w.source_manager.active_id
        w.left_workspace.add_page(AnalysisPageSpec(
            w._make_page_id(), "分析", "energy_dos", "test",
            source_page_id=w.left_workspace.home_page_id, params={"t_index": 0},
        ))

        def accept_combined_changes(dialog):
            dialog.scan_kind.setCurrentIndex(dialog.scan_kind.findData("temperature"))
            dialog.docking_combo.setCurrentIndex(
                dialog.docking_combo.findData(CONTINUOUS_DOCKING)
            )
            return QDialog.Accepted

        with patch.object(AxisAssignmentDialog, "exec_", accept_combined_changes):
            self.assertTrue(w.on_source_assignment_requested())
        self.assertEqual(len(w.source_manager), 2)
        self.assertNotEqual(w.source_manager.active_id, first_id)
        self.assertEqual(w.core.scan_kind, "temperature")
        self.assertEqual(w.source_manager.active().fourth_axis_drag.docking, CONTINUOUS_DOCKING)
        self.assertEqual(w.source_manager.get(first_id).fourth_axis_drag.docking, "auto")

    def test_invalid_assignment_failure_keeps_current_workspace(self):
        w = self.window
        self.load()
        before = w.core
        before_pages = list(w.left_workspace.page_specs)
        invalid = before.axis_assignment.clone()
        invalid.axis_roles["Y"] = invalid.axis_roles["X"]
        with patch.object(w, "_show_message"):
            self.assertFalse(w.apply_axis_assignment(invalid))
        self.assertIs(w.core, before)
        self.assertEqual(list(w.left_workspace.page_specs), before_pages)

    def test_mid_apply_failure_restores_current_source_state(self):
        w = self.window
        canonical = self.load()
        original_core = w.core
        original_home = w.left_workspace.home_page_id
        original_prepare = w._prepare_new_source_state

        def fail_after_prepare(*args):
            original_prepare(*args)
            raise RuntimeError("simulated commit failure")

        updated = original_core.axis_assignment.clone()
        updated.scan_kind = "temperature"
        with patch.object(w, "_prepare_new_source_state", new=fail_after_prepare), patch.object(
            w, "_show_message"
        ):
            self.assertFalse(w.apply_axis_assignment(updated))
        self.assertIs(w.core, original_core)
        self.assertEqual(w.left_workspace.home_page_id, original_home)
        np.testing.assert_array_equal(w.core.raw_data, canonical)
        self.assertIs(w.source_manager.active().state["core"], original_core)

    def test_home_only_reassignment_updates_current_source_and_tracks_values(self):
        w = self.window
        canonical = self.load()
        session_id = w.source_manager.active_id
        updated = w.core.axis_assignment.clone()
        updated.axis_roles["X"], updated.axis_roles["Y"] = (
            updated.axis_roles["Y"], updated.axis_roles["X"]
        )
        updated.coord_keys["X"], updated.coord_keys["Y"] = (
            updated.coord_keys["Y"], updated.coord_keys["X"]
        )
        self.assertTrue(w.apply_axis_assignment(updated))
        self.assertEqual(w.source_manager.active_id, session_id)
        self.assertEqual(len(w.source_manager), 1)
        np.testing.assert_array_equal(w.core.raw_data, canonical.transpose(1, 0, 2, 3))
        self.assertTrue(np.shares_memory(w.core.raw_data, w.core.source_array))
        self.assertIn("Kx:维4", w.left_workspace.page_tree.source_meta.text())

    def test_reassignment_with_analysis_opens_new_source_and_preserves_old_pages(self):
        w = self.window
        canonical = self.load()
        first_id = w.source_manager.active_id
        analysis = AnalysisPageSpec(
            w._make_page_id(), "原分析", "energy_dos", "test",
            source_page_id=w.left_workspace.home_page_id, params={"t_index": 0},
        )
        w.left_workspace.add_page(analysis)
        updated = w.core.axis_assignment.clone()
        updated.scan_kind = "temperature"
        updated.scan_name = "温度"
        updated.coord_units["T"] = "K"
        self.assertTrue(w.apply_axis_assignment(updated))
        self.assertEqual(len(w.source_manager), 2)
        self.assertNotEqual(w.source_manager.active_id, first_id)
        old = w.source_manager.get(first_id)
        self.assertIn(analysis.page_id, old.page_ids)
        np.testing.assert_array_equal(old.state["core"].raw_data, canonical)
        self.assertTrue(w.activate_source(first_id))
        self.assertIn(analysis.page_id, w.left_workspace.page_specs)
        self.assertEqual(w.core.scan_kind, "time")

    def test_temperature_scan_workflow_curve_image_numeric_export_and_plugin(self):
        w = self.window
        canonical = self.make_temperature()
        self.assertEqual(w.time_space.label, "温度")
        self.assertEqual(w.time_space.unit, "K")
        self.assertEqual(w.timeline_bar.time_interval_control.title_label.text(), "温度")
        self.assertEqual(w.timeline_bar.slider_time.value(), 0)
        w.timeline_bar.slider_time.setValue(1)
        np.testing.assert_array_equal(w.current_render_context["data"], canonical[..., 1])

        interval = w.time_interval
        interval.set_locked(False)
        interval.set_low(100.0)
        interval.set_up(300.0)
        w.on_time_interval_changed()
        np.testing.assert_array_equal(w.current_render_context["data"], canonical.sum(axis=3))

        home = w.left_workspace.home_spec()
        w.clip_ranges = [0, 3, 0, 4, 0, 2]
        home.params["clip_ranges"] = list(w.clip_ranges)
        page_id = w._on_page_create_requested(home.page_id, "builtin:slice_dos")
        self.assertIsNotNone(page_id)
        w.left_workspace.activate_page(page_id)
        spec = w.left_workspace.page_by_id(page_id)
        context = w.current_render_context
        self.assertEqual(context["xlabel"], "温度 (K)")
        from bandscope.ui import theme
        w._style_1d_axes(context, ylabel="Intensity (a.u.)")
        self.assertEqual(tuple(w.ax_2d.xaxis.label.get_fontfamily()), tuple(theme.MPL_FONT_FAMILIES))
        np.testing.assert_allclose(context["y_data"], canonical.sum(axis=(0, 1, 2)))

        _title, _name, payload = w._build_slice_dos_export_payload(
            spec, w.core.raw_data, w.core.coords
        )
        self.assertNotIn("time", payload)
        self.assertEqual(payload["scan_type"][0], "temperature")
        self.assertEqual(payload["scan_name"][0], "温度")
        self.assertEqual(payload["scan_unit"][0], "K")
        np.testing.assert_allclose(payload["scan_range"], [100.0, 300.0])
        np.testing.assert_allclose(payload["intensity"], canonical.sum(axis=(0, 1, 2)))

        snapshot = capture_snapshot(w)
        self.assertEqual(snapshot.payload["xlabel"], "温度 (K)")
        image_path = self.root / "temperature.png"
        options = OutputOptions(dpi=80)
        render_and_save(
            snapshot, resolve_style("1d", None), {}, options, str(image_path)
        )
        self.assertGreater(image_path.stat().st_size, 0)

        plugin_context = w._plugin_session().build_context(w, context)
        self.assertEqual(plugin_context.scan_type, "temperature")
        self.assertEqual(plugin_context.scan_name, "温度")
        self.assertEqual(plugin_context.scan_unit, "K")
        self.assertEqual(plugin_context.scan_range, (100.0, 300.0))
        self.assertIn("温度", plugin_context.frame_label)

    def test_same_file_two_assignments_have_independent_recipe_roundtrips(self):
        w = self.window
        canonical = self.make_temperature()
        temperature_document = capture_snapshot(w).view_recipe
        updated = w.core.axis_assignment.clone()
        updated.scan_kind = "angle"
        updated.scan_name = "角度"
        updated.coord_units["T"] = "deg"
        self.assertTrue(w.apply_axis_assignment(updated))
        angle_document = capture_snapshot(w).view_recipe
        self.assertNotEqual(
            temperature_document["primary_source_id"], angle_document["primary_source_id"]
        )
        temperature_source = temperature_document["sources"][
            temperature_document["primary_source_id"]
        ]
        angle_source = angle_document["sources"][angle_document["primary_source_id"]]
        self.assertEqual(temperature_source["sha256"], angle_source["sha256"])

        temperature_path = self.root / "temperature.bsview.json"
        angle_path = self.root / "angle.bsview.json"
        temperature_path.write_text(
            ViewRecipeDocument(temperature_document).dumps(), encoding="utf-8"
        )
        angle_path.write_text(ViewRecipeDocument(angle_document).dumps(), encoding="utf-8")
        self.assertTrue(w._load_view_recipe(
            temperature_path, confirm=False, relocate=lambda _: str(self.path)
        ))
        self.assertEqual(w.core.scan_kind, "temperature")
        np.testing.assert_array_equal(w.core.raw_data, canonical)
        self.assertTrue(w._load_view_recipe(
            angle_path, confirm=False, relocate=lambda _: str(self.path)
        ))
        self.assertEqual(w.core.scan_kind, "angle")
        self.assertEqual(w.core.coord_units["delay"], "deg")
        np.testing.assert_array_equal(w.core.raw_data, canonical)


if __name__ == "__main__":
    unittest.main()
