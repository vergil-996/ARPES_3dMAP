import copy
import tempfile
import unittest
from pathlib import Path

from bandscope.core.display_style import new_display_style, new_render_quality
from bandscope.core.local_enhancement import RENDER_QUALITY_KEY, STATE_KEY, create_display_state
from bandscope.core.view_recipe import (
    RecipeError,
    ViewRecipeDocument,
    digest,
    state_digest,
)
from tests.support.view_recipe import recipe_document, synthetic_npz


class ViewRecipeDisplaySchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "input.npz"
        synthetic_npz(path)
        _core, self.document = recipe_document(path)

    def test_schema_1_is_validated_then_migrated_to_legacy_schema_2(self):
        migrated = ViewRecipeDocument(self.document).validate().data
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["pages"][0]["params"][STATE_KEY]["background"]["display_mode"], "legacy")
        self.assertEqual(migrated["pages"][0]["params"][RENDER_QUALITY_KEY]["mode"], "legacy")
        self.assertTrue(all(node["operation_version"] == 1 for node in migrated["recipes"].values()))
        self.assertEqual(migrated["state_digest"], state_digest(migrated))
        tampered = copy.deepcopy(self.document)
        tampered["pages"][0]["params"][STATE_KEY] = create_display_state()
        with self.assertRaises(RecipeError):
            ViewRecipeDocument(tampered).validate()

    def test_schema_2_independent_display_roundtrip_is_digest_protected(self):
        document = ViewRecipeDocument(self.document).validate().data
        page = document["pages"][0]
        page["params"][STATE_KEY] = create_display_state(new_display_style())
        page["params"][RENDER_QUALITY_KEY] = new_render_quality()
        old_key = page["recipe_id"]
        node = document["recipes"].pop(old_key)
        node["params"] = copy.deepcopy(page["params"])
        new_key = digest(node)
        document["recipes"][new_key] = node
        page["recipe_id"] = new_key
        document["state_digest"] = state_digest(document)
        path = Path(self.temp.name) / "display.bsview.json"
        path.write_text(ViewRecipeDocument(document).dumps(), encoding="utf-8")
        restored = ViewRecipeDocument.load(path).data
        style = restored["pages"][0]["params"][STATE_KEY]["background"]
        self.assertEqual(style["display_mode"], "independent")
        self.assertEqual(style["opacity"]["density"], 12.0)
        self.assertEqual(restored["pages"][0]["params"][RENDER_QUALITY_KEY]["interpolation"], "linear")
        restored["pages"][0]["params"][STATE_KEY]["background"]["opacity"]["density"] = 90.0
        with self.assertRaises(RecipeError):
            ViewRecipeDocument(restored).validate()


if __name__ == "__main__":
    unittest.main()
