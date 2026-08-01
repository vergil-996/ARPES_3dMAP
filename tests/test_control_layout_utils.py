import os
import unittest
from unittest.mock import patch

from control_layout_utils import align_scroll_content, combo_index_for_text, sync_slider_visual
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


class _ComboStub:
    def __init__(self, items):
        self.items = list(items)

    def count(self):
        return len(self.items)

    def itemText(self, index):
        return self.items[index]


class _ProgressAnimation:
    def __init__(self):
        self.current = None
        self.end = None
        self.synced = False

    def fromProperty(self):
        self.synced = True

    def setCurrentValue(self, value):
        self.current = value

    def setEndValue(self, value):
        self.end = value


class _SliderStub:
    class Property:
        TrackProgress = "track-progress"

    def __init__(self, minimum, maximum, value):
        self._minimum = minimum
        self._maximum = maximum
        self._value = value
        self.progress_ani = _ProgressAnimation()
        self.properties = {}
        self.tooltip_flash = None
        self.updated = False

    def minimum(self):
        return self._minimum

    def maximum(self):
        return self._maximum

    def value(self):
        return self._value

    def setProperty(self, key, value):
        self.properties[key] = value

    def _updateToolTip(self, *, flash):
        self.tooltip_flash = flash

    def update(self):
        self.updated = True


class ControlLayoutUtilsTests(unittest.TestCase):
    def test_combo_lookup_supports_explicit_legacy_aliases(self):
        combo = _ComboStub(["切片内强度积分", "能级态密度"])

        index = combo_index_for_text(
            combo,
            "切片态密度",
            aliases={"切片态密度": "切片内强度积分"},
        )

        self.assertEqual(index, 0)

    def test_slider_visual_sync_updates_every_optional_surface(self):
        slider = _SliderStub(10, 30, 15)

        sync_slider_visual(slider)

        self.assertEqual(slider.properties[slider.Property.TrackProgress], 0.25)
        self.assertTrue(slider.progress_ani.synced)
        self.assertEqual(slider.progress_ani.current, 0.25)
        self.assertEqual(slider.progress_ani.end, 0.25)
        self.assertFalse(slider.tooltip_flash)
        self.assertTrue(slider.updated)

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
