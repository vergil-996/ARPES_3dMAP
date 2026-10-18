"""Native-window validation of display regions on real ARPES data."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.validation._common import arguments


def main():
    args = arguments("local_enhancement", default_data=".local/data/WSe2_step.npz")
    import copy
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

    def styles():
        state = window._enhancement_state()
        return copy.deepcopy({"": state["background"], **{r["region_id"]: r["style"] for r in state["regions"]}})

    def select_target(region_id, name):
        combo = window.page_render.region_combo
        index = combo.findData(region_id)
        check(name + " exists in dropdown", index >= 0)
        combo.setCurrentIndex(index)
        state = window._enhancement_state()
        check(name + " is the selected adjustment target",
              state.get("selected_id") == (region_id or None) and not state.get("show_all_regions"))
        style = styles()[region_id]
        check(name + " restores all level and mapping controls", window._enhancement_ui_style() == style)
        check(name + " restores slider value labels", tuple(s._value_label.text() for s in
              (window.page_render.s_low, window.page_render.s_gamma, window.page_render.s_up)) ==
              tuple(str(int(v)) for v in style["levels"]))

    def set_style(style):
        for slider, value in zip((window.page_render.s_low, window.page_render.s_gamma, window.page_render.s_up), style["levels"]):
            slider.setValue(int(value))
        window.page_render.combo_map.setCurrentText(style["opacity_mode"])
        window.flush_render_levels_refresh()

    def verify_global_gamma(view):
        from matplotlib import colormaps
        set_style(dict(levels=(0, 100, 100), opacity_mode="线性"))
        context = window.current_render_context
        check(view + " gamma100 uses the global mapping", window._enhancement_display(context) is None)
        if view == "3D":
            info = window.volume_session.level_info
            source = np.asarray(context["data"])
        else:
            image = window.ax_2d._arpes_image
            info = VisualEngine._level_info(context["data"], (0, 100, 100))
            source = np.asarray(image.get_array())
        normalized = np.clip((source.astype(np.float64) - info["black_value"]) /
                             (info["white_value"] - info["black_value"]), 0, 1)
        expected = normalized ** .1
        weak = np.isfinite(source) & (normalized > 0) & (normalized < .1)
        check(view + " real spectrum contains weak samples", np.any(weak))
        if view == "3D":
            actual = window.volume_session.display_channel.buffer.reshape(source.shape + (2,), order="F")
            check(view + " global weak colors receive gamma before lookup", np.allclose(actual[..., 0][weak], expected[weak]))
            check(view + " global weak opacity receives gamma before lookup", np.allclose(actual[..., 1][weak], expected[weak]))
        else:
            actual = image.to_rgba(image.get_array(), bytes=True)
            palette = colormaps.get_cmap(window.page_render.combo_cmap.currentText())
            check(view + " global weak colors receive gamma before lookup", np.array_equal(actual[weak], palette(expected, bytes=True)[weak]))
            check(view + " weak samples remain opaque", np.all(actual[..., 3][weak] == 255))
        screenshot(view.lower() + "_global_gamma100")

    def verify_independent_controls(view):
        state = window._enhancement_state()
        first, second = state["regions"]
        first_id, second_id = first["region_id"], second["region_id"]
        background_levels = (6., 57., 94.) if view == "2D" else (3., 54., 97.)
        targets = ((second_id, second["label"], (8., 43., 93.), "对数"),
                   (first_id, first["label"], (12., 62., 88.), "线性"),
                   ("", "background", background_levels, "幂函数"))
        for region_id, label, levels, mode in targets:
            name = view + " " + label
            select_target(region_id, name)
            previous = styles()
            before = window._enhancement_display(window.current_render_context)
            expected = dict(levels=levels, opacity_mode=mode)
            check(name + " acceptance changes the previous style", expected != previous[region_id])
            set_style(expected)
            current = styles()
            check(name + " edits its levels and mapping", current[region_id] == expected)
            check(name + " preserves every other target style",
                  all(current[key] == value for key, value in previous.items() if key != region_id))
            after = window._enhancement_display(window.current_render_context)
            owner = next((i for i, r in enumerate(after["regions"], 1) if r["region_id"] == region_id), 0)
            selected = after["owners"] == owner
            check(name + " has display samples", np.any(selected))
            samples = np.asarray(window.current_render_context["data"])[selected]
            data_range = finite_range(samples)
            print(f"[INFO] {name} mapping acceptance sample range: {data_range}", flush=True)
            check(name + " has intermediate intensities for mapping acceptance",
                  data_range is not None and np.any((samples > data_range[0]) & (samples < data_range[1])))
            check(name + " updates its display channels", any(not np.array_equal(before[key][selected], after[key][selected])
                  for key in ("color", "alpha")))
            check(name + " preserves other display channels", all(np.array_equal(before[key][~selected], after[key][~selected])
                  for key in ("color", "alpha")))
        for region_id, label, _, _ in targets:
            select_target(region_id, view + " revisit " + label)
        select_target(first_id, view + " active " + first["label"])

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
                verify_global_gamma("3D")
                window._add_enhancement_region()
                # WSe2's lower-energy corner contains only 0/1: both endpoints
                # remain clipped under level edits. Include intermediate counts.
                set_bounds(window._enhancement_selected(), ((.05, .28), (.25, .55), (.25, .55)))
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
                verify_independent_controls("3D")
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
                session = window.volume_session
                check("last deletion restores global-only mapping", window._enhancement_display(window.current_render_context) is None)
                if session.display_channel is not None:
                    from bandscope.processing.display_transfer import channels
                    style = window._enhancement_background_style()
                    color, alpha = channels(window.current_render_context["data"], session.level_info, style["opacity_mode"])
                    check("global gamma colors survive last region deletion", np.allclose(session.display_channel.buffer[:, 0], color.ravel(order="F")))
                    check("global gamma opacity survives last region deletion", np.allclose(session.display_channel.buffer[:, 1], alpha.ravel(order="F")))
                screenshot("3d_restored")
                window.page_data.combo_ax.setCurrentIndex(1)
                window.on_apply_axis_integral()
            elif step == 7:
                check("2D integral ready", window.current_render_context["view"] == "2d")
                verify_global_gamma("2D")
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
                verify_independent_controls("2D")
                screenshot("2d_regions")
                progress["canonical_bounds"] = tuple(window._enhancement_selected()["bounds"])
                window.timeline_bar.switch_flip.setChecked(True)
            elif step == 11:
                check("E flip preserves canonical sample bounds", tuple(window._enhancement_selected()["bounds"]) == progress["canonical_bounds"])
                screenshot("2d_flipped")
                export("2d_publication")
                progress["styles_before_all"] = styles()
                combo = window.page_render.region_combo
                combo.setCurrentIndex(combo.findData(SHOW_ALL_REGIONS_ID))
            elif step == 12:
                # 下拉框「全部显示」：所有区域色带与轮廓同时显示。
                check("show-all selects the dropdown entry", window._enhancement_state()["show_all_regions"])
                check("show-all displays every region colorbar", len(window._enhancement_colorbars) == 2)
                labels = sorted(t.get_text() for t in window.ax_2d.texts if t.get_text().startswith("R"))
                expected_labels = sorted(r["label"] for r in window._enhancement_state()["regions"])
                check("show-all displays every region outline", labels == expected_labels)
                check("show-all selection preserves independent styles", styles() == progress["styles_before_all"])
                check("show-all restores background as the batch starting point",
                      window._enhancement_ui_style() == styles()[""])
                batch_style = dict(levels=(4., 58., 96.), opacity_mode="sigmoid")
                set_style(batch_style)
                check("show-all sliders and mapping update background and every region",
                      all(style == batch_style for style in styles().values()))
                for region_id in styles():
                    select_target(region_id, "2D batch revisit " + (region_id or "background"))
                combo = window.page_render.region_combo
                combo.setCurrentIndex(combo.findData(SHOW_ALL_REGIONS_ID))
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
