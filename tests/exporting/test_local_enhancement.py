import unittest
from types import SimpleNamespace

import numpy as np

from bandscope.exporting.publication_export import _freeze_enhancement
from bandscope.exporting.publication_models import OutputOptions, resolve_style
from bandscope.exporting.publication_renderers import render_snapshot
from bandscope.processing.local_enhancement import orient_2d, resolve_display
from bandscope.rendering.local_enhancement import display_rgba
from tests.support.enhancement import context_2d, state_2d
from tests.support.publication import _make_2d_snapshot


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
        self.assertEqual(len(ax.patches), 2)
        self.assertTrue(any("Local normalization" in text.get_text() for text in ax.texts))
        fig.clear()
