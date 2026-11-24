from copy import deepcopy
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

import numpy as np
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.processing.compute_backends import CpuComputeBackend
from bandscope.processing.denoise_config import (
    DEFAULT_GAUSSIAN_PARAMS,
    GAUSSIAN_METHOD_NAME,
    LOW_RANK_METHOD_NAME,
    MEDIAN_METHOD_NAME,
    NLM_METHOD_NAME,
    SG_METHOD_NAME,
    SG_METHOD_ALIASES,
    WAVELET_METHOD_ALIASES,
    WAVELET_METHOD_NAME,
)
from bandscope.processing.denoise_engines import DenoiseEngines
from bandscope.rendering.refresh_pipeline import (
    ComputeJob,
    ComputeResult,
    RefreshCause,
    RefreshRequest,
    RenderQuality,
)
from bandscope.ui.denoise_pipeline import DenoisePipelineWidget


def _denoise_parameter_state():
    return {
        "gaussian": {"sigma_kx": 0.75, "sigma_ky": 1.25, "sigma_e": 0.0},
        "median": {"size_kx": 3, "size_ky": 5, "size_e": 1},
        "sg": {"window_length": 7, "polyorder": 3, "smoothing_axis": "kx"},
        "wavelet": {
            "wavelet": "sym4",
            "level": 2,
            "threshold_rule": "bayes",
            "threshold_mode": "hard",
            "strength": 0.8,
            "noise_sigma": 0.15,
        },
        "nlmeans": {
            "patch_size": 5,
            "patch_distance": 4,
            "h_scale": 1.3,
            "noise_sigma": 0.2,
        },
        "low_rank": {"rank": 2},
    }


def _node(node_id, label, params=None, enabled=True):
    return {
        "id": node_id,
        "label": label,
        "params": deepcopy(params or {}),
        "enabled": enabled,
    }


class _BlankControlStateStub:
    def __init__(self):
        self.state = {
            **_denoise_parameter_state(),
            "second_derivative": {"threshold": 0.1},
        }
        self.restores = []

    def export_state(self):
        return deepcopy(self.state)

    def restore_state(self, state, *, block_signals=True):
        restored = deepcopy(state or {})
        self.restores.append((restored, block_signals))
        for key, value in restored.items():
            self.state[key] = deepcopy(value)


class _RenderControlStub(QWidget):
    denoise_edited = pyqtSignal()
    denoise_committed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.denoise_pipeline = DenoisePipelineWidget(self)
        self.denoise_pipeline.pipeline_edited.connect(self.denoise_edited)
        self.denoise_pipeline.pipeline_committed.connect(self.denoise_committed)
        self.restores = []

    def get_denoise_settings(self):
        return self.denoise_pipeline.get_denoise_settings()

    def set_denoise_execution_state(self, state, message=""):
        self.denoise_pipeline.set_execution_state(state, message)

    def restore_state(self, state, *, block_signals=True):
        state = deepcopy(state or {})
        self.restores.append((state, block_signals))
        self.denoise_pipeline.restore_state(
            state.get("denoise_pipeline") or {"nodes": []},
            block_signals=block_signals,
        )


class _TimelineBarStub:
    def __init__(self):
        self.rotation_angle = 0.0
        self.restores = []

    def restore_state(self, state, *, block_signals=True):
        self.restores.append((deepcopy(state), block_signals))

    def get_rotation_angle(self):
        return self.rotation_angle


class _PageContainerStub:
    def __init__(self):
        self.index = 0

    def currentIndex(self):
        return self.index

    def count(self):
        return 1

    def setCurrentIndex(self, index):
        self.index = index


class _CacheStub(dict):
    def put(self, key, value):
        self[key] = value


class _WorkspaceStub:
    def __init__(self, spec):
        self.spec = spec
        self.home_page_id = spec.page_id
        self.page_specs = {spec.page_id: spec}
        self.activations = []

    def current_spec(self):
        return self.spec

    def page_by_id(self, page_id):
        return self.page_specs.get(page_id)

    def activate_page(self, page_id):
        self.activations.append(page_id)


class _RefreshCoordinatorStub:
    def __init__(self):
        self.generation = 0
        self.submitted = []
        self.cancelled = []

    def make_request(self, page_id, cause, quality, snapshot=None):
        self.generation += 1
        return RefreshRequest(
            self.generation,
            page_id,
            cause,
            quality,
            dict(snapshot or {}),
        )

    def submit_compute(self, request, work):
        self.submitted.append((request, work))

    def cancel_page(self, page_id):
        self.cancelled.append(page_id)


class _BackendManagerStub:
    def __init__(self):
        self.jobs = []

    def execute(self, job):
        self.jobs.append(job)
        return ComputeResult(
            value=np.array(job.payload["data"], copy=True),
            backend="CPU",
        )

    def selected_backend_name(self):
        return "CPU"


class _SignalStub:
    def __init__(self):
        self.emissions = []

    def emit(self, *args):
        self.emissions.append(args)


class _AnalyzerHarness:
    GLOBAL_DENOISE_PAGE_ID = My3DAnalyzer.GLOBAL_DENOISE_PAGE_ID
    SCOPE_DENOISE_PAGE_PREFIX = My3DAnalyzer.SCOPE_DENOISE_PAGE_PREFIX
    DENOISE_CONTROL_STATE_KEYS = My3DAnalyzer.DENOISE_CONTROL_STATE_KEYS
    FULL_SCOPE_ID = My3DAnalyzer.FULL_SCOPE_ID

    _on_denoise_edited = My3DAnalyzer._on_denoise_edited
    on_apply_denoise = My3DAnalyzer.on_apply_denoise
    _capture_global_denoise_control_state = My3DAnalyzer._capture_global_denoise_control_state
    _render_state_with_global_denoise = My3DAnalyzer._render_state_with_global_denoise
    _detail_state_with_global_denoise = My3DAnalyzer._detail_state_with_global_denoise
    _sync_denoise_execution_state_after_restore = My3DAnalyzer._sync_denoise_execution_state_after_restore
    _restore_page_ui_state = My3DAnalyzer._restore_page_ui_state
    _initialize_data_scopes = My3DAnalyzer._initialize_data_scopes
    _scope_for_id = My3DAnalyzer._scope_for_id
    _scope_for_spec = My3DAnalyzer._scope_for_spec
    _scope_cache_key = My3DAnalyzer._scope_cache_key
    _scope_values = My3DAnalyzer._scope_values
    _referenced_scope_descriptors = My3DAnalyzer._referenced_scope_descriptors
    _validate_denoise_for_descriptor = My3DAnalyzer._validate_denoise_for_descriptor
    _validate_denoise_for_scopes = My3DAnalyzer._validate_denoise_for_scopes
    _build_denoise_job = My3DAnalyzer._build_denoise_job
    _store_scope_denoise_result = My3DAnalyzer._store_scope_denoise_result
    _commit_shared_denoise = My3DAnalyzer._commit_shared_denoise
    _invalidate_denoise_dependents = My3DAnalyzer._invalidate_denoise_dependents
    _clear_axis_prefix_cache = My3DAnalyzer._clear_axis_prefix_cache
    _clone_coords = My3DAnalyzer._clone_coords
    _get_display_state_for_spec = My3DAnalyzer._get_display_state_for_spec
    _active_refresh_page_id = My3DAnalyzer._active_refresh_page_id
    _global_denoise_page_id = My3DAnalyzer._global_denoise_page_id
    _on_refresh_result = My3DAnalyzer._on_refresh_result
    _on_refresh_failed = My3DAnalyzer._on_refresh_failed
    _normalize_denoise_methods = staticmethod(My3DAnalyzer._normalize_denoise_methods)
    _denoise_signature = staticmethod(My3DAnalyzer._denoise_signature)
    _copy_state = staticmethod(My3DAnalyzer._copy_state)

    def __init__(self, page_render, source):
        self.page_render = page_render
        self.page_control_blank = _BlankControlStateStub()
        self.original_raw_data = source
        self.original_coords = {
            key: np.arange(size)
            for key, size in zip(("X", "Y", "E", "delay"), source.shape)
        }
        self.spec = SimpleNamespace(
            page_id="home",
            page_kind="home",
            params={},
            data_scope_id=self.FULL_SCOPE_ID,
        )
        self.left_workspace = _WorkspaceStub(self.spec)
        self.active_page_spec = self.spec
        self.last_visual_page_id = self.spec.page_id
        self.refresh_coordinator = _RefreshCoordinatorStub()
        self.backend_manager = _BackendManagerStub()
        self.refreshFailed = _SignalStub()
        self.global_denoise_methods = []
        self.shared_denoised_raw_data = None
        self.shared_denoise_signature = self._denoise_signature([])
        self.shared_denoise_version = 0
        self._pending_denoise_methods = None
        self._pending_denoise_signature = None
        self._pending_denoise_scope_id = None
        self._pending_scope_denoise_keys = set()
        self._pending_roi_scope_id = None
        self._syncing_controls = False
        self._rotation_cache = {}
        self._computed_volume_cache = _CacheStub()
        self._second_derivative_volume_cache = None
        self._axis_prefix_cache = None
        self._render_exact_ready = True
        self._last_compute_backend = "CPU"
        self._last_refresh_failure = None
        self.source_namespace = "source-current"
        self.timeline_bar = _TimelineBarStub()
        self.page_container = _PageContainerStub()
        self._preserve_control_tab_on_next_page_sync = False
        self.rotation_angle = 0.0
        self.persisted_specs = []
        self.export_updates = 0
        self.refreshes = []
        self.render_statuses = []
        self.ordinary_page_calls = []
        self.messages = []
        self._initialize_data_scopes()

    def _source_cache_namespace(self):
        return self.source_namespace

    def _control_state_owner(self, spec=None):
        return spec or self.active_page_spec or self.left_workspace.current_spec()

    def _ensure_page_display_state(self, _spec):
        return None

    def _restore_home_page_state(self, _spec):
        return None

    def _plugin_session(self):
        return None

    def _restore_enhancement_style(self):
        return None

    def _select_control_page(self, index):
        self.page_container.setCurrentIndex(index)

    def _render_active_page(self, *args):
        self.ordinary_page_calls.append(("render", args))

    def _finish_ui_refresh(self, *args):
        self.ordinary_page_calls.append(("finish", args))

    def _cache_display_fields(self, *args):
        self.ordinary_page_calls.append(("cache-display", args))

    def _persist_page_ui_state(self, spec=None):
        self.persisted_specs.append(spec)

    def _update_export_button_states(self):
        self.export_updates += 1

    def request_refresh(self, *args, **kwargs):
        self.refreshes.append((args, kwargs))

    def _update_render_status(self, state, backend):
        self.render_statuses.append((state, backend))

    def _show_message(self, *args):
        self.messages.append(args)


class DenoiseNumericsTests(unittest.TestCase):
    def test_historical_mojibake_method_aliases_remain_supported(self):
        self.assertIn("Savitzky-Golay婊ゆ尝", SG_METHOD_ALIASES)
        self.assertIn("灏忔尝鍘诲櫔", WAVELET_METHOD_ALIASES)
        self.assertTrue(DenoiseEngines._is_savgol_method("Savitzky-Golay婊ゆ尝"))
        self.assertTrue(DenoiseEngines._is_wavelet_method("灏忔尝鍘诲櫔"))

    def test_vectorized_4d_savgol_matches_frame_by_frame_result(self):
        data = np.random.default_rng(12).normal(size=(9, 8, 11, 4)).astype(np.float32)
        params = {
            "window_length": 5,
            "polyorder": 2,
            "smoothing_axis": "e",
        }
        expected = np.stack(
            [
                DenoiseEngines.savitzky_golay_filter_3d(data[..., frame], **params)
                for frame in range(data.shape[-1])
            ],
            axis=-1,
        )

        actual = DenoiseEngines.savitzky_golay_filter_4d(data, **params)

        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(actual.dtype, np.float32)

    def test_parallel_wavelet_matches_single_worker_result(self):
        data = np.random.default_rng(21).normal(size=(12, 10, 8, 3)).astype(np.float32)
        params = {
            "wavelet": "db1",
            "level": 1,
            "threshold_rule": "universal",
            "threshold_mode": "soft",
            "strength": 0.8,
        }

        expected = DenoiseEngines.wavelet_denoise_4d(
            data,
            max_workers=1,
            **params,
        )
        actual = DenoiseEngines.wavelet_denoise_4d(
            data,
            max_workers=4,
            **params,
        )

        np.testing.assert_array_equal(actual, expected)

    def test_cpu_backend_exposes_denoise_as_a_compute_job(self):
        data = np.random.default_rng(3).normal(size=(7, 6, 9, 2)).astype(np.float32)
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

        result = CpuComputeBackend(max_workers=2).execute(
            ComputeJob("denoise_pipeline", {"data": data, "methods": methods})
        )

        expected = DenoiseEngines.apply_pipeline(data, methods, max_workers=1)
        np.testing.assert_array_equal(result.value, expected)
        self.assertEqual(result.backend, "CPU")


class DenoiseValidationTests(unittest.TestCase):
    def test_every_canonical_method_is_validated_against_compact_roi_shape(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        descriptor = SimpleNamespace(label="ROI #2", compact_shape=(8, 9, 11, 2))
        method_specs = (
            (DenoiseEngines.GAUSSIAN_METHOD_NAME, DenoiseEngines.DEFAULT_GAUSSIAN_PARAMS),
            (DenoiseEngines.MEDIAN_METHOD_NAME, DenoiseEngines.DEFAULT_MEDIAN_PARAMS),
            (DenoiseEngines.SG_METHOD_NAME, DenoiseEngines.DEFAULT_SG_PARAMS),
            (DenoiseEngines.WAVELET_METHOD_NAME, DenoiseEngines.DEFAULT_WAVELET_PARAMS),
            (DenoiseEngines.NLM_METHOD_NAME, DenoiseEngines.DEFAULT_NLMEANS_PARAMS),
            (DenoiseEngines.LOW_RANK_METHOD_NAME, DenoiseEngines.DEFAULT_LOW_RANK_PARAMS),
        )
        methods = [
            {"label": name, "params": dict(params)} for name, params in method_specs
        ]

        with patch.object(DenoiseEngines, "validate_method") as validate_method:
            analyzer._validate_denoise_for_descriptor(methods, descriptor)

        self.assertEqual(
            validate_method.call_args_list,
            [
                call(name, dict(params), descriptor.compact_shape)
                for name, params in method_specs
            ],
        )

    def test_unknown_method_raises_with_scope_and_method_context(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        descriptor = SimpleNamespace(label="完整数据", compact_shape=(8, 9, 11, 2))

        with self.assertRaises(ValueError) as caught:
            analyzer._validate_denoise_for_descriptor(["不存在的方法"], descriptor)

        self.assertIn("完整数据", str(caught.exception))
        self.assertIn("不存在的方法", str(caught.exception))

    def test_invalid_compact_roi_parameters_are_not_silent(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        descriptor = SimpleNamespace(label="ROI #4", compact_shape=(5, 5, 2, 2))
        methods = [
            {
                "label": DenoiseEngines.SG_METHOD_NAME,
                "params": {"window_length": 5, "polyorder": 2, "smoothing_axis": "e"},
            }
        ]

        with self.assertRaises(ValueError) as caught:
            analyzer._validate_denoise_for_descriptor(methods, descriptor)

        self.assertIn("ROI #4", str(caught.exception))
        self.assertIn(DenoiseEngines.SG_METHOD_NAME, str(caught.exception))

    def test_wavelet_validation_error_keeps_level_context(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        descriptor = SimpleNamespace(label="ROI #5", compact_shape=(8, 8, 8, 1))
        methods = [
            {
                "label": DenoiseEngines.WAVELET_METHOD_NAME,
                "params": {"level": 6},
            }
        ]

        with patch.object(
            DenoiseEngines,
            "validate_method",
            side_effect=ValueError("分解级别无效"),
        ):
            with self.assertRaises(ValueError) as caught:
                analyzer._validate_denoise_for_descriptor(methods, descriptor)

        message = str(caught.exception)
        self.assertIn("ROI #5", message)
        self.assertIn(DenoiseEngines.WAVELET_METHOD_NAME, message)
        self.assertIn("级别 6", message)

    def test_non_mapping_params_reach_engine_validation_without_preconversion(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        descriptor = SimpleNamespace(label="完整数据", compact_shape=(8, 8, 8, 1))
        methods = [
            {
                "label": DenoiseEngines.WAVELET_METHOD_NAME,
                "params": [("level", 1)],
            }
        ]

        with self.assertRaises(ValueError) as caught:
            analyzer._validate_denoise_for_descriptor(methods, descriptor)

        self.assertIn("完整数据", str(caught.exception))
        self.assertIn("参数必须是键值映射", str(caught.exception))


class DenoisePipelineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_analyzer(self, shape=(8, 9, 11, 2)):
        page = _RenderControlStub()
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.close)
        source = np.random.default_rng(31).normal(size=shape).astype(np.float32)
        analyzer = _AnalyzerHarness(page, source)
        page.denoise_edited.connect(analyzer._on_denoise_edited)
        page.denoise_committed.connect(analyzer.on_apply_denoise)
        return analyzer

    def _stale_request(self, analyzer, target_state):
        pipeline = analyzer.page_render.denoise_pipeline
        pipeline.restore_state(
            {"nodes": [_node("gaussian", GAUSSIAN_METHOD_NAME, DEFAULT_GAUSSIAN_PARAMS)]}
        )
        analyzer.on_apply_denoise()
        request = analyzer.refresh_coordinator.submitted[-1][0]
        if target_state == "pending":
            pipeline._on_parameter_edited("gaussian")
        else:
            pipeline._on_parameter_committed(
                "gaussian",
                {**DEFAULT_GAUSSIAN_PARAMS, "sigma_kx": 2.0},
            )
        self.assertEqual(pipeline._execution_state, target_state)
        return request

    def _prime_control_state(self, analyzer):
        analyzer.spec.params["control_state"] = {
            "active_control_tab": 0,
            "image": {},
            "render": {},
            "denoise_detail": analyzer.page_control_blank.export_state(),
        }

    def _complete_current_job(self, analyzer):
        request, work = analyzer.refresh_coordinator.submitted[-1]
        with patch(
            "bandscope.app.refactored_app.QTimer.singleShot",
            side_effect=lambda _delay, callback: callback(),
        ):
            analyzer._on_refresh_result(request, work())

    def _cross_source_scope_request(self, analyzer):
        old_source = np.zeros_like(analyzer.original_raw_data)
        return RefreshRequest(
            999,
            f"{analyzer.SCOPE_DENOISE_PAGE_PREFIX}source-old:roi-1",
            RefreshCause.DATA_SCOPE,
            RenderQuality.EXACT,
            {
                "denoise_signature": "new-signature",
                "source_id": id(old_source),
                "scope_id": "roi-1",
                "roi_commit": True,
            },
        )

    def test_add_node_signal_submits_background_work_and_sets_running(self):
        analyzer = self._make_analyzer()
        pipeline = analyzer.page_render.denoise_pipeline

        pipeline._add_node(GAUSSIAN_METHOD_NAME)

        self.assertEqual(len(analyzer.refresh_coordinator.submitted), 1)
        self.assertEqual(analyzer.backend_manager.jobs, [])
        self.assertEqual(pipeline._execution_state, "running")
        request, work = analyzer.refresh_coordinator.submitted[0]
        self.assertEqual(request.cause, RefreshCause.DENOISE)
        self.assertEqual(request.quality, RenderQuality.EXACT)
        self.assertTrue(callable(work))
        self.assertEqual(analyzer.messages, [])

    def test_numeric_edit_debounces_600ms_and_editing_finished_submits_now(self):
        analyzer = self._make_analyzer()
        pipeline = analyzer.page_render.denoise_pipeline
        pipeline._add_node(GAUSSIAN_METHOD_NAME)
        node_id = pipeline.export_state()["nodes"][0]["id"]
        dialog = pipeline.open_parameter_dialog(node_id)
        control = dialog._controls["sigma_kx"]

        control.setValue(1.75)

        self.assertEqual(dialog._debounce.interval(), 600)
        self.assertTrue(dialog._debounce.isActive())
        self.assertEqual(pipeline._execution_state, "pending")
        self.assertEqual(len(analyzer.refresh_coordinator.submitted), 1)

        QTest.qWait(700)

        self.assertEqual(len(analyzer.refresh_coordinator.submitted), 2)
        self.assertEqual(pipeline._execution_state, "running")

        control.setValue(2.25)
        self.assertEqual(pipeline._execution_state, "pending")
        control.editingFinished.emit()

        self.assertFalse(dialog._debounce.isActive())
        self.assertEqual(len(analyzer.refresh_coordinator.submitted), 3)
        self.assertEqual(pipeline._execution_state, "running")
        analyzer.refresh_coordinator.submitted[-1][1]()
        methods = analyzer.backend_manager.jobs[-1].payload["methods"]
        self.assertEqual(methods[0]["params"]["sigma_kx"], 2.25)

    def test_reorder_option_and_remove_signals_submit_immediately(self):
        analyzer = self._make_analyzer()
        pipeline = analyzer.page_render.denoise_pipeline
        pipeline.restore_state(
            {
                "nodes": [
                    _node("sg", SG_METHOD_NAME),
                    _node("gaussian", GAUSSIAN_METHOD_NAME),
                ]
            }
        )
        analyzer.on_apply_denoise()

        pipeline._move_to("gaussian", 0)
        self.assertEqual(len(analyzer.refresh_coordinator.submitted), 2)

        dialog = pipeline.open_parameter_dialog("sg")
        axis = dialog._controls["smoothing_axis"]
        axis.setCurrentIndex(axis.findData("kx"))
        self.assertFalse(dialog._debounce.isActive())
        self.assertEqual(len(analyzer.refresh_coordinator.submitted), 3)

        pipeline._remove_node("gaussian")
        self.assertEqual(len(analyzer.refresh_coordinator.submitted), 4)
        self.assertEqual(pipeline._execution_state, "running")
        self.assertEqual(analyzer.backend_manager.jobs, [])

    def test_repeated_algorithms_keep_independent_params_and_disabled_nodes_out_of_job(self):
        analyzer = self._make_analyzer()
        pipeline = analyzer.page_render.denoise_pipeline
        first_params = {**DEFAULT_GAUSSIAN_PARAMS, "sigma_kx": 0.5}
        second_params = {**DEFAULT_GAUSSIAN_PARAMS, "sigma_kx": 2.5}
        pipeline.restore_state(
            {
                "nodes": [
                    _node("first", GAUSSIAN_METHOD_NAME, first_params),
                    _node("second", GAUSSIAN_METHOD_NAME, second_params),
                    _node("disabled", LOW_RANK_METHOD_NAME, {"rank": 1}, enabled=False),
                ]
            }
        )

        analyzer.on_apply_denoise()
        analyzer.refresh_coordinator.submitted[-1][1]()

        methods = analyzer.backend_manager.jobs[-1].payload["methods"]
        self.assertEqual([method["id"] for method in methods], ["first", "second"])
        self.assertEqual(methods[0]["params"]["sigma_kx"], 0.5)
        self.assertEqual(methods[1]["params"]["sigma_kx"], 2.5)
        self.assertNotIn("disabled", repr(methods))

        dialog = pipeline.open_parameter_dialog("second")
        control = dialog._controls["sigma_kx"]
        control.setValue(3.25)
        control.editingFinished.emit()
        analyzer.refresh_coordinator.submitted[-1][1]()

        updated = analyzer.backend_manager.jobs[-1].payload["methods"]
        self.assertEqual(updated[0]["params"]["sigma_kx"], 0.5)
        self.assertEqual(updated[1]["params"]["sigma_kx"], 3.25)
        self.assertNotIn("disabled", repr(updated))

    def test_capture_and_restore_include_all_nodes_and_deep_copy_state(self):
        analyzer = self._make_analyzer()
        pipeline = analyzer.page_render.denoise_pipeline
        source_nodes = [
            _node("first", GAUSSIAN_METHOD_NAME, {"sigma_kx": 0.75}),
            _node("second", GAUSSIAN_METHOD_NAME, {"sigma_kx": 2.5}, enabled=False),
            _node("sg", SG_METHOD_NAME, {"window_length": 7, "polyorder": 3}),
        ]
        pipeline.restore_state({"nodes": source_nodes})

        captured = analyzer._capture_global_denoise_control_state()

        self.assertEqual(set(captured), {"nodes", "methods", "legacy_controls"})
        self.assertEqual(captured["nodes"], pipeline.export_state()["nodes"])
        self.assertEqual(len(captured["nodes"]), 3)
        self.assertFalse(captured["nodes"][1]["enabled"])
        self.assertEqual(len(captured["methods"]), 2)
        self.assertEqual(captured["legacy_controls"], _denoise_parameter_state())
        for key, value in captured["legacy_controls"].items():
            self.assertIsNot(value, analyzer.page_control_blank.state[key])
        self.assertIsNot(captured["nodes"], pipeline._nodes)
        self.assertIsNot(captured["nodes"][0]["params"], pipeline._nodes[0]["params"])
        source_nodes[0]["params"]["sigma_kx"] = 9.0
        self.assertEqual(pipeline._nodes[0]["params"]["sigma_kx"], 0.75)
        captured["nodes"][0]["params"]["sigma_kx"] = 4.0
        self.assertEqual(pipeline._nodes[0]["params"]["sigma_kx"], 0.75)

        saved = deepcopy(captured)
        render_state = analyzer._render_state_with_global_denoise(
            {"combo_map": "A"}, captured
        )
        other = self._make_analyzer()
        other.page_render.denoise_pipeline.restore_state(
            render_state["denoise_pipeline"]
        )

        self.assertEqual(
            other.page_render.denoise_pipeline.export_state()["nodes"],
            saved["nodes"],
        )
        captured["nodes"][1]["params"]["sigma_kx"] = 8.0
        self.assertEqual(
            other.page_render.denoise_pipeline._nodes[1]["params"]["sigma_kx"],
            2.5,
        )
        detail = analyzer._detail_state_with_global_denoise(
            {"second_derivative": {"threshold": 0.35}, "waterfall": {"k_step": 0.1}},
            captured,
        )
        self.assertEqual(detail["second_derivative"], {"threshold": 0.35})
        self.assertEqual(detail["waterfall"], {"k_step": 0.1})
        for key in analyzer.DENOISE_CONTROL_STATE_KEYS:
            self.assertNotIn(key, detail)

    def test_legacy_global_params_migrate_only_to_nodes_not_back_to_blank_page(self):
        analyzer = self._make_analyzer()
        legacy = _denoise_parameter_state()
        method_names = [
            GAUSSIAN_METHOD_NAME,
            MEDIAN_METHOD_NAME,
            SG_METHOD_NAME,
            WAVELET_METHOD_NAME,
            NLM_METHOD_NAME,
            LOW_RANK_METHOD_NAME,
        ]
        global_state = {"methods": method_names, **legacy}

        render_state = analyzer._render_state_with_global_denoise({}, global_state)
        detail_state = analyzer._detail_state_with_global_denoise(
            {
                **{key: {"stale": True} for key in analyzer.DENOISE_CONTROL_STATE_KEYS},
                "second_derivative": {"threshold": 0.35},
            },
            global_state,
        )
        pipeline = analyzer.page_render.denoise_pipeline
        pipeline.restore_state(render_state["denoise_pipeline"])
        before_blank = analyzer.page_control_blank.export_state()
        analyzer.page_control_blank.restore_state(detail_state)

        nodes = pipeline.export_state()["nodes"]
        self.assertEqual([node["label"] for node in nodes], method_names)
        for node, key in zip(nodes, analyzer.DENOISE_CONTROL_STATE_KEYS):
            self.assertEqual(node["params"], legacy[key])
        for key in analyzer.DENOISE_CONTROL_STATE_KEYS:
            self.assertNotIn(key, detail_state)
            self.assertEqual(
                analyzer.page_control_blank.export_state()[key],
                before_blank[key],
            )
        self.assertEqual(
            analyzer.page_control_blank.export_state()["second_derivative"],
            {"threshold": 0.35},
        )

    def test_validation_failure_only_sets_inline_error_without_popup_or_submit(self):
        analyzer = self._make_analyzer(shape=(8, 9, 2, 2))
        pipeline = analyzer.page_render.denoise_pipeline

        pipeline._add_node(SG_METHOD_NAME)

        self.assertEqual(analyzer.refresh_coordinator.submitted, [])
        self.assertEqual(analyzer.backend_manager.jobs, [])
        self.assertEqual(analyzer.messages, [])
        self.assertEqual(pipeline._execution_state, "error")
        self.assertTrue(pipeline.error_panel.isVisibleTo(pipeline))
        self.assertIn(SG_METHOD_NAME, pipeline.error_label.text())
        self.assertEqual(analyzer.persisted_specs, [])

    def test_current_result_sets_applied_and_requests_exact_refresh(self):
        analyzer = self._make_analyzer()
        analyzer.spec.page_id = "ordinary-1d"
        analyzer.spec.page_kind = "energy_dos"
        analyzer.left_workspace.page_specs = {analyzer.spec.page_id: analyzer.spec}
        analyzer.last_visual_page_id = analyzer.spec.page_id
        pipeline = analyzer.page_render.denoise_pipeline
        pipeline._add_node(GAUSSIAN_METHOD_NAME)
        self.assertEqual(pipeline._execution_state, "running")
        request, work = analyzer.refresh_coordinator.submitted[-1]

        result = work()
        with patch(
            "bandscope.app.refactored_app.QTimer.singleShot",
            side_effect=lambda _delay, callback: callback(),
        ):
            analyzer._on_refresh_result(request, result)

        self.assertEqual(pipeline._execution_state, "applied")
        self.assertIsNotNone(analyzer.shared_denoised_raw_data)
        self.assertFalse(analyzer.shared_denoised_raw_data.flags.writeable)
        self.assertEqual(analyzer.refresh_coordinator.cancelled, ["ordinary-1d"])
        self.assertEqual(len(analyzer.refreshes), 1)
        args, kwargs = analyzer.refreshes[0]
        self.assertEqual(args, (RefreshCause.DENOISE, RenderQuality.EXACT))
        self.assertTrue(kwargs["immediate"])

    def test_stale_result_does_not_replace_newer_pending_or_running_state(self):
        for target_state in ("pending", "running"):
            with self.subTest(target_state=target_state):
                analyzer = self._make_analyzer()
                request = self._stale_request(analyzer, target_state)
                pending_signature = analyzer._pending_denoise_signature

                analyzer._on_refresh_result(
                    request,
                    ComputeResult(
                        value=np.ones_like(analyzer.original_raw_data),
                        metadata={"denoise_methods": analyzer._pending_denoise_methods or []},
                    ),
                )

                pipeline = analyzer.page_render.denoise_pipeline
                self.assertEqual(pipeline._execution_state, target_state)
                self.assertEqual(analyzer._pending_denoise_signature, pending_signature)
                self.assertIsNone(analyzer.shared_denoised_raw_data)
                self.assertEqual(analyzer.global_denoise_methods, [])
                self.assertEqual(analyzer.refreshes, [])

    def test_stale_failure_does_not_replace_newer_pending_or_running_state(self):
        for target_state in ("pending", "running"):
            with self.subTest(target_state=target_state):
                analyzer = self._make_analyzer()
                request = self._stale_request(analyzer, target_state)
                pending_signature = analyzer._pending_denoise_signature

                analyzer._on_refresh_failed(request, "过期任务失败")

                pipeline = analyzer.page_render.denoise_pipeline
                self.assertEqual(pipeline._execution_state, target_state)
                self.assertEqual(analyzer._pending_denoise_signature, pending_signature)
                self.assertFalse(pipeline.error_panel.isVisibleTo(pipeline))

    def test_cross_source_stale_scope_callbacks_preserve_new_roi_and_render_state(self):
        for outcome in ("success", "failure"):
            for target_state in ("idle", "pending", "running", "applied", "error"):
                with self.subTest(outcome=outcome, target_state=target_state):
                    analyzer = self._make_analyzer()
                    pipeline = analyzer.page_render.denoise_pipeline
                    pipeline.set_execution_state(
                        target_state,
                        "当前错误" if target_state == "error" else "",
                    )
                    roi = SimpleNamespace(scope_id="roi-1")
                    analyzer.data_scopes["roi-1"] = roi
                    analyzer._scope_committed_signatures["roi-1"] = "new-signature"
                    analyzer._pending_scope_denoise_keys = {
                        ("roi-1", "new-signature")
                    }
                    analyzer._pending_roi_scope_id = "roi-1"
                    analyzer._pending_denoise_signature = "current-global"
                    analyzer.shared_denoise_signature = "new-signature"
                    analyzer.shared_denoise_version = 7
                    analyzer._render_exact_ready = False
                    analyzer._last_refresh_failure = "existing-failure"
                    analyzer.refreshFailed.emissions = ["existing-emission"]
                    analyzer.render_statuses = [("existing", "CPU")]
                    analyzer.export_updates = 4
                    request = self._cross_source_scope_request(analyzer)

                    if outcome == "success":
                        analyzer._on_refresh_result(
                            request,
                            ComputeResult(value=np.ones_like(analyzer.original_raw_data)),
                        )
                    else:
                        analyzer._on_refresh_failed(request, "旧源 scope 失败")

                    self.assertIs(analyzer.data_scopes.get("roi-1"), roi)
                    self.assertEqual(
                        analyzer._scope_committed_signatures["roi-1"],
                        "new-signature",
                    )
                    self.assertEqual(
                        analyzer._pending_scope_denoise_keys,
                        {("roi-1", "new-signature")},
                    )
                    self.assertEqual(analyzer._pending_roi_scope_id, "roi-1")
                    self.assertEqual(
                        analyzer._pending_denoise_signature,
                        "current-global",
                    )
                    self.assertEqual(pipeline._execution_state, target_state)
                    self.assertEqual(
                        pipeline._execution_message,
                        "当前错误" if target_state == "error" else "",
                    )
                    self.assertEqual(analyzer.shared_denoise_signature, "new-signature")
                    self.assertEqual(analyzer.shared_denoise_version, 7)
                    self.assertFalse(analyzer._render_exact_ready)
                    self.assertEqual(analyzer._last_refresh_failure, "existing-failure")
                    self.assertEqual(analyzer.refreshFailed.emissions, ["existing-emission"])
                    self.assertEqual(analyzer.render_statuses, [("existing", "CPU")])
                    self.assertEqual(analyzer.export_updates, 4)
                    self.assertEqual(analyzer.refreshes, [])
                    self.assertEqual(analyzer.ordinary_page_calls, [])
                    self.assertEqual(analyzer._computed_volume_cache, {})

    def test_cross_source_global_callbacks_do_not_enter_page_refresh_path(self):
        for outcome in ("success", "failure"):
            with self.subTest(outcome=outcome):
                analyzer = self._make_analyzer()
                pipeline = analyzer.page_render.denoise_pipeline
                pipeline.restore_state(
                    {"nodes": [_node("current", GAUSSIAN_METHOD_NAME)]}
                )
                analyzer.on_apply_denoise()
                old_source = np.zeros_like(analyzer.original_raw_data)
                request = RefreshRequest(
                    999,
                    f"{analyzer.GLOBAL_DENOISE_PAGE_ID}:source-old",
                    RefreshCause.DENOISE,
                    RenderQuality.EXACT,
                    {
                        "denoise_signature": "old-signature",
                        "source_id": id(old_source),
                        "scope_id": analyzer.FULL_SCOPE_ID,
                    },
                )
                analyzer.spec.page_id = request.page_id
                analyzer.left_workspace.page_specs = {request.page_id: analyzer.spec}
                analyzer._computed_volume_cache["existing"] = "keep"
                analyzer._render_exact_ready = False
                analyzer._last_refresh_failure = "existing-failure"
                analyzer.refreshFailed.emissions = ["existing-emission"]
                analyzer.render_statuses = [("existing", "CPU")]
                analyzer.export_updates = 4
                pending_signature = analyzer._pending_denoise_signature
                shared_signature = analyzer.shared_denoise_signature

                if outcome == "success":
                    analyzer._on_refresh_result(
                        request,
                        ComputeResult(
                            value=np.ones_like(analyzer.original_raw_data),
                            metadata={
                                "cache_key": "stale-cache",
                                "display_cache": ("stale-display",),
                            },
                        ),
                    )
                else:
                    analyzer._on_refresh_failed(request, "旧源 global 失败")

                self.assertEqual(analyzer.ordinary_page_calls, [])
                self.assertEqual(analyzer._computed_volume_cache, {"existing": "keep"})
                self.assertEqual(pipeline._execution_state, "running")
                self.assertEqual(analyzer._pending_denoise_signature, pending_signature)
                self.assertEqual(analyzer.shared_denoise_signature, shared_signature)
                self.assertIsNone(analyzer.shared_denoised_raw_data)
                self.assertFalse(analyzer._render_exact_ready)
                self.assertEqual(analyzer._last_refresh_failure, "existing-failure")
                self.assertEqual(analyzer.refreshFailed.emissions, ["existing-emission"])
                self.assertEqual(analyzer.render_statuses, [("existing", "CPU")])
                self.assertEqual(analyzer.export_updates, 4)
                self.assertEqual(analyzer.refresh_coordinator.cancelled, [])
                self.assertEqual(analyzer.refreshes, [])

    def test_same_source_stale_global_failure_does_not_emit_generic_failure(self):
        for target_state in ("pending", "running"):
            with self.subTest(target_state=target_state):
                analyzer = self._make_analyzer()
                request = self._stale_request(analyzer, target_state)
                analyzer._render_exact_ready = False
                analyzer._last_refresh_failure = "existing-failure"
                analyzer.refreshFailed.emissions = ["existing-emission"]
                analyzer.render_statuses = [("existing", "CPU")]
                analyzer.export_updates = 4

                analyzer._on_refresh_failed(request, "旧签名失败")

                self.assertEqual(analyzer._last_refresh_failure, "existing-failure")
                self.assertEqual(analyzer.refreshFailed.emissions, ["existing-emission"])
                self.assertEqual(analyzer.render_statuses, [("existing", "CPU")])
                self.assertEqual(analyzer.export_updates, 4)
                self.assertFalse(analyzer._render_exact_ready)
                self.assertEqual(
                    analyzer.page_render.denoise_pipeline._execution_state,
                    target_state,
                )

    def test_restore_page_ui_state_keeps_running_job_running(self):
        analyzer = self._make_analyzer()
        pipeline = analyzer.page_render.denoise_pipeline
        pipeline._add_node(GAUSSIAN_METHOD_NAME)
        pending_signature = analyzer._pending_denoise_signature
        self._prime_control_state(analyzer)

        analyzer._restore_page_ui_state(analyzer.spec)

        self.assertEqual(pipeline._execution_state, "running")
        self.assertEqual(analyzer._pending_denoise_signature, pending_signature)
        self.assertEqual(len(analyzer.refresh_coordinator.submitted), 1)
        self.assertFalse(analyzer._syncing_controls)

    def test_restore_page_ui_state_marks_committed_pipeline_applied(self):
        analyzer = self._make_analyzer()
        pipeline = analyzer.page_render.denoise_pipeline
        pipeline._add_node(GAUSSIAN_METHOD_NAME)
        self._complete_current_job(analyzer)
        self.assertEqual(pipeline._execution_state, "applied")
        self._prime_control_state(analyzer)

        analyzer._restore_page_ui_state(analyzer.spec)

        self.assertEqual(pipeline._execution_state, "applied")
        self.assertIsNone(analyzer._pending_denoise_signature)
        self.assertEqual(len(analyzer.refresh_coordinator.submitted), 1)
        self.assertFalse(analyzer._syncing_controls)

    def test_restore_page_ui_state_marks_draft_pending_without_submit(self):
        analyzer = self._make_analyzer()
        pipeline = analyzer.page_render.denoise_pipeline
        pipeline._add_node(GAUSSIAN_METHOD_NAME)
        self._complete_current_job(analyzer)
        pipeline.restore_state(
            {
                "nodes": [
                    _node(
                        "draft",
                        GAUSSIAN_METHOD_NAME,
                        {**DEFAULT_GAUSSIAN_PARAMS, "sigma_kx": 3.0},
                    )
                ]
            }
        )
        analyzer._on_denoise_edited()
        self.assertEqual(pipeline._execution_state, "pending")
        self._prime_control_state(analyzer)

        analyzer._restore_page_ui_state(analyzer.spec)

        self.assertEqual(pipeline._execution_state, "pending")
        self.assertIsNone(analyzer._pending_denoise_signature)
        self.assertEqual(len(analyzer.refresh_coordinator.submitted), 1)
        self.assertFalse(analyzer._syncing_controls)

    def test_empty_pipeline_restores_original_data_without_background_submit(self):
        analyzer = self._make_analyzer()
        pipeline = analyzer.page_render.denoise_pipeline
        pipeline._add_node(GAUSSIAN_METHOD_NAME)
        request, work = analyzer.refresh_coordinator.submitted[-1]
        with patch(
            "bandscope.app.refactored_app.QTimer.singleShot",
            side_effect=lambda _delay, callback: callback(),
        ):
            analyzer._on_refresh_result(request, work())
        analyzer.refreshes.clear()
        node_id = pipeline.export_state()["nodes"][0]["id"]

        pipeline._remove_node(node_id)

        self.assertEqual(len(analyzer.refresh_coordinator.submitted), 1)
        self.assertEqual(pipeline._execution_state, "applied")
        self.assertEqual(analyzer.global_denoise_methods, [])
        self.assertIsNone(analyzer.shared_denoised_raw_data)
        display, _coords = analyzer._get_display_state_for_spec(analyzer.spec)
        self.assertIs(display, analyzer.original_raw_data)
        np.testing.assert_array_equal(display, analyzer.original_raw_data)
        self.assertEqual(len(analyzer.refreshes), 1)

    def test_all_live_page_specs_share_one_committed_array(self):
        analyzer = self._make_analyzer()
        analyzer.page_render.denoise_pipeline.restore_state(
            {"nodes": [_node("sg", SG_METHOD_NAME)]}
        )
        methods = analyzer.page_render.get_denoise_settings()
        analyzer._commit_shared_denoise(
            methods,
            np.ones_like(analyzer.original_raw_data),
        )
        first_spec = SimpleNamespace(
            page_id="3d", params={}, data_scope_id=analyzer.FULL_SCOPE_ID
        )
        second_spec = SimpleNamespace(
            page_id="1d", params={}, data_scope_id=analyzer.FULL_SCOPE_ID
        )

        first, first_coords = analyzer._get_display_state_for_spec(first_spec)
        second, second_coords = analyzer._get_display_state_for_spec(second_spec)

        self.assertIs(first, analyzer.shared_denoised_raw_data)
        self.assertIs(second, analyzer.shared_denoised_raw_data)
        self.assertIsNot(first_coords, second_coords)
        self.assertFalse(np.shares_memory(first_coords["E"], second_coords["E"]))


if __name__ == "__main__":
    unittest.main()
