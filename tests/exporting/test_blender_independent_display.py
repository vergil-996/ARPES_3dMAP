import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.core.display_style import new_display_style, new_render_quality
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
    volume_display_unit_distance,
)
from tests.support.blender import make_blender_snapshot, write_fake_blender_project


def independent_snapshot():
    snapshot = make_blender_snapshot()
    style = new_display_style()
    style["normalization"].update(mode="manual", manual_range=(0.0, 1.0))
    style["opacity"].update(threshold=10.0, transition=20.0, gamma=1.7, density=30.0, preset="custom")
    volume = snapshot.payload["volume"]
    info = display_level_info(volume, style)
    snapshot.levels_params = style["levels"]
    snapshot.payload.update(
        level_info=info,
        display={"background_style": style, "resolved_range": (0.0, 1.0), "state": None},
        render_quality=new_render_quality(),
    )
    return snapshot


class IndependentBlenderSceneTests(unittest.TestCase):
    def test_density_and_schema_11_metadata_match_shared_channels(self):
        snapshot = independent_snapshot()
        volume = snapshot.payload["volume"]
        original = volume.copy()
        scene = prepare_blender_scene(snapshot)
        unit = volume_display_unit_distance(snapshot.payload["full_shape"])
        _color, alpha = channels(
            volume, snapshot.payload["level_info"], "linear",
            style=snapshot.payload["display"]["background_style"], unit_distance=unit,
        )
        np.testing.assert_array_equal(scene.alpha, alpha)
        expected = -np.log1p(-np.minimum(alpha, ALPHA_CAP)) / np.float32(scene.unit_distance_blender)
        np.testing.assert_allclose(scene.density, expected, rtol=1e-6, atol=1e-7)
        metadata = scene.metadata()
        self.assertEqual(metadata["schema_version"], 11)
        self.assertEqual(metadata["bandscope_edit"]["format_version"], 2)
        self.assertEqual(metadata["bandscope_edit"]["display_style"]["opacity"]["density"], 30.0)
        self.assertEqual(metadata["bandscope_edit"]["render_quality"]["interpolation"], "linear")
        self.assertEqual(metadata["bandscope_edit"]["resolved_range"], [0.0, 1.0])
        self.assertEqual(metadata["opacity"]["unit_distance_display"], unit)
        np.testing.assert_array_equal(volume, original)

    def test_schema_11_roundtrip_and_editor_restore_independent_controls(self):
        with tempfile.TemporaryDirectory() as root:
            blend, _metadata = write_fake_blender_project(root, [independent_snapshot()])
            project = load_blender_project(blend)
            snapshot = project.entries[0].snapshot
            self.assertTrue(project.supports_display_styles)
            self.assertEqual(snapshot.payload["display"]["background_style"]["opacity"]["gamma"], 1.7)
            self.assertEqual(snapshot.payload["render_quality"]["mode"], "explicit")
            update_snapshot_display(
                snapshot, cmap_name="magma", levels_params=(0, 50, 100), locked_range=None,
                include_zero=False, opacity_mode="linear",
                display_style=snapshot.payload["display"]["background_style"],
                render_quality=snapshot.payload["render_quality"],
            )
            self.assertEqual(snapshot.payload["display"]["resolved_range"], (0.0, 1.0))
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
            self.assertFalse(editor.normalization_combo.isHidden())
            self.assertAlmostEqual(editor.opacity_spins["density"].value(), 30.0)
            editor.opacity_spins["density"].setValue(33.0)
            self.assertEqual(editor.current_snapshot.payload["display"]["background_style"]["opacity"]["density"], 33.0)
            self.assertIn("entry-001", editor._dirty_ids)

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
        self.assertEqual(merged["schema_version"], 11)
        self.assertEqual(merged["text_layout"], original["text_layout"])
        self.assertEqual(merged["bandscope_edit"]["format_version"], 1)
        self.assertNotIn("display_style", merged["bandscope_edit"])
        self.assertNotIn("render_quality", merged["bandscope_edit"])
        self.assertNotIn("resolved_range", merged["bandscope_edit"])
        self.assertNotIn("independent_parameters", merged["opacity"])


if __name__ == "__main__":
    unittest.main()
