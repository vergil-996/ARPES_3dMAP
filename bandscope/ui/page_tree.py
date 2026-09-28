# -*- coding: utf-8 -*-
"""常驻页面树：左侧按来源层级展示结果页，替代旧的图标栏 + 树浮层。

交互按 Windows 文件导航的习惯设计：

- 点击名称切换页面；点击展开箭头只展开/收起，不切页。
- 右键或 F2 在树内直接改名，Enter 或失焦保存，Esc 取消。
- 右键“删除页面”或 Delete 请求删除；是否确认、删哪些页由 ``ResultWorkspace`` 决定。
- 拖拽只在同一父节点内排序，父节点携带整支移动。
- 顶部搜索按名称过滤（不区分大小写），显示命中页面及其祖先；清空后恢复原展开状态与滚动位置。

本模块只负责“怎么显示、怎么操作”，不持有页面记录：``set_pages`` 每次由
``ResultWorkspace`` 喂进最新快照，页面身份一律用 ``page_id`` 关联。
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from siui.core import SiGlobal

import bandscope.ui.theme as theme

#: 页面类型 -> SiUI 图标名；与旧图标栏保持同一套图形。
PAGE_KIND_ICONS = {
    "home": "ic_fluent_home_filled",
    "control_panel": "ic_fluent_wrench_screwdriver_filled",
    "time_integral": "ic_fluent_history_filled",
    "axis_integral": "ic_fluent_data_trending_filled",
    "axis_integral_crop": "ic_fluent_data_trending_filled",
    "slice_dos": "ic_fluent_table_stack_right_filled",
    "energy_dos": "ic_fluent_document_data_filled",
    "curve_comparison_1d": "ic_fluent_data_trending_filled",
    "waterfall_edc": "ic_fluent_document_data_filled",
    "edc_curve": "ic_fluent_document_data_filled",
    "second_derivative": "ic_fluent_document_data_filled",
    "log_curve": "ic_fluent_document_data_filled",
}
DEFAULT_PAGE_ICON = "ic_fluent_document_data_filled"

_ICON_CACHE: Dict[str, QIcon] = {}


def page_kind_icon(page_kind: str) -> QIcon:
    """按页面类型取图标；图标包不可用时退化为无图标，不影响导航。"""
    name = PAGE_KIND_ICONS.get(page_kind, DEFAULT_PAGE_ICON)
    if name not in _ICON_CACHE:
        try:
            icon = SiGlobal.siui.iconpack.toIcon(name, QSize(16, 16), theme.TEXT_2)
        except Exception:
            icon = QIcon()
        _ICON_CACHE[name] = icon
    return _ICON_CACHE[name]


@dataclass(frozen=True)
class PageTreeEntry:
    """树中一行的展示数据；页面记录仍由 ``ResultWorkspace`` 持有。"""

    page_id: str
    title: str
    page_kind: str = ""
    scope_label: str = ""
    renameable: bool = True
    deletable: bool = True

    def tooltip(self) -> str:
        text = str(self.title)
        if self.scope_label:
            return f"{text}\n数据范围：{self.scope_label}"
        return text


class InlineRenameEditor(QLineEdit):
    """树内改名编辑框：Enter/失焦提交，Esc 取消。"""

    committed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent, text: str):
        super().__init__(parent)
        self.setText(str(text))
        self.selectAll()
        self._finished = False

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._finish(cancelled=True)
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._finish(cancelled=False)
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if not self._finished:
            self._finish(cancelled=False)

    def _finish(self, *, cancelled: bool):
        if self._finished:
            return
        self._finished = True
        if cancelled:
            self.cancelled.emit()
        else:
            self.committed.emit(self.text())


class PageTreeView(QTreeWidget):
    """页面树控件：拖拽限制为同级排序，方向键只操作树。"""

    page_activated = pyqtSignal(str)
    rename_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    reorder_requested = pyqtSignal(str, str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page_tree")
        self.setHeaderHidden(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setIndentation(16)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setExpandsOnDoubleClick(False)
        self.setAnimated(False)
        # 长名称不省略，靠横向滚动条完整查看：列宽跟内容走，滚动条才有行程。
        self.setTextElideMode(Qt.ElideNone)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.header().setStretchLastSection(False)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self._dragged_item: Optional[QTreeWidgetItem] = None
        self._pressed_on_branch = False
        self.itemClicked.connect(self._on_item_clicked)

    # ---------------------------------------------------------------- 取值
    @staticmethod
    def item_page_id(item: Optional[QTreeWidgetItem]) -> Optional[str]:
        if item is None:
            return None
        page_id = item.data(0, Qt.UserRole)
        return str(page_id) if page_id else None

    def visible_page_ids(self) -> List[str]:
        """按树中先后顺序列出 page_id（数字切页与键盘导航共用）。

        只排除被搜索隐藏的行；折叠分支里的页面仍在顺序中，与折叠状态无关。
        """
        ordered: List[str] = []

        def walk(item: QTreeWidgetItem):
            if item.isHidden():
                return
            page_id = self.item_page_id(item)
            if page_id is not None:
                ordered.append(page_id)
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(self.topLevelItemCount()):
            walk(self.topLevelItem(index))
        return ordered

    # ---------------------------------------------------------------- 点击
    def _branch_hit(self, pos):
        """返回 ``(点中展开箭头?, 命中的条目)``。"""
        index = self.indexAt(pos)
        if not index.isValid():
            return False, None
        item = self.itemFromIndex(index)
        if item is None:
            return False, None
        if item.childCount() == 0:
            return False, item
        rect = self.visualRect(index)
        arrow_left = rect.left() - self.indentation()
        return (arrow_left <= pos.x() < rect.left()), item

    def mousePressEvent(self, event):
        """箭头点击在按下时就自己处理并吃掉事件。

        Qt 自己的分支点击也会翻一次展开状态，两下抵消后看起来毫无反应；
        这里整条吃掉，既不切页也不动当前行。
        """
        if event.button() == Qt.LeftButton:
            on_arrow, item = self._branch_hit(event.pos())
            if on_arrow and item is not None:
                self._pressed_on_branch = True
                item.setExpanded(not item.isExpanded())
                event.accept()
                return
            self._pressed_on_branch = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pressed_on_branch and event.button() == Qt.LeftButton:
            self._pressed_on_branch = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _on_item_clicked(self, item, _column):
        page_id = self.item_page_id(item)
        if page_id is not None:
            self.page_activated.emit(page_id)

    # ---------------------------------------------------------------- 键盘
    def keyPressEvent(self, event):
        item = self.currentItem()
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            page_id = self.item_page_id(item)
            if page_id is not None:
                self.page_activated.emit(page_id)
            event.accept()
            return
        if key == Qt.Key_Delete:
            page_id = self.item_page_id(item)
            if page_id is not None:
                self.delete_requested.emit(page_id)
            event.accept()
            return
        if key == Qt.Key_F2:
            page_id = self.item_page_id(item)
            if page_id is not None:
                self.rename_requested.emit(page_id)
            event.accept()
            return
        if key == Qt.Key_Left:
            # 只收展，不改当前行也不切页。
            if item is not None and item.childCount() and item.isExpanded():
                item.setExpanded(False)
            event.accept()
            return
        if key == Qt.Key_Right:
            if item is not None and item.childCount() and not item.isExpanded():
                item.setExpanded(True)
            event.accept()
            return
        super().keyPressEvent(event)

    # ---------------------------------------------------------------- 拖拽
    def startDrag(self, supported_actions):
        self._dragged_item = self.currentItem()
        try:
            super().startDrag(supported_actions)
        finally:
            self._dragged_item = None

    def resolve_drop(self, dragged, target, position):
        """把一次投放解析成 ``(拖拽页, 参照页, 插到它前面?)``；不允许则返回 None。

        只接受同一父节点内的排序：跨父节点、落回自身、以及落在空白区都不算。
        落在条目上按“插到它前面”处理，避免 Qt 默认的“变成子节点”。
        """
        if dragged is None or target is None or target is dragged:
            return None
        if target.parent() is not dragged.parent():
            return None
        if position not in (
            QAbstractItemView.AboveItem,
            QAbstractItemView.BelowItem,
            QAbstractItemView.OnItem,
        ):
            return None
        dragged_id = self.item_page_id(dragged)
        target_id = self.item_page_id(target)
        if not dragged_id or not target_id:
            return None
        return dragged_id, target_id, position != QAbstractItemView.BelowItem

    def dropEvent(self, event):
        # 顺序统一由工作区重算，这里只把意图报上去，不让 Qt 自己搬条目。
        resolved = self.resolve_drop(
            self._dragged_item, self.itemAt(event.pos()), self.dropIndicatorPosition()
        )
        if resolved is None:
            event.ignore()
            return
        self.reorder_requested.emit(*resolved)
        event.accept()


class PageTreePanel(QWidget):
    """常驻页面树面板：搜索框 + 树 + 辅助操作。"""

    page_activated = pyqtSignal(str)
    rename_committed = pyqtSignal(str, str)
    restore_name_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    reorder_requested = pyqtSignal(str, str, bool)

    MAX_DEPTH = 64

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page_tree_panel")
        self._entries: Dict[str, PageTreeEntry] = {}
        self._children: Dict[Optional[str], List[str]] = {}
        self._parent_of_id: Dict[str, Optional[str]] = {}
        self._items: Dict[str, QTreeWidgetItem] = {}
        self._expanded_ids: set = set()
        self._known_page_ids: set = set()
        self._current_page_id: Optional[str] = None
        self._anchor_page_id: Optional[str] = None
        self._search_anchor_id: Optional[str] = None
        self._search_text = ""
        self._signature = None
        self._editor: Optional[InlineRenameEditor] = None
        self._editing_page_id: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 10, 8, 10)
        root.setSpacing(8)

        self.search_edit = QLineEdit(self)
        self.search_edit.setObjectName("page_tree_search")
        self.search_edit.setPlaceholderText("搜索页面名称")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_changed)
        root.addWidget(self.search_edit)

        tools = QHBoxLayout()
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(6)
        self.expand_all_button = self._make_tool_button("全部展开", "展开所有页面")
        self.collapse_all_button = self._make_tool_button("全部折叠", "折叠所有页面")
        self.locate_button = self._make_tool_button("定位当前页", "清空搜索并滚动到当前页面")
        self.expand_all_button.clicked.connect(self.expand_all)
        self.collapse_all_button.clicked.connect(self.collapse_all)
        self.locate_button.clicked.connect(self.locate_current_page)
        for button in (self.expand_all_button, self.collapse_all_button, self.locate_button):
            tools.addWidget(button)
        tools.addStretch(1)
        root.addLayout(tools)

        self.tree = PageTreeView(self)
        self.tree.page_activated.connect(self.page_activated)
        self.tree.rename_requested.connect(self.begin_rename)
        self.tree.delete_requested.connect(self.delete_requested)
        self.tree.reorder_requested.connect(self.reorder_requested)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemCollapsed.connect(self._on_item_collapsed)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self.tree, 1)

        self.empty_label = QLabel("没有匹配的页面", self)
        self.empty_label.setObjectName("page_tree_empty")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.hide()
        root.addWidget(self.empty_label)

        self.setStyleSheet(
            """
            QWidget#page_tree_panel {
                background-color: %(BG2)s;
            }
            QLineEdit#page_tree_search {
                background-color: %(BG1)s;
                color: %(T1)s;
                border: 1px solid %(BORD)s;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
            }
            QLineEdit#page_tree_search:focus {
                border-color: %(ACC_DIM)s;
            }
            QToolButton#page_tree_tool {
                background-color: %(BG3)s;
                color: %(T2)s;
                border: none;
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 11px;
            }
            QToolButton#page_tree_tool:hover {
                background-color: %(BG4)s;
                color: %(T1)s;
            }
            QTreeWidget#page_tree {
                background-color: transparent;
                color: %(T2)s;
                border: none;
                outline: none;
                font-size: 12px;
            }
            QTreeWidget#page_tree::item {
                height: 26px;
                padding: 1px 4px;
                border-radius: 6px;
            }
            QTreeWidget#page_tree::item:hover {
                background-color: %(BG3)s;
            }
            QTreeWidget#page_tree::item:selected {
                background-color: %(ACC_SOFT)s;
                color: %(T1)s;
            }
            QLabel#page_tree_empty {
                color: %(T3)s;
                font-size: 11px;
                padding: 8px 0;
            }
            QLineEdit#page_tree_editor {
                background-color: %(BG1)s;
                color: %(T1)s;
                border: 1px solid %(ACCENT)s;
                border-radius: 4px;
                padding: 0 4px;
                font-size: 12px;
                selection-background-color: %(ACCENT)s;
                selection-color: %(ACC_ON)s;
            }
            """ % theme.QSS_TOKENS
        )

    def _make_tool_button(self, text, tooltip):
        button = QToolButton(self)
        button.setObjectName("page_tree_tool")
        button.setText(text)
        button.setToolTip(tooltip)
        button.setCursor(Qt.PointingHandCursor)
        button.setAutoRaise(True)
        return button

    # ------------------------------------------------------------ 对外接口
    def set_pages(
        self,
        entries: Dict[str, PageTreeEntry],
        children_by_parent: Dict[Optional[str], Sequence[str]],
        current_page_id: Optional[str] = None,
    ):
        """重建树。展开状态、滚动位置与搜索词都在重建中保留。"""
        signature = self._make_signature(entries, children_by_parent)
        if signature == self._signature:
            self._current_page_id = current_page_id
            self._sync_current_row()
            return

        self._remember_anchor()
        self._entries = dict(entries)
        self._children = {
            parent_id: [str(pid) for pid in ids if str(pid) in entries]
            for parent_id, ids in children_by_parent.items()
        }
        self._parent_of_id = {}
        for parent_id, ids in self._children.items():
            for page_id in ids:
                self._parent_of_id.setdefault(page_id, parent_id)
        self._current_page_id = current_page_id
        self._rebuild_tree()
        self._signature = signature
        self._sync_current_row()
        self._restore_anchor()

    def visible_page_ids(self) -> List[str]:
        """当前可见行顺序；搜索时只算可见行。"""
        return self.tree.visible_page_ids()

    def set_current_page(self, page_id: Optional[str]):
        """高亮当前页；页面被搜索隐藏时先清空搜索再定位。"""
        self._current_page_id = page_id
        item = self._items.get(page_id) if page_id is not None else None
        if item is not None and item.isHidden():
            self.clear_search()
            self.reveal_page(page_id)
            return
        self._sync_current_row()

    def locate_current_page(self):
        """“定位当前页”按钮：清空搜索并展开祖先。"""
        self.clear_search()
        self.reveal_page(self._current_page_id)

    def reveal_page(self, page_id: Optional[str]):
        """展开祖先并滚动到该页；页面被搜索隐藏时先清空搜索。"""
        if page_id is None:
            return
        item = self._items.get(page_id)
        if item is None:
            return
        if item.isHidden():
            self.clear_search()
        self._expand_ancestors(page_id)
        self.tree.scrollToItem(item, QAbstractItemView.PositionAtCenter)
        self.tree.setCurrentItem(item)

    def clear_search(self):
        if not self._search_text:
            return
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self._search_text = ""
        self._anchor_page_id = self._search_anchor_id
        self._search_anchor_id = None
        self._apply_filter()

    def search_text(self) -> str:
        return self._search_text

    def is_renaming(self, page_id: Optional[str] = None) -> bool:
        if self._editing_page_id is None:
            return False
        return page_id is None or page_id == self._editing_page_id

    def entry_titles(self) -> List[str]:
        """按顺序列出树上（未被搜索隐藏的）标题；供测试与验收脚本核对内容。"""
        titles: List[str] = []

        def walk(item: QTreeWidgetItem):
            if item.isHidden():
                return
            titles.append(item.text(0))
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(index))
        return titles

    # ------------------------------------------------------------ 树构建
    def update_entry(self, entry: PageTreeEntry):
        """单行内容刷新：标题、图标、提示；不重建整棵树。"""
        item = self._items.get(entry.page_id)
        if item is None:
            return
        self._entries[entry.page_id] = entry
        item.setText(0, entry.title)
        item.setIcon(0, page_kind_icon(entry.page_kind))
        item.setToolTip(0, entry.tooltip())
        self._signature = self._make_signature(self._entries, self._children)

    def _make_signature(self, entries, children_by_parent):
        rows = tuple(
            (
                page_id,
                entry.title,
                entry.page_kind,
                entry.scope_label,
                entry.renameable,
                entry.deletable,
            )
            for page_id, entry in sorted(entries.items())
        )
        links = tuple(
            (str(parent_id), tuple(str(pid) for pid in ids))
            for parent_id, ids in sorted(
                children_by_parent.items(), key=lambda pair: str(pair[0])
            )
        )
        return rows, links

    def _remember_anchor(self):
        """记下视口顶部那一行，重建后据此还原滚动位置。"""
        self._anchor_page_id = None
        if self.tree.verticalScrollBar().value() <= 0:
            return
        for index in range(self.tree.topLevelItemCount()):
            if self._remember_anchor_in(self.tree.topLevelItem(index)):
                return

    def _remember_anchor_in(self, item: QTreeWidgetItem) -> bool:
        page_id = self.tree.item_page_id(item)
        if page_id is not None and not item.isHidden():
            if self.tree.visualItemRect(item).top() >= 0:
                self._anchor_page_id = page_id
                return True
        if not item.isExpanded():
            return False
        for index in range(item.childCount()):
            if self._remember_anchor_in(item.child(index)):
                return True
        return False

    def _restore_anchor(self):
        page_id, self._anchor_page_id = self._anchor_page_id, None
        if page_id is None:
            return
        item = self._items.get(page_id)
        if item is not None and not item.isHidden():
            self.tree.scrollToItem(item, QAbstractItemView.PositionAtTop)

    def _rebuild_tree(self):
        first_build = not self._known_page_ids
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            self._items = {}
            self._build_group(None, 0)
        finally:
            self.tree.blockSignals(False)
        new_ids = [pid for pid in self._entries if pid not in self._known_page_ids]
        self._known_page_ids = set(self._entries)
        self._apply_expansion(new_ids)
        self._apply_filter()

        if first_build:
            focus_id = self._current_page_id
        else:
            focus_id = new_ids[0] if new_ids else None
        if focus_id is not None:
            self._expand_ancestors(focus_id)
            self._locate(focus_id)

    def _build_group(self, parent_id: Optional[str], depth: int, parent_item=None):
        if depth > self.MAX_DEPTH:  # 环状来源关系的兜底，避免无限递归
            return
        for page_id in self._ordered_children(parent_id):
            entry = self._entries.get(page_id)
            if entry is None:
                continue
            item = QTreeWidgetItem([entry.title])
            item.setData(0, Qt.UserRole, page_id)
            item.setIcon(0, page_kind_icon(entry.page_kind))
            item.setToolTip(0, entry.tooltip())
            item.setFlags(
                Qt.ItemIsSelectable
                | Qt.ItemIsEnabled
                | Qt.ItemIsDragEnabled
                | Qt.ItemIsDropEnabled
            )
            if parent_item is None:
                self.tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            self._items[page_id] = item
            self._build_group(page_id, depth + 1, item)

    def _ordered_children(self, parent_id: Optional[str]) -> List[str]:
        ordered = [pid for pid in self._children.get(parent_id, []) if pid in self._entries]
        if parent_id is not None:
            return ordered
        # 顶层：来源缺失或来源已不在树里的页面也放上来，保证每页都能看到。
        seen = set(ordered)
        for page_id in self._entries:
            if page_id in seen:
                continue
            parent = self._parent_of_id.get(page_id)
            if parent is None or parent not in self._entries:
                seen.add(page_id)
                ordered.append(page_id)
        return ordered

    def _ancestor_ids(self, page_id: str) -> List[str]:
        chain = []
        cursor = self._parent_of_id.get(page_id)
        visited = set()
        while cursor is not None and cursor not in visited:
            visited.add(cursor)
            chain.append(cursor)
            cursor = self._parent_of_id.get(cursor)
        return chain

    # ------------------------------------------------------------ 展开状态
    def _apply_expansion(self, new_page_ids: Sequence[str] = ()):
        """把记住的展开状态刷到树上；新增页面的祖先顺带展开并记下来。"""
        expanded = set(self._expanded_ids)
        for page_id in new_page_ids:
            expanded.update(self._ancestor_ids(page_id))
        self._expanded_ids = expanded
        self.tree.blockSignals(True)
        try:
            for page_id, item in self._items.items():
                if item.childCount():
                    item.setExpanded(page_id in expanded)
        finally:
            self.tree.blockSignals(False)

    def _expand_ancestors(self, page_id: str):
        for ancestor_id in self._ancestor_ids(page_id):
            item = self._items.get(ancestor_id)
            if item is not None and item.childCount():
                item.setExpanded(True)

    def _locate(self, page_id: str):
        item = self._items.get(page_id)
        if item is None or item.isHidden():
            return
        self.tree.scrollToItem(item, QAbstractItemView.PositionAtCenter)
        self.tree.setCurrentItem(item)

    def _on_item_expanded(self, item):
        page_id = self.tree.item_page_id(item)
        if page_id is not None and not self._search_text:
            self._expanded_ids.add(page_id)

    def _on_item_collapsed(self, item):
        page_id = self.tree.item_page_id(item)
        if page_id is not None and not self._search_text:
            self._expanded_ids.discard(page_id)

    def expand_all(self):
        self.tree.blockSignals(True)
        try:
            self.tree.expandAll()
        finally:
            self.tree.blockSignals(False)
        if not self._search_text:
            self._expanded_ids = {
                page_id for page_id, item in self._items.items() if item.childCount()
            }

    def collapse_all(self):
        self.tree.blockSignals(True)
        try:
            self.tree.collapseAll()
        finally:
            self.tree.blockSignals(False)
        if not self._search_text:
            self._expanded_ids = set()

    def _sync_current_row(self):
        item = self._items.get(self._current_page_id) if self._current_page_id else None
        if item is None:
            self.tree.setCurrentItem(None)
            return
        if self.tree.currentItem() is not item:
            self.tree.setCurrentItem(item)

    # ------------------------------------------------------------ 搜索
    def _on_search_changed(self, text):
        normalized = str(text or "")
        was_empty = not self._search_text.strip()
        now_empty = not normalized.strip()
        if was_empty and not now_empty:
            # 进入搜索：记下原始滚动位置。
            self._remember_anchor()
            self._search_anchor_id = self._anchor_page_id
            self._anchor_page_id = None
        self._search_text = normalized
        if now_empty:
            self._anchor_page_id = self._search_anchor_id
            self._search_anchor_id = None
        self._apply_filter()

    def _apply_filter(self):
        query = self._search_text.strip().lower()
        searchable = not self._search_text.strip()
        self.tree.setDragEnabled(searchable)
        self.tree.setAcceptDrops(searchable)
        if not query:
            hidden = set()
        else:
            matches = {
                page_id
                for page_id, entry in self._entries.items()
                if query in str(entry.title).lower()
            }
            hidden = set(self._entries) - matches
            for page_id in list(matches):
                hidden -= set(self._ancestor_ids(page_id))

        self.tree.blockSignals(True)
        try:
            for page_id, item in self._items.items():
                item.setHidden(page_id in hidden)
                # 搜索是临时视图：只把命中路径撑开，不动平时记住的展开状态。
                if query and page_id not in hidden:
                    self._expand_ancestors(page_id)
        finally:
            self.tree.blockSignals(False)

        self.empty_label.setVisible(bool(query) and len(hidden) == len(self._entries))
        if query:
            self._sync_current_row()
        else:
            # 退出搜索：回到搜索前记住的展开状态与滚动位置。
            self._apply_expansion()
            self._restore_anchor()
            self._sync_current_row()

    # ------------------------------------------------------------ 改名
    def begin_rename(self, page_id) -> bool:
        entry = self._entries.get(page_id)
        item = self._items.get(page_id)
        if entry is None or item is None or not entry.renameable:
            return False
        self._close_editor()
        if item.isHidden():
            self.reveal_page(page_id)
        self.tree.scrollToItem(item, QAbstractItemView.EnsureVisible)
        rect = self.tree.visualItemRect(item)
        if not rect.isValid():
            return False

        editor = InlineRenameEditor(self.tree.viewport(), entry.title)
        editor.setObjectName("page_tree_editor")
        editor.setGeometry(rect.adjusted(1, 1, -1, -1))
        editor.committed.connect(lambda text, pid=page_id: self._commit_rename(pid, text))
        editor.cancelled.connect(self._cancel_rename)
        self._editor = editor
        self._editing_page_id = page_id
        editor.show()
        editor.setFocus()
        return True

    def _commit_rename(self, page_id, text):
        self._close_editor()
        self.rename_committed.emit(page_id, str(text))

    def _cancel_rename(self):
        self._close_editor()

    def _close_editor(self):
        editor, self._editor = self._editor, None
        self._editing_page_id = None
        if editor is None:
            return
        editor.hide()
        editor.setParent(None)
        editor.deleteLater()

    # ------------------------------------------------------------ 右键菜单
    def _on_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        page_id = self.tree.item_page_id(item)
        entry = self._entries.get(page_id) if page_id else None
        if entry is None:
            return
        self.tree.setCurrentItem(item)

        menu = QMenu(self)
        rename_action = menu.addAction("重命名")
        rename_action.setEnabled(entry.renameable)
        rename_action.triggered.connect(lambda: self.begin_rename(page_id))

        restore_action = menu.addAction("恢复自动名称")
        restore_action.setEnabled(entry.renameable)
        restore_action.triggered.connect(lambda: self.restore_name_requested.emit(page_id))

        menu.addSeparator()
        delete_action = menu.addAction("删除页面")
        delete_action.setEnabled(entry.deletable)
        delete_action.triggered.connect(lambda: self.delete_requested.emit(page_id))

        menu.setStyleSheet(
            f"QMenu {{ color: {theme.TEXT_1}; background-color: {theme.BG_2}; }} "
            f"QMenu::item:selected {{ background-color: {theme.BG_4}; }}"
        )
        menu.exec_(self.tree.viewport().mapToGlobal(pos))
