import copy
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QComboBox, QMenu, QPushButton
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from siui.components.combobox_ import SiCapsuleComboBox

from bandscope.app.enhancement_integration import EnhancementInteractionMixin
from bandscope.core.local_enhancement import DEFAULT_STYLE, STATE_KEY, update_region_bounds
from bandscope.processing.display_transfer import level_info
from bandscope.rendering.local_enhancement import draw_region_colorbars_2d
from bandscope.rendering.refresh_pipeline import RefreshCause, RenderQuality
from bandscope.rendering.render_core import VisualEngine
from bandscope.ui import theme
from bandscope.ui.enhancement_controls import SHOW_ALL_REGIONS_ID
from bandscope.ui.page_render_control import RenderControlPage
from tests.support.enhancement import context_2d, context_3d, region, state_2d


class _ProjectedRenderer:
    """Identity display projection for the 3D box-volume hit-test fallback."""

    def __init__(self):
        self._world = (0.0, 0.0, 0.0)

    def SetWorldPoint(self, x, y, z, _w):
        self._world = (x, y, z)

    def WorldToDisplay(self):
        return 1

    def GetDisplayPoint(self):
        return self._world[0], self._world[1], 0.5


class _BandScalarBar:
    def __init__(self, position, size):
        self._position = position
        self._size = size

    def GetPosition(self):
        return self._position

    def GetWidth(self):
        return self._size[0]

    def GetHeight(self):
        return self._size[1]


class _BandRenderer:
    def __init__(self, size=(100, 100), origin=(0, 0)):
        self._size = size
        self._origin = origin

    def GetSize(self):
        return self._size

    def GetOrigin(self):
        return self._origin


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
        self._update_color_lock_button_style = Mock()
        self.request_refresh = Mock()
        self._draw_enhancement_regions = Mock()
        combo = self.page_render.region_combo
        combo.target_changed.connect(self._select_enhancement_target)
        combo.rename_committed.connect(self._rename_enhancement_region)
        self._enhancement_context_target = None
        self._restore_enhancement_style()
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

    def test_switching_targets_saves_and_restores_each_regions_style(self):
        host = self.host
        state = host._enhancement_state()
        original_background = copy.deepcopy(state["background"])
        host.page_render.s_low.setValue(17)
        host.page_render.s_gamma.setValue(63)
        host.page_render.s_up.setValue(88)
        host.page_render.combo_map.setCurrentText("sigmoid")
        host._select_enhancement_target("2")
        self.assertEqual(host._enhancement_ui_style(), original_background)
        host.page_render.s_low.setValue(7)
        host.page_render.s_gamma.setValue(41)
        host.page_render.s_up.setValue(74)
        host.page_render.combo_map.setCurrentText("对数")
        host._select_enhancement_target("1")
        first_style = dict(levels=(17., 63., 88.), opacity_mode="sigmoid")
        second_style = dict(levels=(7., 41., 74.), opacity_mode="对数")
        self.assertEqual(host._enhancement_ui_style(), first_style)
        self.assertEqual(host.page_render.s_low._value_label.text(), "17")
        self.assertEqual(host.page_render.s_gamma._value_label.text(), "63")
        self.assertEqual(host.page_render.s_up._value_label.text(), "88")
        self.assertEqual([r["style"] for r in state["regions"]], [first_style, second_style])
        self.assertEqual(state["background"], original_background)
        host._select_enhancement_target("")
        self.assertEqual(host._enhancement_ui_style(), original_background)
        host._select_enhancement_target("2")
        self.assertEqual(host._enhancement_ui_style(), second_style)

    def test_region_mapping_preserves_background_and_other_region_channels(self):
        host = self.host
        before = host._enhancement_display(host.current_render_context)
        state = host._enhancement_state()
        background = copy.deepcopy(state["background"])
        second_style = copy.deepcopy(state["regions"][1]["style"])
        host.page_render.s_low.setValue(19)
        host.page_render.s_gamma.setValue(64)
        host.page_render.combo_map.setCurrentText("对数")
        host._commit_enhancement_controls()
        after = host._enhancement_display(host.current_render_context)
        self.assertEqual(state["background"], background)
        self.assertEqual(state["regions"][1]["style"], second_style)
        self.assertEqual(after["regions"][0]["style"]["opacity_mode"], "对数")
        self.assertFalse(np.array_equal(after["color"][:2], before["color"][:2]))
        np.testing.assert_array_equal(after["color"][2:], before["color"][2:])
        np.testing.assert_array_equal(after["alpha"][2:], before["alpha"][2:])

    def test_volume_mapping_changes_only_selected_regions_opacity(self):
        host = self.host
        state = host._enhancement_state()
        state["regions"] = [
            region(1, (0, 1, 0, 4, 0, 3), view="3d", axes=("X", "Y", "E")),
            region(2, (2, 3, 0, 4, 0, 3), view="3d", axes=("X", "Y", "E")),
        ]
        host.current_render_context = context_3d()
        before = host._enhancement_display(host.current_render_context)
        host.page_render.combo_map.setCurrentText("sigmoid")
        host._commit_enhancement_controls()
        after = host._enhancement_display(host.current_render_context)
        self.assertFalse(np.array_equal(after["alpha"][:2], before["alpha"][:2]))
        np.testing.assert_array_equal(after["alpha"][2:], before["alpha"][2:])
        np.testing.assert_array_equal(after["color"], before["color"])
        self.assertEqual(state["background"]["opacity_mode"], "线性")
        self.assertEqual(state["regions"][1]["style"]["opacity_mode"], "线性")

    def test_background_controls_preserve_region_styles(self):
        host = self.host
        state = host._enhancement_state()
        original_regions = copy.deepcopy(state["regions"])
        host._select_enhancement_target("")
        host.page_render.s_low.setValue(6)
        host.page_render.s_gamma.setValue(54)
        host.page_render.s_up.setValue(96)
        host.page_render.combo_map.setCurrentText("幂函数")
        host._commit_enhancement_controls()
        self.assertEqual(state["background"], dict(levels=(6., 54., 96.), opacity_mode="幂函数"))
        self.assertEqual(state["regions"], original_regions)
        host._select_enhancement_target("1")
        self.assertEqual(host._enhancement_ui_style(), original_regions[0]["style"])

    def test_page_capture_stores_background_controls_and_selected_region_separately(self):
        host = self.host
        host._select_enhancement_target("")
        host.page_render.s_low.setValue(6)
        host.page_render.s_gamma.setValue(54)
        host.page_render.s_up.setValue(96)
        host.page_render.combo_map.setCurrentText("幂函数")
        host._select_enhancement_target("1")
        host.page_render.s_low.setValue(22)
        host.page_render.combo_map.setCurrentText("sigmoid")
        saved = host._enhancement_export_render_state(host.page_render.export_state())
        self.assertEqual([saved[key]["value"] for key in ("s_low", "s_gamma", "s_up")], [6, 54, 96])
        self.assertEqual(saved["combo_map"], "幂函数")
        state = host._enhancement_state()
        self.assertEqual(state["regions"][0]["style"], dict(levels=(22., 50., 100.), opacity_mode="sigmoid"))
        self.assertEqual(state["regions"][1]["style"], dict(levels=(0., 50., 100.), opacity_mode="线性"))
        self.assertEqual(state["background"], dict(levels=(6., 54., 96.), opacity_mode="幂函数"))

    def test_deletion_restores_next_regions_controls_and_keeps_background_mapping(self):
        host = self.host
        state = host._enhancement_state()
        host._select_enhancement_target("2")
        host.page_render.s_low.setValue(18)
        host.page_render.combo_map.setCurrentText("对数")
        host._select_enhancement_target("1")
        host.page_render.s_low.setValue(13)
        host.page_render.combo_map.setCurrentText("sigmoid")
        host._commit_enhancement_controls()
        background = copy.deepcopy(state["background"])
        self.assertEqual(host._enhancement_display(host.current_render_context)["background_level_info"]["data_min"], 0)
        host._delete_enhancement_region("1")
        self.assertEqual(state["selected_id"], "2")
        self.assertEqual(host.page_render.s_low.value(), 18)
        self.assertEqual(host.page_render.combo_map.currentText(), "对数")
        self.assertEqual(state["background"], background)
        self.assertEqual(host._enhancement_display(host.current_render_context)["background_level_info"]["data_min"], 0)

    def test_last_deletion_leaves_background_and_show_all_entries(self):
        host = self.host
        state = host._enhancement_state()
        host._select_enhancement_target("")
        host.page_render.s_low.setValue(9)
        host.page_render.s_gamma.setValue(44)
        host.page_render.s_up.setValue(95)
        host.page_render.combo_map.setCurrentText("幂函数")
        host._select_enhancement_target("1")
        host.page_render.s_low.setValue(23)
        host._delete_enhancement_region("1")
        host._delete_enhancement_region("2")
        host._sync_enhancement_controls(host.active_page_spec, host.current_render_context)
        combo = host.page_render.region_combo
        self.assertEqual(combo.count(), 2)
        self.assertEqual(combo.itemData(0), "")
        self.assertEqual(combo.itemData(1), SHOW_ALL_REGIONS_ID)
        self.assertEqual(state["background"]["levels"], (9., 44., 95.))
        self.assertEqual(host._enhancement_ui_style(), state["background"])
        self.assertIsNone(host._enhancement_display(host.current_render_context))

    def test_deletion_releases_global_lock(self):
        host = self.host
        VisualEngine._locked_data_range = (24, 35)
        host._set_all_regions_locked(True)
        self.assertTrue(all(r.get("locked_range") for r in host._enhancement_state()["regions"]))
        host._delete_enhancement_region("1")
        self.assertIsNone(VisualEngine.locked_data_range())
        self.assertFalse(any("locked_range" in r for r in host._enhancement_state()["regions"]))
        self.assertFalse(host.page_render.btn_lock.isChecked())

    def test_dropdown_is_siliconui_editable_without_row_delete(self):
        combo = self.host.page_render.region_combo
        self.assertIsInstance(combo, SiCapsuleComboBox)
        self.assertTrue(combo.isEditable())
        self.assertFalse(hasattr(combo, "delete_requested"))
        self.assertEqual(combo.toolTip(), "")
        self.assertEqual(combo.lineEdit().toolTip(), "")
        self.assertEqual(combo.count(), 4)
        self.assertEqual(combo.itemData(1), SHOW_ALL_REGIONS_ID)

    def test_editable_combo_focus_accepts_number_input(self):
        from bandscope.app.refactored_app import My3DAnalyzer

        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        combo = self.host.page_render.region_combo
        plain = QComboBox()
        plain.addItem("普通下拉")
        with patch("bandscope.app.refactored_app.QApplication") as qapp:
            qapp.focusWidget.return_value = combo
            self.assertTrue(analyzer._focus_accepts_number_input())
            qapp.focusWidget.return_value = plain
            self.assertFalse(analyzer._focus_accepts_number_input())
        plain.close()

    def test_dropdown_selection_switches_target(self):
        host = self.host
        combo = host.page_render.region_combo
        combo.setCurrentIndex(3)  # R2
        self.assertEqual(combo.lineEdit().text(), combo.itemText(3))
        self.assertEqual(host._enhancement_state()["selected_id"], "2")
        self.assertFalse(host._enhancement_state()["show_all_regions"])
        combo.setCurrentIndex(1)  # 全部显示
        self.assertTrue(host._enhancement_state()["show_all_regions"])
        combo.setCurrentIndex(0)  # 全局（背景）
        self.assertIsNone(host._enhancement_state()["selected_id"])
        self.assertFalse(host._enhancement_state()["show_all_regions"])

    def test_siliconui_popup_lists_regions_and_selects_target(self):
        host = self.host
        combo = host.page_render.region_combo
        names = [r["name"] for r in host._enhancement_state()["regions"]]
        combo.show()
        try:
            combo.showPopup()
            self.app.processEvents()
            self.assertTrue(combo._menu.isVisible())
            self.assertEqual(
                [a.text() for a in combo._menu.actions()],
                ["全局（背景）", "全部显示"] + names,
            )
            action = next(a for a in combo._menu.actions() if a.text() == names[1])
            action.trigger()
            self.app.processEvents()
            self.assertEqual(host._enhancement_state()["selected_id"], "2")
            self.assertEqual(combo.lineEdit().text(), names[1])
        finally:
            combo.hidePopup()
            combo.hide()

    def test_view_right_click_deletes_region_under_cursor(self):
        host, figure = self._use_real_2d_canvas()
        host._set_enhancement_show_all(True)
        click = SimpleNamespace(inaxes=host.ax_2d, button=3, xdata=2.5, ydata=3.)
        host._pick_enhancement_region(click)
        self.assertEqual(host._enhancement_context_target, "2")
        menu = QMenu()
        host._append_enhancement_actions(menu, host._enhancement_context_target)
        action = next(a for a in menu.actions() if a.text() == "删除增强区域「R2」")
        action.trigger()
        self.app.processEvents()
        self.assertEqual([r["region_id"] for r in host._enhancement_state()["regions"]], ["1"])
        self.assertIsNone(host._enhancement_context_target)
        menu.deleteLater()
        figure.clear()

    def test_view_right_click_outside_regions_offers_no_delete(self):
        host, figure = self._use_real_2d_canvas()
        click = SimpleNamespace(inaxes=host.ax_2d, button=3, xdata=4.5, ydata=4.5)
        host._pick_enhancement_region(click)
        self.assertIsNone(host._enhancement_context_target)
        menu = QMenu()
        host._append_enhancement_actions(menu, host._enhancement_context_target)
        self.assertFalse(any(a.text().startswith("删除增强区域") for a in menu.actions()))
        menu.deleteLater()
        figure.clear()

    def test_3d_right_click_picks_outlined_box_for_delete(self):
        host = self._use_real_outline_drawing()
        self._volume_source()
        host.current_render_context = context_3d()
        host.core = SimpleNamespace(logical_to_render_bounds=lambda bounds, *_a, **_kw: bounds)
        actor = object()
        host.plotter.add_mesh.return_value = actor
        fields = host._enhancement_display(host.current_render_context)
        host._draw_enhancement_regions(host.current_render_context, fields)
        host.plotter.iren.interactor.GetEventPosition.return_value = (10, 10)
        with patch("vtk.vtkPropPicker") as picker_cls:
            picker_cls.return_value.GetActor.return_value = actor
            host._remember_enhancement_box_context(None, None)
        self.assertEqual(host._enhancement_context_target, "1")
        menu = QMenu()
        host._append_enhancement_actions(menu, host._enhancement_context_target)
        self.assertTrue(any(a.text() == "删除增强区域「R1」" for a in menu.actions()))
        menu.deleteLater()

    def test_3d_right_click_inside_box_falls_back_to_projection(self):
        host = self._use_real_outline_drawing()
        self._volume_source()
        host.current_render_context = context_3d()
        host.core = SimpleNamespace(logical_to_render_bounds=lambda bounds, *_a, **_kw: bounds)
        actor = object()
        host.plotter.add_mesh.return_value = actor
        fields = host._enhancement_display(host.current_render_context)
        host._draw_enhancement_regions(host.current_render_context, fields)
        host.plotter.renderer = _ProjectedRenderer()
        host.plotter.iren.interactor.GetEventPosition.return_value = (2.5, 2.0)
        with patch("vtk.vtkPropPicker") as picker_cls:
            # 点在区域内部时硬件拾取只会命中体数据/背景，而不是轮廓线。
            picker_cls.return_value.GetActor.return_value = object()
            host._remember_enhancement_box_context(None, None)
        self.assertEqual(host._enhancement_context_target, "1")

    def test_combo_edit_renames_region_and_syncs_view_label(self):
        host = self.host
        host._rename_enhancement_region("主峰")
        region = host._enhancement_selected()
        self.assertEqual(region["name"], "主峰")
        self.assertEqual(region["label"], "主峰")
        host._sync_enhancement_controls(host.active_page_spec, host.current_render_context)
        combo = host.page_render.region_combo
        self.assertEqual(combo.itemText(2), "主峰")
        # 可编辑框提交即重命名选中区域。
        combo.lineEdit().setText("新名")
        combo.lineEdit().editingFinished.emit()
        self.assertEqual(host._enhancement_selected()["name"], "新名")
        self.assertEqual(host._enhancement_selected()["label"], "新名")
        self.assertEqual(combo.itemText(2), "新名")
        # 空名不生效，下拉框文字回到当前名称。
        host._rename_enhancement_region("  ")
        self.assertEqual(host._enhancement_selected()["name"], "新名")
        self.assertEqual(combo.lineEdit().text(), "新名")
        # 「全部显示」不可重命名：编辑提交后恢复固定文字。
        host._select_enhancement_target(SHOW_ALL_REGIONS_ID)
        combo.lineEdit().setText("改名")
        combo.lineEdit().editingFinished.emit()
        self.assertEqual(combo.lineEdit().text(), "全部显示")

    def test_page_isolation_and_selected_region_style_restore(self):
        host = self.host
        original = host.active_page_spec
        host.page_render.s_gamma.setValue(71)
        host._commit_enhancement_controls()
        host.active_page_spec = SimpleNamespace(page_id="p2", params={STATE_KEY: state_2d()})
        host.active_page_spec.params[STATE_KEY]["regions"][0]["enabled"] = False
        host._restore_enhancement_style()
        self.assertTrue(host._enhancement_selected()["enabled"])
        self.assertTrue(host._enhancement_display(host.current_render_context)["active"])
        self.assertEqual(host.page_render.s_gamma.value(), 50)
        host.active_page_spec = original
        host._restore_enhancement_style()
        self.assertEqual(host.page_render.s_gamma.value(), 71)
        self.assertEqual(host._enhancement_state()["background"]["levels"][1], 50)
        self.assertEqual(host._enhancement_state()["regions"][1]["style"]["levels"][1], 50)

    def test_mismatching_view_restores_background_controls(self):
        host = self.host
        host.page_render.s_low.setValue(21)
        host.page_render.combo_map.setCurrentText("sigmoid")
        host._commit_enhancement_controls()
        host.current_render_context = context_2d(axis=0)
        host._sync_enhancement_controls(host.active_page_spec, host.current_render_context)
        state = host._enhancement_state()
        self.assertIsNone(state["selected_id"])
        self.assertEqual(host._enhancement_ui_style(), state["background"])
        self.assertEqual(state["regions"][0]["style"]["levels"][0], 21)
        self.assertEqual(state["regions"][0]["style"]["opacity_mode"], "sigmoid")

    def _set_distinct_styles(self):
        host = self.host
        state = host._enhancement_state()
        state["background"] = dict(levels=(4., 56., 96.), opacity_mode="幂函数")
        state["regions"][0]["style"] = dict(levels=(11., 62., 88.), opacity_mode="sigmoid")
        state["regions"][1]["style"] = dict(levels=(7., 43., 79.), opacity_mode="对数")
        host._restore_enhancement_style()
        return state

    def test_show_all_selection_and_page_capture_preserve_independent_styles_without_edits(self):
        host = self.host
        state = self._set_distinct_styles()
        before = copy.deepcopy(state)
        host._select_enhancement_target(SHOW_ALL_REGIONS_ID)
        self.assertEqual(host._enhancement_ui_style(), before["background"])
        host._enhancement_export_render_state(host.page_render.export_state())
        host._select_enhancement_target("2")
        self.assertEqual(host._enhancement_ui_style(), before["regions"][1]["style"])
        host._set_enhancement_show_all(True)
        self.assertEqual(host._enhancement_ui_style(), before["background"])
        host._set_enhancement_show_all(False)
        self.assertEqual(host._enhancement_ui_style(), before["regions"][1]["style"])
        self.assertEqual(state["background"], before["background"])
        self.assertEqual(state["regions"], before["regions"])
        self.assertEqual(state["revision"], before["revision"])

    def test_show_all_falls_back_to_background_when_no_regions_match_view(self):
        host = self.host
        state = self._set_distinct_styles()
        before = copy.deepcopy(state["regions"])
        host._select_enhancement_target(SHOW_ALL_REGIONS_ID)
        host.current_render_context = context_2d(axis=0)
        host._sync_enhancement_controls(host.active_page_spec, host.current_render_context)
        self.assertIsNone(state["selected_id"])
        self.assertFalse(state["show_all_regions"])
        self.assertEqual(host._enhancement_ui_style(), state["background"])
        self.assertEqual(state["regions"], before)
        combo = host.page_render.region_combo
        self.assertEqual(combo.itemData(combo.currentIndex()), "")

    def test_show_all_slider_changes_apply_to_background_and_every_region(self):
        from bandscope.app.refactored_app import My3DAnalyzer
        host = self.host
        state = self._set_distinct_styles()
        host._select_enhancement_target(SHOW_ALL_REGIONS_ID)
        host.page_render.s_low.setValue(16)
        host.page_render.s_gamma.setValue(68)
        host.page_render.s_up.setValue(92)
        My3DAnalyzer.schedule_render_levels_refresh(host, 92)
        expected = dict(levels=(16., 68., 92.), opacity_mode="幂函数")
        self.assertEqual(state["background"], expected)
        self.assertTrue(all(r["style"] == expected for r in state["regions"]))
        host._select_enhancement_target("1")
        self.assertEqual(host._enhancement_ui_style(), expected)
        host.page_render.s_low.setValue(24)
        My3DAnalyzer.flush_render_levels_refresh(host)
        self.assertEqual(state["regions"][0]["style"]["levels"][0], 24)
        self.assertEqual(state["regions"][1]["style"], expected)
        self.assertEqual(state["background"], expected)

    def test_show_all_mapping_applies_even_when_it_already_matches_background(self):
        from bandscope.app.refactored_app import My3DAnalyzer
        host = self.host
        state = self._set_distinct_styles()
        host._select_enhancement_target(SHOW_ALL_REGIONS_ID)
        self.assertEqual(host.page_render.combo_map.currentText(), "幂函数")
        # 用户重新应用当前映射仍是调节操作，须覆盖区域原有的其他映射。
        My3DAnalyzer.flush_render_levels_refresh(host)
        self.assertTrue(all(r["style"]["opacity_mode"] == "幂函数" for r in state["regions"]))
        host.page_render.combo_map.setCurrentText("对数")
        My3DAnalyzer.flush_render_levels_refresh(host)
        self.assertEqual(state["background"]["opacity_mode"], "对数")
        self.assertTrue(all(r["style"]["opacity_mode"] == "对数" for r in state["regions"]))

    def test_show_all_updates_legacy_common_style_as_well_as_regions_and_background(self):
        from bandscope.app.refactored_app import My3DAnalyzer
        host = self.host
        state = self._set_distinct_styles()
        state.update(common=True, common_style=dict(levels=(13., 61., 87.), opacity_mode="sigmoid"))
        host._restore_enhancement_style()
        host._select_enhancement_target(SHOW_ALL_REGIONS_ID)
        host.page_render.combo_map.setCurrentText("线性")
        My3DAnalyzer.flush_render_levels_refresh(host)
        expected = dict(levels=(4., 56., 96.), opacity_mode="线性")
        self.assertTrue(state["common"])
        self.assertEqual(state["common_style"], expected)
        self.assertEqual(state["background"], expected)
        self.assertTrue(all(r["style"] == expected for r in state["regions"]))
        fields = host._enhancement_display(host.current_render_context)
        self.assertTrue(all(r["style"] == expected for r in fields["regions"]))

    def test_new_data_releases_regions_and_display_cache(self):
        host = self.host
        host._enhancement_display(host.current_render_context)
        self.assertIsNotNone(host._enhancement_cache)
        host._reset_enhancement_for_new_data()
        self.assertNotIn(STATE_KEY, host.active_page_spec.params)
        self.assertIsNone(host._enhancement_cache)
        self.assertEqual(host.page_render.region_combo.count(), 2)
        self.assertEqual(host.page_render.region_combo.itemData(0), "")
        self.assertEqual(host.page_render.region_combo.itemData(1), SHOW_ALL_REGIONS_ID)

    def _volume_source(self, *, locked=False):
        host = self.host
        state = host._enhancement_state()
        style = dict(levels=(12., 63., 91.), opacity_mode="sigmoid")
        state["regions"] = [region(1, (1, 4, 1, 3, 1, 2), view="3d", axes=("X", "Y", "E"),
                                   style=copy.deepcopy(style))]
        # 此继承样例从相同的背景与区域设置开始。
        state["background"] = copy.deepcopy(style)
        if locked:
            state["regions"][0]["locked_range"] = (29., 94.)
        host._restore_enhancement_style()
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
        self.assertEqual(host.page_render.region_combo.count(), 3)
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

    def _use_real_2d_canvas(self):
        host = self._use_real_outline_drawing()
        figure = Figure()
        host.canvas_2d = FigureCanvasAgg(figure)
        host.ax_2d = figure.add_subplot()
        host.ax_2d._arpes_image = host.ax_2d.imshow(np.zeros((6, 6)))
        fields = host._enhancement_display(host.current_render_context)
        host._draw_enhancement_regions(host.current_render_context, fields)
        return host, figure

    def test_2d_outlines_follow_selection_and_hidden_regions_cannot_be_picked(self):
        host = self._use_real_outline_drawing()
        figure = Figure()
        host.canvas_2d = FigureCanvasAgg(figure)
        host.ax_2d = figure.add_subplot()
        host.ax_2d._arpes_image = host.ax_2d.imshow(np.zeros((6, 6)))
        fields = host._enhancement_display(host.current_render_context)
        host._draw_enhancement_regions(host.current_render_context, fields)
        combo = host.page_render.region_combo
        self.assertEqual([t.get_text() for t in host.ax_2d.texts if t.get_text().startswith("R")], ["R1"])
        hidden_click = SimpleNamespace(inaxes=host.ax_2d, button=1, xdata=2.5, ydata=3.)
        host._pick_enhancement_region(hidden_click)
        self.app.processEvents()
        self.assertEqual(host._enhancement_state()["selected_id"], "1")
        host._set_enhancement_show_all(True)
        self.assertEqual(combo.itemData(combo.currentIndex()), SHOW_ALL_REGIONS_ID)
        self.assertEqual([t.get_text() for t in host.ax_2d.texts if t.get_text().startswith("R")], ["R1", "R2"])
        # 全部显示时点击原本选中的区域，也应切回该区域的独立调整。
        selected_click = SimpleNamespace(inaxes=host.ax_2d, button=1, xdata=.5, ydata=3.)
        host._pick_enhancement_region(selected_click)
        self.app.processEvents()
        self.assertEqual(host._enhancement_state()["selected_id"], "1")
        self.assertFalse(host._enhancement_state()["show_all_regions"])
        self.assertEqual(combo.itemData(combo.currentIndex()), "1")
        self.assertEqual(host._enhancement_ui_style(), host._enhancement_selected()["style"])
        host._set_enhancement_show_all(True)
        host._pick_enhancement_region(hidden_click)
        self.app.processEvents()
        self.assertEqual(host._enhancement_state()["selected_id"], "2")
        self.assertFalse(host._enhancement_state()["show_all_regions"])
        self.assertEqual(combo.itemData(combo.currentIndex()), "2")
        host._set_enhancement_show_all(False)
        self.assertEqual([t.get_text() for t in host.ax_2d.texts if t.get_text().startswith("R")], ["R2"])
        host._select_enhancement_target("")
        self.assertEqual(len(host.ax_2d.patches), 0)
        self.assertIsNone(host._enhancement_selector)
        self.assertEqual(len(fields["regions"]), 2)
        figure.clear()

    def test_dropdown_show_all_entry_controls_colorbar_visibility(self):
        host = self._use_real_outline_drawing()
        figure = Figure()
        host.canvas_2d = FigureCanvasAgg(figure)
        host.ax_2d = figure.add_subplot()
        host.ax_2d._arpes_image = host.ax_2d.imshow(np.zeros((6, 6)))
        combo = host.page_render.region_combo
        # 选中某区域：只画该区域的一条色带。
        host._select_enhancement_target("2")
        self.assertEqual([c.ax.get_xlabel() for c in host._enhancement_colorbars], ["R2"])
        # 下拉框选「全部显示」：全部区域色带都显示。
        host._select_enhancement_target(SHOW_ALL_REGIONS_ID)
        self.assertTrue(host._enhancement_state()["show_all_regions"])
        self.assertEqual(combo.itemData(combo.currentIndex()), SHOW_ALL_REGIONS_ID)
        self.assertEqual([c.ax.get_xlabel() for c in host._enhancement_colorbars], ["R1", "R2"])
        # 选回单个区域：只显示该区域的色带；选「全局（背景）」：不显示区域色带。
        host._select_enhancement_target("1")
        self.assertFalse(host._enhancement_state()["show_all_regions"])
        self.assertEqual([c.ax.get_xlabel() for c in host._enhancement_colorbars], ["R1"])
        host._select_enhancement_target("")
        self.assertEqual(host._enhancement_colorbars, [])
        figure.clear()

    def test_global_lock_freezes_each_regions_own_range(self):
        host = self.host
        VisualEngine._last_data_range = (0., 35.)
        host._set_all_regions_locked(True)
        regions = host._enhancement_state()["regions"]
        # 全局锁定统一作用于所有区域，但每个区域锁定自己实测的强度范围。
        self.assertEqual(tuple(regions[0]["locked_range"]), (0., 11.))
        self.assertEqual(tuple(regions[1]["locked_range"]), (12., 23.))
        self.assertNotEqual(tuple(regions[0]["locked_range"]), tuple(regions[1]["locked_range"]))
        fields = host._enhancement_display(host.current_render_context)
        self.assertEqual([tuple(r["data_range"]) for r in fields["regions"]], [(0., 11.), (12., 23.)])
        host._sync_color_lock_button()
        self.assertTrue(host.page_render.btn_lock.isChecked())
        host._set_all_regions_locked(False)
        self.assertFalse(any("locked_range" in r for r in regions))
        host._sync_color_lock_button()
        self.assertFalse(host.page_render.btn_lock.isChecked())
        VisualEngine.unlock_data_range()

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
        # 下拉框即「全部显示」开关：选「全局（背景）」同时退出全部显示。
        self.assertFalse(host._enhancement_state()["show_all_regions"])
        self.assertEqual(host._enhancement_actors, [])
        self.assertEqual(host._enhancement_actor_regions, {})
        self.assertIsNone(host._enhancement_box_widget)
        host._set_enhancement_show_all(True)
        self.assertEqual(set(host._enhancement_actor_regions.values()), {"1", "2"})
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
        host._add_enhancement_region()
        self.assertTrue(host._enhancement_selected()["enabled"])
        self.assertEqual(VisualEngine.locked_data_range(), (0., 35.))
        self.assertEqual(host._enhancement_selected()["name"], "R1")
        self.assertEqual(host._enhancement_selected()["style"], host._enhancement_state()["background"])
        self.assertEqual(host._enhancement_selected()["style"], dict(levels=(14., 67., 83.), opacity_mode="sigmoid"))
        background = copy.deepcopy(host._enhancement_state()["background"])
        first_id = host._enhancement_selected()["region_id"]
        host.page_render.s_low.setValue(31)
        host.page_render.combo_map.setCurrentText("对数")
        host._add_enhancement_region()
        self.assertEqual(VisualEngine.locked_data_range(), (0., 35.))
        self.assertEqual(len(host._enhancement_state()["regions"]), 2)
        self.assertNotEqual(host._enhancement_selected()["region_id"], first_id)
        self.assertEqual(host._enhancement_ui_style(), dict(levels=(31., 67., 83.), opacity_mode="对数"))
        self.assertEqual(host._enhancement_selected()["style"], host._enhancement_ui_style())
        self.assertEqual(host._enhancement_state()["background"], background)

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


class EnhancementBoxIncrementalTests(unittest.TestCase):
    """3D 辅助对象按签名增量更新：只改色阶时不重建框和标签。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply_siui_palette()

    @staticmethod
    def _host():
        host = EnhancementInteractionMixin.__new__(EnhancementInteractionMixin)
        host.plotter = Mock(box_widgets=[])
        host.plotter.add_mesh.return_value = object()
        host._enhancement_actors = []
        host._enhancement_scalar_bars = []
        host._enhancement_actor_regions = {}
        host._enhancement_box_widget = None
        host._enhancement_draw_key = None
        host._enhancement_box_signature = None
        host._enhancement_bar_signature = None
        host._enhancement_dragging = False
        host.core = SimpleNamespace(logical_to_render_bounds=lambda bounds, shape, clamp=False: tuple(bounds))
        host.page_render = SimpleNamespace(get_selected_cmap=lambda: "magma")
        return host

    @staticmethod
    def _fields(levels=(0, 50, 100)):
        info = level_info((0.0, 100.0), levels)
        return {"regions": [{"region_id": "1", "name": "R1", "label": "R1", "status": "ready",
                             "data_range": (0.0, 100.0), "level_info": info, "enabled": True,
                             "bounds": (0.0, 3.0, 0.0, 3.0, 0.0, 3.0)}],
                "background_level_info": info, "active": True, "common": False}

    def test_geometry_is_reused_while_bars_follow_the_levels(self):
        host = self._host()
        context = {"data": np.zeros((4, 4, 4), dtype=np.float32)}
        fields = self._fields()
        host._draw_enhancement_boxes(context, fields, None)
        self.assertEqual(host.plotter.add_mesh.call_count, 1)
        host.plotter.add_scalar_bar.assert_called_once()

        host.plotter.remove_actor.reset_mock()
        host.plotter.add_scalar_bar.reset_mock()
        host._draw_enhancement_boxes(context, fields, None)
        host.plotter.remove_actor.assert_not_called()
        host.plotter.add_scalar_bar.assert_not_called()

        host.plotter.add_scalar_bar.reset_mock()
        host._draw_enhancement_boxes(context, self._fields((10, 60, 90)), None)
        self.assertEqual(host.plotter.add_mesh.call_count, 1)
        host.plotter.add_scalar_bar.assert_called_once()


class EnhancementRoiAsyncTests(unittest.TestCase):
    """ROI 去噪在后台线程执行；结果按 token/数据源/版本提交，过期即丢弃。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply_siui_palette()

    def setUp(self):
        self.hosts = []

    def tearDown(self):
        for host in self.hosts:
            pool = host._enhancement_roi_pool
            if pool is not None:
                pool.waitForDone(5000)
            host._invalidate_enhancement_roi()

    def _host(self):
        host = EnhancementInteractionMixin.__new__(EnhancementInteractionMixin)
        host._enhancement_roi_pool = None
        host._enhancement_roi_timer = None
        host._enhancement_roi_lock = threading.Lock()
        host._enhancement_roi_token = 0
        host._enhancement_roi_job = None
        host._enhancement_roi_task = None
        host._enhancement_roi_result = None
        host._enhancement_roi_disabled = False
        host._enhancement_roi_data_cache = {}
        host._enhancement_cache = object()
        host._render_exact_ready = True
        host._update_export_button_states = Mock()
        host.request_refresh = Mock()
        host.rotation_angle = 0.0
        host.shared_denoise_version = 0
        host.original_raw_data = np.ones((4, 4, 8, 2), dtype=np.float32)
        host.global_denoise_methods = [{"method": "savitzky_golay"}]
        host.backend_manager = SimpleNamespace(
            execute=Mock(return_value=SimpleNamespace(value=np.ones((4, 4, 4, 2), dtype=np.float32))))
        host._validate_denoise_for_descriptor = Mock()
        roi = region(1, (0, 1, 0, 1, 2, 5), view="3d", axes=("X", "Y", "E"))
        host.state = {"regions": [roi], "background": copy.deepcopy(DEFAULT_STYLE),
                      "revision": 1, "next_label": 2, "common": False}
        host.active_page_spec = SimpleNamespace(page_kind="home", params={STATE_KEY: host.state})
        shape = host.original_raw_data.shape[:3]
        host.current_render_context = {
            "view": "3d",
            "data": host.original_raw_data[..., 0],
            "coords": {key: np.arange(n, dtype=float) for key, n in zip(("X", "Y", "E"), shape)},
        }
        self.hosts.append(host)
        return host

    def _wait_for_job(self, host, timeout=5.0):
        deadline = time.time() + timeout
        while host._enhancement_roi_job is not None and time.time() < deadline:
            QTest.qWait(20)
        return host._enhancement_roi_job is None

    def test_roi_denoise_runs_off_thread_and_commits(self):
        host = self._host()
        host._schedule_enhancement_roi(host.current_render_context, host.active_page_spec, host.state)
        self.assertIsNotNone(host._enhancement_roi_job)
        self.assertFalse(host._render_exact_ready)
        self.assertTrue(self._wait_for_job(host))
        self.assertEqual(host.backend_manager.execute.call_count, 1)
        self.assertIn("1", host._enhancement_roi_data_cache)
        self.assertIs(host._enhancement_cache, None)
        host.request_refresh.assert_called_once()

    def test_stale_result_is_dropped_after_invalidation(self):
        host = self._host()
        host._schedule_enhancement_roi(host.current_render_context, host.active_page_spec, host.state)
        host._invalidate_enhancement_roi()
        deadline = time.time() + 5
        while time.time() < deadline:
            with host._enhancement_roi_lock:
                if host._enhancement_roi_result is not None:
                    break
            QTest.qWait(20)
        host._poll_enhancement_roi()
        self.assertEqual(host._enhancement_roi_data_cache, {})
        self.assertIsNone(host._enhancement_roi_job)


class ColorBandClipboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply_siui_palette()

    def setUp(self):
        VisualEngine.unlock_data_range()
        self.host = Host()
        self.host._color_band_clipboard = None
        self.host._band_context_target = None
        self.host._enhancement_scalar_bar_records = []

    def tearDown(self):
        self.host._clear_enhancement_interaction()
        self.host.page_render.close()
        self.host.page_render.deleteLater()
        self.app.processEvents()
        VisualEngine.unlock_data_range()

    def test_copy_and_paste_region_only_changes_target_levels(self):
        host = self.host
        state = host._enhancement_state()
        first, second = state["regions"]
        first["style"] = {"levels": (10., 60., 90.), "opacity_mode": "sigmoid"}
        second["style"] = {"levels": (0., 50., 100.), "opacity_mode": "对数"}
        second["locked_range"] = (29., 94.)
        cmap = host.page_render.get_selected_cmap()
        self.assertTrue(host._copy_color_band(("region", "1")))
        self.assertEqual(host._color_band_clipboard["levels"], (10., 60., 90.))
        self.assertEqual(host._color_band_clipboard["source"], "R1")
        background = copy.deepcopy(state["background"])
        self.assertTrue(host._paste_color_band(("region", "2")))
        self.assertEqual(first["style"]["levels"], (10., 60., 90.))
        self.assertEqual(second["style"]["levels"], (10., 60., 90.))
        # 透明度映射与锁定范围都属于目标对象，粘贴不覆盖。
        self.assertEqual(second["style"]["opacity_mode"], "对数")
        self.assertEqual(second["locked_range"], (29., 94.))
        self.assertEqual(state["background"], background)
        self.assertEqual(host.page_render.get_selected_cmap(), cmap)
        fields = host._enhancement_display(host.current_render_context)
        record = next(region for region in fields["regions"] if region["region_id"] == "2")
        self.assertAlmostEqual(record["level_info"]["data_min"], 29.)
        self.assertAlmostEqual(record["level_info"]["data_max"], 94.)
        self.assertAlmostEqual(record["level_info"]["black_pos"], .1)
        self.assertAlmostEqual(record["level_info"]["white_pos"], .9)
        host.request_refresh.assert_called_with(
            RefreshCause.DISPLAY_MAPPING, RenderQuality.EXACT, interactive=False, immediate=True)

    def test_paste_to_selected_region_syncs_controls(self):
        host = self.host
        state = host._enhancement_state()
        state["regions"][1]["style"]["levels"] = (20., 70., 90.)
        self.assertTrue(host._copy_color_band(("region", "2")))
        self.assertTrue(host._paste_color_band(("region", "1")))
        self.assertEqual(
            (host.page_render.s_low.value(), host.page_render.s_gamma.value(), host.page_render.s_up.value()),
            (20, 70, 90))
        self.assertEqual(state["regions"][0]["style"]["levels"], (20., 70., 90.))
        self.assertEqual(state["regions"][0]["style"]["opacity_mode"], "线性")

    def test_paste_to_background_does_not_touch_region_controls(self):
        host = self.host
        state = host._enhancement_state()
        state["background"]["levels"] = (5., 55., 95.)
        self.assertTrue(host._copy_color_band(("background", None)))
        self.assertTrue(host._paste_color_band(("region", "2")))
        self.assertEqual(state["background"]["levels"], (5., 55., 95.))
        self.assertEqual(state["regions"][1]["style"]["levels"], (5., 55., 95.))
        # 当前下拉框仍是 R1：粘贴背景只改状态，不移动当前调整对象。
        host._color_band_clipboard = {"levels": (12., 62., 92.), "source": "R1"}
        self.assertTrue(host._paste_color_band(("background", None)))
        self.assertEqual(state["background"]["levels"], (12., 62., 92.))
        self.assertEqual(
            (host.page_render.s_low.value(), host.page_render.s_gamma.value(), host.page_render.s_up.value()),
            (0, 50, 100))

    def test_no_state_background_paste_updates_sliders_only(self):
        host = self.host
        host.active_page_spec.params.clear()
        self.assertIsNone(host._enhancement_state())
        host._color_band_clipboard = {"levels": (20., 70., 90.), "source": "R1"}
        self.assertTrue(host._paste_color_band(("background", None)))
        self.assertEqual(
            (host.page_render.s_low.value(), host.page_render.s_gamma.value(), host.page_render.s_up.value()),
            (20, 70, 90))
        host.request_refresh.assert_called_with(
            RefreshCause.TRANSFER_FUNCTION, RenderQuality.EXACT, immediate=True)

    def test_empty_clipboard_paste_is_noop(self):
        self.host._color_band_clipboard = None
        self.assertFalse(self.host._paste_color_band(("region", "2")))
        self.host.request_refresh.assert_not_called()

    def test_paste_does_not_change_locked_state(self):
        host = self.host
        state = host._enhancement_state()
        state["regions"][0]["style"]["levels"] = (11., 61., 91.)
        state["regions"][1]["locked_range"] = (29., 94.)
        VisualEngine._last_data_range = (0., 100.)
        self.assertTrue(VisualEngine.lock_data_range())
        self.addCleanup(VisualEngine.unlock_data_range)
        self.assertTrue(host._copy_color_band(("region", "1")))
        self.assertTrue(host._paste_color_band(("region", "2")))
        self.assertEqual(VisualEngine.locked_data_range(), (0., 100.))
        self.assertEqual(state["regions"][1]["locked_range"], (29., 94.))

    def test_two_d_colorbar_hit_returns_band_target(self):
        host = self.host
        info = level_info((0., 1.), (0, 50, 100))
        fig = Figure()
        canvas = FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)
        image = ax.imshow(np.zeros((2, 2)))
        VisualEngine._add_2d_colorbar(ax, image, info)
        canvas.draw()
        cax = ax._arpes_colorbar_ax
        self.assertEqual(getattr(cax, "_arpes_band_target", None), ("background", None))
        # inset colourbars live in child_axes, not figure.axes: the parent axes
        # is what matplotlib reports for the click, so always hit-test the boxes.
        event = SimpleNamespace(canvas=canvas, x=(cax.bbox.x0 + cax.bbox.x1) / 2,
                                y=(cax.bbox.y0 + cax.bbox.y1) / 2, inaxes=ax,
                                button=3, xdata=0.0, ydata=0.0)
        self.assertEqual(host._two_d_band_target_at_event(event), ("background", None))
        host.ax_2d = ax
        host.canvas_2d = canvas
        host._crop_enabled = lambda: False
        host._pick_enhancement_region(event)
        self.assertEqual(host._band_context_target, ("background", None))
        self.assertIsNone(host._enhancement_context_target)

        class _CanvasView:
            def __init__(self, source):
                self.figure = source.figure
                self.device_pixel_ratio = 1.0
                self._source = source

            def get_renderer(self):
                return self._source.get_renderer()

            def height(self):
                return self._source.get_width_height()[1]

        view = _CanvasView(canvas)
        host.canvas_2d = view
        pos = SimpleNamespace(
            x=lambda: int(round((cax.bbox.x0 + cax.bbox.x1) / 2)),
            y=lambda: int(round(view.height() - (cax.bbox.y0 + cax.bbox.y1) / 2)))
        self.assertEqual(host._two_d_band_target_at_canvas_pos(pos), ("background", None))

        fig2 = Figure()
        canvas2 = FigureCanvasAgg(fig2)
        ax2 = fig2.add_subplot(111)
        record = {"region_id": "7", "name": "R7", "label": "R7", "status": "ready",
                  "level_info": level_info((0., 1.), (0, 50, 100))}
        bars = draw_region_colorbars_2d(ax2, [record], "magma")
        canvas2.draw()
        region_cax = bars[0].ax
        self.assertEqual(getattr(region_cax, "_arpes_band_target", None), ("region", "7"))
        event = SimpleNamespace(canvas=canvas2, x=(region_cax.bbox.x0 + region_cax.bbox.x1) / 2,
                                y=(region_cax.bbox.y0 + region_cax.bbox.y1) / 2,
                                inaxes=ax2, button=3, xdata=0.0, ydata=0.0)
        self.assertEqual(host._two_d_band_target_at_event(event), ("region", "7"))
        host.ax_2d = ax2
        host._pick_enhancement_region(event)
        self.assertEqual(host._band_context_target, ("region", "7"))
        view2 = _CanvasView(canvas2)
        host.canvas_2d = view2
        pos = SimpleNamespace(
            x=lambda: int(round((region_cax.bbox.x0 + region_cax.bbox.x1) / 2)),
            y=lambda: int(round(view2.height() - (region_cax.bbox.y0 + region_cax.bbox.y1) / 2)))
        self.assertEqual(host._two_d_band_target_at_canvas_pos(pos), ("region", "7"))

    def test_three_d_scalar_bar_hit_returns_region_and_background(self):
        host = self.host
        region_bar = _BandScalarBar((0.02, 0.08), (0.06, 0.26))
        global_bar = _BandScalarBar((0.88, 0.08), (0.08, 0.84))
        host.plotter = SimpleNamespace(
            renderer=_BandRenderer((100, 100), (0, 0)),
            scalar_bars={"Intensity": global_bar},
        )
        host._enhancement_scalar_bar_records = [
            {"title": "R1", "region_id": "1", "actor": region_bar}]
        self.assertEqual(host._three_d_band_target_at_display(5, 20), ("region", "1"))
        self.assertEqual(host._three_d_band_target_at_display(92, 50), ("background", None))
        self.assertIsNone(host._three_d_band_target_at_display(50, 50))
        # A region whose title collides with the global bar must not shadow it.
        host._enhancement_scalar_bar_records = [
            {"title": "Intensity", "region_id": "1", "actor": global_bar}]
        self.assertEqual(host._three_d_band_target_at_display(92, 50), ("background", None))
        # Two regions sharing one PyVista actor (duplicate label) target the
        # region whose bar was actually created first.
        host._enhancement_scalar_bar_records = [
            {"title": "R1", "region_id": "1", "actor": region_bar},
            {"title": "R1", "region_id": "2", "actor": region_bar},
        ]
        self.assertEqual(host._three_d_band_target_at_display(5, 20), ("region", "1"))
        host._enhancement_scalar_bar_records = [
            {"title": "R1", "region_id": "1", "actor": region_bar}]
        top = _BandScalarBar((0.02, 0.08), (0.06, 0.26))
        host._enhancement_scalar_bar_records.append(
            {"title": "R2", "region_id": "2", "actor": top})
        self.assertEqual(host._three_d_band_target_at_display(5, 20), ("region", "2"))

    def test_menu_actions_are_placed_first_and_paste_tracks_clipboard(self):
        host = self.host
        menu = QMenu()
        menu.addAction("原有入口")
        self.assertTrue(host._append_band_actions(menu, ("region", "1")))
        actions = menu.actions()
        self.assertEqual([action.text() for action in actions[:3]],
                         ["复制色带", "粘贴色带", ""])
        self.assertFalse(actions[1].isEnabled())
        actions[0].trigger()
        self.assertTrue(host._color_band_clipboard)
        menu2 = QMenu()
        menu2.addAction("原有入口")
        host._append_band_actions(menu2, ("region", "1"))
        self.assertTrue(menu2.actions()[1].isEnabled())
        menu.deleteLater()
        menu2.deleteLater()
