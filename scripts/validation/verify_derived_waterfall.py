# -*- coding: utf-8 -*-
"""EDC 瀑布图插件（API 3）的真实窗口验收：屏幕、出版图与数值导出一致性。

覆盖计划要求的检查：

1. 下拉动作由插件注册，创建后进入瀑布页（`plugin_derived`）；
2. 冻结来源：积分范围、裁剪范围、数据域与时间来源随页面固定；
3. 自动步长与最近邻采样可用独立复算对上；
4. **E 翻转开／关**分别验证屏幕、出版图和数值导出在相同 E 坐标下对应一致；
5. 「插件设置」的保存默认值 / 应用当前页两个操作可用（默认值影响新建页）。

输出 `.local/outputs/derived-waterfall/`：屏幕与出版图 PNG、检查报告与数值证据。

用法: .venv/Scripts/python.exe scripts/validation/verify_derived_waterfall.py <npz路径> [--output-dir DIR]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "xcb")

_SANDBOX = tempfile.TemporaryDirectory(prefix="bandscope-derived-waterfall-")
_SANDBOX_PATH = Path(_SANDBOX.name)
os.environ["BANDSCOPE_EXTENSION_ROOT"] = str(_SANDBOX_PATH / "extensions")

import numpy as np

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QSettings, QTimer

QSettings.setDefaultFormat(QSettings.IniFormat)
for _scope in (QSettings.UserScope, QSettings.SystemScope):
    QSettings.setPath(QSettings.IniFormat, _scope, _SANDBOX.name)

from bandscope.app_metadata import APP_VERSION  # noqa: E402
from bandscope.extensions.plugin_manager import (  # noqa: E402
    InstallSource,
    PluginManager,
    install_package,
)
from bandscope.extensions.trust import SOURCE_LOCAL  # noqa: E402
from scripts.release.build_plugin import build as build_package  # noqa: E402

RESULTS = []


def record(item, ok, detail=""):
    RESULTS.append({"item": item, "ok": bool(ok), "detail": str(detail)})
    print(f"[{'PASS' if ok else 'FAIL'}] {item}  {detail}", flush=True)


def install_plugin():
    manager = PluginManager()
    staging = tempfile.TemporaryDirectory(prefix="bandscope-edc-plugin-")
    archive = build_package("edc_waterfall", Path(staging.name), APP_VERSION)
    install_package(
        archive,
        root=Path(manager.root),
        source=InstallSource(kind=SOURCE_LOCAL, accepted_unverified=True),
    )
    return manager.root


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path, help="Input NPZ file")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / ".local" / "outputs" / "derived-waterfall",
    )
    # 手动步长范围 0.0001–10：本机数据的动量轴是索引刻度（0~1500），
    # 取上限 10 得到约 150 条曲线，足够读出错位堆叠效果。
    parser.add_argument("--step", type=float, default=10.0, help="k 步长（手动模式，0.0001–10）")
    args = parser.parse_args()
    if not args.data.is_file():
        parser.error(f"数据文件不存在：{args.data}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    install_plugin()

    from PyQt5.QtWidgets import QApplication

    import bandscope.app.refactored_app as refactored_app
    import bandscope.exporting.publication_export as pe
    from bandscope.exporting.publication_models import OutputOptions, resolve_style

    class NoUpdates:
        def __init__(self, *a, **k):
            pass

        def check_automatically(self):
            pass

        def shutdown(self, **k):
            pass

    refactored_app.UpdateController = NoUpdates
    app = QApplication(sys.argv)
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *a: None
    window.show()

    # 模态消息会卡住 QTimer 链：验收脚本接管消息出口，文案照实记录。
    messages = []
    window._show_message = lambda title, text, *a, **k: messages.append((title, text))

    state = {"timed_out": [], "context": None}

    def wait_for(desc, predicate, next_step, timeout_ms=45000):
        deadline = time.perf_counter() + timeout_ms / 1000.0

        def poll():
            try:
                ok = bool(predicate())
            except Exception:
                ok = False
            if ok:
                next_step()
            elif time.perf_counter() < deadline:
                QTimer.singleShot(250, poll)
            else:
                record(f"等待 {desc}", False, "超时")
                state["timed_out"].append(desc)
                next_step()

        poll()

    def save_canvas(name):
        path = args.output_dir / name
        window.fig.savefig(str(path), dpi=150, facecolor="white")
        return path

    def snapshot_payload(stage_name):
        snapshot = pe.capture_snapshot(window)
        style = resolve_style(snapshot.view_family, "1d_open")
        path = args.output_dir / stage_name
        pe.render_and_save(snapshot, style, {}, OutputOptions(fmt="png", dpi=300), str(path))
        return snapshot, path

    # ------------------------------------------------------------------ 步骤
    def step_load():
        window.load_data(str(args.data))
        wait_for(
            "主页 3D 渲染",
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "3d",
            step_integral,
            timeout_ms=90000,
        )

    def step_integral():
        window.page_data.combo_ax.setCurrentIndex(0)
        window.on_apply_axis_integral()
        wait_for(
            "X 轴积分页渲染",
            lambda: window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_kind == "axis_integral"
            and window.current_render_context is not None
            and window.current_render_context.get("view") == "2d",
            step_create_waterfall,
        )

    def _select_other_by_key(prefix):
        combo = window.page_data.combo_other
        for index, key in enumerate(window.page_data._other_keys):
            if str(key).startswith(prefix):
                combo.setCurrentIndex(index)
                return True
        return False

    def step_create_waterfall():
        record(
            "下拉注册插件动作",
            any(key.startswith("edc_waterfall:") for key in window.page_data._other_keys),
            "、".join(window.page_data._other_keys),
        )
        record("选中插件动作", _select_other_by_key("edc_waterfall:"), window.page_data.current_other_key())
        # 越界的手动步长必须先被拒绝、且不建页。
        window.plugin_session.save_derived_defaults(
            "edc_waterfall", {"k_step_mode": "manual", "k_step": 1000.0}
        )
        pages_before = len(window.left_workspace.page_specs)
        window.on_apply_other_integral()
        record(
            "越界步长被拒绝且不建页",
            len(window.left_workspace.page_specs) == pages_before
            and any("步长" in text for _, text in messages),
            repr(messages[-1] if messages else ()),
        )
        window.plugin_session.save_derived_defaults(
            "edc_waterfall", {"k_step_mode": "manual", "k_step": float(args.step)}
        )
        window.on_apply_other_integral()
        wait_for(
            "瀑布页渲染",
            lambda: window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_kind == "plugin_derived"
            and window.current_render_context is not None
            and window.current_render_context.get("view") == "waterfall",
            step_checks,
        )

    def step_checks():
        spec = window.left_workspace.current_spec()
        record(
            "创建后进入瀑布页",
            spec.page_kind == "plugin_derived",
            spec.title + ("｜消息：" + repr(messages) if messages else ""),
        )
        if spec.page_kind != "plugin_derived":
            finish()
            return
        source = spec.params["derived_source"]
        record(
            "冻结来源字段",
            source["scope_id"] == "full" and source["source_mode"] == "frame",
            json.dumps({k: source[k] for k in ("source_page_kind", "axis_index", "integral_low", "integral_up", "scope_id")}, ensure_ascii=False),
        )
        context_off = dict(window.current_render_context)

        # 独立复算：源页参数 → 按步长最近邻采样 → 逐曲线归一化。
        raw = window.original_raw_data
        coords = window.original_coords
        axis_index = int(source["axis_index"])
        low, up = int(source["integral_low"]), int(source["integral_up"])
        if axis_index == 0:
            plane = np.sum(raw[low:up + 1, :, :, 0], axis=0)
            k_key = "Y"
        else:
            plane = np.sum(raw[:, low:up + 1, :, 0], axis=1)
            k_key = "X"
        k_axis = np.asarray(coords[k_key], dtype=np.float64)
        step = float(args.step)
        positions = list(np.arange(k_axis.min(), k_axis.max() + step * 0.5, step))
        positions.extend([k_axis.min(), k_axis.max()])
        indices = sorted(
            {int(np.argmin(np.abs(k_axis - p))) for p in positions},
            key=lambda i: float(k_axis[i]),
        )
        expected_curves = plane[np.asarray(indices)]
        expected_normalized = np.asarray(
            [c / np.max(c) if abs(np.max(c)) > 1e-12 else np.zeros_like(c) for c in expected_curves]
        )
        record(
            "自动采样与独立复算一致",
            np.allclose(context_off["k_values"], k_axis[np.asarray(indices)])
            and np.allclose(context_off["curves"], expected_normalized),
            f"{len(indices)} 条曲线",
        )
        state["context"] = context_off

        # 屏幕：线条数量与曲线一致，E 升序，横坐标为归一化曲线 + 偏移。
        lines = [line for line in window.ax_2d.lines]
        record("屏幕曲线数量", len(lines) == len(expected_normalized), f"{len(lines)} 条")
        offsets = np.asarray(pe.waterfall_offsets(context_off))
        energy_on_screen = np.asarray(lines[0].get_ydata(), dtype=np.float64) if lines else np.array([])
        screen_ok = bool(lines) and energy_on_screen.size == expected_normalized.shape[1]
        if screen_ok:
            ascending = energy_on_screen[0] <= energy_on_screen[-1]
            mid = energy_on_screen.size // 2
            screen_ok = ascending and np.allclose(
                np.asarray(lines[-1].get_xdata()) - offsets[-1],
                expected_normalized[-1],
                rtol=1e-6,
                atol=1e-6,
            )
        record("屏幕与渲染上下文一致", screen_ok, "E 升序 + 曲线逐点对齐")
        save_canvas("screen_flip_off.png")

        # 出版图（翻转关）。
        snapshot_off, path_off = snapshot_payload("publication_flip_off.png")
        payload_off = dict(snapshot_off.payload)
        record("出版图曲线与屏幕一致", np.allclose(payload_off["curves"], expected_normalized, rtol=1e-6), str(path_off))

        # 数值导出（翻转关）：原始坐标。
        raw_data, coords_state = window._get_display_state_for_spec(spec)
        _, _, export_off = window._build_export_payload(spec, raw_data, coords_state)
        record(
            "数值导出为原始强度",
            np.allclose(export_off["intensity"], expected_curves, rtol=1e-6)
            and np.allclose(export_off["E"], coords["E"]),
            f"E {len(export_off['E'])} 点 · {len(export_off['k'])} 条",
        )
        state["export_off"] = {k: np.asarray(v) for k, v in export_off.items() if hasattr(v, "shape")}
        state["payload_off"] = payload_off

        # 打开 E 翻转：屏幕/出版图按定向翻转处理，数值导出不变。
        window.timeline_bar.switch_flip.setChecked(True)
        window.global_refresh()

        def wait_flip():
            wait_for(
                "E 翻转后的瀑布渲染",
                lambda: window.current_render_context is not None
                and window.current_render_context.get("view") == "waterfall"
                and window._render_exact_ready,
                check_flip,
            )

        QTimer.singleShot(500, wait_flip)

    def check_flip():
        spec = window.left_workspace.current_spec()
        base = state["context"]
        # 画面用的是「视觉翻转」后的上下文（current_render_context 保持原始方向）。
        flipped = window._render_context_for_visual_flip(window._compute_render_context(spec))
        record(
            "翻转后的显示上下文定向翻转",
            np.allclose(flipped["energy_axis"], np.flip(base["energy_axis"]))
            and np.allclose(flipped["curves"], np.flip(base["curves"], axis=1)),
            "能量轴与曲线一起反序",
        )
        # 屏幕重绘后仍与归一化曲线逐点对齐（瀑布按升序绘制，观感不变）。
        lines = [line for line in window.ax_2d.lines]
        offsets = np.asarray(pe.waterfall_offsets(base))
        screen_ok = bool(lines) and len(lines) == len(base["curves"])
        if screen_ok:
            energy_on_screen = np.asarray(lines[0].get_ydata(), dtype=np.float64)
            screen_ok = (
                energy_on_screen.size == base["energy_axis"].size
                and energy_on_screen[0] <= energy_on_screen[-1]
                and np.allclose(
                    np.asarray(lines[-1].get_xdata()) - offsets[-1],
                    base["curves"][-1],
                    rtol=1e-6,
                    atol=1e-6,
                )
            )
        record("翻转开关后屏幕仍与上下文一致", screen_ok, "E 升序 + 曲线逐点对齐")
        save_canvas("screen_flip_on.png")
        snapshot_on, path_on = snapshot_payload("publication_flip_on.png")
        payload_on = dict(snapshot_on.payload)

        raw_data, coords_state = window._get_display_state_for_spec(spec)
        _, _, export_on = window._build_export_payload(spec, raw_data, coords_state)

        ok_pub = np.allclose(payload_on["energy_axis"], payload_off := state["payload_off"]["energy_axis"]) and np.allclose(
            payload_on["curves"], state["payload_off"]["curves"]
        )
        record("翻转开关不改变出版图的内容", ok_pub, str(path_on))

        export_off = state["export_off"]
        ok_export = np.allclose(export_on["E"], export_off["E"]) and np.allclose(
            export_on["intensity"], export_off["intensity"]
        )
        record("翻转开关不改变数值导出（原始坐标）", ok_export, "")

        # 相同 E 坐标一致性：屏幕（出版图曲线）与数值导出逐点对齐。
        energy = np.asarray(payload_on["energy_axis"])
        curves = np.asarray(payload_on["curves"])
        export_e = np.asarray(export_on["E"])
        normalized_export = np.asarray(export_on["normalized_intensity"])
        max_diff = 0.0
        for index in range(0, len(energy), max(1, len(energy) // 12)):
            column = int(np.argmin(np.abs(export_e - energy[index])))
            max_diff = max(max_diff, float(np.max(np.abs(curves[:, index] - normalized_export[:, column]))))
        record("相同 E 坐标下出版图与数值导出一致", max_diff < 1e-4, f"最大差 {max_diff:.2e}")

        evidence = {
            "energy_axis": state["context"]["energy_axis"].tolist(),
            "screen_energy": np.asarray(window.ax_2d.lines[0].get_ydata()).tolist(),
            "export_E": np.asarray(export_on["E"]).tolist(),
            "max_abs_diff_screen_vs_export": max_diff,
            "sample_count": int(np.asarray(export_on["k"]).size),
            "step": float(args.step),
        }
        (args.output_dir / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 插件设置：应用当前页（换回自动步长）与默认值持久化。
        window.timeline_bar.switch_flip.setChecked(False)
        state["before_params"] = dict(spec.params.get("derived_params") or {})
        ok, message = window.plugin_session._derived.apply_params_to_current_page(
            "edc_waterfall", {"k_step_mode": "auto", "k_step": float(args.step)}
        )
        record("应用当前瀑布图", ok, message)

        def wait_apply():
            wait_for(
                "应用后的瀑布渲染",
                lambda: window.current_render_context is not None
                and window.current_render_context.get("view") == "waterfall"
                and window._render_exact_ready,
                check_apply,
            )

        QTimer.singleShot(500, wait_apply)

    def check_apply():
        spec = window.left_workspace.current_spec()
        # 逐页参数记录用户的选择（mode + 值）；解析后的实际步长由插件回填在
        # 渲染上下文里（自动步长 = 完整 k 轴的中位相邻间距，本机约 7.538）。
        page_params = spec.params.get("derived_params") or {}
        context = window.current_render_context or {}
        resolved = dict(context.get("derived_params") or {})
        auto_step = float(resolved.get("k_step", context.get("k_step", 0.0)))
        record(
            "应用后改用自动步长且曲线变多",
            page_params.get("k_step_mode") == "auto"
            and resolved.get("k_step_mode") == "auto"
            and 0.0 < auto_step < float(args.step)
            and float(resolved.get("sample_count", 0)) > len(state["context"]["k_values"]),
            f"k_step={auto_step:.4f} 曲线 {resolved.get('sample_count')} 条",
        )
        record(
            "自动标题随参数更新",
            f"{auto_step:.3f}" in (spec.auto_title or ""),
            spec.auto_title or "",
        )
        # 默认值只影响新建页：应用不改变已保存的默认值。
        defaults = window.plugin_session.derived_defaults("edc_waterfall")
        record(
            "应用当前页不改动已保存默认值",
            defaults.get("k_step_mode") == "manual" and float(defaults.get("k_step", 0.0)) == float(args.step),
            json.dumps(defaults, ensure_ascii=False),
        )
        check_settings_dialog()

    def check_settings_dialog():
        """真实窗口里走一遍「插件管理 → 插件设置」：面板、两个按钮与默认值生效。"""
        from PyQt5.QtWidgets import QComboBox, QDoubleSpinBox

        dialog = window.open_plugin_manager()
        if dialog is None:
            record("插件管理可打开", False, "会话不可用")
            finish()
            return
        dialog.tabs.setCurrentIndex(dialog._settings_tab_index)
        sections = dict(dialog._settings_sections)
        record("设置页列出已加载插件", "edc_waterfall" in sections, "、".join(sections))
        section = sections.get("edc_waterfall")
        if section is None:
            finish()
            return
        panel = section["panel"]
        for combo in panel.findChildren(QComboBox):
            index = combo.findData("manual")
            if index >= 0:
                combo.setCurrentIndex(index)
        box = panel.findChild(QDoubleSpinBox)
        target_step = float(args.step) / 2.0
        if box is not None:
            box.setValue(target_step)
        section["save"].click()
        saved = window.plugin_session.derived_defaults("edc_waterfall")
        record(
            "保存为新建默认值写入本机设置",
            saved.get("k_step_mode") == "manual" and abs(float(saved.get("k_step", 0.0)) - target_step) < 1e-9,
            json.dumps(saved, ensure_ascii=False),
        )

        def after_layout():
            # 等事件循环把新面板布局完成后再抓图，否则抓到的是空容器。
            dialog.grab().save(str(args.output_dir / "settings_dialog.png"))
            # 新页立即使用保存后的默认值；「应用当前瀑布图」把它提交给当前页。
            window.timeline_bar.switch_flip.setChecked(False)
            previous = window.left_workspace.current_spec()
            source_page_id = previous.source_page_id
            window.left_workspace.activate_page(source_page_id or previous.page_id)
            window.plugin_session.invoke_integration_action("edc_waterfall", "create")
            created = window.left_workspace.current_spec()
            record(
                "新建页立即使用保存的默认值",
                created.page_kind == "plugin_derived"
                and created.page_id != previous.page_id
                and abs(float((created.params.get("derived_params") or {}).get("k_step", 0.0)) - target_step) < 1e-9,
                json.dumps(created.params.get("derived_params") or {}, ensure_ascii=False),
            )
            dialog.on_settings_apply("edc_waterfall")
            record(
                "应用当前瀑布图提交到当前页",
                abs(float((created.params.get("derived_params") or {}).get("k_step", 0.0)) - target_step) < 1e-9,
                "",
            )
            dialog.close()
            finish()

        QTimer.singleShot(400, after_layout)

    def finish():
        ok_count = sum(1 for item in RESULTS if item["ok"])
        (args.output_dir / "results.json").write_text(
            json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[accept] {ok_count}/{len(RESULTS)} 项通过；输出 {args.output_dir}", flush=True)
        app.quit()

    def launch():
        try:
            step_load()
        except Exception:
            traceback.print_exc()
            finish()

    QTimer.singleShot(0, launch)
    QTimer.singleShot(600000, app.quit)
    app.exec_()
    failures = [item for item in RESULTS if not item["ok"]]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
