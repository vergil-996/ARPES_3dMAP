# -*- coding: utf-8 -*-
"""常驻页面树：左侧按来源层级展示结果页，替代旧的图标栏 + 树浮层。

交互按 Windows 文件导航的习惯设计：

- 点击名称切换页面；点击展开箭头只展开/收起，不切页。
- 右键或 F2 在树内直接改名，Enter 或失焦保存，Esc 取消。
- 空白处拖动框选，Ctrl/Shift 多选；右键或 Delete 请求删除选中的页面。
- 原树内拖拽只在同一父节点内排序，父节点携带整支移动。
- 下方分组区域接受原树页面的拖动或 Ctrl+C/Ctrl+V，始终复制并保留原页面。
- 顶部搜索按名称过滤（不区分大小写），显示命中页面及其祖先；清空后恢复原展开状态与滚动位置。

本模块只负责“怎么显示、怎么操作”，不持有页面记录：``set_pages`` 每次由
``ResultWorkspace`` 喂进最新快照，页面身份一律用 ``page_id`` 关联。
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from PyQt5.QtCore import QItemSelectionModel, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QRubberBand,
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
    "edc_curve": "ic_fluent_document_data_filled",
    "second_derivative": "ic_fluent_document_data_filled",
    "log_curve": "ic_fluent_document_data_filled",
    "plugin_curve": "ic_fluent_data_trending_filled",
    "plugin_derived": "ic_fluent_document_data_filled",
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
    group_id: Optional[str] = None

    def tooltip(self) -> str:
        text = str(self.title)
        if self.scope_label:
            return f"{text}\n数据范围：{self.scope_label}"
        return text


@dataclass(frozen=True)
class PageGroupEntry:
    group_id: str
    title: str


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
    pages_delete_requested = pyqtSignal(list)
    reorder_requested = pyqtSignal(str, str, bool)
    copy_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page_tree")
        self.setHeaderHidden(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setIndentation(16)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
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
        self._selection_click = False
        self._rubber_origin: Optional[QPoint] = None
        self._rubber_base_ids: set = set()
        self._rubber_band = QRubberBand(QRubberBand.Rectangle, self.viewport())
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

    @staticmethod
    def item_is_displayed(item: QTreeWidgetItem) -> bool:
        """排除搜索隐藏行及折叠分支内的行，避免残留选择被意外删除。"""
        if item.isHidden():
            return False
        parent = item.parent()
        while parent is not None:
            if parent.isHidden() or not parent.isExpanded():
                return False
            parent = parent.parent()
        return True

    def displayed_items(self) -> List[QTreeWidgetItem]:
        items: List[QTreeWidgetItem] = []

        def walk(item):
            if item.isHidden():
                return
            items.append(item)
            if item.isExpanded():
                for index in range(item.childCount()):
                    walk(item.child(index))

        for index in range(self.topLevelItemCount()):
            walk(self.topLevelItem(index))
        return items

    def selected_page_ids(self, *, deletable_only=False) -> List[str]:
        return [
            self.item_page_id(item)
            for item in self.displayed_items()
            if item.isSelected()
            and (not deletable_only or item.data(0, Qt.UserRole + 1))
        ]

    def prune_hidden_selection(self):
        # selectedItems() 本身不返回隐藏行；遍历所有节点才能清掉隐藏选择。
        def walk(item):
            if item.isSelected() and not self.item_is_displayed(item):
                item.setSelected(False)
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(self.topLevelItemCount()):
            walk(self.topLevelItem(index))

    def request_delete_selected(self):
        selected = self.selected_page_ids()
        page_ids = self.selected_page_ids(deletable_only=True)
        if not page_ids:
            return
        if len(selected) > 1:
            self.pages_delete_requested.emit(page_ids)
        else:
            self.delete_requested.emit(page_ids[0])

    def row_at(self, pos):
        """名称右侧空白仍属于该行，可用于右键菜单和框选起点判断。"""
        item = self.itemAt(pos)
        if item is not None:
            return item
        for item in self.displayed_items():
            rect = self.visualItemRect(item)
            if rect.top() <= pos.y() <= rect.bottom():
                return item
        return None

    def _blank_hit(self, pos):
        item = self.row_at(pos)
        if item is None:
            return True
        rect = self.visualItemRect(item)
        icon_width = max(16, self.iconSize().width()) if not item.icon(0).isNull() else 0
        name_right = (
            rect.left() + icon_width
            + self.fontMetrics().horizontalAdvance(item.text(0)) + 12
        )
        return pos.x() > name_right

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
            self._selection_click = bool(
                event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)
            )
            on_arrow, item = self._branch_hit(event.pos())
            if on_arrow and item is not None:
                self._pressed_on_branch = True
                item.setExpanded(not item.isExpanded())
                event.accept()
                return
            self._pressed_on_branch = False
            if self._blank_hit(event.pos()):
                self.setFocus(Qt.MouseFocusReason)
                self._rubber_origin = event.pos()
                self._rubber_base_ids = (
                    set(self.selected_page_ids()) if self._selection_click else set()
                )
                if not self._selection_click:
                    self.clearSelection()
                event.accept()
                return
        if event.button() == Qt.RightButton:
            item = self.row_at(event.pos())
            if item is not None and item.isSelected():
                self.setCurrentItem(item, 0, QItemSelectionModel.NoUpdate)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._rubber_origin is not None:
            rect = QRect(self._rubber_origin, event.pos()).normalized()
            self._rubber_band.setGeometry(rect)
            self._rubber_band.show()
            for item in self.displayed_items():
                row_rect = self.visualItemRect(item)
                row_rect.setLeft(0)
                row_rect.setRight(self.viewport().width() - 1)
                intersects = row_rect.intersects(rect) and row_rect.intersects(
                    self.viewport().rect()
                )
                item.setSelected(
                    intersects or self.item_page_id(item) in self._rubber_base_ids
                )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._rubber_origin is not None and event.button() == Qt.LeftButton:
            self._rubber_origin = None
            self._rubber_band.hide()
            self._rubber_base_ids.clear()
            self._selection_click = False
            event.accept()
            return
        if self._pressed_on_branch and event.button() == Qt.LeftButton:
            self._pressed_on_branch = False
            self._selection_click = False
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._selection_click = False

    def _on_item_clicked(self, item, _column):
        if self._selection_click:
            return
        page_id = self.item_page_id(item)
        if page_id is not None:
            self.page_activated.emit(page_id)

    # ---------------------------------------------------------------- 键盘
    def keyPressEvent(self, event):
        item = self.currentItem()
        key = event.key()
        if event.modifiers() == Qt.ControlModifier and key == Qt.Key_C:
            page_id = self.item_page_id(item)
            if page_id is not None:
                self.copy_requested.emit(page_id)
            event.accept()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            page_id = self.item_page_id(item)
            if page_id is not None:
                self.page_activated.emit(page_id)
            event.accept()
            return
        if key == Qt.Key_Delete:
            self.request_delete_selected()
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
        if len(self.selected_page_ids()) != 1:
            return
        self._dragged_item = self.currentItem()
        try:
            super().startDrag(Qt.CopyAction | Qt.MoveAction)
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
        event.accept()
        # startDrag 返回前还会清理 InternalMove 的源行，之后再重建才不会误删新行。
        QTimer.singleShot(0, lambda: self.reorder_requested.emit(*resolved))


class PageGroupView(QTreeWidget):
    """分组区域：接收原树页面的复制投放，不移动来源页面。"""

    GROUP_ROLE = Qt.UserRole + 2

    page_activated = pyqtSignal(str)
    rename_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    copy_requested = pyqtSignal(str)
    paste_requested = pyqtSignal(str)
    group_rename_requested = pyqtSignal(str)
    group_delete_requested = pyqtSignal(str)
    page_drop_requested = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page_group_tree")
        self.setHeaderHidden(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setIndentation(16)
        self.setUniformRowHeights(True)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setExpandsOnDoubleClick(False)
        self.setAnimated(False)
        self.setTextElideMode(Qt.ElideNone)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.header().setStretchLastSection(False)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.itemClicked.connect(self._on_item_clicked)

    @staticmethod
    def item_page_id(item):
        if item is None:
            return None
        page_id = item.data(0, Qt.UserRole)
        return str(page_id) if page_id else None

    @classmethod
    def item_group_id(cls, item):
        if item is None:
            return None
        if item.parent() is not None:
            item = item.parent()
        group_id = item.data(0, cls.GROUP_ROLE)
        return str(group_id) if group_id else None

    def resolve_drop(self, source, target):
        if not isinstance(source, PageTreeView):
            return None
        page_id = source.item_page_id(source._dragged_item)
        group_id = self.item_group_id(target)
        if not page_id or not group_id:
            return None
        return page_id, group_id

    def _accept_external_copy(self, event):
        resolved = self.resolve_drop(event.source(), self.itemAt(event.pos()))
        if resolved is None:
            event.ignore()
            return None
        event.setDropAction(Qt.CopyAction)
        event.accept()
        return resolved

    def dragEnterEvent(self, event):
        if self._accept_external_copy(event) is None:
            event.ignore()

    def dragMoveEvent(self, event):
        self._accept_external_copy(event)

    def dropEvent(self, event):
        resolved = self._accept_external_copy(event)
        if resolved is not None:
            QTimer.singleShot(0, lambda: self.page_drop_requested.emit(*resolved))

    def _on_item_clicked(self, item, _column):
        page_id = self.item_page_id(item)
        if page_id is not None:
            self.page_activated.emit(page_id)

    def keyPressEvent(self, event):
        item = self.currentItem()
        page_id = self.item_page_id(item)
        group_id = self.item_group_id(item)
        if event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_C:
            if page_id:
                self.copy_requested.emit(page_id)
            event.accept()
            return
        if event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_V:
            if group_id:
                self.paste_requested.emit(group_id)
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and page_id:
            self.page_activated.emit(page_id)
            event.accept()
            return
        if event.key() == Qt.Key_Delete and page_id:
            self.delete_requested.emit(page_id)
            event.accept()
            return
        if event.key() == Qt.Key_F2:
            if page_id:
                self.rename_requested.emit(page_id)
            elif group_id:
                self.group_rename_requested.emit(group_id)
            event.accept()
            return
        super().keyPressEvent(event)


class PageTreePanel(QWidget):
    """常驻页面树面板：搜索框 + 来源树 + 独立分组区域。"""

    page_activated = pyqtSignal(str)
    rename_committed = pyqtSignal(str, str)
    restore_name_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    pages_delete_requested = pyqtSignal(list)
    reorder_requested = pyqtSignal(str, str, bool)
    group_create_requested = pyqtSignal()
    group_rename_requested = pyqtSignal(str)
    group_delete_requested = pyqtSignal(str)
    group_copy_page_requested = pyqtSignal(str, str)

    MAX_DEPTH = 64

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page_tree_panel")
        self._entries: Dict[str, PageTreeEntry] = {}
        self._children: Dict[Optional[str], List[str]] = {}
        self._parent_of_id: Dict[str, Optional[str]] = {}
        self._items: Dict[str, QTreeWidgetItem] = {}
        self._groups: List[PageGroupEntry] = []
        self._group_items: Dict[str, QTreeWidgetItem] = {}
        self._group_headers: Dict[str, QTreeWidgetItem] = {}
        self._expanded_group_ids: set = set()
        self._clipboard_page_id: Optional[str] = None
        self._expanded_ids: set = set()
        self._known_page_ids: set = set()
        self._current_page_id: Optional[str] = None
        self._synced_current_page_id: Optional[str] = None
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

        self.tree = PageTreeView(self)
        self.tree.page_activated.connect(self.page_activated)
        self.tree.rename_requested.connect(self.begin_rename)
        self.tree.delete_requested.connect(self.delete_requested)
        self.tree.pages_delete_requested.connect(self.pages_delete_requested)
        self.tree.reorder_requested.connect(self.reorder_requested)
        self.tree.copy_requested.connect(self._copy_page)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemCollapsed.connect(self._on_item_collapsed)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self.tree, 1)

        self.empty_label = QLabel("没有匹配的页面", self)
        self.empty_label.setObjectName("page_tree_empty")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.hide()
        root.addWidget(self.empty_label)

        group_header = QWidget(self)
        group_header.setObjectName("page_group_header")
        header_layout = QHBoxLayout(group_header)
        header_layout.setContentsMargins(2, 0, 0, 0)
        header_layout.setSpacing(4)
        group_label = QLabel("我的分组", group_header)
        group_label.setObjectName("page_group_label")
        header_layout.addWidget(group_label, 1)
        self.new_group_button = QToolButton(group_header)
        self.new_group_button.setObjectName("new_group_button")
        self.new_group_button.setText("+")
        self.new_group_button.setToolTip("新建分组")
        self.new_group_button.setCursor(Qt.PointingHandCursor)
        self.new_group_button.setFixedSize(24, 24)
        self.new_group_button.clicked.connect(self.group_create_requested)
        header_layout.addWidget(self.new_group_button)
        root.addWidget(group_header)

        self.group_tree = PageGroupView(self)
        self.group_tree.page_activated.connect(self.page_activated)
        self.group_tree.rename_requested.connect(self.begin_rename)
        self.group_tree.delete_requested.connect(self.delete_requested)
        self.group_tree.copy_requested.connect(self._copy_page)
        self.group_tree.paste_requested.connect(self._paste_into_group)
        self.group_tree.group_rename_requested.connect(self.group_rename_requested)
        self.group_tree.group_delete_requested.connect(self.group_delete_requested)
        self.group_tree.page_drop_requested.connect(self.group_copy_page_requested)
        self.group_tree.customContextMenuRequested.connect(self._on_group_context_menu)
        self.group_tree.itemExpanded.connect(
            lambda item: self._expanded_group_ids.add(
                self.group_tree.item_group_id(item)
            )
        )
        self.group_tree.itemCollapsed.connect(
            lambda item: self._expanded_group_ids.discard(
                self.group_tree.item_group_id(item)
            )
        )
        root.addWidget(self.group_tree, 1)

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
            QTreeWidget#page_tree, QTreeWidget#page_group_tree {
                background-color: transparent;
                color: %(T2)s;
                border: none;
                outline: none;
                font-size: 12px;
            }
            QTreeWidget#page_tree::item, QTreeWidget#page_group_tree::item {
                height: 26px;
                padding: 1px 4px;
                border-radius: 6px;
            }
            QTreeWidget#page_tree::item:hover, QTreeWidget#page_group_tree::item:hover {
                background-color: %(BG3)s;
            }
            QTreeWidget#page_tree::item:selected, QTreeWidget#page_group_tree::item:selected {
                background-color: %(ACC_SOFT)s;
                color: %(T1)s;
            }
            QLabel#page_group_label {
                color: %(T2)s;
                font-size: 12px;
                font-weight: 600;
            }
            QToolButton#new_group_button {
                background-color: %(BG3)s;
                color: %(T2)s;
                border: none;
                border-radius: 5px;
                font-size: 16px;
            }
            QToolButton#new_group_button:hover {
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

    # ------------------------------------------------------------ 对外接口
    def set_pages(
        self,
        entries: Dict[str, PageTreeEntry],
        children_by_parent: Dict[Optional[str], Sequence[str]],
        current_page_id: Optional[str] = None,
        groups: Sequence[PageGroupEntry] = (),
    ):
        """重建来源树与分组树，保留展开、滚动、搜索与仍可见的选择。"""
        signature = self._make_signature(entries, children_by_parent, groups)
        if signature == self._signature:
            self._current_page_id = current_page_id
            self._sync_current_row()
            return

        self._remember_anchor()
        selected_ids = set(self.tree.selected_page_ids())
        focus_id = self.tree.item_page_id(self.tree.currentItem())
        preserve_selection = current_page_id == self._current_page_id
        self._entries = dict(entries)
        self._groups = list(groups or ())
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
        self._rebuild_groups()
        if preserve_selection:
            self.tree.clearSelection()
            focus_item = self._items.get(focus_id)
            if focus_item is not None and self.tree.item_is_displayed(focus_item):
                self.tree.setCurrentItem(focus_item, 0, QItemSelectionModel.NoUpdate)
            for page_id in selected_ids:
                item = self._items.get(page_id)
                if item is not None and self.tree.item_is_displayed(item):
                    item.setSelected(True)
        self._signature = signature
        self._sync_current_row()
        self._restore_anchor()

    def visible_page_ids(self) -> List[str]:
        """当前可见行顺序；搜索时只算可见行。"""
        return self.tree.visible_page_ids()

    def group_page_ids(self, group_id) -> List[str]:
        header = self._group_headers.get(group_id)
        if header is None:
            return []
        return [
            self.group_tree.item_page_id(header.child(index))
            for index in range(header.childCount())
        ]

    def set_current_page(self, page_id: Optional[str]):
        """高亮当前页；页面被搜索隐藏时先清空搜索再定位。"""
        self._current_page_id = page_id
        item = self._items.get(page_id) if page_id is not None else None
        if item is not None and item.isHidden():
            self.clear_search()
            self.reveal_page(page_id)
            return
        self._sync_current_row()

    def reveal_page(self, page_id: Optional[str]):
        """展开祖先并滚动到该页；页面被搜索隐藏时先清空搜索。"""
        if page_id is None:
            return
        group_item = self._group_items.get(page_id)
        if group_item is not None:
            parent = group_item.parent()
            if parent is not None:
                parent.setExpanded(True)
            self.group_tree.scrollToItem(group_item, QAbstractItemView.PositionAtCenter)
            self.group_tree.setCurrentItem(
                group_item, 0, QItemSelectionModel.ClearAndSelect
            )
            return
        item = self._items.get(page_id)
        if item is None:
            return
        if item.isHidden():
            self.clear_search()
        self._expand_ancestors(page_id)
        self.tree.scrollToItem(item, QAbstractItemView.PositionAtCenter)
        self.tree.setCurrentItem(item, 0, QItemSelectionModel.ClearAndSelect)

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

    # ------------------------------------------------------------ 导航状态
    def export_navigation_state(self) -> dict:
        """保存源级切换要带走的临时导航状态：搜索、展开、滚动与多选。

        栏宽不在这里：它属于全局布局偏好，跨数据源保持一致。
        """
        return {
            "search": str(self._search_text),
            "expanded": sorted(str(page_id) for page_id in self._expanded_ids),
            "scroll": int(self.tree.verticalScrollBar().value()),
            "selected": [str(page_id) for page_id in self.tree.selected_page_ids()],
            "expanded_groups": sorted(
                str(group_id) for group_id in self._expanded_group_ids if group_id
            ),
        }

    def restore_navigation_state(self, state) -> None:
        """恢复另一份数据源的搜索、展开与滚动位置。"""
        state = state or {}
        text = str(state.get("search") or "")
        self.blockSignals(True)
        try:
            if text != self._search_text:
                self.search_edit.setText(text)
                self._search_text = text
            self._expanded_ids = {
                str(page_id) for page_id in state.get("expanded") or () if page_id
            }
            if "expanded_groups" in state:
                self._expanded_group_ids = {
                    str(group_id) for group_id in state.get("expanded_groups") or () if group_id
                }
                for group_id, header in self._group_headers.items():
                    header.setExpanded(group_id in self._expanded_group_ids)
            self._apply_expansion()
            self._apply_filter()
        finally:
            self.blockSignals(False)

        selected = [str(page_id) for page_id in state.get("selected") or ()]
        if selected:
            self.tree.clearSelection()
            for page_id in selected:
                item = self._items.get(page_id)
                if item is not None and self.tree.item_is_displayed(item):
                    item.setSelected(True)

        scroll = int(state.get("scroll") or 0)
        if scroll > 0:
            bar = self.tree.verticalScrollBar()
            bar.setValue(min(scroll, bar.maximum()))

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
        item = self._items.get(entry.page_id) or self._group_items.get(entry.page_id)
        if item is None:
            return
        self._entries[entry.page_id] = entry
        item.setText(0, entry.title)
        item.setIcon(0, page_kind_icon(entry.page_kind))
        item.setToolTip(0, entry.tooltip())
        item.setData(0, Qt.UserRole + 1, entry.deletable)
        self._signature = self._make_signature(self._entries, self._children, self._groups)

    def _make_signature(self, entries, children_by_parent, groups=()):
        rows = tuple(
            (
                page_id,
                entry.title,
                entry.page_kind,
                entry.scope_label,
                entry.renameable,
                entry.deletable,
                entry.group_id,
            )
            for page_id, entry in sorted(entries.items())
        )
        links = tuple(
            (str(parent_id), tuple(str(pid) for pid in ids))
            for parent_id, ids in sorted(
                children_by_parent.items(), key=lambda pair: str(pair[0])
            )
        )
        group_rows = tuple((group.group_id, group.title) for group in groups)
        return rows, links, group_rows

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
        new_ids = [
            pid
            for pid, entry in self._entries.items()
            if pid not in self._known_page_ids and entry.group_id is None
        ]
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

    def _rebuild_groups(self):
        known_groups = set(self._group_headers)
        self._expanded_group_ids.update(
            group.group_id for group in self._groups if group.group_id not in known_groups
        )
        self.group_tree.blockSignals(True)
        try:
            self.group_tree.clear()
            self._group_headers = {}
            self._group_items = {}
            for group in self._groups:
                header = QTreeWidgetItem([group.title])
                header.setData(0, PageGroupView.GROUP_ROLE, group.group_id)
                header.setToolTip(0, "拖动页面到此处，或复制页面后在此分组中粘贴")
                header.setFlags(
                    Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsDropEnabled
                )
                self.group_tree.addTopLevelItem(header)
                self._group_headers[group.group_id] = header
                for entry in self._entries.values():
                    if entry.group_id != group.group_id:
                        continue
                    item = QTreeWidgetItem([entry.title])
                    item.setData(0, Qt.UserRole, entry.page_id)
                    item.setData(0, Qt.UserRole + 1, entry.deletable)
                    item.setIcon(0, page_kind_icon(entry.page_kind))
                    item.setToolTip(0, entry.tooltip())
                    item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                    header.addChild(item)
                    self._group_items[entry.page_id] = item
                header.setExpanded(group.group_id in self._expanded_group_ids)
        finally:
            self.group_tree.blockSignals(False)

    def _build_group(self, parent_id: Optional[str], depth: int, parent_item=None):
        if depth > self.MAX_DEPTH:  # 环状来源关系的兜底，避免无限递归
            return
        for page_id in self._ordered_children(parent_id):
            entry = self._entries.get(page_id)
            if entry is None or entry.group_id is not None:
                continue
            item = QTreeWidgetItem([entry.title])
            item.setData(0, Qt.UserRole, page_id)
            item.setData(0, Qt.UserRole + 1, entry.deletable)
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
        ordered = [
            pid
            for pid in self._children.get(parent_id, [])
            if pid in self._entries and self._entries[pid].group_id is None
        ]
        if parent_id is not None:
            return ordered
        # 分组副本不占原树位置；它们的普通后代提升到顶层，避免页面丢失。
        seen = set(ordered)
        for page_id, entry in self._entries.items():
            if page_id in seen or entry.group_id is not None:
                continue
            parent = self._parent_of_id.get(page_id)
            parent_entry = self._entries.get(parent) if parent is not None else None
            if parent is None or parent_entry is None or parent_entry.group_id is not None:
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
        self.tree.setCurrentItem(item, 0, QItemSelectionModel.ClearAndSelect)

    def _on_item_expanded(self, item):
        page_id = self.tree.item_page_id(item)
        if page_id is not None and not self._search_text:
            self._expanded_ids.add(page_id)

    def _on_item_collapsed(self, item):
        self.tree.prune_hidden_selection()
        page_id = self.tree.item_page_id(item)
        if page_id is not None and not self._search_text:
            self._expanded_ids.discard(page_id)

    def _sync_current_row(self):
        # 活动页面与批量选择是两份状态。刷新同一活动页时不覆盖用户的选择。
        group_item = self._group_items.get(self._current_page_id)
        if self._current_page_id == self._synced_current_page_id:
            if group_item is not None and self.group_tree.currentItem() is not group_item:
                self.group_tree.setCurrentItem(
                    group_item, 0, QItemSelectionModel.ClearAndSelect
                )
            return
        self._synced_current_page_id = self._current_page_id
        item = self._items.get(self._current_page_id) if self._current_page_id else None
        if group_item is not None:
            self.tree.setCurrentItem(None)
            self.tree.clearSelection()
            self.group_tree.setCurrentItem(
                group_item, 0, QItemSelectionModel.ClearAndSelect
            )
            return
        self.group_tree.setCurrentItem(None)
        self.group_tree.clearSelection()
        if item is None:
            self.tree.setCurrentItem(None)
            self.tree.clearSelection()
            return
        if self.tree.item_is_displayed(item):
            self.tree.setCurrentItem(item, 0, QItemSelectionModel.ClearAndSelect)
        else:
            self.tree.setCurrentItem(item, 0, QItemSelectionModel.NoUpdate)

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
        source_ids = set(self._items)
        if not query:
            hidden = set()
        else:
            matches = {
                page_id
                for page_id in source_ids
                if query in str(self._entries[page_id].title).lower()
            }
            hidden = source_ids - matches
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

        self.empty_label.setVisible(bool(query) and hidden == source_ids)
        if query:
            self._sync_current_row()
        else:
            # 退出搜索：回到搜索前记住的展开状态与滚动位置。
            self._apply_expansion()
            self._restore_anchor()
            self._sync_current_row()
        self.tree.prune_hidden_selection()

    # ------------------------------------------------------------ 改名
    def begin_rename(self, page_id) -> bool:
        entry = self._entries.get(page_id)
        item = self._items.get(page_id)
        view = self.tree
        if item is None:
            item = self._group_items.get(page_id)
            view = self.group_tree
        if entry is None or item is None or not entry.renameable:
            return False
        self._close_editor()
        if item.isHidden():
            self.reveal_page(page_id)
        view.scrollToItem(item, QAbstractItemView.EnsureVisible)
        rect = view.visualItemRect(item)
        if not rect.isValid():
            return False

        editor = InlineRenameEditor(view.viewport(), entry.title)
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

    # ------------------------------------------------------------ 复制与粘贴
    def _copy_page(self, page_id):
        if page_id in self._entries:
            self._clipboard_page_id = str(page_id)

    def _paste_into_group(self, group_id):
        if self._clipboard_page_id not in self._entries:
            return
        if group_id not in self._group_headers:
            return
        self.group_copy_page_requested.emit(self._clipboard_page_id, str(group_id))

    # ------------------------------------------------------------ 右键菜单
    def _on_context_menu(self, pos):
        item = self.tree.row_at(pos)
        page_id = self.tree.item_page_id(item)
        entry = self._entries.get(page_id) if page_id else None
        if entry is None:
            return
        if item.isSelected():
            self.tree.setCurrentItem(item, 0, QItemSelectionModel.NoUpdate)
        else:
            self.tree.setCurrentItem(item, 0, QItemSelectionModel.ClearAndSelect)

        selected_ids = self.tree.selected_page_ids()
        deletable_ids = self.tree.selected_page_ids(deletable_only=True)

        menu = QMenu(self)
        rename_action = menu.addAction("重命名")
        rename_action.setEnabled(entry.renameable and len(selected_ids) == 1)
        rename_action.triggered.connect(lambda: self.begin_rename(page_id))

        restore_action = menu.addAction("恢复自动名称")
        restore_action.setEnabled(entry.renameable and len(selected_ids) == 1)
        restore_action.triggered.connect(lambda: self.restore_name_requested.emit(page_id))

        copy_action = menu.addAction("复制页面")
        copy_action.setEnabled(len(selected_ids) == 1)
        copy_action.triggered.connect(lambda: self._copy_page(page_id))

        menu.addSeparator()
        delete_label = (
            f"删除选中的 {len(deletable_ids)} 个页面" if len(selected_ids) > 1 else "删除页面"
        )
        delete_action = menu.addAction(delete_label)
        delete_action.setEnabled(bool(deletable_ids))
        delete_action.triggered.connect(self.tree.request_delete_selected)

        menu.setStyleSheet(theme.context_menu_qss())
        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def _on_group_context_menu(self, pos):
        item = self.group_tree.itemAt(pos)
        page_id = self.group_tree.item_page_id(item)
        group_id = self.group_tree.item_group_id(item)
        menu = QMenu(self)
        if item is None:
            menu.addAction("新建分组").triggered.connect(self.group_create_requested)
        elif page_id is None:
            paste_action = menu.addAction("粘贴页面到此分组")
            paste_action.setEnabled(self._clipboard_page_id in self._entries)
            paste_action.triggered.connect(lambda: self._paste_into_group(group_id))
            menu.addSeparator()
            menu.addAction("重命名分组").triggered.connect(
                lambda: self.group_rename_requested.emit(group_id)
            )
            menu.addAction("删除分组").triggered.connect(
                lambda: self.group_delete_requested.emit(group_id)
            )
        else:
            entry = self._entries.get(page_id)
            menu.addAction("复制页面").triggered.connect(lambda: self._copy_page(page_id))
            rename_action = menu.addAction("重命名")
            rename_action.setEnabled(bool(entry and entry.renameable))
            rename_action.triggered.connect(lambda: self.begin_rename(page_id))
            restore_action = menu.addAction("恢复自动名称")
            restore_action.setEnabled(bool(entry and entry.renameable))
            restore_action.triggered.connect(lambda: self.restore_name_requested.emit(page_id))
            menu.addSeparator()
            delete_action = menu.addAction("删除页面")
            delete_action.setEnabled(bool(entry and entry.deletable))
            delete_action.triggered.connect(lambda: self.delete_requested.emit(page_id))
        menu.setStyleSheet(theme.context_menu_qss())
        menu.exec_(self.group_tree.viewport().mapToGlobal(pos))
