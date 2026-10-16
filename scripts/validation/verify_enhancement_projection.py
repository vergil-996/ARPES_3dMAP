"""Validate 3D-created regions in derived 2D pages on real ARPES data."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.validation._common import arguments


def main():
    args = arguments("enhancement_projection", default_data=".local/data/WSe2_step.npz")
    import copy
    import json
    import traceback
    from types import SimpleNamespace
    import numpy as np
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication
    from bandscope.core.local_enhancement import STATE_KEY, physical_bounds, region_slices, update_region_bounds
    from bandscope.processing.local_enhancement import finite_range
    from bandscope.exporting.publication_export import capture_snapshot, render_and_save
    from bandscope.exporting.publication_models import OutputOptions, resolve_style
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

    def apply_axis(axis):
        window.page_data.combo_ax.setCurrentIndex(axis)
        window.page_data.s_ax_low.setValue(0)
        window.page_data.s_ax_up.setValue(window.page_data.s_ax_up.maximum())
        window.on_apply_axis_integral()

    def tick():
        try:
            if progress["wait"]:
                progress["wait"] -= 1
                return
            if not window._render_exact_ready:
                return
            step = progress["step"]
            if step == 0:
                check("3D source ready", window.current_render_context["view"] == "3d")
                window._add_enhancement_region()
                update_region_bounds(window._enhancement_selected(), (10, 50, 20, 70, 30, 80))
                window._enhancement_refresh()
                window.page_render.s_gamma.setValue(65)
                window.page_render.combo_map.setCurrentIndex(3)
                window.flush_render_levels_refresh()
                progress["first_id"] = window._enhancement_selected()["region_id"]
                window._add_enhancement_region()
                update_region_bounds(window._enhancement_selected(), (80, 130, 75, 125, 100, 170))
                window._enhancement_refresh()
            elif step == 1:
                progress["source"] = window.left_workspace.current_spec()
                progress["state"] = copy.deepcopy(window._enhancement_state())
                progress["science"] = window.current_render_context["data"].copy()
                apply_axis(1)
            elif step == 2:
                context = window.current_render_context
                fields = window._enhancement_display(context)
                check("Y integral inherits both 3D regions", fields is not None and len(fields["regions"]) == 2 and fields["active"])
                check("2D enhancement uses two scalar crop layers", len(window.ax_2d._arpes_enhancement_images) == 2)
                check("dropdown contains background and inherited regions", window.page_render.region_combo.count() == 3)
                check("inherited rectangle has interactive handles", window._enhancement_selector is not None and window._enhancement_selector.get_visible())
                check("first region retains its independent style", fields["regions"][0]["style"] == progress["state"]["regions"][0]["style"])
                check("projection retains X and E bounds", fields["regions"][0]["bounds"] == (10, 50, 30, 80))
                for record in fields["regions"]:
                    check(record["label"] + " normalizes original 2D samples", record["data_range"] == finite_range(context["data"][region_slices(context, record)]))
                check("2D scientific result remains the raw volume sum", np.allclose(context["data"], progress["science"].sum(axis=1)))
                progress["bounds"] = tuple(window._enhancement_selected()["bounds"])
                window.grab().save(str(args.output_dir / "inherited_2d_window.png"))
                window.timeline_bar.switch_flip.setChecked(True)
            elif step == 3:
                check("E flip preserves inherited sample bounds", tuple(window._enhancement_selected()["bounds"]) == progress["bounds"])
                snapshot = capture_snapshot(window)
                check("publication includes both inherited regions", len(snapshot.payload["enhancement"]["regions"]) == 2)
                render_and_save(snapshot, resolve_style("2d", ""), {}, OutputOptions(width_mm=150, height_mm=110, dpi=120), str(args.output_dir / "inherited_2d_publication.png"))
                window.grab().save(str(args.output_dir / "inherited_2d_flipped_window.png"))
                bounds = physical_bounds(window.current_render_context, (85, 135, 105, 175), e_flip=True)
                window._on_enhancement_rectangle(SimpleNamespace(xdata=bounds[0], ydata=bounds[2]), SimpleNamespace(xdata=bounds[1], ydata=bounds[3]))
            elif step == 4:
                check("projected edit survives refresh", np.allclose(window._enhancement_selected()["bounds"], (85, 135, 105, 175)))
                check("editing 2D does not change source volume regions", progress["source"].params[STATE_KEY] == progress["state"])
                window._delete_enhancement_region(progress["first_id"])
            elif step == 5:
                context = window.current_render_context
                fields = window._enhancement_display(context)
                mask = np.ones(context["data"].shape, dtype=bool)
                mask[region_slices(context, fields["regions"][0])] = False
                info = fields["background_level_info"]
                check("deleted projection restores full-source background normalization", finite_range(context["data"]) == (info["data_min"], info["data_max"]))
                window._delete_enhancement_region(window._enhancement_selected()["region_id"])
            elif step == 6:
                check("last projected deletion leaves background-only dropdown", window.page_render.region_combo.count() == 1)
                check("last projected deletion restores scalar image", window.ax_2d._arpes_image.get_array().ndim == 2)
                check("2D deletions leave both 3D source regions intact", progress["source"].params[STATE_KEY] == progress["state"])
                window.left_workspace.activate_page(progress["source"].page_id)
            elif step == 7:
                check("returning to 3D restores both volume regions", len(window._enhancement_display(window.current_render_context)["regions"]) == 2)
                apply_axis(0)
            elif step == 8:
                fields = window._enhancement_display(window.current_render_context)
                check("X integral projects Y and E bounds", fields["regions"][0]["bounds"] == (20, 70, 30, 80))
                window.left_workspace.activate_page(progress["source"].page_id)
            elif step == 9:
                apply_axis(2)
            else:
                fields = window._enhancement_display(window.current_render_context)
                check("E integral projects X and Y bounds", fields["regions"][0]["bounds"] == (10, 50, 20, 70))
                check("source data remain unchanged", np.array_equal(window._compute_render_context(progress["source"])["data"], progress["science"], equal_nan=True))
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
    QTimer.singleShot(150000, lambda: app.exit(2))
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
