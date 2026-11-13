"""Real-window Blender companion export, update and standalone restore acceptance."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=REPO_ROOT / ".local/data/WSe2_step.npz")
    parser.add_argument("--blender", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / ".local/outputs/blender-recipe-acceptance")
    args = parser.parse_args()
    if not args.data.is_file():
        parser.error(f"数据不存在：{args.data}")
    if not args.blender.is_file():
        parser.error(f"Blender 不存在：{args.blender}")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.environ["QT_QPA_PLATFORM"] = "windows"
    from tests.support.environment import isolate_runtime
    isolate_runtime()
    from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path
    configure_qt_high_dpi()
    configure_qt_plugin_path()
    from PyQt5.QtCore import QCoreApplication, Qt
    from PyQt5.QtWidgets import QApplication
    import numpy as np
    import bandscope.app.refactored_app as module
    from bandscope.core.blender_recipe import BlenderRecipeDocument
    from bandscope.exporting.blender_project import load_blender_project
    from bandscope.exporting.publication_export import capture_snapshot

    class NoUpdates:
        def __init__(self, *args, **kwargs):
            pass

        def check_automatically(self):
            pass

        def shutdown(self, **kwargs):
            pass

    module.UpdateController = NoUpdates
    app = QApplication(sys.argv)
    report = {
        "success": False,
        "data": str(args.data.resolve()),
        "blender": str(args.blender.resolve()),
        "checks": [],
        "native_file_dialog_manual_review": "not_run",
    }
    windows = []
    messages = []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        report["checks"].append(name)
        print(f"PASS {name}", flush=True)

    def wait_until(predicate, timeout=300):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError("等待真实窗口任务超时")
            QCoreApplication.processEvents()
            time.sleep(0.05)
        QCoreApplication.processEvents()

    def exact(window):
        return bool(window._render_exact_ready and window.current_render_context is not None)

    def make_window():
        window = module.My3DAnalyzer()
        window._save_splitter_sizes = lambda *_args: None
        window._show_message = lambda *args: messages.append(args)
        window._toast_success = lambda *_args: None
        window.show()
        windows.append(window)
        return window

    try:
        window = make_window()
        window.load_data(str(args.data.resolve()))
        wait_until(lambda: exact(window))
        window.on_apply_time_integral()
        wait_until(lambda: exact(window))
        check("真实 WSe₂ 同源两页准备", len(window.left_workspace.page_specs) == 2)

        check("打开 Blender 导出面板", window.open_blender_project_dialog())
        dialog = window._blender_project_dialog
        panel = dialog.export_panel
        for index in range(panel.page_tree.topLevelItemCount()):
            panel.page_tree.topLevelItem(index).setCheckState(0, Qt.Checked)
        check("两页均被勾选", len(panel.selected_keys()) == 2)
        check("默认保存配套复现文件", panel.save_recipe_check.isChecked())
        panel.preset_checks["guide_frame"].setChecked(False)
        panel.preset_checks["thin_boxes"].setChecked(False)
        panel.preset_checks["text_presets"].setChecked(False)
        panel.path_edit.setText(str(args.blender.resolve()))
        frozen = []
        freeze = panel._freeze_selected

        def retain_snapshots(cancel_event, selections=None):
            snapshots = freeze(cancel_event, selections)
            frozen.extend(snapshots)
            return snapshots

        panel._freeze_selected = retain_snapshots
        blend = output / "WSe2-recipe.blend"
        sidecar = Path(str(blend) + ".bsview.json")
        dialog.grab().save(str(output / "export-dialog.png"))
        check("开始真实 Blender 导出", panel.start_export(blend, overwrite=True))
        wait_until(lambda: not panel.busy)
        check("Blender 工程、资产和配置同时生成", all((
            blend.is_file(),
            (output / "WSe2-recipe.assets/scene.json").is_file(),
            sidecar.is_file(),
        )))
        check("冻结两个科学快照", len(frozen) == 2)
        original_document = BlenderRecipeDocument.load(sidecar).data
        check("同组两页只记录一个来源会话", len(original_document["groups"]) == 1)
        check("条目顺序完整", [entry["entry_id"] for entry in original_document["entries"]] == ["entry-001", "entry-002"])
        check("配置不包含原始 NPZ 路径", str(args.data.resolve()) not in sidecar.read_text(encoding="utf-8"))
        loaded = load_blender_project(blend)
        check("真实工程回读接受配套配置", loaded.recipe == original_document)
        blend_digest = hashlib.sha256(blend.read_bytes()).hexdigest()

        check("打开真实工程进行原位编辑", dialog.open_existing(blend))
        editor = dialog.editor
        editor.level_spins[0].setValue(11.0)
        check("显示修改进入更新队列", "entry-001" in editor._dirty_ids)
        check("开始真实原位更新", editor.start_update())
        wait_until(lambda: not editor.busy)
        updated_document = BlenderRecipeDocument.load(sidecar).data
        check("原位更新不改写 .blend", hashlib.sha256(blend.read_bytes()).hexdigest() == blend_digest)
        check("原位更新保留原科学配方", all(
            updated["recipe"] == original["recipe"]
            for updated, original in zip(updated_document["entries"], original_document["entries"])
        ))
        override = updated_document["entries"][0]["display_override"]
        check("显示覆写记录新参数和效果移除", (
            override["levels"][0] == 11.0
            and "local_enhancement" in override["removed_effects"]
            and "opacity_multiplier" in override["removed_effects"]
        ))

        standalone_dir = output / "standalone"
        standalone_dir.mkdir(exist_ok=True)
        standalone = standalone_dir / sidecar.name
        shutil.copyfile(sidecar, standalone)
        restored = make_window()
        selections = []

        def relocate(source):
            selections.append(source["sha256"])
            return str(args.data.resolve())

        check("独立配置整体恢复", restored._load_blender_recipe(
            standalone, confirm=False, relocate=relocate,
        ))
        wait_until(lambda: exact(restored))
        check("同组两页只主动选择一次 NPZ", len(selections) == 1)
        check("恢复为一个新数据源和两页", (
            len(restored.source_manager) == 1 and len(restored.left_workspace.page_specs) == 2
        ))
        restored_dialog = restored._blender_project_dialog
        check("恢复原导出页面选择", len(restored_dialog.export_panel.selected_keys()) == 2)
        check("恢复 Blender 导出设置", (
            not restored_dialog.export_panel.preset_checks["guide_frame"].isChecked()
            and not restored_dialog.export_panel.preset_checks["thin_boxes"].isChecked()
            and not restored_dialog.export_panel.preset_checks["text_presets"].isChecked()
        ))
        check("首条导出页面被选中", restored.active_page_spec.page_kind == "home")
        first_after = capture_snapshot(restored)
        check("首页科学数值往返一致", np.allclose(
            first_after.payload["volume"], frozen[0].payload["volume"], equal_nan=True,
        ))
        check("首页相机往返一致", np.allclose(first_after.camera["position"], frozen[0].camera["position"]))
        check("原位显示覆写已恢复", first_after.levels_params[0] == 11.0)
        restored_dialog.export_panel.grab().save(str(output / "restored-export-panel.png"))
        second_id = restored_dialog.export_panel.selected_keys()[1][1]
        restored.left_workspace.activate_page(second_id)
        wait_until(lambda: exact(restored))
        second_after = capture_snapshot(restored)
        check("第二页科学数值往返一致", np.allclose(
            second_after.payload["volume"], frozen[1].payload["volume"], equal_nan=True,
        ))
        check("第二页相机往返一致", np.allclose(second_after.camera["position"], frozen[1].camera["position"]))
        restored.plotter.render()
        QCoreApplication.processEvents()
        time.sleep(0.4)
        QCoreApplication.processEvents()
        pixels = restored.plotter.screenshot(str(output / "restored-3d-scene.png"), return_img=True)
        check("恢复后的原生 VTK 场景非空", float(np.std(pixels[..., :3])) > 1.0)
        restored.grab().save(str(output / "restored-window.png"))
        report["selected_source_sha256"] = selections
        report["standalone_recipe"] = str(standalone)
        report["success"] = True
        code = 0
    except Exception:
        report["error"] = traceback.format_exc()
        report["messages"] = [list(map(str, item)) for item in messages]
        print(report["error"], flush=True)
        code = 1
    finally:
        report["messages"] = [list(map(str, item)) for item in messages]
        (output / "acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        for window in reversed(windows):
            try:
                dialog = window.__dict__.get("_blender_project_dialog")
                if dialog is not None and not dialog.busy:
                    dialog.reject()
                window.close()
            except Exception:
                pass
        QCoreApplication.processEvents()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
