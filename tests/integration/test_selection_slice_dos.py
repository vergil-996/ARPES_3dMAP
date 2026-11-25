import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication, QPushButton

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.axis_interval import AxisInterval
from bandscope.core.crop_model import CropSelection
from bandscope.core.data_scope import DataScopeDescriptor
from bandscope.processing.compute_backends import ArrayLRUCache
from bandscope.ui.crop_controls import CropController
from tests.support.pages import make_workspace, page_spec
from tests.support.view_recipe import control_window, synthetic_npz


NX, NY, NE, NT = 4, 5, 6, 3
RAW = np.arange(NX * NY * NE * NT, dtype=np.float64).reshape(NX, NY, NE, NT) + 1
COORDS = {
    "X": np.arange(NX, dtype=np.float64),
    "Y": np.arange(NY, dtype=np.float64),
    "E": np.arange(NE, dtype=np.float64),
    "delay": np.asarray([0.0, 100.0, 300.0]),
}


def clone_coords():
    return {key: np.array(values, copy=True) for key, values in COORDS.items()}


def time_interval():
    return AxisInterval.from_indices(
        COORDS["delay"], 0, 0, locked=True, axis_key="delay"
    ).as_dict()


class SelectionSliceDosTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.workspace = make_workspace()
        self.addCleanup(self.workspace.deleteLater)
        self.analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer = self.analyzer
        analyzer.left_workspace = self.workspace
        analyzer.core = AnalyzerCore()
        analyzer.core.raw_data = RAW
        analyzer.core.coords = clone_coords()
        analyzer.core.coord_sources = {key: "file" for key in COORDS}
        analyzer.core.coord_units = {"X": "1/Å", "Y": "1/Å", "E": "eV", "delay": "fs"}
        analyzer.core.has_time_axis = True
        analyzer.original_raw_data = RAW
        analyzer.original_coords = clone_coords()
        analyzer.base_raw_data = RAW
        analyzer.base_coords = clone_coords()
        analyzer.global_denoise_methods = []
        analyzer.shared_denoised_raw_data = None
        analyzer.shared_denoise_signature = repr([])
        analyzer.shared_denoise_version = 0
        analyzer.data_scopes = {"full": DataScopeDescriptor.full(RAW.shape)}
        analyzer._scope_generation = 0
        analyzer._scope_data_cache = ArrayLRUCache()
        analyzer._scope_committed_signatures = {"full": repr([])}
        analyzer.rotation_angle = 0.0
        analyzer._computed_volume_cache = ArrayLRUCache()
        analyzer._rotation_cache = {}
        analyzer._axis_prefix_cache = None
        analyzer._second_derivative_volume_cache = None
        analyzer._render_context_page_id = None
        analyzer._render_exact_ready = True
        analyzer.home_slice_info = None
        analyzer.clip_ranges = None
        analyzer.precise_logical_bounds = None
        analyzer.last_synced_slice_texts = None
        analyzer.time_space = None
        analyzer.time_interval = None
        analyzer.plugin_session = None
        analyzer._show_message = Mock()
        analyzer._update_export_button_states = Mock()
        analyzer._seed_control_state_for_spec = analyzer._seed_time_interval_for_spec
        analyzer._persist_axis_integral_page_state = lambda spec=None: None

        existing = CropSelection("2d", ("Y", "E"), (0.5, 3.5, 0.0, 5.0))
        self.source = page_spec(
            "source",
            "YZ 切片",
            "axis_integral_crop",
            source_page_id="home",
            params={
                "axis_index": 0,
                "axis_name": "X轴",
                "low": 0,
                "up": 0,
                "mid": 0,
                "time_interval": time_interval(),
                "crop_base_rect": None,
                "crop_regions": [existing.to_dict()],
            },
        )
        self.workspace.add_page(self.source)
        analyzer.active_page_spec = self.source
        analyzer.last_visual_page_id = self.source.page_id
        analyzer.current_render_context = analyzer._compute_render_context(self.source)
        analyzer._render_context_page_id = self.source.page_id
        analyzer.crop_controller = CropController(self.workspace)
        analyzer.crop_controller.activate(
            self.source.page_id, analyzer.current_render_context
        )
        self.active_id = analyzer.crop_controller.active_selection_id
        self.active = CropSelection("2d", ("Y", "E"), (1.2, 2.8, 1.1, 4.2))
        analyzer.crop_controller.update_selection(self.active_id, self.active)
        self.persisted = []

        def persist_normal_range(spec=None):
            target = spec or self.source
            self.persisted.append(target.page_id)
            target.params.update(low=1, up=3, mid=2)

        analyzer._persist_axis_integral_page_state = persist_normal_range

    def test_tool_freezes_active_selection_and_resolved_normal_range_per_delay(self):
        self.assertEqual(
            self.analyzer.current_render_context["slice_info"]["range"], (0, 0)
        )

        page_id = self.analyzer._create_slice_dos_result_from_selections()

        self.assertIsNotNone(page_id)
        created = self.workspace.page_by_id(page_id)
        self.assertEqual(created.source_page_id, self.source.page_id)
        self.assertIn(self.source.page_id, self.persisted)
        source = created.params["slice_source"]
        self.assertEqual(source["context"]["slice_info"]["range"], (1, 3))
        self.assertEqual(len(source["crop_regions"]), 2)
        self.assertEqual(source["crop_regions"][-1], self.active.to_dict())
        expected = np.sum(RAW[1:4, 1:4, 1:6, :], axis=(0, 1, 2))
        np.testing.assert_allclose(
            self.analyzer._compute_slice_dos_for_spec(created, RAW, COORDS), expected
        )

        self.analyzer.crop_controller.update_selection(
            self.active_id, CropSelection("2d", ("Y", "E"), (0.0, 1.0, 0.0, 1.0))
        )
        self.source.params.update(low=0, up=0)
        self.source.params["crop_regions"].clear()
        np.testing.assert_allclose(
            self.analyzer._compute_slice_dos_for_spec(created, RAW, COORDS), expected
        )

    def test_axis_page_without_an_active_selection_never_integrates_the_full_image(self):
        self.analyzer.crop_controller.selections[self.source.page_id].clear()
        self.analyzer.crop_controller.selection = None

        self.assertIsNone(self.analyzer._create_slice_dos_result_from_selections())
        self.assertNotIn("slice_dos", {
            spec.page_kind for spec in self.workspace.page_specs.values()
        })
        message = self.analyzer._show_message.call_args.args[1]
        self.assertIn("有效框选范围", message)

    def test_wrong_axis_or_disjoint_active_selection_is_rejected(self):
        wrong_axes = CropSelection("2d", ("X", "Y"), (0.0, 2.0, 0.0, 2.0))
        self.analyzer.crop_controller.update_selection(self.active_id, wrong_axes)
        self.assertIsNone(self.analyzer._build_slice_dos_spec())

        disjoint = CropSelection("2d", ("Y", "E"), (100.0, 200.0, 100.0, 200.0))
        self.analyzer.crop_controller.update_selection(self.active_id, disjoint)
        self.assertIsNone(self.analyzer._build_slice_dos_spec())
        self.assertEqual(self.analyzer._show_message.call_count, 2)


class SelectionSliceDosWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_real_toolbar_button_uses_the_selection_and_current_normal_interval(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.npz"
            data = synthetic_npz(path, seed=12)
            window = control_window()
            self.addCleanup(window.close)
            self.assertTrue(window.load_data(str(path)))
            identity = window.core.source_identity
            if identity is not None and identity.future is not None:
                identity.future.result(timeout=30)
            source = window.left_workspace.current_spec()
            axis_page = window._build_canvas_axis_slice_spec(source, 0)
            window.left_workspace.add_page(axis_page)
            window.canvas_tool_overlay.selection_button.setChecked(True)
            controller = window.crop_controller
            selection_id = controller.active_selection_id
            self.assertIsNotNone(selection_id)
            coords = window.core.coords
            selection = CropSelection(
                "2d",
                ("Y", "E"),
                (
                    float(coords["Y"][1]), float(coords["Y"][3]),
                    float(coords["E"][1]), float(coords["E"][4]),
                ),
            )
            controller.update_selection(selection_id, selection)
            window.axis_interval.set_low(float(coords["X"][1]))
            window.axis_interval.set_up(float(coords["X"][3]))
            window.axis_interval_controller.sync_widgets()
            window._update_export_button_states()

            self.assertTrue(window.canvas_tool_overlay.slice_dos_button.isEnabled())
            window.canvas_tool_overlay.slice_dos_button.click()

            created = window.left_workspace.current_spec()
            self.assertEqual(created.page_kind, "slice_dos")
            self.assertEqual(created.source_page_id, axis_page.page_id)
            frozen = created.params["slice_source"]
            self.assertEqual(frozen["context"]["slice_info"]["range"], (1, 3))
            frozen_selection = frozen["crop_regions"][-1]
            self.assertEqual(frozen_selection["axes"], selection.axes)
            self.assertEqual(frozen_selection["operation"], "crop")
            np.testing.assert_allclose(frozen_selection["bounds"], selection.bounds)
            expected = np.sum(data[1:4, 1:4, 1:5, :], axis=(0, 1, 2))
            np.testing.assert_allclose(
                window._compute_slice_dos_for_spec(created, data, coords), expected,
                rtol=1e-6, atol=1e-6,
            )


class SelectionToolButtonStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer = self.analyzer
        analyzer.base_raw_data = RAW
        analyzer.core = AnalyzerCore()
        analyzer.core.raw_data = RAW
        analyzer.core.has_time_axis = True
        analyzer._render_exact_ready = True
        self.spec = page_spec("home", "主页", "home")
        analyzer.left_workspace = type(
            "Workspace", (), {"current_spec": lambda _self: self.spec}
        )()
        analyzer.btn_tb_export = QPushButton()
        analyzer.btn_tb_shot = QPushButton()
        analyzer.btn_tb_crop = QPushButton()
        analyzer.btn_tb_erase = QPushButton()
        self.slice_button = QPushButton()
        self.edc_button = QPushButton()
        analyzer.canvas_tool_overlay = type(
            "Overlay",
            (),
            {
                "selection_button": QPushButton(),
                "slice_dos_button": self.slice_button,
                "edc_button": self.edc_button,
            },
        )()

    def test_edc_is_enabled_for_home_and_all_axis_slices(self):
        for axis in (0, 1, 2):
            with self.subTest(axis=axis):
                self.spec = page_spec(
                    f"axis-{axis}",
                    "切片",
                    "axis_integral",
                    params={"axis_index": axis, "low": 0, "up": 1},
                )
                self.analyzer._update_export_button_states()
                self.assertTrue(self.edc_button.isEnabled())
                self.assertTrue(self.slice_button.isEnabled())

        self.spec = page_spec("curve", "曲线", "energy_dos")
        self.analyzer._update_export_button_states()
        self.assertFalse(self.edc_button.isEnabled())
        self.assertFalse(self.slice_button.isEnabled())

    def test_slice_dos_requires_a_dynamic_legal_source(self):
        self.spec = page_spec("home", "主页", "home")
        self.analyzer._update_export_button_states()
        self.assertTrue(self.edc_button.isEnabled())
        self.assertFalse(self.slice_button.isEnabled())

        self.spec = page_spec(
            "xy",
            "XY",
            "home",
            params={"home_slice_info": {"axis": 2, "index": 1}},
        )
        self.analyzer._update_export_button_states()
        self.assertTrue(self.slice_button.isEnabled())
        self.analyzer.core.has_time_axis = False
        self.analyzer._update_export_button_states()
        self.assertFalse(self.slice_button.isEnabled())
        self.assertTrue(self.edc_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
