import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from blank_control_page import BlankControlPage
from denoise_config import (
    DEFAULT_SG_PARAMS,
    DEFAULT_WAVELET_PARAMS,
    SG_AXIS_LABEL_TO_KEY,
    THRESHOLD_MODE_OPTIONS,
    THRESHOLD_RULE_OPTIONS,
    WAVELET_OPTIONS,
)
from page_data_process_v2 import DataProcessPage
from page_image_control_v2 import ImageControlPage
from page_render_control import RenderControlPage
from qt_bootstrap import configure_qt_plugin_path
from settings_popups import DenoiseSettingsPopup, WaterfallSettingsPopup


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

    def test_image_and_data_state_round_trip_preserves_values(self):
        image_page = ImageControlPage()
        image_page.slider_time.setRange(0, 12)
        image_page.slider_time.setValue(7)
        image_page.switch_axes.setChecked(False)
        image_state = image_page.export_state()

        image_page.slider_time.setValue(0)
        image_page.switch_axes.setChecked(True)
        image_page.restore_state(image_state)
        self.assertEqual(image_page.export_state(), image_state)

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

    def test_waterfall_popup_forwards_one_edit_to_the_shared_control(self):
        blank = BlankControlPage()
        popup = WaterfallSettingsPopup(blank)
        received = []
        blank.waterfall_step_box.editingFinished.connect(lambda: received.append(True))
        popup.waterfall_step_box.setValue(0.25)

        popup._on_step_changed()

        self.assertAlmostEqual(blank.get_waterfall_step(), 0.25)
        self.assertEqual(received, [True])

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
