import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bandscope.core.view_recipe import ViewRecipeDocument, sidecar_path
from bandscope.exporting.publication_models import OutputOptions, resolve_style
from bandscope.exporting.recipe_export import export_pair
from tests.support.publication import _make_2d_snapshot
from tests.support.view_recipe import recipe_document, synthetic_npz


class RecipePairExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "figure.png"
        source = self.path.with_suffix(".npz")
        synthetic_npz(source)
        _, doc = recipe_document(source)
        self.snapshot = _make_2d_snapshot()
        self.snapshot.view_recipe = doc
        self.style = resolve_style("2d", "2d_boxed")
        self.options = OutputOptions()

    @staticmethod
    def render(snapshot, style, overrides, options, path, **kw):
        Path(path).write_bytes(b"new picture")

    def test_pair_contains_exported_draft_and_does_not_mutate_frozen_recipe(self):
        frozen = copy.deepcopy(self.snapshot.view_recipe)
        export_pair(self.snapshot, self.style, {"title_text": "new title"}, self.options,
                    str(self.path), render=self.render)
        d = ViewRecipeDocument.load(sidecar_path(self.path)).data
        self.assertEqual(self.path.read_bytes(), b"new picture")
        self.assertEqual(d["publication"]["overrides"]["title_text"], "new title")
        self.assertEqual(self.snapshot.view_recipe, frozen)

    def test_second_replace_failure_rolls_back_both_existing_files(self):
        companion = Path(sidecar_path(self.path))
        self.path.write_bytes(b"old picture")
        companion.write_bytes(b"old json")
        replace = os.replace
        def fail_new_json(src, dst):
            if Path(dst).resolve() == companion.resolve() and Path(src).name.startswith(".bandscope-") and not Path(src).name.startswith(".bandscope-backup-"):
                raise PermissionError("locked sidecar")
            return replace(src, dst)
        with patch("bandscope.exporting.recipe_export.os.replace", side_effect=fail_new_json):
            with self.assertRaises(PermissionError):
                export_pair(self.snapshot, self.style, {}, self.options, str(self.path), render=self.render)
        self.assertEqual(self.path.read_bytes(), b"old picture")
        self.assertEqual(companion.read_bytes(), b"old json")

    def test_render_failure_creates_neither_product(self):
        def fail(*a, **kw):
            raise ValueError("render failed")
        with self.assertRaises(ValueError):
            export_pair(self.snapshot, self.style, {}, self.options, str(self.path), render=fail)
        self.assertFalse(self.path.exists())
        self.assertFalse(Path(sidecar_path(self.path)).exists())


if __name__ == "__main__":
    unittest.main()
