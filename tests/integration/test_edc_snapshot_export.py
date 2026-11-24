"""Frozen EDC snapshots and their log pages export without a live data source."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from scipy.io import loadmat

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.ui.result_workspace import AnalysisPageSpec


def _curve(curve_id, energy, intensity):
    return {
        "curve_kind": "edc_curve",
        "curve_id": curve_id,
        "title": curve_id,
        "source_title": "frozen source",
        "label": curve_id,
        "xlabel": "Energy (eV)",
        "x_data": list(energy),
        "y_data": list(intensity),
    }


def _frozen_scope():
    return {
        "data_scope_id": np.asarray(["roi:frozen"]),
        "data_scope_label": np.asarray(["Frozen ROI"]),
        "roi_index_bounds": np.asarray([1, 3, 2, 4, 5, 7], dtype=np.int32),
        "roi_physical_bounds": np.asarray([-0.3, 0.2, -0.4, 0.1, -1.0, 0.5]),
    }


class _NoCurrentSource:
    raw_data = None

    @property
    def coords(self):
        raise AssertionError("A frozen export must not read current source coordinates")


class EdcSnapshotExportTests(unittest.TestCase):
    @staticmethod
    def _analyzer(spec):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.left_workspace = SimpleNamespace(current_spec=lambda: spec)
        analyzer.core = _NoCurrentSource()
        analyzer._scope_for_spec = Mock(
            side_effect=AssertionError("A frozen export must not resolve the current scope")
        )
        analyzer._scope_export_metadata = Mock(
            side_effect=AssertionError("A frozen export must not rebuild scope metadata")
        )
        analyzer._get_display_state_for_spec = Mock(
            side_effect=AssertionError("A frozen export must not resolve current source data")
        )
        analyzer._toast_success = Mock()
        return analyzer

    @staticmethod
    def _load_export(path):
        if path.endswith(".npz"):
            with np.load(path) as exported:
                return {key: exported[key] for key in exported.files}
        return loadmat(path)

    def _assert_frozen_scope(self, exported):
        self.assertEqual(str(exported["data_scope_id"].reshape(-1)[0]), "roi:frozen")
        np.testing.assert_array_equal(
            exported["roi_index_bounds"].reshape(-1), [1, 3, 2, 4, 5, 7]
        )
        np.testing.assert_allclose(
            exported["roi_physical_bounds"].reshape(-1),
            [-0.3, 0.2, -0.4, 0.1, -1.0, 0.5],
        )

    def test_single_snapshot_mat_and_npz_use_frozen_roi_rotation_and_index_bounds(self):
        spec = AnalysisPageSpec(
            "single",
            "Single EDC",
            "edc_curve",
            "data_process",
            params={
                "base_curve": _curve("a", [0.0, 1.0], [2.0, 3.0]),
                "curve_metadata": [{
                    "curve_id": "a",
                    "index_bounds": (1, 3, 2, 4, 5, 7),
                    "kx_range": (-0.3, 0.2),
                }],
                "source_scope_metadata": _frozen_scope(),
                "source_rotation_angle": 17.25,
            },
        )
        analyzer = self._analyzer(spec)
        with tempfile.TemporaryDirectory() as tmp:
            for extension in ("mat", "npz"):
                with self.subTest(extension=extension):
                    path = os.path.join(tmp, f"single.{extension}")
                    analyzer._choose_export_path = lambda _title, _name, path=path: path
                    analyzer.export_current_result()
                    exported = self._load_export(path)
                    np.testing.assert_allclose(exported["E"].reshape(-1), [0.0, 1.0])
                    np.testing.assert_allclose(exported["intensity"].reshape(-1), [2.0, 3.0])
                    np.testing.assert_array_equal(
                        exported["index_bounds"].reshape(-1), [1, 3, 2, 4, 5, 7]
                    )
                    np.testing.assert_allclose(exported["kx_range"].reshape(-1), [-0.3, 0.2])
                    np.testing.assert_allclose(exported["source_rotation_angle"].reshape(-1), [17.25])
                    self._assert_frozen_scope(exported)
        analyzer._scope_for_spec.assert_not_called()
        analyzer._scope_export_metadata.assert_not_called()
        analyzer._get_display_state_for_spec.assert_not_called()

    def test_single_edc_log_keeps_curve_identity_and_exports_index_bounds(self):
        source = AnalysisPageSpec(
            "single",
            "Single EDC",
            "edc_curve",
            "data_process",
            params={
                "comparison_kind": "edc_curve",
                "base_curve": _curve("a", [0.0, 1.0], [2.0, 4.0]),
                "curve_metadata": [{
                    "curve_id": "a",
                    "index_bounds": (1, 3, 2, 4, 5, 7),
                }],
                "source_scope_metadata": _frozen_scope(),
                "source_rotation_angle": 3.5,
            },
        )
        analyzer = self._analyzer(source)
        created = []
        analyzer.left_workspace.add_page = created.append
        analyzer.current_render_context = analyzer._get_edc_curve_context(source, None, None)
        analyzer._make_page_id = lambda: "single-edc-log"
        analyzer._make_unique_page_title = lambda title: title
        analyzer._seed_control_state_for_spec = Mock()

        self.assertTrue(analyzer._apply_log_to_current_curves())
        log_spec = created[0]
        self.assertEqual(log_spec.params["base_curve"]["curve_id"], "a")
        analyzer.left_workspace.current_spec = lambda: log_spec
        analyzer._build_energy_dos_export_payload = Mock(
            side_effect=AssertionError("An EDC log must not be recomputed as Energy DOS")
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "single-edc-log.npz")
            analyzer._choose_export_path = lambda _title, _name: path
            analyzer.export_current_result()
            exported = self._load_export(path)
            np.testing.assert_allclose(exported["E_1"].reshape(-1), [0.0, 1.0])
            np.testing.assert_allclose(exported["intensity_1"].reshape(-1), np.log10([2.0, 4.0]))
            np.testing.assert_array_equal(
                exported["index_bounds_1"].reshape(-1), [1, 3, 2, 4, 5, 7]
            )
            np.testing.assert_allclose(exported["source_rotation_angle"].reshape(-1), [3.5])
            self._assert_frozen_scope(exported)
        analyzer._build_energy_dos_export_payload.assert_not_called()
        analyzer._scope_for_spec.assert_not_called()
        analyzer._get_display_state_for_spec.assert_not_called()

    def test_multi_axis_edc_log_creation_and_mat_npz_export_stay_frozen(self):
        metadata = [
            {"curve_id": "a", "index_bounds": (0, 1, 2, 3, 4, 5)},
            {"curve_id": "b", "index_bounds": (3, 4, 5, 6, 7, 9)},
        ]
        source = AnalysisPageSpec(
            "comparison",
            "EDC comparison",
            My3DAnalyzer.COMPARISON_PAGE_KIND,
            "data_process",
            params={
                "comparison_kind": "edc_curve",
                "x_match_policy": "per_curve",
                "base_curve": _curve("a", [0.0, 1.0], [2.0, 3.0]),
                "overlay_curves": [_curve("b", [10.0, 20.0, 30.0], [4.0, 5.0, 6.0])],
                "curve_metadata": metadata,
                "source_scope_metadata": _frozen_scope(),
                "source_rotation_angle": -8.5,
            },
        )
        analyzer = self._analyzer(source)
        created = []
        analyzer.left_workspace.add_page = created.append
        analyzer.current_render_context = analyzer._get_curve_comparison_context(source)
        analyzer._make_page_id = lambda: "edc-log"
        analyzer._make_unique_page_title = lambda title: title
        analyzer._seed_control_state_for_spec = Mock()

        self.assertTrue(analyzer._apply_log_to_current_curves())
        self.assertEqual(len(created), 1)
        log_spec = created[0]
        self.assertEqual(log_spec.params["comparison_kind"], "edc_curve")
        self.assertEqual(log_spec.params["x_match_policy"], "per_curve")
        self.assertEqual(log_spec.params["curve_metadata"], metadata)
        self.assertEqual(log_spec.params["source_rotation_angle"], -8.5)
        self.assertIsNot(log_spec.params["source_scope_metadata"], source.params["source_scope_metadata"])
        np.testing.assert_array_equal(
            log_spec.params["source_scope_metadata"]["roi_index_bounds"],
            source.params["source_scope_metadata"]["roi_index_bounds"],
        )

        analyzer.left_workspace.current_spec = lambda: log_spec
        analyzer._build_energy_dos_export_payload = Mock(
            side_effect=AssertionError("An EDC log must not be recomputed as Energy DOS")
        )
        with tempfile.TemporaryDirectory() as tmp:
            for extension in ("mat", "npz"):
                with self.subTest(extension=extension):
                    path = os.path.join(tmp, f"edc-log.{extension}")
                    analyzer._choose_export_path = lambda _title, _name, path=path: path
                    analyzer.export_current_result()
                    exported = self._load_export(path)
                    np.testing.assert_allclose(exported["E_1"].reshape(-1), [0.0, 1.0])
                    np.testing.assert_allclose(exported["E_2"].reshape(-1), [10.0, 20.0, 30.0])
                    np.testing.assert_allclose(
                        exported["intensity_1"].reshape(-1), np.log10([2.0, 3.0])
                    )
                    np.testing.assert_allclose(
                        exported["intensity_2"].reshape(-1), np.log10([4.0, 5.0, 6.0])
                    )
                    np.testing.assert_array_equal(
                        exported["index_bounds_1"].reshape(-1), [0, 1, 2, 3, 4, 5]
                    )
                    np.testing.assert_array_equal(
                        exported["index_bounds_2"].reshape(-1), [3, 4, 5, 6, 7, 9]
                    )
                    np.testing.assert_allclose(exported["source_rotation_angle"].reshape(-1), [-8.5])
                    self._assert_frozen_scope(exported)
        analyzer._build_energy_dos_export_payload.assert_not_called()
        analyzer._scope_for_spec.assert_not_called()
        analyzer._scope_export_metadata.assert_not_called()
        analyzer._get_display_state_for_spec.assert_not_called()

    def test_non_edc_pages_keep_existing_payload_routing(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        sentinel = ("Save Energy-DOS result", "energy_dos.mat", {})
        analyzer._build_energy_dos_export_payload = Mock(return_value=sentinel)
        for spec in (
            AnalysisPageSpec("dos", "DOS", "energy_dos", "data_process"),
            AnalysisPageSpec(
                "dos-log",
                "DOS log",
                My3DAnalyzer.LOG_1D_PAGE_KIND,
                "data_process",
                params={"comparison_kind": "energy_dos"},
            ),
        ):
            with self.subTest(page_kind=spec.page_kind):
                self.assertIs(analyzer._build_export_payload(spec, "raw", "coords"), sentinel)
        other_comparison = AnalysisPageSpec(
            "dos-comparison",
            "DOS comparison",
            My3DAnalyzer.COMPARISON_PAGE_KIND,
            "data_process",
            params={"comparison_kind": "energy_dos"},
        )
        self.assertIsNone(analyzer._build_export_payload(other_comparison, "raw", "coords"))
        self.assertEqual(analyzer._build_energy_dos_export_payload.call_count, 2)


if __name__ == "__main__":
    unittest.main()
