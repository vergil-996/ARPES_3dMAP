import copy
import json
import tempfile
import unittest
from pathlib import Path

from bandscope.core.blender_recipe import (
    BLENDER_RECIPE_FORMAT,
    BlenderRecipeDocument,
    blender_recipe_digest,
    blender_sidecar_path,
    build_blender_recipe,
    updated_blender_recipe,
)
from bandscope.core.view_recipe import RecipeError, ViewRecipeDocument
from bandscope.exporting.blender_project import BlenderProjectRequest, request_from_recipe_settings
from bandscope.exporting.blender_project import update_snapshot_display
from tests.support.blender import make_blender_snapshot
from tests.support.view_recipe import recipe_document, synthetic_npz


class BlenderRecipeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.npz = Path(self.temp.name) / "input.npz"
        synthetic_npz(self.npz)
        _core, document = recipe_document(self.npz)
        self.recipe = ViewRecipeDocument(document).validate().data

    def snapshot(self, session_id, page_id="page-3d"):
        snapshot = make_blender_snapshot()
        snapshot.source_session_id = session_id
        snapshot.source_page_id = page_id
        snapshot.view_recipe = copy.deepcopy(self.recipe)
        return snapshot

    def test_roundtrip_groups_order_and_export_settings_without_paths(self):
        first = self.snapshot("session-a")
        second = self.snapshot("session-b")
        third = self.snapshot("session-a", "page-other")
        request = BlenderProjectRequest(
            [first, second, third], {"coordinates": False}, volume_mode="hybrid_lit",
        )
        document = build_blender_recipe(request)
        self.assertEqual(document["format"], BLENDER_RECIPE_FORMAT)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual([group["entry_ids"] for group in document["groups"]], [
            ["entry-001", "entry-003"], ["entry-002"],
        ])
        self.assertEqual([entry["group_id"] for entry in document["entries"]], [
            "source-001", "source-002", "source-001",
        ])
        self.assertEqual(document["export_settings"], request.recipe_settings())
        self.assertFalse(document["export_settings"]["presets"]["coordinates"])
        path = Path(blender_sidecar_path(Path(self.temp.name) / "demo.blend"))
        path.write_text(BlenderRecipeDocument(document).dumps(), encoding="utf-8")
        restored = BlenderRecipeDocument.load(path).data
        self.assertEqual(restored, document)
        self.assertNotIn(str(self.npz), json.dumps(restored, ensure_ascii=False))
        request_from_recipe_settings(restored["export_settings"], [first])

    def test_same_file_independent_sessions_stay_independent(self):
        request = BlenderProjectRequest([self.snapshot("one"), self.snapshot("two")])
        document = build_blender_recipe(request)
        self.assertEqual(len(document["groups"]), 2)
        self.assertEqual(
            document["entries"][0]["recipe"]["primary_source_id"],
            document["entries"][1]["recipe"]["primary_source_id"],
        )

    def test_missing_page_recipe_names_the_page(self):
        snapshot = self.snapshot("one")
        snapshot.view_recipe = None
        snapshot.recipe_error = "source identity unavailable"
        with self.assertRaisesRegex(RecipeError, "非对称 3D.*source identity"):
            build_blender_recipe(BlenderProjectRequest([snapshot]))

    def test_tampering_and_source_paths_are_rejected(self):
        document = build_blender_recipe(BlenderProjectRequest([self.snapshot("one")]))
        changed = copy.deepcopy(document)
        source = next(iter(changed["entries"][0]["recipe"]["sources"].values()))
        source["path"] = str(self.npz)
        changed["state_digest"] = blender_recipe_digest(changed)
        with self.assertRaisesRegex(RecipeError, "不得保存原始数据路径"):
            BlenderRecipeDocument(changed).validate()
        changed = copy.deepcopy(document)
        changed["export_settings"]["presets"]["coordinates"] = False
        with self.assertRaisesRegex(RecipeError, "已被编辑"):
            BlenderRecipeDocument(changed).validate()

    def test_update_preserves_science_and_records_removed_effects(self):
        snapshot = self.snapshot("one")
        document = build_blender_recipe(BlenderProjectRequest([snapshot]))
        original_recipe = copy.deepcopy(document["entries"][0]["recipe"])
        snapshot.payload["opacity_multiplier"] = [1.0] * snapshot.payload["volume"].shape[2]
        snapshot.payload["enhancement"] = {"active": True}
        update_snapshot_display(
            snapshot, cmap_name="viridis", levels_params=(5.0, 60.0, 90.0),
            locked_range=None, include_zero=False, opacity_mode="linear",
        )
        updated = updated_blender_recipe(document, {"entry-001": snapshot})
        self.assertEqual(updated["entries"][0]["recipe"], original_recipe)
        override = updated["entries"][0]["display_override"]
        self.assertEqual(override["cmap"], "viridis")
        self.assertEqual(override["removed_effects"], ["local_enhancement", "opacity_multiplier"])
        self.assertIsNone(override["opacity_multiplier"])
        BlenderRecipeDocument(updated).validate()


if __name__ == "__main__":
    unittest.main()
