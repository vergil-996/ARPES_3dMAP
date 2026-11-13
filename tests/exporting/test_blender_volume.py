import copy
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from bandscope.exporting.blender_project import (
    PRESET_KEYS, BlenderProjectError, BlenderProjectRequest, load_blender_project,
    make_project_metadata, merged_project_metadata, prepare_scene_for_update,
)
from bandscope.exporting.blender_scene import prepare_blender_scene
from bandscope.exporting.blender_volume import (
    BlenderVolumeError, resolve_volume_shading, volume_shading_coefficients,
)
from tests.support.blender import make_blender_snapshot, write_fake_blender_project


class BlenderVolumeCoefficientTests(unittest.TestCase):
    def test_emission_mode_preserves_original_absorption_and_emission(self):
        sigma = np.array([0.0, 0.25, 2.0])
        gray = np.array([0.0, 0.4, 1.0])
        color = np.repeat(gray[:, None], 3, axis=1)
        result = volume_shading_coefficients(sigma, color)
        np.testing.assert_allclose(
            result["absorption"], np.repeat(sigma[:, None], 3, axis=1), rtol=0.0, atol=0.0,
        )
        np.testing.assert_array_equal(result["scatter"], np.zeros((3, 3)))
        np.testing.assert_allclose(result["emission"], sigma[:, None] * color)
        for key in ("absorption", "scatter", "emission"):
            self.assertTrue(np.all(result[key] >= 0.0))

    def test_hybrid_rgb_and_boundary_colors_conserve_extinction_without_fog(self):
        sigma = np.array([[0.0, 0.3], [1.7, 2.0]])
        color = np.array([
            [[0.0, 0.25, 1.0], [1.0, 1.5, 0.5]],
            [[0.2, 0.0, 2.0], [0.8, 0.4, 0.1]],
        ])
        result = volume_shading_coefficients(sigma, color, "hybrid_lit")
        omega = 0.65 * np.clip(color, 0.0, 1.0)
        np.testing.assert_allclose(result["scatter"], sigma[..., None] * omega)
        np.testing.assert_allclose(result["absorption"], sigma[..., None] * (1.0 - omega))
        np.testing.assert_allclose(result["emission"], 0.15 * sigma[..., None] * color)
        np.testing.assert_allclose(
            result["absorption"] + result["scatter"],
            np.broadcast_to(sigma[..., None], sigma.shape + (3,)), rtol=0.0, atol=1.0e-12,
        )
        for key in ("absorption", "scatter", "emission"):
            self.assertTrue(np.all(result[key] >= 0.0))
        for key in ("absorption", "scatter", "emission"):
            np.testing.assert_array_equal(result[key][0, 0], np.zeros(3))

    def test_density_multiplier_and_color_adjustment_match_node_reference(self):
        result = volume_shading_coefficients(
            np.array([0.5]), np.array([[0.2, 0.8, 1.4]]), "hybrid_lit",
            {
                "density_multiplier": 2.0, "color_adjustment": (0.5, 2.0, 1.0),
                "scattering_amount": 0.4, "emission_compensation": 0.2, "anisotropy": 0.0,
            },
        )
        adjusted = np.array([[0.1, 1.6, 1.4]])
        omega = 0.4 * np.clip(adjusted, 0.0, 1.0)
        np.testing.assert_allclose(result["sigma"], [1.0])
        np.testing.assert_allclose(result["scatter"], omega)
        np.testing.assert_allclose(result["absorption"], 1.0 - omega)
        np.testing.assert_allclose(result["emission"], 0.2 * adjusted)

    def test_invalid_modes_and_unphysical_parameters_are_rejected(self):
        with self.assertRaisesRegex(BlenderProjectError, "未知数据体外观"):
            BlenderProjectRequest([make_blender_snapshot()], volume_mode="pure_scatter")
        for parameters in (
            {"scattering_amount": -0.01}, {"scattering_amount": 1.01},
            {"emission_compensation": -0.1}, {"density_multiplier": -1.0},
            {"color_adjustment": (1.0, -1.0, 1.0)}, {"anisotropy": 2.0},
        ):
            with self.subTest(parameters=parameters), self.assertRaises(BlenderVolumeError):
                resolve_volume_shading("hybrid_lit", parameters)


class BlenderVolumeProjectTests(unittest.TestCase):
    @staticmethod
    def _metadata(request):
        items = []
        for entry in request.entries:
            scene = prepare_blender_scene(entry.snapshot).metadata()
            scene["entry_id"] = entry.entry_id
            items.append((entry, scene))
        return make_project_metadata(items, request, "demo.assets")

    def test_default_request_and_hybrid_render_contract(self):
        snapshot = make_blender_snapshot()
        default = BlenderProjectRequest([snapshot])
        self.assertEqual(default.volume_mode, "emission")
        self.assertEqual(default.volume_shading["parameters"]["emission_compensation"], 1.0)
        default_metadata = self._metadata(default)
        self.assertEqual(default_metadata["project"]["render"]["volume_bounces"], 0)
        hybrid = BlenderProjectRequest(
            [snapshot], {key: False for key in PRESET_KEYS}, volume_mode="hybrid_lit",
        )
        self.assertGreaterEqual(hybrid.recommended_samples, 128)
        metadata = self._metadata(hybrid)
        self.assertEqual(metadata["schema_version"], 11)
        self.assertEqual(metadata["project"]["render"]["samples"], 128)
        self.assertEqual(metadata["project"]["render"]["volume_bounces"], 2)
        self.assertEqual(metadata["project"]["volume_shading"]["mode"], "hybrid_lit")
        self.assertTrue(all(not entry["volume_lighting"]["enabled"]
                            for entry in default_metadata["entries"]))
        self.assertTrue(all(entry["volume_lighting"]["enabled"] for entry in metadata["entries"]))

    def test_volume_mode_does_not_change_scientific_arrays(self):
        snapshot = make_blender_snapshot()
        results = []
        for mode in ("emission", "hybrid_lit"):
            request = BlenderProjectRequest([snapshot], volume_mode=mode)
            scene = prepare_blender_scene(request.entries[0].snapshot)
            results.append(scene)
        for name in ("intensity", "display_color", "alpha", "density"):
            np.testing.assert_array_equal(getattr(results[0], name), getattr(results[1], name))

    def test_three_lights_use_camera_basis_local_positions_and_expected_powers(self):
        request = BlenderProjectRequest([make_blender_snapshot()], volume_mode="hybrid_lit")
        metadata = self._metadata(request)
        entry = metadata["entries"][0]
        lighting = entry["volume_lighting"]
        bounds = np.asarray(entry["blender_grid"]["material_clip_bounds"]).reshape(3, 2)
        center = bounds.mean(axis=1)
        length = max(float(np.max(bounds[:, 1] - bounds[:, 0])), 0.1)
        distance = 2.0 * length
        basis = [np.asarray(value) for value in entry["camera"]["rotation_basis_columns"]]
        expected_directions = {
            "key": (-1.0, 1.0, 1.5), "fill": (1.0, 0.3, 1.0), "rim": (0.5, 1.0, -1.0),
        }
        key_energy = 60.0 * distance * distance
        for light in lighting["lights"]:
            components = expected_directions[light["role"]]
            direction = sum(axis * value for axis, value in zip(basis, components))
            direction /= np.linalg.norm(direction)
            np.testing.assert_allclose(light["position"], center + direction * distance)
            np.testing.assert_allclose(light["target"], center)
            self.assertEqual(light["size"], length)
            self.assertEqual(light["color"], [1.0, 1.0, 1.0])
        self.assertAlmostEqual(lighting["lights"][0]["energy"], key_energy)
        self.assertAlmostEqual(lighting["lights"][1]["energy"], key_energy * 0.25)
        self.assertAlmostEqual(lighting["lights"][2]["energy"], key_energy * 0.5)
        self.assertFalse(lighting["camera_bound"])
        self.assertTrue(lighting["excluded_from_layout"])

    def test_hybrid_roundtrip_and_update_preserve_shading_and_lighting(self):
        with tempfile.TemporaryDirectory() as directory:
            blend, metadata = write_fake_blender_project(directory, volume_mode="hybrid_lit")
            project = load_blender_project(blend)
            self.assertEqual(project.schema_version, 11)
            self.assertEqual(project.volume_shading["mode"], "hybrid_lit")
            self.assertEqual(len(project.metadata["entries"][0]["volume_lighting"]["lights"]), 3)
            original = copy.deepcopy(project.metadata["entries"][0]["volume_lighting"])
            original["lights"][0]["energy"] = 12345.0
            original["lights"][0]["position"] = [4.0, 5.0, 6.0]
            project.metadata["entries"][0]["volume_lighting"] = original
            custom_shading = copy.deepcopy(project.metadata["project"]["volume_shading"])
            custom_shading["parameters"]["scattering_amount"] = 0.4
            project.metadata["project"]["volume_shading"] = custom_shading
            generated = prepare_scene_for_update(project.entries[0].snapshot).metadata()
            merged = merged_project_metadata(project, {"entry-001": generated})
            self.assertEqual(merged["schema_version"], 11)
            self.assertEqual(merged["project"]["volume_shading"], custom_shading)
            self.assertEqual(merged["entries"][0]["volume_lighting"], original)
            self.assertNotEqual(
                merged["entries"][0]["volume_lighting"], metadata["entries"][0]["volume_lighting"],
            )

    def test_schema_7_through_10_default_to_emission_and_never_upgrade_on_update(self):
        for version in range(7, 11):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                blend, metadata = write_fake_blender_project(directory)
                legacy = copy.deepcopy(metadata)
                legacy["schema_version"] = version
                legacy["project"].pop("volume_shading")
                for entry in legacy["entries"]:
                    entry["schema_version"] = version
                    entry.pop("volume_lighting")
                assets = blend.parent / f"{blend.stem}.assets"
                (assets / "scene.json").write_text(json.dumps(legacy), encoding="utf-8")
                project = load_blender_project(blend)
                self.assertEqual(project.schema_version, version)
                self.assertEqual(project.volume_shading["mode"], "emission")
                generated = prepare_scene_for_update(project.entries[0].snapshot).metadata()
                merged = merged_project_metadata(project, {"entry-001": generated})
                self.assertEqual(merged["schema_version"], version)
                self.assertNotIn("volume_shading", merged["project"])
                self.assertNotIn("volume_lighting", merged["entries"][0])


if __name__ == "__main__":
    unittest.main()
