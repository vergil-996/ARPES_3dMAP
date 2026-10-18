import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from bandscope.exporting.publication_export import _freeze_enhancement
from bandscope.exporting.publication_models import (
    OutputOptions,
    PublicationSnapshot,
    resolve_style,
)
from bandscope.exporting.publication_renderers import compute_level_info, render_snapshot
from bandscope.processing.local_enhancement import orient_2d, resolve_display
from bandscope.rendering.local_enhancement import display_rgba
from bandscope.rendering.render_core import VisualEngine
from tests.support.enhancement import context_2d, state_2d
from tests.support.environment import requires_opengl
from tests.support.publication import _make_2d_snapshot


def _make_3d_enhancement_snapshot():
    """带两个局部增强区域的最小 3D 快照。"""
    VisualEngine._last_data_range = None
    VisualEngine._locked_data_range = None
    shape = (6, 6, 8)
    volume = np.linspace(0.0, 1.0, int(np.prod(shape)), dtype=np.float32).reshape(shape)
    level_info = compute_level_info(volume, (10.0, 50.0, 90.0))
    enhancement = {
        "color": volume.copy(),
        "alpha": np.full(shape, 0.7, dtype=np.float32),
        "background_level_info": level_info,
        "common": False,
        "active": True,
        "revision": 1,
        "color_rules": None,
        "regions": [
            {"region_id": "1", "label": "R1", "name": "R1",
             "render_bounds": (0, 2, 0, 2, 0, 3)},
            {"region_id": "2", "label": "R2", "name": "R2",
             "render_bounds": (3, 5, 3, 5, 4, 7)},
        ],
    }
    return PublicationSnapshot(
        snapshot_id="test3d-enhancement",
        source_page_id="p1",
        source_page_title="3D",
        page_kind="home",
        view="3d",
        view_family="3d",
        captured_at="2026-10-17T10:00:00",
        cmap_name="magma",
        levels_params=(10.0, 50.0, 90.0),
        locked_range=None,
        coords={key: np.linspace(-1.0, 1.0, n) for key, n in zip(("X", "Y", "E"), shape)},
        coord_sources={"X": "file", "Y": "file", "E": "file", "delay": "index"},
        coord_units={"X": "Å⁻¹", "Y": "Å⁻¹", "E": "eV", "delay": None},
        camera={
            "position": (20.0, -30.0, 20.0),
            "focal_point": (0.0, 0.0, 0.0),
            "view_up": (0.0, 0.0, 1.0),
            "parallel_projection": True,
            "parallel_scale": 120.0,
        },
        payload={
            "volume": volume,
            "data_bounds": (0, shape[0] - 1, 0, shape[1] - 1, 0, shape[2] - 1),
            "full_shape": shape,
            "spacing": tuple(200.0 / (n - 1) for n in shape),
            "clip_render_bounds": None,
            "include_zero": False,
            "opacity_mode": "linear",
            "opacity_multiplier": None,
            "show_axes": False,
            "viewport_aspect": 1.0,
            "level_info": level_info,
            "intensity_label": "Background intensity (a.u.)",
            "axis_titles": ["kx", "ky", "E"],
            "enhancement": enhancement,
        },
    )


class EnhancementPublicationTests(unittest.TestCase):
    def test_frozen_channels_and_ranges_survive_live_changes(self):
        context, state = context_2d(axis=0), state_2d()
        context["coords"]["E"] = np.arange(5, -1, -1, dtype=float)
        for region in state["regions"]:
            region["axes"] = ("Y", "E")
        fields = resolve_display(context, state)
        window = SimpleNamespace(_enhancement_display=lambda *_args: fields)
        snapshot = _make_2d_snapshot()
        snapshot.payload.update(image=context["data"].T.copy(), extent=[0, 5, 0, 5])
        render_context = dict(context, slice_info=dict(context["slice_info"], display_e_flip=True))
        _freeze_enhancement(window, snapshot, SimpleNamespace(), context, render_context)
        frozen = snapshot.payload["enhancement"]
        expected = orient_2d(fields["color"], render_context["slice_info"], (0, 5, 5, 0)).copy()
        np.testing.assert_array_equal(frozen["color"], expected)
        fields["color"][:] = 0
        state["regions"][0]["bounds"] = (100, 101, 0, 1)
        np.testing.assert_array_equal(frozen["color"], expected)
        self.assertEqual(frozen["regions"][0]["bounds"], (0, 1, 0, 5))
        np.testing.assert_array_equal(snapshot.payload["image"], context["data"].T)

    def test_publication_image_uses_exactly_the_same_color_and_alpha(self):
        context, state = context_2d(), state_2d()
        fields = resolve_display(context, state)
        snapshot = _make_2d_snapshot()
        snapshot.payload.update(image=context["data"].T.copy(), extent=[0, 5, 0, 5])
        window = SimpleNamespace(_enhancement_display=lambda *_args: fields)
        _freeze_enhancement(window, snapshot, SimpleNamespace(), context, context)
        fig = render_snapshot(snapshot, resolve_style("2d", ""), {}, OutputOptions(width_mm=100, dpi=100), dpi=100)
        ax = next(ax for ax in fig.axes if len(ax.images))
        self.assertEqual(len(ax.images), 3)
        np.testing.assert_array_equal(ax.images[0].get_array(), snapshot.payload["image"])
        for image, layer in zip(ax.images[1:], snapshot.payload["enhancement"]["layers"]):
            np.testing.assert_array_equal(image.get_array(), layer["image"])
            self.assertEqual(tuple(image.get_extent()), tuple(layer["extent"]))
        fig.clear()

    def test_publication_image_omits_enhancement_annotations(self):
        """截图只保留增强后的着色；区域框、编号/图例和区域色带都不进图片。"""
        context, state = context_2d(), state_2d()
        fields = resolve_display(context, state)
        snapshot = _make_2d_snapshot()
        snapshot.payload.update(image=context["data"].T.copy(), extent=[0, 5, 0, 5])
        window = SimpleNamespace(_enhancement_display=lambda *_args: fields)
        _freeze_enhancement(window, snapshot, SimpleNamespace(), context, context)
        fig = render_snapshot(snapshot, resolve_style("2d", ""), {}, OutputOptions(width_mm=100, dpi=100), dpi=100)
        ax = next(ax for ax in fig.axes if len(ax.images))
        self.assertEqual(len(ax.images), 3)  # 增强图层仍在
        self.assertEqual(len(ax.patches), 0)  # 没有区域裁剪框
        texts = [text.get_text() for text in ax.texts]
        self.assertFalse(any("Local normalization" in text for text in texts))
        self.assertFalse(any(text.startswith("R") for text in texts))
        # 区域色带会作为独立 axes 带 R 标签；主强度色条不受影响。
        self.assertFalse(any(axis.get_xlabel().startswith("R") for axis in fig.axes))
        fig.clear()

    @requires_opengl
    def test_publication_3d_omits_enhancement_boxes_and_labels(self):
        """3D 截图不画增强区域边框，也不放区域编号。"""
        import pyvista as pv

        snapshot = _make_3d_enhancement_snapshot()
        calls = []
        add_mesh = pv.Plotter.add_mesh
        add_point_labels = pv.Plotter.add_point_labels
        add_scalar_bar = pv.Plotter.add_scalar_bar

        def record_mesh(plotter, *args, **kwargs):
            calls.append("mesh")
            return add_mesh(plotter, *args, **kwargs)

        def record_labels(plotter, *args, **kwargs):
            calls.append("labels")
            return add_point_labels(plotter, *args, **kwargs)

        def record_scalar_bar(plotter, *args, **kwargs):
            calls.append("scalar_bar")
            return add_scalar_bar(plotter, *args, **kwargs)

        with patch.object(pv.Plotter, "add_mesh", record_mesh), \
                patch.object(pv.Plotter, "add_point_labels", record_labels), \
                patch.object(pv.Plotter, "add_scalar_bar", record_scalar_bar):
            fig = render_snapshot(
                snapshot, resolve_style("3d", None), {},
                OutputOptions(width_mm=60, height_mm=60, dpi=60), dpi=60,
            )
        self.assertEqual(calls, [])
        fig.clear()
