"""Native-window recipe round trip using real data; output stays under .local/."""
import faulthandler
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
faulthandler.enable()

from scripts.validation._common import arguments


def main():
    args = arguments("view_recipe", default_data=".local/data/WSe2_step.npz")
    from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path
    configure_qt_high_dpi()
    configure_qt_plugin_path()
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication
    import numpy as np
    import bandscope.app.refactored_app as module
    from bandscope.exporting.publication_export import capture_snapshot
    from bandscope.exporting.publication_models import OutputOptions, resolve_style
    from bandscope.exporting.recipe_export import export_pair

    class NoUpdates:
        def __init__(self, *a, **kw):
            pass
        def check_automatically(self):
            pass
        def shutdown(self, **kw):
            pass

    module.UpdateController = NoUpdates
    app = QApplication(sys.argv)
    window = module.My3DAnalyzer()
    window.show()
    report = {"data": str(args.data.resolve()), "checks": [], "visual_review": "pending"}
    frozen = {}

    def finish(code=0):
        (args.output_dir / "acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        window.close()
        app.exit(code)

    def step(work):
        try:
            work()
        except Exception:
            report["error"] = traceback.format_exc()
            print(report["error"], flush=True)
            finish(1)

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        report["checks"].append(name)
        print(f"PASS {name}", flush=True)

    def wait(work, predicate=None):
        deadline = time.monotonic() + 90
        def poll():
            ready = predicate() if predicate else (window._render_exact_ready and
                    window.current_render_context is not None)
            if ready:
                QTimer.singleShot(400, lambda: step(work))
            elif time.monotonic() > deadline:
                step(lambda: check("等待精确结果", False))
            else:
                QTimer.singleShot(150, poll)
        QTimer.singleShot(150, poll)

    def load():
        window.load_data(str(args.data))
        wait(setup_3d)

    def setup_3d():
        window.timeline_bar.slider_time.setValue(min(3, window.core.raw_data.shape[3] - 1))
        window.page_render.s_low.setValue(8)
        window.page_render.s_gamma.setValue(58)
        window.page_render.s_up.setValue(96)
        window.plotter.camera.focal_point = (110, 90, 100)
        window.plotter.camera.position = (450, -250, 330)
        window.plotter.reset_camera_clipping_range()
        window._capture_3d_camera_position()
        wait(export_3d)

    def export_3d():
        snap = capture_snapshot(window)
        check("3D 配方捕获", snap.view_recipe is not None)
        frozen["3d"] = snap
        path = args.output_dir / "view_3d.png"
        export_pair(snap, resolve_style("3d", "3d_boxed"), {},
                    OutputOptions(dpi=150), str(path))
        window._load_view_recipe(
            str(path) + ".bsview.json", confirm=False, relocate=lambda _: str(args.data)
        )
        wait(verify_3d)

    def verify_3d():
        before = frozen["3d"]
        after = capture_snapshot(window)
        check("3D 数值往返", np.allclose(before.payload["volume"], after.payload["volume"], equal_nan=True))
        check("相机观察中心往返", np.allclose(before.camera["focal_point"], after.camera["focal_point"]))
        check("相机位置往返", np.allclose(before.camera["position"], after.camera["position"]))
        check("黑白灰场往返", before.levels_params == after.levels_params)
        pixels = window.plotter.screenshot(str(args.output_dir / "restored_3d_scene.png"), return_img=True)
        check("3D 场景非空", float(np.std(pixels[..., :3])) > 1.0)
        window.grab().save(str(args.output_dir / "restored_3d_window.png"))
        window.page_data.combo_ax.setCurrentIndex(1)
        window.on_apply_axis_integral()
        wait(export_2d)

    def export_2d():
        snap = capture_snapshot(window)
        check("二维积分视图", snap.view == "2d")
        check("来源树分支", len(snap.view_recipe["pages"]) >= 2)
        frozen["2d"] = snap
        path = args.output_dir / "view_2d.png"
        export_pair(snap, resolve_style("2d", "2d_boxed"), {"title_text": "Recipe validation"},
                    OutputOptions(dpi=150), str(path))
        window._load_view_recipe(
            str(path) + ".bsview.json", confirm=False, relocate=lambda _: str(args.data)
        )
        wait(verify_2d)

    def verify_2d():
        after = capture_snapshot(window)
        check("2D 数值往返", np.allclose(frozen["2d"].payload["image"], after.payload["image"], equal_nan=True))
        # 复现文件现在作为新数据源打开，页面 ID 会重新分配：按标题核对目标页。
        check("目标树节点恢复", window.active_page_spec.title == frozen["2d"].source_page_title)
        window.grab().save(str(args.output_dir / "restored_2d_window.png"))
        window.open_publication_dialog()
        dialog = window._publication_dialog
        check("默认保存复现文件", dialog.chk_view_recipe.isChecked())
        check("出版标题草稿恢复", dialog._draft_overrides.get("title_text") == "Recipe validation")
        wait(preview_ready, lambda: dialog._big_ready)

    def preview_ready():
        window._publication_dialog.grab().save(str(args.output_dir / "restored_export_dialog.png"))
        report["automation"] = "passed"
        finish()

    QTimer.singleShot(800, lambda: step(load))
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
