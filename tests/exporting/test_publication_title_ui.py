# -*- coding: utf-8 -*-
"""截图样式面板：标题命名/位置控件的草稿语义与持久化往返。

覆盖设计意图（plan v2 §5）：默认不直接把软件自动页面标题写进图片——
面板打开时把自动标题带进输入框供改名，只有改动了才写入覆盖；清空表示
"明确不要标题"（必须与"未设置=用自动标题"区分开并持久化）；「自动」
按钮撤销命名覆盖。位置/对齐/距离与标题一起走同一套 draft → 提交链路。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.exporting.publication_dialog import PublicationExportDialog
from bandscope.exporting.publication_export import commit_style, committed_style_for
from bandscope.exporting.publication_models import TITLE_GAP_DEFAULT_MM, load_overrides
from tests.support.publication import _make_2d_snapshot

AUTO_TITLE = "X-Integral (40~60)"


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


class TitleControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = _MemorySettings()
        self.window = _StubWindow(self.settings)
        self.dialog = PublicationExportDialog(self.window)
        # 直接装载草稿状态，绕开预览渲染（本测试只覆盖控件 ↔ 草稿语义）
        self.dialog.snapshot = _make_2d_snapshot()
        self.dialog._draft_style_id = "2d_open"
        self.dialog._committed_overrides = {"2d_open": {}}
        self.dialog._draft_overrides = {}
        self.dialog._load_output_options_ui()
        self.dialog._sync_tune_ui_from_draft()

    def tearDown(self):
        self.dialog._debounce.stop()
        self.dialog.close()
        self.dialog.deleteLater()
        self.window.close()

    def _collect(self):
        return self.dialog._collect_overrides_from_ui()

    def test_opens_with_auto_title_prefilled_and_no_override(self):
        self.assertEqual(self.dialog.edit_title.text(), AUTO_TITLE)
        self.assertEqual(self._collect(), {})

    def test_renaming_stores_only_title_text(self):
        self.dialog.edit_title.setText("Fig. 3(a)")
        self.assertEqual(self._collect(), {"title_text": "Fig. 3(a)"})

    def test_typing_the_auto_title_again_keeps_settings_clean(self):
        self.dialog.edit_title.setText("Fig. 3(a)")
        self.dialog.edit_title.setText(AUTO_TITLE)
        self.assertEqual(self._collect(), {})

    def test_clearing_title_is_an_explicit_override(self):
        self.dialog.edit_title.setText("")
        self.assertEqual(self._collect(), {"title_text": ""})

    def test_auto_button_restores_auto_title(self):
        self.dialog.edit_title.setText("Fig. 3(a)")
        self.dialog._on_title_auto()
        self.assertEqual(self.dialog.edit_title.text(), AUTO_TITLE)
        self.assertEqual(self._collect(), {})

    def test_position_align_and_gap_are_collected(self):
        self.dialog.combo_title_pos.setCurrentIndex(1)
        self.dialog.combo_title_align.setCurrentIndex(0)
        self.dialog.spin_title_gap.setValue(4.0)
        self.assertEqual(
            self._collect(),
            {"title_position": "bottom", "title_align": "left", "title_gap_mm": 4.0},
        )

    def test_default_position_align_gap_write_no_override(self):
        self.dialog.combo_title_pos.setCurrentIndex(0)
        self.dialog.combo_title_align.setCurrentIndex(1)
        self.dialog.spin_title_gap.setValue(TITLE_GAP_DEFAULT_MM)
        self.assertEqual(self._collect(), {})

    def test_show_title_checkbox_is_collected(self):
        self.dialog.chk_title.setChecked(False)
        self.assertEqual(self._collect(), {"show_title": False})

    def test_draft_roundtrip_through_widgets(self):
        draft = {
            "title_text": "Fig. 9",
            "title_position": "bottom",
            "title_align": "right",
            "title_gap_mm": 3.5,
            "show_title": False,
        }
        self.dialog._draft_overrides = dict(draft)
        self.dialog._sync_tune_ui_from_draft()
        self.assertEqual(self.dialog.edit_title.text(), "Fig. 9")
        self.assertEqual(self.dialog.combo_title_pos.currentIndex(), 1)
        self.assertEqual(self.dialog.combo_title_align.currentIndex(), 2)
        self.assertAlmostEqual(self.dialog.spin_title_gap.value(), 3.5)
        self.assertFalse(self.dialog.chk_title.isChecked())
        self.assertEqual(self._collect(), draft)

    def test_reset_returns_to_auto_title(self):
        self.dialog._draft_overrides = {"title_text": "Fig. 9", "title_align": "left"}
        self.dialog._sync_tune_ui_from_draft()
        self.dialog._reset_style_overrides()
        self.assertEqual(self.dialog.edit_title.text(), AUTO_TITLE)
        self.assertEqual(self._collect(), {})

    def test_committed_overrides_persist_including_empty_title(self):
        for overrides in (
            {"title_text": "Fig. 3(a)", "title_position": "bottom"},
            {"title_text": ""},                      # 明确不要标题
            {"title_gap_mm": 5.0, "title_align": "right"},
        ):
            commit_style(self.settings, "2d", "2d_open", overrides)
            self.assertEqual(load_overrides(self.settings, "2d", "2d_open"), overrides)
            style, loaded = committed_style_for(self.settings, "2d")
            self.assertEqual(style.style_id, "2d_open")
            self.assertEqual(loaded, overrides)


if __name__ == "__main__":
    unittest.main()
