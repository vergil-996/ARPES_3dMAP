# -*- coding: utf-8 -*-
"""科研图片导出：数值契约、状态隔离与排版可靠性单元测试。

覆盖 plan v2 §7.3 的实质风险：样式库结构、色阶纯度、标签诚实性、
映射与刻度、物理尺寸、原子保存、曲线身份映射；不含逐像素对比。
"""
import os
import sys
import tempfile
import unittest

import numpy as np


import bandscope.exporting.publication_models as pm
from bandscope.exporting.publication_models import (
    DEFAULT_STYLE_ID,
    OutputOptions,
    PublicationSnapshot,
    STYLE_REGISTRY,
    auto_axis_labels,
    axis_label,
    intensity_label_for,
    resolve_axis_labels,
    validate_overrides,
    view_family_for,
)
from bandscope.exporting.publication_renderers import (
    RENDER_LOCK,
    _draw_3d_axes,
    compute_level_info,
    format_tick_labels,
    nice_ticks,
    render_snapshot,
    save_figure,
)
from bandscope.rendering.render_core import VisualEngine


from tests.support.publication import (
    _fresh_class_state, _make_2d_snapshot, _FakeVTKRenderer,
    _make_3d_axes_snapshot, _make_1d_comparison_snapshot,
)


class StyleRegistryTests(unittest.TestCase):
    def test_nine_styles_in_three_families(self):
        self.assertEqual(set(STYLE_REGISTRY), {"3d", "2d", "1d"})
        for family, styles in STYLE_REGISTRY.items():
            self.assertEqual(len(styles), 3, family)
            self.assertIn(DEFAULT_STYLE_ID[family], styles)
            for style in styles.values():
                self.assertEqual(style.view_family, family)
                self.assertTrue(style.full_name)

    def test_view_family_mapping(self):
        self.assertEqual(view_family_for("3d"), "3d")
        self.assertEqual(view_family_for("2d"), "2d")
        self.assertEqual(view_family_for("1d"), "1d")
        self.assertEqual(view_family_for("1d_comparison"), "1d")
        self.assertEqual(view_family_for("waterfall"), "1d")
        self.assertIsNone(view_family_for("config"))

    def test_resolve_style_falls_back_to_family_default(self):
        style = pm.resolve_style("2d", "does_not_exist")
        self.assertEqual(style.style_id, DEFAULT_STYLE_ID["2d"])

    def test_overrides_validation_scopes_by_family(self):
        # 1D 不接收色条微调
        ov = validate_overrides("1d", {"colorbar_visible": False, "frame_mode": "box"})
        self.assertEqual(ov, {"frame_mode": "box"})
        # 3D 不接收 frame_mode，2D/1D 不接收 show_box
        self.assertEqual(validate_overrides("3d", {"frame_mode": "box"}), {})
        self.assertEqual(validate_overrides("2d", {"show_box": True}), {})
        # 非法值与未知键丢弃
        self.assertEqual(
            validate_overrides("2d", {"colorbar_position": "sky", "unknown": 1}),
            {},
        )
        # 数值夹取
        self.assertEqual(
            validate_overrides("2d", {"colorbar_nticks": 99}),
            {"colorbar_nticks": 8},
        )

    def test_title_overrides_validation(self):
        # 空串是合法覆盖（用户明确清空标题），不能像 panel_label 那样被丢弃：
        # "title_text 存在且为空" 与 "title_text 缺省（用自动标题）" 是两种状态
        self.assertEqual(validate_overrides("2d", {"title_text": ""}), {"title_text": ""})
        self.assertEqual(
            validate_overrides("1d", {"title_text": "  Fig. 3(a)  "}),
            {"title_text": "Fig. 3(a)"},
        )
        # 标题微调对三个视图族都有效
        for family in ("1d", "2d", "3d"):
            self.assertEqual(
                validate_overrides(family, {
                    "title_position": "bottom", "title_align": "left", "title_gap_mm": 5.0,
                }),
                {"title_position": "bottom", "title_align": "left", "title_gap_mm": 5.0},
                family,
            )
        # 非法取值丢弃、数值夹取、超长截断
        self.assertEqual(validate_overrides("3d", {"title_position": "left"}), {})
        self.assertEqual(validate_overrides("3d", {"title_align": "middle"}), {})
        self.assertEqual(validate_overrides("2d", {"title_gap_mm": 99}), {"title_gap_mm": 20.0})
        self.assertEqual(len(validate_overrides("2d", {"title_text": "x" * 300})["title_text"]), 120)

    def test_axis_label_overrides_validation(self):
        # 空串是合法覆盖（用户明确不要该轴标签），必须与"未设置=用自动轴名"区分开
        self.assertEqual(validate_overrides("2d", {"xlabel_text": ""}), {"xlabel_text": ""})
        self.assertEqual(
            validate_overrides("1d", {"ylabel_text": "  Intensity (a.u.)  "}),
            {"ylabel_text": "Intensity (a.u.)"},
        )
        # 第三轴名只属于 3D 族
        self.assertEqual(validate_overrides("3d", {"zlabel_text": "E (eV)"}), {"zlabel_text": "E (eV)"})
        self.assertEqual(validate_overrides("2d", {"zlabel_text": "E (eV)"}), {})
        self.assertEqual(validate_overrides("1d", {"zlabel_text": "E (eV)"}), {})
        # 超长截断（轴名比图题短得多）
        self.assertEqual(
            len(validate_overrides("2d", {"xlabel_text": "x" * 300})["xlabel_text"]), 40
        )


class LevelInfoPurityTests(unittest.TestCase):
    def test_compute_level_info_does_not_touch_class_state(self):
        _fresh_class_state()
        data = np.linspace(0.0, 100.0, 64)
        compute_level_info(data, (10.0, 50.0, 90.0))
        self.assertIsNone(VisualEngine._last_data_range)
        self.assertIsNone(VisualEngine._locked_data_range)

    def test_locked_range_is_used_without_class_state(self):
        _fresh_class_state()
        info = compute_level_info(
            np.array([1.0, 2.0]), (0.0, 50.0, 100.0), locked_range=(5.0, 95.0)
        )
        self.assertEqual(info["data_min"], 5.0)
        self.assertEqual(info["data_max"], 95.0)

    def test_matches_visual_engine_math(self):
        _fresh_class_state()
        rng = np.random.default_rng(0)
        data = rng.random((32, 32)) * 500
        mine = compute_level_info(data, (12.0, 40.0, 88.0))
        theirs = VisualEngine._level_info(data, (12.0, 40.0, 88.0))
        for key in ("black_value", "gray_value", "white_value", "gamma_power"):
            self.assertAlmostEqual(mine[key], theirs[key], places=12)

    def test_constant_and_nan_data(self):
        info = compute_level_info(np.full(8, 7.0), (0.0, 50.0, 100.0))
        self.assertGreater(info["span"], 0.0)
        info = compute_level_info(np.full(8, np.nan), (0.0, 50.0, 100.0))
        self.assertTrue(np.isfinite(info["black_value"]))


class TickFormattingTests(unittest.TestCase):
    def test_constant_data_single_tick(self):
        ticks = nice_ticks(5.0, 5.0, 3)
        self.assertEqual(len(ticks), 1)

    def test_no_meaningless_negative_zero(self):
        labels, _ = format_tick_labels(np.array([-0.0, 0.5, 1.0]), 0.0, 1.0)
        for label in labels:
            self.assertFalse(label.startswith(("-0", "−0")), labels)

    def test_common_scientific_exponent_appears_once(self):
        ticks = np.array([0.0, 5e-5, 1e-4])
        labels, offset = format_tick_labels(ticks, 0.0, 1e-4)
        self.assertTrue(offset)
        for label in labels:
            self.assertNotIn("e", label.lower())

    def test_nice_ticks_stay_in_range(self):
        ticks = nice_ticks(-1.7, 3.2, 5)
        self.assertTrue(np.all(ticks >= -1.7 - 1e-9))
        self.assertTrue(np.all(ticks <= 3.2 + 1e-9))
        self.assertGreaterEqual(len(ticks), 2)


class LabelHonestyTests(unittest.TestCase):
    def test_explicit_unit_is_used(self):
        self.assertEqual(axis_label("X", "file", "Å⁻¹"), "kx (Å⁻¹)")
        self.assertEqual(axis_label("E", "file", "eV"), "E (eV)")

    def test_file_coords_without_unit_do_not_guess(self):
        self.assertEqual(axis_label("E", "file", None), "E")
        self.assertEqual(axis_label("X", "file", ""), "kx")

    def test_index_fallback_never_fakes_units(self):
        self.assertEqual(axis_label("E", "index"), "E (index)")
        self.assertEqual(axis_label("X", "index"), "kx (index)")
        self.assertEqual(axis_label("delay", "index"), "Delay (index)")

    def test_intensity_label_distinguishes_computed_quantity(self):
        self.assertEqual(intensity_label_for("home"), "Intensity (a.u.)")
        self.assertEqual(intensity_label_for("axis_integral", normalized=True), "Normalized intensity")
        self.assertIn("d²I", intensity_label_for("second_derivative"))

    def test_snapshot_axis_label_uses_provenance(self):
        snap = _make_2d_snapshot()
        self.assertEqual(snap.axis_label("X"), "kx (Å⁻¹)")
        self.assertEqual(snap.axis_label("E"), "E")
        self.assertEqual(snap.axis_label("delay"), "Delay (index)")

    def test_auto_axis_labels_follow_the_view_family(self):
        self.assertEqual(auto_axis_labels(_make_2d_snapshot()), ("kx (Å⁻¹)", "E"))
        snap = _make_2d_snapshot()
        snap.view, snap.view_family = "3d", "3d"
        snap.payload = dict(snap.payload, axis_titles=["kx (Å⁻¹)", "ky (Å⁻¹)", "E (eV)"])
        self.assertEqual(auto_axis_labels(snap), ("kx (Å⁻¹)", "ky (Å⁻¹)", "E (eV)"))

    def test_resolve_axis_labels_prefers_explicit_text(self):
        snap = _make_2d_snapshot()
        # 缺省 → 自动标签（仍受坐标来源与单位约束，不因本功能而改写）
        self.assertEqual(resolve_axis_labels(snap, {}), ("kx (Å⁻¹)", "E"))
        # 显式命名覆盖自动标签
        self.assertEqual(
            resolve_axis_labels(snap, {"xlabel_text": "Binding energy (eV)"}),
            ("Binding energy (eV)", "E"),
        )
        # 空串 = 明确不要该轴标签，不能被当成"未设置"退回自动值
        self.assertEqual(resolve_axis_labels(snap, {"ylabel_text": ""}), ("kx (Å⁻¹)", ""))
        self.assertEqual(resolve_axis_labels(snap, {"xlabel_text": "", "ylabel_text": ""}), ("", ""))

    def test_resolve_axis_labels_covers_three_axes_for_3d(self):
        snap = _make_2d_snapshot()
        snap.view, snap.view_family = "3d", "3d"
        snap.payload = dict(snap.payload, axis_titles=["kx", "ky", "E (eV)"])
        self.assertEqual(
            resolve_axis_labels(snap, {"zlabel_text": "Energy above E_F (eV)"}),
            ("kx", "ky", "Energy above E_F (eV)"),
        )


class AxisName3dTests(unittest.TestCase):
    """3D 轴名的用户覆盖：不接 VTK 整图渲染，直接驱动轴线绘制。

    3D 轴名与刻度同属离屏截图后的叠加注记（_draw_3d_axes），轴名文字取自
    resolve_axis_labels，与 2D/1D 同一条解析路径。
    """

    def _axis_name_texts(self, overrides):
        from matplotlib.figure import Figure

        snap = _make_3d_axes_snapshot()
        style = STYLE_REGISTRY["3d"]["3d_minimal"]
        fig = Figure(figsize=(3.0, 3.0), dpi=150)
        ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
        ax.set_xlim(0.0, 1000.0)
        ax.set_ylim(0.0, 1000.0)
        _draw_3d_axes(ax, _FakeVTKRenderer(), snap, style.params, overrides)
        texts = [text.get_text() for text in ax.texts]
        fig.clear()
        return texts

    def test_axis_names_fall_back_to_snapshot_titles(self):
        texts = self._axis_name_texts({})
        for title in ("kx (Å⁻¹)", "ky (Å⁻¹)", "E (eV)"):
            self.assertIn(title, texts)

    def test_overrides_replace_and_can_clear_axis_names(self):
        texts = self._axis_name_texts({
            "xlabel_text": "Binding energy − E_F (eV)",
            "zlabel_text": "",
        })
        self.assertIn("Binding energy − E_F (eV)", texts)
        self.assertNotIn("kx (Å⁻¹)", texts)   # 被用户命名替换
        self.assertNotIn("E (eV)", texts)     # 空串 = 不画该轴名
        self.assertIn("ky (Å⁻¹)", texts)      # 未改动的轴仍是自动轴名


class PhysicalSizeTests(unittest.TestCase):
    def test_figure_size_matches_mm(self):
        snap = _make_2d_snapshot()
        options = OutputOptions(width_mm=89.0, dpi=150, fmt="png")
        for style in STYLE_REGISTRY["2d"].values():
            fig = render_snapshot(snap, style, {}, options, dpi=150)
            w_in, h_in = fig.get_size_inches()
            self.assertAlmostEqual(w_in, 89.0 / 25.4, places=6)
            self.assertAlmostEqual(h_in, 75.0 / 25.4, places=6)
            fig.clear()

    def test_png_pixels_and_dpi_metadata(self):
        snap = _make_2d_snapshot()
        options = OutputOptions(width_mm=89.0, dpi=600, fmt="png")
        style = STYLE_REGISTRY["2d"]["2d_boxed"]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "figure.png")
            fig = render_snapshot(snap, style, {}, options, dpi=600)
            save_figure(fig, path, options)
            fig.clear()
            from PIL import Image

            with Image.open(path) as image:
                expected_w = round(89.0 / 25.4 * 600)
                self.assertLessEqual(abs(image.width - expected_w), 1)
                dpi_meta = image.info.get("dpi")
                self.assertIsNotNone(dpi_meta)
                self.assertLessEqual(abs(dpi_meta[0] - 600), 1.0)

    def test_pdf_page_size_and_text_objects(self):
        snap = _make_2d_snapshot()
        options = OutputOptions(width_mm=183.0, dpi=300, fmt="pdf")
        style = STYLE_REGISTRY["2d"]["2d_open"]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "figure.pdf")
            fig = render_snapshot(snap, style, {}, options, dpi=300)
            save_figure(fig, path, options)
            fig.clear()
            with open(path, "rb") as fh:
                content = fh.read()
            # MediaBox 尺寸 = 183mm x 75mm（pt = mm/25.4*72）；全精度输出，按前缀断言
            self.assertIn(b"0 0 518.740", content)
            self.assertIn(b"212.598", content)
            # 内容流是 FlateDecode 压缩的：解压后应含文本操作符（文字保持为文本对象）
            import re
            import zlib

            inflated = b""
            for match in re.finditer(rb"stream\r?\n(.*?)endstream", content, re.S):
                try:
                    inflated += zlib.decompress(match.group(1))
                except zlib.error:
                    continue
            self.assertIn(b"TJ", inflated)
            self.assertIn(b"BT", inflated)


class TransparentBackgroundTests(unittest.TestCase):
    """透明背景：序列化契约、信号透传，以及产物真的带 alpha。

    棋盘格只存在于面板显示层，产物必须是真透明——这里从文件读回来断言。
    """

    def _alpha_at_corner(self, path):
        from PIL import Image

        with Image.open(path) as image:
            return image.convert("RGBA").getpixel((0, 0))[3]

    def test_output_options_json_round_trip(self):
        options = OutputOptions(width_mm=89.0, dpi=300, fmt="png", transparent=True)
        restored = OutputOptions.from_json(options.to_json())
        self.assertTrue(restored.transparent)
        self.assertEqual(restored, options)

    def test_missing_key_defaults_to_opaque(self):
        # 旧版本写入的偏好没有 transparent 键，必须回退为不透明而不是报错
        legacy = '{"width_mm": 89.0, "height_mm": null, "dpi": 600, "fmt": "png"}'
        options = OutputOptions.from_json(legacy)
        self.assertFalse(options.transparent)
        self.assertEqual(options.width_mm, 89.0)
        self.assertEqual(options.dpi, 600)

    def test_signature_distinguishes_transparency(self):
        # 预览缓存键依赖 signature：开关切换必须使旧预览失效
        opaque = OutputOptions(width_mm=89.0, dpi=150, fmt="png")
        clear = OutputOptions(width_mm=89.0, dpi=150, fmt="png", transparent=True)
        self.assertNotEqual(opaque.signature("2d"), clear.signature("2d"))

    def test_png_carries_alpha_when_transparent(self):
        snap = _make_2d_snapshot()
        options = OutputOptions(width_mm=89.0, dpi=120, fmt="png", transparent=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "figure.png")
            fig = render_snapshot(
                snap, STYLE_REGISTRY["2d"]["2d_boxed"], {}, options, dpi=120
            )
            save_figure(fig, path, options)
            fig.clear()
            self.assertEqual(self._alpha_at_corner(path), 0)

    def test_png_stays_opaque_by_default(self):
        # 守住既有白底行为：不勾选时产物与改动前一致
        snap = _make_2d_snapshot()
        options = OutputOptions(width_mm=89.0, dpi=120, fmt="png")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "figure.png")
            fig = render_snapshot(
                snap, STYLE_REGISTRY["2d"]["2d_boxed"], {}, options, dpi=120
            )
            save_figure(fig, path, options)
            fig.clear()
            self.assertEqual(self._alpha_at_corner(path), 255)

    def _first_paint_op(self, pdf_bytes):
        """PDF 首个内容流中第一条路径的绘制算符。

        首条路径是整页底板矩形：不透明时 matplotlib 用 ``f`` 把它填成白色，
        透明时改用 ``n``（只提交路径、不填充）。这是"有没有画白底"的直接证据。
        """
        import re
        import zlib

        for match in re.finditer(rb"stream\r?\n(.*?)endstream", pdf_bytes, re.S):
            try:
                stream = zlib.decompress(match.group(1))
            except zlib.error:
                continue
            paint = re.search(rb"\bh\s*\n\s*\n\s*([A-Za-z]+)", stream)
            if paint is not None:
                return paint.group(1)
        return None

    def test_pdf_page_is_transparent_when_requested(self):
        snap = _make_1d_comparison_snapshot()
        style = STYLE_REGISTRY["1d"]["1d_open"]
        paints = {}
        for transparent in (False, True):
            options = OutputOptions(
                width_mm=89.0, dpi=300, fmt="pdf", transparent=transparent
            )
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "figure.pdf")
                fig = render_snapshot(snap, style, {}, options, dpi=300)
                save_figure(fig, path, options)
                fig.clear()
                with open(path, "rb") as fh:
                    content = fh.read()
            paints[transparent] = self._first_paint_op(content)
        self.assertEqual(paints[False], b"f")   # 整页白底被填充
        self.assertEqual(paints[True], b"n")    # 仅提交路径，不填白底

    def test_content_bbox_prefers_alpha_over_whiteness(self):
        from bandscope.exporting.publication_renderers import _content_bbox

        # 4 通道：边框 alpha=0 为空，中央 2x2 为内容。若按"非白"判定会把
        # 深色区域也算进去，这里断言它按 alpha 判定。
        rgba = np.zeros((10, 10, 4), dtype=np.uint8)
        rgba[..., :3] = 30  # 深色但全透明——不是内容
        rgba[4:6, 4:6, 3] = 255
        self.assertEqual(_content_bbox(rgba, 0), (4, 6, 4, 6))
        # 3 通道沿用原判定：非白即内容
        rgb = np.full((10, 10, 3), 255, dtype=np.uint8)
        rgb[2:8, 3:7] = 0
        self.assertEqual(_content_bbox(rgb, 0), (2, 8, 3, 7))
        # 全透明无内容
        self.assertIsNone(_content_bbox(np.zeros((10, 10, 4), dtype=np.uint8), 0))


class AtomicSaveTests(unittest.TestCase):
    def _render(self, tmp):
        snap = _make_2d_snapshot()
        options = OutputOptions(width_mm=89.0, dpi=120, fmt="png")
        fig = render_snapshot(snap, STYLE_REGISTRY["2d"]["2d_boxed"], {}, options, dpi=120)
        return fig, options

    def test_successful_save_replaces_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fig.png")
            fig, options = self._render(tmp)
            save_figure(fig, path, options)
            fig.clear()
            self.assertTrue(os.path.getsize(path) > 0)
            first_size = os.path.getsize(path)
            fig, options = self._render(tmp)
            save_figure(fig, path, options)
            fig.clear()
            self.assertTrue(os.path.getsize(path) > 0)
            self.assertNotEqual(first_size, 0)

    def test_failed_save_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fig.png")
            with open(path, "wb") as fh:
                fh.write(b"OLD-CONTENT")
            fig, options = self._render(tmp)
            fig.clear()  # 已清空的 Figure 触发保存失败路径
            options = OutputOptions(width_mm=89.0, dpi=120, fmt="png")

            class _BrokenFigure:
                def savefig(self, *args, **kwargs):
                    raise RuntimeError("simulated write failure")

            with self.assertRaises(Exception):
                save_figure(_BrokenFigure(), path, options)
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), b"OLD-CONTENT")
            # 不残留临时文件
            self.assertEqual(os.listdir(tmp), ["fig.png"])


class RendererContentTests(unittest.TestCase):
    def test_2d_peak_position_preserved(self):
        snap = _make_2d_snapshot()
        options = OutputOptions(width_mm=89.0, dpi=150, fmt="png")
        fig = render_snapshot(snap, STYLE_REGISTRY["2d"]["2d_boxed"], {}, options, dpi=150)
        ax = fig.axes[0]
        image = ax.images[0]
        data = np.asarray(image.get_array())
        peak = np.unravel_index(np.argmax(data), data.shape)
        extent = image.get_extent()
        # 峰值数据坐标应与合成峰 (0.3, -0.8) 一致
        peak_x = extent[0] + (peak[1] + 0.5) / data.shape[1] * (extent[1] - extent[0])
        peak_y = extent[2] + (peak[0] + 0.5) / data.shape[0] * (extent[3] - extent[2])
        self.assertAlmostEqual(peak_x, 0.3, delta=0.05)
        self.assertAlmostEqual(peak_y, -0.8, delta=0.08)
        fig.clear()

    def test_axis_label_overrides_reach_the_figure(self):
        snap = _make_2d_snapshot()
        style = STYLE_REGISTRY["2d"]["2d_boxed"]
        options = OutputOptions(width_mm=89.0, dpi=150, fmt="png")

        fig = render_snapshot(snap, style, {}, options, dpi=150)
        self.assertEqual(fig.axes[0].get_xlabel(), "kx (Å⁻¹)")
        self.assertEqual(fig.axes[0].get_ylabel(), "E")
        fig.clear()

        overrides = {"xlabel_text": "Binding energy (eV)", "ylabel_text": ""}
        fig = render_snapshot(snap, style, overrides, options, dpi=150)
        self.assertEqual(fig.axes[0].get_xlabel(), "Binding energy (eV)")
        # 空串 = 不画该轴标签，不能退回自动标签
        self.assertEqual(fig.axes[0].get_ylabel(), "")
        fig.clear()

        # 1D 族（含 1d_comparison / waterfall）走同一条解析
        snap_1d = _make_1d_comparison_snapshot()
        fig = render_snapshot(
            snap_1d, STYLE_REGISTRY["1d"]["1d_open"],
            {"ylabel_text": "Intensity (arb. units)"}, options, dpi=150,
        )
        self.assertEqual(fig.axes[0].get_xlabel(), "E (eV)")
        self.assertEqual(fig.axes[0].get_ylabel(), "Intensity (arb. units)")
        fig.clear()

    def test_2d_render_does_not_touch_shared_state_or_rcparams(self):
        import matplotlib as mpl

        _fresh_class_state()
        rc_before = dict(mpl.rcParams)
        snap = _make_2d_snapshot()
        options = OutputOptions(width_mm=89.0, dpi=100, fmt="png")
        for style in STYLE_REGISTRY["2d"].values():
            fig = render_snapshot(snap, style, {}, options, dpi=100)
            fig.clear()
        self.assertIsNone(VisualEngine._last_data_range)
        self.assertIsNone(VisualEngine._locked_data_range)
        self.assertEqual(rc_before, dict(mpl.rcParams))

    def test_comparison_identity_and_base_emphasis(self):
        snap = _make_1d_comparison_snapshot()
        options = OutputOptions(width_mm=89.0, dpi=120, fmt="png")
        for style in STYLE_REGISTRY["1d"].values():
            fig = render_snapshot(snap, style, {}, options, dpi=120)
            ax = fig.axes[0]
            lines = ax.get_lines()
            self.assertEqual(len(lines), 2)
            # 颜色身份与样式无关：同一索引在所有样式中颜色一致
            self.assertEqual(
                lines[0].get_color(), pm.PUB_CURVE_PALETTE[0]
            )
            self.assertEqual(lines[1].get_color(), pm.PUB_CURVE_PALETTE[1])
            # 基准曲线线宽强调保留
            self.assertGreater(lines[0].get_linewidth(), lines[1].get_linewidth())
            legend = ax.get_legend()
            self.assertIsNotNone(legend)
            labels = [t.get_text() for t in legend.get_texts()]
            self.assertTrue(labels[0].startswith("基准 - "))
            fig.clear()

    def test_single_curve_without_label_has_no_legend(self):
        _fresh_class_state()
        energy = np.linspace(-2.0, 2.0, 100)
        snap = PublicationSnapshot(
            snapshot_id="t1d", source_page_id="p", source_page_title="DOS",
            page_kind="energy_dos", view="1d", view_family="1d",
            captured_at="2026-09-22T10:00:00", cmap_name="magma",
            levels_params=(10.0, 50.0, 90.0), locked_range=None,
            coords={"E": energy}, coord_sources={"E": "file"}, coord_units={"E": "eV"},
            payload={
                "curve": {"x": energy, "y": np.exp(-energy ** 2), "label": None},
                "title": "Energy DOS", "xlabel": "E (eV)", "ylabel": "Intensity (a.u.)",
            },
        )
        options = OutputOptions(width_mm=89.0, dpi=120, fmt="png")
        fig = render_snapshot(snap, STYLE_REGISTRY["1d"]["1d_open"], {}, options, dpi=120)
        self.assertIsNone(fig.axes[0].get_legend())
        fig.clear()

    def test_waterfall_offsets_and_labels_preserved(self):
        _fresh_class_state()
        energy = np.linspace(-2.0, 2.0, 200)
        k_values = np.linspace(-0.5, 0.5, 6)
        curves = np.stack([np.exp(-((energy - kv) ** 2) / 0.1) for kv in k_values])
        snap = PublicationSnapshot(
            snapshot_id="twf", source_page_id="p", source_page_title="瀑布",
            page_kind="waterfall_edc", view="waterfall", view_family="1d",
            captured_at="2026-09-22T10:00:00", cmap_name="magma",
            levels_params=(10.0, 50.0, 90.0), locked_range=None,
            coords={"E": energy}, coord_sources={"E": "file"}, coord_units={"E": "eV"},
            payload={
                "energy_axis": energy, "curves": curves, "k_values": k_values,
                "offset_step": 1.2, "title": "kx EDCs",
                "xlabel": "Intensity (normalized, arb. u.)", "ylabel": "E (eV)",
            },
        )
        options = OutputOptions(width_mm=89.0, dpi=120, fmt="png")
        fig = render_snapshot(snap, STYLE_REGISTRY["1d"]["1d_open"], {}, options, dpi=120)
        ax = fig.axes[0]
        lines = ax.get_lines()
        self.assertEqual(len(lines), 6)
        # 偏移方向与步长保留：第 i 条曲线整体偏移 i*offset_step
        for i, line in enumerate(lines):
            np.testing.assert_allclose(
                line.get_xdata(), curves[i] + i * 1.2, atol=1e-12
            )
        # 动量序列标签保留
        texts = [t.get_text() for t in ax.texts]
        self.assertEqual(len(texts), 6)
        self.assertAlmostEqual(float(texts[0]), -0.5, places=3)
        fig.clear()

    def test_layout_keeps_annotations_inside_canvas(self):
        snap = _make_2d_snapshot()
        snap.payload["title"] = "A" * 60  # 长标题压力测试
        options = OutputOptions(width_mm=89.0, dpi=150, fmt="png")
        for style in STYLE_REGISTRY["2d"].values():
            fig = render_snapshot(snap, style, {}, options, dpi=150)
            fig.canvas.draw()
            renderer = fig.canvas.get_renderer()
            W, H = fig.canvas.get_width_height()
            for ax in fig.axes:
                from bandscope.exporting.publication_renderers import _annotation_artists

                for artist in _annotation_artists(ax):
                    if artist is None or not artist.get_visible():
                        continue
                    extent = artist.get_window_extent(renderer)
                    if extent.width <= 0 or extent.height <= 0:
                        continue
                    self.assertGreaterEqual(extent.x0, -0.5, style.style_id)
                    self.assertGreaterEqual(extent.y0, -0.5, style.style_id)
                    self.assertLessEqual(extent.x1, W + 0.5, style.style_id)
                    self.assertLessEqual(extent.y1, H + 0.5, style.style_id)
            # 标题是画布级文字（fig.text），不在 _measure_overflow 的测量范围
            # 内，必须单独检查是否出血
            for artist in fig.texts:
                if not artist.get_text().strip():
                    continue
                extent = artist.get_window_extent(renderer)
                self.assertGreaterEqual(extent.x0, -0.5, style.style_id)
                self.assertGreaterEqual(extent.y0, -0.5, style.style_id)
                self.assertLessEqual(extent.x1, W + 0.5, style.style_id)
                self.assertLessEqual(extent.y1, H + 0.5, style.style_id)
            fig.clear()


class TitleLayoutTests(unittest.TestCase):
    """标题命名（自动/自定义/清空）与位置、对齐、距离的排版契约。"""

    @staticmethod
    def _title_artists(fig):
        # 标题是画布级文字：_measure_overflow 测不到它，必须单独断言
        return [t for t in fig.texts if t.get_text().strip()]

    def _title_texts(self, fig):
        return [t.get_text() for t in self._title_artists(fig)]

    def _render(self, overrides=None, style_id="2d_open", family="2d",
                snapshot=None, width_mm=89.0):
        snap = _make_2d_snapshot() if snapshot is None else snapshot
        style = pm.resolve_style(family, style_id)
        options = OutputOptions(width_mm=width_mm, dpi=150, fmt="png")
        fig = render_snapshot(snap, style, dict(overrides or {}), options, dpi=150)
        fig.canvas.draw()
        return fig

    def _title_and_axes(self, fig, ax_index=0):
        """(标题外接框, 参照坐标框外接框)，显示像素坐标（y 自下而上）。"""
        renderer = fig.canvas.get_renderer()
        return (
            self._title_artists(fig)[0].get_window_extent(renderer),
            fig.axes[ax_index].get_window_extent(renderer),
        )

    def test_auto_title_used_without_override(self):
        fig = self._render()
        self.assertEqual(self._title_texts(fig), ["X-Integral (40~60)"])
        fig.clear()

    def test_custom_title_replaces_auto_title(self):
        fig = self._render({"title_text": "Fig. 3(a)"})
        self.assertEqual(self._title_texts(fig), ["Fig. 3(a)"])
        fig.clear()

    def test_explicit_empty_title_removes_auto_title(self):
        fig = self._render({"title_text": ""})
        self.assertEqual(self._title_texts(fig), [])
        fig.clear()

    def test_show_title_false_hides_custom_title(self):
        fig = self._render({"title_text": "Fig. 3(a)", "show_title": False})
        self.assertEqual(self._title_texts(fig), [])
        fig.clear()

    def test_default_position_is_above_axes(self):
        fig = self._render({"title_text": "Fig. 3(a)"})
        title, ax = self._title_and_axes(fig)
        self.assertGreater(title.y0, ax.y1)
        fig.clear()

    def test_bottom_position_places_title_below_axes(self):
        fig = self._render({"title_text": "Fig. 3(a)", "title_position": "bottom"})
        title, ax = self._title_and_axes(fig)
        self.assertLess(title.y1, ax.y0)
        fig.clear()

    def test_align_left_and_right_match_axes_edges(self):
        for align, edge in (("left", "x0"), ("right", "x1")):
            fig = self._render({"title_text": "Fig. 3(a)", "title_align": align})
            title, ax = self._title_and_axes(fig)
            self.assertAlmostEqual(getattr(title, edge), getattr(ax, edge), delta=1.5)
            fig.clear()

    def test_default_align_is_centered_on_content(self):
        fig = self._render({"title_text": "Fig. 3(a)"})
        title, ax = self._title_and_axes(fig)
        self.assertAlmostEqual(
            (title.x0 + title.x1) / 2.0, (ax.x0 + ax.x1) / 2.0, delta=1.5
        )
        fig.clear()

    def test_gap_moves_title_and_sets_distance_to_axes(self):
        # 距离既决定标题到坐标框的净间距，也真的把标题往画布边推
        extents = {}
        for gap in (1.0, 6.0):
            fig = self._render({"title_text": "Fig. 3(a)", "title_gap_mm": gap})
            title, ax = self._title_and_axes(fig)
            extents[gap] = (title.y0, (title.y0 - ax.y1) / 150.0 * 25.4)
            fig.clear()
        self.assertGreater(extents[6.0][0], extents[1.0][0])
        for gap, (_y0, distance) in extents.items():
            # 实测含刻度标注凸出坐标框的部分（保守量），故为下界
            self.assertGreaterEqual(distance + 1e-6, gap)
            self.assertLessEqual(distance, gap + 1.2, gap)

    def test_title_stays_above_top_colorbar(self):
        fig = self._render({"title_text": "Fig. 3(a)"}, style_id="2d_topbar")
        title, _ = self._title_and_axes(fig)
        _, cbar = self._title_and_axes(fig, ax_index=1)
        self.assertGreaterEqual(title.y0, cbar.y1 - 0.5)
        fig.clear()

    def test_long_custom_title_stays_inside_canvas(self):
        for style in STYLE_REGISTRY["2d"].values():
            fig = self._render({"title_text": "Very long panel title " * 8}, style_id=style.style_id)
            title = self._title_artists(fig)[0]
            extent = title.get_window_extent(fig.canvas.get_renderer())
            W, H = fig.canvas.get_width_height()
            self.assertGreaterEqual(extent.x0, -0.5, style.style_id)
            self.assertGreaterEqual(extent.y0, -0.5, style.style_id)
            self.assertLessEqual(extent.x1, W + 0.5, style.style_id)
            self.assertLessEqual(extent.y1, H + 0.5, style.style_id)
            fig.clear()

    def test_single_column_and_double_column_widths(self):
        for width_mm in (89.0, 183.0):
            fig = self._render({"title_text": "Fig. 3(a)"}, width_mm=width_mm)
            title = self._title_artists(fig)[0]
            extent = title.get_window_extent(fig.canvas.get_renderer())
            W, H = fig.canvas.get_width_height()
            self.assertGreaterEqual(extent.x0, -0.5, width_mm)
            self.assertLessEqual(extent.x1, W + 0.5, width_mm)
            self.assertLessEqual(extent.y1, H + 0.5, width_mm)
            fig.clear()

    def test_1d_family_honours_title_overrides(self):
        snap = _make_1d_comparison_snapshot()
        fig = self._render({"title_text": "Fig. 4", "title_position": "bottom"},
                           style_id=DEFAULT_STYLE_ID["1d"], family="1d", snapshot=snap)
        self.assertEqual(self._title_texts(fig), ["Fig. 4"])
        title, ax = self._title_and_axes(fig)
        self.assertLess(title.y1, ax.y0)
        fig.clear()

    def test_3d_layout_reserves_band_and_places_title(self):
        # 3D 渲染需要离屏 VTK，此处直接验证 3D 与 1D/2D 共用的排版助手
        from bandscope.exporting.publication_renderers import (
            _colorbar_layout_for,
            _compute_rects,
            _fit_layout_3d,
            _initial_margins,
            _new_figure,
            place_title,
            plan_title,
        )

        snap = _make_2d_snapshot()
        style = pm.resolve_style("3d", DEFAULT_STYLE_ID["3d"])
        params = style.params
        overrides = {"title_text": "Fig. 5", "title_position": "bottom",
                     "title_align": "right", "title_gap_mm": 8.0}
        dpi = 150
        width_mm = 89.0
        height_mm = OutputOptions(width_mm=width_mm, dpi=dpi, fmt="png").resolved_height_mm("3d")

        fig = _new_figure(width_mm, height_mm, dpi)
        ax_img = fig.add_axes([0.12, 0.12, 0.7, 0.7])
        ax_img.set_axis_off()
        cbar_layout = _colorbar_layout_for(params, overrides, "3d")
        margins = _initial_margins(params)
        base_bottom = margins["bottom"]
        plan = plan_title(snap, overrides, params, width_mm)
        overflow = _fit_layout_3d(
            fig, ax_img, None, width_mm, height_mm, margins, cbar_layout,
            title_plan=plan,
        )
        # 距离 + 标题带被叠加进底边距，注释之外才排得下标题
        self.assertGreater(margins["bottom"], base_bottom)
        place_title(fig, plan, width_mm, height_mm, margins, cbar_layout, overflow)
        fig.canvas.draw()
        title = [t for t in fig.texts if t.get_text().strip()][0]
        extent = title.get_window_extent(fig.canvas.get_renderer())
        main, _cbar = _compute_rects(width_mm, height_mm, margins, cbar_layout)
        W, H = fig.canvas.get_width_height()
        self.assertGreaterEqual(extent.y0, -0.5)
        self.assertLessEqual(extent.y1, H + 0.5)
        self.assertLessEqual(extent.y1, main[1] / height_mm * H + 0.5)
        self.assertAlmostEqual(extent.x1, main[2] / width_mm * W, delta=1.5)
        fig.clear()


class AnalyzerProvenanceTests(unittest.TestCase):
    def test_load_npz_records_coord_provenance(self):
        from bandscope.core.analyzer_core import AnalyzerCore

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sample.npz")
            rng = np.random.default_rng(0)
            np.savez(
                path,
                sample=rng.random((10, 12, 14)).astype(np.float32),
                axis_order=np.array(["X", "Y", "E"]),
                axis_mapping_version=np.array(["v1"]),
                kx=np.linspace(-1, 1, 10),
                ky=np.linspace(-1, 1, 12),
                # 缺 E 坐标：应回退索引且来源标记为 index
                kx_unit=np.array(["Å⁻¹"]),
            )
            core = AnalyzerCore()
            ok, _shape = core.load_npz(path)
            self.assertTrue(ok)
            self.assertEqual(core.coord_sources.get("X"), "file")
            self.assertEqual(core.coord_sources.get("Y"), "file")
            self.assertEqual(core.coord_sources.get("E"), "index")
            self.assertEqual(core.coord_units.get("X"), "Å⁻¹")
            self.assertIsNone(core.coord_units.get("E"))
            # 标签：缺 E 坐标不可输出虚假 eV
            self.assertEqual(axis_label("E", core.coord_sources["E"], None), "E (index)")


class NonUniformGridTests(unittest.TestCase):
    def test_non_uniform_axis_rejected(self):
        from bandscope.exporting.publication_export import _check_uniform

        uniform = np.linspace(0.0, 1.0, 50)
        _check_uniform(uniform, "X")  # 不抛异常
        non_uniform = np.asarray([0.0, 0.1, 0.15, 0.4, 0.9, 1.0])
        with self.assertRaises(Exception):
            _check_uniform(non_uniform, "X")


if __name__ == "__main__":
    unittest.main()
