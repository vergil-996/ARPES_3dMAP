# -*- coding: utf-8 -*-
"""合成页面树：页面树与删除流程的测试共用一套构造器。

只用 ``page_id`` / 标题 / 父页描述一棵树，不依赖本机实验数据；工作区创建的
``QSettings`` 由 ``tests/__init__.py`` 隔离到临时目录。
"""
from typing import Optional, Sequence, Tuple

from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QWidget

from bandscope.ui.result_workspace import AnalysisPageSpec, ResultWorkspace

HOME_ID = "home"
#: 树描述项：``(page_id, 标题, 父页 id, 页面类型)``；父页为 ``None`` 表示挂在主页下。
PageRow = Tuple[str, str, Optional[str], str]


def page_spec(page_id, title, kind="axis_integral", **kwargs) -> AnalysisPageSpec:
    return AnalysisPageSpec(page_id, title, kind, "test", **kwargs)


def make_workspace(
    rows: Sequence[PageRow] = (),
    *,
    home_title: str = "原始视图",
    display_widget: Optional[QWidget] = None,
) -> ResultWorkspace:
    """建一个带主页的工作区，按 ``rows`` 依次添加派生页。"""
    workspace = ResultWorkspace(display_widget if display_widget is not None else QWidget())
    workspace.set_home_page(page_spec(HOME_ID, home_title, "home", closeable=False))
    for page_id, title, parent_id, kind in rows:
        workspace.add_page(
            page_spec(page_id, title, kind, source_page_id=parent_id or HOME_ID)
        )
    return workspace


def reset_workspace_settings():
    """清掉上一次用例留下的页面顺序与栏宽，避免用例之间互相影响。

    设置本身仍然隔离在测试临时目录里（见 ``tests/support/environment.py``）。
    """
    settings = QSettings("ARPES", "ARPES_3dMAP")
    settings.remove("result_workspace/tab_order")
    settings.remove("result_workspace/nav_width")
    settings.sync()


def sibling_titles(workspace: ResultWorkspace, parent_id: Optional[str]) -> list:
    """某父页下同级页面的显示顺序（标题）。"""
    workspace._rebuild_children_map()
    return [
        str(workspace.page_specs[child_id].title)
        for child_id in workspace.children_by_parent.get(parent_id, [])
    ]
