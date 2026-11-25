import unittest

from PyQt5.QtCore import QPoint, QRect, QSize, Qt
from PyQt5.QtGui import QIcon, QImage, QPainter
from PyQt5.QtWidgets import QApplication, QStyleOption
from siui.core import SiGlobal

import bandscope.ui.theme as theme
from bandscope.ui.fluent_icon import FluentIconEngine, FluentIconLabel


def _image_bytes(image):
    converted = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    return converted.bits().asstring(converted.sizeInBytes())


def _pixmap_bytes(pixmap):
    return _image_bytes(pixmap.toImage())


def _target_size(logical_size, scale):
    return int(logical_size * scale + 0.5)


class FluentIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        theme.set_theme("dark")
        self.addCleanup(theme.set_theme, "dark")

    def assertPixmapEqual(self, actual, expected):
        self.assertEqual(actual.size(), expected.size())
        self.assertEqual(_pixmap_bytes(actual), _pixmap_bytes(expected))

    def test_all_logical_sizes_and_icon_states_match_legacy_pixmaps(self):
        for name in ("ic_fluent_weather_sunny_regular", "ic_fluent_document_data_filled"):
            for size in (14, 15, 16, 18, 20):
                for color in (theme.TEXT_2, theme.ACCENT_ON):
                    svg = SiGlobal.siui.iconpack.get(name, color)
                    engine = FluentIconEngine(svg)
                    normal = SiGlobal.siui.iconpack.toPixmap(name, QSize(size, size), color)
                    for mode in (QIcon.Normal, QIcon.Disabled, QIcon.Active, QIcon.Selected):
                        expected = normal if mode == QIcon.Normal else self.app.style().generatedIconPixmap(
                            mode, normal, QStyleOption()
                        )
                        for state in (QIcon.Off, QIcon.On):
                            with self.subTest(name=name, size=size, color=color, mode=mode, state=state):
                                self.assertPixmapEqual(
                                    engine.pixmap(QSize(size, size), mode, state), expected
                                )

    def test_fractional_scale_targets_match_direct_svg_rendering(self):
        name = "ic_fluent_weather_sunny_regular"
        color = theme.TEXT_2
        svg = SiGlobal.siui.iconpack.get(name, color)
        engine = FluentIconEngine(svg)
        for logical_size in (14, 15, 16, 18, 20):
            for scale in (1.0, 1.25, 1.5, 1.5625, 1.75, 2.0):
                target = _target_size(logical_size, scale)
                with self.subTest(logical_size=logical_size, scale=scale, target=target):
                    actual = engine.pixmap(QSize(target, target), QIcon.Normal, QIcon.Off)
                    expected = SiGlobal.siui.iconpack.toPixmap(
                        name, QSize(target, target), color
                    )
                    self.assertPixmapEqual(actual, expected)
                    self.assertEqual(
                        engine.actualSize(QSize(target, target), QIcon.Normal, QIcon.Off),
                        QSize(target, target),
                    )

    def test_direct_paint_uses_the_painters_fractional_device_transform(self):
        name = "ic_fluent_database_regular"
        color = theme.TEXT_2
        scale = 1.5625
        logical_size = 16
        target = _target_size(logical_size, scale)
        svg = SiGlobal.siui.iconpack.get(name, color)
        engine = FluentIconEngine(svg)
        image = QImage(target, target, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.scale(scale, scale)
        engine.paint(painter, QRect(0, 0, logical_size, logical_size), QIcon.Normal, QIcon.Off)
        painter.end()

        expected = SiGlobal.siui.iconpack.toPixmap(name, QSize(target, target), color)
        self.assertEqual(_image_bytes(image), _pixmap_bytes(expected))

    def test_direct_paint_preserves_generated_disabled_mode(self):
        name = "ic_fluent_arrow_export_regular"
        color = theme.ACCENT_ON
        svg = SiGlobal.siui.iconpack.get(name, color)
        engine = FluentIconEngine(svg)
        for scale in (1.0, 1.5625):
            logical_size = 16
            target = _target_size(logical_size, scale)
            image = QImage(target, target, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            painter.scale(scale, scale)
            engine.paint(
                painter,
                QRect(0, 0, logical_size, logical_size),
                QIcon.Disabled,
                QIcon.Off,
            )
            painter.end()
            normal = SiGlobal.siui.iconpack.toPixmap(name, QSize(target, target), color)
            expected = self.app.style().generatedIconPixmap(
                QIcon.Disabled, normal, QStyleOption()
            )
            with self.subTest(scale=scale):
                self.assertEqual(_image_bytes(image), _pixmap_bytes(expected))

    def test_engine_clone_preserves_svg_and_requested_size(self):
        svg = SiGlobal.siui.iconpack.get("ic_fluent_cube_regular", theme.TEXT_2)
        engine = FluentIconEngine(svg)
        clone = engine.clone()
        self.assertIsInstance(clone, FluentIconEngine)
        self.assertIsNot(clone, engine)
        self.assertEqual(clone.actualSize(QSize(27, 31), QIcon.Normal, QIcon.Off), QSize(27, 31))
        self.assertPixmapEqual(
            clone.pixmap(QSize(27, 31), QIcon.Normal, QIcon.Off),
            SiGlobal.siui.iconpack.toPixmap("ic_fluent_cube_regular", QSize(27, 31), theme.TEXT_2),
        )

    def test_factory_reuses_icons_by_name_and_actual_color(self):
        first = theme.fluent_icon("ic_fluent_settings_regular", 14, theme.TEXT_2)
        second = theme.fluent_icon("ic_fluent_settings_regular", 20, theme.TEXT_2)
        other_color = theme.fluent_icon("ic_fluent_settings_regular", 14, theme.TEXT_1)
        other_name = theme.fluent_icon("ic_fluent_cube_regular", 14, theme.TEXT_2)
        self.assertIs(first, second)
        self.assertIsNot(first, other_color)
        self.assertIsNot(first, other_name)
        self.assertTrue(theme.fluent_icon("missing-fluent-icon").isNull())

    def test_relief_factory_adds_layers_and_preserves_size_and_disabled_mode(self):
        name = "ic_fluent_settings_regular"
        size = QSize(40, 40)
        flat = theme.fluent_icon(name, 20, theme.TEXT_2)
        relief = theme.fluent_icon(name, 20, theme.TEXT_2, relief=True)

        self.assertIsNot(flat, relief)
        normal = relief.pixmap(size, QIcon.Normal, QIcon.Off)
        self.assertEqual(relief.actualSize(size), size)
        self.assertNotEqual(
            _pixmap_bytes(normal),
            _pixmap_bytes(flat.pixmap(size, QIcon.Normal, QIcon.Off)),
        )
        expected_disabled = self.app.style().generatedIconPixmap(
            QIcon.Disabled, normal, QStyleOption(),
        )
        self.assertPixmapEqual(
            relief.pixmap(size, QIcon.Disabled, QIcon.Off), expected_disabled,
        )

    def test_label_repaints_icon_theme_changes_and_clear(self):
        label = FluentIconLabel(name="ic_fluent_database_regular", size=16)
        label.setFixedSize(30, 30)
        self.addCleanup(label.deleteLater)

        def render():
            image = QImage(30, 30, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            label.render(painter, QPoint())
            painter.end()
            return image

        expected = SiGlobal.siui.iconpack.toPixmap(
            "ic_fluent_database_regular", QSize(16, 16), theme.TEXT_2
        )
        self.assertEqual(_image_bytes(render().copy(7, 7, 16, 16)), _pixmap_bytes(expected))

        theme.set_theme("light")
        label.apply_theme()
        expected = SiGlobal.siui.iconpack.toPixmap(
            "ic_fluent_database_regular", QSize(16, 16), theme.TEXT_2
        )
        self.assertEqual(_image_bytes(render().copy(7, 7, 16, 16)), _pixmap_bytes(expected))

        label.set_fluent_icon("ic_fluent_settings_regular", 18)
        expected = SiGlobal.siui.iconpack.toPixmap(
            "ic_fluent_settings_regular", QSize(18, 18), theme.TEXT_2
        )
        self.assertEqual(_image_bytes(render().copy(6, 6, 18, 18)), _pixmap_bytes(expected))

        label.clear()
        self.assertTrue(label.icon().isNull())
        cleared = render()
        self.assertEqual(
            _image_bytes(cleared), b"\x00" * cleared.sizeInBytes()
        )


if __name__ == "__main__":
    unittest.main()
