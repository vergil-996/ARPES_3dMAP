import os
import tempfile
import unittest

import numpy as np

from refactored_app import My3DAnalyzer


class TabularExportTests(unittest.TestCase):
    def test_sanitize_save_path_maps_filters(self):
        self.assertEqual(
            My3DAnalyzer._sanitize_save_path("slice_dos.mat", "Text Files (*.txt)"),
            "slice_dos.txt",
        )
        self.assertEqual(
            My3DAnalyzer._sanitize_save_path("slice_dos.mat", "CSV Files (*.csv)"),
            "slice_dos.csv",
        )
        self.assertEqual(
            My3DAnalyzer._sanitize_save_path("slice_dos.mat", "NumPy Files (*.npz)"),
            "slice_dos.npz",
        )
        self.assertEqual(
            My3DAnalyzer._sanitize_save_path("slice_dos.mat", "MATLAB Files (*.mat)"),
            "slice_dos.mat",
        )
        self.assertEqual(
            My3DAnalyzer._sanitize_save_path("slice_dos", "Text Files (*.txt)"),
            "slice_dos.txt",
        )

    def test_find_tabular_primary(self):
        self.assertEqual(
            My3DAnalyzer._find_tabular_primary({"intensity": np.ones(4)})[0],
            "curve",
        )
        self.assertEqual(
            My3DAnalyzer._find_tabular_primary({"sample": np.ones((3, 4))})[0],
            "matrix",
        )
        self.assertIsNone(
            My3DAnalyzer._find_tabular_primary({"sample": np.ones((3, 4, 5))})[0]
        )

    def test_curve_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "dos.csv")
            My3DAnalyzer._save_tabular_to_path(
                path,
                {
                    "E": np.array([-1.0, 0.0, 1.0]),
                    "intensity": np.array([2.0, 4.0, 8.0]),
                },
            )
            with open(path, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        self.assertEqual(lines[0], "E,intensity")
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[2], "0,4")

    def test_curve_txt_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "slice.txt")
            My3DAnalyzer._save_tabular_to_path(
                path,
                {
                    "time": np.array([0.1, 0.2]),
                    "intensity": np.array([3.0, 5.0]),
                    "data_scope_id": np.asarray(["full"]),
                },
            )
            with open(path, encoding="utf-8") as handle:
                text = handle.read().splitlines()
        self.assertTrue(any(line.startswith("# data_scope_id") for line in text))
        self.assertEqual(text[-1], "0.2\t5")

    def test_matrix_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "wf.csv")
            My3DAnalyzer._save_tabular_to_path(
                path,
                {
                    "k": np.array([0.0, 1.0]),
                    "E": np.array([-0.5, 0.0, 0.5]),
                    "intensity": np.arange(6, dtype=np.float32).reshape(2, 3),
                },
            )
            with open(path, encoding="utf-8") as handle:
                text = handle.read().splitlines()
        self.assertTrue(text[0].startswith("k / E"))
        self.assertEqual(len(text), 3)
        self.assertEqual(text[2].split(",")[2], "4")

    def test_matrix_txt_kxky(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "map.txt")
            My3DAnalyzer._save_tabular_to_path(
                path,
                {
                    "sample": np.arange(4, dtype=np.float32).reshape(2, 2),
                    "kx": np.array([0.0, 1.0]),
                    "ky": np.array([2.0, 3.0]),
                },
            )
            with open(path, encoding="utf-8") as handle:
                text = handle.read().splitlines()
        self.assertTrue(text[0].startswith("# rows = kx"))
        self.assertIn("columns = ky", text[0])


if __name__ == "__main__":
    unittest.main()
