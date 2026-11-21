import copy
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.ui.blank_control_page import BlankControlPage
from bandscope.processing.denoise_config import (
    DEFAULT_SG_PARAMS,
    DEFAULT_WAVELET_PARAMS,
    SG_AXIS_LABEL_TO_KEY,
    THRESHOLD_MODE_OPTIONS,
    THRESHOLD_RULE_OPTIONS,
    WAVELET_OPTIONS,
)
from bandscope.ui.page_render_control import RenderControlPage
from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.ui.settings_popups import DenoiseSettingsPanel
from bandscope.ui.timeline_bar import TimelineBar


configure_qt_plugin_path()


class ControlPageStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_render_state_round_trip_preserves_controls(self):
        page = RenderControlPage()
        page.s_low.setValue(13)
        page.s_gamma.setValue(47)
        page.s_up.setValue(91)
        page.combo_n1.setCurrentIndex(3)
        page.colormap_selector.set_colormap("coolwarm")
        expected = page.export_state()

        page.s_low.setValue(0)
        page.s_gamma.setValue(0)
        page.s_up.setValue(0)
        page.combo_n1.setCurrentIndex(0)
        page.colormap_selector.set_colormap("magma")
        page.restore_state(expected)

        self.assertEqual(page.export_state(), expected)
        self.assertEqual(page.get_selected_cmap(), "coolwarm")
        self.assertEqual(
            [slider._value_label.text() for slider in (page.s_low, page.s_gamma, page.s_up)],
            ["13", "47", "91"],
        )

    def test_canvas_bar_state_round_trip_preserves_values(self):
        bar = TimelineBar()
        bar.switch_axes.setChecked(False)
        bar_state = bar.export_state()

        bar.switch_axes.setChecked(True)
        bar.restore_state(bar_state)
        self.assertEqual(bar.export_state(), bar_state)

    def test_canvas_bar_switch_restore_moves_painted_thumb(self):
        bar = TimelineBar()
        # Simulate a user-toggle switch: logical state and painted thumb agree.
        bar.switch_axes.setChecked(True)
        bar.switch_axes.progress = 1.0
        state = bar.export_state()

        # Revert the switch; SiSwitchRefactor's setChecked only changes the
        # logical state, leaving the painted thumb out of sync.
        bar.switch_axes.setChecked(False)
        bar.switch_axes.progress = 0.0

        bar.restore_state(state)
        self.assertTrue(bar.switch_axes.isChecked())
        self.assertEqual(bar.switch_axes.progress, 1.0)

    def test_rotation_input_preserves_two_decimals_and_live_preview_text(self):
        bar = TimelineBar()
        observed = []
        bar.edit_rotation.textChanged.connect(observed.append)
        angle = 12.34
        bar.set_rotation_angle(angle)
        self.assertAlmostEqual(bar.get_rotation_angle(), angle, places=2)
        self.assertEqual(observed[-1], "12.34")
        saved = bar.export_state()
        bar.set_rotation_angle(0)
        self.assertEqual(bar.edit_rotation.text(), "0.0")
        bar.restore_state(saved)
        self.assertAlmostEqual(bar.get_rotation_angle(), angle, places=2)

    def test_legacy_processing_state_restores_single_render_page(self):
        from tests.support.view_recipe import control_window

        window = control_window()
        self.addCleanup(window.close)
        legacy = copy.deepcopy(window._capture_control_state())
        legacy["active_control_tab"] = 1
        legacy["data_process"] = {
            "combo_other": {"index": 2, "key": "builtin:edc_curve", "text": "单条 EDC 曲线"}
        }
        pages_before = list(window.left_workspace.page_specs)

        window.initial_control_state = legacy
        window._restore_initial_controls_after_load()

        self.assertEqual(window.page_container.count(), 1)
        self.assertEqual(window.page_container.currentIndex(), 0)
        self.assertIs(window.page_container.widget(0), window.page_render)
        self.assertFalse(hasattr(window, "btn_page2"))
        self.assertFalse(hasattr(window, "button_group"))
        self.assertFalse(hasattr(window, "_control_page_buttons"))
        self.assertFalse(hasattr(window, "page_data"))
        self.assertNotIn("data_process", window._capture_control_state())
        self.assertEqual(list(window.left_workspace.page_specs), pages_before)

    def test_ctrl_2_shortcut_is_removed(self):
        from tests.support.view_recipe import control_window

        window = control_window()
        self.addCleanup(window.close)
        shortcuts = {shortcut.key().toString() for shortcut in window._global_shortcuts}
        self.assertIn("Ctrl+1", shortcuts)
        self.assertNotIn("Ctrl+2", shortcuts)
        current_index = window.page_container.currentIndex()
        pages_before = list(window.left_workspace.page_specs)

        QTest.keyClick(window, Qt.Key_2, Qt.ControlModifier)
        QApplication.processEvents()

        self.assertEqual(window.page_container.currentIndex(), current_index)
        self.assertEqual(list(window.left_workspace.page_specs), pages_before)

    def test_waterfall_step_controls_moved_out_of_the_shared_page(self):
        # 瀑布图步长已随插件迁移：空白控制页不再保留步长控件与状态，
        # 相关编辑与默认值由「插件管理 → 插件设置」里的插件面板负责。
        blank = BlankControlPage()
        self.assertFalse(hasattr(blank, "waterfall_step_box"))
        self.assertNotIn("waterfall", blank.export_state())

    def test_colormap_selector_is_the_only_entry_in_the_levels_card(self):
        page = RenderControlPage()
        self.addCleanup(page.deleteLater)
        self.assertEqual(page.get_selected_cmap(), "magma")
        self.assertEqual(page.colormap_selector.current_colormap(), "magma")
        self.assertEqual(page.export_state()["combo_cmap"], "magma")
        for name in ("combo_cmap", "btn_apply_cmap", "grp_cmap"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(page, name))
        card_titles = [group.toggle_button.text() for group in page._adaptive_groups]
        self.assertNotIn("色带选择", card_titles)
        self.assertIn("色阶", card_titles)
        # 菜单首两项与 coolwarm 唯一性由 tests.ui.test_colormap_selector 覆盖。
        self.assertEqual(page.colormap_selector.options()[:2], ["magma", "coolwarm"])

    def test_card_titles_are_short(self):
        page = RenderControlPage()
        self.addCleanup(page.deleteLater)
        titles = [group.toggle_button.text() for group in page._adaptive_groups]
        self.assertEqual(titles[:4], ["视角", "色阶", "不透明度", "去噪"])

    def test_colormap_header_fits_the_longest_name_in_a_narrow_card(self):
        page = RenderControlPage()
        self.addCleanup(page.deleteLater)
        page.resize(336, 900)
        page.show()
        page.colormap_selector.set_colormap("twilight_shifted")
        selector = page.colormap_selector
        required = (
            selector.title_label.fontMetrics().horizontalAdvance(selector.title_label.text())
            + selector.name_label.fontMetrics().horizontalAdvance(selector.name_label.text())
            + selector.arrow_label.fontMetrics().horizontalAdvance(selector.arrow_label.text())
            + 3 * 6
        )
        self.assertGreaterEqual(selector.width(), required)
        self.assertLessEqual(selector.width(), page.width())
        self.assertEqual(selector.width(), page.colormap_range_editor.width())

    def test_denoise_pages_share_defaults_and_options(self):
        blank = BlankControlPage()
        popup = DenoiseSettingsPanel(blank)

        self.assertEqual(blank.get_savgol_params(), DEFAULT_SG_PARAMS)
        self.assertEqual(blank.get_wavelet_params(), DEFAULT_WAVELET_PARAMS)
        self.assertEqual(
            [blank.sg_axis_combo.itemText(i) for i in range(blank.sg_axis_combo.count())],
            list(SG_AXIS_LABEL_TO_KEY),
        )
        self.assertEqual(
            [popup.wavelet_combo.itemText(i) for i in range(popup.wavelet_combo.count())],
            list(WAVELET_OPTIONS),
        )
        self.assertEqual(
            [popup.wavelet_threshold_rule_combo.itemText(i)
             for i in range(popup.wavelet_threshold_rule_combo.count())],
            list(THRESHOLD_RULE_OPTIONS),
        )
        self.assertEqual(
            [popup.wavelet_threshold_mode_combo.itemText(i)
             for i in range(popup.wavelet_threshold_mode_combo.count())],
            list(THRESHOLD_MODE_OPTIONS),
        )


if __name__ == "__main__":
    unittest.main()
