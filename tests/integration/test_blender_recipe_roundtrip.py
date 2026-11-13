import copy
import tempfile
import unittest
from pathlib import Path

from PyQt5.QtWidgets import QApplication

from bandscope.core.blender_recipe import BlenderRecipeDocument, build_blender_recipe
from bandscope.core.display_style import new_render_quality
from bandscope.core.view_recipe import RecipeError
from bandscope.exporting.blender_project import BlenderProjectRequest
from bandscope.exporting.publication_export import capture_snapshot
from bandscope.ui.result_workspace import AnalysisPageSpec
from tests.support.view_recipe import control_window, synthetic_npz


class BlenderRecipeRoundtripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.first = self.root / "first.npz"
        self.second = self.root / "second.npz"
        synthetic_npz(self.first, seed=1, descending=True)
        synthetic_npz(self.second, seed=2, descending=True)
        self.window = control_window()
        self.addCleanup(self.window.close)

    def write_recipe(self, snapshots, name="demo", *, volume_mode="emission"):
        request = BlenderProjectRequest(
            snapshots,
            {"coordinates": True, "guide_frame": False, "orientation": True,
             "thin_boxes": False, "text_presets": True},
            volume_mode=volume_mode,
        )
        document = build_blender_recipe(request)
        path = self.root / f"{name}.blend.bsview.json"
        path.write_text(BlenderRecipeDocument(document).dumps(), encoding="utf-8")
        return path, document

    def same_source_snapshots(self):
        self.window.load_data(str(self.first))
        self.window.timeline_bar.set_rotation_angle(0)
        home = capture_snapshot(self.window)
        source = self.window.left_workspace.home_spec()
        page = AnalysisPageSpec(
            self.window._make_page_id(), "不同旋转的积分", "time_integral", "test",
            source_page_id=source.page_id,
            params={"t_low": 0, "t_up": 2,
                    "control_state": copy.deepcopy(source.params["control_state"])},
        )
        self.window.left_workspace.add_page(page)
        self.window.timeline_bar.set_rotation_angle(23)
        quality = new_render_quality()
        quality["interpolation"] = "nearest"
        quality["resting"] = "fine"
        page.params["render_quality"] = copy.deepcopy(quality)
        derived = capture_snapshot(self.window)
        return [home, derived]

    def test_same_source_multi_page_rotation_quality_and_export_draft(self):
        snapshots = self.same_source_snapshots()
        path, document = self.write_recipe(snapshots, volume_mode="hybrid_lit")
        by_page = {page["page_id"]: page for page in snapshots[1].view_recipe["pages"]}
        home_node = snapshots[1].view_recipe["recipes"][by_page[snapshots[0].source_page_id]["recipe_id"]]
        target_node = snapshots[1].view_recipe["recipes"][by_page[snapshots[1].source_page_id]["recipe_id"]]
        self.assertEqual(home_node["environment"]["rotation_angle"], 0)
        self.assertEqual(target_node["environment"]["rotation_angle"], 23)
        other = control_window()
        self.addCleanup(other.close)
        selections = []
        self.assertTrue(other._load_blender_recipe(
            path, confirm=False,
            relocate=lambda source: selections.append(source["sha256"]) or str(self.first),
        ))
        self.assertEqual(len(selections), 1)
        self.assertEqual(len(other.source_manager), 1)
        self.assertEqual(len(other.left_workspace.page_specs), 2)
        self.assertEqual(other.active_page_spec.page_kind, "home")
        pages = {spec.title: spec for spec in other.left_workspace.page_specs.values()}
        self.assertEqual(pages["不同旋转的积分"].params["control_state"]["image"]["rotation_angle"], 23.0)
        self.assertEqual(pages["不同旋转的积分"].params["render_quality"]["interpolation"], "nearest")
        dialog = other._blender_project_dialog
        self.assertEqual(dialog.export_panel.selected_volume_mode(), "hybrid_lit")
        self.assertFalse(dialog.export_panel.selected_presets()["guide_frame"])
        self.assertEqual(len(dialog.export_panel.selected_keys()), 2)
        self.assertIn(
            (other.source_manager.active_id, other.active_page_spec.page_id),
            dialog.export_panel.selected_keys(),
        )
        self.assertEqual(len(document["groups"]), 1)

    def test_multi_source_success_selects_each_group_once(self):
        self.window.load_data(str(self.first))
        first = capture_snapshot(self.window)
        self.window.load_data(str(self.second))
        second = capture_snapshot(self.window)
        path, _ = self.write_recipe([first, second])
        other = control_window()
        self.addCleanup(other.close)
        selected = []
        paths = iter((self.first, self.second))
        self.assertTrue(other._load_recipe_by_format(
            path, confirm=False,
            relocate=lambda source: selected.append(source["sha256"]) or str(next(paths)),
        ))
        self.assertEqual(len(selected), 2)
        self.assertNotEqual(selected[0], selected[1])
        self.assertEqual(len(other.source_manager), 2)
        self.assertEqual(len(other._blender_project_dialog.export_panel.selected_keys()), 2)

    def test_same_file_independent_sessions_require_two_selections(self):
        self.window.load_data(str(self.first))
        first = capture_snapshot(self.window)
        self.window.load_data(str(self.first))
        second = capture_snapshot(self.window)
        path, document = self.write_recipe([first, second])
        self.assertEqual(len(document["groups"]), 2)
        other = control_window()
        self.addCleanup(other.close)
        selected = []
        self.assertTrue(other._load_blender_recipe(
            path, confirm=False,
            relocate=lambda source: selected.append(source["sha256"]) or str(self.first),
        ))
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0], selected[1])
        self.assertEqual(len(other.source_manager), 2)
        self.assertEqual(len({session.session_id for session in other.source_manager}), 2)

    def test_second_source_cancel_preserves_existing_workspace(self):
        self.window.load_data(str(self.first))
        first = capture_snapshot(self.window)
        self.window.load_data(str(self.second))
        second = capture_snapshot(self.window)
        path, _ = self.write_recipe([first, second])
        other = control_window()
        self.addCleanup(other.close)
        other.load_data(str(self.first))
        original_core = other.core
        original_pages = list(other.left_workspace.page_specs)
        original_session = other.source_manager.active_id
        paths = iter((self.first, None))
        self.assertFalse(other._load_blender_recipe(
            path, confirm=False, relocate=lambda _source: next(paths),
        ))
        self.assertIs(other.core, original_core)
        self.assertEqual(list(other.left_workspace.page_specs), original_pages)
        self.assertEqual(other.source_manager.active_id, original_session)
        self.assertEqual(len(other.source_manager), 1)

    def test_second_source_digest_mismatch_preserves_existing_workspace(self):
        self.window.load_data(str(self.first))
        first = capture_snapshot(self.window)
        self.window.load_data(str(self.second))
        second = capture_snapshot(self.window)
        path, _ = self.write_recipe([first, second])
        synthetic_npz(self.second, seed=99)
        other = control_window()
        self.addCleanup(other.close)
        other.load_data(str(self.first))
        original_core = other.core
        paths = iter((self.first, self.second))
        with self.assertRaisesRegex(RecipeError, "来源 2.*SHA-256"):
            other._load_blender_recipe(
                path, confirm=False, relocate=lambda _source: str(next(paths)),
            )
        self.assertIs(other.core, original_core)
        self.assertEqual(len(other.source_manager), 1)

    def test_second_session_commit_failure_rolls_back_all_new_sessions(self):
        self.window.load_data(str(self.first))
        first = capture_snapshot(self.window)
        self.window.load_data(str(self.second))
        second = capture_snapshot(self.window)
        path, _ = self.write_recipe([first, second])
        other = control_window()
        self.addCleanup(other.close)
        other.load_data(str(self.first))
        original_core = other.core
        original_session = other.source_manager.active_id
        original_add = other.source_manager.add
        calls = []

        def fail_second(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise RuntimeError("commit failed")
            return original_add(**kwargs)

        other.source_manager.add = fail_second
        paths = iter((self.first, self.second))
        with self.assertRaisesRegex(RuntimeError, "commit failed"):
            other._load_blender_recipe(
                path, confirm=False, relocate=lambda _source: str(next(paths)),
            )
        self.assertIs(other.core, original_core)
        self.assertEqual(other.source_manager.active_id, original_session)
        self.assertEqual(len(other.source_manager), 1)
        self.assertEqual(len(calls), 2)

    def test_display_override_does_not_reenable_removed_effects(self):
        from bandscope.core.blender_recipe import updated_blender_recipe
        from bandscope.core.display_style import create_display_state, new_display_style
        from bandscope.core.view_recipe import digest, state_digest
        from bandscope.exporting.blender_project import update_snapshot_display

        self.window.load_data(str(self.first))
        snapshot = capture_snapshot(self.window)
        document = snapshot.view_recipe
        page = document["pages"][0]
        state = create_display_state(new_display_style())
        state["regions"] = [{"region_id": "removed-region", "enabled": True}]
        page["params"]["local_enhancement"] = state
        old_key = page["recipe_id"]
        node = document["recipes"].pop(old_key)
        node["params"] = copy.deepcopy(page["params"])
        new_key = digest(node)
        document["recipes"][new_key] = node
        page["recipe_id"] = new_key
        weights = [1.5] * snapshot.payload["volume"].shape[2]
        document["view_state"]["opacity_multiplier"] = weights
        document["state_digest"] = state_digest(document)
        snapshot.payload["opacity_multiplier"] = weights
        snapshot.payload["enhancement"] = {"active": True}
        original = build_blender_recipe(BlenderProjectRequest([snapshot]))
        snapshot.payload["blender_project"] = {"display_schema": True}
        update_snapshot_display(
            snapshot, cmap_name="viridis", levels_params=(5.0, 60.0, 90.0),
            locked_range=None, include_zero=False, opacity_mode="linear",
        )
        updated = updated_blender_recipe(original, {"entry-001": snapshot})
        path = self.root / "updated.blend.bsview.json"
        path.write_text(BlenderRecipeDocument(updated).dumps(), encoding="utf-8")
        other = control_window()
        self.addCleanup(other.close)
        self.assertTrue(other._load_blender_recipe(
            path, confirm=False, relocate=lambda _source: str(self.first),
        ))
        restored = other.active_page_spec
        self.assertEqual(restored.params["local_enhancement"]["regions"], [])
        self.assertEqual(restored.params["local_enhancement"]["background"]["levels"], (5.0, 60.0, 90.0))
        self.assertIsNone(other._restored_recipe_views[restored.page_id]["opacity_multiplier"])
        self.assertIsNone(other._recipe_frozen_multiplier(other.current_render_context))
        self.assertFalse(restored.params.get("_restored_effect", False))
        self.assertEqual(updated["entries"][0]["recipe"], original["entries"][0]["recipe"])

    def test_duplicate_target_with_real_science_conflict_is_rejected(self):
        self.window.load_data(str(self.first))
        first = capture_snapshot(self.window)
        self.window.timeline_bar.slider_time.setValue(2)
        second = capture_snapshot(self.window)
        path, _ = self.write_recipe([first, second])
        other = control_window()
        self.addCleanup(other.close)
        with self.assertRaisesRegex(RecipeError, "真实科学参数冲突"):
            other._load_blender_recipe(path, confirm=False, relocate=lambda _source: str(self.first))
        self.assertEqual(len(other.source_manager), 0)


if __name__ == "__main__":
    unittest.main()
