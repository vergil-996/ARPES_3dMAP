import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bandscope.app.refactored_app import My3DAnalyzer


class RenderStatusOverlayTests(unittest.TestCase):
    def test_render_status_overlay_is_removed(self):
        self.assertFalse(hasattr(My3DAnalyzer, "RENDER_STATUS_WIDTH"))
        self.assertFalse(hasattr(My3DAnalyzer, "_position_render_status"))
        self.assertFalse(hasattr(My3DAnalyzer, "_render_device_label"))

    def test_statusbar_still_updates_without_hardware_indicators(self):
        state_label = Mock()
        update_statusbar = Mock()
        analyzer = SimpleNamespace(
            status_state=state_label,
            _update_statusbar=update_statusbar,
        )

        My3DAnalyzer._update_render_status(analyzer, "complete", "NVIDIA GPU")

        state_label.setText.assert_called_once_with("● 就绪")
        state_label.setStyleSheet.assert_called_once()
        update_statusbar.assert_called_once_with()

    def test_tool_overlay_positioning_no_longer_needs_a_status_label(self):
        overlay = Mock()
        analyzer = SimpleNamespace(canvas_tool_overlay=overlay)

        My3DAnalyzer._position_canvas_tool_overlay(analyzer)

        overlay.reposition.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
