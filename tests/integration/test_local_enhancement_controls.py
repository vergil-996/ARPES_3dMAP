import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from PyQt5.QtCore import QEvent, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMenu, QPushButton
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from bandscope.app.enhancement_integration import EnhancementInteractionMixin
from bandscope.core.local_enhancement import STATE_KEY, update_region_bounds
from bandscope.rendering.render_core import VisualEngine
from bandscope.ui import theme
from bandscope.ui.page_render_control import RenderControlPage
from tests.support.enhancement import context_2d, context_3d, region, state_2d


class Host(EnhancementInteractionMixin):
    def __init__(self):
        self.page_render = RenderControlPage()
        self.active_page_spec = SimpleNamespace(page_id="p1", params={STATE_KEY: state_2d()})
        self.left_workspace = SimpleNamespace(current_spec=lambda: self.active_page_spec)
        self.current_render_context = context_2d()
        self._syncing_controls = False
        self._enhancement_artists = []
        self._enhancement_actors = []
        self._enhancement_box_widget = None
        self.plotter = Mock(box_widgets=[])
        self.btn_tb_lock = QPushButton()
        self.btn_tb_lock.setCheckable(True)
        self._update_color_lock_button_style = Mock()
        self.request_refresh = Mock()
        self._draw_enhancement_regions = Mock()
        controls = self.page_render.enhancement_controls
        controls.target_changed.connect(self._select_enhancement_target)
        controls.delete_requested.connect(self._delete_enhancement_region)
        self._restore_enhancement_target_style()
        self._sync_enhancement_controls(self.active_page_spec, self.current_render_context)


class RegionControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply_siui_palette()

    def setUp(self):
        VisualEngine.unlock_data_range()
        self.host = Host()

    def tearDown(self):
        self.host._clear_enhancement_interaction()
        self.host.page_render.close()
        self.host.page_render.deleteLater()
        self.app.processEvents()
        VisualEngine.unlock_data_range()

    def test_switching_targets_preserves_independent_settings(self):
        host = self.host
        host.page_render.s_low.setValue(17)
        host.page_render.s_gamma.setValue(63)
        host._select_enhancement_target("2")
        self.assertEqual(host.page_render.s_low.value(), 0)
        host.page_render.s_up.setValue(74)
        host._select_enhancement_target("1")
        self.assertEqual(host.page_render.s_low.value(), 17)
        self.assertEqual(host.page_render.s_gamma.value(), 63)
        self.assertEqual(host.page_render.s_gamma._value_label.text(), "63")
        self.assertEqual(host.page_render.s_up.value(), 100)
        host._select_enhancement_target("")
        self.assertEqual(host.page_render.s_low.value(), 0)
        self.assertEqual(host.page_render.s_low._value_label.text(), "0")
        host._select_enhancement_target("2")
        self.assertEqual(host.page_render.s_up.value(), 74)

    def test_page_capture_stores_background_and_keeps_region_style_separate(self):
        host = self.host
        host.page_render.s_low.setValue(22)
        saved = host._enhancement_export_render_state(host.page_render.export_state())
        self.assertEqual(saved["s_low"]["value"], 0)
        self.assertEqual(host._enhancement_selected()["style"]["levels"][0], 22)

    def test_deletion_rejoins_background_and_preserves_other_settings(self):
        host = self.host
        state = host._enhancement_state()
        state["regions"][1]["style"]["levels"] = (13, 52, 91)
        self.assertEqual(host._enhancement_display(host.current_render_context)["background_level_info"]["data_min"], 0)
        host._delete_enhancement_region("1")
        self.assertEqual(state["selected_id"], "2")
        self.assertEqual(host.page_render.s_low.value(), 13)
        self.assertEqual(host._enhancement_display(host.current_render_context)["background_level_info"]["data_min"], 0)

    def test_last_deletion_restores_background_and_hides_panel_after_animation(self):
        host = self.host
        state = host._enhancement_state()
        state["background"]["levels"] = (9, 44, 95)
        host._delete_enhancement_region("1")
        host._delete_enhancement_region("2")
        host._sync_enhancement_controls(host.active_page_spec, host.current_render_context)
        QTest.qWait(260)
        self.assertTrue(host.page_render.enhancement_controls.isHidden())
        self.assertEqual(host.page_render.s_low.value(), 9)
        self.assertEqual(host.page_render.s_gamma.value(), 44)
        self.assertIsNone(host._enhancement_display(host.current_render_context))

    def test_deletion_invalidates_background_lock(self):
        VisualEngine._locked_data_range = (24, 35)
        self.host._delete_enhancement_region("1")
        self.assertIsNone(VisualEngine.locked_data_range())
        self.assertFalse(self.host.btn_tb_lock.isChecked())

    def test_dropdown_delete_is_a_row_action_and_background_cannot_be_deleted(self):
        host = self.host
        combo = host.page_render.enhancement_controls.combo
        combo.show()
        combo.showPopup()
        self.app.processEvents()
        index = combo.model().index(1, 0)
        rect = combo.view().visualRect(index)
        position = QPointF(rect.right() - 8, rect.center().y())
        for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            event = QMouseEvent(kind, position, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
            self.assertTrue(combo.eventFilter(combo.view().viewport(), event))
        self.assertEqual([r["region_id"] for r in host._enhancement_state()["regions"]], ["2"])
        host._sync_enhancement_controls(host.active_page_spec, host.current_render_context)
        self.assertEqual(combo.itemData(0), "")
        self.assertEqual(combo.count(), 2)
        combo.hidePopup()

    def test_common_style_does_not_destroy_independent_styles(self):
        host = self.host
        saved = copy.deepcopy(host._enhancement_state()["regions"])
        host._set_enhancement_common(True)
        host.page_render.s_gamma.setValue(71)
        host._commit_enhancement_controls()
        host._set_enhancement_common(False)
        self.assertEqual(host.page_render.s_gamma.value(), 50)
        self.assertEqual(host._enhancement_state()["regions"], saved)

    def test_page_isolation_and_selected_style_restore(self):
        host = self.host
        original = host.active_page_spec
        host.page_render.s_gamma.setValue(71)
        host._commit_enhancement_controls()
        host.active_page_spec = SimpleNamespace(page_id="p2", params={STATE_KEY: state_2d()})
        host.active_page_spec.params[STATE_KEY]["regions"][0]["enabled"] = False
        host._restore_enhancement_target_style()
        self.assertTrue(host._enhancement_selected()["enabled"])
        self.assertTrue(host._enhancement_display(host.current_render_context)["active"])
        self.assertEqual(host.page_render.s_gamma.value(), 50)
        host.active_page_spec = original
        host._restore_enhancement_target_style()
        self.assertEqual(host.page_render.s_gamma.value(), 71)

    def test_new_data_releases_regions_and_display_cache(self):
        host = self.host
        host._enhancement_display(host.current_render_context)
        self.assertIsNotNone(host._enhancement_cache)
        host._reset_enhancement_for_new_data()
        self.assertNotIn(STATE_KEY, host.active_page_spec.params)
        self.assertIsNone(host._enhancement_cache)
        self.assertTrue(host.page_render.enhancement_controls.isHidden())

    def _volume_source(self, *, locked=False):
        host = self.host
        state = host._enhancement_state()
        state["regions"] = [region(1, (1, 4, 1, 3, 1, 2), view="3d", axes=("X", "Y", "E"),
                                   style=dict(levels=(12., 63., 91.), opacity_mode="sigmoid"))]
        if locked:
            state["regions"][0]["locked_range"] = (29., 94.)
        host._restore_enhancement_target_style()
        return host.active_page_spec, copy.deepcopy(state)

    def test_actual_2d_page_creation_inherits_volume_regions_and_styles(self):
        from bandscope.app.refactored_app import My3DAnalyzer
        source, original = self._volume_source()
        source.page_kind = "home"
        host = self.host
        host._build_axis_request_params = lambda **_kw: dict(axis_name="E", low=0, up=3, source_page_kind="home")
        host._current_home_source_spec = lambda: source
        host._make_page_id = lambda: "derived"
        host._assign_scope_to_spec = Mock()
        host._capture_control_state = lambda: {}
        host._page_axis_source_mode = lambda _spec: "frame"
        host._store_control_state = lambda spec, state: spec.params.update(control_state=state)
        host._seed_control_state_for_spec = lambda spec: My3DAnalyzer._seed_control_state_for_spec(host, spec)
        host._integral_length = lambda low, up: up-low+1
        derived = My3DAnalyzer._build_axis_integral_spec(host)
        host.active_page_spec = derived
        context = context_3d()
        result = context_2d(context["data"].sum(axis=2))
        result["slice_info"] = dict(axis=2, mode="integral", range=(0, 3))
        host.current_render_context = result
        fields = host._enhancement_display(result)
        host._sync_enhancement_controls(derived, result)
        self.assertTrue(fields["active"])
        self.assertEqual(host.page_render.enhancement_controls.combo.count(), 2)
        self.assertEqual(host._enhancement_selected()["bounds"], (1, 4, 1, 3))
        self.assertEqual(fields["regions"][0]["style"], original["regions"][0]["style"])
        samples = result["data"][1:5, 1:4]
        self.assertEqual(fields["regions"][0]["data_range"], (samples.min(), samples.max()))
        np.testing.assert_array_equal(context["data"].sum(axis=2), result["data"])
        update_region_bounds(host._enhancement_selected(), (0, 2, 0, 2))
        host._delete_enhancement_region("1")
        self.assertEqual(source.params[STATE_KEY], original)

    def test_inherited_lock_uses_first_exact_2d_result_and_not_preview(self):
        source, original = self._volume_source(locked=True)
        derived = SimpleNamespace(page_id="derived", page_kind="axis_integral", source_page_id="p1", params={})
        host = self.host
        host._inherit_enhancement_state(derived)
        host.active_page_spec = derived
        preview = context_2d(np.arange(36).reshape(6, 6) * 100)
        preview["slice_info"]["index"] = 1
        host._enhancement_display(preview, freeze_pending=False)
        selected = host._enhancement_selected()
        self.assertTrue(selected["lock_pending"])
        self.assertNotIn("locked_range", selected)
        exact = context_2d(np.arange(36).reshape(6, 6) * 10)
        exact["slice_info"]["index"] = 1
        fields = host._enhancement_display(exact)
        self.assertEqual(selected["locked_range"], (70., 270.))
        exact["data"] *= 3
        self.assertEqual(host._enhancement_display(exact)["regions"][0]["data_range"], (70., 270.))
        self.assertEqual(source.params[STATE_KEY], original)

    def test_copied_crop_state_projects_and_nonintersecting_plane_has_no_overlay(self):
        source, original = self._volume_source()
        host = self.host
        derived = SimpleNamespace(page_id="crop", params=copy.deepcopy(source.params))
        host.active_page_spec = derived
        context = context_2d()
        self.assertIsNone(host._enhancement_display(context))
        context["slice_info"]["index"] = 2
        self.assertTrue(host._enhancement_display(context)["active"])
        self.assertEqual(source.params[STATE_KEY], original)

    def test_one_dimensional_results_do_not_inherit_regions(self):
        self._volume_source()
        target = SimpleNamespace(page_kind="edc_curve", params={})
        self.host._inherit_enhancement_state(target)
        self.assertNotIn(STATE_KEY, target.params)

    def test_common_lock_rebases_without_overwriting_independent_lock(self):
        source, _ = self._volume_source(locked=True)
        state = source.params[STATE_KEY]
        state.update(common=True, common_locked_range=(29., 94.))
        derived = SimpleNamespace(page_id="derived", page_kind="axis_integral", source_page_id="p1", params={})
        host = self.host
        host._inherit_enhancement_state(derived)
        host.active_page_spec = derived
        context = context_2d(np.arange(36).reshape(6, 6) * 10)
        context["slice_info"]["index"] = 1
        fields = host._enhancement_display(context)
        self.assertEqual(fields["regions"][0]["data_range"], (70., 270.))
        self.assertEqual(host._enhancement_state()["common_locked_range"], (70., 270.))
        self.assertTrue(host._enhancement_selected()["lock_pending"])
        host._enhancement_state()["common"] = False
        context["data"] = context["data"] * 3
        host._enhancement_display(context)
        self.assertEqual(host._enhancement_selected()["locked_range"], (210., 810.))
        self.assertEqual(host._enhancement_state()["common_locked_range"], (70., 270.))

    def _use_real_outline_drawing(self):
        host = self.host
        host._enhancement_draw_key = None
        host._enhancement_dragging = False
        host._crop_enabled = lambda: False
        host.timeline_bar = SimpleNamespace(switch_flip=SimpleNamespace(isChecked=lambda: False))
        host._draw_enhancement_regions = EnhancementInteractionMixin._draw_enhancement_regions.__get__(host)
        return host

    def test_2d_outlines_follow_selection_and_hidden_regions_cannot_be_picked(self):
        host = self._use_real_outline_drawing()
        figure = Figure()
        host.canvas_2d = FigureCanvasAgg(figure)
        host.ax_2d = figure.add_subplot()
        host.ax_2d._arpes_image = host.ax_2d.imshow(np.zeros((6, 6)))
        fields = host._enhancement_display(host.current_render_context)
        host._draw_enhancement_regions(host.current_render_context, fields)
        self.assertEqual([t.get_text() for t in host.ax_2d.texts if t.get_text().startswith("R")], ["R1"])
        hidden_click = SimpleNamespace(inaxes=host.ax_2d, button=1, xdata=2.5, ydata=3.)
        host._pick_enhancement_region(hidden_click)
        self.app.processEvents()
        self.assertEqual(host._enhancement_state()["selected_id"], "1")
        host._set_enhancement_show_all(True)
        self.assertEqual([t.get_text() for t in host.ax_2d.texts if t.get_text().startswith("R")], ["R1", "R2"])
        host._pick_enhancement_region(hidden_click)
        self.app.processEvents()
        self.assertEqual(host._enhancement_state()["selected_id"], "2")
        host._set_enhancement_show_all(False)
        self.assertEqual([t.get_text() for t in host.ax_2d.texts if t.get_text().startswith("R")], ["R2"])
        host._select_enhancement_target("")
        self.assertEqual(len(host.ax_2d.patches), 0)
        self.assertIsNone(host._enhancement_selector)
        self.assertEqual(len(fields["regions"]), 2)
        figure.clear()

    def test_3d_only_visible_boxes_have_actors_and_only_selected_has_editor(self):
        host = self._use_real_outline_drawing()
        self._volume_source()
        state = host._enhancement_state()
        state["regions"].append(region(2, (2, 5, 0, 2, 0, 3), view="3d", axes=("X", "Y", "E")))
        host.current_render_context = context_3d()
        host.core = SimpleNamespace(logical_to_render_bounds=lambda bounds, *_a, **_kw: bounds)
        host.plotter.add_mesh.side_effect = lambda *_a, **_kw: Mock()
        fields = host._enhancement_display(host.current_render_context)
        host._draw_enhancement_regions(host.current_render_context, fields)
        self.assertEqual(set(host._enhancement_actor_regions.values()), {"1"})
        self.assertIsNotNone(host._enhancement_box_widget)
        host._set_enhancement_show_all(True)
        self.assertEqual(set(host._enhancement_actor_regions.values()), {"1", "2"})
        host._select_enhancement_target("")
        self.assertEqual(set(host._enhancement_actor_regions.values()), {"1", "2"})
        self.assertIsNone(host._enhancement_box_widget)
        host._set_enhancement_show_all(False)
        self.assertEqual(host._enhancement_actors, [])
        self.assertEqual(host._enhancement_actor_regions, {})

    def test_right_click_toggle_does_not_recompute_channels_and_is_page_owned(self):
        host = self.host
        before = host._enhancement_display(host.current_render_context)
        menu = QMenu()
        host._append_enhancement_actions(menu)
        action = next(a for a in menu.actions() if a.text() == "显示全部增强区域")
        self.assertTrue(action.isCheckable())
        self.assertFalse(action.isChecked())
        with patch("bandscope.app.enhancement_integration.resolve_display", side_effect=AssertionError("Outline toggle must reuse channels")):
            action.trigger()
            self.assertIs(host._enhancement_display(host.current_render_context), before)
        original = host.active_page_spec
        host.active_page_spec = SimpleNamespace(page_id="other", params={STATE_KEY: state_2d()})
        other_menu = QMenu()
        host._append_enhancement_actions(other_menu)
        other_action = next(a for a in other_menu.actions() if a.text() == "显示全部增强区域")
        self.assertFalse(other_action.isChecked())
        host.active_page_spec = original
        self.assertTrue(host._enhancement_state()["show_all_regions"])
        menu.deleteLater()
        other_menu.deleteLater()

    def test_adding_region_inherits_current_settings_and_keeps_background_lock(self):
        host = self.host
        host.active_page_spec.params.pop(STATE_KEY)
        host.page_render.s_low.setValue(14)
        host.page_render.s_gamma.setValue(67)
        host.page_render.s_up.setValue(83)
        host.page_render.combo_map.setCurrentIndex(3)
        host._render_exact_ready = True
        host.btn_tb_crop, host.btn_tb_erase = QPushButton(), QPushButton()
        host._select_control_page = Mock()
        VisualEngine._locked_data_range = (0., 35.)
        host.btn_tb_lock.setChecked(True)
        host._add_enhancement_region()
        self.assertTrue(host._enhancement_selected()["enabled"])
        self.assertEqual(VisualEngine.locked_data_range(), (0., 35.))
        self.assertTrue(host.btn_tb_lock.isChecked())
        self.assertEqual(host._enhancement_selected()["style"], host._enhancement_state()["background"])
        self.assertEqual(host._enhancement_selected()["style"], dict(levels=(14., 67., 83.), opacity_mode="sigmoid"))
        host._add_enhancement_region()
        self.assertEqual(VisualEngine.locked_data_range(), (0., 35.))
        self.assertEqual(len(host._enhancement_state()["regions"]), 2)

    def test_home_roi_uses_crop_denoise_and_cache_without_changing_science_data(self):
        from bandscope.processing.denoise_engines import DenoiseEngines
        from bandscope.app.refactored_app import My3DAnalyzer
        host = self.host
        source, _ = self._volume_source()
        source.page_kind = "home"
        raw = np.zeros((4, 4, 16, 2), dtype=np.float32)
        raw[:, :, 3] = 100
        raw[:, :, 5] = 10
        original = raw.copy()
        source.params[STATE_KEY]["regions"][0]["bounds"] = (1, 2, 1, 2, 4, 11)
        source.params[STATE_KEY]["regions"][0].pop("volume_bounds", None)
        host.original_raw_data = raw
        host.global_denoise_methods = [dict(method=DenoiseEngines.SG_METHOD_NAME, params=dict(window_length=5, polyorder=2, smoothing_axis="e"))]
        host._validate_denoise_for_descriptor = Mock()
        host._copy_state = copy.deepcopy
        host._build_denoise_job = lambda descriptor, methods: My3DAnalyzer._build_denoise_job(host, descriptor, methods)
        host.backend_manager = SimpleNamespace(execute=Mock(side_effect=lambda job: SimpleNamespace(
            value=DenoiseEngines.apply_pipeline(job.payload["data"], job.payload["methods"])) ))
        host.timeline_bar = SimpleNamespace(slider_time=SimpleNamespace(value=lambda: 0))
        processed = DenoiseEngines.apply_pipeline(raw, host.global_denoise_methods)
        host.current_render_context = dict(view="3d", data=processed[..., 0],
            coords={k: np.arange(n, dtype=float) for k, n in zip(("X", "Y", "E"), raw.shape[:3])})
        preview = host._enhancement_display(host.current_render_context, freeze_pending=False)
        host.backend_manager.execute.assert_not_called()
        fields = host._enhancement_display(host.current_render_context)
        roi = raw[1:3, 1:3, 4:12]
        expected = DenoiseEngines.apply_pipeline(roi, host.global_denoise_methods)[..., 0]
        np.testing.assert_array_equal(fields["region_data"]["1"], expected)
        self.assertFalse(np.array_equal(fields["region_data"]["1"], preview["region_data"]["1"]))
        self.assertTrue(host._enhancement_display(host.current_render_context) is fields)
        self.assertTrue(host._enhancement_display(host.current_render_context, freeze_pending=False) is fields)
        host.backend_manager.execute.assert_called_once()
        np.testing.assert_array_equal(raw, original)
        np.testing.assert_array_equal(host.current_render_context["data"], processed[..., 0])
