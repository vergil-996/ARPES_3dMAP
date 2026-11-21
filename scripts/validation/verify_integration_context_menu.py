# -*- coding: utf-8 -*-
"""真实窗口验收“视图右键 → 处理分析”的完整入口。

默认使用 WSe₂ 时间分辨数据，在隔离设置和扩展目录中构建并安装 EDC 瀑布图插件。
所有创建都通过 2D／3D 视图区域的真实右键 QMenu 完成；QTimer 操作菜单，避免
exec_() 阻塞。报告、菜单截图和结果页截图仅写入 .local/outputs/integration-context-menu/。

用法: .venv/Scripts/python.exe scripts/validation/verify_integration_context_menu.py [npz路径] [--output-dir DIR]
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _drive_view_create_menu(
    window,
    parent_page_id,
    action_key,
    callback,
    *,
    output_dir=None,
    shot_prefix=None,
):
    """激活父页后右键视图，读取「处理分析」子菜单，并按需点击稳定键对应的 QAction。"""
    from PyQt5.QtCore import QPoint, Qt, QTimer
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication, QMenu

    workspace = window.left_workspace
    parent_page_id = str(parent_page_id)
    current = workspace.current_spec()
    if current is None or current.page_id != parent_page_id:
        workspace.activate_page(parent_page_id)
    current = workspace.current_spec()
    stack = getattr(window, "left_display_stack", None)
    widget = stack.currentWidget() if stack is not None else None
    if widget not in (window.plotter, window.canvas_2d):
        widget = window.canvas_2d
    position = QPoint(max(1, widget.width() // 2), max(1, widget.height() // 2))
    report = {
        "parent_page_id": parent_page_id,
        "action_key": action_key,
        "active_before": current.page_id if current is not None else None,
        "opened": False,
        "right_click_preserved_active_page": False,
        "create_present": False,
        "analysis_present": False,
        "keys": [],
        "labels": [],
        "selected_page_ids": [],
        "triggered": False,
        "screenshots": [],
        "error": "",
    }
    output = Path(output_dir) if output_dir is not None else REPO_ROOT / ".local" / "outputs" / "integration-context-menu"
    if shot_prefix:
        output.mkdir(parents=True, exist_ok=True)
    state = {"done": False}

    def finish(*widgets):
        if state["done"]:
            return
        state["done"] = True
        for item in widgets:
            if item is None:
                continue
            try:
                item.close()
            except RuntimeError:
                pass
        QTimer.singleShot(0, lambda: callback(report))

    def fail(message, *widgets):
        report["error"] = str(message)
        finish(*widgets)

    def save_shot(target, suffix):
        if not shot_prefix:
            return
        path = output / f"{shot_prefix}-{suffix}.png"
        if target.grab().save(str(path)):
            report["screenshots"].append(str(path))
        else:
            report["error"] = f"截图失败：{path}"

    def analysis_action(menu):
        for action in menu.actions():
            if action.menu() is not None and action.text().replace("&", "") == "处理分析":
                return action
        return None

    def poll_submenu(root, entry, remaining=60):
        if state["done"]:
            return
        popup = QApplication.activePopupWidget()
        submenu = entry.menu()
        if popup is submenu and isinstance(popup, QMenu):
            actions = [action for action in submenu.actions() if not action.isSeparator()]
            report["keys"] = [str(action.data()) for action in actions if action.data() is not None]
            report["labels"] = [action.text() for action in actions]
            save_shot(root, "view")
            save_shot(submenu, "analysis")
            if action_key is None:
                finish(submenu, root)
                return
            target = next((action for action in actions if str(action.data()) == str(action_key)), None)
            if target is None:
                fail(f"处理分析子菜单没有稳定键 {action_key}", submenu, root)
                return
            if not target.isEnabled():
                fail(f"处理分析动作被禁用：{action_key}", submenu, root)
                return
            target.triggered.connect(lambda *_args: report.update(triggered=True))
            rect = submenu.actionGeometry(target)
            QTest.mouseClick(submenu, Qt.LeftButton, Qt.NoModifier, rect.center())
            QTimer.singleShot(0, lambda: finish(submenu, root))
            return
        if remaining <= 0:
            fail("等待「处理分析」子菜单超时", root)
            return
        QTimer.singleShot(50, lambda: poll_submenu(root, entry, remaining - 1))

    def poll_root(remaining=60):
        if state["done"]:
            return
        popup = QApplication.activePopupWidget()
        if not isinstance(popup, QMenu):
            if remaining <= 0:
                fail("视图右键后没有出现 QMenu")
                return
            QTimer.singleShot(50, lambda: poll_root(remaining - 1))
            return
        report["opened"] = True
        report["selected_page_ids"] = list(workspace.page_tree.tree.selected_page_ids())
        active = workspace.current_spec()
        active_id = active.page_id if active is not None else None
        report["right_click_preserved_active_page"] = active_id == report["active_before"]
        entry = analysis_action(popup)
        report["create_present"] = entry is not None
        report["analysis_present"] = entry is not None
        if entry is None:
            if action_key is not None:
                report["error"] = "视图右键菜单没有「处理分析」子菜单"
            save_shot(popup, "view")
            finish(popup)
            return
        rect = popup.actionGeometry(entry)
        popup.setActiveAction(entry)
        QTimer.singleShot(25, lambda: poll_submenu(popup, entry))
        QTest.mouseClick(popup, Qt.LeftButton, Qt.NoModifier, rect.center())

    QTimer.singleShot(50, poll_root)
    widget.customContextMenuRequested.emit(position)


def inspect_analysis_menu(window, parent_page_id, callback, **kwargs):
    _drive_view_create_menu(window, parent_page_id, None, callback, **kwargs)


def create_from_page_menu(window, parent_page_id, action_key, callback, **kwargs):
    _drive_view_create_menu(window, parent_page_id, action_key, callback, **kwargs)


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def install_edc_waterfall_plugin():
    from bandscope.app_metadata import APP_VERSION
    from bandscope.extensions.plugin_manager import (
        InstallSource,
        PluginManager,
        install_package,
    )
    from bandscope.extensions.trust import SOURCE_LOCAL
    from scripts.release.build_plugin import build as build_package

    manager = PluginManager()
    with tempfile.TemporaryDirectory(prefix="bandscope-context-menu-plugin-") as staging:
        archive = build_package("edc_waterfall", Path(staging), APP_VERSION)
        install_package(
            archive,
            root=Path(manager.root),
            source=InstallSource(kind=SOURCE_LOCAL, accepted_unverified=True),
        )


def main() -> int:
    from scripts.validation._common import arguments

    args = arguments(
        "integration-context-menu",
        default_data=REPO_ROOT / ".local" / "data" / "WSe2_step.npz",
    )
    install_edc_waterfall_plugin()

    from PyQt5.QtCore import Qt, QTimer
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    import bandscope.app.refactored_app as refactored_app

    refactored_app.UpdateController = _NullUpdateController
    app = QApplication(sys.argv)
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.resize(1600, 950)
    window.show()
    messages = []
    window._show_message = lambda title, text, *a, **k: messages.append((title, text))

    from bandscope.core.crop_model import selection_for_context
    from bandscope.ui import theme

    results = []
    pages = {}
    state = {"finished": False}

    def record(item, ok, detail=""):
        entry = {"item": item, "ok": bool(ok), "detail": str(detail)}
        results.append(entry)
        print(f"[{'PASS' if ok else 'FAIL'}] {item}  {detail}", flush=True)

    def save_window(name):
        path = args.output_dir / name
        return window.grab().save(str(path))

    def current_page_id():
        spec = window.left_workspace.current_spec()
        return spec.page_id if spec is not None else None

    def page_ready(page_id, view=None):
        if current_page_id() != page_id or window.current_render_context is None:
            return False
        if view is not None and window.current_render_context.get("view") != view:
            return False
        if getattr(window, "_render_context_page_id", page_id) != page_id:
            return False
        return bool(getattr(window, "_render_exact_ready", True))

    def render_diagnostics():
        spec = window.left_workspace.current_spec()
        context = window.current_render_context or {}
        return json.dumps(
            {
                "current_page_id": current_page_id(),
                "context_page_id": getattr(window, "_render_context_page_id", None),
                "view": context.get("view"),
                "slice_info": context.get("slice_info"),
                "exact_ready": getattr(window, "_render_exact_ready", None),
                "live_home_slice_info": window.home_slice_info,
                "spec_home_slice_info": (spec.params.get("home_slice_info") if spec else None),
                "syncing_controls": getattr(window, "_syncing_controls", None),
                "source_pending_id": getattr(window, "_source_pending_id", None),
            },
            ensure_ascii=False,
            default=str,
        )

    def wait_for(desc, predicate, next_step, timeout_ms=60000):
        deadline = time.perf_counter() + timeout_ms / 1000.0

        def poll():
            if state["finished"]:
                return
            try:
                ok = bool(predicate())
            except Exception:
                ok = False
            if ok:
                next_step()
            elif time.perf_counter() < deadline:
                QTimer.singleShot(250, poll)
            else:
                record(desc, False, "等待超时；" + render_diagnostics())
                next_step()

        poll()

    def activate(page_id, next_step, view=None):
        window.left_workspace.activate_page(page_id)
        wait_for(f"激活 {page_id}", lambda: page_ready(page_id, view), next_step)

    def guard(step):
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - 验收记录实际异常并退出
            record("验收步骤异常", False, f"{type(exc).__name__}: {exc}")
            messages.append(("traceback", traceback.format_exc()))
            finish()

    def guarded(step):
        return lambda *a, **k: guard(lambda: step(*a, **k))

    def create_result(
        parent_id,
        action_key,
        expected_kind,
        expected_view,
        next_step,
        *,
        parent_view="2d",
        shot_prefix=None,
        validate=None,
    ):
        before_ids = set(window.left_workspace.page_specs)

        def open_menu():
            def after_menu(report):
                record(
                    f"视图右键提供并触发 {action_key}",
                    report["opened"]
                    and report["create_present"]
                    and action_key in report["keys"]
                    and report["triggered"],
                    report["error"] or "、".join(report["keys"]),
                )
                record(
                    "右键打开菜单不切换页面",
                    report["right_click_preserved_active_page"],
                    f"活动页={report['active_before']}，父页={parent_id}",
                )
                if not report["triggered"]:
                    record(f"创建 {expected_kind}", False, report["error"] or "菜单动作未触发")
                    QTimer.singleShot(0, next_step)
                    return

                def created_id():
                    new_ids = set(window.left_workspace.page_specs) - before_ids
                    if len(new_ids) != 1:
                        return None
                    page_id = next(iter(new_ids))
                    return page_id if page_ready(page_id, expected_view) else None

                def check_created():
                    new_ids = set(window.left_workspace.page_specs) - before_ids
                    spec = window.left_workspace.current_spec()
                    ok = (
                        len(new_ids) == 1
                        and spec is not None
                        and spec.page_id in new_ids
                        and spec.page_kind == expected_kind
                        and spec.source_page_id == parent_id
                    )
                    record(
                        f"右键创建 {expected_kind} 并选中新页",
                        ok and window.left_workspace.page_tree._current_page_id == (spec.page_id if spec else None),
                        f"新页={sorted(new_ids)}，当前={current_page_id()}，父页={parent_id}",
                    )
                    if ok:
                        pages[expected_kind] = spec.page_id
                        save_window(f"result-{expected_kind}-{theme.current_theme()}.png")
                        if validate is not None:
                            validate(spec)
                    next_step()

                wait_for(f"{expected_kind} 结果页渲染", lambda: created_id() is not None, check_created, 90000)

            create_from_page_menu(
                window,
                parent_id,
                action_key,
                guarded(after_menu),
                output_dir=args.output_dir,
                shot_prefix=shot_prefix,
            )

        activate(parent_id, open_menu, parent_view)

    def step_load():
        window._set_theme("light")
        window.load_data(str(args.data))
        wait_for("WSe₂ 主页 3D 渲染", lambda: page_ready(window.left_workspace.home_page_id, "3d"), step_home, 90000)

    def step_home():
        record(
            "旧「处理分析」控制页、外观导航按钮和旧下拉入口已移除",
            not hasattr(window, "page_data")
            and not hasattr(window, "btn_page2")
            and not hasattr(window, "btn_page3")
            and not hasattr(window, "button_group")
            and not hasattr(window, "_control_page_buttons")
            and window.page_container.count() == 1
            and window.page_container.widget(0) is window.page_render,
            f"控制页数={window.page_container.count()}",
        )
        session = window.plugin_session
        actions = session.integration_actions() if session is not None else []
        record(
            "隔离扩展根中的 EDC 插件已就绪",
            any(str(key) == "edc_waterfall:create" for key, _label, _order in actions),
            repr(actions),
        )
        window.canvas_tool_overlay.preset_buttons["yz"].click()
        wait_for(
            "X 轴积分来源页",
            lambda: window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_kind == "axis_integral"
            and int(window.left_workspace.current_spec().params.get("axis_index", -1)) == 0
            and page_ready(current_page_id(), "2d"),
            step_x_integral,
            90000,
        )

    def step_x_integral():
        spec = window.left_workspace.current_spec()
        pages["axis_x"] = spec.page_id
        activate(window.left_workspace.home_page_id, step_home_for_z, "3d")

    def step_home_for_z():
        window.canvas_tool_overlay.preset_buttons["xy"].click()
        wait_for(
            "E/Z 轴积分来源页",
            lambda: window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_kind == "axis_integral"
            and int(window.left_workspace.current_spec().params.get("axis_index", -1)) == 2
            and page_ready(current_page_id(), "2d"),
            step_z_integral,
            90000,
        )

    def step_z_integral():
        pages["axis_z"] = current_page_id()
        window.btn_tb_crop.setChecked(True)
        QTimer.singleShot(400, guarded(step_crop_z))

    def step_crop_z():
        source_id = pages["axis_z"]
        before_ids = set(window.left_workspace.page_specs)
        selection = window.crop_controller.selection or selection_for_context(window.current_render_context)
        if selection is None:
            record("准备 EDC 矩形裁剪父页", False, "无法建立裁剪选区")
            step_inspect_home()
            return
        x0, x1, y0, y1 = selection.bounds
        bounds = (
            x0 + 0.20 * (x1 - x0),
            x0 + 0.75 * (x1 - x0),
            y0 + 0.20 * (y1 - y0),
            y0 + 0.75 * (y1 - y0),
        )
        window.crop_controller.set_selection(replace(selection, bounds=bounds, operation="crop"))
        window.on_cut()
        window.btn_tb_crop.setChecked(False)
        wait_for(
            "EDC 矩形裁剪父页",
            lambda: len(set(window.left_workspace.page_specs) - before_ids) == 1
            and window.left_workspace.current_spec() is not None
            and window.left_workspace.current_spec().page_kind == "axis_integral_crop"
            and window.left_workspace.current_spec().source_page_id == source_id
            and page_ready(current_page_id(), "2d"),
            step_crop_ready,
            90000,
        )

    def step_crop_ready():
        spec = window.left_workspace.current_spec()
        pages["axis_z_crop"] = spec.page_id
        record(
            "准备 EDC 矩形裁剪父页",
            spec.page_kind == "axis_integral_crop" and spec.source_page_id == pages["axis_z"],
            spec.page_id,
        )
        activate(window.left_workspace.home_page_id, step_home_slice, "3d")

    def step_home_slice():
        home = window.left_workspace.current_spec()
        window.home_slice_info = {
            "axis": 0,
            "index": int(window.core.raw_data.shape[0] // 2),
        }
        window._persist_home_page_state(home)
        window.global_refresh()
        wait_for(
            "含能量轴的 2D 主页",
            lambda: page_ready(window.left_workspace.home_page_id, "2d"),
            step_home_slice_ready,
            90000,
        )

    def step_home_slice_ready():
        window._persist_active_page_state()
        step_inspect_home()

    def step_inspect_home():
        expected = ["builtin:slice_dos", "builtin:energy_dos", "builtin:second_derivative"]

        def checked(report):
            record(
                "含能量轴的 2D 主页只显示合法内置动作（浅色菜单）",
                report["opened"] and report["keys"] == expected,
                report["error"] or "、".join(report["keys"]),
            )
            create_result(
                window.left_workspace.home_page_id,
                "builtin:slice_dos",
                "slice_dos",
                "1d",
                step_energy_dos,
                shot_prefix="menu-light-slice",
            )

        inspect_analysis_menu(
            window,
            window.left_workspace.home_page_id,
            guarded(checked),
            output_dir=args.output_dir,
            shot_prefix="menu-light-home",
        )

    def step_energy_dos():
        create_result(
            window.left_workspace.home_page_id,
            "builtin:energy_dos",
            "energy_dos",
            "1d",
            step_illegal_parent,
        )

    def step_illegal_parent():
        parent_id = pages.get("energy_dos")
        before_ids = set(window.left_workspace.page_specs)

        def checked(report):
            record(
                "非法结果父页没有处理分析子菜单且不建页",
                report["opened"]
                and not report["create_present"]
                and set(window.left_workspace.page_specs) == before_ids,
                report["error"] or f"父页={parent_id}",
            )
            step_page_tree_entry_removed()

        if parent_id is None:
            record("非法结果父页没有处理分析子菜单且不建页", False, "energy_dos 父页不可用")
            step_page_tree_entry_removed()
            return
        inspect_analysis_menu(window, parent_id, guarded(checked), output_dir=args.output_dir)

    def step_page_tree_entry_removed():
        panel = window.left_workspace.page_tree
        workspace = window.left_workspace
        record(
            "页面树不再提供结果创建入口",
            not hasattr(panel, "_page_create_action_provider")
            and not hasattr(panel, "set_page_create_action_provider")
            and not hasattr(workspace, "page_create_requested")
            and not hasattr(workspace, "set_page_create_action_provider"),
            "页树 provider 与 page_create_requested 已移除",
        )
        step_dark_second_derivative()

    def step_dark_second_derivative():
        window._set_theme("dark")

        def create():
            parent_id = pages.get("axis_x")
            if parent_id is None:
                record("视图右键创建二阶导", False, "X 积分父页不可用")
                step_edc()
                return

            def validate(spec):
                source = window.left_workspace.page_specs[parent_id]
                record(
                    "二阶导数值参数来自视图右键的父页",
                    spec.params.get("source_page_id") == parent_id
                    and spec.params.get("axis_index") == source.params.get("axis_index")
                    and spec.params.get("integral_low") == source.params.get("low")
                    and spec.params.get("integral_up") == source.params.get("up"),
                    json.dumps(spec.params, ensure_ascii=False, default=str),
                )

            create_result(
                parent_id,
                "builtin:second_derivative",
                "second_derivative",
                "2d",
                step_edc,
                shot_prefix="menu-dark-second",
                validate=validate,
            )

        QTimer.singleShot(600, guarded(create))

    def step_edc():
        parent_id = pages.get("axis_z_crop")
        if parent_id is None:
            record("右键创建 edc_curve", False, "Z 轴裁剪父页不可用")
            step_waterfall()
            return

        def validate(spec):
            source = window.left_workspace.page_specs[parent_id]
            keys = ("crop_k_low", "crop_k_up", "crop_e_low", "crop_e_up")
            record(
                "单条 EDC 冻结矩形裁剪父页",
                spec.params.get("source_page_id") == parent_id
                and all(spec.params.get(key) == source.params.get(key) for key in keys),
                json.dumps({key: spec.params.get(key) for key in keys}, ensure_ascii=False),
            )

        create_result(
            parent_id,
            "builtin:edc_curve",
            "edc_curve",
            "1d",
            step_waterfall,
            validate=validate,
        )

    def step_waterfall():
        parent_id = pages.get("axis_x")
        if parent_id is None or window.plugin_session is None:
            record("右键创建 plugin_derived", False, "X 积分父页或插件会话不可用")
            step_final()
            return
        window.plugin_session.save_derived_defaults(
            "edc_waterfall", {"k_step_mode": "manual", "k_step": 10.0}
        )

        def validate(spec):
            source = (spec.params.get("derived_source") or {})
            params = spec.params.get("derived_params") or {}
            record(
                "插件瀑布图冻结来源、时间区间和新建默认值",
                source.get("source_page_id") == parent_id
                and source.get("scope_id") == "full"
                and (source.get("time_interval") or {}).get("axis_key") == "delay"
                and params.get("k_step_mode") == "manual"
                and float(params.get("k_step", 0.0)) == 10.0,
                json.dumps(spec.params, ensure_ascii=False, default=str),
            )

        create_result(
            parent_id,
            "edc_waterfall:create",
            "plugin_derived",
            "waterfall",
            step_final,
            shot_prefix="menu-dark-waterfall",
            validate=validate,
        )

    def step_final():
        window._set_theme("light")

        def checks():
            before = window.page_container.currentIndex()
            QTest.keyClick(window, Qt.Key_2, Qt.ControlModifier)
            record(
                "Ctrl+2 不再进入已删除的处理分析页",
                window.page_container.count() == 1 and window.page_container.currentIndex() == before,
                f"索引 {before} → {window.page_container.currentIndex()}",
            )
            save_window("workspace-final-light.png")
            finish()

        QTimer.singleShot(500, guarded(checks))

    def finish():
        if state["finished"]:
            return
        state["finished"] = True
        payload = {
            "data": str(args.data),
            "data_shape": list(window.core.raw_data.shape) if window.core.raw_data is not None else None,
            "pages": pages,
            "results": results,
            "messages": messages,
            "success": bool(results) and all(item["ok"] for item in results),
        }
        (args.output_dir / "report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        passed = sum(1 for item in results if item["ok"])
        print(f"[accept] {passed}/{len(results)} 项通过；输出 {args.output_dir}", flush=True)
        for widget in QApplication.topLevelWidgets():
            if widget.isVisible():
                widget.close()
        app.quit()

    QTimer.singleShot(0, guarded(step_load))
    QTimer.singleShot(600000, lambda: (record("真实窗口验收总超时", False), finish()))
    app.exec_()
    return 0 if results and all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
