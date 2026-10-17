"""Native-window validation of display regions on real ARPES data."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.validation._common import arguments


def main():
    args = arguments("local_enhancement", default_data=".local/data/WSe2_step.npz")
    import json
    import traceback
    import numpy as np
    from PyQt5.QtCore import QEvent, QPointF, Qt, QTimer
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtWidgets import QApplication
    from bandscope.core.local_enhancement import geometry, region_slices
    from bandscope.processing.local_enhancement import finite_range
    from bandscope.rendering.render_core import VisualEngine
    from bandscope.ui.enhancement_controls import SHOW_ALL_REGIONS_ID
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
    progress = {"step": 0, "wait": 0}
    checks = []

    def check(name, condition):
        assert condition, name
        checks.append(name)
        print(f"[PASS] {name}", flush=True)

    def screenshot(name):
        app.processEvents()
        window.grab().save(str(args.output_dir / f"{name}_window.png"))
        if window.current_render_context.get("view") == "3d":
            window.plotter.screenshot(str(args.output_dir / f"{name}_scene.png"))

    def set_bounds(region, fractions):
        _, domain = geometry(window.current_render_context)
        region["bounds"] = tuple(low + f * (high - low) for low, high, pair in
            zip(domain[::2], domain[1::2], fractions) for f in pair)
        window._enhancement_state()["revision"] += 1
        window._enhancement_refresh()

    def export(name):
        snapshot = capture_snapshot(window)
        check(name + " snapshot has scientific data and separate display channels", "enhancement" in snapshot.payload)
        options = OutputOptions(width_mm=150, height_mm=110, dpi=120)
        render_and_save(snapshot, resolve_style(snapshot.view_family, ""), {}, options, str(args.output_dir / (name + ".png")))
        check(name + " publication file created", (args.output_dir / (name + ".png")).is_file())

    def mouse(kind, xy, button, buttons):
        canvas = window.canvas_2d
        ratio = canvas.devicePixelRatioF()
        height = canvas.get_width_height(physical=True)[1]
        x, y = window.ax_2d.transData.transform(xy)
        pos = QPointF(x / ratio, (height - y) / ratio)
        QApplication.sendEvent(canvas, QMouseEvent(kind, pos, QPointF(canvas.mapToGlobal(pos.toPoint())), button, buttons, Qt.NoModifier))

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
                progress["source_sample"] = window.core.raw_data[::25, ::25, ::25, :].copy()
                window._add_enhancement_region()
                set_bounds(window._enhancement_selected(), ((.05, .28), (.25, .55), (.1, .4)))
            elif step == 1:
                check("region dropdown lists the new region", window.page_render.region_combo.count() == 3)
                check("3D editor widget present", window._enhancement_box_widget is not None)
                import vtk
                widget = window._enhancement_box_widget
                before = tuple(window._enhancement_selected()["bounds"])
                widget.InvokeEvent("StartInteractionEvent")
                transform = vtk.vtkTransform()
                transform.Translate(2, 0, 0)
                widget.SetTransform(transform)
                widget.InvokeEvent("InteractionEvent")
                widget.InvokeEvent("EndInteractionEvent")
                check("3D VTK interaction updates canonical region", not np.allclose(before, window._enhancement_selected()["bounds"]))
                window.page_render.s_gamma.setValue(62)
                window.flush_render_levels_refresh()
                progress["first_id"] = window._enhancement_selected()["region_id"]
                window._add_enhancement_region()
                set_bounds(window._enhancement_selected(), ((.6, .9), (.45, .8), (.5, .85)))
            elif step == 2:
                check("two independent 3D regions", len(window._enhancement_state()["regions"]) == 2)
                check("second region inherits current gamma", window.page_render.s_gamma.value() == 62)
                window._select_enhancement_target(progress["first_id"])
                check("first gamma restored", window.page_render.s_gamma.value() == 62)
                progress["colors"] = window.volume_session.display_channel.buffer[:, 0].copy()
                progress["alphas"] = window.volume_session.display_channel.buffer[:, 1].copy()
                window.page_render.combo_map.setCurrentIndex(3)
                window.flush_render_levels_refresh()
            elif step == 3:
                check("opacity mode leaves color positions unchanged", np.array_equal(progress["colors"], window.volume_session.display_channel.buffer[:, 0]))
                check("opacity mode updates alpha", not np.array_equal(progress["alphas"], window.volume_session.display_channel.buffer[:, 1]))
                screenshot("3d_regions")
                export("3d_publication")
                # 全局锁定：背景与所有区域同时锁定，每个区域锁定自己的强度范围。
                window.page_render.btn_lock.setChecked(True)
                check("global lock freezes background range", VisualEngine.locked_data_range() is not None)
                ranges = [r.get("locked_range") for r in window._enhancement_state()["regions"]]
                check("global lock freezes every region", all(ranges))
                check("regions keep independent locked ranges", len({tuple(r) for r in ranges}) == len(ranges))
                progress["locked_range"] = window._enhancement_selected()["locked_range"]
                window.timeline_bar.slider_time.setValue(1)
                window.flush_time_slider_refresh()
            elif step == 4:
                fields = window._enhancement_display(window.current_render_context)
                first = next(r for r in fields["regions"] if r["region_id"] == progress["first_id"])
                check("locked region range survives time frame change", tuple(first["data_range"]) == tuple(progress["locked_range"]))
                window._delete_enhancement_region(progress["first_id"])
            elif step == 5:
                check("deleting a region releases the global lock", VisualEngine.locked_data_range() is None and not window.page_render.btn_lock.isChecked())
                context = window.current_render_context
                fields = window._enhancement_display(context)
                mask = np.ones(context["data"].shape, dtype=bool)
                for region in window._enhancement_state()["regions"]:
                    mask[region_slices(context, region)] = False
                expected = finite_range(context["data"])
                info = fields["background_level_info"]
                check("deleted 3D region restores full-source background mapping", np.allclose(expected, (info["data_min"], info["data_max"])))
                screenshot("3d_deleted_one")
                window._delete_enhancement_region(window._enhancement_selected()["region_id"])
            elif step == 6:
                check("last deletion leaves background and show-all entries", window.page_render.region_combo.count() == 2)
                check("last deletion restores original volume path", window.volume_session.display_channel is None)
                screenshot("3d_restored")
                window.page_data.combo_ax.setCurrentIndex(1)
                window.on_apply_axis_integral()
            elif step == 7:
                check("2D integral ready", window.current_render_context["view"] == "2d")
                window._add_enhancement_region()
                set_bounds(window._enhancement_selected(), ((.1, .35), (.2, .5)))
            elif step == 8:
                check("2D interactive handles visible", window._enhancement_selector is not None and window._enhancement_selector.get_visible())
                selector = window._enhancement_selector
                x0, x1, y0, y1 = selector.extents
                start = ((x0+x1)/2, (y0+y1)/2)
                end = (start[0] + .05*(x1-x0), start[1] + .05*(y1-y0))
                progress["before_drag"] = tuple(window._enhancement_selected()["bounds"])
                mouse(QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
                mouse(QEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton)
                mouse(QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)
            elif step == 9:
                check("2D Qt gesture moves active region", not np.allclose(window._enhancement_selected()["bounds"], progress["before_drag"]))
                window._add_enhancement_region()
                set_bounds(window._enhancement_selected(), ((.55, .85), (.5, .8)))
            elif step == 10:
                check("two 2D regions share one view", len(window._enhancement_state()["regions"]) == 2)
                screenshot("2d_regions")
                progress["canonical_bounds"] = tuple(window._enhancement_selected()["bounds"])
                window.timeline_bar.switch_flip.setChecked(True)
            elif step == 11:
                check("E flip preserves canonical sample bounds", tuple(window._enhancement_selected()["bounds"]) == progress["canonical_bounds"])
                screenshot("2d_flipped")
                export("2d_publication")
                combo = window.page_render.region_combo
                combo.setCurrentIndex(combo.findData(SHOW_ALL_REGIONS_ID))
            elif step == 12:
                # 下拉框「全部显示」：所有区域色带与轮廓同时显示。
                check("show-all selects the dropdown entry", window._enhancement_state()["show_all_regions"])
                check("show-all displays every region colorbar", len(window._enhancement_colorbars) == 2)
                labels = sorted(t.get_text() for t in window.ax_2d.texts if t.get_text().startswith("R"))
                check("show-all displays every region outline", labels == ["R1", "R2"])
                screenshot("2d_show_all")
                window.page_render.region_combo.showPopup()
            elif step == 13:
                combo = window.page_render.region_combo
                screenshot("dropdown_delete")
                combo.view().window().grab().save(str(args.output_dir / "dropdown_popup.png"))
                index = combo.model().index(2, 0)
                rect = combo.view().visualRect(index)
                pos = QPointF(rect.right()-8, rect.center().y())
                for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
                    QApplication.sendEvent(combo.view().viewport(), QMouseEvent(kind, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
                combo.hidePopup()
            elif step == 14:
                check("dropdown row action deletes region", len(window._enhancement_state()["regions"]) == 1)
                window._delete_enhancement_region(window._enhancement_state()["regions"][0]["region_id"])
            else:
                check("2D last deletion leaves background and show-all entries", window.page_render.region_combo.count() == 2)
                check("2D returns to scalar image", window.ax_2d._arpes_image.get_array().ndim == 2)
                check("science samples remain unchanged", np.array_equal(window.core.raw_data[::25, ::25, ::25, :], progress["source_sample"], equal_nan=True))
                screenshot("2d_restored")
                (args.output_dir / "acceptance.json").write_text(json.dumps({"data": str(args.data.resolve()), "checks": checks}, ensure_ascii=False, indent=2), encoding="utf-8")
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
