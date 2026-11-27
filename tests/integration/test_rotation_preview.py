import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PyQt5.QtWidgets import QApplication
from scipy.ndimage import rotate as scipy_rotate

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.crop_model import CropSelection
from bandscope.rendering.refresh_pipeline import RefreshCause, RenderQuality
from tests.support.view_recipe import control_window, synthetic_npz


def rotate_linear(frame, angle):
    return scipy_rotate(
        np.asarray(frame, dtype=np.float32), float(angle), axes=(0, 1),
        reshape=False, order=1, mode="constant", cval=0.0, prefilter=True,
    )


class _Volume:
    def __init__(self):
        self.origin = None
        self.orientation = None

    def SetOrigin(self, *origin):
        self.origin = origin

    def SetOrientation(self, *orientation):
        self.orientation = orientation


class _Session:
    def __init__(self):
        self.active = True
        self.volume = _Volume()
        self.render_count = 0
        self.quality = None

    def _set_interactive_quality(self, quality):
        self.quality = quality


class _Plotter:
    def __init__(self):
        self.render_count = 0

    def render(self):
        self.render_count += 1


class _Stack:
    def __init__(self, widget):
        self.widget = widget

    def currentWidget(self):
        return self.widget


class RotationPreviewTests(unittest.TestCase):
    def test_actor_preview_uses_delta_from_last_exact_rotation(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.plotter = _Plotter()
        analyzer.left_display_stack = _Stack(analyzer.plotter)
        analyzer.volume_session = _Session()
        analyzer._rendered_rotation_angle = 12.0
        analyzer._actor_preview_rotation_angle = None

        rendered = My3DAnalyzer._apply_rotation_actor_preview(analyzer, 15.0)

        self.assertTrue(rendered)
        self.assertEqual(analyzer.volume_session.volume.origin, (100.0, 100.0, 100.0))
        self.assertEqual(analyzer.volume_session.volume.orientation, (0.0, 0.0, 3.0))
        self.assertEqual(analyzer.volume_session.quality, RenderQuality.PREVIEW.value)
        self.assertEqual(analyzer.plotter.render_count, 1)

    def test_same_actor_preview_angle_is_not_rendered_twice(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.plotter = _Plotter()
        analyzer.left_display_stack = _Stack(analyzer.plotter)
        analyzer.volume_session = _Session()
        analyzer._rendered_rotation_angle = 0.0
        analyzer._actor_preview_rotation_angle = None

        My3DAnalyzer._apply_rotation_actor_preview(analyzer, 4.0)
        My3DAnalyzer._apply_rotation_actor_preview(analyzer, 4.0)

        self.assertEqual(analyzer.plotter.render_count, 1)
        self.assertEqual(analyzer.volume_session.render_count, 1)

    def test_preview_dispatch_stops_before_numerical_resampling_for_3d_actor(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.rotation_angle = 0.0
        analyzer._last_compute_backend = "CPU"
        analyzer._apply_rotation_actor_preview = lambda angle: angle == 7.0
        finished = []
        analyzer._finish_ui_refresh = lambda request, backend: finished.append((request, backend))
        analyzer.left_workspace = SimpleNamespace(
            current_spec=lambda: SimpleNamespace(page_id="home", page_kind="home"),
            home_spec=lambda: None,
        )
        analyzer._volume_source_descriptor = lambda *_args: self.fail(
            "3D actor preview must not start a numerical rotation job"
        )
        request = SimpleNamespace(
            page_id="home",
            quality=RenderQuality.PREVIEW,
            cause=RefreshCause.ROTATION,
            snapshot={"rotation_angle": 7.0},
        )

        My3DAnalyzer._dispatch_refresh_request(analyzer, request)

        self.assertEqual(analyzer.rotation_angle, 7.0)
        self.assertEqual(finished, [(request, "CPU")])

    @staticmethod
    def _numerical_preview_analyzer(page_kind):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.rotation_angle = 30.0
        analyzer._last_compute_backend = "CPU"
        analyzer._apply_rotation_actor_preview = lambda _angle: False
        spec = SimpleNamespace(page_id="page", page_kind=page_kind)
        analyzer.left_workspace = SimpleNamespace(
            current_spec=lambda: spec,
            home_spec=lambda: None,
        )
        rendered = []
        finished = []
        analyzer._volume_source_descriptor = lambda *_args: None
        analyzer._render_active_page_timed = lambda quality, cause: rendered.append(
            (quality, cause)
        )
        analyzer._finish_ui_refresh = lambda request, backend: finished.append(
            (request, backend)
        )
        return analyzer, rendered, finished

    def test_direct_axis_slice_preview_reaches_numerical_rendering(self):
        for page_kind in ("axis_integral", "axis_integral_crop"):
            with self.subTest(page_kind=page_kind):
                analyzer, rendered, finished = self._numerical_preview_analyzer(page_kind)
                request = SimpleNamespace(
                    page_id="page",
                    quality=RenderQuality.PREVIEW,
                    cause=RefreshCause.ROTATION,
                    snapshot={"rotation_angle": -12.5},
                )

                My3DAnalyzer._dispatch_refresh_request(analyzer, request)

                self.assertEqual(analyzer.rotation_angle, -12.5)
                self.assertEqual(rendered, [(RenderQuality.PREVIEW, RefreshCause.ROTATION)])
                self.assertEqual(finished, [(request, "CPU")])

    def test_frozen_derived_page_preview_still_skips_numerical_rendering(self):
        for page_kind in ("second_derivative", "energy_dos", "path_slice"):
            with self.subTest(page_kind=page_kind):
                analyzer, _rendered, finished = self._numerical_preview_analyzer(page_kind)
                analyzer._volume_source_descriptor = lambda *_args: self.fail(
                    "frozen derived pages must not start a numerical rotation job"
                )
                analyzer._render_active_page_timed = lambda *_args: self.fail(
                    "frozen derived pages must not render a numerical rotation preview"
                )
                request = SimpleNamespace(
                    page_id="page",
                    quality=RenderQuality.PREVIEW,
                    cause=RefreshCause.ROTATION,
                    snapshot={"rotation_angle": -12.5},
                )

                My3DAnalyzer._dispatch_refresh_request(analyzer, request)

                self.assertEqual(analyzer.rotation_angle, -12.5)
                self.assertEqual(finished, [(request, "CPU")])


class RotationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_axis_preview_updates_data_and_exact_result_uses_its_own_angle(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "source.npz"
            raw = synthetic_npz(path, seed=4, descending=True)
            window = control_window()
            self.addCleanup(window.close)
            self.assertTrue(window.load_data(str(path)))
            identity = window.core.source_identity
            if identity is not None and identity.future is not None:
                identity.future.result(timeout=30)
            window.timeline_bar.set_rotation_angle(30.0)
            window.on_rotation_angle_changed()
            home = window.left_workspace.current_spec()
            spec = window._build_canvas_axis_slice_spec(home, 2)
            window.left_workspace.add_page(spec)
            fixed_interval = dict(spec.params["axis_interval"])
            fixed_range = (spec.params["low"], spec.params["up"], spec.params["mid"])

            def expected(angle):
                rotated = rotate_linear(raw[..., 0], angle)
                low, up = fixed_range[:2]
                return np.sum(rotated[:, :, low:up + 1], axis=2, dtype=np.float64)

            def render(quality, cause):
                del quality, cause
                current = window.left_workspace.current_spec()
                window.current_render_context = window._compute_render_context(current)
                window._render_context_page_id = current.page_id

            def dispatch(angle, quality):
                request = SimpleNamespace(
                    page_id=spec.page_id,
                    quality=quality,
                    cause=RefreshCause.ROTATION,
                    snapshot={"rotation_angle": angle},
                )

                def submit_compute(submitted, work):
                    result = work()
                    window._on_refresh_result(submitted, result)

                with patch.object(
                    window.refresh_coordinator, "submit_compute", side_effect=submit_compute
                ):
                    window._dispatch_refresh_request(request)
                return request, window.current_render_context["data"].copy()

            with (
                patch.object(window, "_render_active_page", side_effect=render),
                patch.object(window, "_restore_recipe_scene"),
            ):
                preview_request, preview = dispatch(12.0, RenderQuality.PREVIEW)
                np.testing.assert_allclose(preview, expected(12.0), rtol=2e-6, atol=2e-6)
                self.assertEqual(window._last_completed_refresh, preview_request)
                self.assertEqual(spec.params["axis_interval"], fixed_interval)
                self.assertEqual(
                    (spec.params["low"], spec.params["up"], spec.params["mid"]), fixed_range
                )

                exact_request, exact = dispatch(-23.0, RenderQuality.EXACT)
                np.testing.assert_allclose(exact, expected(-23.0), rtol=2e-6, atol=2e-6)
                self.assertFalse(np.allclose(exact, preview))
                self.assertEqual(window._last_completed_refresh, exact_request)
                self.assertEqual(window._last_completed_refresh.quality, RenderQuality.EXACT)
                self.assertTrue(window._render_exact_ready)
                self.assertEqual(spec.params["axis_interval"], fixed_interval)
                self.assertEqual(spec.params["source_3d"]["rotation_angle"], 30.0)

            raw_data, coords = window._get_display_state_for_spec(spec)
            exported = window._get_axis_integral_export_context(spec, raw_data, coords)
            np.testing.assert_allclose(
                exported["export_data"]["sample"], expected(-23.0), rtol=2e-6, atol=2e-6
            )

    def test_zero_preserves_camera_and_allows_full_frame_roi_crop(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "source.npz"
            synthetic_npz(path)
            window = control_window()
            self.addCleanup(window.close)
            self.assertTrue(window.load_data(str(path)))
            overlay = window.canvas_tool_overlay
            overlay.set_rotation_angle(-7.5)
            window.on_rotation_angle_changed()
            camera_before = np.asarray(window._current_3d_camera_position(), dtype=float)
            source = window.left_workspace.current_spec()
            window.crop_controller.set_selection(
                CropSelection("3d", ("X", "Y", "E"), (1, 3, 1, 4, 1, 5)),
                notify=False,
            )

            window.on_cut()
            rotated = window.left_workspace.current_spec()
            self.assertIsNot(rotated, source)
            self.assertEqual(rotated.source_page_id, source.page_id)
            self.assertEqual(rotated.params["crop_regions"][0]["rotation_angle"], -7.5)
            self.assertNotIn("恢复为 0°", window.crop_controller.popup.error_label.text())

            window.left_workspace.activate_page(source.page_id)
            overlay.rotation_panel.zero_button.click()
            self.assertEqual(window.rotation_angle, 0.0)
            self.assertFalse(overlay.rotation_button.isChecked())
            np.testing.assert_allclose(
                np.asarray(window._current_3d_camera_position(), dtype=float), camera_before
            )
            window.crop_controller.set_selection(
                CropSelection("3d", ("X", "Y", "E"), (1, 3, 1, 4, 1, 5)),
                notify=False,
            )
            window.on_cut()

            cropped = window.left_workspace.current_spec()
            self.assertIsNot(cropped, source)
            self.assertNotEqual(cropped.data_scope_id, "full")
            self.assertEqual(
                window.data_scopes[cropped.data_scope_id].bounds, (5, 5, 4, 6, 7, 7)
            )


if __name__ == "__main__":
    unittest.main()
