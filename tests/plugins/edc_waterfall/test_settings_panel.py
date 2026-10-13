# -*- coding: utf-8 -*-
"""插件设置面板（Qt 离屏）：状态读写、模式联动与插件往返。"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from plugins.edc_waterfall import edc
from plugins.edc_waterfall.entry import EdcWaterfallPlugin
from plugins.edc_waterfall.settings_panel import EdcSettingsPanel

configure_qt_plugin_path()


class SettingsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_manual_mode_enables_the_step_box_and_reports_state(self):
        panel = EdcSettingsPanel({"k_step_mode": edc.MODE_AUTO, "k_step": 0.02})
        self.assertFalse(panel.box_step.isEnabled())

        panel.combo_mode.setCurrentIndex(panel.combo_mode.findData(edc.MODE_MANUAL))
        self.assertTrue(panel.box_step.isEnabled())
        panel.box_step.setValue(0.25)
        self.assertEqual(
            panel.export_state(), {"k_step_mode": "manual", "k_step": 0.25}
        )

    def test_panel_forwards_one_edit_to_the_plugin_state(self):
        plugin = EdcWaterfallPlugin()
        received = []
        panel = EdcSettingsPanel(plugin.export_settings(), on_changed=received.append)
        plugin._panel = panel
        panel.combo_mode.setCurrentIndex(panel.combo_mode.findData(edc.MODE_MANUAL))
        panel.box_step.setValue(0.4)

        self.assertTrue(received)
        state = plugin.export_settings()
        self.assertEqual(state["k_step_mode"], "manual")
        self.assertAlmostEqual(float(state["k_step"]), 0.4)

    def test_restore_clamps_and_tolerates_missing_fields(self):
        panel = EdcSettingsPanel()
        panel.restore({"k_step_mode": "manual", "k_step": 999})
        self.assertEqual(panel.export_state()["k_step"], edc.MAX_STEP)
        panel.restore({"k_step": 0.0})
        self.assertEqual(panel.export_state()["k_step_mode"], edc.MODE_AUTO)
        self.assertEqual(panel.export_state()["k_step"], edc.MIN_STEP)


if __name__ == "__main__":
    unittest.main()
