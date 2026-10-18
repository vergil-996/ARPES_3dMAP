"""Time one real-window 3D refresh and its sections.

Plan: docs/plans/enhancement-render-performance.md.  Off-screen
``Plotter.render()`` is a no-op on this machine, so the P1 decisions need a
native window.

Usage:
  python scripts/diagnostics/probe_enhancement_window.py [--data PATH]

Prints the app-level refresh cost for:
  * a style tick (levels changed, the drag path),
  * an idle refresh (same style, cache should hit),
  * ``plotter.render()`` and an explicit sample-distance sweep.
"""
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.validation._common import arguments  # noqa: E402


def _median_ms(call, repeat=3):
    call()
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        call()
        samples.append((time.perf_counter() - started) * 1000.0)
    return round(statistics.median(samples), 1)


def main():
    args = arguments("enhancement_window", default_data=".local/data/WSe2_step.npz")
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    import bandscope.app.enhancement_integration as integration
    import bandscope.app.refactored_app as app_module
    from bandscope.processing import gpu_display
    from bandscope.rendering.refresh_pipeline import RefreshCause, RenderQuality

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
    state = {"ticks": 0}

    def tick():
        try:
            if window.current_render_context is None or not window._render_exact_ready:
                return
            if window.current_render_context.get("view") != "3d":
                return
            state["ticks"] += 1
            if state["ticks"] < 2:
                return
            if not window._enhancement_state():
                window._add_enhancement_region()
                return
            if state["ticks"] < 4:
                return
            timer.stop()
            measure()
            window.close()
            app.quit()
        except Exception:
            import traceback

            traceback.print_exc()
            timer.stop()
            window.close()
            app.exit(1)

    def toggle_style():
        enhancement = window._enhancement_state()
        levels = list(enhancement["background"]["levels"])
        levels[1] = 51 if levels[1] == 50 else 50
        enhancement["background"]["levels"] = tuple(levels)
        enhancement["revision"] += 1

    def measure():
        context = window.current_render_context
        enhancement = window._enhancement_state()
        mapper = window.volume_session.volume.mapper
        spacing = tuple(float(v) for v in window.volume_session.grid.spacing)
        data = np.asarray(context["data"])
        print(f"volume {tuple(window.volume_session.shape)} spacing={spacing}", flush=True)
        print(f"gpu={gpu_display.enabled()} backend={window.backend_manager.selected_backend_name()} "
              f"regions={len(enhancement['regions'])} data_c={data.flags['C_CONTIGUOUS']}", flush=True)

        sections = {}

        original_resolve = integration.resolve_display
        original_adapt = integration.adapt_regions

        def resolve_wrapper(*call_args, **kwargs):
            started = time.perf_counter()
            try:
                return original_resolve(*call_args, **kwargs)
            finally:
                sections["resolve_display"] = sections.get("resolve_display", 0.0) + (
                    time.perf_counter() - started) * 1000.0

        def adapt_wrapper(*call_args, **kwargs):
            started = time.perf_counter()
            try:
                return original_adapt(*call_args, **kwargs)
            finally:
                sections["adapt_regions"] = sections.get("adapt_regions", 0.0) + (
                    time.perf_counter() - started) * 1000.0

        integration.resolve_display = resolve_wrapper
        integration.adapt_regions = adapt_wrapper
        try:
            toggle_style()
            started = time.perf_counter()
            window._render_active_page(RenderQuality.EXACT, RefreshCause.DISPLAY_MAPPING)
            total = (time.perf_counter() - started) * 1000.0
        finally:
            integration.resolve_display = original_resolve
            integration.adapt_regions = original_adapt
        print(f"style tick (instrumented): {total:.1f} ms", flush=True)
        for name, value in sections.items():
            print(f"  {name}: {value:.1f} ms", flush=True)

        def style_tick():
            toggle_style()
            window._render_active_page(RenderQuality.EXACT, RefreshCause.DISPLAY_MAPPING)

        import cProfile
        import io
        import pstats

        profiler = cProfile.Profile()
        profiler.enable()
        for _ in range(3):
            integration.resolve_display(context, enhancement)
        profiler.disable()
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(14)
        print("resolve profile:", flush=True)
        print(stream.getvalue(), flush=True)

        print(f"style tick (median): {_median_ms(style_tick, 3)} ms", flush=True)
        print(f"idle refresh (median): "
              f"{_median_ms(lambda: window._render_active_page(RenderQuality.EXACT, RefreshCause.DISPLAY_MAPPING), 3)} ms",
              flush=True)
        print(f"plotter.render: {_median_ms(lambda: window.plotter.render(), 5)} ms", flush=True)

        base = float(spacing[0])
        rows = []
        try:
            mapper.SetAutoAdjustSampleDistances(False)
            for factor in (0.5, 1.0, 2.0, 4.0):
                mapper.SetSampleDistance(base * factor)
                rows.append((factor, base * factor, _median_ms(lambda: window.plotter.render(), 5)))
        finally:
            mapper.SetAutoAdjustSampleDistances(True)
        print("| distance/spacing | distance | render ms |", flush=True)
        for factor, distance, value in rows:
            print(f"| {factor:g} | {distance:.3f} | {value} |", flush=True)

    timer = QTimer()
    timer.timeout.connect(tick)
    window.load_data(str(args.data))
    timer.start(250)
    QTimer.singleShot(180000, lambda: app.exit(2))
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
