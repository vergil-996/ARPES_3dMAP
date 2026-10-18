"""Measure the display path that local enhancement and non-linear levels share.

Plan: docs/plans/enhancement-render-performance.md

Modes:
  resolve   -- time ``resolve_display`` only (enhancement regions present).
  channels  -- time ``display_transfer.channels`` (gamma / no region control group).
  session   -- off-screen ``VolumeRenderSession.render`` including upload + VTK render.

The probe never touches application state; it builds frozen contexts from a volume.
Results are estimates for a single machine, not installation-package measurements.
"""
import argparse
import copy
import csv
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bandscope.core.local_enhancement import DEFAULT_STYLE, EnhancementRegion  # noqa: E402
from bandscope.processing.display_transfer import channels, level_info  # noqa: E402
from bandscope.processing.local_enhancement import finite_range, resolve_display  # noqa: E402

DEFAULT_DATA = ".local/data/WSe2_step.npz"


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def load_volume(path, frame=0):
    source = Path(path)
    if source.suffix == ".npz":
        with np.load(source) as payload:
            volume = np.asarray(payload["sample"], dtype=np.float32)
        return np.ascontiguousarray(volume[..., int(frame)])
    return np.random.default_rng(7).random((150, 150, 200), dtype=np.float32)


def context_3d(volume):
    return {
        "view": "3d",
        "data": volume,
        "coords": {
            key: np.arange(size, dtype=float)
            for key, size in zip(("X", "Y", "E"), volume.shape)
        },
    }


def region(number, shape, low, high, axes=("X", "Y", "E")):
    bounds = []
    for size in shape:
        bounds.extend((low * size, high * size))
    entry = EnhancementRegion(str(number), f"R{number}", "3d", axes, tuple(bounds)).to_dict()
    entry.update(style=copy.deepcopy(DEFAULT_STYLE), label=f"R{number}")
    return entry


def state_for(count, shape, levels, opacity_mode="linear"):
    regions = []
    if count >= 1:
        regions.append(region(1, shape, .25, .55))
    if count >= 2:
        regions.append(region(2, shape, .50, .70))
    if count >= 3:
        regions.append(region(3, shape, .10, .20))
    style = {"levels": tuple(float(v) for v in levels), "opacity_mode": opacity_mode}
    for entry in regions:
        entry["style"] = dict(style)
    return {"regions": regions, "background": dict(style), "revision": 1,
            "next_label": count + 1, "common": False, "selected_id": regions[0]["region_id"] if regions else None}


def time_calls(call, repeat, warmup=1):
    for _ in range(warmup):
        call()
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        call()
        samples.append((time.perf_counter() - start) * 1000.0)
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=DEFAULT_DATA)
    parser.add_argument("--frame", type=int, default=0)
    parser.add_argument("--regions", type=int, nargs="+", default=[0, 1, 3])
    parser.add_argument("--gamma", type=int, nargs="+", default=[50, 100])
    parser.add_argument("--mode", choices=["resolve", "channels", "session", "frame", "all"], default="all")
    parser.add_argument("--gpu", choices=["on", "off"], default="off",
                        help="force the CUDA display path (needs CuPy); off uses the CPU reference")
    parser.add_argument("--repeat", type=int, default=7)
    parser.add_argument("--window-size", type=int, nargs=2, default=[1280, 800])
    parser.add_argument("--out", default=".local/outputs/enhancement_timings")
    args = parser.parse_args()

    volume = load_volume(args.data, args.frame)
    shape = volume.shape
    voxels = int(volume.size)
    print(f"volume {shape} = {voxels/1e6:.2f}M voxels, {volume.nbytes/1e6:.1f} MB", flush=True)
    context = context_3d(volume)
    data_range = finite_range(volume)

    from bandscope.processing import gpu_display

    print(f"gpu display: requested={args.gpu} active={gpu_display.set_enabled(args.gpu == 'on')} "
          f"reason={gpu_display.failure_reason()}", flush=True)

    rows = []
    session = None
    plotter = None
    if args.mode in {"session", "frame", "all"}:
        import pyvista as pv
        from bandscope.rendering.render_core import VolumeRenderSession

        plotter = pv.Plotter(off_screen=True, window_size=tuple(args.window_size))
        session = VolumeRenderSession(plotter)

    for gamma in args.gamma:
        levels = (0, gamma, 100)
        info = level_info(data_range, levels)
        if args.mode in {"channels", "all"}:
            samples = time_calls(lambda: channels(volume, info, "linear"), args.repeat)
            rows.append(("channels", 0, gamma, samples))
        for count in args.regions:
            if count == 0:
                continue
            state = state_for(count, shape, levels)
            if args.mode in {"resolve", "all"}:
                samples = time_calls(lambda: resolve_display(context, state), args.repeat)
                rows.append(("resolve", count, gamma, samples))
            if session is not None:
                fields = resolve_display(context, state)

                def render_only(fields=fields):
                    session.render(volume, levels, "linear", cmap="magma", quality="exact",
                                   display_fields=fields, show_axes=False)

                if args.mode in {"session", "all"}:
                    rows.append(("session", count, gamma, time_calls(render_only, args.repeat)))
                if args.mode in {"frame", "all"}:
                    def frame(fields=fields):
                        session.render(volume, levels, "linear", cmap="magma", quality="exact",
                                       display_fields=fields, show_axes=False, render=False)
                        plotter.render()

                    rows.append(("frame", count, gamma, time_calls(frame, args.repeat)))

    if plotter is not None:
        plotter.close()

    print()
    print("| mode | regions | gamma | p50 ms | p95 ms | min ms | samples |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for mode, count, gamma, samples in rows:
        print(f"| {mode} | {count} | {gamma} | {statistics.median(samples):.1f} | "
              f"{percentile(samples, .95):.1f} | {min(samples):.1f} | {len(samples)} |")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    csv_path = out_dir / f"probe-{stamp}.csv"
    meta = {"volume": "x".join(str(v) for v in shape), "voxels": voxels, "data": str(args.data),
            "frame": args.frame, "repeat": args.repeat}
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["mode", "regions", "gamma", "p50_ms", "p95_ms", "min_ms", "samples", *meta])
        for mode, count, gamma, samples in rows:
            writer.writerow([mode, count, gamma, f"{statistics.median(samples):.2f}",
                             f"{percentile(samples, .95):.2f}", f"{min(samples):.2f}", len(samples),
                             *meta.values()])
    print(f"\nCSV: {csv_path}")


if __name__ == "__main__":
    main()
