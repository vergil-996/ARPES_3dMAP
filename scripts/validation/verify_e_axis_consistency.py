"""Native-window acceptance of E-flip coordinates, exports and plugin inputs.

Run from the repository root with python -m scripts.validation.verify_e_axis_consistency.
This reports current behavior; failed checks do not modify application code.
"""
import json
import sys
import time
import traceback
from dataclasses import replace


def main():
    from scripts.validation._common import arguments
    args = arguments("verify_e_axis_consistency", default_data=".local/data/WSe2_step.npz")
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication
    import numpy as np
    import bandscope.app.refactored_app as app_module
    from bandscope.core.crop_model import apply_selection, axis_values
    from bandscope.exporting.publication_export import capture_snapshot, render_and_save
    from bandscope.exporting.publication_models import OutputOptions, resolve_style

    class NullUpdateController:
        def __init__(self, *args, **kwargs):
            pass

        def check_automatically(self):
            pass

        def shutdown(self, **kwargs):
            pass

    app_module.UpdateController = NullUpdateController
    app = QApplication(sys.argv)
    window = app_module.My3DAnalyzer()
    window._save_splitter_sizes = lambda: None
    window.resize(1400, 900)
    window.show()
    report = {"data": str(args.data.resolve()), "platform": sys.platform,
              "checks": [], "errors": [], "screenshots": []}
    state = {"finished": False}

    def check(name, actual, expected):
        actual, expected = np.asarray(actual), np.asarray(expected)
        passed = actual.shape == expected.shape and np.allclose(actual, expected, equal_nan=True)
        record = dict(name=name, passed=bool(passed), actual_shape=list(actual.shape),
                      expected_shape=list(expected.shape), actual_head=actual.ravel()[:8].tolist(),
                      expected_head=expected.ravel()[:8].tolist())
        if actual.shape == expected.shape and actual.size:
            record["max_abs_difference"] = float(np.nanmax(np.abs(actual.astype(float) - expected.astype(float))))
        report["checks"].append(record)
        print(f"{'PASS' if passed else 'FAIL'}: {name}", flush=True)

    def screenshot(name):
        path = args.output_dir / (name + ".png")
        window.grab().save(str(path))
        report["screenshots"].append(str(path.resolve()))
        if window.current_render_context.get("view") == "3d":
            path = args.output_dir / (name + "_scene.png")
            window.plotter.screenshot(str(path))
            report["screenshots"].append(str(path.resolve()))
        popup = window.crop_controller.popup
        if popup.isVisible():
            path = args.output_dir / (name + "_popup.png")
            popup.grab().save(str(path))
            report["screenshots"].append(str(path.resolve()))

    def finish():
        if state["finished"]:
            return
        state["finished"] = True
        (args.output_dir / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        window.close()
        app.exit(1 if report["errors"] or any(not r["passed"] for r in report["checks"]) else 0)

    def failed(exc_type, exc, tb):
        traceback.print_exception(exc_type, exc, tb)
        report["errors"].append(str(exc))
        finish()

    sys.excepthook = failed

    def ready(view, callback, deadline=None):
        if state["finished"]:
            return
        if deadline is None:
            deadline = time.monotonic() + 60
        context = window.current_render_context
        if context and context.get("view") == view and window._render_exact_ready:
            QTimer.singleShot(300, callback)
        elif time.monotonic() > deadline:
            raise RuntimeError(f"Timed out waiting for {view}")
        else:
            QTimer.singleShot(200, lambda: ready(view, callback, deadline))

    def load():
        window.load_data(str(args.data))
        QTimer.singleShot(1000, lambda: ready("3d", baseline))

    def baseline():
        window.timeline_bar.switch_flip.setChecked(False)
        window.timeline_bar.switch_axes.setChecked(True)
        snap = capture_snapshot(window)
        context = window.current_render_context
        check("3D snapshot E without flip", snap.coords["E"], context["coords"]["E"])
        window.timeline_bar.switch_flip.setChecked(True)
        QTimer.singleShot(1000, lambda: ready("3d", flipped))

    def flipped():
        context = window.current_render_context
        rendered = window._render_context_for_visual_flip(context)
        snap = capture_snapshot(window)
        check("3D snapshot E with flip", snap.coords["E"], rendered["coords"]["E"])
        screenshot("3d_flipped")
        render_and_save(snap, resolve_style("3d", "3d_boxed"), {},
                        OutputOptions(width_mm=120, dpi=100, fmt="png"),
                        str(args.output_dir / "3d_flipped_export.png"))
        window.btn_tb_crop.setChecked(True)
        e = rendered["coords"]["E"]
        lo, hi = max(1, len(e) // 5), max(2, len(e) // 3)
        state["e_indices"] = (lo, hi)
        selection = window.crop_controller.selection
        bounds = (*selection.bounds[:4], *sorted((float(e[lo]), float(e[hi]))))
        for edit, value in zip(window.crop_controller.popup.edits, bounds):
            edit.setText(str(value))
        if not window.crop_controller.commit_edits():
            raise RuntimeError("Crop fields rejected the test range")
        check("Flipped crop fields map to absolute E indices", window._crop_logical_bounds()[4:6], [lo, hi])
        selection = window.crop_controller.selection
        cropped = apply_selection(context, selection)
        check("Flipped crop selects requested voxels", cropped["data"], context["data"][:, :, lo:hi + 1])
        erased = apply_selection(context, replace(selection, operation="erase"))
        expected = np.zeros(context["data"].shape, dtype=bool)
        expected[:, :, lo:hi + 1] = True
        check("Flipped erase selects requested voxels", np.isnan(erased["data"]), expected)
        screenshot("3d_crop_fields")
        window.btn_tb_erase.setChecked(True)
        screenshot("3d_erase_fields")
        window.btn_tb_crop.setChecked(True)
        window.on_cut()
        QTimer.singleShot(1000, lambda: ready("3d", roi))

    def roi():
        context = window.current_render_context
        lo, hi = state["e_indices"]
        check("Real ROI page has requested E bounds", context["data_bounds"][4:6], [lo, hi])
        rendered = window._render_context_for_visual_flip(context)
        plugin = window.plugin_session.build_context(window, rendered)
        check("Plugin ROI energy matches displayed ROI", sorted(plugin.energy.roi_range),
              sorted((axis_values(rendered, 2)[0], axis_values(rendered, 2)[-1])))
        check("ROI crop popup matches displayed E limits", window.crop_controller.selection.bounds[4:6],
              sorted((axis_values(rendered, 2)[0], axis_values(rendered, 2)[-1])))
        screenshot("roi_flipped")
        window.btn_tb_crop.setChecked(False)
        window._select_control_page(1)
        window.page_data.combo_ax.setCurrentIndex(0)
        window.page_data.btn_ax_apply.click()
        QTimer.singleShot(1000, lambda: ready("2d", analysis))

    def analysis():
        snapshot = capture_snapshot(window)
        plugin = window.plugin_session.capture_analysis_input("acceptance")
        check("2D picture snapshot matches visible image", snapshot.payload["image"], window.ax_2d.images[0].get_array())
        check("2D plugin data matches visible E orientation", plugin.data, snapshot.payload["image"].T)
        screenshot("2d_flipped")
        spec = window._build_energy_dos_spec()
        if spec is None:
            raise RuntimeError("Could not create Energy DOS")
        window.left_workspace.add_page(spec)
        QTimer.singleShot(1000, lambda: ready("1d", curve))

    def curve():
        spec = window.left_workspace.current_spec()
        context = window.current_render_context
        raw, coords = window._get_display_state_for_spec(spec)
        payload = window._build_export_payload(spec, raw, coords)[2]
        rendered = window._render_context_for_visual_flip(context)
        x, y = np.asarray(rendered["x_data"]), np.asarray(rendered["y_data"])
        if x[0] > x[-1]:
            x, y = x[::-1], y[::-1]
        visible_at_export_coordinates = np.interp(payload["E"], x, y)
        check("Energy DOS export matches visible curve at same E", payload["intensity"], visible_at_export_coordinates)
        check("Visible curve matches rendered context", window.ax_2d.lines[0].get_ydata(), rendered["y_data"])
        np.savez(args.output_dir / "curve_arrays.npz", displayed=rendered["y_data"],
                 exported=payload["intensity"], E=payload["E"])
        screenshot("energy_dos_flipped")
        finish()

    QTimer.singleShot(300, load)
    QTimer.singleShot(180000, lambda: failed(RuntimeError, RuntimeError("Acceptance timed out"), None))
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
