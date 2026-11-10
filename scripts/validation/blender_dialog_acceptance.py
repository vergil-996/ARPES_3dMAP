# -*- coding: utf-8 -*-
"""Real-window BandScope → Blender dialog acceptance with an isolated profile."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from bandscope.exporting.blender_materials import MATERIAL_SPECS  # noqa: E402
from bandscope.exporting.blender_project import BOX_GROUP_PRESET, PRESET_KEYS  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path, nargs="?", default=REPO_ROOT / ".local/data/WSe2_step.npz")
    parser.add_argument("--blender", required=True, type=Path)
    parser.add_argument("--multi", action="store_true", help="重复加载同一文件并跨两个数据源导出")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / ".local/outputs/blender-dialog-acceptance")
    args = parser.parse_args()
    if not args.data.is_file():
        parser.error(f"数据不存在：{args.data}")
    if not args.blender.is_file():
        parser.error(f"Blender 不存在：{args.blender}")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    from tests.support.environment import isolate_runtime
    isolate_runtime()
    from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path
    configure_qt_high_dpi()
    configure_qt_plugin_path()
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    import bandscope.app.refactored_app as refactored_app

    class NullUpdateController:
        def __init__(self, *args, **kwargs):
            pass

        def check_automatically(self):
            pass

        def shutdown(self, **kwargs):
            pass

    refactored_app.UpdateController = NullUpdateController
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    messages = []
    window._show_message = lambda *args: messages.append(args)
    window._toast_success = lambda *_args: None
    window.show()
    report = {"success": False, "data": str(args.data), "blender": str(args.blender)}
    dialog = None
    deadline = time.perf_counter() + 90

    def finish(success, detail=""):
        report["success"] = bool(success)
        report["detail"] = detail
        report["messages"] = [list(map(str, item)) for item in messages]
        if dialog is not None:
            report["selected_pages"] = [list(key) for key in dialog.export_panel.selected_keys()]
            report["panel_status"] = dialog.export_panel.status_label.text()
        (output / "dialog-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if dialog is not None and not dialog.busy:
            dialog.reject()
        window.close()
        app.quit()

    def start_export():
        nonlocal deadline
        try:
            panel = dialog.export_panel
            panel.path_edit.setText(str(args.blender.resolve()))
            dialog.show()
            dialog.raise_()
            blend = output / "WSe2-窗口导出.blend"
            if not panel.start_export(blend, overwrite=True):
                finish(False, "面板拒绝开始导出：" + panel.detection_label.text())
                return
            report["reference_has_pixmap"] = panel.reference_label.pixmap() is not None and not panel.reference_label.pixmap().isNull()
            if not report["reference_has_pixmap"]:
                finish(False, "冻结参考缩略图为空")
                return
            report["blend"] = str(blend)
            deadline = time.perf_counter() + 180
            poll_export()
        except Exception as exc:
            finish(False, f"{type(exc).__name__}: {exc}")

    def poll_export():
        panel = dialog.export_panel
        if not panel.busy:
            blend = Path(report["blend"])
            success = blend.is_file() and (blend.parent / f"{blend.stem}.assets/entries/entry-001/volume.vdb").is_file()
            if args.multi:
                success = success and (blend.parent / f"{blend.stem}.assets/entries/entry-002/volume.vdb").is_file()
                success = success and dialog.main_window.source_manager.active_id == report["selected_pages"][1][0]
            finish(success, panel.status_label.text())
        elif time.perf_counter() < deadline:
            QTimer.singleShot(300, poll_export)
        else:
            panel.cancel()
            finish(False, "等待 Blender 导出超时")

    def open_dialog():
        nonlocal dialog, deadline
        try:
            if not window.open_blender_project_dialog():
                finish(False, "无法打开独立 Blender 工程对话框")
                return
            dialog = window._blender_project_dialog
            if not dialog.export_panel.selected_keys():
                finish(False, "当前 3D 页面未被默认勾选")
                return
            if args.multi:
                for index in range(dialog.export_panel.page_tree.topLevelItemCount()):
                    dialog.export_panel.page_tree.topLevelItem(index).setCheckState(0, 2)
                if len(dialog.export_panel.selected_keys()) != 2:
                    finish(False, "跨源验收没有得到两个独立页面选择")
                    return
            panel = dialog.export_panel
            if (set(panel.preset_checks) != set(PRESET_KEYS)
                    or panel.preset_checks[BOX_GROUP_PRESET].text() != "薄层长方体"
                    or any(hasattr(panel, name) for name in (
                        "material_combos", "parameter_spins", "layout_combo", "material_note",
                    ))):
                finish(False, "薄层预设未合并，或材质与布局设置区仍存在")
                return
            report["thin_box_controls"] = {
                "preset": panel.preset_checks[BOX_GROUP_PRESET].text(),
                "checked": panel.preset_checks[BOX_GROUP_PRESET].isChecked(),
                "material_layout_group_removed": True,
            }
            report["selected_pages"] = [list(key) for key in dialog.export_panel.selected_keys()]
            deadline = time.perf_counter() + 180

            def capture_then_start():
                screenshot = output / "dialog.png"
                dialog.grab().save(str(screenshot))
                report["dialog_screenshot"] = str(screenshot)
                start_export()

            # 让窗口完成首次绘制和滚动区布局后再截图，否则 grab() 可能得到未绘制的画面。
            QTimer.singleShot(400, capture_then_start)
        except Exception as exc:
            finish(False, f"{type(exc).__name__}: {exc}")

    def poll_loaded():
        context = getattr(window, "current_render_context", None)
        if context and context.get("view") == "3d":
            open_dialog()
        elif time.perf_counter() < deadline:
            QTimer.singleShot(300, poll_loaded)
        else:
            finish(False, "等待真实 3D 渲染超时")

    window.load_data(str(args.data.resolve()))
    if args.multi:
        window.load_data(str(args.data.resolve()))
    QTimer.singleShot(300, poll_loaded)
    app.exec_()
    if not report.get("success"):
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 1

    inspection = output / "inspection.json"
    render = output / "render.png"
    inspector = REPO_ROOT / "scripts/validation/blender_scene_acceptance.py"
    report["inspector"] = str(inspector)
    command = [
        str(args.blender.resolve()), "--background", "--factory-startup", "--python",
        str(inspector), "--",
        "inspect", "--blend", report["blend"], "--report", str(inspection),
        "--long-edge", "512", "--samples", "16", "--render", str(render),
    ]
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", timeout=600)
    (REPO_ROOT / ".local/logs/blender-dialog-inspection.log").write_text(completed.stdout or "", encoding="utf-8")
    if completed.returncode != 0:
        print("\n".join((completed.stdout or "").splitlines()[-25:]), flush=True)
        return 1
    result = json.loads(inspection.read_text(encoding="utf-8"))
    if args.multi:
        multi_report = output / "multi-inspection.json"
        command = [
            str(args.blender.resolve()), "--background", "--factory-startup", "--python",
            str(REPO_ROOT / "scripts/validation/blender_project_scene_acceptance.py"), "--",
            "check", "--blend", report["blend"], "--report", str(multi_report),
        ]
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", timeout=600)
        (REPO_ROOT / ".local/logs/blender-dialog-multi-inspection.log").write_text(completed.stdout or "", encoding="utf-8")
        if completed.returncode != 0:
            print("\n".join((completed.stdout or "").splitlines()[-25:]), flush=True)
            return 1
        if json.loads(multi_report.read_text(encoding="utf-8"))["entry_count"] != 2:
            return 1
        report["multi_entry_roundtrip"] = True
    report["success"] = True
    report["render"] = str(render)
    blend = Path(report["blend"])
    metadata = json.loads((blend.parent / f"{blend.stem}.assets/scene.json").read_text(encoding="utf-8"))
    expected = {spec.material_id: spec.default_parameters() for spec in MATERIAL_SPECS}
    expected_slots = {
        spec.material_id: ("front" if spec.requires_light else "right") for spec in MATERIAL_SPECS
    }
    if metadata["project"]["box_layout"]["id"] != "right_front_groups":
        print("FAIL：真实窗口产物未使用右侧／前侧分组布局", flush=True)
        return 1
    for entry in metadata["entries"]:
        boxes = entry["box_presets"]
        if (len(boxes) != 8
                or {box["material_id"]: box["parameters"] for box in boxes} != expected
                or any(box["color"] != [1.0, 0.35, 0.05] for box in boxes)
                or any(box["preset_group"] != BOX_GROUP_PRESET for box in boxes)
                or any(box["layout_slot"] != expected_slots[box["material_id"]] for box in boxes)):
            print("FAIL：真实窗口产物不符合八块默认薄板及右侧／前侧分组契约", flush=True)
            return 1
        for box in boxes:
            slot = expected_slots[box["material_id"]]
            slot_center = entry["layout"]["slots"][slot]
            expected_offset = 0.175 if slot == "front" else 0.0
            if any(abs(box["center"][axis] - slot_center[axis]) > 1e-9 for axis in (0, 1)):
                print(f"FAIL：薄板 {box['preset']} 未位于预期方位", flush=True)
                return 1
            if abs(box["group_height_offset"] - expected_offset) > 1e-9:
                print(f"FAIL：薄板 {box['preset']} 的高度偏移不正确", flush=True)
                return 1
        right_z = sorted(box["center"][2] for box in boxes if box["layout_slot"] == "right")
        front_z = sorted(box["center"][2] for box in boxes if box["layout_slot"] == "front")
        if any(abs(front - right - 0.175) > 1e-9 for right, front in zip(right_z, front_z)):
            print("FAIL：两组薄板没有按半层高度错开", flush=True)
            return 1
    report["inspected_schema"] = metadata["schema_version"]
    report["inspected_layout"] = metadata["project"]["box_layout"]["id"]
    report["inspected_slots"] = expected_slots
    report["front_height_offset"] = 0.175
    report["inspected_materials"] = [
        [box["preset"], box["material_id"]]
        for entry in metadata["entries"] for box in entry["box_presets"]
    ]
    report["max_projection_error_pixels"] = result["max_projection_error_pixels"]
    (output / "dialog-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"PASS：真实窗口导出八块默认橙色薄板并由 Blender 重开渲染；"
        f"投影误差 {result['max_projection_error_pixels']:.6f} px",
        flush=True,
    )
    print(f"报告：{output / 'dialog-report.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
