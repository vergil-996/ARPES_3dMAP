import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication, QMenu, QStackedWidget, QWidget

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.axis_interval import AxisInterval
from bandscope.core.crop_model import CropSelection
from bandscope.core.data_scope import DataScopeDescriptor
from bandscope.processing.compute_backends import ArrayLRUCache
from bandscope.ui.crop_controls import CropController
from tests.support.pages import make_workspace, page_spec


NX, NY, NE, NT = 5, 5, 7, 3
RAW = np.arange(NX * NY * NE * NT, dtype=np.float32).reshape(NX, NY, NE, NT) + 1
COORDS = {
    "X": np.linspace(-1.0, 1.0, NX),
    "Y": np.linspace(-1.0, 1.0, NY),
    "E": np.linspace(-1.0, 1.0, NE),
    "delay": np.asarray([0.0, 1.0, 2.0]),
}


def clone_coords():
    return {key: np.array(values, copy=True) for key, values in COORDS.items()}


def time_interval(low=0, up=1):
    return AxisInterval.from_indices(
        COORDS["delay"], low, up, locked=low == up, axis_key="delay"
    ).as_dict()


def integral_params(axis_index=0, **overrides):
    params = {
        "axis_index": axis_index,
        "axis_name": ("X轴", "Y轴", "Z轴")[axis_index],
        "low": 1,
        "up": 2,
        "mid": 1,
        "time_interval": time_interval(),
    }
    params.update(overrides)
    return params


class IntegrationContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.workspace = make_workspace()
        self.addCleanup(self.workspace.deleteLater)
        self.analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer = self.analyzer
        analyzer.left_workspace = self.workspace
        analyzer.core = self.make_core()
        analyzer.original_raw_data = RAW
        analyzer.original_coords = clone_coords()
        analyzer.base_raw_data = RAW
        analyzer.base_coords = clone_coords()
        analyzer.global_denoise_methods = []
        analyzer.shared_denoised_raw_data = None
        analyzer.shared_denoise_signature = repr([])
        analyzer.data_scopes = {"full": DataScopeDescriptor.full(RAW.shape)}
        analyzer._scope_generation = 0
        analyzer._scope_data_cache = ArrayLRUCache()
        analyzer._scope_committed_signatures = {"full": repr([])}
        analyzer.rotation_angle = 0.0
        analyzer._computed_volume_cache = ArrayLRUCache()
        analyzer._rotation_cache = {}
        analyzer._axis_prefix_cache = None
        analyzer._second_derivative_volume_cache = None
        analyzer.current_render_context = None
        analyzer._render_context_page_id = None
        analyzer._render_exact_ready = True
        analyzer.home_slice_info = None
        analyzer.clip_ranges = None
        analyzer.precise_logical_bounds = None
        analyzer.last_synced_slice_texts = None
        analyzer.time_space = None
        analyzer.time_interval = None
        analyzer.plugin_session = None
        analyzer.page_control_blank = SimpleNamespace(
            get_second_derivative_params=lambda: {"threshold": 0.0}
        )
        analyzer._persist_axis_integral_page_state = lambda spec=None: None
        analyzer._seed_second_derivative_axis_control_state = lambda spec: None
        analyzer._seed_control_state_for_spec = analyzer._seed_time_interval_for_spec
        analyzer._ask_second_derivative_axis = Mock(return_value=2)
        analyzer._show_message = Mock()
        analyzer._update_export_button_states = Mock()
        analyzer.active_page_spec = self.workspace.current_spec()
        analyzer.last_visual_page_id = self.workspace.current_page_id
        self.workspace.page_activated.connect(self._sync_active_page)

    @staticmethod
    def make_core(*, dynamic=True):
        core = AnalyzerCore()
        core.raw_data = RAW
        core.coords = clone_coords()
        core.coord_sources = {key: "file" for key in COORDS}
        core.coord_units = {"X": "1/Å", "Y": "1/Å", "E": "eV", "delay": "fs"}
        core.has_time_axis = bool(dynamic)
        return core

    def _sync_active_page(self, page_id):
        spec = self.workspace.page_by_id(page_id)
        self.analyzer.active_page_spec = spec
        self.analyzer.last_visual_page_id = page_id
        if spec is not None and spec.page_kind == "home":
            self.analyzer.home_slice_info = copy.deepcopy(spec.params.get("home_slice_info"))
            self.analyzer.clip_ranges = copy.deepcopy(spec.params.get("clip_ranges"))

    def add_page(self, page_id, kind, *, parent="home", params=None, activate=True):
        spec = page_spec(
            page_id,
            page_id,
            kind,
            source_page_id=parent,
            params=copy.deepcopy(params or {}),
        )
        if activate:
            self.workspace.add_page(spec)
        else:
            self.workspace.add_page_inactive(spec)
        return spec

    def action_keys(self, parent_id):
        return [item[0] for item in self.analyzer._integration_actions_for_parent(parent_id)]

    @staticmethod
    def analysis_submenu(menu):
        return next(
            (
                action.menu()
                for action in menu.actions()
                if action.text() == "处理分析"
            ),
            None,
        )

    def test_view_menu_lists_the_current_pages_legal_actions(self):
        menu = QMenu()
        self.addCleanup(menu.deleteLater)

        self.assertTrue(self.analyzer._append_analysis_actions(menu))

        submenu = self.analysis_submenu(menu)
        self.assertIsNotNone(submenu)
        self.assertEqual(
            [action.text() for action in submenu.actions()],
            ["能级态密度", "二阶导"],
        )

    def test_view_menu_trigger_uses_the_current_page_and_stable_key(self):
        menu = QMenu()
        self.addCleanup(menu.deleteLater)
        self.analyzer._on_page_create_requested = Mock(return_value=None)

        self.analyzer._append_analysis_actions(menu)
        self.analysis_submenu(menu).actions()[0].trigger()

        self.analyzer._on_page_create_requested.assert_called_once_with(
            "home", "builtin:energy_dos"
        )

    def test_view_menu_hides_itself_for_an_illegal_page(self):
        self.add_page("dos", "energy_dos")
        menu = QMenu()
        self.addCleanup(menu.deleteLater)

        self.assertFalse(self.analyzer._append_analysis_actions(menu))
        self.assertEqual(menu.actions(), [])

    def test_view_menu_includes_ready_plugin_actions(self):
        self.analyzer.plugin_session = SimpleNamespace(
            can_freeze_source=Mock(return_value=True),
            integration_actions=Mock(
                return_value=[("edc_waterfall:create", "EDC瀑布图", 20.0)]
            ),
        )
        self.add_page("x", "axis_integral", params=integral_params(0))
        menu = QMenu()
        self.addCleanup(menu.deleteLater)

        self.assertTrue(self.analyzer._append_analysis_actions(menu))

        self.assertEqual(
            [action.text() for action in self.analysis_submenu(menu).actions()],
            ["切片内强度积分", "能级态密度", "EDC瀑布图", "二阶导"],
        )

    def test_2d_context_menu_adds_the_analysis_entry(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.current_render_context = {"view": "2d"}
        analyzer.canvas_2d = QWidget()
        self.addCleanup(analyzer.canvas_2d.deleteLater)
        analyzer._can_copy_current_curve = Mock(return_value=False)
        analyzer._can_paste_curve_to_current_page = Mock(return_value=False)
        for name in (
            "_append_band_actions",
            "_append_enhancement_actions",
            "_append_axis_title_actions",
            "_append_analysis_actions",
        ):
            setattr(analyzer, name, Mock())

        with patch.object(QMenu, "exec_"):
            My3DAnalyzer._show_curve_context_menu(analyzer, QPoint(1, 1))

        analyzer._append_analysis_actions.assert_called_once()

    def test_3d_context_menu_adds_the_analysis_entry(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.current_render_context = {"view": "3d"}
        analyzer.plotter = QWidget()
        self.addCleanup(analyzer.plotter.deleteLater)
        analyzer._three_d_band_target_at_cursor = Mock(return_value=None)
        analyzer._enhancement_box_region_at_cursor = Mock(return_value=None)
        for name in (
            "_append_band_actions",
            "_append_enhancement_actions",
            "_append_axis_title_actions",
            "_append_recipe_actions",
            "_append_analysis_actions",
        ):
            setattr(analyzer, name, Mock())

        with patch.object(QMenu, "exec_"):
            My3DAnalyzer._show_main_context_menu(analyzer, QPoint(1, 1))

        analyzer._append_analysis_actions.assert_called_once()

    def test_2d_crop_mode_reopens_the_compact_context_menu(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.current_render_context = {"view": "2d"}
        analyzer.canvas_2d = QWidget()
        self.addCleanup(analyzer.canvas_2d.deleteLater)
        analyzer._crop_enabled = Mock(return_value=True)
        analyzer._show_crop_popup = Mock()
        analyzer._show_axis_title_menu = Mock()

        My3DAnalyzer._show_curve_context_menu(analyzer, QPoint(1, 1))

        analyzer._show_crop_popup.assert_called_once()
        analyzer._show_axis_title_menu.assert_called_once()

    def test_crop_mode_menu_includes_analysis_actions(self):
        analyzer = self.analyzer
        analyzer.current_render_context = {"view": "2d"}
        analyzer._append_axis_title_actions = Mock(return_value=False)
        analyzer._append_enhancement_actions = Mock(return_value=False)
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        captured = []

        def execute(menu, _pos):
            captured.append(menu)

        with patch.object(QMenu, "exec_", new=execute):
            self.assertTrue(analyzer._show_axis_title_menu(owner, QPoint(2, 3)))

        self.assertEqual(len(captured), 1)
        self.assertEqual(
            [action.text() for action in captured[0].actions()], ["处理分析"]
        )

    def test_builtin_action_legality_matrix(self):
        self.assertEqual(
            self.action_keys("home"),
            ["builtin:energy_dos", "builtin:second_derivative"],
        )
        self.add_page("x", "axis_integral", params=integral_params(0))
        self.assertEqual(
            self.action_keys("x"),
            ["builtin:slice_dos", "builtin:energy_dos", "builtin:second_derivative"],
        )
        self.add_page("e", "axis_integral", params=integral_params(2))
        self.assertEqual(self.action_keys("e"), ["builtin:slice_dos"])
        self.add_page(
            "z-crop",
            "axis_integral_crop",
            params=integral_params(
                2, crop_k_low=0, crop_k_up=2, crop_e_low=1, crop_e_up=3
            ),
        )
        self.assertEqual(self.action_keys("z-crop"), ["builtin:slice_dos"])
        self.add_page(
            "e-home",
            "home",
            params={"home_slice_info": {"axis": 2, "index": 2}},
        )
        self.assertEqual(
            self.action_keys("e-home"), ["builtin:slice_dos", "builtin:energy_dos"]
        )
        self.add_page("result", "energy_dos")
        self.assertEqual(self.action_keys("result"), [])
        self.add_page(
            "incomplete-crop",
            "axis_integral_crop",
            params=integral_params(2, crop_k_low=0, crop_k_up=2),
        )
        self.assertEqual(self.action_keys("incomplete-crop"), ["builtin:slice_dos"])

    def test_slice_dos_uses_the_nonactive_parent_and_freezes_its_slice(self):
        self.add_page(
            "parent-a",
            "home",
            params={"home_slice_info": {"axis": 0, "index": 0}},
        )
        self.add_page(
            "parent-b",
            "home",
            params={"home_slice_info": {"axis": 0, "index": 2}},
        )
        self.workspace.activate_page("parent-a")

        page_id = self.analyzer._on_page_create_requested(
            "parent-b", "builtin:slice_dos"
        )

        self.assertIsNotNone(page_id)
        created = self.workspace.page_by_id(page_id)
        self.assertEqual(self.workspace.current_page_id, page_id)
        self.assertEqual(created.source_page_id, "parent-b")
        frozen = created.params["slice_source"]["context"]
        self.assertEqual(frozen["slice_info"]["axis"], 0)
        self.assertEqual(frozen["slice_info"]["index"], 2)
        actual = self.analyzer._compute_slice_dos_for_spec(created, RAW, COORDS)
        np.testing.assert_allclose(actual, np.sum(RAW[2], axis=(0, 1)))

    def test_energy_dos_preserves_parent_time_interval_and_crop(self):
        params = integral_params(
            0,
            crop_k_low=1,
            crop_k_up=3,
            crop_e_low=2,
            crop_e_up=5,
            time_interval=time_interval(0, 1),
        )
        self.add_page("x-crop", "axis_integral_crop", params=params)

        page_id = self.analyzer._on_page_create_requested(
            "x-crop", "builtin:energy_dos"
        )

        created = self.workspace.page_by_id(page_id)
        self.assertEqual(created.page_kind, "energy_dos")
        self.assertEqual(created.source_page_id, "x-crop")
        self.assertEqual(created.params["source_page_kind"], "axis_integral")
        self.assertEqual(created.params["time_interval"], time_interval(0, 1))
        self.assertEqual(
            [created.params[key] for key in (
                "crop_k_low", "crop_k_up", "crop_e_low", "crop_e_up"
            )],
            [1, 3, 2, 5],
        )

    def test_edc_curve_uses_the_selection_tool_and_its_time_frame(self):
        params = integral_params(
            2,
            crop_k_low=0,
            crop_k_up=2,
            crop_e_low=1,
            crop_e_up=3,
            time_interval=time_interval(1, 1),
        )
        source = self.add_page("z-crop", "axis_integral_crop", params=params)
        self.analyzer.shared_denoise_version = 0
        self.analyzer.current_render_context = self.analyzer._compute_render_context(source)
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        self.analyzer.crop_controller = CropController(parent)
        self.analyzer.crop_controller.activate(
            source.page_id, self.analyzer.current_render_context
        )

        page_id = self.analyzer._create_edc_result_from_selections()

        created = self.workspace.page_by_id(page_id)
        self.assertEqual(created.page_kind, "edc_curve")
        self.assertEqual(created.source_page_id, "z-crop")
        self.assertEqual(created.params["time_interval"], time_interval(1, 1))
        context = self.analyzer._get_edc_curve_context(created, None, None)
        np.testing.assert_allclose(context["y_data"], np.sum(RAW[0:3, 1:4, :, 1], axis=(0, 1)))
        self.assertEqual(context["source_t_indices"], (1, 1))
        self.assertNotIn("builtin:edc_curve", self.action_keys("z-crop"))
        self.assertIsNone(
            self.analyzer._on_page_create_requested("z-crop", "builtin:edc_curve")
        )

    def test_second_derivative_rebuilds_a_cropped_nonactive_home_context(self):
        regions = [
            CropSelection(
                "3d", ("X", "Y", "E"), (0.0, 0.0, -1.0, 1.0, -1.0, 1.0)
            ).to_dict(),
            CropSelection(
                "2d", ("Y", "E"), (-0.5, 0.5, -0.5, 0.5)
            ).to_dict(),
        ]
        self.add_page(
            "cropped-b",
            "home",
            params={"home_slice_info": None, "crop_regions": regions},
        )
        self.add_page(
            "parent-a",
            "home",
            params={"home_slice_info": {"axis": 1, "index": 0}},
        )
        self.analyzer.current_render_context = {
            "view": "2d",
            "slice_info": {"axis": 1, "index": 0},
        }
        self.analyzer._render_context_page_id = "parent-a"

        page_id = self.analyzer._on_page_create_requested(
            "cropped-b", "builtin:second_derivative"
        )

        created = self.workspace.page_by_id(page_id)
        self.assertEqual(created.source_page_id, "cropped-b")
        self.assertEqual(created.params["source_view"], "2d")
        self.assertEqual(created.params["slice_axis"], 0)
        self.assertEqual(created.params["slice_index"], 2)
        self.assertEqual(len(created.params["crop_regions"]), 1)
        self.assertEqual(created.params["crop_regions"][0]["view"], "2d")
        self.analyzer._ask_second_derivative_axis.assert_not_called()

    def test_static_data_hides_and_rejects_slice_dos_only(self):
        self.analyzer.core.has_time_axis = False
        self.add_page(
            "slice-home",
            "home",
            params={"home_slice_info": {"axis": 0, "index": 1}},
        )
        self.assertEqual(
            self.action_keys("slice-home"),
            ["builtin:energy_dos", "builtin:second_derivative"],
        )
        before = set(self.workspace.page_specs)
        self.assertIsNone(
            self.analyzer._on_page_create_requested("slice-home", "builtin:slice_dos")
        )
        self.assertEqual(set(self.workspace.page_specs), before)

    def test_illegal_deleted_and_stale_requests_never_fall_back(self):
        self.add_page("illegal", "energy_dos")
        before = set(self.workspace.page_specs)
        self.assertIsNone(
            self.analyzer._on_page_create_requested("illegal", "builtin:energy_dos")
        )
        self.assertEqual(set(self.workspace.page_specs), before)
        self.assertEqual(self.workspace.current_page_id, "illegal")

        self.assertIsNone(
            self.analyzer._on_page_create_requested("home", "edc_waterfall:create")
        )
        self.assertEqual(set(self.workspace.page_specs), before)

        current = self.workspace.current_page_id
        self.assertIsNone(
            self.analyzer._on_page_create_requested("deleted", "builtin:energy_dos")
        )
        self.assertEqual(self.workspace.current_page_id, current)
        self.assertEqual(set(self.workspace.page_specs), before)

    def test_cancelling_second_derivative_direction_creates_no_page(self):
        self.analyzer._ask_second_derivative_axis.return_value = None
        before = set(self.workspace.page_specs)
        self.assertIsNone(
            self.analyzer._on_page_create_requested("home", "builtin:second_derivative")
        )
        self.assertEqual(set(self.workspace.page_specs), before)
        self.assertEqual(self.workspace.current_page_id, "home")
        self.analyzer._ask_second_derivative_axis.assert_called_once_with()

    def test_plugin_action_is_sorted_and_dispatched_with_stable_ids(self):
        session = SimpleNamespace(
            can_freeze_source=Mock(return_value=True),
            integration_actions=Mock(
                return_value=[("edc_waterfall:create", "EDC瀑布图", 20.0)]
            ),
        )

        def invoke(plugin_id, action_id):
            current = self.workspace.current_spec()
            created = page_spec(
                "plugin-result",
                "EDC瀑布图",
                "plugin_derived",
                source_page_id=current.page_id,
                params={"plugin": plugin_id, "action": action_id},
            )
            self.workspace.add_page(created)
            return created.page_id

        session.invoke_integration_action = Mock(side_effect=invoke)
        self.analyzer.plugin_session = session
        self.add_page("x", "axis_integral", params=integral_params(0))
        self.assertEqual(
            self.action_keys("x"),
            [
                "builtin:slice_dos",
                "builtin:energy_dos",
                "edc_waterfall:create",
                "builtin:second_derivative",
            ],
        )

        page_id = self.analyzer._on_page_create_requested("x", "edc_waterfall:create")

        self.assertEqual(page_id, "plugin-result")
        self.assertEqual(self.workspace.page_by_id(page_id).source_page_id, "x")
        session.invoke_integration_action.assert_called_once_with("edc_waterfall", "create")

    def test_group_copy_uses_group_activation_route(self):
        self.add_page("x", "axis_integral", params=integral_params(0))
        group = self.workspace.create_group("组")
        copied = self.workspace.copy_page_to_group("x", group.group_id)
        self.workspace.activate_page("home")
        original = self.analyzer._on_group_page_activation_requested
        activations = []

        def activate(page_id):
            activations.append(page_id)
            original(page_id)

        self.analyzer._on_group_page_activation_requested = activate
        page_id = self.analyzer._on_page_create_requested(
            copied.page_id, "builtin:energy_dos"
        )

        self.assertEqual(activations, [copied.page_id])
        self.assertEqual(self.workspace.page_by_id(page_id).source_page_id, copied.page_id)

    def test_cross_source_group_query_uses_the_target_sources_time_axis(self):
        dynamic_core = self.make_core(dynamic=True)
        self.analyzer.core = self.make_core(dynamic=False)
        self.add_page(
            "dynamic-slice",
            "home",
            params={"home_slice_info": {"axis": 0, "index": 1}},
        )
        group = self.workspace.create_group("组")
        copied = self.workspace.copy_page_to_group("dynamic-slice", group.group_id)
        self.workspace.activate_page("home")

        class Manager:
            active_id = "static"
            session = SimpleNamespace(state={}, target_page_id=None)

            @staticmethod
            def owner_of(page_id):
                return "dynamic" if page_id == copied.page_id else "static"

            def is_active(self, owner):
                return owner == self.active_id

            def get(self, owner):
                return self.session if owner == "dynamic" else None

            @staticmethod
            def is_closing(owner):
                return False

        manager = Manager()
        manager.session.state["core"] = dynamic_core
        self.analyzer.source_manager = manager

        def activate_source(owner):
            manager.active_id = owner
            self.analyzer.core = dynamic_core
            self.workspace.activate_page(copied.page_id)
            return True

        self.analyzer.activate_source = activate_source
        self.assertIn("builtin:slice_dos", self.action_keys(copied.page_id))
        page_id = self.analyzer._on_page_create_requested(
            copied.page_id, "builtin:slice_dos"
        )
        self.assertIsNotNone(page_id)
        self.assertEqual(self.workspace.page_by_id(page_id).source_page_id, copied.page_id)

    def test_ctrl_2_is_not_registered(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.left_workspace = SimpleNamespace(close_current_page=Mock())
        registered = []

        class Shortcut:
            def __init__(self, key):
                self.key = key
                self.activated = SimpleNamespace(connect=lambda handler: None)

            def setContext(self, context):
                pass

        def bind(key, parent):
            shortcut = Shortcut(key.toString())
            registered.append(shortcut.key)
            return shortcut

        with patch("bandscope.app.refactored_app.QShortcut", side_effect=bind):
            analyzer._install_global_shortcuts()
        self.assertIn("Ctrl+1", registered)
        self.assertNotIn("Ctrl+2", registered)


class LegacyControlStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_old_tab_and_combo_state_are_ignored_without_creating_a_page(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.page_container = QStackedWidget()
        analyzer.page_container.addWidget(QWidget())
        self.addCleanup(analyzer.page_container.deleteLater)
        analyzer.timeline_bar = SimpleNamespace(
            export_state=Mock(return_value={}),
            restore_state=Mock(),
            get_rotation_angle=lambda: 0.0,
        )
        analyzer.page_render = SimpleNamespace(
            export_state=Mock(return_value={}),
            restore_state=Mock(),
            get_denoise_settings=lambda: [],
            denoise_pipeline=SimpleNamespace(export_state=Mock(return_value={"nodes": []})),
            set_denoise_execution_state=Mock(),
        )
        analyzer.page_control_blank = SimpleNamespace(
            export_state=Mock(return_value={}), restore_state=Mock()
        )
        analyzer.original_raw_data = None
        analyzer._enhancement_export_render_state = lambda state: state
        analyzer._ensure_page_display_state = lambda spec: None
        analyzer._restore_enhancement_style = lambda: None
        analyzer._preserve_control_tab_on_next_page_sync = False
        analyzer._syncing_controls = False
        analyzer.rotation_angle = 0.0
        analyzer.plugin_session = None
        analyzer._on_page_create_requested = Mock()
        old_state = {
            "active_control_tab": 1,
            "image": {},
            "render": {},
            "denoise_detail": {},
            "data_process": {
                "combo_other": {"key": "builtin:second_derivative", "index": 3}
            },
        }
        spec = page_spec("legacy", "legacy", "home", params={"control_state": old_state})
        analyzer.active_page_spec = spec
        analyzer.left_workspace = SimpleNamespace(current_spec=lambda: spec)

        captured = analyzer._capture_control_state()
        self.assertNotIn("data_process", captured)
        self.assertNotIn("combo_other", captured)
        analyzer._restore_page_ui_state(spec)

        self.assertEqual(analyzer.page_container.count(), 1)
        self.assertEqual(analyzer.page_container.currentIndex(), 0)
        analyzer._on_page_create_requested.assert_not_called()
        self.assertEqual(spec.params["control_state"], old_state)


if __name__ == "__main__":
    unittest.main()
