"""Command-line paths and runtime isolation for local GUI validation."""
import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def arguments(description, *, data=True, dynamic=False, two_datasets=False):
    parser = argparse.ArgumentParser(description=description)
    if data:
        parser.add_argument("data", type=Path, help="Input NPZ file")
    if two_datasets:
        parser.add_argument("static_data", type=Path, help="Static NPZ file")
    parser.add_argument("--output-dir", type=Path,
                        default=REPO_ROOT / ".local" / "outputs" / description)
    if dynamic:
        parser.add_argument("--dynamic", action="store_true")
        parser.add_argument("--size", help="Window size, e.g. 1280x800")
        parser.add_argument("--hold", action="store_true", help="Leave the window open")
    args = parser.parse_args()
    for key in ("data", "static_data"):
        path = getattr(args, key, None)
        if path is not None and not path.is_file():
            parser.error(f"Input file does not exist: {path}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Embedded VTK needs a native Windows surface. Unit tests can use Qt's
    # offscreen platform, but full-window validation requires a desktop.
    if sys.platform == "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    elif sys.platform == "darwin":
        os.environ.setdefault("QT_QPA_PLATFORM", "cocoa")
    else:
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    # Reuse test isolation only in developer scripts, never application startup.
    from tests.support.environment import isolate_runtime
    isolate_runtime()
    return args
