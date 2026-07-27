import unittest
from types import SimpleNamespace

from refactored_app import My3DAnalyzer


class _LabelStub:
    def __init__(self):
        self.width = None
        self.position = None
        self.raised = False

    def setFixedWidth(self, width):
        self.width = width

    def move(self, x, y):
        self.position = (x, y)

    def raise_(self):
        self.raised = True


class _StackStub:
    def __init__(self, width):
        self._width = width

    def width(self):
        return self._width


class RenderStatusOverlayTests(unittest.TestCase):
    def test_status_geometry_is_stable_for_normal_view_widths(self):
        label = _LabelStub()
        analyzer = SimpleNamespace(
            RENDER_STATUS_WIDTH=My3DAnalyzer.RENDER_STATUS_WIDTH,
            render_status_label=label,
            left_display_stack=_StackStub(800),
        )

        My3DAnalyzer._position_render_status(analyzer)
        first_geometry = label.width, label.position
        My3DAnalyzer._position_render_status(analyzer)

        self.assertEqual(first_geometry, (330, (456, 12)))
        self.assertEqual((label.width, label.position), first_geometry)
        self.assertTrue(label.raised)

    def test_status_width_is_bounded_on_a_narrow_view(self):
        label = _LabelStub()
        analyzer = SimpleNamespace(
            RENDER_STATUS_WIDTH=My3DAnalyzer.RENDER_STATUS_WIDTH,
            render_status_label=label,
            left_display_stack=_StackStub(240),
        )

        My3DAnalyzer._position_render_status(analyzer)

        self.assertEqual(label.width, 212)
        self.assertEqual(label.position, (14, 12))


if __name__ == "__main__":
    unittest.main()
