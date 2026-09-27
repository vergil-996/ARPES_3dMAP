"""「平带增强」面板：增删条目、单独调节、无效输入与提示（计划 §2、§8）。

控件行为用离屏 Qt 直接驱动：这些槽函数由用户交互触发，任何异常都会让整个
应用崩溃，所以必须逐个覆盖。
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.extensions.api import SOURCE_FILE, SOURCE_INDEX, EnergyAxisSpec
from plugins.flat_band_opacity.controls import FlatBandPanel
from plugins.flat_band_opacity.effect import (
    DEFAULT_BACKGROUND,
    DEFAULT_GAIN,
    EffectState,
    MAX_BANDS,
    suggested_fwhm,
)


def axis(values=None, unit="eV", source=SOURCE_FILE):
    values = np.linspace(-1.0, 1.0, 101) if values is None else np.asarray(values, float)
    return EnergyAxisSpec(
        values=values,
        unit=unit,
        source=source,
        roi_range=(float(values.min()), float(values.max())),
        full_range=(float(values.min()), float(values.max())),
    )


class PanelTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bandscope.app.qt_bootstrap import configure_qt_plugin_path

        configure_qt_plugin_path()
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self, state=None, spec=None):
        state = state if state is not None else EffectState()
        panel = FlatBandPanel(state)
        self.addCleanup(panel.deleteLater)
        panel.sync_context(_Context(spec if spec is not None else axis()))
        changes = []
        panel.changed.connect(lambda: changes.append(True))
        panel._changes = changes
        return panel


class _Context:
    def __init__(self, spec):
        self.energy = spec
        self.view = "3d"
        self.page_id = "p"
        self.display_e_flip = False


class EmptyStateTests(PanelTestCase):
    def test_starts_empty_with_a_hint(self):
        panel = self.make_panel()
        self.assertEqual(panel.state.bands, [])
        self.assertIn("添加平带", panel.lbl_message.text())
        self.assertTrue(panel.scroll.isHidden())

    def test_first_band_gets_the_suggested_thickness(self):
        panel = self.make_panel()
        panel.add_band()
        self.assertEqual(len(panel.state.bands), 1)
        band = panel.state.bands[0]
        self.assertIsNone(band.center)
        self.assertFalse(band.enabled)
        self.assertAlmostEqual(band.fwhm, suggested_fwhm(axis()))
        self.assertAlmostEqual(band.gain, DEFAULT_GAIN)

    def test_blank_center_does_not_produce_an_effect(self):
        panel = self.make_panel()
        panel.add_band()
        band = panel.state.bands[0]
        self.assertFalse(band.active)
        # 打开开关但没填中心：原因是没填中心，而不是“未启用”。
        band.enabled = True
        self.assertEqual(band.invalid_reason(), "请填写有效的能量中心")


class EditingTests(PanelTestCase):
    def test_valid_center_enables_the_row_once(self):
        panel = self.make_panel()
        panel.add_band()
        row = panel._rows[panel.state.bands[0].band_id]
        row.field_center.setText("0.25")
        row.field_center.edited.emit()
        self.assertTrue(panel.state.bands[0].enabled)
        self.assertAlmostEqual(panel.state.bands[0].center, 0.25)
        self.assertTrue(panel._changes)

        # 再关掉开关后，不会因为继续编辑中心又被自动打开。
        row.switch.setChecked(False)
        row.field_center.setText("0.4")
        row.field_center.edited.emit()
        self.assertFalse(panel.state.bands[0].enabled)
        self.assertAlmostEqual(panel.state.bands[0].center, 0.4)

    def test_invalid_text_keeps_the_last_valid_value(self):
        panel = self.make_panel()
        panel.add_band()
        row = panel._rows[panel.state.bands[0].band_id]
        row.field_center.setText("0.3")
        row.field_center.edited.emit()

        for junk in ("", "abc", "-", "1e", ".", "--"):
            row.field_center.setText(junk)
            row.field_center.edited.emit()
            self.assertAlmostEqual(panel.state.bands[0].center, 0.3)
        self.assertAlmostEqual(panel.state.bands[0].center, 0.3)

    def test_non_positive_thickness_is_ignored(self):
        panel = self.make_panel()
        panel.add_band()
        row = panel._rows[panel.state.bands[0].band_id]
        row.field_fwhm.setText("0.05")
        row.field_fwhm.edited.emit()
        self.assertAlmostEqual(panel.state.bands[0].fwhm, 0.05)

        row.field_fwhm.setText("0")
        row.field_fwhm.edited.emit()
        self.assertAlmostEqual(panel.state.bands[0].fwhm, 0.05)

    def test_gain_is_clamped_to_the_documented_range(self):
        panel = self.make_panel()
        panel.add_band()
        row = panel._rows[panel.state.bands[0].band_id]
        row.field_gain.setText("99")
        row.field_gain.edited.emit()
        self.assertAlmostEqual(panel.state.bands[0].gain, 5.0)

        row.field_gain.setText("-3")
        row.field_gain.edited.emit()
        self.assertAlmostEqual(panel.state.bands[0].gain, 0.0)


class ListTests(PanelTestCase):
    def test_delete_middle_row_keeps_the_others(self):
        state = EffectState()
        panel = self.make_panel(state)
        for _ in range(3):
            panel.add_band()
        ids = [band.band_id for band in state.bands]
        for band, center in zip(state.bands, (-0.5, 0.0, 0.5)):
            band.center = center
            band.enabled = True

        panel.remove_band(ids[1])
        self.assertEqual([band.band_id for band in state.bands], [ids[0], ids[2]])
        self.assertAlmostEqual(state.bands[0].center, -0.5)
        self.assertAlmostEqual(state.bands[1].center, 0.5)
        self.assertNotIn(ids[1], panel._rows)

    def test_delete_last_row_removes_the_effect(self):
        state = EffectState()
        panel = self.make_panel(state)
        panel.add_band()
        band = state.bands[0]
        band.center = 0.0
        band.enabled = True
        panel.remove_band(band.band_id)
        self.assertEqual(state.bands, [])
        self.assertIn("尚未添加平带", panel.lbl_message.text())

    def test_many_rows_scroll_inside_the_card(self):
        panel = self.make_panel()
        for _ in range(12):
            panel.add_band()
        # 面板自身没有 show()，用 isHidden() 看显隐标志而不是 isVisible()。
        self.assertFalse(panel.scroll.isHidden())
        self.assertEqual(panel.scroll.height(), panel.LIST_MAX_HEIGHT)

    def test_band_count_is_capped(self):
        panel = self.make_panel()
        for _ in range(MAX_BANDS + 3):
            panel.add_band()
        self.assertEqual(len(panel.state.bands), MAX_BANDS)
        self.assertIn("最多支持", panel.lbl_message.text())


class SwitchAndResetTests(PanelTestCase):
    def test_master_switch_keeps_every_parameter(self):
        state = EffectState()
        panel = self.make_panel(state)
        panel.add_band()
        band = state.bands[0]
        band.center = 0.1
        band.enabled = True

        panel.switch_enabled.setChecked(False)
        self.assertFalse(state.enabled)
        self.assertIn("原图", panel.lbl_message.text())
        self.assertEqual(len(state.bands), 1)
        self.assertAlmostEqual(state.bands[0].center, 0.1)

        panel.switch_enabled.setChecked(True)
        self.assertTrue(state.enabled)

    def test_reset_keeps_count_and_centers(self):
        state = EffectState(background=0.9)
        panel = self.make_panel(state)
        panel.add_band()
        panel.add_band()
        for band, center in zip(state.bands, (-0.4, 0.6)):
            band.center = center
            band.enabled = True
            band.gain = 4.0
            band.fwhm = 0.001

        panel.reset_display_parameters()
        self.assertAlmostEqual(state.background, DEFAULT_BACKGROUND)
        self.assertEqual(len(state.bands), 2)
        self.assertAlmostEqual(state.bands[0].center, -0.4)
        self.assertAlmostEqual(state.bands[1].center, 0.6)
        self.assertAlmostEqual(state.bands[0].gain, DEFAULT_GAIN)
        self.assertAlmostEqual(state.bands[0].fwhm, suggested_fwhm(axis()))
        self.assertAlmostEqual(panel.slider_background.value(), 20)

    def test_background_slider_writes_a_fraction(self):
        state = EffectState()
        panel = self.make_panel(state)
        panel.slider_background.setValue(55)
        self.assertAlmostEqual(state.background, 0.55)


class HintTests(PanelTestCase):
    def test_energy_outside_the_roi_is_reported_but_kept(self):
        state = EffectState()
        tight = axis(np.linspace(0.0, 1.0, 21))
        panel = self.make_panel(state, spec=tight)
        panel.add_band()
        row = panel._rows[state.bands[0].band_id]
        row.field_center.setText("-5")
        row.field_center.edited.emit()

        self.assertAlmostEqual(state.bands[0].center, -5.0)
        self.assertIn("不在当前能量范围", row.hint.text())
        self.assertIn("不在当前能量范围", panel.lbl_message.text())

    def test_unit_label_follows_the_axis(self):
        for spec, expected in (
            (axis(unit="eV"), "eV"),
            (axis(unit=None), "坐标值"),
            (axis(unit=None, source=SOURCE_INDEX), "index"),
        ):
            state = EffectState()
            panel = self.make_panel(state, spec=spec)
            panel.add_band()
            row = panel._rows[state.bands[0].band_id]
            self.assertIn(expected, row.lbl_center.text())
            self.assertNotIn("eV", row.lbl_center.text()) if expected != "eV" else None

    def test_all_disabled_reports_the_original_image(self):
        state = EffectState()
        panel = self.make_panel(state)
        panel.add_band()
        state.bands[0].center = 0.0
        state.bands[0].enabled = False
        panel._update_status()
        self.assertIn("原图", panel.lbl_message.text())


class ShowAndResizeTests(PanelTestCase):
    """真正 show 出来并处理事件。

    回归背景：面板原本用 siui 的 SiScrollArea，而它在 resizeEvent 里直接除以
    附件尺寸；列表为空时附件是空的，窗口一显示就 ZeroDivisionError。这个异常
    出在 Qt 槽里，PyQt 会直接 abort 进程（退出码 0xC0000409），表现为"启动即
    闪退、没有任何报错"。只构造不 show 的测试抓不到它。
    """

    def _show_and_settle(self, widget, size=(360, 640)):
        widget.resize(*size)
        widget.show()
        QApplication.processEvents()
        widget.resize(size[0], size[1] + 40)
        QApplication.processEvents()

    def test_empty_panel_survives_show_and_resize(self):
        panel = self.make_panel()
        self._show_and_settle(panel)
        self.assertTrue(panel.scroll.isHidden())
        panel.hide()

    def test_panel_with_rows_survives_show_and_resize(self):
        panel = self.make_panel()
        for _ in range(6):
            panel.add_band()
        self._show_and_settle(panel)
        self.assertFalse(panel.scroll.isHidden())
        self.assertGreater(panel.scroll.height(), 0)
        panel.hide()

    def test_removing_every_row_does_not_resize_the_list_to_zero(self):
        panel = self.make_panel()
        for _ in range(3):
            panel.add_band()
        self._show_and_settle(panel)
        for band_id in [band.band_id for band in panel.state.bands]:
            panel.remove_band(band_id)
            QApplication.processEvents()
        self.assertTrue(panel.scroll.isHidden())
        self.assertGreater(panel.scroll.height(), 0)
        panel.hide()

    def test_scroll_area_is_not_the_siui_one(self):
        """SiUI 的滚动区在附件为空时会除零，这个列表不能用它。"""
        from siui.components.widgets import SiScrollArea

        panel = self.make_panel()
        self.assertNotIsInstance(panel.scroll, SiScrollArea)


class StateRestoreTests(PanelTestCase):
    def test_refresh_from_state_rebuilds_the_rows(self):
        state = EffectState()
        panel = self.make_panel(state)
        state.bands = []
        from plugins.flat_band_opacity.effect import FlatBand

        for center in (-0.5, 0.0, 0.5):
            band = FlatBand()
            band.center = center
            band.fwhm = 0.1
            band.gain = 1.0
            state.bands.append(band)
        panel.refresh_from_state(axis())
        self.assertEqual(len(panel._rows), 3)
        for band in state.bands:
            row = panel._rows[band.band_id]
            self.assertAlmostEqual(row.field_center.value(), band.center)

    def test_restore_does_not_emit_changes(self):
        state = EffectState()
        panel = self.make_panel(state)
        changes = []
        panel.changed.connect(lambda: changes.append(True))
        state.bands = []
        panel.refresh_from_state(axis())
        # 回填是宿主驱动的，不应再触发一次刷新请求。
        self.assertEqual(changes, [])


if __name__ == "__main__":
    unittest.main()
