# -*- coding: utf-8 -*-
"""图片导出验收：真实窗口驱动，全视图族/全样式导出 + 对话框交互 + 状态隔离。

用法: .venv/Scripts/python.exe scripts/export_acceptance.py
输出: design/publication_export_acceptance/（样例图、拼图、验证报告 validation_report.md）

注意：全程 QTimer 链驱动，不在槽里手动 processEvents。
"""
import os
import sys
import time
import traceback

import faulthandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fh = open(os.path.join("smoke_output", "acceptance_faulthandler.log"), "w", encoding="utf-8")
faulthandler.enable(_fh)

from qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

OUT_DIR = os.path.join("design", "publication_export_acceptance")
NPZ = os.path.join("smoke_data", "NiHITP_calibrated_2.npz")

RESULTS = []   # (检查项, PASS/FAIL/UNTESTED, 说明)
TIMINGS = []   # (产物, 秒)


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def log(msg):
    print(msg, flush=True)


def record(item, status, detail=""):
    RESULTS.append((item, status, detail))
    log(f"[accept] {status:8s} {item}  {detail}")


def wait_for(desc, predicate, next_step, timeout_ms=45000):
    deadline = time.perf_counter() + timeout_ms / 1000.0

    def poll():
        try:
            ok = bool(predicate())
        except Exception:
            ok = False
        if ok:
            log(f"[accept] ready: {desc}")
            next_step()
        elif time.perf_counter() < deadline:
            QTimer.singleShot(250, poll)
        else:
            record(desc, "FAIL", "等待超时")
            next_step()

    poll()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    app = QApplication(sys.argv)

    import refactored_app
    refactored_app.UpdateController = _NullUpdateController

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()

    import publication_export as pe
    from publication_models import (
        OutputOptions, load_overrides, load_style_id, resolve_style,
    )
    from render_core import VisualEngine

    RESULTS.clear()
    TIMINGS.clear()
    pages = {}  # 记录创建的关键页 id，供后续激活使用

    def science_state():
        cam = window.plotter.camera
        return {
            "camera": (tuple(cam.position), tuple(cam.focal_point), tuple(cam.up)),
            "last_range": VisualEngine._last_data_range,
            "locked_range": VisualEngine._locked_data_range,
            "page_id": (
                window.left_workspace.current_spec().page_id
                if window.left_workspace.current_spec() is not None else None
            ),
            "clip_ranges": list(window.clip_ranges) if window.clip_ranges else None,
            "slice": dict(window.home_slice_info) if window.home_slice_info else None,
        }

    def check_state_unchanged(label, before):
        after = science_state()
        same = before == after
        record(f"状态隔离 · {label}", "PASS" if same else "FAIL",
               "" if same else f"before={before} after={after}")

    def do_export(item, style_id, fmt, filename, dpi=600, expect_family=None):
        """捕获当前页快照并按指定样式导出；返回路径或 None。"""
        try:
            t0 = time.perf_counter()
            snap = pe.capture_snapshot(window)
            if expect_family is not None and snap.view_family != expect_family:
                record(item, "FAIL", f"视图族不符：{snap.view_family} != {expect_family}")
                return None
            style = resolve_style(snap.view_family, style_id)
            overrides = load_overrides(window.settings, snap.view_family, style_id)
            options = OutputOptions(width_mm=89.0, dpi=dpi, fmt=fmt)
            path = os.path.join(OUT_DIR, filename)
            pe.render_and_save(snap, style, overrides, options, path)
            dt = time.perf_counter() - t0
            TIMINGS.append((filename, dt))
            size_kb = os.path.getsize(path) / 1024.0
            record(item, "PASS", f"{dt:.2f}s · {size_kb:.0f} KB")
            return path
        except Exception as exc:  # noqa: BLE001 - 验收脚本记录而非中断
            record(item, "FAIL", f"{type(exc).__name__}: {exc}")
            log(traceback.format_exc())
            return None

    # ---------------------------------------------------------------- 阶段链

    def step_load():
        log(f"[accept] loading {NPZ}")
        window.load_data(NPZ)
        wait_for(
            "3D 主页渲染",
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "3d",
            step_3d_exports, timeout_ms=60000,
        )

    def step_3d_exports():
        before = science_state()
        for style_id in ("3d_minimal", "3d_boxed", "3d_horizontal"):
            do_export(f"3D 样式 {style_id}", style_id, "png", f"3d_{style_id}.png",
                      expect_family="3d")
        check_state_unchanged("3D 三样式导出", before)
        step_2d_slice()

    def step_2d_slice():
        window.home_slice_info = {"axis": 2, "index": int(window.core.raw_data.shape[2] // 2)}
        window.global_refresh()
        wait_for(
            "主页单层切片 2D 渲染",
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "2d"
            and window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_kind == "home",
            step_2d_exports, timeout_ms=30000,
        )

    def step_2d_exports():
        before = science_state()
        for style_id in ("2d_boxed", "2d_topbar", "2d_open"):
            do_export(f"2D 样式 {style_id}", style_id, "png", f"2d_{style_id}.png",
                      expect_family="2d")
        do_export("2D PDF", "2d_boxed", "pdf", "2d_2d_boxed.pdf", expect_family="2d")
        check_state_unchanged("2D 三样式 + PDF 导出", before)
        # 还原 3D 主页
        window.home_slice_info = None
        window.global_refresh()
        QTimer.singleShot(1500, step_axis_integral)

    def step_axis_integral():
        try:
            window.on_apply_axis_integral()
        except Exception as exc:  # noqa: BLE001
            record("axis_integral 页创建", "FAIL", str(exc))
            step_energy_dos()
            return
        spec = window.left_workspace.current_spec()
        if spec is None or spec.page_kind != "axis_integral":
            record("axis_integral 页创建", "FAIL", "未成为当前页")
            step_energy_dos()
            return
        pages["axis_integral"] = spec.page_id
        wait_for(
            "axis_integral 页渲染",
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "2d"
            and window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_id == spec.page_id,
            lambda: (do_export("axis_integral 页导出", "2d_boxed", "png",
                               "page_axis_integral.png", expect_family="2d"),
                     QTimer.singleShot(300, step_energy_dos))[1],
            timeout_ms=30000,
        )

    def _select_other(text):
        combo = window.page_data.combo_other
        if combo.findText(text) < 0:
            return False
        combo.setCurrentText(text)
        return True

    def step_energy_dos():
        if not _select_other("能级态密度"):
            record("energy_dos 页创建", "UNTESTED", "下拉项不存在")
            step_waterfall()
            return
        try:
            window.on_apply_other_integral()
        except Exception as exc:  # noqa: BLE001
            record("energy_dos 页创建", "FAIL", str(exc))
            step_waterfall()
            return
        wait_for(
            "energy_dos 页渲染",
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "1d"
            and window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_kind == "energy_dos",
            step_energy_dos_export, timeout_ms=30000,
        )

    def step_energy_dos_export():
        spec = window.left_workspace.current_spec()
        if spec is None or spec.page_kind != "energy_dos":
            record("energy_dos 页创建", "FAIL", "未成为当前页")
            step_waterfall()
            return
        before = science_state()
        for style_id in ("1d_open", "1d_boxed", "1d_compact"):
            do_export(f"1D 样式 {style_id}", style_id, "png", f"1d_{style_id}.png",
                      expect_family="1d")
        do_export("1D PDF", "1d_open", "pdf", "1d_1d_open.pdf", expect_family="1d")
        check_state_unchanged("1D 三样式 + PDF 导出", before)
        step_waterfall()

    def step_waterfall():
        # 瀑布图只能从 axis_integral 类页面创建：先切回该页
        axis_page = pages.get("axis_integral")
        if axis_page is None:
            record("waterfall_edc 页创建", "UNTESTED", "axis_integral 页不可用")
            step_edc()
            return
        window.left_workspace.activate_page(axis_page)

        def _apply():
            if not _select_other("EDC瀑布图"):
                record("waterfall_edc 页创建", "UNTESTED", "下拉项不存在")
                step_edc()
                return
            # 用可读步长（约 20 条曲线）；默认建议步长对该数据过密
            window.page_control_blank.set_waterfall_step(0.1, custom=True)
            try:
                window.on_apply_other_integral()
            except Exception as exc:  # noqa: BLE001
                record("waterfall_edc 页创建", "FAIL", str(exc))
                step_edc()
                return
            wait_for(
                "waterfall_edc 页渲染",
                lambda: window.current_render_context is not None
                and window.current_render_context.get("view") == "waterfall"
                and window.left_workspace.current_spec() is not None
                and window.left_workspace.current_spec().page_kind == "waterfall_edc",
                lambda: (do_export("waterfall_edc 页导出", "1d_open", "png",
                                   "page_waterfall.png", expect_family="1d"),
                         QTimer.singleShot(300, step_edc))[1],
                timeout_ms=30000,
            )

        wait_for(
            "回到 axis_integral 页",
            lambda: window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_id == axis_page
            and window.current_render_context is not None
            and window.current_render_context.get("view") == "2d",
            _apply, timeout_ms=30000,
        )

    def step_edc():
        # edc_curve 页按 UI 路径要求 axis_integral_crop 来源页（拖框交互），
        # 脚本直接按同一参数契约构造页（渲染管线与 UI 创建完全一致）。
        from result_workspace import AnalysisPageSpec

        spec = AnalysisPageSpec(
            page_id=window._make_page_id(),
            title="EDC曲线_验收",
            page_kind="edc_curve",
            source_module="data_process",
            source_page_id=pages.get("axis_integral", "home"),
            params={
                "axis_index": 2,
                "integral_low": 200, "integral_up": 300, "integral_mid": 250,
                "source_mode": "frame", "source_t_index": 0,
                "source_t_low": 0, "source_t_up": 0,
                "crop_k_low": 60, "crop_k_up": 140,
                "crop_e_low": 150, "crop_e_up": 350,
            },
        )
        try:
            window._seed_control_state_for_spec(spec)
            window.left_workspace.add_page(spec)
        except Exception as exc:  # noqa: BLE001
            record("edc_curve 页创建", "FAIL", str(exc))
            step_comparison()
            return
        wait_for(
            "edc_curve 页渲染",
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "1d"
            and window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_kind == "edc_curve",
            lambda: (do_export("edc_curve 页导出", "1d_boxed", "png",
                               "page_edc.png", expect_family="1d"),
                     QTimer.singleShot(300, step_comparison))[1],
            timeout_ms=30000,
        )

    def step_comparison():
        ok = False
        try:
            if window._copy_current_curve_to_clipboard():
                ok = window._paste_curve_to_comparison_page()
        except Exception as exc:  # noqa: BLE001
            record("比较页创建", "FAIL", str(exc))
            step_log_page()
            return
        if not ok:
            record("比较页创建", "UNTESTED", "剪贴板/粘贴条件不满足")
            step_log_page()
            return
        wait_for(
            "比较页渲染",
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "1d_comparison",
            lambda: (do_export("比较页导出", "1d_open", "png",
                               "page_comparison.png", expect_family="1d"),
                     QTimer.singleShot(300, step_log_page))[1],
            timeout_ms=30000,
        )

    def step_log_page():
        ok = False
        try:
            ok = window._apply_log_to_current_curves(show_message=False)
        except Exception as exc:  # noqa: BLE001
            record("log_curve 页创建", "FAIL", str(exc))
            step_dialog()
            return
        if not ok:
            record("log_curve 页创建", "UNTESTED", "当前页无可用 1D 曲线")
            step_dialog()
            return
        wait_for(
            "log_curve 页渲染",
            lambda: window.current_render_context is not None
            and window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_kind == "log_curve",
            lambda: (do_export("log_curve 页导出", "1d_open", "png",
                               "page_log.png", expect_family="1d"),
                     QTimer.singleShot(300, step_dialog))[1],
            timeout_ms=30000,
        )

    # ------------------------------------------------------- 对话框交互验证

    def step_dialog():
        log("[accept] step_dialog: activate home page")
        home = window.left_workspace.home_spec()
        if home is not None:
            window.left_workspace.activate_page(home.page_id)
        log("[accept] step_dialog: waiting for 3d view")
        wait_for(
            "回到 3D 主页",
            lambda: window.current_render_context is not None
            and window.current_render_context.get("view") == "3d"
            and window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_kind == "home",
            step_dialog_open, timeout_ms=30000,
        )

    def step_dialog_open():
        log("[accept] step_dialog_open: enter")
        committed_before = load_style_id(window.settings, "3d")
        try:
            window.open_publication_dialog()
        except Exception as exc:  # noqa: BLE001
            record("样式面板打开", "FAIL", f"{type(exc).__name__}: {exc}")
            step_finalize()
            return
        dialog = window.__dict__.get("_publication_dialog")
        log(f"[accept] step_dialog_open: visible={dialog is not None and dialog.isVisible()}")
        if dialog is None or not dialog.isVisible():
            record("样式面板打开", "FAIL", "面板未显示")
            step_finalize()
            return
        record("样式面板打开", "PASS", f"快照族={dialog.snapshot.view_family}")

        # 卡片与大图预览（3D 预览为 GUI 线程同步渲染，打开时已就绪）
        n_cards = len(dialog.cards)
        record("样式卡片生成", "PASS" if n_cards == 3 and dialog._big_ready else "FAIL",
               f"卡片数={n_cards} 大图就绪={dialog._big_ready}")
        # 大图重排由 singleShot(0) 触发，截图推迟一拍等事件循环跑完
        QTimer.singleShot(400, lambda: step_dialog_grab(dialog, committed_before))

    def step_dialog_grab(dialog, committed_before):
        shot = os.path.join(OUT_DIR, "dialog_cards.png")
        dialog.grab().save(shot)

        # 草稿语义：切换卡片只改草稿，不写设置
        other = next(sid for sid in dialog.cards if sid != dialog._draft_style_id)
        dialog._on_card_clicked(other)
        committed_now = load_style_id(window.settings, "3d")
        record("草稿语义 · 切换不提交",
               "PASS" if (dialog._draft_style_id == other and committed_now == committed_before)
               else "FAIL",
               f"draft={dialog._draft_style_id} committed={committed_now}")

        # 取消不提交
        dialog.reject()
        record("取消不提交",
               "PASS" if load_style_id(window.settings, "3d") == committed_before else "FAIL",
               "")
        QTimer.singleShot(400, lambda: step_dialog_commit(committed_before, other))

    def step_dialog_commit(committed_before, other):
        dialog = window.__dict__.get("_publication_dialog")
        try:
            ok = dialog.open_for_current_view()
            if not ok:
                record("面板重开", "FAIL", "open_for_current_view=False")
                step_finalize()
                return
            dialog._on_card_clicked(other)
            committed_mid = load_style_id(window.settings, "3d")
            record("切换样式不提前提交",
                   "PASS" if committed_mid == committed_before else "FAIL",
                   f"committed={committed_mid}")
            # “使用此样式”已并入“导出此预览”（写入成功才提交）；
            # 此处直接调用同一 commit_style 路径验证提交与提示刷新
            pe.commit_style(window.settings, "3d", other, {})
            window._update_screenshot_tooltip()
            committed_after = load_style_id(window.settings, "3d")
            record("提交样式偏好",
                   "PASS" if committed_after == other else "FAIL",
                   f"committed={committed_after}")
            # 还原偏好，避免污染用户环境
            if committed_before:
                pe.commit_style(window.settings, "3d", committed_before, {})
            tooltip = window.btn_tb_shot.toolTip()
            record("截图样式按钮 tooltip 刷新",
                   "PASS" if "截图样式" in tooltip else "FAIL", tooltip)
            dialog.close()
        except Exception as exc:  # noqa: BLE001
            record("面板重开/提交", "FAIL", f"{type(exc).__name__}: {exc}")
        step_finalize()

    # ------------------------------------------------------- 校验与报告

    def step_finalize():
        check_png_metadata()
        check_pdf_metadata()
        build_mosaics()
        write_report()
        log("[accept] DONE")
        window.close()
        app.quit()

    def check_png_metadata():
        from PIL import Image

        expected = {
            "3d_3d_minimal.png": (89.0, 85.0), "3d_3d_boxed.png": (89.0, 85.0),
            "3d_3d_horizontal.png": (89.0, 85.0),
            "2d_2d_boxed.png": (89.0, 75.0), "2d_2d_topbar.png": (89.0, 75.0),
            "2d_2d_open.png": (89.0, 75.0),
            "1d_1d_open.png": (89.0, 65.0), "1d_1d_boxed.png": (89.0, 65.0),
            "1d_1d_compact.png": (89.0, 65.0),
        }
        for name, (w_mm, h_mm) in expected.items():
            path = os.path.join(OUT_DIR, name)
            if not os.path.exists(path):
                record(f"PNG 元数据 {name}", "FAIL", "文件缺失")
                continue
            try:
                with Image.open(path) as im:
                    exp_w = round(w_mm / 25.4 * 600)
                    exp_h = round(h_mm / 25.4 * 600)
                    dpi_meta = im.info.get("dpi", (None,))[0]
                    ok = (abs(im.width - exp_w) <= 2 and abs(im.height - exp_h) <= 2
                          and dpi_meta is not None and abs(dpi_meta - 600) <= 1.0)
                    record(f"PNG 元数据 {name}", "PASS" if ok else "FAIL",
                           f"{im.width}x{im.height}px dpi={dpi_meta:.0f} 期望 {exp_w}x{exp_h}")
            except Exception as exc:  # noqa: BLE001
                record(f"PNG 元数据 {name}", "FAIL", str(exc))

    def check_pdf_metadata():
        for name, w_mm, h_mm in (
            ("2d_2d_boxed.pdf", 183.0 / 2, 75.0), ("1d_1d_open.pdf", 89.0, 65.0),
        ):
            path = os.path.join(OUT_DIR, name)
            if not os.path.exists(path):
                record(f"PDF 校验 {name}", "FAIL", "文件缺失")
                continue
            with open(path, "rb") as fh:
                content = fh.read()
            w_pt = 89.0 / 25.4 * 72.0
            token = f"0 0 {w_pt:.3f}".encode()
            record(f"PDF 校验 {name}",
                   "PASS" if token[:9] in content else "FAIL",
                   f"MediaBox 前缀 {token[:9].decode()}")

    def build_mosaics():
        from PIL import Image

        for family, names in (
            ("3d", ["3d_3d_minimal.png", "3d_3d_boxed.png", "3d_3d_horizontal.png"]),
            ("2d", ["2d_2d_boxed.png", "2d_2d_topbar.png", "2d_2d_open.png"]),
            ("1d", ["1d_1d_open.png", "1d_1d_boxed.png", "1d_1d_compact.png"]),
        ):
            images = []
            for name in names:
                path = os.path.join(OUT_DIR, name)
                if os.path.exists(path):
                    im = Image.open(path).convert("RGB")
                    im.thumbnail((640, 640))
                    images.append(im)
            if len(images) < 2:
                record(f"同数据对照拼图 {family}", "UNTESTED", "样例不足")
                continue
            gap = 24
            w = sum(im.width for im in images) + gap * (len(images) + 1)
            h = max(im.height for im in images) + gap * 2
            mosaic = Image.new("RGB", (w, h), (245, 245, 245))
            x = gap
            for im in images:
                mosaic.paste(im, (x, gap))
                x += im.width + gap
            out = os.path.join(OUT_DIR, f"mosaic_{family}.png")
            mosaic.save(out)
            record(f"同数据对照拼图 {family}", "PASS", out)

    def write_report():
        import platform

        import matplotlib
        import pyvista
        import vtk
        from PyQt5.QtCore import QT_VERSION_STR

        lines = [
            "# 图片导出 · 验收报告",
            "",
            f"- 日期：{time.strftime('%Y-%m-%d %H:%M')}",
            f"- 数据：`{NPZ}`（shape={tuple(window.core.raw_data.shape)}）",
            f"- 环境：Python {platform.python_version()} · matplotlib {matplotlib.__version__}"
            f" · pyvista {pyvista.__version__} · vtk {vtk.VTK_VERSION} · Qt {QT_VERSION_STR}",
            "",
            "## 检查项",
            "",
            "| 检查项 | 结果 | 说明 |",
            "| --- | --- | --- |",
        ]
        for item, status, detail in RESULTS:
            lines.append(f"| {item} | {status} | {detail} |")
        lines += [
            "",
            "## 导出耗时",
            "",
            "| 产物 | 秒 |",
            "| --- | --- |",
        ]
        for name, dt in TIMINGS:
            lines.append(f"| {name} | {dt:.2f} |")
        n_pass = sum(1 for _, s, _ in RESULTS if s == "PASS")
        n_fail = sum(1 for _, s, _ in RESULTS if s == "FAIL")
        n_skip = sum(1 for _, s, _ in RESULTS if s == "UNTESTED")
        lines += [
            "",
            f"**汇总：{n_pass} 通过 / {n_fail} 失败 / {n_skip} 未测**",
            "",
            "## 已知限制",
            "",
            "- 非均匀坐标网格：2D/3D 明确报不支持（1D 曲线按真实值绘制不受影响）。",
            "- 2D 导出继承当前正式显示的 spline16 插值。",
            "- 3D 首版仅 PNG；PDF 对 3D 禁用（TIFF/混合 PDF 属后续增强）。",
            "- 轴标签单位仅在数据文件显式提供时标注，否则只写物理量名（不编造单位）。",
            "- 瀑布图动量标签带逐曲线标注（与主视图一致）：k 步长过密时标签会互相压盖，"
            "请调大步长（本验收样例用 0.1）。",
        ]
        report = os.path.join(OUT_DIR, "validation_report.md")
        with open(report, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        log(f"[accept] report -> {report}")

    QTimer.singleShot(2000, step_load)
    app.exec_()


if __name__ == "__main__":
    main()
