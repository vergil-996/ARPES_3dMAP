import unittest
from types import SimpleNamespace

from refactored_app import My3DAnalyzer
from refresh_pipeline import RefreshCause, RenderQuality


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


if __name__ == "__main__":
    unittest.main()
