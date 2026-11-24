"""Real My3DAnalyzer and background-compute acceptance for instant denoising.

Run from the repository root:
    .venv/Scripts/python.exe scripts/validation/verify_denoise_live.py --synthetic-only
    .venv/Scripts/python.exe scripts/validation/verify_denoise_live.py
    .venv/Scripts/python.exe scripts/validation/verify_denoise_live.py --data path/to/data.npz

The default data is .local/data/WSe2_step.npz. Synthetic mode writes a small,
fixed-seed NPZ and checks every committed array exactly. Artifacts are written
to .local/outputs/denoise-live/ by default.
"""

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time
import traceback

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "xcb")

from tests.support.environment import isolate_runtime

isolate_runtime()

from PyQt5.QtCore import QPoint, QThread, QTimer, Qt
from PyQt5.QtGui import QImage
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QAbstractButton, QApplication, QLabel, QMenu, QWidget

import numpy as np

from bandscope.processing.denoise_engines import DenoiseEngines
from bandscope.processing.denoise_config import GAUSSIAN_METHOD_NAME


class NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def create_settings_panel(self):
        return None

    def shutdown(self, **kwargs):
        pass


class LiveAcceptance:
    def __init__(self, args, data_path):
        self.args = args
        self.output_dir = args.output_dir
        self.prefix = "synthetic" if args.synthetic_only else "real"
        self.failures = []
        self.steps = []
        self.checks = []
        self.signatures = []
        self.computations = []
        self.screenshots = []
        self.unexecuted = [
            "Maintainer human visual review is not replaced by these generated screenshots.",
        ]
        self.started = time.perf_counter()
        self.report = {
            "mode": "synthetic" if args.synthetic_only else "real-data",
            "requested_data_path": str(args.data.resolve()),
            "data": {"path": str(data_path.resolve()), "shape": None, "dtype": None, "size": None},
            "qt_platform": os.environ.get("QT_QPA_PLATFORM"),
            "steps": self.steps,
            "checks": self.checks,
            "signatures": self.signatures,
            "computations": self.computations,
            "computation_count": 0,
            "computation_total_seconds": 0.0,
            "automatic_commit_signal_count": 0,
            "screenshots": self.screenshots,
            "unexecuted": self.unexecuted,
        }
        if args.synthetic_only:
            self.unexecuted.append(
                "This synthetic run does not claim WSe2 acceptance; real-data mode is a separate invocation."
            )

    def require(self, name, condition, detail=""):
        entry = {"name": name, "passed": bool(condition), "detail": str(detail)}
        self.checks.append(entry)
        print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
        if not condition:
            self.failures.append(name)
            raise AssertionError(f"{name}: {detail}")

    def step(self, name, detail=""):
        self.steps.append({
            "name": name,
            "executed": True,
            "elapsed_seconds": round(time.perf_counter() - self.started, 6),
            "detail": str(detail),
        })

    def skip(self, name, reason):
        self.unexecuted.append(f"{name}: {reason}")
        print(f"[SKIP] {name} — {reason}", flush=True)

    def capture(self, widget, filename, scenario):
        QTest.qWait(100)
        QApplication.processEvents()
        path = self.output_dir / f"{self.prefix}-{filename}"
        pixmap = widget.grab()
        self.require(f"Capture {path.name}", not pixmap.isNull() and pixmap.save(str(path)))
        image = QImage(str(path))
        size = path.stat().st_size if path.is_file() else 0
        self.require(
            f"Validate {path.name}",
            path.is_file() and size > 0 and not image.isNull(),
            f"bytes={size}, pixels={image.width()}x{image.height()}",
        )
        self.screenshots.append({
            "path": str(path.resolve()),
            "scenario": scenario,
            "image_size": [image.width(), image.height()],
            "bytes": size,
        })
        print(f"[SHOT] {path.resolve()}", flush=True)

    def capture_scene(self, window, filename):
        path = self.output_dir / f"{self.prefix}-{filename}"
        pixels = window.plotter.screenshot(str(path), return_img=True)
        image = QImage(str(path))
        size = path.stat().st_size if path.is_file() else 0
        self.require(
            f"Validate native VTK scene {path.name}",
            pixels is not None
            and np.asarray(pixels).ndim == 3
            and path.is_file()
            and size > 0
            and not image.isNull(),
            f"bytes={size}, pixels={image.width()}x{image.height()}",
        )
        self.screenshots.append({
            "path": str(path.resolve()),
            "scenario": "Native VTK scene after removing all nodes and restoring original data",
            "image_size": [image.width(), image.height()],
            "bytes": size,
        })
        print(f"[SHOT] {path.resolve()}", flush=True)

    def finalize(self, exit_code, exception=None):
        self.report["computation_count"] = len(self.computations)
        self.report["computation_total_seconds"] = round(
            sum(item["duration_seconds"] for item in self.computations), 6
        )
        self.report["elapsed_seconds"] = round(time.perf_counter() - self.started, 6)
        self.report["passed"] = exit_code == 0 and not self.failures
        self.report["failures"] = self.failures
        if exception is not None:
            self.report["exception"] = exception
        text = json.dumps(self.report, ensure_ascii=False, indent=2)
        report_path = self.output_dir / "report.json"
        report_path.write_text(text, encoding="utf-8")
        mode_path = self.output_dir / f"report-{self.prefix}.json"
        mode_path.write_text(text, encoding="utf-8")
        print(f"[REPORT] {report_path.resolve()}", flush=True)


def write_synthetic(path):
    rng = np.random.default_rng(20261124)
    shape = (8, 7, 9, 2)
    x = np.linspace(-0.7, 0.8, shape[0], dtype=np.float64)
    y = np.linspace(0.6, -0.5, shape[1], dtype=np.float64)
    energy = np.linspace(-1.2, 0.4, shape[2], dtype=np.float64)
    delay = np.asarray([0.0, 250.0], dtype=np.float64)
    xx, yy, ee, tt = np.meshgrid(x, y, energy, delay, indexing="ij")
    signal = np.exp(-((xx - 0.1) ** 2 + (yy + 0.1) ** 2) / 0.12)
    signal *= 1.0 + 0.2 * np.cos(ee * np.pi) + 0.001 * tt
    raw = (signal + rng.normal(0.0, 0.08, size=shape)).astype(np.float32)
    np.savez(
        path,
        sample=raw,
        kx=x,
        ky=y,
        E=energy,
        time=delay,
        kx_unit=np.asarray("Å⁻¹"),
        ky_unit=np.asarray("Å⁻¹"),
        E_unit=np.asarray("eV"),
        time_unit=np.asarray("fs"),
        axis_order=np.asarray(["X", "Y", "E", "T"]),
    )


def pump():
    QApplication.processEvents()


def wait_until(predicate, description, timeout_seconds=120):
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        pump()
        if predicate():
            return
        QTest.qWait(25)
    raise AssertionError(f"Timed out waiting for {description} after {timeout_seconds}s")


def nodes(window):
    return window.page_render.denoise_pipeline.export_state()["nodes"]


def node_id(window, index):
    return nodes(window)[index]["id"]


def assert_no_apply_buttons(acceptance, widget, scope):
    forbidden = []
    for button in widget.findChildren(QAbstractButton):
        text = button.text().replace(" ", "")
        if any(word in text for word in ("应用", "保存", "确定")):
            forbidden.append(text)
    acceptance.require(f"{scope} has no apply/save/OK buttons", not forbidden, forbidden)


def add_gaussian(acceptance, window, capture_popup=False):
    pipeline = window.page_render.denoise_pipeline
    previous_version = window.shared_denoise_version
    previous = {node["id"] for node in nodes(window)}
    pipeline.add_button.click()
    pump()
    popup = pipeline.method_popup
    acceptance.require("Real add-method popup opens", popup.isVisible())
    if capture_popup:
        acceptance.capture(popup, "01-add-popup-light.png", "Real algorithm popup before adding Gaussian")
    choice = next(item for item in popup.choices if item.name == GAUSSIAN_METHOD_NAME)
    QTest.mouseClick(choice, Qt.LeftButton, pos=choice.rect().center())
    pump()
    created = [node["id"] for node in nodes(window) if node["id"] not in previous]
    acceptance.require("Gaussian added through real method choice", len(created) == 1)
    dialog = pipeline._parameter_dialogs.get(created[0])
    acceptance.require("New node opens its real parameter dialog", dialog is not None and dialog.isVisible())
    acceptance.step("add Gaussian through real controls", created[0])
    return created[0], dialog, previous_version


def expected_signature(window):
    methods = window.page_render.get_denoise_settings()
    return methods, window._denoise_signature(methods)


def require_auto_pending(acceptance, window, signature, previous_version):
    if signature != window._denoise_signature([]):
        already_committed = (
            window.shared_denoise_signature == signature
            and window.shared_denoise_version > previous_version
            and window._pending_denoise_signature is None
        )
        acceptance.require(
            "Control signal automatically submits the current signature",
            already_committed or window._pending_denoise_signature == signature,
            {
                "pending": window._pending_denoise_signature,
                "state": window.page_render.denoise_pipeline._execution_state,
                "error": window.page_render.denoise_pipeline._execution_message,
                "global_methods": window.global_denoise_methods,
                "page_kind": getattr(window.left_workspace.current_spec(), "page_kind", None),
            },
        )
        acceptance.require(
            "Pipeline is running or has already applied without an apply action",
            window.page_render.denoise_pipeline._execution_state in ("running", "applied"),
        )


def wait_committed(acceptance, window, operation, previous_version, timeout_seconds):
    methods, signature = expected_signature(window)
    require_auto_pending(acceptance, window, signature, previous_version)
    empty_signature = window._denoise_signature([])

    def complete():
        if window.shared_denoise_signature != signature:
            return False
        if window.shared_denoise_version <= previous_version:
            return False
        if window._pending_denoise_signature is not None:
            return False
        if signature == empty_signature:
            return window.shared_denoised_raw_data is None
        return window.shared_denoised_raw_data is not None

    try:
        wait_until(
            complete,
            f"{operation} signature {signature}",
            timeout_seconds=timeout_seconds,
        )
    except AssertionError:
        state = window.page_render.denoise_pipeline._execution_state
        message = window.page_render.denoise_pipeline._execution_message
        raise AssertionError(f"{operation} did not commit; state={state}, error={message}, signature={signature}")
    acceptance.require(
        f"{operation} commits the exact current signature",
        window.shared_denoise_signature == signature,
    )
    acceptance.require(
        f"{operation} updates global_denoise_methods",
        window.global_denoise_methods == methods,
    )
    acceptance.require(
        f"{operation} clears pending state",
        window._pending_denoise_signature is None,
    )
    record = {
        "operation": operation,
        "signature": signature,
        "committed_signature": window.shared_denoise_signature,
        "methods": copy.deepcopy(methods),
        "version": window.shared_denoise_version,
        "matching_compute_records": [
            item["index"] for item in acceptance.computations if item["signature"] == signature
        ],
    }
    acceptance.signatures.append(record)
    acceptance.step(f"commit:{operation}", signature)
    return methods, signature


def check_shared(acceptance, window, original, operation, exact):
    shared = window.shared_denoised_raw_data
    acceptance.require(f"{operation} shared array exists", isinstance(shared, np.ndarray))
    acceptance.require(
        f"{operation} shared array shape and dtype",
        shared.shape == original.shape and shared.dtype == np.float32,
        f"shape={shared.shape}, dtype={shared.dtype}",
    )
    acceptance.require(
        f"{operation} source array is unchanged and not aliased",
        np.array_equal(window.original_raw_data, original) and not np.shares_memory(shared, original),
    )
    acceptance.require(f"{operation} shared result is immutable", not shared.flags.writeable)
    display, _coords = window._get_display_state_for_spec(window.left_workspace.home_spec())
    acceptance.require(
        f"{operation} home page resolves the shared array",
        display is shared,
    )
    if exact:
        expected = DenoiseEngines.apply_pipeline(
            original,
            window.global_denoise_methods,
            max_workers=1,
        )
        equal = np.array_equal(shared, expected, equal_nan=True)
        max_difference = float(np.nanmax(np.abs(shared - expected))) if shared.size else 0.0
        acceptance.require(
            f"{operation} synthetic array exactly matches single-worker reference",
            equal,
            f"max_abs_difference={max_difference}",
        )


def edit_sigma(acceptance, window, dialog, key, value, operation, timeout_seconds, exact, original):
    previous_version = window.shared_denoise_version
    control = dialog._controls[key]
    line_edit = control.lineEdit()
    line_edit.setFocus(Qt.OtherFocusReason)
    line_edit.selectAll()
    QTest.keyClicks(line_edit, str(value))
    QTest.keyClick(line_edit, Qt.Key_Return)
    pump()
    acceptance.require(f"{operation} entered sigma", abs(control.value() - value) < 1e-6)
    methods, _signature = wait_committed(
        acceptance, window, operation, previous_version, timeout_seconds
    )
    check_shared(acceptance, window, original, operation, exact)
    return methods


def remove_node(acceptance, window, target_id):
    previous_version = window.shared_denoise_version
    pipeline = window.page_render.denoise_pipeline
    card = pipeline._node_cards[target_id]
    outcome = {"triggered": False, "error": ""}

    def visible_menu():
        active = QApplication.activePopupWidget()
        if isinstance(active, QMenu):
            return active
        menus = [item for item in QApplication.topLevelWidgets() if isinstance(item, QMenu) and item.isVisible()]
        return menus[-1] if menus else None

    def trigger():
        menu = visible_menu()
        if menu is None:
            outcome["error"] = "No visible QMenu"
            return
        action = next((item for item in menu.actions() if item.text() == "移除节点"), None)
        if action is None:
            outcome["error"] = "Remove action missing"
            menu.close()
            return
        outcome["triggered"] = True
        action.trigger()
        menu.close()

    def unblock():
        menu = visible_menu()
        if menu is not None:
            outcome["error"] = outcome["error"] or "Menu timed out"
            menu.close()

    guard = QTimer(pipeline)
    guard.setSingleShot(True)
    guard.setInterval(3000)
    guard.timeout.connect(unblock)
    QTimer.singleShot(100, trigger)
    guard.start()
    QTest.mouseClick(card.more, Qt.LeftButton, pos=card.more.rect().center())
    guard.stop()
    guard.deleteLater()
    pump()
    acceptance.require(
        "Real node menu removes the selected node",
        outcome["triggered"] and not outcome["error"] and target_id not in pipeline._node_cards,
        outcome["error"],
    )
    acceptance.step("remove node through real menu", target_id)
    return previous_version


def verify_settings(acceptance, window):
    dialog = window.open_settings()
    pump()
    acceptance.require("Real settings dialog opens", dialog.isVisible())
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    keywords = ("去噪", "高斯", "小波", "中值滤波", "Savitzky")
    offending_labels = [text for text in labels if any(keyword in text for keyword in keywords)]
    denoise_objects = [
        widget.objectName() for widget in dialog.findChildren(QWidget)
        if "denoise" in widget.objectName().lower()
    ]
    acceptance.require(
        "Settings contains no global denoise parameters",
        not offending_labels and not denoise_objects,
        {"labels": offending_labels, "objects": denoise_objects},
    )
    acceptance.capture(dialog, "05-settings-no-denoise-light.png", "Real settings dialog without denoise parameters")
    dialog.close()
    pump()
    acceptance.step("verify settings has no denoise parameters")


def run_live(acceptance, data_path):
    import bandscope.app.refactored_app as refactored_app
    from bandscope.ui import theme

    refactored_app.UpdateController = NullUpdateController
    app = QApplication.instance() or QApplication([])
    theme.apply_app_font()
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.resize(1500, 920)
    window.showNormal()
    window.show()
    ui_thread = app.thread()
    real_execute = window.backend_manager.execute

    def observed_execute(job):
        if getattr(job, "operation", None) != "denoise_pipeline":
            return real_execute(job)
        methods = copy.deepcopy(job.payload["methods"])
        signature = window._denoise_signature(methods)
        started = time.perf_counter()
        record = {
            "index": len(acceptance.computations) + 1,
            "signature": signature,
            "methods": methods,
            "input_shape": list(job.payload["data"].shape),
            "off_ui_thread": QThread.currentThread() != ui_thread,
            "duration_seconds": None,
            "backend": None,
            "error": None,
        }
        acceptance.computations.append(record)
        try:
            result = real_execute(job)
            record["backend"] = result.backend
            return result
        except Exception as exc:
            record["error"] = repr(exc)
            raise
        finally:
            record["duration_seconds"] = round(time.perf_counter() - started, 6)
            print(
                f"[COMPUTE] #{record['index']} {record['duration_seconds']:.3f}s "
                f"off_ui={record['off_ui_thread']} signature={signature}",
                flush=True,
            )

    window.backend_manager.execute = observed_execute
    automatic_commits = {"count": 0}

    def count_commit():
        automatic_commits["count"] += 1
        acceptance.report["automatic_commit_signal_count"] = automatic_commits["count"]

    window.page_render.denoise_committed.connect(count_commit)
    timeout_seconds = 120 if acceptance.args.synthetic_only else 600

    try:
        loaded = window.load_data(str(data_path))
        acceptance.require("My3DAnalyzer load_data accepts the NPZ", loaded is not False)
        wait_until(
            lambda: window.original_raw_data is not None
            and window.current_render_context is not None
            and window._render_exact_ready,
            "main window initial exact 3D render",
            timeout_seconds=timeout_seconds,
        )
        original = np.array(window.original_raw_data, copy=True)
        acceptance.report["data"].update({
            "shape": list(original.shape),
            "dtype": str(original.dtype),
            "size": int(original.size),
        })
        acceptance.require("Main window loaded the real source", window.isVisible() and original.size > 0)
        acceptance.step("main window startup, load, and initial exact render", acceptance.report["data"])

        pipeline = window.page_render.denoise_pipeline
        assert_no_apply_buttons(acceptance, pipeline, "Denoise pipeline")
        first_id, first_dialog, initial_version = add_gaussian(acceptance, window, capture_popup=True)
        assert_no_apply_buttons(acceptance, first_dialog, "Gaussian parameter dialog")
        methods, _signature = wait_committed(
            acceptance, window, "add-single", initial_version, timeout_seconds
        )
        check_shared(
            acceptance,
            window,
            original,
            "add-single",
            acceptance.args.synthetic_only,
        )
        acceptance.require(
            "Gaussian changes the source samples",
            not np.array_equal(window.shared_denoised_raw_data, original),
        )
        acceptance.capture(first_dialog, "02-gaussian-applied-light.png", "Applied Gaussian parameter dialog")

        edited_methods = edit_sigma(
            acceptance,
            window,
            first_dialog,
            "sigma_kx",
            1.4,
            "edit-single-sigma-kx",
            timeout_seconds,
            acceptance.args.synthetic_only,
            original,
        )
        acceptance.require(
            "Sigma edit is present in committed methods",
            edited_methods[0]["params"]["sigma_kx"] == 1.4,
        )
        window._set_theme("dark")
        acceptance.capture(first_dialog, "03-gaussian-edited-dark.png", "Edited Gaussian parameter dialog in dark theme")
        window._set_theme("light")
        first_dialog.close()
        pump()

        run_duplicates = acceptance.args.synthetic_only or original.size <= 1_000_000
        if run_duplicates:
            second_id, second_dialog, second_add_version = add_gaussian(acceptance, window)
            methods, _signature = wait_committed(
                acceptance,
                window,
                "add-second-gaussian",
                second_add_version,
                timeout_seconds,
            )
            acceptance.require(
                "Two Gaussian nodes have independent identities and defaults",
                len(methods) == 2
                and methods[0]["id"] == first_id
                and methods[1]["id"] == second_id
                and first_id != second_id
                and methods[0]["params"]["sigma_kx"] == 1.4
                and methods[1]["params"]["sigma_kx"] == 1.0,
            )
            check_shared(
                acceptance,
                window,
                original,
                "add-second-gaussian",
                acceptance.args.synthetic_only,
            )
            edit_sigma(
                acceptance,
                window,
                second_dialog,
                "sigma_ky",
                2.0,
                "edit-second-gaussian-only",
                timeout_seconds,
                acceptance.args.synthetic_only,
                original,
            )
            current_nodes = nodes(window)
            acceptance.require(
                "Editing the second Gaussian leaves the first unchanged",
                current_nodes[0]["params"] == {
                    "sigma_kx": 1.4,
                    "sigma_ky": 1.0,
                    "sigma_e": 1.0,
                }
                and current_nodes[1]["params"] == {
                    "sigma_kx": 1.0,
                    "sigma_ky": 2.0,
                    "sigma_e": 1.0,
                },
                [node["params"] for node in current_nodes],
            )
            second_dialog.close()
            first_remove_version = remove_node(acceptance, window, first_id)
            methods, _signature = wait_committed(
                acceptance,
                window,
                "remove-first-of-two",
                first_remove_version,
                timeout_seconds,
            )
            acceptance.require(
                "Removal leaves exactly the independent second Gaussian",
                len(methods) == 1
                and methods[0]["id"] == second_id
                and methods[0]["params"]["sigma_ky"] == 2.0,
            )
            check_shared(
                acceptance,
                window,
                original,
                "remove-first-of-two",
                acceptance.args.synthetic_only,
            )
            previous_version = remove_node(acceptance, window, second_id)
        else:
            acceptance.skip(
                "Repeated Gaussian computation on the large real array",
                "Resource limit: single-node add/edit/remove were executed; duplicate large-array calculations were not started.",
            )
            previous_version = remove_node(acceptance, window, first_id)
        methods, empty_signature = wait_committed(
            acceptance,
            window,
            "remove-final-node",
            previous_version,
            timeout_seconds,
        )
        acceptance.require("Empty pipeline is committed", methods == [] and nodes(window) == [])
        acceptance.require(
            "Removing the final node clears global methods and shared array",
            window.global_denoise_methods == [] and window.shared_denoised_raw_data is None,
        )
        acceptance.require(
            "Original source remains byte-for-byte unchanged",
            np.array_equal(window.original_raw_data, original),
        )
        display, _coords = window._get_display_state_for_spec(window.left_workspace.home_spec())
        acceptance.require(
            "Removing the final node automatically restores original display data",
            np.array_equal(np.asarray(display), original, equal_nan=True),
        )
        acceptance.require(
            "Empty restoration needs no denoise compute job",
            not any(item["signature"] == empty_signature for item in acceptance.computations),
        )
        wait_until(
            lambda: window._render_exact_ready,
            "exact render after restoring original data",
            timeout_seconds=timeout_seconds,
        )
        acceptance.require(
            "All observed denoise jobs ran off the UI thread",
            bool(acceptance.computations)
            and all(item["off_ui_thread"] for item in acceptance.computations),
            f"jobs={len(acceptance.computations)}",
        )
        if acceptance.args.synthetic_only:
            acceptance.require(
                "Synthetic run executes all five unique non-empty pipelines",
                len(acceptance.computations) == 5,
                f"jobs={len(acceptance.computations)}",
            )
        verify_settings(acceptance, window)
        acceptance.capture(window, "06-main-restored-light.png", "Final real main window after restoring original data")
        acceptance.capture_scene(window, "07-scene-restored-light.png")
        acceptance.step("all live denoise acceptance steps completed")
    finally:
        pipeline = getattr(window, "page_render", None)
        if pipeline is not None:
            pipeline.denoise_pipeline._close_all_dialogs()
            pipeline.denoise_pipeline.method_popup.close()
        window.close()
        pump()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        type=Path,
        default=REPO_ROOT / ".local" / "data" / "WSe2_step.npz",
        help="NPZ path; defaults to .local/data/WSe2_step.npz",
    )
    parser.add_argument("--synthetic-only", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / ".local" / "outputs" / "denoise-live",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    data_path = args.output_dir / "synthetic-input.npz" if args.synthetic_only else args.data
    acceptance = LiveAcceptance(args, data_path)
    exit_code = 0
    exception = None
    try:
        if args.synthetic_only:
            write_synthetic(data_path)
        elif not data_path.is_file():
            acceptance.skip(
                "Real My3DAnalyzer WSe2/data acceptance",
                f"Input file does not exist: {data_path.resolve()}. Synthetic results, if present separately, are not real-data evidence.",
            )
            acceptance.report["data"]["missing"] = True
            exit_code = 2
        if exit_code == 0:
            run_live(acceptance, data_path)
    except Exception:
        exception = traceback.format_exc()
        print(exception, flush=True)
        exit_code = 1
    finally:
        acceptance.finalize(exit_code, exception)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
