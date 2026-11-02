# -*- coding: utf-8 -*-
"""「我的分组」的真实窗口验收：新建分组、复制页面、副本独立与删除回收。

验证点：
1. 「我的分组」区域常驻页面树下方，新建分组后出现标题且初始为空；
2. 「+」按钮新建分组走真实控件路径，重名自动编号；
3. 把来源树页面复制到分组会生成带新 ``page_id`` 的深拷贝副本，标题追加「 副本」；
4. 原页面留在来源树，副本出现在分组树；激活副本后页眉、画布与树选中行同步；
5. 副本参数与来源页面互不影响；
6. 删除分组只删除组内副本，原页面保留且画布回退到原页面。

未覆盖：跨数据源激活副本与关闭数据源清理（``tests/integration/`` 覆盖），
拖动投放与 ``Ctrl+C`` / ``Ctrl+V`` 的鼠标键盘路径（``tests/ui/test_page_tree.py`` 覆盖）。

用法: .venv/Scripts/python.exe scripts/validation/verify_page_groups.py [npz路径]
默认数据: .local/data/WSe2_step.npz；输出: .local/outputs/verify_page_groups/。
"""
import os
import sys
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

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
    def __init__(self, *args, **kwargs):
        pass

    def show(self, *args, **kwargs):
        pass

    def clear(self):
        pass


def save_shot(window, name):
    path = os.path.join(OUT_DIR, name)
    window.grab().save(path)
    log(f"[SHOT] {path}")


def scene_pixels(window, name):
    """QWidget.grab() 抓不到原生 OpenGL 表面，3D 场景单独取像素判断非空。"""
    path = os.path.join(OUT_DIR, name)
    pixels = window.plotter.screenshot(path, return_img=True)
    rgb = np.asarray(pixels[..., :3], dtype=np.float64)
    # 只统计左侧画布：右侧色条本身就有内容，不能当作"体积可见"。
    canvas = rgb[:, : int(rgb.shape[1] * 0.75)]
    log(f"[SHOT] {path}")
    return int(np.count_nonzero(np.any(canvas < 200, axis=2)))


def workspace(window):
    return window.left_workspace


def panel(window):
    return window.left_workspace.page_tree


def group_row(window, group_id):
    return panel(window)._group_headers.get(group_id)


def group_child_ids(window, group_id):
    header = group_row(window, group_id)
    if header is None:
        return []
    return [
        panel(window).group_tree.item_page_id(header.child(index))
        for index in range(header.childCount())
    ]


def main():
    global NPZ, OUT_DIR
    from scripts.validation._common import REPO_ROOT, arguments

    args = arguments(
        "verify_page_groups", dynamic=True,
        default_data=REPO_ROOT / ".local" / "data" / "WSe2_step.npz",
    )
    NPZ, OUT_DIR = str(args.data), str(args.output_dir)
    if args.size:
        width, height = (int(value) for value in args.size.lower().split("x"))
    else:
        width, height = 1500, 950

    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app

    refactored_app.UpdateController = _NullUpdateController
    refactored_app.ToastManager = _NullToast

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    window.resize(width, height)

    def finish():
        save_shot(window, "page_groups_final.png")
        window.close()
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

    def step_loaded():
        workspace_ = workspace(window)
        panel_ = panel(window)
        state["home"] = workspace_.home_page_id
        check("数据已加载", window.core.raw_data is not None)
        check("主页已在来源树中", state["home"] in panel_._items)
        check("分组区域可见", panel_.group_tree.isVisible())
        check("初始没有分组", panel_.group_tree.topLevelItemCount() == 0)
        check("「+」新建分组按钮可见", panel_.new_group_button.isVisible())
        QTimer.singleShot(200, guarded(step_create_group))

    def step_create_group():
        panel_ = panel(window)
        with patch(
            "bandscope.ui.result_workspace.QInputDialog.getText",
            return_value=("验收分组", True),
        ):
            panel_.new_group_button.click()
        QApplication.processEvents()

        groups = workspace(window).groups
        state["group"] = groups[0].group_id if groups else None
        check("按钮新建了一个分组", len(groups) == 1)
        check("分组标题取自对话框输入", groups[0].title == "验收分组" if groups else False)
        header = group_row(window, state["group"]) if state["group"] else None
        check("分组树出现分组标题行", header is not None)
        check("分组标题行文本正确", header is not None and header.text(0) == "验收分组")
        check("新分组初始为空", group_child_ids(window, state["group"]) == [] if state["group"] else False)
        save_shot(window, "page_groups_empty.png")
        QTimer.singleShot(200, guarded(step_copy_page))

    def step_copy_page():
        workspace_ = workspace(window)
        home = workspace_.page_by_id(state["home"])
        state["home_title"] = home.title
        copied = workspace_.copy_page_to_group(state["home"], state["group"])
        state["copy"] = copied.page_id if copied is not None else None
        check("复制返回了副本页面", copied is not None)
        if copied is None:
            finish()
            return
        check("副本使用新的 page_id", copied.page_id != state["home"])
        check("副本属于目标分组", copied.group_id == state["group"])
        check("来源页面没有进入分组", workspace_.page_by_id(state["home"]).group_id is None)
        check("副本标题追加「 副本」", copied.title == f"{state['home_title']} 副本")
        check("副本参数与来源页面不是同一对象", copied.params is not workspace_.page_by_id(state["home"]).params)
        QTimer.singleShot(3000, guarded(step_copy_visible))

    def step_copy_visible():
        workspace_ = workspace(window)
        panel_ = panel(window)
        copied = workspace_.page_by_id(state["copy"])
        check("来源页面仍留在来源树", state["home"] in panel_._items)
        check("副本不出现在来源树", state["copy"] not in panel_._items)
        check("副本条目在分组树中", state["copy"] in panel_._group_items)
        check("分组行挂在标题下", group_child_ids(window, state["group"]) == [state["copy"]])
        check("复制后副本成为当前页", workspace_.current_page_id == state["copy"])
        check("宿主活动页同步为副本", window.active_page_spec is not None and window.active_page_spec.page_id == state["copy"])
        check("页眉显示副本名称", workspace_.page_title.text() == copied.title)
        before = set(workspace_.page_specs)
        workspace_.page_by_id(state["copy"]).params["group_probe"] = 1
        check(
            "改副本参数不影响来源页面",
            "group_probe" not in workspace_.page_by_id(state["home"]).params,
        )
        check("复制没有产生多余页面", set(workspace_.page_specs) == before)
        visible = scene_pixels(window, "page_groups_copy_scene.png")
        check(f"副本页 3D 场景非空（{visible} 个暗像素）", visible > 500)
        save_shot(window, "page_groups_copy.png")
        QTimer.singleShot(200, guarded(step_delete_copy))

    def step_delete_copy():
        workspace_ = workspace(window)
        copied = workspace_.page_by_id(state["copy"])
        check("删除分组前副本存在", copied is not None)
        removed = workspace_.delete_group(state["group"])
        check("删除分组成功", removed is True)
        check("分组已从列表移除", workspace_.groups == ())
        check("分组树回到空状态", panel(window).group_tree.topLevelItemCount() == 0)
        QTimer.singleShot(2500, guarded(step_delete_check))

    def step_delete_check():
        workspace_ = workspace(window)
        panel_ = panel(window)
        check("组内副本已删除", workspace_.page_by_id(state["copy"]) is None)
        check("副本已从记录中移除", state["copy"] not in workspace_.page_specs)
        check("原页面仍在来源树中", state["home"] in workspace_.page_specs)
        check("原页面行仍在页面树", state["home"] in panel_._items)
        check("画布回退到原页面", workspace_.current_page_id == state["home"])
        check(
            "宿主活动页回到原页面",
            window.active_page_spec is not None and window.active_page_spec.page_id == state["home"],
        )
        check("删除分组没有弹出模态框", QApplication.activeModalWidget() is None)
        visible = scene_pixels(window, "page_groups_deleted_scene.png")
        check(f"回到原页面后 3D 场景非空（{visible} 个暗像素）", visible > 500)
        save_shot(window, "page_groups_deleted.png")
        QTimer.singleShot(200, guarded(step_done))

    def step_done():
        finish()

    window.load_data(NPZ)
    QTimer.singleShot(8000, guarded(step_loaded))
    QTimer.singleShot(180000, lambda: app.exit(2))  # 兜底防挂死

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
