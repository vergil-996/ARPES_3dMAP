"""Resources must resolve independently of the launch working directory."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bandscope.app.startup import resource_path


class StartupResourceTests(unittest.TestCase):
    def test_source_icon_from_another_working_directory(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                icon = Path(resource_path("assets/app.ico"))
                self.assertTrue(icon.is_file())
                self.assertEqual(icon, Path(__file__).resolve().parents[2] / "assets/app.ico")
            finally:
                os.chdir(original)

    def test_frozen_resources_use_bundle_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys, "_MEIPASS", directory, create=True):
                self.assertEqual(Path(resource_path("assets/app.ico")),
                                 Path(directory) / "assets/app.ico")
