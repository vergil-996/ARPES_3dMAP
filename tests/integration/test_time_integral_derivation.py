"""当前视图的时间区间求和：不创建独立时间积分页面。"""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.axis_interval import AxisInterval
from bandscope.ui.result_workspace import AnalysisPageSpec
from tests.support.view_recipe import control_window, synthetic_npz


class CurrentViewTimeIntervalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "source.npz"
        self.data = synthetic_npz(self.path, seed=19)
        self.window = control_window()
        self.addCleanup(self.window.close)
        self.assertTrue(self.window.load_data(str(self.path)))
        identity = self.window.core.source_identity
        if identity is not None and identity.future is not None:
            identity.future.result(timeout=30)

    def _set_range(self, spec, low, up):
        coords = self.window.core.coords["delay"]
        spec.params["time_interval"] = AxisInterval.from_indices(
            coords, low, up, locked=low == up, axis_key="delay"
        ).as_dict()
        self.window._bind_time_interval(spec, animate=False)

    def test_home_sums_only_the_current_view_and_collapses_back_to_a_frame(self):
        spec = self.window.left_workspace.current_spec()
        original = (spec.page_id, spec.page_kind, len(self.window.left_workspace.page_specs))
        self._set_range(spec, 0, 1)
        context = self.window._compute_render_context(spec)
        np.testing.assert_allclose(
            context["data"], np.sum(self.data[..., 0:2], axis=3), rtol=2e-6, atol=2e-6
        )
        self.assertEqual(
            (spec.page_id, spec.page_kind, len(self.window.left_workspace.page_specs)), original
        )

        self._set_range(spec, 2, 2)
        context = self.window._compute_render_context(spec)
        np.testing.assert_allclose(context["data"], self.data[..., 2], rtol=2e-6, atol=2e-6)
        self.assertEqual(
            (spec.page_id, spec.page_kind, len(self.window.left_workspace.page_specs)), original
        )

    def test_spatial_and_time_intervals_compose_on_the_same_2d_page(self):
        source = self.window.left_workspace.current_spec()
        spec = self.window._build_canvas_axis_slice_spec(source, 2)
        self.window.left_workspace.add_page(spec)
        original = (spec.page_id, spec.page_kind, len(self.window.left_workspace.page_specs))
        self._set_range(spec, 0, 1)

        spatial = AxisInterval.from_dict(spec.params["axis_interval"])
        low, up = spatial.to_indices(self.window.core.coords["E"])
        expected = np.sum(np.sum(self.data[..., 0:2], axis=3)[:, :, low:up + 1], axis=2)
        context = self.window._get_axis_integral_context(
            spec, self.window.core.raw_data, self.window.core.coords
        )
        np.testing.assert_allclose(context["data"], expected, rtol=2e-6, atol=2e-6)
        self.assertEqual(
            (spec.page_id, spec.page_kind, len(self.window.left_workspace.page_specs)), original
        )

    def test_energy_dos_reads_its_own_time_interval(self):
        home = self.window.left_workspace.current_spec()
        self._set_range(home, 0, 1)
        spec = self.window._build_energy_dos_spec()
        self.window.left_workspace.add_page(spec)
        original = (spec.page_id, spec.page_kind, len(self.window.left_workspace.page_specs))
        raw, coords = self.window._get_display_state_for_spec(spec)

        context = self.window._get_energy_dos_context(spec, raw, coords)
        np.testing.assert_allclose(
            context["y_data"], np.sum(self.data[..., 0:2], axis=(0, 1, 3)), rtol=2e-6, atol=2e-6
        )

        self._set_range(spec, 1, 2)
        context = self.window._get_energy_dos_context(spec, raw, coords)
        np.testing.assert_allclose(
            context["y_data"], np.sum(self.data[..., 1:3], axis=(0, 1, 3)), rtol=2e-6, atol=2e-6
        )
        self.assertEqual(
            (spec.page_id, spec.page_kind, len(self.window.left_workspace.page_specs)), original
        )


class LegacyTimePageMigrationTests(unittest.TestCase):
    def test_legacy_page_kind_becomes_an_ordinary_home_page(self):
        spec = AnalysisPageSpec(
            "legacy", "旧时间积分", "time_integral", "test",
            params={"t_low": 0, "t_up": 1},
        )
        self.assertEqual(spec.page_kind, "home")

    def test_static_volume_is_not_sliced(self):
        raw = np.ones((2, 3, 4), dtype=np.float32)
        self.assertIs(My3DAnalyzer._time_range_data(raw, 0, 0), raw)


if __name__ == "__main__":
    unittest.main()
