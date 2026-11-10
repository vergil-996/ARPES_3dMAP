import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import zlib

import numpy as np

from bandscope.exporting.illustrator_export import (
    IllustratorError,
    _conversion_script,
    _executable_from_command,
    convert_pdf_to_ai,
)
from bandscope.exporting.publication_models import (
    FAMILY_FORMATS,
    OUTPUT_FORMATS,
    OutputOptions,
    STYLE_REGISTRY,
)
from bandscope.exporting.publication_renderers import (
    _new_figure,
    render_snapshot,
    save_figure,
)
from tests.support.publication import _make_2d_snapshot


class OutputFormatTests(unittest.TestCase):
    def test_every_family_offers_png_pdf_svg_and_ai(self):
        self.assertEqual(OUTPUT_FORMATS, ("png", "pdf", "svg", "ai"))
        for family in ("3d", "2d", "1d"):
            self.assertEqual(FAMILY_FORMATS[family], OUTPUT_FORMATS)

    def test_new_formats_survive_preferences_roundtrip(self):
        for fmt in ("svg", "ai"):
            options = OutputOptions(width_mm=123, height_mm=98, dpi=450, fmt=fmt)
            self.assertEqual(OutputOptions.from_json(options.to_json()), options)

    def test_svg_keeps_text_editable_and_heatmap_embedded(self):
        options = OutputOptions(width_mm=89, dpi=120, fmt="svg")
        figure = render_snapshot(
            _make_2d_snapshot(), STYLE_REGISTRY["2d"]["2d_boxed"], {}, options, dpi=120
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "figure.svg"
            save_figure(figure, str(path), options)
            content = path.read_text(encoding="utf-8")
        figure.clear()
        self.assertIn("<text", content)
        self.assertIn("data:image/png;base64", content)

    def test_hybrid_pdf_contains_raster_and_text_operators(self):
        figure = _new_figure(89, 65, 120)
        axes = figure.add_axes([0.15, 0.15, 0.7, 0.7])
        axes.imshow(np.arange(16).reshape(4, 4))
        axes.set_title("Hybrid figure")
        options = OutputOptions(width_mm=89, height_mm=65, dpi=120, fmt="pdf")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "figure.pdf"
            save_figure(figure, str(path), options)
            content = path.read_bytes()
        figure.clear()
        self.assertIn(b"/Subtype /Image", content)
        inflated = b""
        for match in re.finditer(rb"stream\r?\n(.*?)endstream", content, re.S):
            try:
                inflated += zlib.decompress(match.group(1))
            except zlib.error:
                pass
        self.assertIn(b"BT", inflated)
        self.assertIn(b"TJ", inflated)


class AISaveTests(unittest.TestCase):
    def test_ai_save_uses_pdf_source_and_ai_temporary_suffix(self):
        figure = _new_figure(89, 65, 100)
        figure.text(0.5, 0.5, "editable")
        options = OutputOptions(width_mm=89, height_mm=65, dpi=100, fmt="ai")
        calls = []

        def convert(source, target):
            calls.append((source, target))
            self.assertTrue(Path(source).read_bytes().startswith(b"%PDF-"))
            Path(target).write_bytes(b"%PDF-1.6\n%AI-test")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "figure.ai"
            with patch(
                "bandscope.exporting.illustrator_export.convert_pdf_to_ai",
                side_effect=convert,
            ):
                save_figure(figure, str(path), options)
            self.assertTrue(path.read_bytes().endswith(b"%AI-test"))
            self.assertEqual(os.listdir(directory), ["figure.ai"])
        figure.clear()
        self.assertEqual(len(calls), 1)
        source, target = calls[0]
        self.assertTrue(source.endswith(".pdf"))
        self.assertTrue(target.endswith(".ai"))

    def test_failed_ai_conversion_preserves_existing_file(self):
        figure = _new_figure(89, 65, 100)
        options = OutputOptions(width_mm=89, height_mm=65, dpi=100, fmt="ai")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "figure.ai"
            path.write_bytes(b"OLD-CONTENT")
            with patch(
                "bandscope.exporting.illustrator_export.convert_pdf_to_ai",
                side_effect=IllustratorError("simulated conversion failure"),
            ):
                with self.assertRaises(IllustratorError):
                    save_figure(figure, str(path), options)
            self.assertEqual(path.read_bytes(), b"OLD-CONTENT")
            self.assertEqual(os.listdir(directory), ["figure.ai"])
        figure.clear()


class IllustratorConversionTests(unittest.TestCase):
    def test_executable_path_parsing(self):
        expected = r"C:\Program Files\Adobe\Illustrator.exe"
        self.assertEqual(_executable_from_command(f'"{expected}" /Automation'), expected)
        self.assertEqual(_executable_from_command(expected), expected)
        self.assertIsNone(_executable_from_command("not registered"))

    def test_powershell_script_uses_pdf_compatible_ai(self):
        script = _conversion_script()
        self.assertIn("Illustrator.Application", script)
        self.assertIn("DoJavaScript", script)
        self.assertIn("IllustratorSaveOptions", script)
        self.assertIn("options.pdfCompatible=true", script)

    def test_conversion_invokes_powershell_without_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            target = Path(directory) / "target.ai"
            source.write_bytes(b"%PDF-test")
            calls = []

            def run(command, **kwargs):
                calls.append((command, kwargs))
                Path(command[-1]).write_bytes(b"%PDF-1.6\n%AI-test")
                from subprocess import CompletedProcess
                return CompletedProcess(command, 0, "", "")

            with patch(
                "bandscope.exporting.illustrator_export.illustrator_available",
                return_value=True,
            ), patch(
                "bandscope.exporting.illustrator_export.shutil.which",
                return_value="powershell.exe",
            ), patch(
                "bandscope.exporting.illustrator_export.subprocess.run",
                side_effect=run,
            ):
                convert_pdf_to_ai(source, target)
            self.assertEqual(target.read_bytes(), b"%PDF-1.6\n%AI-test")
        self.assertEqual(len(calls), 1)
        command, kwargs = calls[0]
        self.assertEqual(command[0], "powershell.exe")
        self.assertIn("-File", command)
        self.assertNotIn("shell", kwargs)
        self.assertFalse(kwargs.get("shell", False))

    def test_failed_conversion_removes_partial_target(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            target = Path(directory) / "target.ai"
            source.write_bytes(b"%PDF-test")

            def run(command, **kwargs):
                Path(command[-1]).write_bytes(b"partial")
                from subprocess import CompletedProcess
                return CompletedProcess(command, 1, "", "conversion failed")

            with patch(
                "bandscope.exporting.illustrator_export.illustrator_available",
                return_value=True,
            ), patch(
                "bandscope.exporting.illustrator_export.shutil.which",
                return_value="powershell.exe",
            ), patch(
                "bandscope.exporting.illustrator_export.subprocess.run",
                side_effect=run,
            ):
                with self.assertRaisesRegex(IllustratorError, "conversion failed"):
                    convert_pdf_to_ai(source, target)
            self.assertFalse(target.exists())

    def test_missing_illustrator_fails_before_launching(self):
        with patch(
            "bandscope.exporting.illustrator_export.illustrator_available",
            return_value=False,
        ), patch(
            "bandscope.exporting.illustrator_export.subprocess.run"
        ) as run:
            with self.assertRaisesRegex(IllustratorError, "PDF 或 SVG"):
                convert_pdf_to_ai("source.pdf", "target.ai")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
