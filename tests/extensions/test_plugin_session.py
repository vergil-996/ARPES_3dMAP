"""宿主适配层：上下文、倍率对齐、每页状态（计划 §4、§5）。

用桩窗口 + 真实安装的扩展包，覆盖不需要 VTK 渲染的那部分集成逻辑。
"""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

# 挂载测试会真正创建插件面板；离屏平台必须在导入 Qt 之前定好。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from bandscope.app_metadata import APP_VERSION
from bandscope.extensions.api import (
    SOURCE_FILE,
    SOURCE_INDEX,
    EnergyAxisSpec,
    align_multiplier_to_voxels,
)
from bandscope.extensions.plugin_host import PluginSession
from bandscope.extensions.plugin_manager import PluginManager
from tests.support.plugins import install_synthetic
from scripts.release.build_plugin import build as build_package

PLUGIN_ID = "flat_band_opacity"


class AlignTests(unittest.TestCase):
    """倍率 → 体素索引：显示翻转、紧凑 ROI、预览降采样。"""

    def test_passthrough_without_flip_or_roi(self):
        values = np.arange(5.0)
        np.testing.assert_array_equal(
            align_multiplier_to_voxels(values, flipped=False, e_bounds=None), values
        )

    def test_flip_reverses_the_display_order(self):
        values = np.arange(5.0)
        np.testing.assert_array_equal(
            align_multiplier_to_voxels(values, flipped=True, e_bounds=None),
            values[::-1],
        )

    def test_compact_roi_slices_absolute_indices(self):
        values = np.arange(10.0)
        np.testing.assert_array_equal(
            align_multiplier_to_voxels(values, flipped=False, e_bounds=(2, 5)),
            [2.0, 3.0, 4.0, 5.0],
        )

    def test_flip_then_slice_matches_the_voxel_axis(self):
        # 显示序是真实序的翻转；体素索引仍在真实序上。
        true_axis = np.arange(10.0)
        displayed = true_axis[::-1]
        aligned = align_multiplier_to_voxels(displayed, flipped=True, e_bounds=(2, 5))
        np.testing.assert_array_equal(aligned, true_axis[2:6])

    def test_preview_stride_uses_the_same_indices_as_the_data(self):
        values = np.arange(10.0)
        aligned = align_multiplier_to_voxels(
            values, flipped=False, e_bounds=None, stride=3
        )
        np.testing.assert_array_equal(aligned, values[::3])
        # 与数据用同一组索引：data[::3] 的第 j 个能量处拿到的是同一条倍率。
        data = np.arange(10.0)
        np.testing.assert_array_equal(aligned, data[::3])

    def test_out_of_range_roi_is_an_error_not_a_silent_truncation(self):
        with self.assertRaises(ValueError):
            align_multiplier_to_voxels(np.arange(4.0), flipped=False, e_bounds=(1, 9))

    def test_none_stays_none(self):
        self.assertIsNone(
            align_multiplier_to_voxels(None, flipped=False, e_bounds=None)
        )


class SpecTests(unittest.TestCase):
    def test_units_are_reported_honestly(self):
        values = np.linspace(-1.0, 1.0, 5)
        with_unit = EnergyAxisSpec(values, "eV", SOURCE_FILE, (-1.0, 1.0), (-1.0, 1.0))
        self.assertEqual(with_unit.display_unit, "eV")
        self.assertEqual(with_unit.label, "E (eV)")

        without_unit = EnergyAxisSpec(values, None, SOURCE_FILE, (-1.0, 1.0), (-1.0, 1.0))
        self.assertEqual(without_unit.display_unit, "坐标值")

        indexed = EnergyAxisSpec(values, None, SOURCE_INDEX, (-1.0, 1.0), (-1.0, 1.0))
        self.assertEqual(indexed.display_unit, "index")

    def test_sample_spacing_uses_the_median_difference(self):
        coarse = EnergyAxisSpec(np.linspace(-1.0, 1.0, 5), "eV", SOURCE_FILE)
        self.assertAlmostEqual(coarse.sample_spacing(), 0.5, places=9)
        self.assertEqual(EnergyAxisSpec(np.array([1.0]), "eV", SOURCE_FILE).sample_spacing(), 0.0)


def make_window(*, energy, unit="eV", source=SOURCE_FILE, flip=False, strides=()):
    """桩窗口：只提供 PluginSession 真正读取的属性。"""
    coords = {"E": np.asarray(energy, dtype=np.float64)}
    core = SimpleNamespace(
        coords=coords,
        coord_sources={"E": source},
        coord_units={"E": unit},
        raw_data=np.zeros((2, 2, len(energy))),
    )

    class _Spec:
        page_id = "home"
        title = "首页"
        page_kind = "home"
        params = {}

        def __init__(self):
            self.params = {}

    spec = _Spec()
    workspace = SimpleNamespace(
        current_spec=lambda: spec,
        home_spec=lambda: spec,
        page_by_id=lambda _pid: spec,
    )
    window = SimpleNamespace(
        core=core,
        left_workspace=workspace,
        timeline_bar=SimpleNamespace(
            slider_time=SimpleNamespace(value=lambda: 0),
            switch_flip=SimpleNamespace(isChecked=lambda: flip),
        ),
        toast_manager=None,
    )
    window._current_delay_text = lambda index: str(index)
    window.request_refresh = lambda *args, **kwargs: None
    return window, spec


class SessionTestCase(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.root = Path(self._root.name)
        self._previous = os.environ.get("BANDSCOPE_EXTENSION_ROOT")
        os.environ["BANDSCOPE_EXTENSION_ROOT"] = str(self.root)
        self.addCleanup(self._restore)
        archive = build_package(PLUGIN_ID, self.root / "dist", APP_VERSION)
        install_synthetic(archive, app_version=APP_VERSION)

    def _restore(self):
        if self._previous is None:
            os.environ.pop("BANDSCOPE_EXTENSION_ROOT", None)
        else:
            os.environ["BANDSCOPE_EXTENSION_ROOT"] = self._previous
        self._root.cleanup()

    def make_session(self, window):
        session = PluginSession(window, manager=PluginManager(app_version=APP_VERSION, root=self.root))
        session.startup()
        self.addCleanup(session.shutdown)
        return session


class ContextTests(SessionTestCase):
    def test_energy_axis_is_the_displayed_one(self):
        energy = np.linspace(-1.0, 1.0, 9)
        window, _ = make_window(energy=energy, flip=True)
        session = self.make_session(window)
        render_context = {
            "view": "3d",
            "coords": {"E": energy[::-1]},
            "data": np.zeros((2, 2, 9)),
        }
        context = session.build_context(window, render_context)
        self.assertEqual(context.view, "3d")
        np.testing.assert_allclose(context.energy.values, energy[::-1])
        self.assertTrue(context.display_e_flip)
        self.assertEqual(context.energy.unit, "eV")
        self.assertEqual(context.energy.display_unit, "eV")

    def test_roi_range_comes_from_the_absolute_bounds(self):
        energy = np.linspace(0.0, 8.0, 9)
        window, _ = make_window(energy=energy)
        session = self.make_session(window)
        render_context = {
            "view": "3d",
            "coords": {"E": energy},
            "data": np.zeros((2, 2, 4)),
            "data_bounds": (0, 1, 0, 1, 2, 5),
        }
        context = session.build_context(window, render_context)
        self.assertEqual(context.energy.roi_range, (2.0, 5.0))

    def test_index_axis_is_reported_as_index(self):
        energy = np.arange(9.0)
        window, _ = make_window(energy=energy, unit=None, source=SOURCE_INDEX)
        session = self.make_session(window)
        context = session.build_context(
            window, {"view": "3d", "coords": {"E": energy}, "data": np.zeros((2, 2, 9))}
        )
        self.assertEqual(context.energy.display_unit, "index")

    def test_non_3d_views_carry_no_energy_axis(self):
        energy = np.linspace(-1.0, 1.0, 9)
        window, _ = make_window(energy=energy)
        session = self.make_session(window)
        context = session.build_context(window, {"view": "2d", "data": np.zeros((2, 2))})
        self.assertIsNone(context.energy)
        self.assertFalse(context.renders_volume)


class MultiplierTests(SessionTestCase):
    def setUp(self):
        super().setUp()
        self.energy = np.linspace(-1.0, 1.0, 9)
        self.window, self.spec = make_window(energy=self.energy)
        self.session = self.make_session(self.window)
        self.record = self.session.manager.record(PLUGIN_ID)
        self.record.instance.restore_state(
            {"bands": [{"center": 0.0, "fwhm": 0.5, "gain": 1.0, "enabled": True}]}
        )

    def _context(self, *, flip=False, bounds=None):
        values = self.energy[::-1] if flip else self.energy
        render_context = {
            "view": "3d",
            "coords": {"E": values},
            "data": np.zeros((2, 2, 9)),
        }
        if bounds is not None:
            render_context["data_bounds"] = bounds
        self.window.timeline_bar.switch_flip = SimpleNamespace(isChecked=lambda: flip)
        context = self.session.build_context(self.window, render_context)
        return context, render_context

    def test_multiplier_reaches_the_voxel_axis(self):
        context, render_context = self._context()
        multiplier = self.session.voxel_multiplier(context, render_context)
        self.assertEqual(multiplier.shape, (9,))
        centre = int(np.argmin(np.abs(self.energy)))
        self.assertAlmostEqual(float(multiplier[centre]), 1.2, places=4)
        self.assertAlmostEqual(float(multiplier[0]), 0.2, places=4)

    def test_flip_keeps_the_peak_on_the_same_voxel(self):
        context, render_context = self._context(flip=True)
        multiplier = self.session.voxel_multiplier(context, render_context)
        centre = int(np.argmin(np.abs(self.energy)))
        self.assertAlmostEqual(float(multiplier[centre]), 1.2, places=4)

    def test_compact_roi_returns_only_the_roi_weights(self):
        # data_bounds 是 (x0,x1, y0,y1, e0,e1) 的闭区间绝对体素索引。
        context, render_context = self._context(bounds=(0, 1, 0, 1, 2, 5))
        render_context["data"] = np.zeros((2, 2, 4))
        multiplier = self.session.voxel_multiplier(context, render_context)
        self.assertEqual(multiplier.shape, (4,))
        full = self.session.voxel_multiplier(*self._context())
        np.testing.assert_allclose(multiplier, full[2:6])

    def test_flip_and_compact_roi_together_still_peak_on_the_same_voxel(self):
        """最容易串位的一档：显示方向翻转 + 紧凑 ROI 同时生效。"""
        bounds = (0, 1, 0, 1, 2, 5)
        context, render_context = self._context(flip=True, bounds=bounds)
        render_context["data"] = np.zeros((2, 2, 4))
        multiplier = self.session.voxel_multiplier(context, render_context)
        full = self.session.voxel_multiplier(*self._context())
        np.testing.assert_allclose(multiplier, full[2:6])
        # 体素 4（绝对索引）是能量 0 的位置，对应紧凑网格里的第 2 个能量索引。
        self.assertAlmostEqual(float(multiplier[2]), 1.2, places=4)

    def test_preview_stride_matches_the_data_indices(self):
        context, render_context = self._context()
        multiplier = self.session.voxel_multiplier(context, render_context, stride=3)
        full = self.session.voxel_multiplier(context, render_context)
        np.testing.assert_allclose(multiplier, full[::3])

    def test_no_bands_means_no_effect(self):
        self.record.instance.restore_state({"bands": []})
        context, render_context = self._context()
        self.assertIsNone(self.session.voxel_multiplier(context, render_context))

    def test_master_switch_bypasses_the_effect(self):
        self.record.instance.restore_state(
            {"enabled": False, "bands": [{"center": 0.0, "fwhm": 0.5, "gain": 1.0}]}
        )
        context, render_context = self._context()
        self.assertIsNone(self.session.voxel_multiplier(context, render_context))

    def test_wrong_length_is_rejected_without_raising(self):
        axis = self.window.core.coords["E"]
        context = SimpleNamespace(
            view="3d",
            display_e_flip=False,
            page_id="home",
            energy=EnergyAxisSpec(
                values=axis, unit="eV", source=SOURCE_FILE,
                roi_range=(-1.0, 1.0), full_range=(-1.0, 1.0),
            ),
        )
        self.record.instance.opacity_multiplier = lambda _context: np.ones(3)
        self.assertIsNone(self.session.voxel_multiplier(context, {"view": "3d"}))
        self.assertIn(PLUGIN_ID, self.session.errors())

    def test_raising_plugin_does_not_break_the_render_path(self):
        def boom(_context):
            raise RuntimeError("plugin exploded")

        self.record.instance.opacity_multiplier = boom
        context, render_context = self._context()
        self.assertIsNone(self.session.voxel_multiplier(context, render_context))
        self.assertIn("exploded", self.session.errors()[PLUGIN_ID])


class PageStateTests(SessionTestCase):
    def setUp(self):
        super().setUp()
        self.energy = np.linspace(-1.0, 1.0, 9)
        self.window, self.spec = make_window(energy=self.energy)
        self.session = self.make_session(self.window)
        self.record = self.session.manager.record(PLUGIN_ID)

    def test_state_round_trips_through_the_page(self):
        self.record.instance.restore_state(
            {
                "background": 0.4,
                "bands": [{"id": "a", "center": -0.25, "fwhm": 0.08, "gain": 2.0}],
            }
        )
        self.session.store_page_state(self.spec)
        self.assertIn(PLUGIN_ID, self.spec.params["plugins"])

        self.record.instance.reset_for_new_data()
        self.assertEqual(self.record.instance.export_state()["bands"], [])

        self.session.restore_page_state(self.spec)
        restored = self.record.instance.export_state()
        self.assertEqual(len(restored["bands"]), 1)
        self.assertAlmostEqual(restored["bands"][0]["center"], -0.25)
        self.assertAlmostEqual(restored["background"], 0.4)

    def test_pages_keep_independent_copies(self):
        import copy

        self.record.instance.restore_state(
            {"bands": [{"center": 0.1, "fwhm": 0.2, "gain": 1.0}]}
        )
        self.session.store_page_state(self.spec)
        derived_params = copy.deepcopy(self.spec.params)

        self.record.instance.restore_state(
            {"bands": [{"center": 0.9, "fwhm": 0.2, "gain": 1.0}]}
        )
        self.session.store_page_state(self.spec)

        # 派生页拿到的是一份独立副本，源页后续改动不会渗进去。
        self.assertAlmostEqual(
            derived_params["plugins"][PLUGIN_ID]["bands"][0]["center"], 0.1
        )
        self.assertAlmostEqual(
            self.spec.params["plugins"][PLUGIN_ID]["bands"][0]["center"], 0.9
        )

    def test_new_data_clears_peak_positions_but_keeps_the_switch(self):
        self.record.instance.restore_state(
            {"enabled": False, "bands": [{"center": 0.1, "fwhm": 0.2, "gain": 1.0}]}
        )
        self.session.reset_for_new_data()
        state = self.record.instance.export_state()
        self.assertEqual(state["bands"], [])
        self.assertFalse(state["enabled"])

    def test_restore_without_recorded_state_keeps_the_current_one(self):
        self.record.instance.restore_state(
            {"bands": [{"center": 0.5, "fwhm": 0.2, "gain": 1.0}]}
        )
        self.spec.params.pop("plugins", None)
        self.session.restore_page_state(self.spec)
        self.assertEqual(len(self.record.instance.export_state()["bands"]), 1)


class TransferSignatureTests(SessionTestCase):
    """传输函数指纹必须与主窗口记录的指纹同构。

    它是 EXACT 刷新时“传输函数没变、整帧跳过”的判据；两处结构不一致会让判等
    永远失败，每次刷新都变成一次完整的体渲染。
    """

    def _window_with_signature(self, plugin_state):
        from bandscope.app.refactored_app import My3DAnalyzer

        energy = np.linspace(-1.0, 1.0, 9)
        window, spec = make_window(energy=energy)
        window.page_render = SimpleNamespace(
            combo_map=SimpleNamespace(currentText=lambda: "线性"),
            get_selected_cmap=lambda: "magma",
        )
        window._get_display_levels = lambda: (0, 50, 100)
        session = self.make_session(window)
        window.plugin_session = session
        # 桩上没有真正的方法，把待测的那两个按真实 self 绑上去。
        window._plugin_session = lambda: session
        window._plugin_effect_signature = lambda: My3DAnalyzer._plugin_effect_signature(
            window
        )
        record = session.manager.record(PLUGIN_ID)
        record.instance.restore_state(plugin_state)
        return session, record.instance

    def test_signature_changes_when_a_plugin_parameter_changes(self):
        from bandscope.app.refactored_app import My3DAnalyzer

        session, instance = self._window_with_signature(
            {"bands": [{"center": 0.0, "fwhm": 0.1, "gain": 0.8}]}
        )
        window = session.window
        before = My3DAnalyzer._current_transfer_signature(window)
        self.assertEqual(len(before), 5)

        instance.restore_state(
            {"bands": [{"center": 0.0, "fwhm": 0.1, "gain": 2.0}]}
        )
        after = My3DAnalyzer._current_transfer_signature(window)
        self.assertNotEqual(before, after)
        self.assertEqual(len(before), len(after))

    def test_signature_is_stable_when_nothing_changes(self):
        from bandscope.app.refactored_app import My3DAnalyzer

        session, _instance = self._window_with_signature(
            {"bands": [{"center": 0.0, "fwhm": 0.1, "gain": 0.8}]}
        )
        window = session.window
        first = My3DAnalyzer._current_transfer_signature(window)
        second = My3DAnalyzer._current_transfer_signature(window)
        self.assertEqual(first, second)


class MountTests(SessionTestCase):
    """面板挂载会真正创建 Qt 控件，因此这里需要 QApplication。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication

        from bandscope.app.qt_bootstrap import configure_qt_plugin_path

        configure_qt_plugin_path()
        cls.app = QApplication.instance() or QApplication([])

    def test_cards_are_mounted_and_toggled_by_the_host(self):
        energy = np.linspace(-1.0, 1.0, 9)
        window, spec = make_window(energy=energy)
        mounted = {}
        visibility = {}

        class _PageStub:
            def mount_extension_card(self, plugin_id, title, panel):
                mounted[plugin_id] = (title, panel)
                return object()

            def set_extension_card_visible(self, plugin_id, visible, animate=True):
                visibility[plugin_id] = visible

        window.page_render = _PageStub()
        session = self.session = self.make_session(window)
        session.mount_cards(window.page_render)
        self.assertIn(PLUGIN_ID, mounted)
        self.assertEqual(mounted[PLUGIN_ID][0], "平带增强")

        session.set_cards_visible(False)
        self.assertFalse(visibility[PLUGIN_ID])
        session.set_cards_visible(True)
        self.assertTrue(visibility[PLUGIN_ID])

    def test_panel_failure_is_contained(self):
        energy = np.linspace(-1.0, 1.0, 9)
        window, spec = make_window(energy=energy)
        session = self.make_session(window)
        record = session.manager.record(PLUGIN_ID)
        record.instance.create_panel = lambda host: (_ for _ in ()).throw(RuntimeError("no panel"))
        window.page_render = SimpleNamespace(
            mount_extension_card=lambda *a, **k: None,
            set_extension_card_visible=lambda *a, **k: None,
        )
        session.mount_cards(window.page_render)
        self.assertEqual(session.card_ids(), [])
        self.assertIn("no panel", session.errors()[PLUGIN_ID])


if __name__ == "__main__":
    unittest.main()
