import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from bandscope.exporting.blender_export import (
    BlenderExportJob, BlenderOutputExistsError, BlenderProcessError,
)
from bandscope.exporting.blender_jobs import (
    BlenderProjectUpdateJob, commit_staged_project, commit_staged_update,
)
from bandscope.exporting.blender_project import (
    BOX_COLOR, BOX_GROUP_PRESET, BOX_OPACITY, BOX_PRESETS, BOX_THICKNESS,
    PRESET_KEYS, BlenderProjectError,
    BlenderProjectRequest, load_blender_project, make_project_metadata,
    merged_project_metadata, prepare_scene_for_update, update_snapshot_display,
)
from bandscope.exporting.blender_scene import prepare_blender_scene
from tests.support.blender import (
    make_blender_snapshot, project_report_boxes, project_report_texts, project_report_volume,
    write_fake_blender_project,
)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _project_report(metadata, final):
    reports = []
    for scene in metadata["entries"]:
        entry_id = scene["entry_id"]
        boxes, lights = project_report_boxes(metadata, entry_id)
        texts, text_lights = project_report_texts(metadata, entry_id)
        volume_material, volume_lights = project_report_volume(metadata, entry_id)
        reports.append({
            "entry_id": entry_id,
            "volume_filepath": f"//{final.stem}.assets/{scene['assets']['vdb']}",
            "density_space": "OBJECT", "weak_signal_clipping": 0.0,
            "grid_names": ["density", "color"],
            "coordinates_present": metadata["project"]["presets"]["coordinates"],
            "guide_frame": ({
                "object": scene["guide_frame"]["object"],
                "larger_than_axis_frame": True, "encloses_data_bounds": True,
            } if metadata["project"]["presets"]["guide_frame"] else {}),
            "axis_indicator": ({
                "all_present": True, "visible_by_default": True, "style": "solid_world_axes",
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


def _stage_project(directory, blend, metadata, report):
    import shutil
    stage = Path(directory) / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    shutil.copytree(blend.parent / f"{blend.stem}.assets", stage / f"{blend.stem}.assets")
    (stage / blend.name).write_bytes(b"BLENDER-new")
    (stage / "blender-report.json").write_text(json.dumps(report), encoding="utf-8")
    return stage


class BlenderProjectContractTests(unittest.TestCase):
    def test_multi_entry_assets_layout_presets_and_edit_state(self):
        first = make_blender_snapshot(cmap_name="magma", locked_range=(0.0, 1.0))
        second = make_blender_snapshot(cmap_name="viridis", levels=(5.0, 60.0, 95.0))
        second.source_session_id = "source-b"
        request = BlenderProjectRequest([first, second])
        items = []
        for entry in request.entries:
            metadata = prepare_blender_scene(entry.snapshot).metadata()
            metadata["entry_id"] = entry.entry_id
            items.append((entry, metadata))
        metadata = make_project_metadata(items, request, "demo.assets")
        self.assertEqual(metadata["schema_version"], 11)
        self.assertEqual(metadata["project"]["entry_order"], ["entry-001", "entry-002"])
        self.assertEqual(set(metadata["project"]["presets"]), set(PRESET_KEYS))
        self.assertTrue(all(metadata["project"]["presets"].values()))
        self.assertEqual(metadata["project"]["box_layout"]["id"], "right_front_groups")
        self.assertEqual(
            {key: record["material_id"] for key, record in metadata["project"]["box_materials"].items()},
            {key: key for key in BOX_PRESETS},
        )
        self.assertNotEqual(
            metadata["entries"][0]["assets"]["arrays"], metadata["entries"][1]["assets"]["arrays"],
        )
        first_layout = metadata["project"]["layout"]["entries"]["entry-001"]
        second_layout = metadata["project"]["layout"]["entries"]["entry-002"]
        self.assertLess(first_layout["world_bounds"][1], second_layout["world_bounds"][0])
        for scene in metadata["entries"]:
            self.assertEqual(len(scene["box_presets"]), 8)
            self.assertEqual(len({box["material"] for box in scene["box_presets"]}), 8)
            for box in scene["box_presets"]:
                self.assertEqual(box["thickness"], BOX_THICKNESS)
                self.assertEqual(box["opacity"], box["parameters"].get("opacity"))
                self.assertEqual(box["color"], list(BOX_COLOR))
                self.assertTrue(box["full_cuboid"])
        edit = metadata["entries"][0]["bandscope_edit"]
        self.assertEqual(edit["cmap_name"], "magma")
        self.assertEqual(edit["locked_range"], [0.0, 1.0])
        self.assertEqual(edit["levels_params"], [0.0, 50.0, 100.0])
        self.assertEqual(metadata["entries"][1]["source"]["session_id"], "source-b")

    def test_partial_presets_change_report_validation_and_box_geometry(self):
        presets = {key: False for key in PRESET_KEYS}
        presets["coordinates"] = True
        presets[BOX_GROUP_PRESET] = True
        request = BlenderProjectRequest([make_blender_snapshot()], presets)
        scene = prepare_blender_scene(request.entries[0].snapshot).metadata()
        scene["entry_id"] = "entry-001"
        metadata = make_project_metadata([(request.entries[0], scene)], request, "demo.assets")
        self.assertFalse(metadata["project"]["presets"]["guide_frame"])
        self.assertEqual(
            [box["preset"] for box in metadata["entries"][0]["box_presets"]
             if presets[box["preset_group"]]],
            list(BOX_PRESETS),
        )
        with tempfile.TemporaryDirectory() as directory:
            final = Path(directory) / "partial.blend"
            blend, metadata = write_fake_blender_project(directory, presets=presets, name="partial")
            stage = Path(directory) / "stage"
            staged_assets = stage / "partial.assets"
            (stage).mkdir()
            import shutil
            shutil.copytree(blend.parent / "partial.assets", staged_assets)
            (stage / "partial.blend").write_bytes(b"BLENDER-new")
            (stage / "blender-report.json").write_text(json.dumps(_project_report(metadata, final)))
            result = commit_staged_project(stage, final, metadata, overwrite=True)
            self.assertTrue(result.blend_path.is_file())

    def test_reopen_report_rejects_wrong_material_emission_lights_and_overlap(self):
        def case(mutate, expected):
            with tempfile.TemporaryDirectory() as directory:
                blend, metadata = write_fake_blender_project(
                    directory, box_layout="data_aligned",
                )
                final = Path(directory) / f"{Path(blend).stem}.blend"
                report = _project_report(metadata, final)
                mutate(report)
                stage = _stage_project(directory, blend, metadata, report)
                with self.assertRaisesRegex(BlenderProcessError, expected):
                    commit_staged_project(stage, final, metadata, overwrite=True)

        def drop_light(report):
            entry = report["entries"][0]
            entry["box_lights"] = [
                light for light in entry["box_lights"] if light["preset"] != "matte"
            ]
            for box in entry["box_presets"]:
                if box["preset"] == "matte":
                    box["light"] = None

        case(drop_light, "缺少辅助灯")

        def fake_emission(report):
            for box in report["entries"][0]["box_presets"]:
                if box["preset"] == "data_translucent":
                    box["emission"] = {"effective": True}

        case(fake_emission, "不应存在有效自发光")

        def break_overlap(report):
            for box in report["entries"][0]["box_presets"]:
                if box["preset"] == "data_translucent":
                    box["intersects_data"] = False

        case(break_overlap, "没有实际光路重叠")

        def share_material(report):
            boxes = report["entries"][0]["box_presets"]
            boxes[0]["material"] = boxes[1]["material"]

        case(share_material, "共享材质")

        def wrong_layout(report):
            report["entries"][0]["box_layout"] = "separated_right"

        case(wrong_layout, "布局")

    def test_reopen_report_rejects_missing_render_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            blend, metadata = write_fake_blender_project(directory, name="samples")
            final = Path(directory) / "samples.blend"
            report = _project_report(metadata, final)
            report["render_samples"] = 8
            stage = _stage_project(directory, blend, metadata, report)
            with self.assertRaisesRegex(BlenderProcessError, "采样"):
                commit_staged_project(stage, final, metadata, overwrite=True)

    def test_project_with_all_presets_disabled_commits_without_optional_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            presets = {key: False for key in PRESET_KEYS}
            blend, metadata = write_fake_blender_project(
                directory, presets=presets, name="empty",
            )
            final = Path(directory) / "empty.blend"
            report = _project_report(metadata, final)
            entry = report["entries"][0]
            self.assertEqual(entry["box_presets"], [])
            self.assertEqual(entry["box_lights"], [])
            stage = _stage_project(directory, blend, metadata, report)
            result = commit_staged_project(stage, final, metadata, overwrite=True)
            self.assertTrue(result.blend_path.is_file())

    def test_hybrid_all_presets_disabled_still_requires_three_volume_lights(self):
        with tempfile.TemporaryDirectory() as directory:
            presets = {key: False for key in PRESET_KEYS}
            blend, metadata = write_fake_blender_project(
                directory, presets=presets, volume_mode="hybrid_lit",
            )
            final = blend
            report = _project_report(metadata, final)
            entry = report["entries"][0]
            self.assertEqual(entry["box_lights"], [])
            self.assertEqual(entry["text_lights"], [])
            self.assertEqual(len(entry["volume_lights"]), 3)
            stage = _stage_project(directory, blend, metadata, report)
            result = commit_staged_project(stage, final, metadata, overwrite=True)
            self.assertEqual(result.report["volume_bounces"], 2)

    def test_hybrid_report_rejects_bad_connections_lights_sharing_and_bounces(self):
        def case(mutate, expected, snapshots=None):
            with tempfile.TemporaryDirectory() as directory:
                blend, metadata = write_fake_blender_project(
                    directory, snapshots, volume_mode="hybrid_lit",
                )
                report = _project_report(metadata, blend)
                mutate(report)
                stage = _stage_project(directory, blend, metadata, report)
                with self.assertRaisesRegex(BlenderProcessError, expected):
                    commit_staged_project(stage, blend, metadata, overwrite=True)

        def wrong_connection(report):
            report["entries"][0]["volume_material"]["connections"]["Scatter Coefficients"] = "wrong"

        case(wrong_connection, "实际连接")

        def missing_light(report):
            report["entries"][0]["volume_lights"].pop()

        case(missing_light, "灯数量")

        def wrong_receiver(report):
            report["entries"][0]["volume_lights"][0]["receiver_objects"] = []

        case(wrong_receiver, "接收者")

        def share_volume_material(report):
            reports = report["entries"]
            reports[1]["volume_material"]["material"] = reports[0]["volume_material"]["material"]

        case(share_volume_material, "共享可变数据体材质", [make_blender_snapshot(), make_blender_snapshot()])

        def wrong_bounces(report):
            report["volume_bounces"] = 0

        case(wrong_bounces, "反弹次数")


class BlenderProjectReaderTests(unittest.TestCase):
    @staticmethod
    def _downgrade_to_schema_7(assets, metadata):
        import copy
        legacy = copy.deepcopy(metadata)
        legacy["schema_version"] = 7
        for key in ("box_materials", "box_layout", "box_lighting", "render",
                    "text_styles", "text_fonts", "text_lighting", "volume_shading"):
            legacy["project"].pop(key, None)
        legacy["project"]["presets"].pop("text_presets", None)
        for entry in legacy["entries"]:
            entry["schema_version"] = 7
            entry.pop("box_lighting", None)
            entry.pop("volume_lighting", None)
            for key in ("text_layout", "text_presets", "text_lighting"):
                entry.pop(key, None)
            entry["box_presets"] = [
                {
                    "preset": box["preset"], "object": box["object"], "material": box["material"],
                    "center": box["center"], "size": box["size"], "color": box["color"],
                    "opacity": BOX_OPACITY, "thickness": box["thickness"],
                    "layer_center_spacing": 0.35, "independent_material": True,
                    "full_cuboid": True,
                }
                for box in entry["box_presets"]
            ]
        (assets / "scene.json").write_text(json.dumps(legacy), encoding="utf-8")
        return legacy

    def test_roundtrip_restores_each_frozen_entry_without_source_lock_scope(self):
        first = make_blender_snapshot(cmap_name="magma", locked_range=(0.0, 0.5))
        second = make_blender_snapshot(cmap_name="viridis", levels=(10.0, 70.0, 90.0))
        second.payload["include_zero"] = True
        second.payload["opacity_mode"] = "sigmoid"
        second.payload["volume"] = second.payload["volume"] * 3.0
        second.payload["level_info"] = __import__(
            "bandscope.exporting.publication_renderers", fromlist=["compute_level_info"]
        ).compute_level_info(second.payload["volume"], second.levels_params, include_zero=True)
        with tempfile.TemporaryDirectory() as directory:
            blend, _ = write_fake_blender_project(directory, [first, second])
            project = load_blender_project(blend)
            self.assertEqual(len(project.entries), 2)
            one, two = project.entries
            self.assertIsNone(one.snapshot.source_session_id)
            self.assertEqual(one.snapshot.cmap_name, "magma")
            self.assertEqual(one.snapshot.locked_range, (0.0, 0.5))
            self.assertEqual(two.snapshot.cmap_name, "viridis")
            self.assertEqual(two.snapshot.levels_params, (10.0, 70.0, 90.0))
            self.assertTrue(two.snapshot.payload["include_zero"])
            self.assertEqual(two.snapshot.payload["opacity_mode"], "sigmoid")
            np.testing.assert_array_equal(two.snapshot.payload["volume"], second.payload["volume"])
            self.assertTrue(two.snapshot.payload["blender_project"]["frozen_result"])
            self.assertNotEqual(id(one.snapshot.payload), id(two.snapshot.payload))
            np.testing.assert_allclose(
                one.snapshot.camera["position"], first.camera["position"], rtol=0, atol=1e-6,
            )

    def test_legacy_schema_has_limited_recovery_and_requires_cmap_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            blend, metadata = write_fake_blender_project(directory)
            assets = blend.parent / f"{blend.stem}.assets"
            scene = metadata["entries"][0]
            import shutil
            for name in ("arrays.npz", "volume.vdb"):
                shutil.copyfile(assets / "entries/entry-001" / name, assets / name)
            scene["schema_version"] = 6
            scene.pop("bandscope_edit", None)
            scene["assets"] = {"vdb": "volume.vdb", "arrays": "arrays.npz", "readme": "README.txt"}
            (assets / "scene.json").write_text(json.dumps(scene), encoding="utf-8")
            project = load_blender_project(blend)
            self.assertTrue(project.legacy)
            snapshot = project.entries[0].snapshot
            self.assertEqual(snapshot.cmap_name, "")
            self.assertTrue(snapshot.payload["blender_project"]["needs_cmap"])
            self.assertIn("unknown_legacy_effects", snapshot.payload["blender_project"]["baked_effects"])
            self.assertTrue(project.warnings)
            with self.assertRaisesRegex(BlenderProjectError, "colormap"):
                prepare_scene_for_update(snapshot)

    def test_missing_assets_arbitrary_blend_and_future_schema_are_specific_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            arbitrary = root / "ordinary.blend"
            arbitrary.write_bytes(b"not a blender project")
            with self.assertRaisesRegex(BlenderProjectError, "任意"):
                load_blender_project(arbitrary)
            blend, metadata = write_fake_blender_project(root, name="valid")
            (blend.parent / "valid.assets/entries/entry-001/volume.vdb").unlink()
            with self.assertRaisesRegex(BlenderProjectError, "VDB"):
                load_blender_project(blend)
            blend, metadata = write_fake_blender_project(root, name="future")
            metadata["schema_version"] = 999
            (blend.parent / "future.assets/scene.json").write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(BlenderProjectError, "高于"):
                load_blender_project(blend)

    def test_schema_7_project_keeps_version_and_flat_boxes_on_update(self):
        with tempfile.TemporaryDirectory() as directory:
            blend, metadata = write_fake_blender_project(directory)
            assets = blend.parent / f"{blend.stem}.assets"
            legacy = self._downgrade_to_schema_7(assets, metadata)
            project = load_blender_project(blend)
            self.assertEqual(project.schema_version, 7)
            self.assertFalse(project.supports_box_materials)
            self.assertTrue(any("schema 7" in warning for warning in project.warnings))
            snapshot = project.entries[0].snapshot
            update_snapshot_display(
                snapshot, cmap_name="viridis", levels_params=(5.0, 60.0, 90.0),
                locked_range=None, include_zero=False, opacity_mode="linear",
            )
            generated = prepare_scene_for_update(snapshot).metadata()
            merged = merged_project_metadata(project, {"entry-001": generated})
            self.assertEqual(merged["schema_version"], 7)
            self.assertEqual(merged["entries"][0]["schema_version"], 7)
            self.assertNotIn("box_materials", merged["project"])
            self.assertEqual(
                merged["entries"][0]["box_presets"], legacy["entries"][0]["box_presets"],
            )

    def test_unknown_or_out_of_range_box_metadata_is_rejected_on_read(self):
        with tempfile.TemporaryDirectory() as directory:
            blend, metadata = write_fake_blender_project(directory)
            assets = blend.parent / f"{blend.stem}.assets"
            metadata["project"]["box_materials"]["flat_emission"]["material_id"] = "not-a-material"
            (assets / "scene.json").write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(BlenderProjectError, "未知材质"):
                load_blender_project(blend)

            blend, metadata = write_fake_blender_project(directory, name="range")
            assets = blend.parent / f"{blend.stem}.assets"
            metadata["entries"][0]["box_presets"][0]["parameters"]["opacity"] = 9.0
            (assets / "scene.json").write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(BlenderProjectError, "范围"):
                load_blender_project(blend)


class BlenderProjectUpdateTests(unittest.TestCase):
    def test_update_replaces_only_selected_assets_and_preserves_blend(self):
        with tempfile.TemporaryDirectory() as directory:
            blend, _ = write_fake_blender_project(directory, [make_blender_snapshot(), make_blender_snapshot()])
            project = load_blender_project(blend)
            first = project.entries[0].snapshot
            update_snapshot_display(
                first, cmap_name="viridis", levels_params=(5.0, 65.0, 90.0),
                locked_range=(0.0, 0.8), include_zero=True, opacity_mode="power",
            )
            blend_hash = _sha(blend)
            untouched_vdb = project.entries[1].vdb_path.read_bytes()
            report = {"project_checked": True, "blend_untouched": True, "vdb_staged": [{"entry_id": "entry-001"}]}

            def external(job, job_path, log_handle):
                payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
                for entry in payload["entries"]:
                    Path(entry["vdb_path"]).write_bytes(b"VDB-updated")
                Path(payload["report_file"]).write_text(json.dumps(report), encoding="utf-8")

            with patch("bandscope.exporting.blender_export.probe_blender", return_value={}), \
                    patch.object(BlenderExportJob, "_run_process", autospec=True, side_effect=external):
                result = BlenderProjectUpdateJob(project, {"entry-001": first}, "blender.exe").run()
            self.assertEqual(_sha(blend), blend_hash)
            self.assertEqual(result.project.entries[1].vdb_path.read_bytes(), untouched_vdb)
            self.assertEqual(result.project.entries[0].vdb_path.read_bytes(), b"VDB-updated")
            self.assertEqual(result.project.schema_version, 11)
            self.assertEqual(result.project.metadata["entries"][0]["schema_version"], 11)
            updated = result.project.entries[0].snapshot
            self.assertEqual(updated.cmap_name, "viridis")
            self.assertEqual(updated.levels_params, (5.0, 65.0, 90.0))
            self.assertEqual(updated.locked_range, (0.0, 0.8))
            expected = prepare_scene_for_update(first)
            with np.load(result.project.entries[0].arrays_path) as arrays:
                np.testing.assert_allclose(arrays["density"], expected.density, rtol=1e-6, atol=1e-6)

    def test_concurrent_change_aborts_before_external_process(self):
        with tempfile.TemporaryDirectory() as directory:
            blend, _ = write_fake_blender_project(directory)
            project = load_blender_project(blend)
            snapshot = project.entries[0].snapshot
            with (project.assets_path / "scene.json").open("a", encoding="utf-8") as handle:
                handle.write(" ")
            with patch.object(BlenderExportJob, "_run_process") as process:
                with self.assertRaisesRegex(BlenderProjectError, "已变化"):
                    BlenderProjectUpdateJob(project, {"entry-001": snapshot}, "blender.exe").run()
            process.assert_not_called()

    def test_external_structure_failure_preserves_old_project(self):
        with tempfile.TemporaryDirectory() as directory:
            blend, _ = write_fake_blender_project(directory)
            project = load_blender_project(blend)
            original = project.entries[0].arrays_path.read_bytes()
            blend_hash = _sha(blend)
            with patch("bandscope.exporting.blender_export.probe_blender", return_value={}), \
                    patch.object(BlenderExportJob, "_run_process", side_effect=BlenderProcessError("VDB 路径改向")):
                with self.assertRaisesRegex(BlenderProcessError, "改向"):
                    BlenderProjectUpdateJob(project, {"entry-001": project.entries[0].snapshot}, "blender.exe").run()
            self.assertEqual(project.entries[0].arrays_path.read_bytes(), original)
            self.assertEqual(_sha(blend), blend_hash)

    def test_partial_commit_failure_rolls_back_every_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            blend, _ = write_fake_blender_project(directory)
            project = load_blender_project(blend)
            stage = Path(directory) / "stage"
            staged_assets = stage / project.assets_path.name
            entry = project.entries[0].metadata
            arrays_rel = Path(entry["assets"]["arrays"])
            vdb_rel = Path(entry["assets"]["vdb"])
            staged_arrays = staged_assets / arrays_rel
            staged_vdb = staged_assets / vdb_rel
            staged_arrays.parent.mkdir(parents=True)
            staged_arrays.write_bytes(b"new-arrays")
            staged_vdb.write_bytes(b"new-vdb")
            (staged_assets / "scene.json").write_text(json.dumps(project.metadata), encoding="utf-8")
            old_arrays = project.entries[0].arrays_path.read_bytes()
            old_vdb = project.entries[0].vdb_path.read_bytes()
            old_scene = (project.assets_path / "scene.json").read_bytes()
            real_replace = os.replace

            def fail_vdb(source, target):
                if Path(source) == staged_vdb:
                    raise OSError("simulated update failure")
                return real_replace(source, target)

            with patch("bandscope.exporting.blender_jobs.os.replace", side_effect=fail_vdb):
                with self.assertRaisesRegex(OSError, "simulated"):
                    commit_staged_update(stage, project, {"entry-001": {}})
            self.assertEqual(project.entries[0].arrays_path.read_bytes(), old_arrays)
            self.assertEqual(project.entries[0].vdb_path.read_bytes(), old_vdb)
            self.assertEqual((project.assets_path / "scene.json").read_bytes(), old_scene)

    def test_post_commit_validation_failure_also_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            blend, _ = write_fake_blender_project(directory)
            project = load_blender_project(blend)
            stage = Path(directory) / "stage"
            staged_assets = stage / project.assets_path.name
            entry = project.entries[0].metadata
            for key, payload in (("arrays", b"new-arrays"), ("vdb", b"new-vdb")):
                target = staged_assets / entry["assets"][key]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            (staged_assets / "scene.json").write_text("{}", encoding="utf-8")
            old_scene = (project.assets_path / "scene.json").read_bytes()
            old_arrays = project.entries[0].arrays_path.read_bytes()
            old_vdb = project.entries[0].vdb_path.read_bytes()
            with self.assertRaises(BlenderProjectError):
                commit_staged_update(stage, project, {"entry-001": {}})
            self.assertEqual((project.assets_path / "scene.json").read_bytes(), old_scene)
            self.assertEqual(project.entries[0].arrays_path.read_bytes(), old_arrays)
            self.assertEqual(project.entries[0].vdb_path.read_bytes(), old_vdb)


class BlenderCompressedReaderTests(unittest.TestCase):
    def test_zstd_blender_header_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            blend, _ = write_fake_blender_project(directory)
            payload = bytearray(blend.read_bytes())
            payload[:4] = b"\x28\xb5\x2f\xfd"
            blend.write_bytes(payload)
            project = load_blender_project(blend)
            self.assertEqual(len(project.entries), 1)


if __name__ == "__main__":
    unittest.main()
