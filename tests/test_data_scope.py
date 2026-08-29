import unittest
from types import SimpleNamespace

import numpy as np
import pyvista as pv

from data_scope import DataScopeDescriptor, ScopedDataVolume, ScopedSpatialArray
from denoise_engines import DenoiseEngines
from compute_backends import ArrayLRUCache, CpuComputeBackend
from refactored_app import My3DAnalyzer
from render_core import VolumeRenderSession
from result_workspace import AnalysisPageSpec


class DataScopeNumericsTests(unittest.TestCase):
    def setUp(self):
        self.full = np.arange(6 * 7 * 8 * 3, dtype=np.float32).reshape(6, 7, 8, 3)
        self.descriptor = DataScopeDescriptor(
            "roi-1",
            self.full.shape,
            (1, 3, 2, 5, 3, 6),
            generation=1,
        )
        self.scoped = ScopedDataVolume(
            self.descriptor,
            self.descriptor.extract(self.full),
        )

    def _masked_frame(self, time_index):
        expected = np.zeros(self.full.shape[:3], dtype=np.float32)
        expected[self.descriptor.spatial_slices] = self.full[
            self.descriptor.spatial_slices + (time_index,)
        ]
        return expected

    def test_roi_keeps_only_a_compact_four_dimensional_view(self):
        self.assertEqual(self.scoped.values.shape, (3, 4, 4, 3))
        self.assertLess(self.scoped.values.nbytes, self.full.nbytes)
        self.assertTrue(np.shares_memory(self.scoped.values, self.full))
        self.assertEqual(self.scoped.shape, self.full.shape)

    def test_scope_cache_identity_is_stable_across_fresh_compact_views(self):
        second_view = ScopedDataVolume(
            self.descriptor,
            self.descriptor.extract(self.full),
        )
        self.assertEqual(
            My3DAnalyzer._raw_data_identity(self.scoped),
            My3DAnalyzer._raw_data_identity(second_view),
        )

    def test_frame_and_time_integral_match_a_full_zero_reference(self):
        frame = self.scoped.frame(1)
        np.testing.assert_array_equal(
            self.scoped.materialize_3d(frame),
            self._masked_frame(1),
        )

        compact_integral = self.scoped.time_integral(0, 2)
        expected = sum((self._masked_frame(index) for index in range(3)))
        np.testing.assert_array_equal(
            self.scoped.materialize_3d(compact_integral),
            expected,
        )

    def test_every_axis_integral_matches_a_full_zero_reference(self):
        frame = self.scoped.frame(1)
        masked = self._masked_frame(1)
        for axis, requested_range in enumerate(((0, 4), (3, 6), (0, 4))):
            low, up = requested_range
            actual = My3DAnalyzer._get_axis_integrated_data_from_raw(
                frame,
                axis,
                low,
                up,
            )
            slices = [slice(None)] * 3
            slices[axis] = slice(low, up + 1)
            expected = np.sum(masked[tuple(slices)], axis=axis, dtype=np.float32)
            np.testing.assert_array_equal(actual, expected)

    def test_non_overlapping_axis_range_returns_a_zero_image(self):
        result = My3DAnalyzer._get_axis_integrated_data_from_raw(
            self.scoped.frame(0),
            0,
            4,
            5,
        )
        self.assertEqual(result.shape, (7, 8))
        self.assertFalse(np.any(result))

    def test_second_derivative_uses_the_local_roi_boundary(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        constant_full = np.ones((6, 7, 8, 1), dtype=np.float32)
        scoped = ScopedDataVolume(
            self.descriptor,
            np.ones(self.descriptor.compact_shape, dtype=np.float32),
        )
        analyzer.rotation_angle = 0.0
        analyzer._computed_volume_cache = ArrayLRUCache(max_bytes=constant_full.nbytes)
        analyzer._rotation_cache = {}
        analyzer._second_derivative_volume_cache = None
        analyzer.page_image = SimpleNamespace(
            slider_time=SimpleNamespace(value=lambda: 0)
        )
        coords = {
            "X": np.linspace(-1, 1, 6),
            "Y": np.linspace(-2, 2, 7),
            "E": np.linspace(-0.5, 0.5, 8),
            "delay": np.asarray([0.0]),
        }
        context = analyzer._build_second_derivative_context_from_params(
            scoped,
            coords,
            {
                "source_page_kind": "home",
                "source_view": "2d",
                "slice_axis": 0,
                "slice_index": 2,
                "source_t_index": 0,
                "second_derivative_params": {
                    "threshold": 0.0,
                },
            },
        )

        self.assertEqual(context["data"].shape, (7, 8))
        np.testing.assert_allclose(context["data"], 0.0, atol=1e-6)

    def test_rotated_roi_matches_a_full_three_dimensional_zero_reference(self):
        backend = CpuComputeBackend(max_workers=1)
        angle = 17.0
        actual = backend.prepare_volume(
            self.scoped.values,
            source_mode="frame",
            frame_index=1,
            angle=angle,
            scope_bounds=self.descriptor.spatial_bounds,
            full_shape=self.descriptor.full_shape,
        )
        expected = backend.rotate_volume(self._masked_frame(1), angle)
        np.testing.assert_array_equal(actual, expected)

    def test_stale_full_cache_is_reduced_before_roi_geometry_is_attached(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        full_frame = np.asarray(self.full[..., 1], dtype=np.float32)

        restored = analyzer._restore_scoped_spatial(
            self.scoped,
            full_frame,
            0.0,
        )

        self.assertIsInstance(restored, ScopedSpatialArray)
        self.assertEqual(restored.shape, self.descriptor.compact_spatial_shape)
        np.testing.assert_array_equal(
            restored,
            full_frame[self.descriptor.spatial_slices],
        )

    def test_home_roi_context_uses_compact_values_without_reclipping_them(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.rotation_angle = 0.0
        analyzer.home_slice_info = None
        analyzer.clip_ranges = list(self.descriptor.spatial_bounds)
        analyzer._computed_volume_cache = ArrayLRUCache(max_bytes=self.full.nbytes)
        analyzer._rotation_cache = {}
        analyzer.page_image = SimpleNamespace(
            slider_time=SimpleNamespace(value=lambda: 1)
        )
        coords = {
            "X": np.arange(self.full.shape[0]),
            "Y": np.arange(self.full.shape[1]),
            "E": np.arange(self.full.shape[2]),
            "delay": np.arange(self.full.shape[3]),
        }

        context = analyzer._get_home_render_context(
            AnalysisPageSpec("home", "Home", "home", "test", data_scope_id="roi-1"),
            self.scoped,
            coords,
        )

        np.testing.assert_array_equal(
            context["data"],
            self.descriptor.extract(self.full)[..., 1],
        )
        self.assertEqual(context["data_bounds"], self.descriptor.spatial_bounds)
        self.assertIsNone(context["clip_ranges"])


class DataScopeLineageTests(unittest.TestCase):
    def test_new_page_inherits_its_direct_source_scope(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        source = np.zeros((6, 7, 8, 2), dtype=np.float32)
        analyzer.original_raw_data = source
        analyzer.data_scopes = {
            "full": DataScopeDescriptor.full(source.shape),
            "roi-1": DataScopeDescriptor("roi-1", source.shape, (1, 3, 2, 5, 3, 6), 1),
        }
        full_page = AnalysisPageSpec("old", "Old", "axis_integral", "test")
        roi_page = AnalysisPageSpec(
            "roi",
            "ROI",
            "axis_integral",
            "test",
            data_scope_id="roi-1",
        )
        pages = {"old": full_page, "roi": roi_page}
        analyzer.left_workspace = SimpleNamespace(
            page_by_id=lambda page_id: pages.get(page_id),
            current_spec=lambda: roi_page,
            home_spec=lambda: roi_page,
            page_buttons={},
        )

        from_old = AnalysisPageSpec(
            "child-old",
            "Child",
            "edc_curve",
            "test",
            source_page_id="old",
        )
        analyzer._assign_scope_to_spec(from_old)
        self.assertEqual(from_old.data_scope_id, "full")

        from_roi = AnalysisPageSpec(
            "child-roi",
            "Child",
            "edc_curve",
            "test",
            source_page_id="roi",
        )
        analyzer._assign_scope_to_spec(from_roi)
        self.assertEqual(from_roi.data_scope_id, "roi-1")
        self.assertIn("ROI #1", from_roi.title)

    def test_missing_roi_never_silently_falls_back_to_full_data(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        source = np.zeros((4, 5, 6, 2), dtype=np.float32)
        analyzer.original_raw_data = source
        analyzer.data_scopes = {
            "full": DataScopeDescriptor.full(source.shape),
        }

        self.assertIsNone(analyzer._scope_for_id("roi-missing"))

    def test_cut_creates_a_derived_crop_page_and_keeps_home_full(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        source = np.zeros((6, 7, 8, 2), dtype=np.float32)
        analyzer.original_raw_data = source
        analyzer.global_denoise_methods = []
        analyzer.rotation_angle = 0.0
        analyzer._scope_generation = 0
        analyzer.data_scopes = {"full": DataScopeDescriptor.full(source.shape)}
        analyzer._scope_committed_signatures = {"full": analyzer._denoise_signature([])}
        analyzer._scope_data_cache = ArrayLRUCache(max_bytes=source.nbytes)
        analyzer.precise_logical_bounds = None
        analyzer.last_synced_slice_texts = None
        analyzer.clip_ranges = None
        analyzer.home_slice_info = None

        home = AnalysisPageSpec("home", "Home", "home", "test")
        created = []
        analyzer.left_workspace = SimpleNamespace(
            current_spec=lambda: home,
            page_specs={"home": home},
            page_buttons={},
            add_page=lambda spec: created.append(spec),
        )
        analyzer.page_image = SimpleNamespace(get_slice_values=lambda: {})
        cut_result = {
            "value": {
                "clip_ranges": [1, 3, 2, 5, 3, 6],
                "slice_info": None,
                "logical_bounds": [1, 3, 2, 5, 3, 6],
            }
        }
        analyzer.core = SimpleNamespace(
            raw_data=source,
            physical_texts_to_logical_texts=lambda _texts: {},
            process_cut_logic=lambda _texts: cut_result["value"],
        )
        analyzer._sync_slice_edits_from_logical_bounds = lambda _bounds: None
        analyzer._persist_home_page_state = lambda _spec: None
        analyzer._invalidate_scope_render_state = lambda: None
        analyzer._request_scope_denoise_if_needed = lambda _spec: False
        analyzer._capture_control_state = lambda: {}
        analyzer._store_control_state = lambda _spec, _state: None
        analyzer.global_refresh = lambda: None

        analyzer.on_cut()

        # The home page is never mutated by a crop.
        self.assertEqual(home.data_scope_id, "full")

        # A new derived crop page is created as a direct child of home.
        self.assertEqual(len(created), 1)
        crop = created[0]
        self.assertEqual(crop.page_kind, "home")
        self.assertEqual(crop.source_page_id, "home")
        self.assertEqual(crop.data_scope_id, "roi-1")
        self.assertIn("ROI #1", crop.title)
        self.assertEqual(analyzer.data_scopes["roi-1"].compact_shape, (3, 4, 4, 2))
        self.assertEqual(analyzer.clip_ranges, [1, 3, 2, 5, 3, 6])

        original_pointer = int(source.__array_interface__["data"][0])
        cut_result["value"] = {
            "clip_ranges": [3, 5, 0, 2, 0, 2],
            "slice_info": None,
            "logical_bounds": [3, 5, 0, 2, 0, 2],
        }
        analyzer.on_cut()

        # A second crop is also a direct child of home (not of the first crop).
        self.assertEqual(home.data_scope_id, "full")
        self.assertEqual(len(created), 2)
        self.assertEqual(created[1].data_scope_id, "roi-2")
        self.assertEqual(created[1].source_page_id, "home")
        second_scope = analyzer.data_scopes["roi-2"]
        np.testing.assert_array_equal(
            second_scope.extract(source),
            source[3:6, 0:3, 0:3, :],
        )
        self.assertEqual(
            int(analyzer.original_raw_data.__array_interface__["data"][0]),
            original_pointer,
        )
        self.assertIn("roi-1", analyzer.data_scopes)

    def test_savgol_is_validated_against_the_compact_roi_axis(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        descriptor = DataScopeDescriptor(
            "roi-1",
            (8, 8, 9, 2),
            (1, 6, 1, 6, 3, 5),
            1,
        )
        methods = [
            {
                "label": DenoiseEngines.SG_METHOD_NAME,
                "params": {
                    "window_length": 5,
                    "polyorder": 2,
                    "smoothing_axis": "e",
                },
            }
        ]
        with self.assertRaisesRegex(ValueError, "ROI #1"):
            analyzer._validate_denoise_for_descriptor(methods, descriptor)

    def test_scope_cache_never_keeps_a_full_result_outside_its_budget(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        source = np.zeros((6, 7, 8, 2), dtype=np.float32)
        full = DataScopeDescriptor.full(source.shape)
        roi = DataScopeDescriptor("roi-1", source.shape, (1, 3, 2, 5, 3, 6), 1)
        analyzer.original_raw_data = source
        analyzer.data_scopes = {"full": full, "roi-1": roi}
        analyzer._scope_data_cache = ArrayLRUCache(max_bytes=source.nbytes)
        analyzer._scope_committed_signatures = {}
        analyzer.shared_denoised_raw_data = None
        signature = "sg"

        analyzer._store_scope_denoise_result("full", np.ones(full.compact_shape), signature)
        self.assertIsNotNone(analyzer.shared_denoised_raw_data)
        analyzer._store_scope_denoise_result("roi-1", np.ones(roi.compact_shape), signature)

        self.assertLessEqual(analyzer._scope_data_cache.current_bytes, source.nbytes)
        self.assertIsNone(analyzer.shared_denoised_raw_data)

    def test_back_switches_to_parent_then_unwinds_home_slice(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        source = np.zeros((6, 7, 8, 2), dtype=np.float32)
        home = AnalysisPageSpec("home", "Home", "home", "test", data_scope_id="full")
        derived = AnalysisPageSpec(
            "derived",
            "Derived",
            "axis_integral",
            "test",
            data_scope_id="full",
            source_page_id="home",
        )
        state = {"current": derived}
        activations = []

        def activate(page_id):
            activations.append(page_id)
            state["current"] = home

        analyzer.original_raw_data = source
        analyzer.data_scopes = {"full": DataScopeDescriptor.full(source.shape)}
        analyzer.left_workspace = SimpleNamespace(
            current_spec=lambda: state["current"],
            home_spec=lambda: home,
            page_specs={"home": home, "derived": derived},
            activate_page=activate,
        )
        analyzer.core = SimpleNamespace(raw_data=source)
        analyzer.clip_ranges = None
        analyzer.home_slice_info = None
        analyzer._sync_slice_edits_from_logical_bounds = lambda _bounds: None
        analyzer._persist_home_page_state = lambda _spec: None
        analyzer._get_full_logical_bounds = lambda: [0, 5, 0, 6, 0, 7]
        analyzer.global_refresh = lambda: None
        analyzer.plotter = SimpleNamespace(
            reset_camera=lambda: None,
            render=lambda: None,
        )
        analyzer._capture_3d_camera_position = lambda: None

        # From a derived page, back switches to its parent instead of mutating it.
        analyzer.on_back()
        self.assertEqual(activations, ["home"])
        self.assertEqual(home.data_scope_id, "full")

        # From the home page, back first clears the transient one-layer slice.
        analyzer.home_slice_info = {"axis": 0, "index": 2}
        analyzer.on_back()
        self.assertIsNone(analyzer.home_slice_info)
        self.assertIsNone(analyzer.clip_ranges)

        # With no slice and no crop, back just resets the selection box.
        analyzer.clip_ranges = [1, 3, 2, 5, 3, 6]
        analyzer.on_back()
        self.assertIsNone(analyzer.clip_ranges)


class ScopedVolumeRenderTests(unittest.TestCase):
    def test_compact_grid_uses_full_domain_spacing_and_absolute_roi_extent(self):
        plotter = pv.Plotter(off_screen=True, window_size=(240, 180))
        try:
            session = VolumeRenderSession(plotter)
            data = np.ones((3, 4, 3), dtype=np.float32)
            full_shape = (10, 11, 12)
            bounds = (2, 4, 3, 6, 5, 7)
            session.render(
                data,
                (0, 50, 100),
                "linear",
                show_axes=False,
                data_bounds=bounds,
                full_shape=full_shape,
                include_zero=True,
            )

            expected_spacing = tuple(
                200.0 / (size - 1) for size in full_shape
            )
            np.testing.assert_allclose(session.grid.spacing, expected_spacing)
            np.testing.assert_allclose(session.grid.origin, (0.0, 0.0, 0.0))
            self.assertEqual(tuple(session.grid.extent), bounds)
            self.assertEqual(session.host_buffer.shape, data.shape)
            self.assertEqual(session.level_info["data_min"], 0.0)
        finally:
            plotter.close()

    def test_second_roi_rebuilds_with_new_absolute_extent_and_values(self):
        plotter = pv.Plotter(off_screen=True, window_size=(240, 180))
        try:
            session = VolumeRenderSession(plotter)
            full_shape = (20, 21, 22)
            first_bounds = (2, 7, 3, 9, 4, 11)
            second_bounds = (8, 13, 7, 13, 9, 16)
            first = np.arange(6 * 7 * 8, dtype=np.float32).reshape(6, 7, 8)
            second = (1000 + np.arange(6 * 7 * 8, dtype=np.float32)).reshape(6, 7, 8)

            session.render(
                first,
                (0, 50, 100),
                "linear",
                show_axes=False,
                data_bounds=first_bounds,
                full_shape=full_shape,
                include_zero=True,
            )
            first_actor = session.volume

            session.render(
                second,
                (0, 50, 100),
                "linear",
                show_axes=False,
                data_bounds=second_bounds,
                full_shape=full_shape,
                include_zero=True,
            )

            self.assertIsNot(session.volume, first_actor)
            self.assertEqual(tuple(session.grid.extent), second_bounds)
            np.testing.assert_array_equal(session.host_buffer, np.asfortranarray(second))
            self.assertEqual(plotter.renderer.GetVolumes().GetNumberOfItems(), 1)
        finally:
            plotter.close()

    def test_shared_energy_feature_keeps_world_height_after_e_roi_moves(self):
        plotter = pv.Plotter(off_screen=True, window_size=(240, 180))
        try:
            session = VolumeRenderSession(plotter)
            full_shape = (10, 10, 20)
            full = np.zeros(full_shape, dtype=np.float32)
            feature_index = (4, 5, 10)
            full[feature_index] = 99.0
            feature_heights = []

            for bounds in (
                (2, 7, 2, 7, 4, 12),
                (2, 7, 2, 7, 7, 15),
            ):
                slices = tuple(
                    slice(bounds[2 * axis], bounds[2 * axis + 1] + 1)
                    for axis in range(3)
                )
                session.render(
                    full[slices],
                    (0, 50, 100),
                    "linear",
                    show_axes=False,
                    data_bounds=bounds,
                    full_shape=full_shape,
                    include_zero=True,
                )
                point_id = session.grid.ComputePointId(feature_index)
                self.assertEqual(
                    float(session.host_buffer[
                        feature_index[0] - bounds[0],
                        feature_index[1] - bounds[2],
                        feature_index[2] - bounds[4],
                    ]),
                    99.0,
                )
                feature_heights.append(float(session.grid.GetPoint(point_id)[2]))

            self.assertAlmostEqual(feature_heights[0], feature_heights[1])
            self.assertAlmostEqual(
                feature_heights[0],
                feature_index[2] * 200.0 / (full_shape[2] - 1),
            )
        finally:
            plotter.close()

    def test_renderer_rejects_full_data_paired_with_compact_roi_bounds(self):
        plotter = pv.Plotter(off_screen=True, window_size=(240, 180))
        try:
            session = VolumeRenderSession(plotter)
            with self.assertRaisesRegex(ValueError, "does not match data bounds"):
                session.render(
                    np.ones((10, 11, 12), dtype=np.float32),
                    (0, 50, 100),
                    "linear",
                    show_axes=False,
                    data_bounds=(2, 4, 3, 6, 5, 7),
                    full_shape=(10, 11, 12),
                    include_zero=True,
                )
        finally:
            plotter.close()


if __name__ == "__main__":
    unittest.main()
