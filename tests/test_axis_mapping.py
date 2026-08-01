import os
import tempfile
import unittest

import numpy as np

from analyzer_core import AnalyzerCore
from axis_mapping import (
    AXIS_MAPPING_VERSION,
    MAT_COORD_CANDIDATES,
    NPZ_COORD_CANDIDATES,
    canonicalize_array,
    find_first_key,
    infer_axis_roles,
    legacy_hdf5_axis_roles,
    metadata_axis_roles,
)


class AxisMappingTests(unittest.TestCase):
    def test_equal_momentum_lengths_keep_x_before_y(self):
        roles = infer_axis_roles(
            (4, 4, 6, 3),
            {"X": 4, "Y": 4, "E": 6, "T": 3},
            ["X", "Y", "E", "T"],
        )

        self.assertEqual(roles, {"T": 3, "E": 2, "X": 0, "Y": 1})

    def test_missing_role_is_inserted_as_a_singleton_axis(self):
        source = np.arange(20).reshape(4, 5)

        actual = canonicalize_array(
            source,
            {"X": 0, "Y": 1},
            ["X", "Y", "E"],
        )

        self.assertEqual(actual.shape, (4, 5, 1))
        np.testing.assert_array_equal(actual[..., 0], source)

    def test_metadata_accepts_byte_aliases(self):
        roles = metadata_axis_roles(
            np.asarray([AXIS_MAPPING_VERSION]),
            np.asarray([b"time", b"energy", b"kx", b"ky"]),
            ["X", "Y", "E", "T"],
        )

        self.assertEqual(roles, {"X": 2, "Y": 3, "E": 1, "T": 0})

    def test_legacy_hdf5_layouts_are_preserved(self):
        self.assertEqual(
            legacy_hdf5_axis_roles(
                (6, 4, 5),
                {"X": 4, "Y": 5, "E": 6},
                ["X", "Y", "E"],
            ),
            {"X": 1, "Y": 2, "E": 0},
        )
        self.assertEqual(
            legacy_hdf5_axis_roles(
                (2, 6, 4, 5),
                {"X": 4, "Y": 5, "E": 6, "T": 2},
                ["X", "Y", "E", "T"],
            ),
            {"X": 2, "Y": 3, "E": 1, "T": 0},
        )

    def test_npz_and_mat_keep_their_existing_case_precedence(self):
        keys = ["x", "X"]

        self.assertEqual(find_first_key(keys, NPZ_COORD_CANDIDATES["X"]), "x")
        self.assertEqual(find_first_key(keys, MAT_COORD_CANDIDATES["X"]), "X")

    def test_loader_honors_explicit_axis_order(self):
        source = np.arange(2 * 5 * 3 * 4, dtype=np.float32).reshape(2, 5, 3, 4)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "explicit-order.npz")
            np.savez(
                path,
                sample=source,
                kx=np.arange(3),
                ky=np.arange(4),
                E=np.arange(5),
                time=np.arange(2),
                axis_order=np.asarray(["T", "E", "X", "Y"]),
                axis_mapping_version=np.asarray([AXIS_MAPPING_VERSION]),
            )

            analyzer = AnalyzerCore()
            success, shape = analyzer.load_npz(path)

        self.assertTrue(success)
        self.assertEqual(shape, (3, 4, 5, 2))
        np.testing.assert_array_equal(analyzer.raw_data, source.transpose(2, 3, 1, 0))

    def test_loader_prefers_lowercase_x_without_metadata(self):
        source = np.arange(3 * 4 * 5, dtype=np.float32).reshape(3, 4, 5)
        lowercase_x = np.asarray([10.0, 20.0, 30.0])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "case-precedence.npz")
            np.savez(
                path,
                sample=source,
                x=lowercase_x,
                X=np.arange(99),
                ky=np.arange(4),
                E=np.arange(5),
            )

            analyzer = AnalyzerCore()
            success, shape = analyzer.load_npz(path)

        self.assertTrue(success)
        self.assertEqual(shape, (3, 4, 5, 1))
        np.testing.assert_array_equal(analyzer.coords["X"], lowercase_x)


if __name__ == "__main__":
    unittest.main()
