import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

import bandscope.exporting.blender_export as be
from bandscope.exporting.blender_export import (
    BLENDER_PATH_SETTING, BlenderCapabilityError, BlenderExportCancelled,
    BlenderExportJob, BlenderOutputExistsError, BlenderProcessError,
    assets_path_for, blender_script_path, commit_staged_output, detect_blender,
    probe_blender, remember_blender_path, usage_text,
)
from tests.support.blender import (
    make_blender_snapshot, project_report_boxes, project_report_texts, project_report_volume,
)
from tests.support.publication import MemoryPublicationSettings


def _report(final_path, *, relative_separator="\\"):
    assets = assets_path_for(final_path)
    return {
        "blend_reopened": True,
        "volume_filepath": f"//{assets.name}{relative_separator}volume.vdb",
        "density_space": "OBJECT",
        "weak_signal_clipping": 0.0,
        "grid_names": ["density", "color"],
        "guide_frame": {
            "object": "BandScope guide frame",
            "collection": "Guide frame · auxiliary",
            "parent": "BandScope Root",
            "bounds": [-1.1, 1.1, -1.1, 1.1, -1.1, 1.1],
            "axis_frame_bounds": [-1.0, 1.0, -1.0, 1.0, -1.0, 1.0],
            "data_bounds": [-0.6, 0.2, -1.0 / 3.0, 2.0 / 3.0, -5.0 / 7.0, 3.0 / 7.0],
            "larger_than_axis_frame": True,
            "encloses_data_bounds": True,
            "hide_render": False,
            "collection_hide_render": True,
        },
        "axis_indicator": {
            "collection": "Orientation marker · publication",
            "style": "solid_world_axes",
            "all_present": True,
            "visible_by_default": True,
            "geometry_validated": True,
            "axes": [
                {"key": key, "role": role, "kind": "arrow", "visible": True}
                for key, role in zip(("X", "E", "Y"), ("horizontal", "vertical", "depth"))
            ],
        },
    }


def _write_staged(stage, final_path):
    assets = stage / assets_path_for(final_path).name
    assets.mkdir(parents=True)
    (stage / final_path.name).write_bytes(b"BLENDER-new")
    (assets / "volume.vdb").write_bytes(b"VDB-new")
    np.savez_compressed(assets / "arrays.npz", density=np.ones((1, 1, 1), dtype=np.float32))
    (assets / "scene.json").write_text("{}", encoding="utf-8")
    (assets / "README.txt").write_text("readme", encoding="utf-8")
    (stage / "blender-report.json").write_text(json.dumps(_report(final_path)), encoding="utf-8")
    return assets


class BlenderDetectionTests(unittest.TestCase):
    def test_saved_path_is_used_and_manual_path_is_remembered(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "Blender 中文" / "blender.exe"
            executable.parent.mkdir()
            executable.write_bytes(b"placeholder")
            settings = MemoryPublicationSettings()
            settings.setValue(BLENDER_PATH_SETTING, str(executable))
            self.assertEqual(detect_blender(settings), executable.resolve())
            other = Path(directory) / "other.exe"
            other.write_bytes(b"placeholder")
            remember_blender_path(settings, other)
            self.assertEqual(settings.value(BLENDER_PATH_SETTING), str(other.resolve()))

    def test_path_candidate_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "blender.exe"
            executable.write_bytes(b"placeholder")
            with patch("bandscope.exporting.blender_export.shutil.which", return_value=str(executable)):
                self.assertEqual(detect_blender(MemoryPublicationSettings()), executable.resolve())

    def test_missing_blender_returns_none(self):
        settings = MemoryPublicationSettings()
        with patch("bandscope.exporting.blender_export.blender_candidates", return_value=()):
            self.assertIsNone(detect_blender(settings))
            self.assertIsNone(detect_blender(settings, "missing/blender.exe"))

    def test_probe_uses_argument_list_factory_settings_and_unicode_path(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "Blender 中文 空格" / "blender.exe"
            executable.parent.mkdir()
            executable.write_bytes(b"placeholder")
            capability = {
                "blender_version": "5.2.3 LTS", "volume_coefficients": True,
                "object_space_density": True, "cycles": True,
                "box_materials": True, "light_linking": True, "text_presets": True,
            }
            completed = subprocess.CompletedProcess(
                [], 0, stdout=be.CAPABILITY_PREFIX + json.dumps(capability),
            )
            with patch("bandscope.exporting.blender_export.subprocess.run", return_value=completed) as run:
                result = probe_blender(executable)
            self.assertEqual(result, capability)
            command = run.call_args.args[0]
            self.assertIsInstance(command, list)
            self.assertEqual(command[0], str(executable))
            self.assertIn("--background", command)
            self.assertIn("--factory-startup", command)
            self.assertEqual(command[-1], "--probe")
            self.assertTrue(blender_script_path().is_file())
            self.assertNotIn("shell", run.call_args.kwargs)

    def test_probe_reports_missing_capabilities(self):
        completed = subprocess.CompletedProcess([], 1, stdout="BANDSCOPE_ERROR no openvdb")
        with patch("bandscope.exporting.blender_export.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(BlenderCapabilityError, "openvdb"):
                probe_blender("blender.exe")


class BlenderTransactionTests(unittest.TestCase):
    def test_commit_installs_blend_and_assets_with_windows_relative_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            final = root / "工程 名称.blend"
            stage = root / "stage"
            stage.mkdir()
            _write_staged(stage, final)
            result = commit_staged_output(stage, final)
            self.assertEqual(result.blend_path, final)
            self.assertEqual(final.read_bytes(), b"BLENDER-new")
            self.assertEqual((assets_path_for(final) / "volume.vdb").read_bytes(), b"VDB-new")

    def test_existing_output_is_not_overwritten_without_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            final = root / "scene.blend"
            assets = assets_path_for(final)
            assets.mkdir()
            final.write_bytes(b"old-blend")
            (assets / "volume.vdb").write_bytes(b"old-vdb")
            stage = root / "stage"
            stage.mkdir()
            _write_staged(stage, final)
            with self.assertRaises(BlenderOutputExistsError):
                commit_staged_output(stage, final, overwrite=False)
            self.assertEqual(final.read_bytes(), b"old-blend")
            self.assertEqual((assets / "volume.vdb").read_bytes(), b"old-vdb")

    def test_overwrite_replaces_both_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            final = root / "scene.blend"
            assets = assets_path_for(final)
            assets.mkdir()
            final.write_bytes(b"old-blend")
            (assets / "volume.vdb").write_bytes(b"old-vdb")
            stage = root / "stage"
            stage.mkdir()
            _write_staged(stage, final)
            commit_staged_output(stage, final, overwrite=True)
            self.assertEqual(final.read_bytes(), b"BLENDER-new")
            self.assertEqual((assets / "volume.vdb").read_bytes(), b"VDB-new")

    def test_commit_rejects_missing_or_invalid_guide_frame(self):
        def remove(report):
            report.pop("guide_frame")

        def not_larger(report):
            report["guide_frame"]["larger_than_axis_frame"] = False

        def not_enclosing(report):
            report["guide_frame"]["encloses_data_bounds"] = False

        for label, mutate in (("missing", remove), ("small", not_larger), ("outside", not_enclosing)):
            with self.subTest(label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                final = root / "scene.blend"
                stage = root / "stage"
                stage.mkdir()
                _write_staged(stage, final)
                report_path = stage / "blender-report.json"
                report = json.loads(report_path.read_text(encoding="utf-8"))
                mutate(report)
                report_path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaisesRegex(BlenderProcessError, "辅助盒框"):
                    commit_staged_output(stage, final)
                self.assertFalse(final.exists())

    def test_commit_rejects_missing_incomplete_or_hidden_axis_indicator(self):
        def remove(report):
            report.pop("axis_indicator")

        def incomplete(report):
            report["axis_indicator"]["all_present"] = False

        def hidden(report):
            report["axis_indicator"]["visible_by_default"] = False

        def hidden_axis(report):
            report["axis_indicator"]["axes"][0]["visible"] = False

        def missing_axis(report):
            report["axis_indicator"]["axes"].pop()

        def wrong_style(report):
            report["axis_indicator"]["style"] = "fixed_rgb_axes"

        def wrong_projection(report):
            report["axis_indicator"]["geometry_validated"] = False

        cases = (
            ("missing", remove), ("incomplete", incomplete), ("hidden", hidden),
            ("hidden_axis", hidden_axis), ("missing_axis", missing_axis),
            ("wrong_style", wrong_style), ("wrong_projection", wrong_projection),
        )
        for label, mutate in cases:
            with self.subTest(label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                final = root / "scene.blend"
                stage = root / "stage"
                stage.mkdir()
                _write_staged(stage, final)
                report_path = stage / "blender-report.json"
                report = json.loads(report_path.read_text(encoding="utf-8"))
                mutate(report)
                report_path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaisesRegex(BlenderProcessError, "三轴方向标"):
                    commit_staged_output(stage, final)
                self.assertFalse(final.exists())

    def test_asset_commit_failure_rolls_back_previous_blend_and_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            final = root / "scene.blend"
            assets = assets_path_for(final)
            assets.mkdir()
            final.write_bytes(b"old-blend")
            (assets / "volume.vdb").write_bytes(b"old-vdb")
            stage = root / "stage"
            stage.mkdir()
            staged_assets = _write_staged(stage, final)
            real_replace = os.replace

            def fail_new_assets(source, target):
                if Path(source) == staged_assets:
                    raise OSError("simulated disk failure")
                return real_replace(source, target)

            with patch("bandscope.exporting.blender_export.os.replace", side_effect=fail_new_assets):
                with self.assertRaisesRegex(OSError, "simulated"):
                    commit_staged_output(stage, final, overwrite=True)
            self.assertEqual(final.read_bytes(), b"old-blend")
            self.assertEqual((assets / "volume.vdb").read_bytes(), b"old-vdb")


class BlenderJobTests(unittest.TestCase):
    def test_fake_external_builder_runs_complete_job_with_unicode_output(self):
        with tempfile.TemporaryDirectory() as directory:
            final = Path(directory) / "结果 中文.blend"
            snapshot = make_blender_snapshot()

            def build(job, job_path, log_handle):
                payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
                metadata = json.loads(Path(payload["metadata_file"]).read_text(encoding="utf-8"))
                Path(payload["output_blend"]).write_bytes(b"BLENDER-fake")
                reports = []
                for entry in payload["entries"]:
                    Path(entry["vdb_path"]).write_bytes(b"VDB-fake")
                    report = _report(final)
                    boxes, lights = project_report_boxes(metadata, entry["entry_id"])
                    texts, text_lights = project_report_texts(metadata, entry["entry_id"])
                    volume_material, volume_lights = project_report_volume(metadata, entry["entry_id"])
                    report.update(
                        entry_id=entry["entry_id"],
                        volume_filepath=f"//{final.stem}.assets/entries/{entry['entry_id']}/volume.vdb",
                        coordinates_present=True,
                        box_presets=boxes,
                        box_lights=lights,
                        volume_material=volume_material,
                        volume_lights=volume_lights,
                        text_presets=texts,
                        text_lights=text_lights,
                        box_layout=metadata["project"]["box_layout"]["id"],
                    )
                    reports.append(report)
                Path(payload["report_file"]).write_text(json.dumps({
                    "blend_reopened": True,
                    "render_samples": metadata["project"]["render"]["samples"],
                    "volume_bounces": metadata["project"]["render"]["volume_bounces"],
                    "box_layout": metadata["project"]["box_layout"],
                    "entries": reports,
                }), encoding="utf-8")
                log_handle.write("fake builder\n")
                job.progress(90, "fake complete")

            with patch("bandscope.exporting.blender_export.probe_blender", return_value={}), \
                    patch.object(BlenderExportJob, "_run_process", autospec=True, side_effect=build):
                result = BlenderExportJob(snapshot, "blender.exe", final).run()
            self.assertEqual(final.read_bytes(), b"BLENDER-fake")
            entry_assets = result.assets_path / "entries" / "entry-001"
            self.assertEqual((entry_assets / "volume.vdb").read_bytes(), b"VDB-fake")
            with np.load(entry_assets / "arrays.npz") as arrays:
                self.assertEqual(arrays["intensity"].shape, (3, 4, 5))
                self.assertEqual(arrays["display_color"].shape, (3, 4, 5, 3))
                self.assertTrue(np.all(np.isfinite(arrays["density"])))
            metadata = json.loads((result.assets_path / "scene.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["entries"][0]["source"]["snapshot_id"], snapshot.snapshot_id)
            self.assertEqual(metadata["project"]["assets"]["directory"], "结果 中文.assets")
            self.assertIn("Append", (result.assets_path / "README.txt").read_text(encoding="utf-8"))
            self.assertEqual(list(Path(directory).glob(".结果 中文.bandscope-*.tmp")), [])

    def test_external_failure_preserves_existing_output_and_cleans_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            final = Path(directory) / "scene.blend"
            assets = assets_path_for(final)
            assets.mkdir()
            final.write_bytes(b"old-blend")
            (assets / "volume.vdb").write_bytes(b"old-vdb")
            with patch("bandscope.exporting.blender_export.probe_blender", return_value={}), \
                    patch.object(BlenderExportJob, "_run_process", side_effect=BlenderProcessError("conversion failed")):
                with self.assertRaisesRegex(BlenderProcessError, "conversion failed"):
                    BlenderExportJob(make_blender_snapshot(), "blender.exe", final, overwrite=True).run()
            self.assertEqual(final.read_bytes(), b"old-blend")
            self.assertEqual((assets / "volume.vdb").read_bytes(), b"old-vdb")
            self.assertEqual(list(Path(directory).glob(".scene.bandscope-*.tmp")), [])

    def test_capability_failure_happens_before_preparing_arrays(self):
        with tempfile.TemporaryDirectory() as directory:
            final = Path(directory) / "scene.blend"
            with patch("bandscope.exporting.blender_export.probe_blender", side_effect=BlenderCapabilityError("missing VDB")), \
                    patch("bandscope.exporting.blender_export.prepare_blender_scene") as prepare:
                with self.assertRaises(BlenderCapabilityError):
                    BlenderExportJob(make_blender_snapshot(), "blender.exe", final).run()
            prepare.assert_not_called()
            self.assertFalse(final.exists())

    def test_cancel_before_start_and_during_launch_preserves_output(self):
        with tempfile.TemporaryDirectory() as directory:
            final = Path(directory) / "scene.blend"
            cancelled = threading.Event()
            cancelled.set()
            with patch("bandscope.exporting.blender_export.probe_blender") as probe:
                with self.assertRaises(BlenderExportCancelled):
                    BlenderExportJob(make_blender_snapshot(), "blender.exe", final, cancel_event=cancelled).run()
            probe.assert_not_called()
            self.assertFalse(final.exists())

            active = threading.Event()

            def cancel_at_launch(percent, _stage):
                if percent >= 25:
                    active.set()

            with patch("bandscope.exporting.blender_export.probe_blender", return_value={}), \
                    patch.object(BlenderExportJob, "_run_process") as process:
                with self.assertRaises(BlenderExportCancelled):
                    BlenderExportJob(
                        make_blender_snapshot(), "blender.exe", final,
                        cancel_event=active, progress=cancel_at_launch,
                    ).run()
            process.assert_not_called()
            self.assertFalse(final.exists())

    def test_existing_output_is_rejected_before_external_process(self):
        with tempfile.TemporaryDirectory() as directory:
            final = Path(directory) / "scene.blend"
            final.write_bytes(b"old")
            with patch("bandscope.exporting.blender_export.probe_blender") as probe:
                with self.assertRaises(BlenderOutputExistsError):
                    BlenderExportJob(make_blender_snapshot(), "blender.exe", final).run()
            probe.assert_not_called()
            self.assertEqual(final.read_bytes(), b"old")

    def test_usage_text_explains_append_and_asset_retention(self):
        metadata = make_blender_snapshot()
        scene = __import__("bandscope.exporting.blender_scene", fromlist=["prepare_blender_scene"]).prepare_blender_scene(metadata)
        text = usage_text(scene.metadata(), "scene.assets")
        self.assertIn("Append", text)
        self.assertIn("不会复制", text)
        self.assertIn("scene.assets", text)
        self.assertIn("不是毫米", text)
        self.assertIn("辅助盒框", text)
        self.assertIn("Guide frame", text)
        self.assertIn("三轴方向标", text)
        self.assertIn("Orientation marker · publication", text)
        self.assertIn("封闭圆锥", text)
        self.assertIn("六方位布局", text)
        self.assertIn("左侧（−X）", text)
        self.assertIn("后侧（+Y）", text)
        self.assertIn("上方（+Z）", text)
        self.assertIn("每次导出都会生成", text)


if __name__ == "__main__":
    unittest.main()
