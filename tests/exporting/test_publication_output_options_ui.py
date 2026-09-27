# -*- coding: utf-8 -*-
"""截图样式面板：输出行（尺寸/DPI/格式/透明背景）的控件 ↔ 持久化往返。

透明背景是渲染层之外最容易接错的一环——勾选框若没被 `_collect_output_options`
读到、或没进 `OutputOptions.signature`，产物和预览都会静默保持白底。
这里绕开渲染，只覆盖控件与偏好之间的契约。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.exporting.publication_dialog import PublicationExportDialog, _composite_preview
from bandscope.exporting.publication_models import OutputOptions, load_output_options, save_output_options
from tests.support.publication import _make_2d_snapshot


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


class OutputOptionsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = _MemorySettings()
        self.window = _StubWindow(self.settings)
        self.dialog = PublicationExportDialog(self.window)
        self.dialog.snapshot = _make_2d_snapshot()
        self.dialog._draft_style_id = "2d_boxed"
        self.dialog._committed_overrides = {"2d_boxed": {}}
        self.dialog._draft_overrides = {}
        self.dialog._load_output_options_ui()

    def tearDown(self):
        self.dialog._debounce.stop()
        self.dialog.close()
        self.dialog.deleteLater()
        self.window.close()

    def test_defaults_to_opaque(self):
        self.assertFalse(self.dialog.chk_transparent.isChecked())
        self.assertFalse(self.dialog._collect_output_options().transparent)

    def test_checkbox_reflects_persisted_preference(self):
        save_output_options(
            self.settings, OutputOptions(width_mm=89.0, dpi=600, transparent=True)
        )
        self.dialog._load_output_options_ui()
        self.assertTrue(self.dialog.chk_transparent.isChecked())
        self.assertTrue(self.dialog._collect_output_options().transparent)

    def test_toggling_persists_without_waiting_for_export(self):
        # 尺寸/DPI/格式是独立输出偏好、改动即持久化，透明背景走同一条路
        self.dialog.chk_transparent.setChecked(True)
        self.assertTrue(load_output_options(self.settings).transparent)
        self.dialog.chk_transparent.setChecked(False)
        self.assertFalse(load_output_options(self.settings).transparent)

    def test_toggle_invalidates_preview_cache_key(self):
        # 预览缓存以 options.signature 为键：切换开关必须让旧预览失效，
        # 否则勾选后仍显示上一次的白底预览
        before = self.dialog._preview_key("2d_boxed", 150)
        self.dialog.chk_transparent.setChecked(True)
        self.assertNotEqual(before, self.dialog._preview_key("2d_boxed", 150))

    def test_composite_only_rewrites_transparent_previews(self):
        from PyQt5.QtGui import QColor, QPixmap

        # 半透明填充才会带 alpha 通道；纯不透明 fill() 的 QPixmap 没有，
        # hasAlphaChannel() 为假（下面第一条断言正是这个护栏）
        alpha_pixmap = QPixmap(40, 40)
        alpha_pixmap.fill(QColor(255, 0, 0, 128))
        self.assertTrue(alpha_pixmap.hasAlphaChannel())

        # 开关关着：原样返回，不给白底预览无谓地铺一层棋盘格
        self.assertIs(_composite_preview(alpha_pixmap, False), alpha_pixmap)
        # 开关打开：换成棋盘格底，尺寸保持一致
        composed = _composite_preview(alpha_pixmap, True)
        self.assertIsNot(composed, alpha_pixmap)
        self.assertEqual(composed.size(), alpha_pixmap.size())

        # 无 alpha 通道的图（不透明产物）即使开关打开也不合成
        opaque_pixmap = QPixmap(40, 40)
        opaque_pixmap.fill(QColor("#FF0000"))
        self.assertFalse(opaque_pixmap.hasAlphaChannel())
        self.assertIs(_composite_preview(opaque_pixmap, True), opaque_pixmap)


if __name__ == "__main__":
    unittest.main()
