import unittest

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.core.display_style import new_display_style
from bandscope.rendering.refresh_pipeline import RefreshCause, RenderQuality


class ColormapSelectionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from tests.support.view_recipe import control_window

        self.window = control_window()
        self.addCleanup(self.window.close)
        self.page = self.window.page_render
        self.requests = []
        self.window.request_refresh = self._record_refresh

    def _record_refresh(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def _select_from_menu(self, name):
        selector = self.page.colormap_selector
        menu = selector.open_menu()
        row = menu.row_for(name)
        self.assertIsNotNone(row, f"菜单缺少色带：{name}")
        QTest.mouseClick(row, Qt.LeftButton)
        QApplication.processEvents()
        return menu

    def test_selecting_colormap_keeps_range_and_requests_one_exact_refresh(self):
        page = self.page
        page.set_display_style(new_display_style((0, 50, 100), "线性", (0.15, 0.8)))
        self.assertEqual(page.get_display_style()["colormap_range"], (0.15, 0.8))
        self.requests.clear()

        menu = self._select_from_menu("coolwarm")

        self.assertFalse(menu.isVisible())
        self.assertEqual(page.get_selected_cmap(), "coolwarm")
        self.assertEqual(page.export_state()["combo_cmap"], "coolwarm")
        self.assertEqual(page.colormap_selector.name_label.text(), "coolwarm")
        self.assertEqual(page.colormap_range_editor._cmap, "coolwarm")
        # 换色带保留输出区间：两个手柄仍停在 15% 和 80%。
        self.assertEqual((page.s_cmap_low.value(), page.s_cmap_high.value()), (150, 800))
        self.assertEqual(page.get_display_style()["colormap_range"], (0.15, 0.8))
        # 精确刷新只请求一次，且由传输函数变化触发。
        self.assertEqual(len(self.requests), 1)
        args, kwargs = self.requests[0]
        self.assertEqual(args, (RefreshCause.TRANSFER_FUNCTION, RenderQuality.EXACT))
        self.assertTrue(kwargs.get("immediate"))

    def test_reselecting_the_current_colormap_and_cancelling_do_not_refresh(self):
        page = self.page
        self.requests.clear()

        self._select_from_menu("magma")
        self.assertEqual(self.requests, [])
        self.assertEqual(page.get_selected_cmap(), "magma")

        menu = page.colormap_selector.open_menu()
        menu.close()
        self.assertEqual(self.requests, [])
        self.assertEqual(page.get_selected_cmap(), "magma")

        menu = page.colormap_selector.open_menu()
        QTest.keyClick(menu, Qt.Key_Escape)
        self.assertFalse(menu.isVisible())
        self.assertEqual(self.requests, [])
        self.assertEqual(page.get_selected_cmap(), "magma")

    def test_restoring_a_saved_recipe_syncs_name_and_gradient_silently(self):
        page = self.page
        page.set_display_style(new_display_style((0, 50, 100), "线性", (0.25, 0.75)))
        state = page.export_state()
        state["combo_cmap"] = "coolwarm"
        page.colormap_selector.set_colormap("magma")
        page.colormap_range_editor.set_colormap("magma")
        self.requests.clear()

        page.restore_state(state)

        self.assertEqual(page.get_selected_cmap(), "coolwarm")
        self.assertEqual(page.colormap_selector.name_label.text(), "coolwarm")
        self.assertEqual(page.colormap_range_editor._cmap, "coolwarm")
        self.assertEqual(page.get_display_style()["colormap_range"], (0.25, 0.75))
        self.assertFalse(page.colormap_selector.menu_visible())
        self.assertEqual(self.requests, [])

    def test_legacy_state_without_a_colormap_falls_back_to_magma(self):
        page = self.page
        page.colormap_selector.set_colormap("coolwarm")
        page.colormap_range_editor.set_colormap("coolwarm")
        self.requests.clear()

        page.restore_state({
            "s_low": {"value": 7}, "s_gamma": {"value": 61}, "s_up": {"value": 88},
        })

        self.assertEqual(page.get_selected_cmap(), "magma")
        self.assertEqual(page.colormap_selector.name_label.text(), "magma")
        self.assertEqual(page.colormap_range_editor._cmap, "magma")
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
