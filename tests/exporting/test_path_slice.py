import unittest
from types import SimpleNamespace

import numpy as np

from bandscope.core.path_slice import PathNode, PathSegmentStyle, PathSliceModel
from bandscope.exporting.publication_export import _freeze_path_slice
from bandscope.exporting.publication_models import OutputOptions, PublicationSnapshot, resolve_style
from bandscope.exporting.publication_renderers import render_snapshot
from bandscope.processing.path_slice import MomentumCalibration, PathSliceSampler


class PathSlicePublicationTests(unittest.TestCase):
    def setUp(self):
        self.model = PathSliceModel(
            [PathNode(0, 1, "Γ"), PathNode(2, 1, "K"), PathNode(2, 3, "M")],
            [PathSegmentStyle(), PathSegmentStyle()], viewport=[0, 1],
        )
        volume = np.arange(4 * 5 * 3, dtype=float).reshape(4, 5, 3)
        self.sample = PathSliceSampler().sample(
            volume, np.array([-1.0, 0.0, 1.0]), self.model,
            MomentumCalibration.create(np.arange(4), np.arange(5)),
        )
        self.snapshot = PublicationSnapshot(
            snapshot_id="path", source_page_id="path", source_page_title="路径",
            page_kind="path_slice", view="path_slice", view_family="2d",
            captured_at="2026-11-20T00:00:00", cmap_name="magma",
            levels_params=(0, 50, 100), locked_range=None,
        )

    def freeze(self):
        context = {
            "path_sample": self.sample, "path_model": self.model,
            "title": "路径切片 Γ–K–M", "display_e_flip": False,
        }
        window = SimpleNamespace(_axis_label_text=lambda _key: "E (eV)")
        _freeze_path_slice(window, self.snapshot, context)

    def test_publication_uses_full_path_not_scrolled_viewport(self):
        self.freeze()
        self.assertEqual(self.snapshot.payload["extent"][:2], [0.0, 4.0])
        figure = render_snapshot(
            self.snapshot, resolve_style("2d", "2d_boxed"), {},
            OutputOptions(dpi=120), dpi=120,
        )
        ax = figure.axes[0]
        self.assertEqual(ax.get_xlim(), (0.0, 4.0))
        self.assertEqual(len(ax.lines), 3)
        self.assertEqual([text.get_text() for text in ax.texts], ["Γ", "K", "M"])
        self.assertEqual(len(ax.get_xticks()), 0)
        figure.clear()

    def test_publication_preserves_independent_annotation_flags(self):
        self.model.show_guides = False
        self.model.show_labels = True
        self.freeze()
        figure = render_snapshot(
            self.snapshot, resolve_style("2d", "2d_boxed"), {},
            OutputOptions(dpi=120), dpi=120,
        )
        ax = figure.axes[0]
        self.assertEqual(len(ax.lines), 0)
        self.assertEqual(len(ax.texts), 3)
        figure.clear()


if __name__ == "__main__":
    unittest.main()
