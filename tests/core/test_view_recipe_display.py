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

    def test_schema_1_is_validated_then_migrated_to_current_schema(self):
        migrated = ViewRecipeDocument(self.document).validate().data
        self.assertEqual(migrated["schema_version"], 4)
        self.assertEqual(migrated["pages"][0]["params"][STATE_KEY]["background"]["display_mode"], "legacy")
        self.assertEqual(migrated["pages"][0]["params"][STATE_KEY]["background"]["colormap_range"], (0.0, 1.0))
        self.assertEqual(migrated["pages"][0]["params"][RENDER_QUALITY_KEY]["mode"], "legacy")
        self.assertTrue(all(node["operation_version"] == 1 for node in migrated["recipes"].values()))
        self.assertEqual(migrated["state_digest"], state_digest(migrated))
        tampered = copy.deepcopy(self.document)
        tampered["pages"][0]["params"][STATE_KEY] = create_display_state()
        with self.assertRaises(RecipeError):
            ViewRecipeDocument(tampered).validate()

    def test_schema_3_restricted_ranges_are_validated_before_full_range_migration(self):
        document = ViewRecipeDocument(self.document).validate().data
        style = new_display_style()
        style["normalization"] = {
            "mode": "manual", "percentiles": (2.0, 98.0), "manual_range": (-3.0, 7.0),
        }
        state = create_display_state(style)
        state["regions"] = [{
            "region_id": "region", "style": copy.deepcopy(style), "locked_range": (-3.0, 7.0),
        }]
        page = document["pages"][0]
        page["params"][STATE_KEY] = state
        old_key = page["recipe_id"]
        node = document["recipes"].pop(old_key)
        node["params"] = copy.deepcopy(page["params"])
        new_key = digest(node)
        document["recipes"][new_key] = node
        page["recipe_id"] = new_key
        document["view_state"].update(
            locked_range=(-3.0, 7.0),
            level_info={"data_min": -3.0, "data_max": 7.0},
            display={"background_style": copy.deepcopy(style), "resolved_range": (-3.0, 7.0)},
        )
        document["schema_version"] = 3
        document["state_digest"] = state_digest(document)

        tampered = copy.deepcopy(document)
        tampered["view_state"]["locked_range"] = (8.0, 9.0)
        with self.assertRaises(RecipeError):
            ViewRecipeDocument(tampered).validate()

        migrated = ViewRecipeDocument(document).validate().data
        self.assertEqual(migrated["schema_version"], 4)
        restored_state = migrated["pages"][0]["params"][STATE_KEY]
        self.assertEqual(restored_state["background"]["normalization"], {"mode": "full"})
        self.assertNotIn("locked_range", restored_state["regions"][0])
        self.assertTrue(restored_state["regions"][0]["lock_pending"])
        self.assertIsNone(migrated["view_state"]["locked_range"])
        self.assertIsNone(migrated["view_state"]["display"]["resolved_range"])
        self.assertTrue(migrated["view_state"]["relock_full_range"])
        self.assertNotIn("level_info", migrated["view_state"])
        self.assertEqual(migrated["state_digest"], state_digest(migrated))

    def test_current_schema_independent_display_roundtrip_is_digest_protected(self):
        document = ViewRecipeDocument(self.document).validate().data
        page = document["pages"][0]
        page["params"][STATE_KEY] = create_display_state(
            new_display_style(colormap_range=(0.2, 0.8)),
        )
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
        self.assertEqual(tuple(style["colormap_range"]), (0.2, 0.8))
        self.assertEqual(restored["pages"][0]["params"][RENDER_QUALITY_KEY]["interpolation"], "linear")
        restored["pages"][0]["params"][STATE_KEY]["background"]["opacity"]["density"] = 90.0
        with self.assertRaises(RecipeError):
            ViewRecipeDocument(restored).validate()


if __name__ == "__main__":
    unittest.main()
