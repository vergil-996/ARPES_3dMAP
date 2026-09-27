"""Public plugin imports retain identity and remain usable in a frozen host."""
import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class PluginImportCompatibilityTests(unittest.TestCase):
    def test_old_imports_share_the_actual_module(self):
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        for old, new in (
            ("plugin_api", "bandscope.extensions.api"),
            ("theme", "bandscope.ui.theme"),
            ("ui_controls", "bandscope.ui.ui_controls"),
        ):
            with self.subTest(module=old):
                self.assertIs(importlib.import_module(old), importlib.import_module(new))
        from bandscope.extensions import ui
        self.assertIs(ui.ActionButton, importlib.import_module("ui_controls").ActionButton)

    def test_protocol_does_not_load_graphics_dependencies(self):
        root = Path(__file__).resolve().parents[2]
        env = dict(os.environ, PYTHONPATH=str(root))
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-c",
                 "from bandscope.extensions.api import Plugin; import sys; "
                 "assert not any(m.split('.')[0] in ('PyQt5', 'vtk', 'vtkmodules') "
                 "for m in sys.modules)"],
                cwd=directory, env=env, capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
