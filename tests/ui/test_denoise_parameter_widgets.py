import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.processing.denoise_config import (
    DEFAULT_GAUSSIAN_PARAMS,
    DEFAULT_LOW_RANK_PARAMS,
    DEFAULT_MEDIAN_PARAMS,
    DEFAULT_NLMEANS_PARAMS,
    DEFAULT_SG_PARAMS,
    DEFAULT_WAVELET_PARAMS,
    DENOISE_METHODS,
    GAUSSIAN_METHOD_NAME,
    LOW_RANK_METHOD_NAME,
    MEDIAN_METHOD_NAME,
    NLM_METHOD_NAME,
    SG_METHOD_NAME,
    WAVELET_METHOD_NAME,
)
from bandscope.ui.blank_control_page import BlankControlPage
from bandscope.ui.page_render_control import RenderControlPage


configure_qt_plugin_path()


class DenoiseParameterWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.page = BlankControlPage()
        self.addCleanup(self.page.deleteLater)

    def test_all_method_groups_expose_contract_defaults(self):
        page = self.page
        for name in (
            "group_gaussian",
            "group_median",
            "group_savgol",
            "group_wavelet",
            "group_nlmeans",
            "group_low_rank",
        ):
            with self.subTest(group=name):
                self.assertTrue(hasattr(page, name))

        self.assertEqual(page.get_gaussian_params(), DEFAULT_GAUSSIAN_PARAMS)
        self.assertEqual(page.get_median_params(), DEFAULT_MEDIAN_PARAMS)
        self.assertEqual(page.get_savgol_params(), DEFAULT_SG_PARAMS)
        self.assertEqual(page.get_wavelet_params(), DEFAULT_WAVELET_PARAMS)
        self.assertEqual(page.get_nlmeans_params(), DEFAULT_NLMEANS_PARAMS)
        self.assertEqual(page.get_low_rank_params(), DEFAULT_LOW_RANK_PARAMS)
        self.assertEqual(
            set(page.export_state()),
            {"gaussian", "median", "sg", "wavelet", "nlmeans", "low_rank", "second_derivative"},
        )

    def test_ranges_steps_and_units_are_visible(self):
        page = self.page
        for spin_box in (
            page.gaussian_sigma_kx_box,
            page.gaussian_sigma_ky_box,
            page.gaussian_sigma_e_box,
        ):
            self.assertEqual(spin_box.minimum(), 0.0)
            self.assertEqual(spin_box.maximum(), 20.0)
            self.assertEqual(spin_box.singleStep(), 0.1)
            self.assertIn("采样点", spin_box.toolTip())
            self.assertIn("0 表示不平滑", spin_box.toolTip())

        for spin_box in (
            page.median_size_kx_box,
            page.median_size_ky_box,
            page.median_size_e_box,
        ):
            self.assertEqual(spin_box.minimum(), 1)
            self.assertEqual(spin_box.maximum(), 31)
            self.assertEqual(spin_box.singleStep(), 2)
            self.assertIn("奇数", spin_box.toolTip())
            self.assertIn("1 表示不平滑", spin_box.toolTip())

        self.assertEqual(page.nlmeans_patch_size_box.minimum(), 1)
        self.assertEqual(page.nlmeans_patch_size_box.maximum(), 15)
        self.assertEqual(page.nlmeans_patch_size_box.singleStep(), 2)
        self.assertEqual(page.nlmeans_patch_distance_box.minimum(), 1)
        self.assertEqual(page.nlmeans_patch_distance_box.maximum(), 20)
        self.assertGreater(page.nlmeans_h_scale_box.minimum(), 0.0)
        self.assertIn("无量纲", page.nlmeans_h_scale_box.toolTip())
        self.assertIn("采样点", page.nlmeans_patch_size_box.toolTip())
        self.assertIn("采样点", page.nlmeans_patch_distance_box.toolTip())
        self.assertGreaterEqual(page.low_rank_rank_box.minimum(), 1)
        self.assertIn("数据形状", page.low_rank_rank_box.toolTip())

        for spin_box in (page.wavelet_noise_sigma_box, page.nlmeans_noise_sigma_box):
            self.assertEqual(spin_box.minimum(), 0.0)
            self.assertIn("自动", spin_box.toolTip())
            self.assertIn("数据强度", spin_box.toolTip())
        self.assertIn("MAD", page.wavelet_noise_sigma_box.toolTip())
        self.assertEqual(page.wavelet_combo._title, "小波基")
        self.assertEqual(page.wavelet_level_box.title(), "分解层数")
        self.assertEqual(page.wavelet_threshold_rule_combo._title, "阈值规则")
        self.assertEqual(page.wavelet_threshold_mode_combo._title, "阈值模式")

    def test_gaussian_and_median_constraints(self):
        page = self.page
        page.gaussian_sigma_kx_box.setValue(-2.0)
        page.gaussian_sigma_ky_box.setValue(2.5)
        page.gaussian_sigma_e_box.setValue(30.0)
        self.assertEqual(
            page.get_gaussian_params(),
            {"sigma_kx": 0.0, "sigma_ky": 2.5, "sigma_e": 20.0},
        )

        page.median_size_kx_box.setValue(1)
        page.median_size_ky_box.setValue(4)
        page.median_size_e_box.setValue(98)
        self.assertEqual(
            page.get_median_params(),
            {"size_kx": 1, "size_ky": 5, "size_e": 31},
        )

    def test_nlmeans_and_low_rank_constraints(self):
        page = self.page
        page.nlmeans_patch_size_box.setValue(6)
        page.nlmeans_patch_distance_box.setValue(0)
        page.nlmeans_h_scale_box.setValue(0.0)
        page.nlmeans_noise_sigma_box.setValue(-1.0)
        page.low_rank_rank_box.setValue(0)

        self.assertEqual(
            page.get_nlmeans_params(),
            {
                "patch_size": 7,
                "patch_distance": 1,
                "h_scale": page.nlmeans_h_scale_box.minimum(),
                "noise_sigma": 0.0,
            },
        )
        self.assertGreater(page.get_nlmeans_params()["h_scale"], 0.0)
        self.assertEqual(page.get_low_rank_params(), {"rank": 1})

        page.nlmeans_patch_size_box.setValue(98)
        page.nlmeans_patch_distance_box.setValue(99)
        self.assertEqual(page.get_nlmeans_params()["patch_size"], 15)
        self.assertEqual(page.get_nlmeans_params()["patch_distance"], 20)

    def test_restore_clamps_saved_parameters_to_current_limits(self):
        self.page.restore_state(
            {
                "median": {"size_kx": 99, "size_ky": 30, "size_e": -2},
                "nlmeans": {"patch_size": 99, "patch_distance": 999},
            }
        )

        self.assertEqual(
            self.page.get_median_params(),
            {"size_kx": 31, "size_ky": 31, "size_e": 1},
        )
        self.assertEqual(self.page.get_nlmeans_params()["patch_size"], 15)
        self.assertEqual(self.page.get_nlmeans_params()["patch_distance"], 20)

    def test_savgol_and_second_derivative_behavior_is_preserved(self):
        page = self.page
        page.sg_window_box.setValue(4)
        page.sg_polyorder_box.setValue(4)
        self.assertEqual(
            page.get_savgol_params(),
            {"window_length": 5, "polyorder": 4, "smoothing_axis": "e"},
        )
        page.sg_window_box.setValue(3)
        self.assertEqual(page.get_savgol_params()["polyorder"], 2)
        page.sg_axis_combo.setCurrentIndex(page.sg_axis_combo.findText("Kx轴"))
        self.assertEqual(page.get_savgol_params()["smoothing_axis"], "kx")
        with self.assertRaisesRegex(ValueError, "滑动窗口长度"):
            page.get_savgol_params((2, 9, 11, 1))

        page.sd_threshold_box.setValue(0.35)
        self.assertEqual(page.get_second_derivative_params(), {"threshold": 0.35})

    def test_full_state_round_trip_includes_new_parameters(self):
        page = self.page
        page.gaussian_sigma_kx_box.setValue(0.0)
        page.gaussian_sigma_ky_box.setValue(2.5)
        page.gaussian_sigma_e_box.setValue(20.0)
        page.median_size_kx_box.setValue(1)
        page.median_size_ky_box.setValue(5)
        page.median_size_e_box.setValue(31)
        page.sg_window_box.setValue(9)
        page.sg_polyorder_box.setValue(3)
        page.sg_axis_combo.setCurrentIndex(page.sg_axis_combo.findText("Kx轴"))
        page.wavelet_combo.setCurrentIndex(page.wavelet_combo.findText("sym4"))
        page.wavelet_level_box.setValue(4)
        page.wavelet_threshold_rule_combo.setCurrentIndex(
            page.wavelet_threshold_rule_combo.findText("sure")
        )
        page.wavelet_threshold_mode_combo.setCurrentIndex(
            page.wavelet_threshold_mode_combo.findText("hard")
        )
        page.wavelet_strength_box.setValue(0.75)
        page.wavelet_noise_sigma_box.setValue(2.5)
        page.nlmeans_patch_size_box.setValue(7)
        page.nlmeans_patch_distance_box.setValue(9)
        page.nlmeans_h_scale_box.setValue(1.75)
        page.nlmeans_noise_sigma_box.setValue(0.25)
        page.low_rank_rank_box.setValue(8)
        page.sd_threshold_box.setValue(0.2)
        saved = page.export_state()

        restored = BlankControlPage()
        self.addCleanup(restored.deleteLater)
        restored.restore_state(saved)

        self.assertEqual(restored.export_state(), saved)
        self.assertEqual(restored.get_wavelet_params()["noise_sigma"], 2.5)
        self.assertEqual(restored.get_nlmeans_params()["patch_size"], 7)
        self.assertEqual(restored.get_low_rank_params(), {"rank": 8})
        self.assertEqual(restored.get_second_derivative_params(), {"threshold": 0.2})

    def test_sg_restore_accepts_axis_keys_and_display_labels(self):
        cases = (
            ("e", "E轴"),
            ("kx", "Kx轴"),
            ("ky", "Ky轴"),
            ("E轴", "E轴"),
            ("Kx轴", "Kx轴"),
            ("Ky轴", "Ky轴"),
        )
        for saved_axis, expected_label in cases:
            with self.subTest(saved_axis=saved_axis):
                self.page.restore_state({"sg": {"smoothing_axis": saved_axis}})
                self.assertEqual(self.page.sg_axis_combo.currentText(), expected_label)
                self.assertEqual(
                    self.page.get_savgol_params()["smoothing_axis"],
                    self.page.SG_AXIS_LABEL_TO_KEY[expected_label],
                )

    def test_render_restore_migrates_aliases_and_clears_unknown_methods(self):
        page = RenderControlPage()
        self.addCleanup(page.deleteLater)
        aliases = (
            ("频域平滑", GAUSSIAN_METHOD_NAME),
            ("滑动平均", MEDIAN_METHOD_NAME),
            ("Savitzky-Golay婊ゆ尝", SG_METHOD_NAME),
            ("灏忔尝鍘诲櫔", WAVELET_METHOD_NAME),
            ("贝叶斯去噪", NLM_METHOD_NAME),
        )
        for alias, canonical_name in aliases:
            with self.subTest(alias=alias):
                page.restore_state({"combo_n1": alias})
                self.assertEqual(page.combo_n1.currentText(), canonical_name)

        for combo in (page.combo_n1, page.combo_n2, page.combo_n3):
            combo.setCurrentIndex(combo.findText(LOW_RANK_METHOD_NAME))
        page.restore_state(
            {"combo_n1": "卡尔曼滤波", "combo_n2": "不存在的方法", "combo_n3": 42}
        )
        self.assertEqual(page.get_denoise_settings(), ["None", "None", "None"])

    def test_partial_legacy_restore_preserves_missing_fields(self):
        page = self.page
        page.gaussian_sigma_kx_box.setValue(3.5)
        page.median_size_ky_box.setValue(7)
        page.wavelet_noise_sigma_box.setValue(1.25)
        page.nlmeans_patch_distance_box.setValue(8)
        page.low_rank_rank_box.setValue(6)
        before = page.export_state()

        page.restore_state(
            {
                "sg": {"window_length": 7},
                "wavelet": {"level": 2},
                "second_derivative": {"threshold": 0.4},
            }
        )

        after = page.export_state()
        self.assertEqual(after["gaussian"], before["gaussian"])
        self.assertEqual(after["median"], before["median"])
        self.assertEqual(after["nlmeans"], before["nlmeans"])
        self.assertEqual(after["low_rank"], before["low_rank"])
        self.assertEqual(after["wavelet"]["noise_sigma"], 1.25)
        self.assertEqual(after["sg"]["window_length"], 7)
        self.assertEqual(after["wavelet"]["level"], 2)
        self.assertEqual(after["second_derivative"], {"threshold": 0.4})

    def test_pipeline_uses_canonical_names_and_current_parameters(self):
        specs = self.page.build_method_specs(DENOISE_METHODS, data_shape=(9, 9, 11, 2))

        self.assertEqual(specs[0], "None")
        expected = (
            (GAUSSIAN_METHOD_NAME, self.page.get_gaussian_params()),
            (MEDIAN_METHOD_NAME, self.page.get_median_params()),
            (SG_METHOD_NAME, self.page.get_savgol_params()),
            (WAVELET_METHOD_NAME, self.page.get_wavelet_params()),
            (NLM_METHOD_NAME, self.page.get_nlmeans_params()),
            (LOW_RANK_METHOD_NAME, self.page.get_low_rank_params()),
        )
        self.assertEqual(
            specs[1:],
            [{"label": label, "params": params} for label, params in expected],
        )

    def test_pipeline_accepts_historical_aliases(self):
        specs = self.page.build_method_specs(
            ["频域平滑", "滑动平均", "Savitzky-Golay婊ゆ尝", "灏忔尝鍘诲櫔", "贝叶斯去噪"],
            data_shape=(9, 9, 11, 2),
        )

        self.assertEqual(
            [spec["label"] for spec in specs],
            [
                GAUSSIAN_METHOD_NAME,
                MEDIAN_METHOD_NAME,
                SG_METHOD_NAME,
                WAVELET_METHOD_NAME,
                NLM_METHOD_NAME,
            ],
        )
        with self.assertRaises(ValueError):
            self.page.build_method_specs(["卡尔曼滤波"])


if __name__ == "__main__":
    unittest.main()
