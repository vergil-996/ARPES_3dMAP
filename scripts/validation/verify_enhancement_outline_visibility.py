"""Native 2D/3D validation of selected-only enhancement outlines and menu toggle."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.validation._common import arguments


def main():
    args = arguments("enhancement_outline_visibility", default_data=".local/data/WSe2_step.npz")
    import json
    import traceback
    import numpy as np
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication, QMenu
    from bandscope.core.local_enhancement import update_region_bounds
    import bandscope.app.refactored_app as app_module

    class NoUpdates:
        def __init__(self, *args, **kwargs):
            pass

        def check_automatically(self):
            pass

        def shutdown(self, **kwargs):
            pass

    app_module.UpdateController = NoUpdates
    app = QApplication([])
    window = app_module.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    progress = dict(step=0, wait=0)
    checks = []

    def check(name, condition):
        assert condition, name
        checks.append(name)
        print(f"[PASS] {name}", flush=True)

    def outlines():
        if window.current_render_context["view"] == "3d":
            return set(window._enhancement_actor_regions.values())
        return {a.get_text() for a in window._enhancement_artists
                if hasattr(a, "get_text") and a.get_text().startswith("R")}

    def screenshot(name):
        app.processEvents()
        window.grab().save(str(args.output_dir / (name + "_window.png")))
        if window.current_render_context["view"] == "3d":
            window.plotter.screenshot(str(args.output_dir / (name + "_scene.png")))

    def toggle_all():
        before = window._enhancement_display(window.current_render_context)
        menu = QMenu(window)
        window._append_enhancement_actions(menu)
        action = next(a for a in menu.actions() if a.text() == "显示全部增强区域")
        check("right-click action is checkable", action.isCheckable() and action.isEnabled())
        action.trigger()
        check("outline toggle reuses enhancement channels", window._enhancement_display(window.current_render_context) is before)
        menu.deleteLater()

    def tick():
        try:
            if progress["wait"]:
                progress["wait"] -= 1
                return
            if not window._render_exact_ready:
                return
            if progress["step"] == 0:
                window._add_enhancement_region()
                update_region_bounds(window._enhancement_selected(), (10, 50, 20, 70, 30, 80))
                window._enhancement_refresh()
                progress["first"] = window._enhancement_selected()["region_id"]
                window._add_enhancement_region()
                update_region_bounds(window._enhancement_selected(), (80, 130, 75, 125, 100, 170))
                window._enhancement_refresh()
                progress["second"] = window._enhancement_selected()["region_id"]
                progress["science"] = window.core.raw_data[::25, ::25, ::25, :].copy()
            elif progress["step"] == 1:
                check("3D shows only selected second box", outlines() == {progress["second"]})
                screenshot("3d_selected")
                buffer = window.volume_session.display_channel.buffer.copy()
                window.page_render.region_combo.setCurrentIndex(1)
                check("3D dropdown switches visible box", outlines() == {progress["first"]})
                toggle_all()
                check("3D menu shows both boxes", outlines() == {progress["first"], progress["second"]})
                screenshot("3d_all")
                window.page_render.region_combo.setCurrentIndex(0)
                check("3D all boxes remain visible on background selection", len(outlines()) == 2)
                check("3D background selection removes editable widget", window._enhancement_box_widget is None)
                toggle_all()
                check("3D background selection with all off hides all annotations", outlines() == set() and not window._enhancement_actors)
                check("3D enhanced volume survives outline changes", np.array_equal(buffer, window.volume_session.display_channel.buffer))
                screenshot("3d_hidden")
                window.page_render.region_combo.setCurrentIndex(2)
                window.page_data.combo_ax.setCurrentIndex(1)
                window.page_data.s_ax_low.setValue(0)
                window.page_data.s_ax_up.setValue(window.page_data.s_ax_up.maximum())
                window.on_apply_axis_integral()
            else:
                check("2D inherits both enhancement effects", len(window._enhancement_display(window.current_render_context)["regions"]) == 2)
                check("2D shows only selected inherited rectangle", outlines() == {"R2"})
                screenshot("2d_selected")
                rgba = np.array(window.ax_2d._arpes_image.get_array(), copy=True)
                window.page_render.region_combo.setCurrentIndex(1)
                check("2D dropdown switches visible rectangle", outlines() == {"R1"})
                toggle_all()
                check("2D menu shows both rectangles", outlines() == {"R1", "R2"})
                screenshot("2d_all")
                window.page_render.region_combo.setCurrentIndex(0)
                check("2D background selection removes handles while all rectangles remain", window._enhancement_selector is None and outlines() == {"R1", "R2"})
                toggle_all()
                check("2D background selection with all off hides all annotations", outlines() == set() and not window._enhancement_artists)
                check("2D enhanced colors and opacity survive outline changes", np.array_equal(rgba, window.ax_2d._arpes_image.get_array()))
                screenshot("2d_hidden")
                check("scientific data unchanged", np.array_equal(progress["science"], window.core.raw_data[::25, ::25, ::25, :], equal_nan=True))
                (args.output_dir / "acceptance.json").write_text(json.dumps(dict(data=str(args.data.resolve()), checks=checks), ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[RESULT] {len(checks)} checks passed", flush=True)
                timer.stop()
                window.close()
                app.exit(0)
                return
            progress["step"] += 1
            progress["wait"] = 4
        except Exception:
            traceback.print_exc()
            timer.stop()
            window.close()
            app.exit(1)

    timer = QTimer()
    timer.timeout.connect(tick)
    window.load_data(str(args.data))
    timer.start(250)
    QTimer.singleShot(90000, lambda: app.exit(2))
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
