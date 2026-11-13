import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bandscope.core.blender_recipe import BlenderRecipeDocument, blender_sidecar_path
from bandscope.core.view_recipe import RecipeError, ViewRecipeDocument
from bandscope.exporting.blender_export import BlenderExportJob
from bandscope.exporting.blender_jobs import BlenderProjectExportJob, BlenderProjectUpdateJob
from bandscope.exporting.blender_project import (
    BlenderProjectError,
    BlenderProjectRequest,
    load_blender_project,
    update_snapshot_display,
)
from tests.support.blender import (
    make_blender_snapshot,
    project_report_boxes,
    project_report_texts,
    project_report_volume,
)
from tests.support.view_recipe import recipe_document, synthetic_npz


class BlenderRecipeExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.npz = self.root / "input.npz"
        synthetic_npz(self.npz)
        _core, document = recipe_document(self.npz)
        self.recipe = ViewRecipeDocument(document).validate().data

    def snapshot(self, session_id="source-a"):
        snapshot = make_blender_snapshot()
        snapshot.source_session_id = session_id
        snapshot.view_recipe = copy.deepcopy(self.recipe)
        return snapshot

    @staticmethod
    def build_report(metadata, final):
        reports = []
        for scene in metadata["entries"]:
            entry_id = scene["entry_id"]
            boxes, lights = project_report_boxes(metadata, entry_id)
            texts, text_lights = project_report_texts(metadata, entry_id)
            volume_material, volume_lights = project_report_volume(metadata, entry_id)
            reports.append({
                "entry_id": entry_id,
                "volume_filepath": f"//{final.stem}.assets/{scene['assets']['vdb']}",
                "density_space": "OBJECT",
                "weak_signal_clipping": 0.0,
                "grid_names": ["density", "color"],
                "coordinates_present": metadata["project"]["presets"]["coordinates"],
                "guide_frame": ({
                    "object": scene["guide_frame"]["object"],
                    "larger_than_axis_frame": True,
                    "encloses_data_bounds": True,
                } if metadata["project"]["presets"]["guide_frame"] else {}),
                "axis_indicator": ({
                    "all_present": True,
                    "visible_by_default": True,
                    "style": "solid_world_axes",
                    "geometry_validated": True,
                    "axes": [
                        {"key": key, "role": role, "visible": True}
                        for key, role in zip(("X", "E", "Y"), ("horizontal", "vertical", "depth"))
                    ],
                } if metadata["project"]["presets"]["orientation"] else {}),
                "box_presets": boxes,
                "box_lights": lights,
                "volume_material": volume_material,
                "volume_lights": volume_lights,
                "text_presets": texts,
                "text_lights": text_lights,
                "box_layout": metadata["project"]["box_layout"]["id"],
            })
        return {
            "blend_reopened": True,
            "render_samples": metadata["project"]["render"]["samples"],
            "volume_bounces": metadata["project"]["render"]["volume_bounces"],
            "box_layout": metadata["project"]["box_layout"],
            "entries": reports,
        }

    def export(self, final, snapshots, *, save_recipe=True, overwrite=False, replace=None):
        request = BlenderProjectRequest(snapshots)
        job = BlenderProjectExportJob(
            request, "blender.exe", final, overwrite=overwrite, save_recipe=save_recipe,
        )

        def external(_job, job_path, _log_handle):
            payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
            metadata = json.loads(Path(payload["metadata_file"]).read_text(encoding="utf-8"))
            Path(payload["output_blend"]).write_bytes(b"BLENDER-new")
            for entry in payload["entries"]:
                Path(entry["vdb_path"]).write_bytes(b"VDB-new")
            Path(payload["report_file"]).write_text(
                json.dumps(self.build_report(metadata, final)), encoding="utf-8",
            )

        with patch("bandscope.exporting.blender_export.probe_blender", return_value={}), \
                patch.object(BlenderExportJob, "_run_process", autospec=True, side_effect=external):
            if replace is None:
                return job.run()
            with patch("bandscope.exporting.blender_jobs.os.replace", side_effect=replace):
                return job.run()

    def test_export_writes_valid_sidecar_and_actual_settings(self):
        final = self.root / "demo.blend"
        result = self.export(final, [self.snapshot()])
        self.assertEqual(result.recipe_path, Path(blender_sidecar_path(final)))
        document = BlenderRecipeDocument.load(result.recipe_path).data
        self.assertEqual(document["entries"][0]["entry_id"], "entry-001")
        self.assertEqual(document["export_settings"]["volume_shading"]["mode"], "emission")
        self.assertEqual(len(document["export_settings"]["box_materials"]), 8)
        self.assertEqual(len(document["export_settings"]["text_styles"]), 10)
        self.assertNotIn(str(self.npz), result.recipe_path.read_text(encoding="utf-8"))
        loaded = load_blender_project(final)
        self.assertEqual(loaded.recipe, document)

    def test_missing_page_recipe_fails_before_external_process(self):
        snapshot = self.snapshot()
        snapshot.view_recipe = None
        snapshot.recipe_error = "missing scientific state"
        with patch("bandscope.exporting.blender_export.probe_blender") as probe:
            with self.assertRaisesRegex(RecipeError, "非对称 3D.*missing scientific state"):
                BlenderProjectExportJob(
                    BlenderProjectRequest([snapshot]), "blender.exe", self.root / "bad.blend",
                    save_recipe=True,
                )
        probe.assert_not_called()
        self.assertFalse((self.root / "bad.blend").exists())

    def test_staged_sidecar_write_failure_prevents_external_process(self):
        final = self.root / "unwritten.blend"
        job = BlenderProjectExportJob(
            BlenderProjectRequest([self.snapshot()]), "blender.exe", final, save_recipe=True,
        )
        with patch("bandscope.exporting.blender_export.probe_blender", return_value={}), \
                patch.object(BlenderExportJob, "_run_process") as process, \
                patch("bandscope.exporting.blender_jobs.BlenderRecipeDocument.dumps", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                job.run()
        process.assert_not_called()
        self.assertFalse(final.exists())
        self.assertFalse(Path(blender_sidecar_path(final)).exists())

    def test_sidecar_install_failure_restores_all_previous_outputs(self):
        final = self.root / "demo.blend"
        recipe = Path(blender_sidecar_path(final))
        self.export(final, [self.snapshot()])
        old_recipe = recipe.read_bytes()
        final.write_bytes(b"old-blend")
        scene = final.parent / "demo.assets" / "scene.json"
        scene.write_bytes(b"old-scene")
        real_replace = os.replace

        def fail_sidecar(source, target):
            if Path(target).resolve() == recipe.resolve() and Path(source).parent.name != "previous-output":
                raise OSError("sidecar locked")
            return real_replace(source, target)

        with self.assertRaisesRegex(OSError, "sidecar locked"):
            self.export(
                final, [self.snapshot()], overwrite=True, replace=fail_sidecar,
            )
        self.assertEqual(final.read_bytes(), b"old-blend")
        self.assertEqual(scene.read_bytes(), b"old-scene")
        self.assertEqual(recipe.read_bytes(), old_recipe)

    def test_disabled_sidecar_export_rolls_back_then_removes_old_sidecar(self):
        final = self.root / "demo.blend"
        recipe = Path(blender_sidecar_path(final))
        self.export(final, [self.snapshot()])
        old_recipe = recipe.read_bytes()
        final.write_bytes(b"old-blend")
        real_replace = os.replace

        def fail_assets(source, target):
            if (Path(source).name == "demo.assets" and Path(source).parent != self.root
                    and Path(source).parent.name != "previous-output"):
                raise OSError("assets locked")
            return real_replace(source, target)

        with self.assertRaisesRegex(OSError, "assets locked"):
            self.export(
                final, [self.snapshot()], save_recipe=False,
                overwrite=True, replace=fail_assets,
            )
        self.assertEqual(final.read_bytes(), b"old-blend")
        self.assertEqual(recipe.read_bytes(), old_recipe)
        result = self.export(final, [self.snapshot()], save_recipe=False, overwrite=True)
        self.assertIsNone(result.recipe_path)
        self.assertFalse(recipe.exists())

    def test_update_adds_only_display_override_and_preserves_blend(self):
        final = self.root / "demo.blend"
        self.export(final, [self.snapshot()])
        project = load_blender_project(final)
        original_recipe = copy.deepcopy(project.recipe["entries"][0]["recipe"])
        original_blend = hashlib.sha256(final.read_bytes()).hexdigest()
        snapshot = project.entries[0].snapshot
        update_snapshot_display(
            snapshot, cmap_name="viridis", levels_params=(5.0, 65.0, 90.0),
            locked_range=(0.0, 0.8), include_zero=True, opacity_mode="power",
        )
        job = BlenderProjectUpdateJob(project, {"entry-001": snapshot}, "blender.exe")

        def external(_job, job_path, _log_handle):
            payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
            for entry in payload["entries"]:
                Path(entry["vdb_path"]).write_bytes(b"VDB-updated")
            Path(payload["report_file"]).write_text(json.dumps({
                "project_checked": True, "blend_untouched": True, "vdb_staged": True,
            }), encoding="utf-8")

        with patch("bandscope.exporting.blender_export.probe_blender", return_value={}), \
                patch.object(BlenderExportJob, "_run_process", autospec=True, side_effect=external):
            result = job.run()
        self.assertEqual(hashlib.sha256(final.read_bytes()).hexdigest(), original_blend)
        self.assertEqual(result.project.recipe["entries"][0]["recipe"], original_recipe)
        override = result.project.recipe["entries"][0]["display_override"]
        self.assertEqual(override["cmap"], "viridis")
        self.assertEqual(override["locked_range"], [0.0, 0.8])
        self.assertIn("local_enhancement", override["removed_effects"])
        self.assertIn("opacity_multiplier", override["removed_effects"])

    def test_update_sidecar_failure_rolls_back_assets_scene_and_recipe(self):
        final = self.root / "demo.blend"
        self.export(final, [self.snapshot()])
        project = load_blender_project(final)
        recipe = Path(blender_sidecar_path(final))
        old_recipe = recipe.read_bytes()
        old_scene = (project.assets_path / "scene.json").read_bytes()
        old_arrays = project.entries[0].arrays_path.read_bytes()
        old_vdb = project.entries[0].vdb_path.read_bytes()
        old_blend = final.read_bytes()
        snapshot = project.entries[0].snapshot
        update_snapshot_display(
            snapshot, cmap_name="viridis", levels_params=(5.0, 65.0, 90.0),
            locked_range=None, include_zero=False, opacity_mode="linear",
        )
        job = BlenderProjectUpdateJob(project, {"entry-001": snapshot}, "blender.exe")

        def external(_job, job_path, _log_handle):
            payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
            for entry in payload["entries"]:
                Path(entry["vdb_path"]).write_bytes(b"VDB-updated")
            Path(payload["report_file"]).write_text(json.dumps({
                "project_checked": True, "blend_untouched": True, "vdb_staged": True,
            }), encoding="utf-8")

        real_replace = os.replace

        def fail_sidecar(source, target):
            if (Path(target).resolve() == recipe.resolve() and Path(source).resolve() != recipe.resolve()
                    and Path(source).parent.name != "previous-assets"):
                raise OSError("updated sidecar locked")
            return real_replace(source, target)

        with patch("bandscope.exporting.blender_export.probe_blender", return_value={}), \
                patch.object(BlenderExportJob, "_run_process", autospec=True, side_effect=external), \
                patch("bandscope.exporting.blender_jobs.os.replace", side_effect=fail_sidecar):
            with self.assertRaisesRegex(OSError, "updated sidecar locked"):
                job.run()
        self.assertEqual(final.read_bytes(), old_blend)
        self.assertEqual((project.assets_path / "scene.json").read_bytes(), old_scene)
        self.assertEqual(project.entries[0].arrays_path.read_bytes(), old_arrays)
        self.assertEqual(project.entries[0].vdb_path.read_bytes(), old_vdb)
        self.assertEqual(recipe.read_bytes(), old_recipe)

    def test_concurrent_sidecar_change_aborts_before_external_process(self):
        final = self.root / "demo.blend"
        self.export(final, [self.snapshot()])
        project = load_blender_project(final)
        recipe = Path(blender_sidecar_path(final))
        with recipe.open("a", encoding="utf-8") as handle:
            handle.write(" ")
        with patch.object(BlenderExportJob, "_run_process") as process:
            with self.assertRaisesRegex(BlenderProjectError, "已变化"):
                BlenderProjectUpdateJob(
                    project, {"entry-001": project.entries[0].snapshot}, "blender.exe",
                ).run()
        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
