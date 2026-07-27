import os
import unittest
from unittest.mock import patch

from control_layout_utils import align_scroll_content
from qt_bootstrap import configure_qt_high_dpi


class _Animation:
    def __init__(self):
        self.current = None
        self.target = None

    def setCurrent(self, position):
        self.current = position

    def setTarget(self, position):
        self.target = position


class _GeometryStub:
    def __init__(self, width, height, *, y=0):
        self._width = width
        self._height = height
        self._y = y
        self.position = None
        self.updated = False

    def width(self):
        return self._width

    def height(self):
        return self._height

    def y(self):
        return self._y

    def move(self, x, y):
        self.position = (x, y)
        self._y = y

    def update(self):
        self.updated = True


class ControlLayoutUtilsTests(unittest.TestCase):
    def test_short_content_is_top_aligned_by_default(self):
        scroll = _GeometryStub(500, 700)
        scroll.widget_scroll_animation = _Animation()
        container = _GeometryStub(360, 400, y=120)

        align_scroll_content(scroll, container)

        self.assertEqual(container.position, (70, 0))
        self.assertEqual(scroll.widget_scroll_animation.current, [70, 0])
        self.assertEqual(scroll.widget_scroll_animation.target, [70, 0])
        self.assertTrue(scroll.updated)

    def test_vertical_centering_remains_available_when_requested(self):
        scroll = _GeometryStub(500, 700)
        scroll.widget_scroll_animation = _Animation()
        container = _GeometryStub(360, 400)

        align_scroll_content(scroll, container, center_y_when_short=True)

        self.assertEqual(container.position, (70, 150))

    def test_high_dpi_defaults_preserve_explicit_user_overrides(self):
        with patch.dict(os.environ, {}, clear=True):
            configure_qt_high_dpi()
            self.assertEqual(os.environ["QT_ENABLE_HIGHDPI_SCALING"], "1")
            self.assertEqual(os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"], "PassThrough")

        with patch.dict(
            os.environ,
            {
                "QT_ENABLE_HIGHDPI_SCALING": "0",
                "QT_SCALE_FACTOR_ROUNDING_POLICY": "Round",
            },
            clear=True,
        ):
            configure_qt_high_dpi()
            self.assertEqual(os.environ["QT_ENABLE_HIGHDPI_SCALING"], "0")
            self.assertEqual(os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"], "Round")


if __name__ == "__main__":
    unittest.main()
