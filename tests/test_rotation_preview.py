import unittest
from types import SimpleNamespace

import numpy as np

from compute_backends import ComputeResult
from refactored_app import My3DAnalyzer
from refresh_pipeline import RefreshCause, RenderQuality


class _RecordingCache:
    def __init__(self):
        self.puts = []

    def put(self, key, value):
        self.puts.append((key, value))


class RotationPreviewTests(unittest.TestCase):
    def test_resampled_preview_is_rendered_without_polluting_exact_cache(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer._computed_volume_cache = _RecordingCache()
        analyzer._axis_prefix_cache = None
        analyzer._second_derivative_volume_cache = None
        analyzer._render_volume_override = None
        analyzer._last_compute_backend = "CPU"
        analyzer._active_refresh_page_id = lambda: "home"
        analyzer._finish_ui_refresh = lambda _request, _backend: None

        cache_key = ("frame", 123, 0, 12.0)
        preview = np.ones((20, 20, 10), dtype=np.float32, order="F")
        seen_override = []
        analyzer._render_active_page = lambda _quality, _cause: seen_override.append(
            analyzer._render_volume_override
        )
        request = SimpleNamespace(
            page_id="home",
            quality=RenderQuality.PREVIEW,
            cause=RefreshCause.ROTATION,
        )
        result = ComputeResult(
            value=preview,
            backend="CPU",
            metadata={"cache_key": cache_key, "resampled_preview": True},
        )

        My3DAnalyzer._on_refresh_result(analyzer, request, result)

        self.assertEqual(analyzer._computed_volume_cache.puts, [])
        self.assertEqual(len(seen_override), 1)
        self.assertEqual(seen_override[0][0], cache_key)
        self.assertIs(seen_override[0][1], preview)
        self.assertIsNone(analyzer._render_volume_override)

    def test_frame_lookup_uses_transient_preview_override(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        raw = np.zeros((8, 9, 10, 1), dtype=np.float32)
        preview = np.ones((4, 5, 5), dtype=np.float32, order="F")
        analyzer.rotation_angle = 12.0
        key = ("frame", id(raw), 0, 12.0)
        analyzer._render_volume_override = (key, preview)

        actual = My3DAnalyzer._get_rotated_frame(analyzer, raw, 0)

        self.assertIs(actual, preview)


if __name__ == "__main__":
    unittest.main()
