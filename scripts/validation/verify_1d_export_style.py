"""真实窗口/实测数据的 1D 出图验收；输出样例供人工复核。

用法：python -m scripts.validation.verify_1d_export_style [NPZ] [--output-dir DIR]
默认使用 .local/data/WSe2_step.npz；隔离设置与插件目录，不改实验文件。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import traceback


def install_edc_waterfall_plugin() -> bool:
    """把真实构建的 EDC 瀑布图插件装进隔离扩展根（瀑布页由插件计算）。"""
    import tempfile
    from pathlib import Path

    try:
        from bandscope.app_metadata import APP_VERSION
        from bandscope.extensions.plugin_manager import (
            InstallSource,
            PluginManager,
            install_package,
        )
        from bandscope.extensions.trust import SOURCE_LOCAL
        from scripts.release.build_plugin import build as build_package

        manager = PluginManager()
        if manager.record("edc_waterfall") is not None:
            return True
        staging = tempfile.TemporaryDirectory(prefix="bandscope-edc-plugin-")
        try:
            archive = build_package("edc_waterfall", Path(staging.name), APP_VERSION)
            install_package(
                archive,
                root=Path(manager.root),
                source=InstallSource(kind=SOURCE_LOCAL, accepted_unverified=True),
            )
        finally:
            staging.cleanup()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 安装 edc_waterfall 失败：{type(exc).__name__}: {exc}", flush=True)
        return False


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", nargs="?", type=Path, default=root / ".local/data/WSe2_step.npz")
    parser.add_argument("--output-dir", type=Path, default=root / ".local/outputs/1d-export-style")
    args = parser.parse_args()
    if not args.data.is_file():
        parser.error(f"数据文件不存在：{args.data}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    os.environ["QT_QPA_PLATFORM"] = "windows" if sys.platform == "win32" else "xcb"
    from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path
    configure_qt_high_dpi()
    configure_qt_plugin_path()
    from tests.support.environment import isolate_runtime
    isolate_runtime()
    if not install_edc_waterfall_plugin():
        print("[warn] edc_waterfall 插件安装失败：瀑布图样例将按 UNTESTED 跳过。", flush=True)
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication
    import numpy as np
    import bandscope.app.refactored_app as app_module
    from bandscope.exporting.publication_export import capture_snapshot, render_and_save
    from bandscope.exporting.publication_models import OutputOptions, resolve_style
    from bandscope.exporting.curve_presentation import Figure1DPresentation, snapshot_curves
    from bandscope.ui.result_workspace import AnalysisPageSpec

    class NoUpdates:
        def __init__(self, *args, **kwargs):
            pass

        def check_automatically(self):
            pass

        def shutdown(self, **kwargs):
            pass

    app_module.UpdateController = NoUpdates
    app = QApplication(sys.argv)
    window = app_module.My3DAnalyzer()
    window._save_splitter_sizes = lambda *args: None
    window.show()
    results = []
    frames = []
    source_curves = []
    fail = [False]

    def finish(error=None):
        if error is not None:
            fail[0] = True
            results.append({"check": "exception", "status": "FAIL", "detail": str(error)})
            traceback.print_exc()
        report = {"data": str(args.data.resolve()), "checks": results,
                  "visual_review": "样例已生成；需查看 PNG 与编辑器截图后记录人工复核结果。",
                  "scope": "真实主窗口、宿主 1D/比较/log/瀑布数据路径、截图编辑器和 PNG/PDF；未重建冻结包。"}
        (args.output_dir / "validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        dialog = window.__dict__.get("_publication_dialog")
        if dialog is not None:
            dialog.close()
            dialog._pool.waitForDone(10000)
        window.close()
        app.quit()

    def guarded(callback):
        def run():
            try:
                callback()
            except Exception as error:
                finish(error)
        return run

    def wait_for(predicate, callback, title, timeout=60):
        deadline = time.monotonic() + timeout
        def poll():
            if predicate():
                callback()
            elif time.monotonic() > deadline:
                raise RuntimeError(f"等待超时：{title}")
            else:
                QTimer.singleShot(100, guarded(poll))
        guarded(poll)()

    def record(title, condition):
        results.append({"check": title, "status": "PASS" if condition else "FAIL"})
        if not condition:
            raise AssertionError(title)
        print(f"PASS {title}", flush=True)

    def activate(spec, next_step):
        window._seed_control_state_for_spec(spec)
        window.left_workspace.add_page(spec)
        wait_for(lambda: window.current_render_context is not None
                 and window.left_workspace.current_spec().page_id == spec.page_id
                 and window.current_render_context.get("view") in ("1d", "1d_comparison", "waterfall"),
                 next_step, spec.page_kind)

    def export_current(name, next_step):
        before = (window.left_workspace.current_spec().page_id,
                  tuple(window.plotter.camera.position), window._get_display_levels(),
                  float(window.core.raw_data[0, 0, 0, 0]))
        snapshot = capture_snapshot(window)
        p = Figure1DPresentation()
        curves = snapshot_curves(snapshot)
        if snapshot.view == "waterfall":
            p.figure.update(palette="gradient", waterfall_labels="auto", waterfall_label_size=7,
                            major_grid=True)
            p.curves[curves[len(curves) // 2].curve_id] = {"linewidth": 1.5, "color": "#C2236B"}
        else:
            p.figure.update(legend_position="outside_top", legend_columns=min(3, len(curves)), major_grid=True)
            for index, curve in enumerate(curves):
                p.curves[curve.curve_id] = {"label": f"实测帧 {frames[index] if index < len(frames) else index}",
                                           "marker": ("o", "s", "^")[index % 3], "markersize": 2.5,
                                           "markevery": 12, "marker_filled": False}
            if snapshot.view == "1d_comparison":
                p.order = [c.curve_id for c in reversed(curves)]
        for width in (89, 183):
            render_and_save(snapshot, resolve_style("1d", "1d_open"), {},
                            OutputOptions(width_mm=width, height_mm=85, dpi=300),
                            str(args.output_dir / f"{name}-{width}mm.png"), presentation=p)
        render_and_save(snapshot, resolve_style("1d", "1d_boxed"), {},
                        OutputOptions(width_mm=183, height_mm=85, dpi=300, fmt="pdf"),
                        str(args.output_dir / f"{name}.pdf"), presentation=p)
        after = (window.left_workspace.current_spec().page_id,
                 tuple(window.plotter.camera.position), window._get_display_levels(),
                 float(window.core.raw_data[0, 0, 0, 0]))
        record(f"{name}：PNG/PDF 与主视图状态隔离", before == after)
        window.open_publication_dialog()
        dialog = window._publication_dialog
        dialog.presentation = p.clone()
        dialog.curve_editor.set_snapshot(dialog.snapshot, dialog.presentation, dialog._current_style())
        dialog.edit_title.setText(f"WSe2 · {name}")
        dialog._on_presentation_changed()
        def preview_ready():
            record(f"{name}：1D 编辑窗口预览就绪", dialog._big_ready)
            dialog.grab().save(str(args.output_dir / f"dialog-{name}.png"))
            if name == "comparison":
                for index in range(dialog.curve_editor.tabs.count()):
                    dialog.curve_editor.tabs.setCurrentIndex(index)
                    app.processEvents()
                    dialog.grab().save(str(args.output_dir / f"dialog-comparison-tab-{index}.png"))
            dialog.reject()
            QTimer.singleShot(100, guarded(next_step))
        wait_for(lambda: dialog._big_ready, preview_ready, f"{name} 编辑器预览")

    def loaded():
        count = window.core.raw_data.shape[3]
        frames.extend(sorted(set([0, min(5, count - 1), min(10, count - 1)])))
        for frame in frames:
            spec = AnalysisPageSpec(window._make_page_id(), f"实测帧 {frame}", "energy_dos", "validation",
                                    params={"source_page_kind": "home", "source_mode": "frame", "source_t_index": frame, "t_index": frame,
                                            "source_t_low": frame, "source_t_up": frame})
            context = window._compute_render_context(spec)
            source = window._curve_snapshot_from_context(spec, context, label=f"实测帧 {frame}")
            source["curve_id"] = f"frame:{frame}"
            source_curves.append(source)
        record("实测数据加载与逐帧曲线生成", bool(source_curves))
        single = AnalysisPageSpec(window._make_page_id(), "实测单曲线", "energy_dos", "validation",
                                  params={"source_page_kind": "home", "source_mode": "frame", "source_t_index": frames[0], "t_index": frames[0]})
        activate(single, lambda: export_current("single", comparison))

    def comparison():
        spec = AnalysisPageSpec(window._make_page_id(), "实测多曲线", window.COMPARISON_PAGE_KIND, "validation",
                                params={"comparison_kind": "energy_dos", "base_curve": source_curves[0],
                                        "overlay_curves": source_curves[1:]})
        activate(spec, lambda: export_current("comparison", logarithm))

    def logarithm():
        record("宿主 log 衍生页创建", window._apply_log_to_current_curves(show_message=False))
        wait_for(lambda: window.current_render_context is not None
                 and window.left_workspace.current_spec().page_kind == window.LOG_1D_PAGE_KIND,
                 lambda: export_current("log", waterfall), "log 结果")

    def waterfall():
        # 瀑布页由插件计算：页面带冻结来源与逐页参数（步长模式 + 值），
        # 与下拉动作创建的页面结构一致；渲染、出版与导出仍走宿主路径。
        from bandscope.extensions.api import DerivedSource2D
        from bandscope.extensions.derived_host import (
            DERIVED_ACTION_KEY,
            DERIVED_PARAMS_KEY,
            DERIVED_PLUGIN_KEY,
            DERIVED_SOURCE_KEY,
        )

        coords = window.core.coords["Y"]
        length = window.core.raw_data.shape[0]
        step = max(float(np.ptp(coords)) / 28, 0.0001)
        source = DerivedSource2D(
            plugin_id="edc_waterfall",
            action_id="create",
            source_page_id="validation",
            source_page_kind="axis_integral",
            axis_index=0,
            integral_low=length // 3,
            integral_up=2 * length // 3,
            integral_mid=length // 2,
            source_mode="frame",
            frame_index=int(window.timeline_bar.slider_time.value()),
            time_low=0,
            time_up=0,
            scope_id="full",
            crop_rect=None,
            source_title="实测瀑布图来源",
            data_generation=int(window.shared_denoise_version),
        )
        spec = AnalysisPageSpec(window._make_page_id(), "实测瀑布图", window.PLUGIN_DERIVED_PAGE_KIND, "plugin_derived",
                                params={"source_mode": "frame",
                                        "source_t_index": int(window.timeline_bar.slider_time.value()),
                                        "source_t_low": 0, "source_t_up": 0,
                                        DERIVED_SOURCE_KEY: source.to_mapping(),
                                        DERIVED_PLUGIN_KEY: "edc_waterfall",
                                        DERIVED_ACTION_KEY: "create",
                                        DERIVED_PARAMS_KEY: {"k_step_mode": "manual", "k_step": step}})
        activate(spec, lambda: export_current("waterfall", lambda: finish()))

    def load():
        window.load_data(str(args.data))
        wait_for(lambda: window.current_render_context is not None and window.current_render_context.get("view") == "3d",
                 loaded, "主页数据加载", timeout=90)
    QTimer.singleShot(0, guarded(load))
    app.exec_()
    return 1 if fail[0] else 0


if __name__ == "__main__":
    raise SystemExit(main())
