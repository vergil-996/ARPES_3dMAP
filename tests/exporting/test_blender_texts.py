import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from bandscope.exporting.blender_jobs import _validate_text_report
from bandscope.exporting.blender_export import BlenderProcessError
from bandscope.exporting.blender_project import (
    BLENDER_PROJECT_SCHEMA, PRESET_KEYS, TEXT_GROUP_PRESET, BlenderProjectError,
    BlenderProjectRequest, load_blender_project, make_project_metadata, merged_project_metadata,
    prepare_scene_for_update, update_snapshot_display,
)
from bandscope.exporting.blender_scene import prepare_blender_scene
from bandscope.exporting.blender_texts import (
    TEXT_BODY, TEXT_CAP_HEIGHT, TEXT_FONT_FILES, TEXT_PRESETS, TEXT_STYLES,
    BlenderTextError, stage_text_fonts, text_style,
)
from tests.support.blender import (
    make_blender_snapshot, project_report_texts, write_fake_blender_project,
)


EXPECTED_IDS = (
    "sans_regular", "sans_bold", "serif_regular", "serif_italic", "monospace_regular",
    "extruded_bold", "outline", "emissive", "metallic", "glass",
)


def _project(request):
    items = []
    for entry in request.entries:
        scene = prepare_blender_scene(entry.snapshot).metadata()
        scene["entry_id"] = entry.entry_id
        items.append((entry, scene))
    return make_project_metadata(items, request, "demo.assets")


class BlenderTextRegistryTests(unittest.TestCase):
    def test_ten_styles_cover_agreed_fonts_and_effects(self):
        self.assertEqual(TEXT_PRESETS, EXPECTED_IDS)
        self.assertEqual(len({style.label for style in TEXT_STYLES}), 10)
        self.assertEqual(TEXT_BODY, "A")
        self.assertEqual(TEXT_CAP_HEIGHT, 0.12)
        self.assertEqual(len(TEXT_FONT_FILES), 5)
        self.assertEqual(text_style("outline").fill_mode, "NONE")
        self.assertEqual(text_style("metallic").metallic, 1.0)
        self.assertEqual(text_style("glass").kind, "glass")
        self.assertGreater(text_style("emissive").emission_strength, 1.0)
        with self.assertRaisesRegex(BlenderTextError, "未知文本预设"):
            text_style("handwritten")

    def test_default_project_has_ten_small_editable_text_records(self):
        request = BlenderProjectRequest([make_blender_snapshot(), make_blender_snapshot()])
        metadata = _project(request)
        self.assertEqual(metadata["schema_version"], BLENDER_PROJECT_SCHEMA)
        self.assertTrue(request.presets[TEXT_GROUP_PRESET])
        self.assertEqual(len(request.enabled_texts()), 10)
        self.assertEqual(len(metadata["project"]["text_fonts"]["files"]), 5)
        for scene in metadata["entries"]:
            layout = scene["text_layout"]
            self.assertTrue(layout["enabled"])
            self.assertEqual(layout["layout_slot"], "bottom")
            self.assertEqual(layout["center"], scene["layout"]["slots"]["bottom"])
            self.assertEqual((layout["columns"], layout["rows"]), (5, 2))
            self.assertEqual(scene["text_lighting"]["lights"][0]["energy"], 45.0)
            for index, record in enumerate(scene["text_presets"]):
                self.assertEqual(record["body"], "A")
                self.assertEqual(record["preset_group"], TEXT_GROUP_PRESET)
                self.assertEqual((record["row"], record["column"]), divmod(index, 5))
                self.assertTrue(np.all(np.isfinite(record["center"])))
                self.assertTrue(record["independent_material"])
        first, second = metadata["entries"]
        self.assertTrue({item["object"] for item in first["text_presets"]}.isdisjoint(
            item["object"] for item in second["text_presets"]
        ))
        self.assertTrue({item["material"] for item in first["text_presets"]}.isdisjoint(
            item["material"] for item in second["text_presets"]
        ))

    def test_disabling_one_group_disables_all_texts_fonts_and_lights(self):
        presets = {key: True for key in PRESET_KEYS}
        presets[TEXT_GROUP_PRESET] = False
        request = BlenderProjectRequest([make_blender_snapshot()], presets)
        metadata = _project(request)
        self.assertEqual(request.enabled_texts(), [])
        self.assertEqual(metadata["project"]["text_fonts"]["files"], [])
        self.assertIsNone(metadata["project"]["text_fonts"]["license"])
        self.assertFalse(metadata["entries"][0]["text_layout"]["enabled"])
        self.assertFalse(metadata["entries"][0]["text_lighting"]["enabled"])

    def test_font_staging_copies_real_resources_and_license(self):
        with tempfile.TemporaryDirectory() as directory:
            record = stage_text_fonts(directory)
            for relative in (*record["files"], record["license"]):
                path = Path(directory).joinpath(*relative.split("/"))
                self.assertTrue(path.is_file(), relative)
                self.assertGreater(path.stat().st_size, 0)


class BlenderTextReportTests(unittest.TestCase):
    def test_reopen_report_checks_body_size_font_and_group_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            _blend, metadata = write_fake_blender_project(directory)
            scene = metadata["entries"][0]
            records, lights = project_report_texts(metadata, scene["entry_id"])
            report = {"text_presets": records, "text_lights": lights}
            _validate_text_report(report, scene, metadata["project"], metadata["project"]["presets"])
            for field, value, expected in (
                ("body", "a", "对象、字体或材质"),
                ("font_packed", False, "对象、字体或材质"),
                ("cap_height", 0.3, "字号或位置"),
                ("fill_mode", "NONE", "字体路径或排版"),
            ):
                with self.subTest(field=field):
                    mutated = copy.deepcopy(report)
                    mutated["text_presets"][0][field] = value
                    with self.assertRaisesRegex(BlenderProcessError, expected):
                        _validate_text_report(
                            mutated, scene, metadata["project"], metadata["project"]["presets"],
                        )
            presets = dict(metadata["project"]["presets"])
            presets[TEXT_GROUP_PRESET] = False
            with self.assertRaisesRegex(BlenderProcessError, "勾选不一致"):
                _validate_text_report(report, scene, metadata["project"], presets)


class BlenderTextCompatibilityTests(unittest.TestCase):
    def test_missing_font_and_modified_body_are_rejected_on_read(self):
        with tempfile.TemporaryDirectory() as directory:
            blend, metadata = write_fake_blender_project(directory)
            assets = blend.parent / f"{blend.stem}.assets"
            (assets / metadata["project"]["text_fonts"]["files"][0]).unlink()
            with self.assertRaisesRegex(BlenderProjectError, "字体"):
                load_blender_project(blend)

            blend, metadata = write_fake_blender_project(directory, name="body")
            metadata["entries"][0]["text_presets"][0]["body"] = "a"
            (blend.parent / "body.assets/scene.json").write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8",
            )
            with self.assertRaisesRegex(BlenderProjectError, "大写 A"):
                load_blender_project(blend)

    def test_schema_8_keeps_box_support_version_and_absent_texts_on_update(self):
        with tempfile.TemporaryDirectory() as directory:
            blend, metadata = write_fake_blender_project(directory)
            assets = blend.parent / f"{blend.stem}.assets"
            legacy = copy.deepcopy(metadata)
            legacy["schema_version"] = 8
            for key in ("text_styles", "text_fonts", "text_lighting"):
                legacy["project"].pop(key)
            legacy["project"]["presets"].pop(TEXT_GROUP_PRESET)
            for entry in legacy["entries"]:
                entry["schema_version"] = 8
                for key in ("text_layout", "text_presets", "text_lighting"):
                    entry.pop(key)
            (assets / "scene.json").write_text(json.dumps(legacy), encoding="utf-8")
            project = load_blender_project(blend)
            self.assertTrue(project.supports_box_materials)
            self.assertFalse(project.supports_text_presets)
            self.assertTrue(any("schema 8" in warning for warning in project.warnings))
            snapshot = project.entries[0].snapshot
            update_snapshot_display(
                snapshot, cmap_name="viridis", levels_params=(0.0, 50.0, 100.0),
                locked_range=None, include_zero=False, opacity_mode="linear",
            )
            generated = prepare_scene_for_update(snapshot).metadata()
            merged = merged_project_metadata(project, {"entry-001": generated})
            self.assertEqual(merged["schema_version"], 8)
            self.assertNotIn("text_styles", merged["project"])
            self.assertNotIn("text_presets", merged["entries"][0])


if __name__ == "__main__":
    unittest.main()
