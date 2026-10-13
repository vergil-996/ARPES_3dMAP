"""Verify an expanding 3D box on a real cropped ROI and native VTK window."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.validation._common import arguments


def main():
    args = arguments("crop_box_bounds", default_data=".local/data/WSe2_step.npz")
    from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path
    configure_qt_high_dpi()
    configure_qt_plugin_path()

    import numpy as np
    import vtk
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication
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
    state = {"step": 0, "settle": 0}

    def box_bounds():
        poly = vtk.vtkPolyData()
        window.plotter.box_widgets[0].GetPolyData(poly)
        return poly.GetBounds()

    def gesture(bounds):
        render = window.core.logical_to_render_bounds(bounds, window._full_domain_spatial_shape(), clamp=False)
        widget = window.plotter.box_widgets[0]
        widget.PlaceWidget(render)
        for event in ("StartInteractionEvent", "InteractionEvent", "EndInteractionEvent"):
            widget.InvokeEvent(event)
        np.testing.assert_allclose(window._crop_logical_bounds(), bounds, atol=1e-6)
        np.testing.assert_allclose(box_bounds(), render, atol=1e-6)
        print("[PASS] VTK gesture and numeric bounds agree", flush=True)
        return render

    def tick():
        try:
            if state["settle"]:
                state["settle"] -= 1
                return
            if not window._render_exact_ready or (window.current_render_context or {}).get("view") != "3d":
                return
            step = state["step"]
            if step == 0:
                window.btn_tb_crop.setChecked(True)
                maximum = np.asarray(window._full_domain_spatial_shape()) - 1
                state["maximum"] = maximum
                inner = tuple(v for size in maximum for v in (round(size * .2), round(size * .8)))
                gesture(inner)
                window.on_cut()
                state["step"] = 1
                state["settle"] = 8
            elif step == 1:
                assert window.left_workspace.current_spec().data_scope_id != "full"
                state["roi"] = tuple(window.current_render_context["data_bounds"])
                outside = tuple(v for size in state["maximum"] for v in (-size * .1, size * 1.1))
                state["render"] = gesture(outside)
                state["physical"] = window.crop_controller.selection.bounds
                window.global_refresh()
                state["step"] = 2
                state["settle"] = 8
            elif step == 2:
                np.testing.assert_allclose(box_bounds(), state["render"], atol=1e-6)
                np.testing.assert_allclose(window.crop_controller.selection.bounds, state["physical"], atol=1e-6)
                assert tuple(window.current_render_context["data_bounds"]) == state["roi"]
                print("[PASS] refresh preserves outside box and ROI data extent", flush=True)
                window.plotter.render()
                window.grab().save(str(args.output_dir / "roi_expanded_box.png"))
                window.plotter.screenshot(str(args.output_dir / "roi_expanded_scene.png"))
                window.crop_controller.popup.grab().save(str(args.output_dir / "roi_expanded_popup.png"))
                window.timeline_bar.switch_flip.setChecked(True)
                window.global_refresh()
                state["step"] = 3
                state["settle"] = 8
            else:
                np.testing.assert_allclose(box_bounds(), state["render"], atol=1e-6)
                print("[PASS] E flip preserves outside box geometry", flush=True)
                gesture(state["roi"])
                window._rebuild_interactive_box()
                print("[PASS] box moves back into ROI after expansion", flush=True)
                window.grab().save(str(args.output_dir / "roi_box_returned.png"))
                window.plotter.screenshot(str(args.output_dir / "roi_returned_scene.png"))
                timer.stop()
                window.close()
                app.exit(0)
        except Exception:
            import traceback
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
