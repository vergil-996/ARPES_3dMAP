"""Compare native-window timeline previews with settled 3D renders.

Uses WSe2_step.npz by default; writes screenshots and measured results under
.local/outputs/verify_time_preview. User settings and extensions are isolated.
"""
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    from scripts.validation._common import arguments
    args = arguments("verify_time_preview", default_data=".local/data/WSe2_step.npz")
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication
    import numpy as np
    import pyvista as pv
    import vtk
    import bandscope.app.refactored_app as app_module
    from bandscope.rendering.refresh_pipeline import RefreshCause, RenderQuality

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
    window.resize(1280, 800)
    window.show()
    slider = window.timeline_bar.slider_time
    report = {"data": str(args.data.resolve()), "window_size": [1280, 800],
              "platform": sys.platform, "vtk_version": vtk.vtkVersion.GetVTKVersion(),
              "pyvista_version": pv.__version__, "numpy_version": np.__version__,
              "pairs": [], "drag_preview_ms": [], "drag_exact_ms": [], "errors": []}
    state = {"stage": "loading", "index": 0}
    render = window._render_active_page

    def finish():
        state["stage"] = "finished"
        path = args.output_dir / "results.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        window.close()
        app.exit(1 if report["errors"] else 0)

    def failed(exc_type, exc, tb):
        traceback.print_exception(exc_type, exc, tb)
        report["errors"].append(str(exc))
        finish()

    sys.excepthook = failed

    def snapshot(quality, elapsed):
        session = window.volume_session
        frame = slider.value()
        path = args.output_dir / f"frame_{frame}_{quality.value}.png"
        pixels = window.plotter.screenshot(str(path), return_img=True)
        window.grab().save(str(args.output_dir / f"window_{frame}_{quality.value}.png"))
        return {"frame": frame, "render_ms": elapsed, "shape": list(session.shape),
                "levels": dict(session.level_info), "rebuilds": session.rebuild_count,
                "uploads": session.data_update_count,
                "opacity_unit_distance": session.volume.GetProperty().GetScalarOpacityUnitDistance(),
                "bounds": list(session.grid.bounds),
                "camera": [list(v) for v in window.plotter.camera_position],
                "screenshot": str(path.resolve())}, np.asarray(pixels).copy()

    def measured_render(quality=RenderQuality.EXACT, cause=None):
        started = time.perf_counter()
        render(quality, cause)
        elapsed = (time.perf_counter() - started) * 1000
        if cause != RefreshCause.DATA_SOURCE:
            return
        if state["stage"] == "pairs":
            if quality == RenderQuality.PREVIEW and "preview" not in state:
                # Screenshot work must not let the idle exact timer win before
                # the simulated release. Drag timing below has no screenshots.
                window.refresh_coordinator._exact_timer.stop()
                state["preview"], state["pixels"] = snapshot(quality, elapsed)
                QTimer.singleShot(0, release_slider)
            elif quality == RenderQuality.EXACT and "preview" in state:
                settled_at = time.perf_counter()
                exact, pixels = snapshot(quality, elapsed)
                preview = state.pop("preview")
                diff = np.abs(pixels.astype(np.float32) - state.pop("pixels").astype(np.float32))
                pair = {"preview": preview, "exact": exact,
                        "pixel_mae_255": float(diff.mean()),
                        "pixel_changed_fraction": float(np.any(diff > 0, axis=2).mean()),
                        "settle_ms": (settled_at - state["released_at"]) * 1000}
                report["pairs"].append(pair)
                for key in ("shape", "levels", "rebuilds", "uploads", "opacity_unit_distance", "bounds", "camera"):
                    if preview[key] != exact[key]:
                        report["errors"].append(f"frame {slider.value()}: {key} differs")
                if preview["shape"] != list(window.core.raw_data.shape[:3]):
                    report["errors"].append("preview shape is downsampled")
                QTimer.singleShot(200, next_pair)
        elif state["stage"] == "drag":
            key = "drag_preview_ms" if quality == RenderQuality.PREVIEW else "drag_exact_ms"
            report[key].append(elapsed)
        elif state["stage"] == "release" and quality == RenderQuality.EXACT:
            report["release_settle_ms"] = (time.perf_counter() - state["released_at"]) * 1000
            report["final_rebuilds"] = window.volume_session.rebuild_count
            QTimer.singleShot(100, finish)

    window._render_active_page = measured_render

    def release_slider():
        # The app's release slot renders synchronously, before later slots run.
        state["released_at"] = time.perf_counter()
        slider.setSliderDown(False)

    def next_pair():
        if state["index"] >= len(state["frames"]):
            state.update(stage="drag", drag_index=0, drag_started=time.perf_counter())
            slider.setSliderDown(True)
            drag_step()
            return
        state["stage"] = "pairs"
        frame = state["frames"][state["index"]]
        state["index"] += 1
        slider.setSliderDown(True)
        slider.setValue(frame)

    def drag_step():
        i = state["drag_index"]
        if i > slider.maximum():
            report["drag_duration_ms"] = (time.perf_counter() - state["drag_started"]) * 1000
            report["drag_input_interval_ms"] = 90
            state["stage"] = "release"
            release_slider()
            return
        slider.setValue(i)
        state["drag_index"] += 1
        QTimer.singleShot(90, drag_step)

    def load():
        window.load_data(str(args.data))
        if window.core.raw_data is None or window.core.raw_data.ndim != 4:
            raise ValueError("A dynamic 4D dataset is required")
        state["frames"] = list(dict.fromkeys([min(3, slider.maximum()), 0,
                                              slider.maximum() // 2, slider.maximum()]))
        QTimer.singleShot(1500, next_pair)

    def timeout():
        if state["stage"] != "finished":
            report["errors"].append("validation timed out")
            finish()

    QTimer.singleShot(300, load)
    QTimer.singleShot(60000, timeout)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
