import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from matplotlib import colormaps
from PyQt5.QtWidgets import QApplication

from bandscope.core.opacity_curve import insert_opacity_point
from bandscope.core.display_style import (
    legacy_display_style,
    new_display_style,
    new_render_quality,
    sanitized_legacy_opacity,
)
from bandscope.exporting.blender_project import (
    BlenderProjectError,
    load_blender_project,
    merge_updated_entry,
    update_snapshot_display,
)
from bandscope.exporting.blender_project_dialog import BlenderProjectEditor
from bandscope.exporting.blender_scene import ALPHA_CAP, prepare_blender_scene
from bandscope.processing.display_transfer import (
    channels,
    display_level_info,
    opacity_reference_distance,
)
from tests.support.blender import make_blender_snapshot, write_fake_blender_project

CURVE = [[0.0, 0.0], [0.15, 0.45], [0.6, 0.9], [1.0, 1.0]]


def independent_snapshot(points=None):
    snapshot = make_blender_snapshot()
    style = new_display_style()
    style["opacity"] = {"points": [list(point) for point in (points or CURVE)]}
    volume = snapshot.payload["volume"]
    volume.flat[0] = 100.0
    info = display_level_info(volume, style)
    snapshot.levels_params = style["levels"]
    snapshot.payload.update(
        level_info=info,
        display={"background_style": style, "resolved_range": (0.0, 1.0), "state": None},
        render_quality=new_render_quality(),
    )
    return snapshot


def downgrade_to_schema_12(metadata):
    legacy = json.loads(json.dumps(metadata))
    legacy["schema_version"] = 12
    legacy["project"]["display_edit_format"] = 3
    for entry in legacy["entries"]:
        entry["schema_version"] = 12
        edit = entry["bandscope_edit"]
        edit["format_version"] = 3
        edit["capabilities"] = [
            name for name in edit.get("capabilities", []) if name != "opacity_interpolation"
        ]
        edit["display_style"]["opacity"].pop("interpolation", None)
        edit["display_style"]["opacity"].pop("segments", None)
        entry["opacity"]["independent_parameters"].pop("interpolation", None)
        entry["opacity"]["independent_parameters"].pop("segments", None)
    return legacy


def downgrade_to_schema_11(metadata):
    """把新生成工程改成 schema 11（四参数、显示编辑格式 2）。"""
    legacy = json.loads(json.dumps(metadata))
    legacy["schema_version"] = 11
    legacy["project"]["display_edit_format"] = 2
    for entry in legacy["entries"]:
        entry["schema_version"] = 11
        edit = entry["bandscope_edit"]
        edit["format_version"] = 2
        edit["capabilities"] = [
            name for name in edit.get("capabilities", []) if name != "opacity_points"
        ]
        edit["display_style"]["opacity"] = {
            "threshold": 10.0, "transition": 20.0, "gamma": 1.7, "density": 30.0,
        }
        entry["opacity"]["independent_parameters"] = dict(edit["display_style"]["opacity"])
    return legacy


class IndependentBlenderSceneTests(unittest.TestCase):
    def test_density_and_schema_13_metadata_match_shared_channels(self):
        snapshot = independent_snapshot()
        volume = snapshot.payload["volume"]
        original = volume.copy()
        scene = prepare_blender_scene(snapshot)
        unit = opacity_reference_distance(snapshot.payload["full_shape"])
        with np.errstate(divide="ignore"):
            _color, alpha = channels(
                volume, snapshot.payload["level_info"], "linear",
                style=snapshot.payload["display"]["background_style"], unit_distance=unit,
            )
        np.testing.assert_array_equal(scene.alpha, alpha)
        expected = -np.log1p(-np.minimum(alpha, ALPHA_CAP)) / np.float32(scene.unit_distance_blender)
        np.testing.assert_allclose(scene.density, expected, rtol=1e-6, atol=1e-7)
        self.assertTrue(np.all(np.isfinite(scene.density)))
        metadata = scene.metadata()
        self.assertEqual(metadata["schema_version"], 13)
        self.assertEqual(metadata["bandscope_edit"]["format_version"], 4)
        self.assertEqual(metadata["bandscope_edit"]["display_style"]["opacity"]["points"], CURVE)
        self.assertEqual(metadata["bandscope_edit"]["render_quality"]["interpolation"], "linear")
        self.assertIn("opacity_points", metadata["bandscope_edit"]["capabilities"])
        self.assertEqual(metadata["bandscope_edit"]["resolved_range"], [
            snapshot.payload["level_info"]["data_min"], snapshot.payload["level_info"]["data_max"],
        ])
        self.assertEqual(metadata["opacity"]["unit_distance_display"], unit)
        np.testing.assert_array_equal(volume, original)

    def test_alpha_one_becomes_finite_extinction(self):
        volume = np.array([[[0.0]], [[1.0]]], dtype=np.float32)
        snapshot = make_blender_snapshot(volume=volume, cmap_name="gray")
        style = new_display_style()
        style["opacity"] = {"points": [[0.0, 0.0], [1.0, 1.0]]}
        info = display_level_info(volume, style)
        snapshot.payload.update(
            level_info=info,
            display={"background_style": style, "resolved_range": (0.0, 1.0), "state": None},
        )
        scene = prepare_blender_scene(snapshot)
        self.assertEqual(float(scene.alpha[0, 0, 0]), 0.0)
        self.assertAlmostEqual(float(scene.alpha[-1, 0, 0]), 1.0, places=6)
        self.assertTrue(np.all(np.isfinite(scene.density)))
        self.assertGreater(float(scene.density[-1, 0, 0]), 0.0)

    def test_blender_colors_use_selected_palette_interval_without_changing_alpha(self):
        volume = np.array([[[0.0]], [[0.5]], [[1.0]]], dtype=np.float32)
        snapshot = make_blender_snapshot(volume=volume, cmap_name="gray")
        style = legacy_display_style((0, 50, 100), "linear", (0.2, 0.8))
        info = display_level_info(volume, style)
        snapshot.payload.update(
            level_info=info,
            display={"background_style": style, "resolved_range": (0.0, 1.0), "state": None},
        )
        scene = prepare_blender_scene(snapshot)
        srgb = colormaps["gray"]([0.2, 0.5, 0.8])[:, :3]
        expected = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
        np.testing.assert_allclose(scene.display_color.reshape(3, 3), expected, rtol=1e-6, atol=1e-7)
        np.testing.assert_allclose(scene.alpha.reshape(-1), [0.0, 0.5, 1.0], rtol=1e-6, atol=1e-7)
        metadata = scene.metadata()
        self.assertEqual(tuple(metadata["bandscope_edit"]["display_style"]["colormap_range"]), (0.2, 0.8))

    def test_schema_13_roundtrip_and_editor_restore_points(self):
        with tempfile.TemporaryDirectory() as root:
            blend, _metadata = write_fake_blender_project(root, [independent_snapshot()])
            project = load_blender_project(blend)
            self.assertTrue(project.supports_opacity_points)
            snapshot = project.entries[0].snapshot
            self.assertEqual(snapshot.payload["display"]["background_style"]["opacity"]["points"], CURVE)
            self.assertEqual(snapshot.payload["render_quality"]["mode"], "explicit")
            update_snapshot_display(
                snapshot, cmap_name="magma", levels_params=(0, 50, 100), locked_range=None,
                include_zero=False, opacity_mode="linear",
                display_style=snapshot.payload["display"]["background_style"],
                render_quality=snapshot.payload["render_quality"],
            )
            self.assertEqual(snapshot.payload["display"]["resolved_range"], (
                snapshot.payload["level_info"]["data_min"], snapshot.payload["level_info"]["data_max"],
            ))
            self.assertIsNotNone(prepare_blender_scene(snapshot))

            app = QApplication.instance() or QApplication([])
            editor = BlenderProjectEditor(SimpleNamespace(settings=None, _show_message=Mock()))

            def dispose():
                editor.close_preview()
                editor.close()
                editor.deleteLater()
                app.processEvents()
            self.addCleanup(dispose)
            editor.load_project(blend)
            self.assertFalse(hasattr(editor, "normalization_combo"))
            self.assertFalse(hasattr(editor, "manual_low"))
            self.assertFalse(hasattr(editor, "manual_high"))
            self.assertEqual(editor.opacity_curve.points(), CURVE)
            self.assertTrue(editor.opacity_curve.isVisibleTo(editor))
            self.assertFalse(editor.opacity_spins["density"].isVisibleTo(editor))
            editor.opacity_curve.set_points([[0.0, 0.0], [0.3, 0.2], [1.0, 1.0]])
            editor._on_display_changed()
            self.assertEqual(
                editor.current_snapshot.payload["display"]["background_style"]["opacity"]["points"],
                [[0.0, 0.0], [0.3, 0.2], [1.0, 1.0]],
            )
            self.assertIn("entry-001", editor._dirty_ids)

    def test_smooth_split_coefficients_drive_density_and_survive_project_roundtrip(self):
        snapshot = independent_snapshot()
        points, segments, _index = insert_opacity_point(
            CURVE, 0.35, interpolation="smooth",
        )
        style = snapshot.payload["display"]["background_style"]
        style["opacity"] = {
            "points": [list(point) for point in points],
            "interpolation": "smooth", "segments": segments,
        }
        scene = prepare_blender_scene(snapshot)
        unit = opacity_reference_distance(snapshot.payload["full_shape"])
        _color, alpha = channels(
            snapshot.payload["volume"], snapshot.payload["level_info"], "linear",
            style=style, unit_distance=unit,
        )
        np.testing.assert_array_equal(scene.alpha, alpha)
        self.assertEqual(scene.metadata()["schema_version"], 13)
        self.assertEqual(scene.metadata()["bandscope_edit"]["format_version"], 4)
        with tempfile.TemporaryDirectory() as root:
            blend, _metadata = write_fake_blender_project(root, [snapshot])
            project = load_blender_project(blend)
            self.assertTrue(project.supports_opacity_interpolation)
            restored = project.entries[0].snapshot.payload["display"]["background_style"]["opacity"]
            self.assertEqual(restored["interpolation"], "smooth")
            self.assertEqual(restored["segments"], segments)
            self.assertEqual(restored["points"], [list(point) for point in points])
            app = QApplication.instance() or QApplication([])
            editor = BlenderProjectEditor(SimpleNamespace(settings=None, _show_message=Mock()))

            def dispose():
                editor.close_preview()
                editor.close()
                editor.deleteLater()
                app.processEvents()
            self.addCleanup(dispose)
            editor.load_project(blend)
            self.assertEqual(editor.opacity_curve.opacity(), restored)
            self.assertTrue(editor.opacity_curve.interpolation_selector.isEnabled())
            editor.opacity_curve.interpolation_selector.buttons["linear"].click()
            editor.opacity_curve.interpolation_selector.buttons["smooth"].click()
            self.assertEqual(editor.opacity_curve.segments(), segments)

    def test_schema_12_project_restores_linear_and_keeps_format_3_on_update(self):
        with tempfile.TemporaryDirectory() as root:
            blend, metadata = write_fake_blender_project(root, [independent_snapshot()])
            legacy = downgrade_to_schema_12(metadata)
            assets = blend.parent / f"{blend.stem}.assets"
            (assets / "scene.json").write_text(json.dumps(legacy), encoding="utf-8")
            project = load_blender_project(blend)
            self.assertTrue(project.supports_opacity_points)
            self.assertFalse(project.supports_opacity_interpolation)
            snapshot = project.entries[0].snapshot
            style = snapshot.payload["display"]["background_style"]
            self.assertEqual(style["opacity"]["interpolation"], "linear")
            smooth = new_display_style()
            with self.assertRaisesRegex(BlenderProjectError, "schema 12"):
                update_snapshot_display(
                    snapshot, cmap_name="magma", levels_params=(0, 50, 100), locked_range=None,
                    include_zero=False, opacity_mode="linear", display_style=smooth,
                )
            update_snapshot_display(
                snapshot, cmap_name="magma", levels_params=(0, 50, 100), locked_range=None,
                include_zero=False, opacity_mode="linear", display_style=style,
            )
            merged = merge_updated_entry(
                project.metadata["entries"][0], prepare_blender_scene(snapshot).metadata(),
            )
            self.assertEqual(merged["bandscope_edit"]["format_version"], 3)
            self.assertNotIn("interpolation", merged["bandscope_edit"]["display_style"]["opacity"])
            self.assertNotIn("opacity_interpolation", merged["bandscope_edit"]["capabilities"])

    def test_schema_11_project_keeps_four_parameter_style_and_updates_in_place(self):
        with tempfile.TemporaryDirectory() as root:
            blend, metadata = write_fake_blender_project(root, [independent_snapshot()])
            legacy = downgrade_to_schema_11(metadata)
            assets = blend.parent / f"{blend.stem}.assets"
            (assets / "scene.json").write_text(json.dumps(legacy), encoding="utf-8")
            project = load_blender_project(blend)
            self.assertEqual(project.schema_version, 11)
            self.assertFalse(project.supports_opacity_points)
            snapshot = project.entries[0].snapshot
            style = snapshot.payload["display"]["background_style"]
            self.assertEqual(sanitized_legacy_opacity(style.get("opacity"))["density"], 30.0)
            self.assertNotIn("points", style["opacity"])
            with self.assertRaisesRegex(BlenderProjectError, "重新导出"):
                update_snapshot_display(
                    snapshot, cmap_name="magma", levels_params=(0, 50, 100), locked_range=None,
                    include_zero=False, opacity_mode="linear",
                    display_style=new_display_style(),
                )
            update_snapshot_display(
                snapshot, cmap_name="viridis", levels_params=(5, 60, 90), locked_range=None,
                include_zero=False, opacity_mode="linear",
            )
            self.assertIn("density", snapshot.payload["display"]["background_style"]["opacity"])
            merged = merge_updated_entry(
                project.metadata["entries"][0],
                prepare_blender_scene(snapshot).metadata(),
            )
            self.assertEqual(merged["bandscope_edit"]["format_version"], 2)
            self.assertNotIn("opacity_points", merged["bandscope_edit"]["capabilities"])
            self.assertIn("density", merged["bandscope_edit"]["display_style"]["opacity"])

    def test_old_restricted_project_relocks_to_frozen_full_range(self):
        snapshot = independent_snapshot()
        snapshot.locked_range = (0.0, 1.0)
        with tempfile.TemporaryDirectory() as root:
            blend, _metadata = write_fake_blender_project(root, [snapshot])
            metadata_path = blend.parent / f"{blend.stem}.assets" / "scene.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            edit = metadata["entries"][0]["bandscope_edit"]
            edit["display_style"]["normalization"] = {
                "mode": "manual", "percentiles": (0.0, 99.5), "manual_range": (0.0, 1.0),
            }
            edit["resolved_range"] = [0.0, 1.0]
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

            restored = load_blender_project(blend).entries[0].snapshot
            finite = restored.payload["volume"][np.isfinite(restored.payload["volume"])]
            expected = (float(finite.min()), float(finite.max()))
            self.assertEqual(restored.locked_range, expected)
            self.assertEqual(restored.payload["display"]["resolved_range"], expected)
            self.assertEqual(restored.payload["display"]["background_style"]["normalization"], {"mode": "full"})
            self.assertEqual(restored.payload["level_info"]["data_min"], expected[0])
            self.assertEqual(restored.payload["level_info"]["data_max"], expected[1])

    def test_legacy_update_cannot_enable_independent_display(self):
        snapshot = make_blender_snapshot()
        snapshot.payload["blender_project"] = {"display_schema": False}
        with self.assertRaisesRegex(BlenderProjectError, "schema 7"):
            update_snapshot_display(
                snapshot, cmap_name="magma", levels_params=(0, 50, 100), locked_range=None,
                include_zero=False, opacity_mode="linear", display_style=new_display_style(),
            )

    def test_legacy_merge_removes_format_2_fields_and_preserves_creation(self):
        original = prepare_blender_scene(make_blender_snapshot()).metadata()
        original["schema_version"] = 9
        original["text_layout"] = {"enabled": True, "custom": "keep"}
        generated = prepare_blender_scene(make_blender_snapshot()).metadata()
        generated["display_style"] = {"unexpected": True}
        merged = merge_updated_entry(original, generated)
        self.assertEqual(merged["schema_version"], 13)
        self.assertEqual(merged["text_layout"], original["text_layout"])
        self.assertEqual(merged["bandscope_edit"]["format_version"], 1)
        self.assertNotIn("display_style", merged["bandscope_edit"])
        self.assertNotIn("render_quality", merged["bandscope_edit"])
        self.assertNotIn("resolved_range", merged["bandscope_edit"])
        self.assertNotIn("independent_parameters", merged["opacity"])


if __name__ == "__main__":
    unittest.main()
