import unittest

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QScrollArea

from bandscope.ui.colormap_selector import (
    COLORMAP_OPTIONS,
    DEFAULT_COLORMAP,
    ColormapMenu,
    ColormapMenuRow,
    OutputColormapSelector,
)

#: 改造前的顺序，用来核对除置顶两项外的相对顺序未变。
LEGACY_ORDER = [
    "magma", "inferno", "plasma", "viridis", "cividis", "turbo",
    "afmhot", "hot", "gist_heat", "coolwarm", "RdBu_r", "seismic",
    "Spectral", "jet", "rainbow", "nipy_spectral", "cubehelix",
    "twilight", "twilight_shifted", "Greys", "gray", "bone", "pink",
    "spring", "summer", "autumn", "winter", "cool", "hsv", "terrain",
    "ocean", "gnuplot", "gnuplot2",
]


def grab_pixel(widget, x, y):
    image = widget.grab().toImage()
    scale_x = image.width() / max(widget.width(), 1)
    scale_y = image.height() / max(widget.height(), 1)
    return image.pixelColor(int(round(x * scale_x)), int(round(y * scale_y)))


class ColormapOptionsTests(unittest.TestCase):
    def test_shared_order_starts_with_magma_and_coolwarm(self):
        self.assertEqual(DEFAULT_COLORMAP, "magma")
        self.assertEqual(COLORMAP_OPTIONS[:2], ["magma", "coolwarm"])
        self.assertEqual(COLORMAP_OPTIONS.count("coolwarm"), 1)
        self.assertEqual(len(COLORMAP_OPTIONS), len(set(COLORMAP_OPTIONS)))
        # 其余色带保持原有相对顺序，coolwarm 只从原位置移动到第二位。
        self.assertEqual(
            COLORMAP_OPTIONS[1:],
            ["coolwarm"] + [name for name in LEGACY_ORDER
                            if name not in {"magma", "coolwarm"}],
        )

    def test_main_page_and_blender_dialog_reuse_the_shared_list(self):
        from bandscope.exporting import blender_project_dialog
        from bandscope.ui.page_render_control import RenderControlPage

        self.assertIs(RenderControlPage.CMAP_OPTIONS, COLORMAP_OPTIONS)
        self.assertIs(blender_project_dialog.COLORMAP_OPTIONS, COLORMAP_OPTIONS)


class ColormapSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_selector(self):
        selector = OutputColormapSelector()
        selector.resize(260, selector.HEIGHT)
        selector.show()
        self.addCleanup(selector.deleteLater)
        self.addCleanup(selector.close_menu)
        self.addCleanup(selector.close)
        QApplication.processEvents()
        return selector

    def test_defaults_to_magma_and_menu_lists_every_option_once(self):
        selector = self.make_selector()
        self.assertEqual(selector.current_colormap(), "magma")
        self.assertEqual(selector.name_label.text(), "magma")
        self.assertEqual(selector.title_label.text(), "输出色带")
        self.assertEqual(selector.arrow_label.text(), "▾")

        menu = selector.open_menu()
        self.assertIsInstance(menu, ColormapMenu)
        self.assertIsInstance(menu.scroll, QScrollArea)
        self.assertEqual([row.name for row in menu.rows], list(COLORMAP_OPTIONS))
        self.assertEqual(len({row.name for row in menu.rows}), len(menu.rows))
        current = [row.name for row in menu.rows if row.is_current()]
        self.assertEqual(current, ["magma"])

    def test_each_row_paints_a_full_gradient_swatch(self):
        selector = self.make_selector()
        menu = selector.open_menu()
        QApplication.processEvents()
        row = menu.row_for("magma")
        self.assertIsNotNone(row)
        y = row.height() // 2
        left = grab_pixel(row, ColormapMenuRow.PADDING + 3, y)
        right = grab_pixel(
            row, ColormapMenuRow.PADDING + ColormapMenuRow.SWATCH_WIDTH - 3, y,
        )
        self.assertEqual(left.alpha(), 255)
        self.assertEqual(right.alpha(), 255)
        self.assertNotEqual(left.name(), right.name())

    def test_long_menu_scrolls_and_reveals_the_current_row(self):
        selector = self.make_selector()
        selector.set_colormap("gnuplot2")
        menu = selector.open_menu()
        QApplication.processEvents()
        self.assertLess(menu.height(), menu.content_height())
        self.assertLessEqual(menu.height(), ColormapMenu.MAX_HEIGHT)
        bar = menu.scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.assertGreater(bar.value(), 0)
        self.assertTrue(menu.row_for("gnuplot2").is_current())

    def test_selecting_another_colormap_emits_once_and_updates_the_name(self):
        selector = self.make_selector()
        emitted = []
        selector.colormap_changed.connect(emitted.append)
        menu = selector.open_menu()
        QTest.mouseClick(menu.row_for("coolwarm"), Qt.LeftButton)

        self.assertEqual(emitted, ["coolwarm"])
        self.assertEqual(selector.current_colormap(), "coolwarm")
        self.assertEqual(selector.name_label.text(), "coolwarm")
        self.assertFalse(menu.isVisible())
        self.assertEqual(
            [row.name for row in menu.rows if row.is_current()], ["coolwarm"],
        )

    def test_reselecting_the_current_colormap_is_a_noop(self):
        selector = self.make_selector()
        emitted = []
        selector.colormap_changed.connect(emitted.append)
        menu = selector.open_menu()
        QTest.mouseClick(menu.row_for("magma"), Qt.LeftButton)

        self.assertEqual(emitted, [])
        self.assertEqual(selector.current_colormap(), "magma")
        self.assertFalse(menu.isVisible())

    def test_cancelling_the_menu_keeps_the_current_colormap(self):
        selector = self.make_selector()
        emitted = []
        selector.colormap_changed.connect(emitted.append)

        menu = selector.open_menu()
        menu.close()
        self.assertEqual(emitted, [])
        self.assertEqual(selector.current_colormap(), "magma")

        menu = selector.open_menu()
        QTest.keyClick(menu, Qt.Key_Escape)
        self.assertFalse(menu.isVisible())
        self.assertEqual(emitted, [])
        self.assertEqual(selector.current_colormap(), "magma")

    def test_programmatic_sync_is_silent_and_unknown_names_are_ignored(self):
        selector = self.make_selector()
        emitted = []
        selector.colormap_changed.connect(emitted.append)
        self.assertTrue(selector.set_colormap("viridis"))
        self.assertEqual(emitted, [])
        self.assertEqual(selector.current_colormap(), "viridis")
        self.assertFalse(selector.set_colormap("not-a-colormap"))
        self.assertEqual(selector.current_colormap(), "viridis")
        self.assertEqual(selector.name_label.text(), "viridis")
        self.assertEqual(emitted, [])

    def test_clicking_the_header_opens_the_menu(self):
        selector = self.make_selector()
        QTest.mouseClick(
            selector, Qt.LeftButton,
            pos=QPoint(selector.width() // 2, selector.height() // 2),
        )
        menu = selector.menu()
        self.assertIsNotNone(menu)
        self.assertTrue(menu.isVisible())
        self.assertTrue(selector.menu_visible())
        selector.close_menu()
        self.assertFalse(selector.menu_visible())


if __name__ == "__main__":
    unittest.main()
