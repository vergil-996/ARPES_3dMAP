import copy
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.view_recipe import RecipeError, ViewRecipeDocument, plain
from tests.support.view_recipe import recipe_document, synthetic_npz


class ViewRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "实验.npz"
        synthetic_npz(self.path)
        self.core, self.doc = recipe_document(self.path)

    def test_json_roundtrip_omits_source_paths_even_for_legacy_documents(self):
        path = self.path.parent / "view.bsview.json"
        path.write_text(ViewRecipeDocument(self.doc).dumps(), encoding="utf-8")
        content = path.read_text(encoding="utf-8")
        loaded = ViewRecipeDocument.load(path).data
        self.assertNotIn("path", loaded["sources"][loaded["primary_source_id"]])
        self.assertNotIn(str(self.path), content)
        self.assertIn("原始视图", content)

    def test_wrong_schema_duplicate_ids_and_changed_params_rejected(self):
        variants = []
        d = copy.deepcopy(self.doc); d["schema_version"] = 99; variants.append(d)
        d = copy.deepcopy(self.doc); d["pages"].append(d["pages"][0]); variants.append(d)
        d = copy.deepcopy(self.doc); d["pages"][0]["params"]["clip_ranges"] = [0]*6; variants.append(d)
        d = copy.deepcopy(self.doc); d["environment"]["denoise_methods"] = ["new"]; variants.append(d)
        for variant in variants:
            with self.subTest(variant=variant.get("schema_version")), self.assertRaises(RecipeError):
                ViewRecipeDocument(variant).validate()

    def test_duplicate_json_keys_and_nonstandard_nan_rejected(self):
        path = self.path.parent / "bad.json"
        for text in ('{"format":"a","format":"b"}', '{"x":NaN}'):
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(RecipeError):
                ViewRecipeDocument.load(path)
        self.assertEqual(plain(np.array([1.0, np.nan])), [1.0, None])
        with self.assertRaises(RecipeError):
            plain(float("inf"))

    def test_identity_is_bound_to_load_not_later_file_contents(self):
        identity = self.core.source_identity
        identity.future.result()
        synthetic_npz(self.path, seed=22)
        with self.assertRaises(RecipeError):
            identity.descriptor(self.core.source_layout)

    def test_layout_replay_and_failed_load_preserve_existing_data(self):
        core = AnalyzerCore()
        ok, _ = core.load_npz(str(self.path), layout=self.core.source_layout)
        self.assertTrue(ok)
        np.testing.assert_array_equal(core.raw_data, self.core.raw_data)
        before = core.raw_data
        ok, _ = core.load_npz(str(self.path.parent / "missing.npz"))
        self.assertFalse(ok)
        self.assertIs(core.raw_data, before)


if __name__ == "__main__":
    unittest.main()
