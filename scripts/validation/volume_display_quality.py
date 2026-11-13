# -*- coding: utf-8 -*-
"""Real-data acceptance for independent volume display, quality and Blender export."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from bandscope.exporting.blender_jobs import BlenderProjectExportJob
from bandscope.exporting.blender_project import BlenderProjectRequest
from bandscope.exporting.publication_export import capture_snapshot, render_and_save
from bandscope.exporting.publication_models import OutputOptions, resolve_style
from bandscope.processing.display_transfer import volume_display_unit_distance


def _hidden_kwargs():
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {"startupinfo": startup, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _png_metrics(path):
    from PIL import Image

    with Image.open(path) as image:
        rgba = np.asarray(image.convert("RGBA"))
    return {
        "visible_pixels": int(np.count_nonzero(rgba[..., 3])),
        "alpha_min": int(rgba[..., 3].min()),
        "alpha_max": int(rgba[..., 3].max()),
    }


class VolumeQualityAcceptance:
    def __init__(self, args):
        self.args = args
        self.output = args.output_dir.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.logs = REPO_ROOT / ".local/logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.records = []
        self.app = None
        self.window = None
        self.current = None
        self.current_snapshot = None
        self.science = None
        self.deadline = 0.0

    def record(self, name, status, detail=""):
        item = {"name": name, "status": status, "detail": str(detail)}
        self.records.append(item)
        print(f"[{status}] {name}: {detail}", flush=True)
        self._write_report()

    def _write_report(self):
        payload = {
            "records": self.records,
            "manual_visual_review": (
                "Review background occlusion, weak-band visibility and voxel stepping in the saved images. "
                "No data smoothing was introduced and no sharpness claim is made by this script."
            ),
        }
        (self.output / "volume-quality-report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        lines = ["# 体渲染显示与质量真实数据验收", ""]
        lines += [f"- **{item['status']}** {item['name']}：{item['detail']}" for item in self.records]
        lines += ["", "人工复核：背景遮挡、弱带可见性与体素台阶。未使用数据平滑，不据此宣称更锐利。"]
        (self.output / "volume-quality-report.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8",
        )

    def run(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "xcb")
        from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path
        configure_qt_high_dpi()
        configure_qt_plugin_path()
        from PyQt5.QtCore import QTimer
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        import bandscope.app.refactored_app as refactored_app

        class NullUpdateController:
            def __init__(self, *args, **kwargs):
                pass

            def check_automatically(self):
                pass

            def shutdown(self, **kwargs):
                pass

        refactored_app.UpdateController = NullUpdateController
        self.window = refactored_app.My3DAnalyzer()
        self.window._save_splitter_sizes = lambda *_args: None
        self.window.show()
        QTimer.singleShot(300, self._next_dataset)
        self.app.exec_()
        self._write_report()
        return 1 if any(item["status"] == "FAIL" for item in self.records) else 0

    def _next_dataset(self):
        from PyQt5.QtCore import QTimer

        if self.window is not None:
            self.window.close()
            self.window = None
        if not self.args.data:
            self.app.quit()
            return
        self.current = self.args.data.pop(0)
        if not self.current.is_file():
            self.record(self.current.name, "NOT RUN", "数据文件不存在")
            QTimer.singleShot(0, self._next_dataset)
            return
        self.window = self.app.activeWindow()
        if self.window is None or not hasattr(self.window, "load_data"):
            import bandscope.app.refactored_app as refactored_app
            self.window = refactored_app.My3DAnalyzer()
            self.window._save_splitter_sizes = lambda *_args: None
            self.window.show()
        self.window.load_data(str(self.current.resolve()))
        self.record(self.current.name, "INFO", "开始真实窗口与显示链路验收")
        self._wait_ready(self._continuous_case)

    def _wait_ready(self, callback, timeout=180):
        from PyQt5.QtCore import QTimer
        self.deadline = time.perf_counter() + timeout

        def poll():
            try:
                context = getattr(self.window, "current_render_context", None)
                if context is not None and self.window._render_exact_ready:
                    callback()
                elif time.perf_counter() < self.deadline:
                    QTimer.singleShot(100, poll)
                else:
                    self._fail("等待精确显示结果超时")
            except Exception as exc:
                self._fail(f"{type(exc).__name__}: {exc}")

        poll()

    def _case_dir(self, name):
        path = self.output / f"{self.current.stem}-{name}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _continuous_case(self):
        context = self.window.current_render_context
        if context.get("view") != "3d":
            self._fail(f"预期 3D，实际为 {context.get('view')}")
            return
        style = self.window._enhancement_background_style()
        if style.get("display_mode") != "independent":
            self._fail("全新页面没有使用独立显示默认")
            return
        self.science = np.array(context["data"], copy=True)
        self.initial_frame = self.window.timeline_bar.slider_time.value()
        snapshot = capture_snapshot(self.window)
        self.current_snapshot = snapshot
        directory = self._case_dir("continuous")
        self.window.plotter.screenshot(str(directory / "vtk.png"))
        render_and_save(
            snapshot, resolve_style("3d", None), {},
            OutputOptions(width_mm=80, height_mm=80, dpi=90), str(directory / "publication.png"),
        )
        self.window.grab().save(str(directory / "window.png"))
        fields = snapshot.payload["enhancement"]
        q = (np.asarray(snapshot.payload["volume"], dtype=float) - fields["background_level_info"]["data_min"]) / fields["background_level_info"]["span"]
        weak = (q > 0.0) & (q < 0.01) & np.isfinite(q)
        weak_visible = int(np.count_nonzero(fields["alpha"][weak] > 0.0))
        mapper = self.window.volume_session.volume.mapper
        expected = 0.5 * volume_display_unit_distance(snapshot.payload["full_shape"])
        if abs(mapper.GetSampleDistance() - expected) > max(1e-6, expected * 1e-5):
            raise AssertionError(f"静止标准采样距离 {mapper.GetSampleDistance()} != {expected}")
        self.window.volume_session._set_interactive_quality("preview", snapshot.payload["render_quality"])
        preview_distance = mapper.GetSampleDistance()
        self.window.volume_session._set_interactive_quality("exact", snapshot.payload["render_quality"])
        self.record(self.current.name + " 连续体与质量", "PASS", {
            "range": fields["background_range"], "weak_visible_voxels": weak_visible,
            "sample_distance": mapper.GetSampleDistance(), "preview_distance": preview_distance,
            "render_ms": self.window._last_render_ms,
            "images": [str(directory / name) for name in ("vtk.png", "publication.png", "window.png")],
        })
        self._dynamic_case()

    def _dynamic_case(self):
        slider = self.window.timeline_bar.slider_time
        if slider.maximum() <= slider.minimum():
            self.record(self.current.name + " 动态帧", "NOT RUN", "无时间轴")
            self._local_case()
            return
        slider.setValue((slider.minimum() + slider.maximum()) // 2)
        self._wait_ready(self._dynamic_ready)

    def _dynamic_ready(self):
        directory = self._case_dir("dynamic")
        self.window.plotter.screenshot(str(directory / "vtk.png"))
        self.record(self.current.name + " 动态帧", "PASS", {
            "frame": self.window.timeline_bar.slider_time.value(),
            "render_ms": self.window._last_render_ms,
            "image": str(directory / "vtk.png"),
        })
        self._local_case()

    def _local_case(self):
        self.window._add_enhancement_region()
        self._wait_ready(self._local_ready)

    def _local_ready(self):
        fields = self.window._enhancement_display(self.window.current_render_context)
        if not fields or not fields.get("active"):
            self._fail("局部增强区域未产生有效显示通道")
            return
        directory = self._case_dir("local")
        self.window.plotter.screenshot(str(directory / "vtk.png"))
        self.window.grab().save(str(directory / "controls.png"))
        self.record(self.current.name + " 局部增强", "PASS", {
            "regions": [region["region_id"] for region in fields["regions"]],
            "render_ms": self.window._last_render_ms,
            "images": [str(directory / "vtk.png"), str(directory / "controls.png")],
        })
        state = self.window._enhancement_state()
        for region in list(state["regions"]):
            self.window._delete_enhancement_region(region["region_id"])
        self._wait_ready(self._strong_case)

    def _strong_case(self):
        self.window.timeline_bar.slider_time.setValue(self.initial_frame)
        values = {"threshold": 21.739, "transition": 0.0, "gamma": 1.3, "density": 40.0}
        for name, value in values.items():
            slider, _spin, scale = self.window.page_render.display_parameters[name]
            slider.setValue(int(round(value * scale)))
        self.window._commit_enhancement_controls(apply_all=True)
        self.window._enhancement_refresh()
        self._wait_ready(self._strong_ready)

    def _strong_ready(self):
        snapshot = capture_snapshot(self.window)
        directory = self._case_dir("strong")
        self.window.plotter.screenshot(str(directory / "vtk.png"))
        render_and_save(
            snapshot, resolve_style("3d", None), {},
            OutputOptions(width_mm=80, height_mm=80, dpi=90), str(directory / "publication.png"),
        )
        before = self.current_snapshot.payload["enhancement"]
        after = snapshot.payload["enhancement"]
        np.testing.assert_array_equal(before["color"], after["color"])
        info = after["background_level_info"]
        q = (np.asarray(snapshot.payload["volume"], dtype=float) - info["data_min"]) / info["span"]
        below = q <= after["background_style"]["opacity"]["threshold"] / 100.0
        if np.any(after["alpha"][below] != 0.0):
            raise AssertionError("强信号自定义参数的阈值以下仍有底雾")
        np.testing.assert_array_equal(self.science, self.window.current_render_context["data"])
        self.record(self.current.name + " 强信号与数组保护", "PASS", {
            "opacity": after["background_style"]["opacity"],
            "below_threshold_transparent": True,
            "color_unchanged": True,
            "science_unchanged": True,
            "images": [str(directory / "vtk.png"), str(directory / "publication.png")],
        })
        self.current_snapshot = snapshot
        self._lock_case()

    def _lock_case(self):
        self.window.on_toggle_color_lock(True)
        self._wait_ready(self._lock_ready)

    def _lock_ready(self):
        locked = capture_snapshot(self.window)
        expected = locked.payload["level_info"]["data_min"], locked.payload["level_info"]["data_max"]
        slider = self.window.timeline_bar.slider_time
        if slider.maximum() > slider.minimum():
            slider.setValue(slider.maximum())
            self._wait_ready(lambda: self._lock_compare(expected))
        else:
            self._lock_compare(expected)

    def _lock_compare(self, expected):
        snapshot = capture_snapshot(self.window)
        actual = snapshot.payload["level_info"]["data_min"], snapshot.payload["level_info"]["data_max"]
        np.testing.assert_allclose(actual, expected)
        self.record(self.current.name + " 相同绝对色阶", "PASS", {
            "locked_range": expected, "actual_range": actual,
        })
        self.window.on_toggle_color_lock(False)
        self._slice_case()

    def _slice_case(self):
        self.window.page_data.combo_ax.setCurrentIndex(0)
        self.window.on_apply_axis_integral()
        self._wait_ready(self._slice_ready)

    def _slice_ready(self):
        if self.window.current_render_context.get("view") != "2d":
            self._fail("E–k 页面没有生成 2D 结果")
            return
        snapshot = capture_snapshot(self.window)
        directory = self._case_dir("ek")
        self.window.fig.savefig(str(directory / "matplotlib.png"), dpi=150)
        render_and_save(
            snapshot, resolve_style("2d", None), {},
            OutputOptions(width_mm=80, height_mm=70, dpi=90), str(directory / "publication.png"),
        )
        self.record(self.current.name + " E–k 图", "PASS", {
            "images": [str(directory / "matplotlib.png"), str(directory / "publication.png")],
        })
        if self.args.blender is None:
            self.record(self.current.name + " Blender 对照", "NOT RUN", "未提供 Blender")
            self._finish_dataset()
        else:
            self._blender_case()

    def _blender_case(self):
        directory = self._case_dir("blender")
        blend = directory / "comparison.blend"
        BlenderProjectExportJob(
            BlenderProjectRequest([self.current_snapshot]), self.args.blender, blend,
            overwrite=True, log_path=self.logs / f"{self.current.stem}-volume-quality-blender.log",
        ).run()
        inspection = directory / "inspection.json"
        render = directory / "blender.png"
        command = [
            str(self.args.blender), "--background", "--factory-startup", "--python",
            str(REPO_ROOT / "scripts/validation/blender_scene_acceptance.py"), "--",
            "inspect", "--blend", str(blend), "--report", str(inspection),
            "--long-edge", "640", "--samples", "16", "--render", str(render),
        ]
        completed = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=600,
            **_hidden_kwargs(),
        )
        (self.logs / f"{self.current.stem}-volume-quality-inspection.log").write_text(
            completed.stdout or "", encoding="utf-8",
        )
        if completed.returncode != 0:
            raise RuntimeError("Blender 重开渲染失败：" + "\n".join((completed.stdout or "").splitlines()[-15:]))
        result = json.loads(inspection.read_text(encoding="utf-8"))
        metadata = json.loads((directory / "comparison.assets/scene.json").read_text(encoding="utf-8"))
        edit = metadata["entries"][0]["bandscope_edit"]
        if metadata["schema_version"] != 11 or edit["format_version"] != 2:
            raise AssertionError("Blender 对照不是 schema 11 / 编辑格式 2")
        self.record(self.current.name + " Blender 同快照对照", "PASS", {
            "projection_error_pixels": result["max_projection_error_pixels"],
            "png": _png_metrics(render),
            "schema": 11,
            "display_mode": edit["display_style"].get("display_mode", "legacy"),
            "image": str(render),
        })
        self._finish_dataset()

    def _finish_dataset(self):
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, self._next_dataset)

    def _fail(self, detail):
        self.record(self.current.name if self.current else "验收", "FAIL", detail)
        print(traceback.format_exc(), flush=True)
        self._finish_dataset()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "data", type=Path, nargs="*",
        default=[
            REPO_ROOT / ".local/data/WSe2_step.npz",
            Path("D:/ARPES数据/npz_files/西湖/Au_cut.npz"),
            Path("D:/ARPES数据/npz_files/西湖/NiHITP_cut.npz"),
        ],
    )
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / ".local/outputs/volume-display-quality")
    args = parser.parse_args()
    if args.blender is not None and not args.blender.is_file():
        parser.error(f"Blender 不存在：{args.blender}")
    return VolumeQualityAcceptance(args).run()


if __name__ == "__main__":
    sys.exit(main())
