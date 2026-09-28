# -*- coding: utf-8 -*-
"""页面树的真实窗口验收：层级、长中文名、搜索、栏宽、删除确认与快捷键隔离。

验证点：
1. 页面树常驻主页左侧，派生页按来源关系逐级挂靠；展开箭头只收展、点名称才切页；
2. 长中文名称完整显示（不省略 + 完整提示），页眉与关闭按钮不被挤坏；
3. 搜索显示命中页面及其祖先，清空后还原展开状态，无匹配时空结果提示且画布不动；
4. 拖动改栏宽、低于最小值被夹住、画布至少 320 像素，栏宽跨会话保留；
5. 3D 主页 → 裁剪页 → 积分页逐级切换，页眉、画布与树选中行保持同步；
6. 删除先弹确认（含目标名称、总数、默认取消）；取消后页面仍在，确认后整支删除；
7. 主页受保护不可删除；数字切页按树中可见行顺序；页面树持焦时方向键不逐帧。

未覆盖：1D 曲线页（EDC / 瀑布图 / 曲线比较）的树内切换——这些页面的生成依赖
特定数据与控件状态，已有 ``verify_axis_titles.py`` / ``verify_page_shortcuts.py``
负责其真实窗口验收；本脚本只覆盖 2D / 3D 结果页。

用法: .venv/Scripts/python.exe scripts/validation/verify_page_tree.py <npz路径> [--dynamic]
"""
import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QEvent, QPoint, Qt, QTimer
from PyQt5.QtGui import QKeyEvent, QMouseEvent
from PyQt5.QtWidgets import QApplication, QMessageBox

NPZ = None
OUT_DIR = None

failures = []
state = {}


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}", flush=True)
    if not condition:
        failures.append(label)


def log(message):
    print(message, flush=True)


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


class _NullToast:
    """提示层不参与验收，免得在截图上盖住页面树。"""

    def __init__(self, *args, **kwargs):
        pass

    def show(self, *args, **kwargs):
        pass

    def clear(self):
        pass


def answer_message_box(button):
    """删除确认是模态框：答案要在 exec_() 之前排好，并记下按钮文字。"""

    def act():
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            failures.append("没有找到删除确认弹窗")
            return
        state.setdefault("delete_boxes", []).append(
            {
                "text": box.text(),
                "default": box.defaultButton().text() if box.defaultButton() else "",
                "yes": box.button(QMessageBox.Yes).text(),
                "no": box.button(QMessageBox.No).text(),
            }
        )
        box.button(button).click()

    QTimer.singleShot(0, act)


def answer_dialog(text):
    def act():
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            failures.append("没有找到改名弹窗")
            return
        dialog.edit.setText(text)
        dialog.accept()

    QTimer.singleShot(0, act)


def send_mouse(widget, kind, pos, *, button=Qt.LeftButton, buttons=Qt.NoButton):
    event = QMouseEvent(kind, pos, widget.mapToGlobal(pos), button, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, event)


def click_row(tree, item, *, arrow=False):
    rect = tree.visualItemRect(item)
    x = rect.left() - 1 if arrow else rect.left() + 20
    pos = QPoint(x, rect.center().y())
    send_mouse(tree.viewport(), QEvent.MouseButtonPress, pos, buttons=Qt.LeftButton)
    send_mouse(tree.viewport(), QEvent.MouseButtonRelease, pos, buttons=Qt.NoButton)


def save_shot(window, name):
    path = os.path.join(OUT_DIR, name)
    window.grab().save(path)
    log(f"[SHOT] {path}")


def workspace(window):
    return window.left_workspace


def panel(window):
    return window.left_workspace.page_tree


def tree(window):
    return window.left_workspace.page_tree.tree


def item(window, page_id):
    return panel(window)._items.get(page_id)


def child_page_ids(window, page_id):
    row = item(window, page_id)
    if row is None:
        return []
    return [
        tree(window).item_page_id(row.child(index)) for index in range(row.childCount())
    ]


def main():
    global NPZ, OUT_DIR
    from scripts.validation._common import arguments

    args = arguments("verify_page_tree", dynamic=True)
    NPZ, OUT_DIR = str(args.data), str(args.output_dir)
    if args.size:
        width, height = (int(value) for value in args.size.lower().split("x"))
    else:
        width, height = 1500, 950

    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app
    from bandscope.ui.result_workspace import (
        CANVAS_MIN_WIDTH,
        NAV_INITIAL_WIDTH,
        NAV_MIN_WIDTH,
    )

    refactored_app.UpdateController = _NullUpdateController
    refactored_app.ToastManager = _NullToast

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    window.resize(width, height)

    def finish():
        save_shot(window, "page_tree_final.png")
        if failures:
            print(f"[RESULT] {len(failures)} 项失败", flush=True)
            for label in failures:
                print(f"  - {label}", flush=True)
            app.exit(1)
        else:
            print("[RESULT] 全部通过", flush=True)
            app.exit(0)

    def guarded(step):
        def run():
            try:
                step()
            except Exception as exc:  # 验收脚本把异常算作失败，不挂死
                failures.append(f"{step.__name__}: {exc!r}")
                print(f"[FAIL] {step.__name__} 抛出异常 {exc!r}", flush=True)
                finish()

        return run

    # ------------------------------------------------------------ 加载与建树
    def step_loaded():
        check("数据已加载", window.core.raw_data is not None)
        check("页面树随主页出现", item(window, "home") is not None)
        check("主页是树里的第一行", tree(window).topLevelItem(0) is item(window, "home"))
        check(
            f"初始栏宽 {NAV_INITIAL_WIDTH} 像素",
            workspace(window).nav_width() == NAV_INITIAL_WIDTH,
        )
        check(
            f"画布最小宽度 {CANVAS_MIN_WIDTH} 像素",
            workspace(window).splitter.widget(1).minimumWidth() == CANVAS_MIN_WIDTH,
        )
        QTimer.singleShot(200, guarded(step_crop))

    def step_crop():
        """用真实裁剪路径造出「主页 → 裁剪页」这一级。"""
        selection = window.crop_controller.selection
        bounds = list(selection.bounds)
        bounds[4] = bounds[5] = (bounds[4] + bounds[5]) / 2.0
        window.crop_controller.set_selection(replace(selection, bounds=tuple(bounds)))
        window.on_cut()
        QTimer.singleShot(6000, guarded(step_crop_check))

    def step_crop_check():
        spec = workspace(window).current_spec()
        check("裁剪生成了派生页", spec is not None and spec.page_id != "home")
        if spec is None or spec.page_id == "home":
            finish()
            return
        state["crop"] = spec.page_id
        check("裁剪页挂在主页下", state["crop"] in child_page_ids(window, "home"))
        check("主页显示展开箭头", item(window, "home").childCount() > 0)
        QTimer.singleShot(200, guarded(step_integral))

    def step_integral():
        """在裁剪页上再做一次轴积分，得到第三级。"""
        window.on_apply_axis_integral()
        QTimer.singleShot(4000, guarded(step_tree_shape))

    def step_tree_shape():
        state["integral"] = workspace(window).current_page_id
        check("积分页是新页面", state["integral"] not in {"home", state["crop"]})
        check(
            "积分页挂在裁剪页下",
            item(window, state["crop"]).childCount() >= 1,
        )
        check(
            "可见顺序是主页 → 裁剪 → 积分",
            panel(window).visible_page_ids()[:3] == ["home", state["crop"], state["integral"]],
        )
        save_shot(window, "page_tree_built.png")
        QTimer.singleShot(200, guarded(step_arrow_click))

    # --------------------------------------------------- 箭头不切页 / 名称切页
    def step_arrow_click():
        workspace(window).activate_page("home")
        click_row(tree(window), item(window, "home"), arrow=True)
        current = workspace(window).current_page_id
        check(f"点箭头不切页（当前仍是 {current}）", current == "home")
        check("点箭头收起该支", not item(window, "home").isExpanded())
        save_shot(window, "page_tree_collapsed.png")
        click_row(tree(window), item(window, "home"), arrow=True)
        check("再点箭头展开回来", item(window, "home").isExpanded())
        QTimer.singleShot(200, guarded(step_name_click))

    def step_name_click():
        click_row(tree(window), item(window, state["crop"]))
        check(
            "点名称切换页面",
            workspace(window).current_page_id == state["crop"],
        )
        QTimer.singleShot(3000, guarded(step_long_name))

    # -------------------------------------------------------- 长中文名称
    def step_long_name():
        state["long_title"] = "超长的页面名称：沿能量轴积分得到的费米面（页面树横向滚动验收）"
        answer_dialog(f"  {state['long_title']}  ")
        workspace(window)._on_rename_requested()
        QTimer.singleShot(800, guarded(step_long_name_check))

    def step_long_name_check():
        spec = workspace(window).current_spec()
        row = item(window, spec.page_id) if spec is not None else None
        check("长中文名改名成功（首尾空白去掉）", spec is not None and spec.title == state["long_title"])
        check(
            "页眉同步新名称",
            workspace(window).page_title.text() == state["long_title"],
        )
        check("树行显示完整名称", row is not None and row.text(0) == state["long_title"])
        check(
            "树行提示带完整名称",
            row is not None and state["long_title"] in row.toolTip(0),
        )
        check(
            "长名称没挤掉页眉关闭按钮",
            workspace(window).close_button.isVisible()
            and workspace(window).close_button.x() > 0,
        )
        check(
            "长名称可以横向滚动看全",
            tree(window).horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded
            and tree(window).horizontalScrollBar().maximum() > 0,
        )
        save_shot(window, "page_tree_long_name.png")
        QTimer.singleShot(200, guarded(step_search))

    # ------------------------------------------------------------ 搜索
    def step_search():
        # 用箭头收起整棵树，模拟用户手动折叠后再搜索。
        for page_id in list(panel(window)._items):
            item(window, page_id).setExpanded(False)
        state["expanded_before_search"] = item(window, "home").isExpanded()
        panel(window).search_edit.setText("费米面")

        titles = panel(window).entry_titles()
        check("搜索显示命中页面", state["long_title"] in titles)
        check("搜索显示命中页面的祖先", "原始视图" in titles)
        check(
            "搜索临时展开命中路径",
            panel(window).visible_page_ids() == ["home", state["crop"]],
        )
        check(
            "搜索隐藏不相关的页面",
            state["integral"] not in panel(window).visible_page_ids(),
        )
        save_shot(window, "page_tree_search.png")
        QTimer.singleShot(200, guarded(step_search_empty))

    def step_search_empty():
        panel(window).search_edit.setText("绝对不存在的页面名称")
        check("无匹配时给出空结果提示", panel(window).empty_label.isVisible())
        check(
            "无匹配时画布保持当前页面",
            workspace(window).current_page_id == state["crop"],
        )
        panel(window).search_edit.clear()
        check(
            "清空搜索后恢复搜索前的展开状态",
            item(window, "home").isExpanded() == state["expanded_before_search"],
        )
        check("清空搜索后命中页面重新可见", not item(window, state["crop"]).isHidden())
        check("清空搜索后空结果提示消失", not panel(window).empty_label.isVisible())
        QTimer.singleShot(200, guarded(step_width))

    # ------------------------------------------------------------ 栏宽
    def step_width():
        workspace(window).set_nav_width(320)
        check("拖动后栏宽变为 320", workspace(window).nav_width() == 320)
        workspace(window).set_nav_width(40)
        check(f"低于最小宽度被夹到 {NAV_MIN_WIDTH}", workspace(window).nav_width() == NAV_MIN_WIDTH)
        workspace(window).set_nav_width(100000)
        check(
            f"画布至少保留 {CANVAS_MIN_WIDTH} 像素",
            workspace(window).splitter.sizes()[1] >= CANVAS_MIN_WIDTH,
        )
        workspace(window).set_nav_width(260)
        workspace(window).save_nav_width()
        check("设为 260 后栏宽生效", workspace(window).nav_width() == 260)
        save_shot(window, "page_tree_width.png")
        QTimer.singleShot(200, guarded(step_reopen_width))

    def step_reopen_width():
        """不重启进程，另开一个工作区读同一份设置，验证栏宽跨会话保留。"""
        from PyQt5.QtWidgets import QWidget

        from bandscope.ui.result_workspace import ResultWorkspace

        probe = ResultWorkspace(QWidget())
        probe.resize(900, 600)
        probe.show()
        state["reopened_width"] = probe.nav_width()
        probe.hide()
        probe.deleteLater()
        check(
            f"栏宽跨重启保留（读回 {state['reopened_width']}）",
            state["reopened_width"] == 260,
        )
        QTimer.singleShot(200, guarded(step_delete_cancel))

    # ------------------------------------------------------ 切页与删除
    def step_delete_cancel():
        workspace(window).activate_page(state["crop"])
        state["before_delete"] = sorted(workspace(window).page_specs)
        state["delete_count"] = workspace(window).page_delete_plan(state["crop"])["count"]
        check("裁剪支路包含两个页面", state["delete_count"] == 2)

        answer_message_box(QMessageBox.No)
        workspace(window).close_current_page()
        QTimer.singleShot(1200, guarded(step_delete_cancel_check))

    def step_delete_cancel_check():
        boxes = state.get("delete_boxes", [])
        check("删除前弹出确认", bool(boxes))
        if boxes:
            box = boxes[-1]
            check("确认里写出目标名称", state["long_title"] in box["text"])
            check(f"确认里写出总数 {state['delete_count']}", str(state["delete_count"]) in box["text"])
            check("默认按钮是取消", box["default"] == box["no"])
            check("确认按钮文字是删除 / 取消", (box["yes"], box["no"]) == ("删除", "取消"))
        check("取消后页面仍在", sorted(workspace(window).page_specs) == state["before_delete"])
        save_shot(window, "page_tree_delete_confirm.png")
        QTimer.singleShot(200, guarded(step_delete_confirm))

    def step_delete_confirm():
        state["closed_events"] = []
        workspace(window).page_closed.connect(state["closed_events"].append)
        answer_message_box(QMessageBox.Yes)
        workspace(window).close_current_page()
        QTimer.singleShot(2000, guarded(step_delete_confirm_check))

    def step_delete_confirm_check():
        remaining = sorted(workspace(window).page_specs)
        check("确认后父页与子页一起删除", remaining == ["home"])
        check("删除逐页发出关闭通知", sorted(state["closed_events"]) == sorted([state["crop"], state["integral"]]))
        check("子页先于父页收到通知", state["closed_events"].index(state["integral"]) < state["closed_events"].index(state["crop"]))
        check("当前页回退到主页", workspace(window).current_page_id == "home")
        QTimer.singleShot(200, guarded(step_home_protected))

    def step_home_protected():
        plan = workspace(window).page_delete_plan("home")
        check("主页受保护，删除预案被拦截", plan["blocked"] == "protected_root")
        check("主页删除请求被拒绝", workspace(window).delete_page("home") is False)
        check("主页仍在", "home" in workspace(window).page_specs)
        QTimer.singleShot(200, guarded(step_number_shortcut))

    def step_number_shortcut():
        state["visible"] = workspace(window).visible_page_ids()
        check("可见行顺序以主页开头", state["visible"][:1] == ["home"])
        check(
            "数字切页按可见行顺序",
            window._ordered_left_workspace_page_ids() == state["visible"],
        )
        QTimer.singleShot(200, guarded(step_focus_isolation))

    def step_focus_isolation():
        before = window.timeline_bar.slider_time.value()
        tree(window).setFocus()
        QApplication.processEvents()
        check("页面树持有焦点时方向键不逐帧", window._focus_blocks_frame_step(tree(window)))
        key_event = QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier)
        handled = window._handle_frame_step_shortcut(key_event)
        check(
            "页面树持焦时方向键不移动时间轴",
            not handled and window.timeline_bar.slider_time.value() == before,
        )
        check(
            "搜索框持有焦点时同样不逐帧",
            window._focus_blocks_frame_step(panel(window).search_edit),
        )
        QTimer.singleShot(200, guarded(step_done))

    def step_done():
        save_shot(window, "page_tree_final.png")
        finish()

    window.load_data(NPZ)
    QTimer.singleShot(8000, guarded(step_loaded))
    QTimer.singleShot(300000, lambda: app.exit(2))  # 兜底防挂死

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
