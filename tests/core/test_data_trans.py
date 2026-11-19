import math
import os
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from scipy.io import savemat

from bandscope.core.data_trans import convert, load_mat_auto


def oversized_single_mat_header(shape):
    expected_count = math.prod(shape) * 4
    description = b"MATLAB 5.0 MAT-file, Platform: posix, Created by BandScope tests"
    header = description.ljust(116, b"\0") + b"\0" * 8
    header += struct.pack("<H", 0x0100) + b"IM"
    header += struct.pack("<II", 14, 0)
    header += struct.pack("<IIII", 6, 8, 7, 0)
    dimensions = struct.pack("<" + str(len(shape)) + "i", *shape)
    header += struct.pack("<II", 5, len(dimensions)) + dimensions
    header += b"\0" * ((-len(dimensions)) % 8)
    header += struct.pack("<II", 1, 6) + b"binned\0\0"
    header += struct.pack("<II", 7, expected_count & 0xFFFFFFFF)
    return header, expected_count


class DataTransTests(unittest.TestCase):
    def test_regular_mat_conversion_is_preserved(self):
        source = np.arange(2 * 3 * 4, dtype=np.float32).reshape(2, 3, 4)
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, "source.mat")
            target_path = os.path.join(temp_dir, "target.npz")
            savemat(
                source_path,
                {
                    "binned": source,
                    "kx": np.arange(2),
                    "ky": np.arange(3),
                    "E": np.arange(4),
                },
            )

            convert(source_path, target_path)

            with np.load(target_path, allow_pickle=False) as converted:
                np.testing.assert_array_equal(converted["sample"], source)
                np.testing.assert_array_equal(converted["kx"], np.arange(2))
                np.testing.assert_array_equal(converted["ky"], np.arange(3))
                np.testing.assert_array_equal(converted["E"], np.arange(4))
                np.testing.assert_array_equal(converted["time"], np.asarray([0.0]))

    def test_oversized_v5_matrix_uses_column_major_read_only_memmap(self):
        shape = (65536, 65536)
        header, expected_count = oversized_single_mat_header(shape)
        mapped = object()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "oversized.mat")
            with open(path, "wb") as stream:
                stream.write(header)

            with (
                patch(
                    "bandscope.core.data_trans.os.path.getsize",
                    return_value=len(header) + expected_count,
                ),
                patch(
                    "bandscope.core.data_trans.np.memmap",
                    return_value=mapped,
                ) as memmap,
            ):
                mode, data = load_mat_auto(path)

        self.assertEqual(mode, "mapped")
        self.assertIs(data["binned"], mapped)
        memmap.assert_called_once_with(
            path,
            dtype=np.dtype("<f4"),
            mode="r",
            offset=len(header),
            shape=shape,
            order="F",
        )

    def test_float32_conversion_does_not_copy_sample(self):
        source = np.arange(2 * 3 * 4, dtype=np.float32).reshape(2, 3, 4)
        with (
            patch(
                "bandscope.core.data_trans.load_mat_auto",
                return_value=("loadmat", {"binned": source}),
            ),
            patch("bandscope.core.data_trans.np.savez") as savez,
        ):
            convert("source.mat", "target.npz")

        self.assertTrue(np.shares_memory(savez.call_args.kwargs["sample"], source))


if __name__ == "__main__":
    unittest.main()
