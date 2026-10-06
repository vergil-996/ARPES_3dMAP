# -*- coding: utf-8 -*-
"""阶段 F：宿主侧的分析接入（快照、提交、结果页、取消与失效）。

主窗口用替身：这里验证的是 plugin_host 的判断与归属，不启动真实渲染。
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.extensions.api import (
    AnalysisCurve1D,
    AnalysisInput2D,
    AnalysisUnavailable,
)
from bandscope.extensions.plugin_host import PluginSession
from bandscope.extensions.plugin_manager import PluginManager
from tests.support.plugins import install_fake, manifest_payload, make_plugin_archive
from tests.support.plugins import synthetic_source
from bandscope.extensions.plugin_manager import install_package


#: 合成分析插件的入口：交出一个真实控件，供挂载用例使用。
PANEL_ENTRY_SOURCE = (
    "from PyQt5.QtWidgets import QWidget\n"
    "from plugin_api import Plugin\n"
    "\n"
    "\n"
    "class Plugin(Plugin):\n"
    "    def create_panel(self, host):\n"
    "        return QWidget()\n"
)


def spec_stub(**overrides):
    payload = {
        "page_id": "page-1",
        "title": "kx-E 结果",
        "page_kind": "axis_integral",
        "data_scope_id": "full",
        "params": {"data_scope_label": "完整数据"},
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def context_stub(**overrides):
    payload = {
        "view": "2d",
        "data": np.arange(12.0).reshape(3, 4),
        "slice_info": {"axis": 0, "mode": "integral", "range": (0, 2)},
        "coords": {
            "X": np.array([0.0, 1.0, 2.0]),
            "Y": np.array([10.0, 20.0, 30.0, 40.0]),
        },
        "plot_axes": {"x_key": "X", "y_key": "Y", "x_label": "kx", "y_label": "ky"},
        "plot_logical_bounds": {"x_low": 0, "x_up": 2, "y_low": 0, "y_up": 3},
    }
    payload.update(overrides)
    return payload


class _WorkspaceStub:
    def __init__(self, spec, *, pages=None):
        self.spec = spec
        self.pages = dict(pages or {})
        if spec is not None:
            self.pages[spec.page_id] = spec

    def current_spec(self):
        return self.spec

    def home_spec(self):
        return self.spec

    def page_by_id(self, page_id):
        return self.pages.get(page_id)


_UNSET = object()


def make_window(*, spec=_UNSET, context=_UNSET, pages=None, exact=True, generation=7, **extra):
    spec = spec_stub() if spec is _UNSET else spec
    context = context_stub() if context is _UNSET else context
    created = []

    def add_plugin_result_page(**kwargs):
        created.append(kwargs)
        return SimpleNamespace(page_id=f"plugin-{len(created)}", **kwargs)

    window = SimpleNamespace(
        left_workspace=_WorkspaceStub(spec, pages=pages),
        current_render_context=context,
        _render_exact_ready=exact,
        shared_denoise_version=generation,
        core=SimpleNamespace(
            coord_sources={"X": "file", "Y": "file"},
            coord_units={"X": "1/Å", "Y": "eV"},
        ),
        timeline_bar=SimpleNamespace(slider_time=SimpleNamespace(value=lambda: 3)),
        _current_delay_text=lambda index: f"{index} ps",
        add_plugin_result_page=add_plugin_result_page,
        page_data=None,
    )
    window.created_pages = created
    for key, value in extra.items():
        setattr(window, key, value)
    return window


class AnalysisSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.root = Path(self._root.name) / "extensions"
        self.manager = PluginManager(root=self.root)
        self.addCleanup(self.manager.shutdown)
        self.manager.startup()

    def make_session(self, window):
        session = PluginSession(window, manager=self.manager)
        self.addCleanup(session.shutdown)
        return session

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        QApplication.processEvents()
        return predicate()

    # -- 快照 ----------------------------------------------------------
    def test_snapshot_has_expected_arrays_and_units(self):
        session = self.make_session(make_window())
        snapshot = session.capture_analysis_input("demo")
        self.assertEqual(snapshot.page_id, "page-1")
        self.assertEqual(snapshot.shape, (3, 4))
        np.testing.assert_array_equal(snapshot.data, np.arange(12.0).reshape(3, 4))
        np.testing.assert_array_equal(snapshot.x, [0.0, 1.0, 2.0])
        np.testing.assert_array_equal(snapshot.y, [10.0, 20.0, 30.0, 40.0])
        self.assertEqual(snapshot.x_label, "kx")
        self.assertEqual(snapshot.x_unit, "1/Å")
        self.assertEqual(snapshot.y_unit, "eV")
        self.assertEqual(snapshot.data_generation, 7)
        self.assertEqual(snapshot.frame_label, "3 ps")
        self.assertEqual(snapshot.scope_id, "full")

    def test_snapshot_arrays_are_read_only_copies(self):
        window = make_window()
        session = self.make_session(window)
        before = np.asarray(window.current_render_context["data"]).copy()
        snapshot = session.capture_analysis_input("demo")
        self.assertFalse(snapshot.data.flags.writeable)
        with self.assertRaises(ValueError):
            snapshot.data[0, 0] = 99.0
        np.testing.assert_array_equal(window.current_render_context["data"], before)

    def test_index_axis_keeps_unknown_units(self):
        window = make_window()
        window.core.coord_sources = {"X": "index", "Y": "file"}
        session = self.make_session(window)
        snapshot = session.capture_analysis_input("demo")
        self.assertEqual(snapshot.x_unit, "")

    def test_unavailable_reasons(self):
        cases = [
            ({"context": None}, "还没有完成的计算结果"),
            ({"context": context_stub(view="3d")}, "只支持二维"),
            ({"exact": False}, "正在计算中"),
            ({"context": context_stub(crop_empty=True)}, "没有有效数据"),
        ]
        for overrides, expected in cases:
            with self.subTest(expected=expected):
                session = self.make_session(make_window(**overrides))
                with self.assertRaises(AnalysisUnavailable) as ctx:
                    session.capture_analysis_input("demo")
                self.assertIn(expected, str(ctx.exception))

    def test_erase_regions_are_unavailable(self):
        spec = spec_stub(params={
            "data_scope_label": "完整数据",
            "crop_regions": [{"operation": "erase", "x_low": 0, "x_up": 1}],
        })
        session = self.make_session(make_window(spec=spec))
        with self.assertRaises(AnalysisUnavailable) as ctx:
            session.capture_analysis_input("demo")
        self.assertIn("擦除", str(ctx.exception))

    def test_coordinates_must_match_the_result_shape(self):
        window = make_window(context=context_stub(coords={"X": np.array([0.0, 1.0]), "Y": np.array([0.0])}))
        session = self.make_session(window)
        with self.assertRaises(AnalysisUnavailable) as ctx:
            session.capture_analysis_input("demo")
        self.assertIn("坐标与结果维度不一致", str(ctx.exception))

    # -- 提交与结果 ----------------------------------------------------
    def test_result_page_is_created_from_the_snapshot(self):
        window = make_window()
        session = self.make_session(window)
        snapshot = session.capture_analysis_input("demo")

        def work(snap, params, cancel):
            return AnalysisCurve1D(
                x=snap.y, y=snap.data.sum(axis=0),
                x_label="E", x_unit="eV", title="积分曲线",
                params={"axis": "x"},
            )

        handle = session.submit_analysis("demo", snapshot, work, title="积分", params={"axis": "x"})
        self.assertIsNotNone(handle)
        self.assertTrue(self.wait_for(lambda: window.created_pages))

        created = window.created_pages[0]
        self.assertEqual(created["source_page_id"], "page-1")
        self.assertEqual(created["source_title"], "kx-E 结果")
        analysis = created["params"]["plugin_analysis"]
        self.assertEqual(analysis["snapshot_id"], snapshot.snapshot_id)
        self.assertEqual(analysis["data_generation"], 7)
        self.assertEqual(analysis["params"], {"axis": "x"})
        curve = created["params"]["base_curve"]
        self.assertEqual(curve["curve_kind"], "plugin_curve")
        np.testing.assert_array_equal(curve["y_data"], [12.0, 15.0, 18.0, 21.0])
        self.assertEqual(curve["xlabel"], "E (eV)")

    def test_invalid_result_is_rejected(self):
        window = make_window()
        session = self.make_session(window)
        snapshot = session.capture_analysis_input("demo")

        def work(snap, params, cancel):
            return AnalysisCurve1D(x=[0.0, 1.0], y=[1.0, 2.0, 3.0])

        session.submit_analysis("demo", snapshot, work)
        self.assertTrue(self.wait_for(lambda: session.errors()))
        self.assertEqual(window.created_pages, [])

    def test_stale_result_is_dropped(self):
        window = make_window()
        session = self.make_session(window)
        snapshot = session.capture_analysis_input("demo")
        window.shared_denoise_version = 8  # 期间加载了新数据

        session.submit_analysis("demo", snapshot, lambda s, p, c: AnalysisCurve1D(x=s.x, y=s.x))
        self.assertTrue(self.wait_for(lambda: session._snapshots == {}))
        self.assertEqual(window.created_pages, [])

    def test_result_for_a_closed_source_page_is_dropped(self):
        window = make_window()
        session = self.make_session(window)
        snapshot = session.capture_analysis_input("demo")
        window.left_workspace.pages.pop("page-1")

        session.submit_analysis("demo", snapshot, lambda s, p, c: AnalysisCurve1D(x=s.x, y=s.x))
        self.assertTrue(self.wait_for(lambda: session._snapshots == {}))
        self.assertEqual(window.created_pages, [])

    def test_cancel_all_is_called_on_new_data(self):
        entered = []

        def slow(snap, params, cancel):
            entered.append(True)
            for _ in range(400):
                time.sleep(0.005)
                cancel.raise_if_cancelled()
            return AnalysisCurve1D(x=snap.x, y=snap.x)

        window = make_window()
        session = self.make_session(window)
        snapshot = session.capture_analysis_input("demo")
        session.submit_analysis("demo", snapshot, slow)
        self.assertTrue(self.wait_for(lambda: entered))

        session.reset_for_new_data()
        self.assertTrue(self.wait_for(lambda: session._snapshots == {}, timeout=5))
        self.assertEqual(window.created_pages, [])

    def test_cancel_page_analysis_targets_only_that_page(self):
        window = make_window()
        session = self.make_session(window)
        snapshot = session.capture_analysis_input("demo")
        session.submit_analysis("demo", snapshot, lambda s, p, c: AnalysisCurve1D(x=s.x, y=s.x))
        self.assertEqual(session.cancel_page_analysis("other-page"), 0)

    # -- 面板 ----------------------------------------------------------
    def test_analysis_plugin_mounts_on_the_analysis_slot_only(self):
        archive = make_plugin_archive(
            self.root / "staging" / "analysis.bsplugin",
            manifest=manifest_payload(
                "demo_analysis",
                api_version=2,
                capabilities=["data_snapshot_2d", "analysis_task", "result_curve_1d"],
            ),
            entry_source=PANEL_ENTRY_SOURCE,
        )
        install_package(archive, root=self.root, source=synthetic_source())
        manager = PluginManager(root=self.root)
        self.addCleanup(manager.shutdown)
        manager.startup()
        record = manager.record("demo_analysis")
        self.assertTrue(record.ready, record.load_error)

        v1_record = SimpleNamespace(manifest=SimpleNamespace(capabilities=("opacity_multiplier",)))
        self.assertFalse(PluginSession.is_analysis_plugin(v1_record))
        self.assertTrue(PluginSession.is_analysis_plugin(record))

        session = self.make_session(make_window())
        session.manager = manager
        session.mount_cards(SimpleNamespace(mount_extension_card=lambda *a: object()))
        self.assertEqual(session.card_ids(), [])

        mounted = []
        page = SimpleNamespace(
            mount_analysis_card=lambda pid, title, panel: mounted.append((pid, title, panel)) or object()
        )
        session.mount_analysis_cards(page)
        self.assertEqual([item[0] for item in mounted], ["demo_analysis"])
        self.assertEqual(session.analysis_card_ids(), ["demo_analysis"])

    def test_v1_plugin_still_get_a_v1_compatible_host(self):
        install_fake(self.root)  # api_version = 当前宿主版本(2)
        archive = make_plugin_archive(
            self.root / "staging" / "v1.bsplugin",
            manifest=manifest_payload("old_plugin", api_version=1),
        )
        install_package(archive, root=self.root, source=synthetic_source())
        manager = PluginManager(root=self.root)
        self.addCleanup(manager.shutdown)
        manager.startup()
        record = manager.record("old_plugin")
        self.assertTrue(record.ready, record.load_error)
        bridge = PluginSession(make_window(), manager=manager).host_for("old_plugin")
        self.assertTrue(hasattr(bridge, "context"))
        self.assertTrue(hasattr(bridge, "request_refresh"))
        self.assertTrue(hasattr(bridge, "notify"))


if __name__ == "__main__":
    unittest.main()
