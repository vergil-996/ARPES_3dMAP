"""字体选择的草稿、持久化、回退与实际渲染契约。"""
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path

from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.exporting.publication_dialog import PublicationExportDialog
from bandscope.exporting.publication_export import commit_style
from bandscope.exporting.publication_models import (
    OutputOptions, load_overrides, publication_font_family, resolve_style,
    validate_overrides,
)
from bandscope.exporting.publication_renderers import _font_style_params, render_2d
from tests.support.publication import _make_2d_snapshot


class PublicationFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_widgets_roundtrip_persistence_and_reset(self):
        with TemporaryDirectory() as directory:
            window = QWidget()
            window.settings = QSettings(str(Path(directory) / "fonts.ini"), QSettings.IniFormat)
            dialog = PublicationExportDialog(window)
            try:
                dialog.snapshot = _make_2d_snapshot()
                dialog._draft_style_id = "2d_open"
                dialog._draft_overrides = {}
                dialog._load_output_options_ui()
                dialog._sync_tune_ui_from_draft()
                self.assertEqual(dialog._collect_overrides_from_ui(), {})
                self.assertEqual(dialog.combo_font_english.count(), 4)
                self.assertEqual(dialog.combo_font_chinese.count(), 4)
                dialog.combo_font_english.setCurrentIndex(2)
                dialog.combo_font_chinese.setCurrentIndex(1)
                expected = {"font_english": "Times New Roman", "font_chinese": "SimSun"}
                self.assertEqual(dialog._collect_overrides_from_ui(), expected)
                commit_style(window.settings, "2d", "2d_open", expected)
                dialog._draft_overrides = load_overrides(window.settings, "2d", "2d_open")
                dialog._sync_tune_ui_from_draft()
                self.assertEqual(dialog._collect_overrides_from_ui(), expected)
                dialog._reset_style_overrides()
                self.assertEqual(dialog._collect_overrides_from_ui(), {})
            finally:
                dialog._debounce.stop()
                dialog.close()
                dialog.deleteLater()
                window.close()

    def test_validated_for_every_view_family(self):
        expected = {"font_english": "Georgia", "font_chinese": "KaiTi"}
        for family in ("1d", "2d", "3d"):
            self.assertEqual(validate_overrides(family, expected), expected)
            self.assertEqual(validate_overrides(family, {"font_english": "invalid"}), {})
            chain = publication_font_family(resolve_style(family, None).params, expected)
            self.assertEqual(chain[:2], ["Georgia", "KaiTi"])
            self.assertIn("DejaVu Sans", chain)
            self.assertIn("Microsoft YaHei", chain)

    def test_missing_fonts_use_available_fallback(self):
        from types import SimpleNamespace
        with patch("matplotlib.font_manager.fontManager", SimpleNamespace(
            ttflist=[SimpleNamespace(name="DejaVu Sans")]
        )):
            params = _font_style_params(resolve_style("2d", None), {
                "font_english": "Georgia", "font_chinese": "KaiTi",
            })
        self.assertEqual(params["font_family"], ["DejaVu Sans", "sans-serif"])

    def test_rendered_annotations_receive_selected_family(self):
        style = resolve_style("2d", None)
        overrides = {"font_english": "DejaVu Sans", "font_chinese": "SimSun", "panel_label": "a"}
        figure = render_2d(_make_2d_snapshot(), style, overrides, OutputOptions(), 80)
        expected = _font_style_params(style, overrides)["font_family"]
        axes = figure.axes[0]
        texts = [axes.xaxis.label, axes.yaxis.label, axes.xaxis.get_offset_text()]
        texts += axes.get_xticklabels() + list(axes.texts) + list(figure.texts)
        for text in texts:
            self.assertEqual(text.get_fontfamily(), expected)
        figure.clear()
