# -*- coding: utf-8 -*-
"""截图样式面板：轴标签命名控件的草稿语义与持久化往返。

覆盖设计意图：默认不把软件自动轴名硬写进图片——面板打开时把当前自动
轴名带进输入框供改名，只有改动了才写入覆盖；清空表示"明确不要该轴标签"
（必须与"未设置=用自动轴名"区分开并持久化）；「自动」按钮撤销命名覆盖。
2D/1D 族是横轴 / 纵轴两个框，3D 族是 X / Y / E 三条轴名。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.exporting.publication_dialog import PublicationExportDialog
from bandscope.exporting.publication_export import commit_style, committed_style_for
from bandscope.exporting.publication_models import load_overrides
from tests.support.publication import _make_2d_snapshot

AUTO_X = "kx (Å⁻¹)"
AUTO_Y = "E"
# 3D 族的自动轴名按绘制顺序 X / Y / E，与 payload["axis_titles"] 一致
AUTO_3D = ("kx (Å⁻¹)", "ky (Å⁻¹)", "E (eV)")


def _make_3d_snapshot():
    """只用于控件语义的手工 3D 快照：本测试不渲染，故不需要真实 VTK 状态。"""
    snap = _make_2d_snapshot()
    snap.view = "3d"
    snap.view_family = "3d"
    snap.payload = dict(snap.payload, axis_titles=list(AUTO_3D))
    return snap


class _MemorySettings:
    """QSettings 的最小内存替身：只实现面板/持久化用到的部分。"""

    def __init__(self):
        self._data = {}

    def value(self, key, default=None, type=None):
        if key not in self._data:
            return default
        value = self._data[key]
        return type(value) if type is not None else value

    def setValue(self, key, value):
        self._data[key] = value

    def remove(self, key):
        self._data.pop(key, None)

    def clear(self):
        self._data.clear()


class _StubWindow(QWidget):
    """面板只需要 settings 与 QWidget 父级，不需要真实主窗口。"""

    def __init__(self, settings):
        super().__init__()
        self.settings = settings


class AxisLabelControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = _MemorySettings()
        self.window = _StubWindow(self.settings)
        self.dialog = PublicationExportDialog(self.window)
        # 直接装载草稿状态，绕开预览渲染（本测试只覆盖控件 ↔ 草稿语义）
        self._load_snapshot(_make_2d_snapshot(), "2d_open", "2d")

    def tearDown(self):
        self.dialog._debounce.stop()
        self.dialog.close()
        self.dialog.deleteLater()
        self.window.close()

    def _load_snapshot(self, snapshot, style_id, family):
        self.dialog.snapshot = snapshot
        self.dialog._draft_style_id = style_id
        self.dialog._committed_overrides = {style_id: {}}
        self.dialog._draft_overrides = {}
        self.dialog._load_output_options_ui()
        self.dialog._update_tune_availability()
        self.dialog._sync_tune_ui_from_draft()
        self.family = family

    def _collect(self):
        return self.dialog._collect_overrides_from_ui()

    # ------------------------------------------------------------ 2D / 1D

    def test_opens_with_auto_labels_prefilled_and_no_override(self):
        self.assertEqual(self.dialog.edit_axis_x.text(), AUTO_X)
        self.assertEqual(self.dialog.edit_axis_y.text(), AUTO_Y)
        self.assertEqual(self._collect(), {})

    def test_renaming_one_axis_stores_only_that_key(self):
        self.dialog.edit_axis_x.setText("Binding energy (eV)")
        self.assertEqual(self._collect(), {"xlabel_text": "Binding energy (eV)"})

    def test_typing_the_auto_label_again_keeps_settings_clean(self):
        self.dialog.edit_axis_x.setText("Fig. 3 x")
        self.dialog.edit_axis_x.setText(AUTO_X)
        self.assertEqual(self._collect(), {})

    def test_clearing_a_label_is_an_explicit_override(self):
        self.dialog.edit_axis_y.setText("")
        self.assertEqual(self._collect(), {"ylabel_text": ""})

    def test_auto_button_restores_all_auto_labels(self):
        self.dialog.edit_axis_x.setText("kx (1/Å)")
        self.dialog.edit_axis_y.setText("")
        self.dialog._on_axis_auto()
        self.assertEqual(self.dialog.edit_axis_x.text(), AUTO_X)
        self.assertEqual(self.dialog.edit_axis_y.text(), AUTO_Y)
        self.assertEqual(self._collect(), {})

    def test_third_field_is_hidden_outside_3d(self):
        self.assertTrue(self.dialog.edit_axis_z.isHidden())
        self.assertTrue(self.dialog.label_axis_z.isHidden())
        self.assertEqual(self.dialog.label_axis_x.text(), "横轴")
        self.assertEqual(self.dialog.label_axis_y.text(), "纵轴")

    def test_draft_roundtrip_through_widgets(self):
        draft = {"xlabel_text": "Binding energy − E_F (eV)", "ylabel_text": ""}
        self.dialog._draft_overrides = dict(draft)
        self.dialog._sync_tune_ui_from_draft()
        self.assertEqual(self.dialog.edit_axis_x.text(), "Binding energy − E_F (eV)")
        self.assertEqual(self.dialog.edit_axis_y.text(), "")
        self.assertEqual(self._collect(), draft)

    def test_reset_returns_to_auto_labels(self):
        self.dialog._draft_overrides = {"xlabel_text": "custom", "ylabel_text": ""}
        self.dialog._sync_tune_ui_from_draft()
        self.dialog._reset_style_overrides()
        self.assertEqual(self.dialog.edit_axis_x.text(), AUTO_X)
        self.assertEqual(self.dialog.edit_axis_y.text(), AUTO_Y)
        self.assertEqual(self._collect(), {})

    # ---------------------------------------------------------------- 3D

    def test_3d_shows_three_axis_names(self):
        self._load_snapshot(_make_3d_snapshot(), "3d_minimal", "3d")
        self.assertFalse(self.dialog.edit_axis_z.isHidden())
        self.assertEqual(self.dialog.label_axis_x.text(), "X")
        self.assertEqual(self.dialog.label_axis_y.text(), "Y")
        self.assertEqual(self.dialog.label_axis_z.text(), "E")
        for edit, auto in zip(self.dialog.axis_edits, AUTO_3D):
            self.assertEqual(edit.text(), auto)
        self.assertEqual(self._collect(), {})

    def test_3d_collects_all_three_names(self):
        self._load_snapshot(_make_3d_snapshot(), "3d_minimal", "3d")
        self.dialog.edit_axis_z.setText("Energy above E_F (eV)")
        self.dialog.edit_axis_y.setText("")
        self.assertEqual(
            self._collect(),
            {"ylabel_text": "", "zlabel_text": "Energy above E_F (eV)"},
        )

    def test_3d_auto_button_restores_three_names(self):
        self._load_snapshot(_make_3d_snapshot(), "3d_minimal", "3d")
        for edit in self.dialog.axis_edits:
            edit.setText("x")
        self.dialog._on_axis_auto()
        self.assertEqual(
            [edit.text() for edit in self.dialog.axis_edits], list(AUTO_3D)
        )
        self.assertEqual(self._collect(), {})

    def test_hint_warns_when_main_view_axes_switch_is_off(self):
        # 3D 轴名只在主视图「坐标轴」开关打开时绘制：关掉时提示用户，
        # 但输入框保持可用（预置的轴名仍会被记住）
        snap = _make_3d_snapshot()
        snap.payload["show_axes"] = False
        self._load_snapshot(snap, "3d_minimal", "3d")
        self.assertFalse(self.dialog.label_axis_hint.isHidden())
        self.assertTrue(self.dialog.edit_axis_x.isEnabled())
        self.dialog.edit_axis_x.setText("kx (1/Å)")
        self.assertEqual(self._collect(), {"xlabel_text": "kx (1/Å)"})

    def test_no_hint_when_axes_are_drawn(self):
        self._load_snapshot(_make_3d_snapshot(), "3d_minimal", "3d")
        self.assertTrue(self.dialog.label_axis_hint.isHidden())
        # 2D / 1D 的坐标轴始终绘制，与 3D 开关无关
        self._load_snapshot(_make_2d_snapshot(), "2d_open", "2d")
        self.assertTrue(self.dialog.label_axis_hint.isHidden())

    # ------------------------------------------------------------- 持久化

    def test_committed_overrides_persist_including_empty_label(self):
        for overrides in (
            {"xlabel_text": "Binding energy (eV)", "zlabel_text": "E (eV)"},
            {"ylabel_text": ""},                     # 明确不要该轴标签
        ):
            commit_style(self.settings, "3d", "3d_minimal", overrides)
            self.assertEqual(load_overrides(self.settings, "3d", "3d_minimal"), overrides)
            style, loaded = committed_style_for(self.settings, "3d")
            self.assertEqual(style.style_id, "3d_minimal")
            self.assertEqual(loaded, overrides)

    def test_empty_label_survives_a_new_dialog(self):
        commit_style(self.settings, "2d", "2d_open", {"ylabel_text": ""})
        fresh = PublicationExportDialog(self.window)
        try:
            fresh.snapshot = _make_2d_snapshot()
            fresh._draft_style_id = "2d_open"
            fresh._committed_overrides = {"2d_open": {"ylabel_text": ""}}
            fresh._draft_overrides = dict(fresh._committed_overrides["2d_open"])
            fresh._update_tune_availability()
            fresh._sync_tune_ui_from_draft()
            self.assertEqual(fresh.edit_axis_y.text(), "")
            self.assertEqual(fresh._collect_overrides_from_ui(), {"ylabel_text": ""})
        finally:
            fresh._debounce.stop()
            fresh.close()
            fresh.deleteLater()


if __name__ == "__main__":
    unittest.main()
