import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
from bandscope.ui.page_data_process_v2 import DataProcessPage
from bandscope.ui.page_render_control import RenderControlPage
from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.ui.settings_popups import DenoiseSettingsPopup
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
        expected = page.export_state()

        page.s_low.setValue(0)
        page.s_gamma.setValue(0)
        page.s_up.setValue(0)
        page.combo_n1.setCurrentIndex(0)
        page.restore_state(expected)

        self.assertEqual(page.export_state(), expected)

    def test_canvas_bar_and_data_state_round_trip_preserves_values(self):
        bar = TimelineBar()
        bar.slider_time.setRange(0, 12)
        bar.slider_time.setValue(7)
        bar.switch_axes.setChecked(False)
        bar_state = bar.export_state()

        bar.slider_time.setValue(0)
        bar.switch_axes.setChecked(True)
        bar.restore_state(bar_state)
        self.assertEqual(bar.export_state(), bar_state)

        data_page = DataProcessPage()
        data_page.s_t_low.setRange(0, 12)
        data_page.s_t_up.setRange(0, 12)
        data_page.s_t_low.setValue(2)
        data_page.s_t_up.setValue(9)
        data_page.combo_other.setCurrentIndex(2)
        data_state = data_page.export_state()

        data_page.s_t_low.setValue(0)
        data_page.s_t_up.setValue(0)
        data_page.combo_other.setCurrentIndex(0)
        data_page.restore_state(data_state)
        self.assertEqual(data_page.export_state(), data_state)

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

    def test_rotation_input_preserves_precision_and_live_preview_text(self):
        bar = TimelineBar()
        observed = []
        bar.edit_rotation.textChanged.connect(observed.append)
        angle = 12.3456789012
        bar.set_rotation_angle(angle)
        self.assertAlmostEqual(bar.get_rotation_angle(), angle, places=10)
        self.assertEqual(observed[-1], "12.3456789012")
        saved = bar.export_state()
        bar.set_rotation_angle(0)
        self.assertEqual(bar.edit_rotation.text(), "0.0")
        bar.restore_state(saved)
        self.assertAlmostEqual(bar.get_rotation_angle(), angle, places=10)

    def test_waterfall_step_controls_moved_out_of_the_shared_page(self):
        # 瀑布图步长已随插件迁移：空白控制页不再保留步长控件与状态，
        # 相关编辑与默认值由「插件管理 → 插件设置」里的插件面板负责。
        blank = BlankControlPage()
        self.assertFalse(hasattr(blank, "waterfall_step_box"))
        self.assertNotIn("waterfall", blank.export_state())

    def test_denoise_pages_share_defaults_and_options(self):
        blank = BlankControlPage()
        popup = DenoiseSettingsPopup(blank)

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
