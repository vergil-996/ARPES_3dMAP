import unittest
from types import SimpleNamespace

import numpy as np

from refactored_app import My3DAnalyzer
from result_workspace import AnalysisPageSpec


class ExportPayloadTests(unittest.TestCase):
    @staticmethod
    def _coords(shape=(4, 5, 6, 2)):
        return {
            "X": np.linspace(-1.0, 1.0, shape[0]),
            "Y": np.linspace(-2.0, 2.0, shape[1]),
            "E": np.linspace(-0.5, 0.5, shape[2]),
            "delay": np.linspace(0.0, 1.0, shape[3]),
        }

    def test_home_payload_keeps_existing_slice_fields(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.core = SimpleNamespace(has_time_axis=True)
        analyzer._get_clip_slices = lambda: (
            (slice(1, 3), slice(2, 5), slice(1, 4)),
            [1, 2, 2, 4, 1, 3],
        )
        raw_data = np.arange(4 * 5 * 6 * 2, dtype=np.float32).reshape(4, 5, 6, 2)
        coords = self._coords(raw_data.shape)

        title, default_name, payload = analyzer._build_home_export_payload(
            raw_data, coords
        )

        self.assertEqual(title, "Save current slice cube")
        self.assertEqual(default_name, "slice_cube.mat")
        self.assertEqual(set(payload), {"sample", "kx", "ky", "E", "time"})
        np.testing.assert_array_equal(
            payload["sample"], raw_data[1:3, 2:5, 1:4, :]
        )

    def test_edc_payload_keeps_frame_metadata_and_crop_order(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer._get_edc_curve_context = lambda _spec, _raw, _coords: {
            "x_data": np.asarray([1.0, 2.0, 3.0]),
            "y_data": np.asarray([4.0, 5.0, 6.0]),
            "kx_range": (-1.0, 1.0),
            "ky_range": (-2.0, 2.0),
            "source_mode": "frame",
            "source_t_index": 2,
            "source_t_value": 0.25,
            "crop_rect": {"x_low": 3, "x_up": 8, "y_low": 4, "y_up": 9},
        }
        spec = AnalysisPageSpec("edc", "EDC", "edc_curve", "test")

        title, default_name, payload = analyzer._build_edc_export_payload(
            spec, np.zeros((2, 2, 3, 1), dtype=np.float32), self._coords()
        )

        self.assertEqual(title, "Save EDC curve result")
        self.assertEqual(default_name, "edc_curve.mat")
        np.testing.assert_array_equal(payload["crop_rect"], [3, 8, 4, 9])
        np.testing.assert_array_equal(payload["source_t_index"], [2])
        np.testing.assert_allclose(payload["source_t_value"], [0.25])

    def test_second_derivative_3d_payload_keeps_axis_and_time_fields(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer._second_derivative_axis_label = lambda _axis: "E"
        coords = self._coords((2, 3, 4, 2))
        context = {
            "data": np.ones((2, 3, 4), dtype=np.float32),
            "derivative_axis": 2,
            "source_mode": "frame",
            "source_t_index": 1,
        }

        title, default_name, payload = (
            analyzer._build_second_derivative_3d_export_payload(context, coords)
        )

        self.assertEqual(title, "Save 3D second-derivative result")
        self.assertEqual(default_name, "second_derivative_3d_E.mat")
        np.testing.assert_array_equal(payload["derivative_axis"], ["E"])
        np.testing.assert_array_equal(payload["source_t_index"], [1])
        np.testing.assert_allclose(payload["time"], [coords["delay"][1]])

    def test_export_wrapper_adds_scope_metadata_before_saving(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        spec = AnalysisPageSpec("dos", "DOS", "energy_dos", "test")
        analyzer.left_workspace = SimpleNamespace(current_spec=lambda: spec)
        analyzer.core = SimpleNamespace(raw_data=np.ones((1, 1, 1, 1)))
        analyzer._get_display_state_for_spec = lambda _spec: ("raw", {"E": [0.0]})
        analyzer._build_export_payload = lambda _spec, _raw, _coords: (
            "Save",
            "result.mat",
            {"sample": np.asarray([1.0])},
        )
        analyzer._scope_for_spec = lambda _spec: "scope"
        analyzer._scope_export_metadata = lambda _scope, _coords: {
            "data_scope_id": np.asarray(["full"])
        }
        analyzer._choose_export_path = lambda _title, _name: "result.npz"
        saved = {}
        analyzer._save_dict_to_path = lambda path, data: saved.update(
            {"path": path, "data": data}
        )

        analyzer.export_current_result()

        self.assertEqual(saved["path"], "result.npz")
        self.assertEqual(set(saved["data"]), {"sample", "data_scope_id"})


if __name__ == "__main__":
    unittest.main()
