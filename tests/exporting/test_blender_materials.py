"""八材质注册表、统一薄板预设与布局的合成测试；不依赖 Blender。"""
import math
import unittest

import numpy as np

from bandscope.exporting.blender_materials import (
    BOX_LAYOUTS, DEFAULT_BOX_LAYOUT, MATERIAL_IDS, MATERIAL_SPECS,
    BlenderMaterialError, layout_spec, material_spec, resolve_parameters,
    resolved_samples,
)
from bandscope.exporting.blender_project import (
    BOX_COLOR, BOX_GROUP_PRESET, BOX_PRESETS, BOX_THICKNESS, PRESET_KEYS,
    BlenderProjectError, BlenderProjectRequest, make_project_metadata,
)
from bandscope.exporting.blender_scene import prepare_blender_scene
from tests.support.blender import make_blender_snapshot

EXPECTED_IDS = (
    "flat_emission", "matte", "satin", "holographic",
    "data_translucent", "transmission_filter", "reflection_glass", "optical_glass",
)
EXPECTED_LIGHTS = {"matte", "satin", "reflection_glass", "optical_glass"}


def _project(request):
    items = []
    for entry in request.entries:
        metadata = prepare_blender_scene(entry.snapshot).metadata()
        metadata["entry_id"] = entry.entry_id
        items.append((entry, metadata))
    return make_project_metadata(items, request, "demo.assets")


def _flat_settings(**overrides):
    settings = {key: "flat_emission" for key in BOX_PRESETS}
    settings.update(overrides)
    return settings


class BlenderMaterialRegistryTests(unittest.TestCase):
    def test_eight_materials_have_distinct_labels_and_valid_defaults(self):
        self.assertEqual(MATERIAL_IDS, EXPECTED_IDS)
        self.assertEqual(len(set(MATERIAL_IDS)), 8)
        labels = [spec.label for spec in MATERIAL_SPECS]
        self.assertEqual(len(set(labels)), 8)
        for spec in MATERIAL_SPECS:
            self.assertTrue(spec.summary and spec.limitations)
            self.assertTrue(spec.node_contract)
            self.assertGreaterEqual(spec.recommended_samples, 32)
            for name, value in spec.parameters.items():
                low, high = spec.parameter_range(name)
                self.assertGreaterEqual(value, low, f"{spec.material_id}.{name}")
                self.assertLessEqual(value, high, f"{spec.material_id}.{name}")
                self.assertTrue(math.isfinite(value))
            for control in spec.controls:
                self.assertIn(control, spec.parameters)
                self.assertIn(control, ("opacity", "emission_compensation", "filter_strength"))

    def test_lighting_and_emission_contracts_match_the_plan(self):
        lit = {spec.material_id for spec in MATERIAL_SPECS if spec.requires_light}
        self.assertEqual(lit, EXPECTED_LIGHTS)
        emitting = {spec.material_id for spec in MATERIAL_SPECS if spec.emits_by_design}
        self.assertEqual(emitting, {"flat_emission", "holographic"})
        overlap = {spec.material_id for spec in MATERIAL_SPECS if spec.prefers_data_aligned}
        self.assertEqual(overlap, {"data_translucent", "transmission_filter"})
        experimental = {spec.material_id for spec in MATERIAL_SPECS if spec.experimental}
        self.assertEqual(experimental, {"data_translucent"})

    def test_flat_emission_keeps_existing_default_opacity_and_strength(self):
        spec = material_spec("flat_emission")
        self.assertEqual(spec.parameters["opacity"], 0.30)
        self.assertEqual(spec.parameters["emission_strength"], 1.0)
        self.assertFalse(spec.requires_light)
        self.assertEqual(spec.controls, ("opacity",))

    def test_materials_without_opacity_do_not_expose_it(self):
        self.assertNotIn("opacity", material_spec("data_translucent").parameters)
        self.assertNotIn("opacity", material_spec("transmission_filter").parameters)
        self.assertNotIn("opacity", material_spec("reflection_glass").parameters)
        self.assertNotIn("opacity", material_spec("optical_glass").parameters)
        self.assertEqual(material_spec("transmission_filter").parameters["filter_strength"], 0.0)
        self.assertEqual(material_spec("matte").parameters["emission_compensation"], 0.0)

    def test_unknown_material_layout_and_parameter_values_are_rejected(self):
        with self.assertRaisesRegex(BlenderMaterialError, "未知薄板材质"):
            material_spec("plasma")
        with self.assertRaisesRegex(BlenderMaterialError, "未知薄板布局"):
            layout_spec("floating")
        for value in (float("nan"), float("inf"), -0.1, 1.5):
            with self.subTest(value=value), self.assertRaises(BlenderMaterialError):
                resolve_parameters("flat_emission", {"opacity": value})
        with self.assertRaisesRegex(BlenderMaterialError, "不支持参数"):
            resolve_parameters("flat_emission", {"filter_strength": 0.5})
        with self.assertRaisesRegex(BlenderMaterialError, "布尔"):
            resolve_parameters("flat_emission", {"opacity": True})

    def test_all_layouts_have_labels_and_descriptions(self):
        self.assertEqual(
            set(BOX_LAYOUTS), {"right_front_groups", "separated_right", "data_aligned"},
        )
        self.assertEqual(DEFAULT_BOX_LAYOUT, "right_front_groups")
        for key, record in BOX_LAYOUTS.items():
            self.assertTrue(record["label"])
            self.assertTrue(record["description"])
            self.assertEqual(layout_spec(key)["label"], record["label"])

    def test_recommended_samples_uses_the_most_demanding_material(self):
        self.assertEqual(resolved_samples(["flat_emission"]), 32)
        self.assertEqual(
            resolved_samples(["flat_emission", "optical_glass"]),
            material_spec("optical_glass").recommended_samples,
        )


class BlenderProjectRequestTests(unittest.TestCase):
    def test_default_request_contains_each_material_once_with_default_parameters(self):
        request = BlenderProjectRequest([make_blender_snapshot()])
        self.assertEqual(set(request.presets), set(PRESET_KEYS))
        self.assertTrue(all(request.presets.values()))
        self.assertEqual(request.box_layout, "right_front_groups")
        self.assertEqual(tuple(request.boxes), EXPECTED_IDS)
        self.assertEqual(len(request.enabled_boxes()), 8)
        for key in BOX_PRESETS:
            box = request.boxes[key]
            self.assertEqual(box.material_id, key)
            self.assertEqual(box.parameters, material_spec(key).default_parameters())
        self.assertEqual(request.recommended_samples, 128)

    def test_boxes_are_independent_and_input_mutation_does_not_freeze_late(self):
        materials = _flat_settings(
            matte="matte",
            satin={"material": "satin", "parameters": {"opacity": 0.25}},
            optical_glass={"material": "optical_glass", "parameters": {"ior": 1.6}},
        )
        request = BlenderProjectRequest(
            [make_blender_snapshot()], box_settings=materials, box_layout="data_aligned",
        )
        self.assertEqual(request.box_layout, "data_aligned")
        self.assertEqual(request.boxes["matte"].material_id, "matte")
        self.assertEqual(request.boxes["satin"].parameters["opacity"], 0.25)
        self.assertEqual(request.boxes["optical_glass"].parameters["ior"], 1.6)
        self.assertNotEqual(
            request.boxes["matte"].parameters, request.boxes["satin"].parameters,
        )
        self.assertEqual(request.recommended_samples, 128)
        materials["matte"] = "holographic"
        materials["satin"]["parameters"]["opacity"] = 0.9
        self.assertEqual(request.boxes["matte"].material_id, "matte")
        self.assertEqual(request.boxes["satin"].parameters["opacity"], 0.25)
        settings = request.box_settings()
        settings["matte"]["parameters"]["opacity"] = 0.9
        self.assertEqual(request.boxes["matte"].parameters["opacity"], 0.5)
        with self.assertRaises(TypeError):
            request.boxes["matte"].parameters["opacity"] = 0.9

    def test_invalid_box_configuration_is_rejected(self):
        snapshot = make_blender_snapshot()
        cases = (
            ({"flat_emission": "unknown"}, {}),
            ({"matte": {"material": "matte", "parameters": {"ior": 1.5}}}, {}),
            ({"flat_emission": {"material": "flat_emission", "parameters": {"opacity": 2.0}}}, {}),
            ({"unknown_box": "matte"}, {}),
            ({"orange_box": "matte"}, {}),
            ({"matte": "matte"}, {"box_layout": "diagonal"}),
        )
        for settings, extra in cases:
            with self.subTest(settings=settings, extra=extra), self.assertRaises(BlenderProjectError):
                BlenderProjectRequest([snapshot], box_settings=settings, **extra)

    def test_disabling_unified_preset_disables_all_eight_boxes(self):
        presets = {key: True for key in PRESET_KEYS}
        presets[BOX_GROUP_PRESET] = False
        request = BlenderProjectRequest([make_blender_snapshot()], presets)
        self.assertEqual(request.enabled_boxes(), [])


class BlenderBoxLayoutTests(unittest.TestCase):
    def _scene(self, request):
        scene = prepare_blender_scene(request.entries[0].snapshot).metadata()
        scene["entry_id"] = "entry-001"
        return scene

    def test_default_layout_splits_lit_boxes_to_front_with_default_parameters(self):
        request = BlenderProjectRequest([make_blender_snapshot()])
        scene = self._scene(request)
        metadata = _project(request)
        boxes = metadata["entries"][0]["box_presets"]
        self.assertEqual(len(boxes), 8)
        self.assertEqual(BOX_COLOR, (1.0, 0.35, 0.05))
        group_indices = {"right": 0, "front": 0}
        for index, box in enumerate(boxes):
            key = box["preset"]
            slot = "front" if key in EXPECTED_LIGHTS else "right"
            group_index = group_indices[slot]
            slot_center = scene["layout"]["slots"][slot]
            height_offset = 0.175 if slot == "front" else 0.0
            self.assertEqual(key, EXPECTED_IDS[index])
            self.assertEqual(box["material_id"], key)
            self.assertEqual(box["parameters"], material_spec(key).default_parameters())
            self.assertEqual(box["color"], list(BOX_COLOR))
            self.assertTrue(box["object"].startswith("BandScope orange "))
            self.assertTrue(box["material"].startswith("BandScope orange "))
            self.assertEqual(box["preset_group"], BOX_GROUP_PRESET)
            self.assertEqual(box["layout_slot"], slot)
            self.assertEqual(box["group_height_offset"], height_offset)
            self.assertAlmostEqual(box["center"][0], slot_center[0])
            self.assertAlmostEqual(box["center"][1], slot_center[1])
            self.assertAlmostEqual(
                box["center"][2],
                slot_center[2] + height_offset + (group_index - 1.5) * 0.35,
            )
            self.assertEqual(box["layout"], "right_front_groups")
            self.assertEqual(box["thickness"], BOX_THICKNESS)
            self.assertFalse(box["intersects_data"])
            self.assertEqual(box["layer_center_spacing"], 0.35)
            group_indices[slot] += 1
        self.assertEqual(group_indices, {"right": 4, "front": 4})
        right_z = [box["center"][2] for box in boxes if box["layout_slot"] == "right"]
        front_z = [box["center"][2] for box in boxes if box["layout_slot"] == "front"]
        for right, front in zip(right_z, front_z):
            self.assertAlmostEqual(front - right, 0.175)
        interleaved_z = sorted(right_z + front_z)
        for first, second in zip(interleaved_z, interleaved_z[1:]):
            self.assertAlmostEqual(second - first, 0.175)
        guide = scene["guide_frame"]["bounds"]
        self.assertAlmostEqual(boxes[0]["size"][0], guide[1] - guide[0])
        self.assertAlmostEqual(boxes[0]["size"][1], guide[3] - guide[2])
        self.assertAlmostEqual(boxes[0]["size"][2], BOX_THICKNESS)

    def test_internal_right_layout_remains_available_for_acceptance(self):
        request = BlenderProjectRequest(
            [make_blender_snapshot()], box_layout="separated_right",
        )
        scene = self._scene(request)
        boxes = _project(request)["entries"][0]["box_presets"]
        right = scene["layout"]["slots"]["right"]
        for index, box in enumerate(boxes):
            self.assertEqual(box["layout_slot"], "right")
            self.assertAlmostEqual(box["center"][0], right[0])
            self.assertAlmostEqual(box["center"][1], right[1])
            self.assertAlmostEqual(box["center"][2], right[2] + (index - 3.5) * 0.35)

    def test_data_aligned_uses_data_center_and_e_range_without_shrinking_thickness(self):
        request = BlenderProjectRequest(
            [make_blender_snapshot()], box_layout="data_aligned",
        )
        scene = self._scene(request)
        data = np.asarray(scene["blender_grid"]["material_clip_bounds"], dtype=float).reshape(3, 2)
        metadata = _project(request)
        boxes = metadata["entries"][0]["box_presets"]
        centers = [box["center"] for box in boxes]
        self.assertEqual(len(boxes), 8)
        for box in boxes:
            self.assertAlmostEqual(box["center"][0], float(data[0].mean()))
            self.assertAlmostEqual(box["center"][1], float(data[1].mean()))
            self.assertGreaterEqual(box["center"][2], float(data[2, 0]) - 1e-9)
            self.assertLessEqual(box["center"][2], float(data[2, 1]) + 1e-9)
            self.assertEqual(box["size"][2], BOX_THICKNESS)
            self.assertTrue(box["intersects_data"])
            self.assertEqual(box["layout"], "data_aligned")
        for first, second in zip(centers, centers[1:]):
            self.assertLess(first[2], second[2])
        span = float(data[2, 1] - data[2, 0])
        self.assertAlmostEqual(boxes[0]["layer_center_spacing"], span / 8.0)
        self.assertFalse(boxes[0]["layers_overlap"])

    def test_data_aligned_keeps_entry_local_bounds_and_multi_page_layout(self):
        request = BlenderProjectRequest(
            [make_blender_snapshot(), make_blender_snapshot()], box_layout="data_aligned",
        )
        metadata = _project(request)
        first, second = (metadata["project"]["layout"]["entries"][key]
                         for key in ("entry-001", "entry-002"))
        self.assertLess(first["world_bounds"][1], second["world_bounds"][0])
        data = np.asarray(
            metadata["entries"][0]["blender_grid"]["material_clip_bounds"], dtype=float,
        ).reshape(3, 2)
        for box in metadata["entries"][0]["box_presets"]:
            low = np.asarray(box["center"]) - np.asarray(box["size"]) * 0.5
            high = np.asarray(box["center"]) + np.asarray(box["size"]) * 0.5
            self.assertTrue(np.all(low >= data[:, 0] - 1e-9))
            self.assertTrue(np.all(high <= data[:, 1] + 1e-9))

    def test_project_and_entry_records_describe_default_materials_layout_and_lights(self):
        metadata = _project(BlenderProjectRequest([make_blender_snapshot()]))
        project = metadata["project"]
        self.assertEqual(project["box_layout"]["id"], "right_front_groups")
        self.assertEqual(tuple(project["box_materials"]), EXPECTED_IDS)
        self.assertEqual(project["render"]["samples"], 128)
        lighting = metadata["entries"][0]["box_lighting"]
        self.assertTrue(lighting["enabled"])
        self.assertEqual({record["preset"] for record in lighting["lights"]}, EXPECTED_LIGHTS)
        self.assertTrue(all(record["preset_group"] == BOX_GROUP_PRESET for record in lighting["lights"]))
        self.assertEqual(project["box_lighting"]["link_method"], "cycles_receiver_collection")
        for box in metadata["entries"][0]["box_presets"]:
            key = box["preset"]
            self.assertEqual(box["material_id"], project["box_materials"][key]["material_id"])
            self.assertEqual(box["parameters"], project["box_materials"][key]["parameters"])
            self.assertEqual(box["recommended_samples"], project["box_materials"][key]["recommended_samples"])
            self.assertEqual(box["color"], list(BOX_COLOR))
        self.assertEqual(len({box["object"] for box in metadata["entries"][0]["box_presets"]}), 8)
        self.assertEqual(len({box["material"] for box in metadata["entries"][0]["box_presets"]}), 8)

    def test_flat_emission_only_project_creates_no_light_group(self):
        request = BlenderProjectRequest(
            [make_blender_snapshot()], box_settings=_flat_settings(),
        )
        metadata = _project(request)
        lighting = metadata["entries"][0]["box_lighting"]
        self.assertFalse(lighting["enabled"])
        self.assertEqual(lighting["lights"], [])
        self.assertFalse(metadata["project"]["box_lighting"]["enabled"])

    def test_all_presets_off_creates_no_objects_lights_or_extra_bounds(self):
        presets = {key: False for key in PRESET_KEYS}
        request = BlenderProjectRequest([make_blender_snapshot()], presets)
        metadata = _project(request)
        self.assertFalse(any(metadata["project"]["presets"].values()))
        entry = metadata["entries"][0]
        self.assertFalse(entry["box_lighting"]["enabled"])
        self.assertEqual(entry["box_lighting"]["lights"], [])
        self.assertFalse(metadata["project"]["box_lighting"]["enabled"])
        layout = metadata["project"]["layout"]["entries"]["entry-001"]
        np.testing.assert_allclose(
            layout["local_bounds"], entry["blender_grid"]["material_clip_bounds"], atol=1e-9,
        )


if __name__ == "__main__":
    unittest.main()
