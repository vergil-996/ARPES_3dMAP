# -*- coding: utf-8 -*-
"""轴标题接进主窗口与导出的那段：默认轴名、会话隔离与快照文字。

默认轴名按切面确定（X 切面 ky / E、Y 切面 kx / E、E 切面 kx / ky），有
``plot_axes`` 时以实际轴映射为准；单位只来自数据元信息，缺单位不猜，索引
坐标标 index。导出快照采用主画布的自定义轴名，截图样式的显式覆盖优先。
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.exporting.publication_export import capture_snapshot
from bandscope.exporting.publication_models import resolve_axis_labels
from bandscope.rendering.render_core import VisualEngine
from bandscope.ui.axis_title_controller import AxisTitleState, axis_title_key
from bandscope.ui.result_workspace import AnalysisPageSpec

PAGE = "p1"


def _context(axis_index=2, x_key=None, y_key=None, x_label="kx", y_label="ky"):
    """2D 渲染上下文；plot_axes 缺省按切面给出。"""
    plan = {0: ("Y", "E"), 1: ("X", "E"), 2: ("X", "Y")}[axis_index]
    labels = {0: ("ky", "E"), 1: ("kx", "E"), 2: ("kx", "ky")}[axis_index]
    x_key = plan[0] if x_key is None else x_key
    y_key = plan[1] if y_key is None else y_key
    return {
        "view": "2d",
        "data": np.ones((5, 6), dtype=np.float64),
        "slice_info": {"axis": axis_index, "mode": "slice", "index": 0},
        "coords": {
            "X": np.linspace(-1.0, 1.0, 5),
            "Y": np.linspace(-1.0, 1.0, 6),
            "E": np.linspace(-2.0, 2.0, 7),
            "delay": np.array([0.0, 1.0]),
        },
        "plot_axes": {"x_key": x_key, "y_key": y_key, "x_label": x_label, "y_label": y_label},
    }


def _analyzer(context=None, *, sources=None, units=None, spec=None):
    analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
    spec = spec or AnalysisPageSpec(PAGE, "X轴积分", "axis_integral", "test")
    analyzer.axis_title_state = AxisTitleState()
    analyzer.left_workspace = SimpleNamespace(
        current_spec=lambda: spec,
        home_spec=lambda: spec,
        page_by_id=lambda page_id: spec if page_id == spec.page_id else None,
        page_specs={spec.page_id: spec},
    )
    analyzer.core = SimpleNamespace(
        raw_data=np.zeros((5, 6, 7, 2), dtype=np.float32),
        coords=_context()["coords"],
        coord_sources=dict(sources or {"X": "file", "Y": "file", "E": "file"}),
        coord_units=dict(units or {"X": "Å⁻¹", "Y": "Å⁻¹", "E": None}),
        has_time_axis=False,
    )
    analyzer.current_render_context = context if context is not None else _context()
    analyzer.page_render = SimpleNamespace(get_selected_cmap=lambda: "magma")
    analyzer._compute_render_context = lambda _spec: analyzer.current_render_context
    analyzer._render_context_for_visual_flip = lambda value: value
    analyzer._get_display_levels = lambda: (0.0, 50.0, 100.0)
    return analyzer


class DefaultAxisTitleTests(unittest.TestCase):
    def test_default_names_follow_the_slice_plane(self):
        expected = {0: ("ky (Å⁻¹)", "E"), 1: ("kx (Å⁻¹)", "E"), 2: ("kx (Å⁻¹)", "ky (Å⁻¹)")}
        for axis_index, names in expected.items():
            with self.subTest(axis=axis_index):
                analyzer = _analyzer(_context(axis_index))
                titles, positions = analyzer._render_2d_axis_titles(
                    analyzer.current_render_context, analyzer.left_workspace.current_spec()
                )
                self.assertEqual((titles["x"], titles["y"]), names)
                self.assertEqual(positions, {})

    def test_plot_axes_mapping_wins_over_the_slice_plane(self):
        # 裁剪后的 2D 页可能带着实际轴映射；按它标轴，而不是按切面编号猜。
        context = _context(0, x_key="E", y_key="Y")
        analyzer = _analyzer(context)
        titles, _ = analyzer._render_2d_axis_titles(context, analyzer.left_workspace.current_spec())
        self.assertEqual((titles["x"], titles["y"]), ("E", "ky (Å⁻¹)"))

    def test_missing_units_are_not_guessed_and_index_axes_say_index(self):
        analyzer = _analyzer(units={"X": None, "Y": None, "E": None})
        titles, _ = analyzer._render_2d_axis_titles(
            analyzer.current_render_context, analyzer.left_workspace.current_spec()
        )
        self.assertEqual((titles["x"], titles["y"]), ("kx", "ky"))

        analyzer = _analyzer(sources={"X": "index", "Y": "file", "E": "file"},
                             units={"X": "Å⁻¹", "Y": None, "E": None})
        titles, _ = analyzer._render_2d_axis_titles(
            analyzer.current_render_context, analyzer.left_workspace.current_spec()
        )
        self.assertEqual((titles["x"], titles["y"]), ("kx (index)", "ky"))

    def test_axis_title_key_separates_page_view_and_axis_pair(self):
        analyzer = _analyzer()
        context = analyzer.current_render_context
        spec = analyzer.left_workspace.current_spec()
        first = analyzer._axis_title_context(context, spec)["key"]
        fourth = analyzer._axis_title_context(_context(0), spec)["key"]
        self.assertEqual(first, axis_title_key(PAGE, "2d", ("X", "Y")))
        self.assertNotEqual(first, fourth)

    def test_renaming_one_axis_pair_leaves_the_other_alone(self):
        analyzer = _analyzer()
        spec = analyzer.left_workspace.current_spec()
        key = analyzer._axis_title_context(analyzer.current_render_context, spec)["key"]
        analyzer.axis_title_state.set_text(key, "x", "Binding energy")

        titles, _ = analyzer._render_2d_axis_titles(analyzer.current_render_context, spec)
        self.assertEqual(titles["x"], "Binding energy")
        self.assertEqual(titles["y"], "ky (Å⁻¹)")

        other_titles, _ = analyzer._render_2d_axis_titles(_context(0), spec)
        self.assertEqual(other_titles["x"], "ky (Å⁻¹)")

    def test_positions_are_passed_through_relative_coordinates(self):
        analyzer = _analyzer()
        spec = analyzer.left_workspace.current_spec()
        key = analyzer._axis_title_context(analyzer.current_render_context, spec)["key"]
        analyzer.axis_title_state.set_position(key, "y", (0.1, 0.4))
        _, positions = analyzer._render_2d_axis_titles(analyzer.current_render_context, spec)
        self.assertEqual(positions, {"y": (0.1, 0.4)})

    def test_three_d_titles_keep_the_existing_defaults_and_track_the_page(self):
        analyzer = _analyzer()
        self.assertEqual(
            analyzer._three_d_axis_titles(), VisualEngine.DEFAULT_3D_AXIS_TITLES
        )
        key = analyzer._three_d_axis_title_key()
        analyzer.axis_title_state.set_text(key, "E", "Binding energy (eV)")
        self.assertEqual(
            analyzer._three_d_axis_titles(), ("Kx", "Ky", "Binding energy (eV)")
        )
        other = AnalysisPageSpec("p2", "另一个 3D 页", "home", "test")
        analyzer.left_workspace.page_specs["p2"] = other
        self.assertEqual(
            analyzer._three_d_axis_titles(other), VisualEngine.DEFAULT_3D_AXIS_TITLES
        )

    def test_1d_pages_take_the_axis_labels_back_from_a_2d_drag(self):
        """拖动留下的显式位置只属于 2D 页；1D 页复用同一个 ax_2d。"""
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        figure = Figure(figsize=(4, 3), dpi=100)
        canvas = FigureCanvasAgg(figure)
        analyzer.ax_2d = figure.add_subplot(111)
        analyzer._display_title_for_1d_plot = lambda title: title
        analyzer.ax_2d.xaxis.set_label_coords(0.2, 0.3)
        self.assertFalse(analyzer.ax_2d.xaxis._autolabelpos)

        analyzer._style_1d_axes({"title": "EDC", "xlabel": "E (eV)"}, ylabel="Intensity")

        self.assertTrue(analyzer.ax_2d.xaxis._autolabelpos)
        self.assertTrue(analyzer.ax_2d.yaxis._autolabelpos)
        canvas.draw()
        self.assertEqual(analyzer.ax_2d.get_xlabel(), "E (eV)")

    def test_closing_a_page_forgets_its_axis_titles(self):
        analyzer = _analyzer()
        analyzer.crop_controller = SimpleNamespace(remove_page=Mock())
        analyzer.refresh_coordinator = SimpleNamespace(cancel_page=Mock())
        analyzer._clear_axis_prefix_cache = Mock()
        analyzer._rotation_cache = {}
        analyzer._computed_volume_cache = {}
        analyzer._second_derivative_volume_cache = None
        analyzer.active_page_spec = None
        analyzer.last_visual_page_id = PAGE
        analyzer._discard_unreferenced_roi_scopes = Mock()
        key = analyzer._axis_title_context(analyzer.current_render_context)["key"]
        analyzer.axis_title_state.set_text(key, "x", "kx")

        analyzer.on_result_page_closed(PAGE)
        self.assertEqual(analyzer.axis_title_state.texts(key), {})


class ExportSnapshotAxisTitleTests(unittest.TestCase):
    def test_snapshot_uses_the_canvas_title(self):
        analyzer = _analyzer()
        spec = analyzer.left_workspace.current_spec()
        key = analyzer._axis_title_context(analyzer.current_render_context, spec)["key"]
        analyzer.axis_title_state.set_text(key, "x", "Binding energy (eV)")

        snapshot = capture_snapshot(analyzer)
        self.assertEqual(snapshot.payload["xlabel"], "Binding energy (eV)")
        self.assertEqual(snapshot.payload["ylabel"], "ky (Å⁻¹)")

    def test_auto_labels_are_untouched_without_a_rename(self):
        snapshot = capture_snapshot(_analyzer())
        self.assertEqual(snapshot.payload["xlabel"], "kx (Å⁻¹)")
        self.assertEqual(snapshot.payload["ylabel"], "ky (Å⁻¹)")

    def test_a_hidden_canvas_title_stays_hidden_in_the_snapshot(self):
        analyzer = _analyzer()
        spec = analyzer.left_workspace.current_spec()
        key = analyzer._axis_title_context(analyzer.current_render_context, spec)["key"]
        analyzer.axis_title_state.set_text(key, "y", "")
        snapshot = capture_snapshot(analyzer)
        self.assertEqual(snapshot.payload["ylabel"], "")

    def test_style_override_beats_the_canvas_title(self):
        analyzer = _analyzer()
        spec = analyzer.left_workspace.current_spec()
        key = analyzer._axis_title_context(analyzer.current_render_context, spec)["key"]
        analyzer.axis_title_state.set_text(key, "x", "Binding energy (eV)")

        snapshot = capture_snapshot(analyzer)
        self.assertEqual(
            resolve_axis_labels(snapshot, {})[0], "Binding energy (eV)"
        )
        self.assertEqual(
            resolve_axis_labels(snapshot, {"xlabel_text": "Fig. 3 x"})[0], "Fig. 3 x"
        )

    def test_page_rename_feeds_the_snapshot_title_and_default_filename(self):
        import bandscope.exporting.publication_export as export_module

        analyzer = _analyzer()
        spec = analyzer.left_workspace.current_spec()
        spec.title = "费米面"
        snapshot = capture_snapshot(analyzer)
        self.assertEqual(snapshot.source_page_title, "费米面")

        style = SimpleNamespace(style_id="2d_open")
        options = SimpleNamespace(fmt="png")
        self.assertEqual(
            export_module.default_filename(snapshot, style, options), "费米面_2d_open.png"
        )

    def test_renaming_while_the_dialog_is_open_marks_the_snapshot_stale(self):
        """快照的来源名与默认文件名跟着页面走，改名后要提示重新取快照。"""
        from PyQt5.QtWidgets import QApplication, QWidget

        from bandscope.exporting.publication_dialog import PublicationExportDialog
        from tests.support.publication import _make_2d_snapshot

        app = QApplication.instance() or QApplication([])
        # 父窗口要留引用：临时 QWidget 被回收时会把子对话框一起带走。
        parent = QWidget()
        dialog = PublicationExportDialog(parent)
        try:
            snapshot = _make_2d_snapshot()
            dialog.snapshot = snapshot
            spec = AnalysisPageSpec(PAGE, snapshot.source_page_title, "axis_integral", "test")
            dialog.main_window = SimpleNamespace(
                left_workspace=SimpleNamespace(current_spec=lambda: spec),
                core=SimpleNamespace(raw_data=None, has_time_axis=False),
                timeline_bar=SimpleNamespace(),
            )
            self.assertEqual(
                dialog._current_main_state_token(), dialog._snapshot_state_token()
            )

            spec.title = "换了个名字"
            self.assertNotEqual(
                dialog._current_main_state_token(), dialog._snapshot_state_token()
            )
        finally:
            dialog._debounce.stop()
            dialog._stale_timer.stop()
            dialog.close()
            dialog.deleteLater()
            parent.close()
            parent.deleteLater()
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
