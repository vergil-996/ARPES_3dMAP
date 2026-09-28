# -*- coding: utf-8 -*-
"""分析工作区：左侧常驻页面树 + 右侧画布，页面记录由本模块统一持有。

页面身份一律是 ``page_id``（随机 UUID，主页固定为 ``home``）；来源关系走
``AnalysisPageSpec.source_page_id``，树的父子结构由它推导，排序由 ``_tab_order``
（会话内也跨重启保存，见 ``_save_tab_order``）决定。

改名、删除、排序都先在本模块落地，再通过信号通知宿主：
``page_closed`` 逐页发出（先子后父），删除的确认文案由宿主提供的处理器决定。
"""
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from PyQt5.QtCore import QSettings, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import bandscope.ui.theme as theme
from bandscope.ui.page_tree import PageTreeEntry, PageTreePanel

#: 页面树初始宽度、最小宽度，以及画布至少保留的宽度。
NAV_INITIAL_WIDTH = 240
NAV_MIN_WIDTH = 180
CANVAS_MIN_WIDTH = 320


def unique_title_among(existing_titles, base_title) -> str:
    """同名时追加 ``_2``、``_3``…；调用方负责把当前页自己排除在外。"""
    base_title = str(base_title)
    existing = {str(title) for title in existing_titles}
    if base_title not in existing:
        return base_title
    suffix = 2
    while f"{base_title}_{suffix}" in existing:
        suffix += 1
    return f"{base_title}_{suffix}"


class PageRenameDialog(QDialog):
    """页面改名输入框；空名称不允许提交。"""

    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle("重命名页面")
        self.setModal(True)
        self.setMinimumWidth(380)
        self.setStyleSheet(f"QDialog {{ background-color: {theme.BG_2}; }}")
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        label = QLabel("页面名称：", self)
        label.setStyleSheet(theme.field_label_qss())
        root.addWidget(label)

        self.edit = QLineEdit(self)
        self.edit.setFixedHeight(32)
        self.edit.setStyleSheet(theme.value_input_qss())
        self.edit.setText(str(title or ""))
        self.edit.setPlaceholderText("输入新的页面名称")
        self.edit.selectAll()
        root.addWidget(self.edit)

        self.error_label = QLabel(self)
        self.error_label.setStyleSheet(f"color: {theme.DANGER}; background: transparent;")
        self.error_label.hide()
        root.addWidget(self.error_label)

        row = QHBoxLayout()
        row.addStretch()
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self
        )
        self.buttons.button(QDialogButtonBox.Ok).setText("确定")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        for button in self.buttons.buttons():
            button.setFixedHeight(32)
            accept_role = self.buttons.buttonRole(button) == QDialogButtonBox.AcceptRole
            theme.style_push_button(button, "primary" if accept_role else "secondary")
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        self.edit.returnPressed.connect(self._on_accept)
        row.addWidget(self.buttons)
        root.addLayout(row)

    def _on_accept(self):
        if not self.edit.text().strip():
            self.error_label.setText("名称不能为空。")
            self.error_label.show()
            return
        self.accept()

    def page_title(self) -> str:
        return self.edit.text().strip()


class PageTitleLabel(QLabel):
    """工作区顶部的页面名称：双击改名，右键给出改名与恢复自动名称。"""

    rename_requested = pyqtSignal()
    restore_requested = pyqtSignal()

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.renameable = False
        self.can_restore = False

    def set_rename_state(self, renameable: bool, can_restore: bool):
        self.renameable = bool(renameable)
        self.can_restore = bool(can_restore)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.renameable:
            self.rename_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        if not self.renameable:
            super().contextMenuEvent(event)
            return
        menu = QMenu(self)
        rename_action = menu.addAction("重命名…")
        rename_action.triggered.connect(self.rename_requested.emit)
        restore_action = menu.addAction("恢复自动名称")
        restore_action.setEnabled(self.can_restore)
        restore_action.triggered.connect(self.restore_requested.emit)
        menu.exec_(event.globalPos())
        event.accept()


@dataclass
class AnalysisPageSpec:
    page_id: str
    title: str
    page_kind: str
    source_module: str
    params: Dict[str, Any] = field(default_factory=dict)
    activation_seq: int = 0
    closeable: bool = True
    source_page_id: Optional[str] = None
    source_title: Optional[str] = None
    data_scope_id: str = "full"
    #: 自动命名算出的底层默认名；``None`` 表示与 ``title`` 相同。
    auto_title: Optional[str] = None
    #: 用户是否改过名。改过名后自动命名只更新 ``auto_title``，不动显示名。
    title_overridden: bool = False


class ResultWorkspace(QWidget):
    page_activated = pyqtSignal(str)
    page_closed = pyqtSignal(str)
    #: 页面标题等展示信息更新后发出（改名、改名撤销、自动命名刷新）。
    page_updated = pyqtSignal(str)
    #: 用户要求改名 / 恢复自动名称；应用层负责弹窗、去重与落地。
    page_rename_requested = pyqtSignal(str)
    page_restore_name_requested = pyqtSignal(str)

    def __init__(self, display_widget: QWidget, parent=None):
        super().__init__(parent)
        self.display_widget = display_widget
        self.home_page_id: Optional[str] = None
        self.current_page_id: Optional[str] = None
        self.activation_counter = 0
        self.page_specs: Dict[str, AnalysisPageSpec] = {}
        self.title_to_page_id: Dict[str, str] = {}
        self.activation_history = []
        self.children_by_parent: Dict[Optional[str], list] = {}
        #: 删除确认处理器：``handler(plan) -> bool``，返回 True 才真正删除。
        self._delete_confirm_handler: Optional[Callable[[dict], bool]] = None

        self.settings = QSettings("ARPES", "ARPES_3dMAP")
        raw_order = self.settings.value("result_workspace/tab_order", "", type=str)
        try:
            self._tab_order = json.loads(raw_order) if raw_order else []
        except (TypeError, ValueError, json.JSONDecodeError):
            self._tab_order = []

        self.setObjectName("result_workspace")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.content_frame = QFrame(self)
        self.content_frame.setObjectName("result_content_frame")
        self.content_layout = QVBoxLayout(self.content_frame)
        self.content_layout.setContentsMargins(14, 14, 14, 14)
        self.content_layout.setSpacing(12)

        self.header = QFrame(self.content_frame)
        self.header.setObjectName("result_header")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        self.header.setFixedHeight(30)

        self.page_title = PageTitleLabel("分析工作区", self.header)
        self.page_title.setMinimumWidth(0)
        self.page_title.setStyleSheet(theme.card_title_qss())
        self.page_title.setToolTip("双击重命名页面")
        self.page_title.rename_requested.connect(self._on_rename_requested)
        self.page_title.restore_requested.connect(self._on_restore_requested)
        header_layout.addWidget(self.page_title, 1)

        self.scope_label = QLabel(self.header)
        self.scope_label.setStyleSheet(
            f"color: {theme.TEXT_2}; background: {theme.BG_3};"
            f"border-radius: 5px; padding: 3px 8px; font-size: 11px;"
        )
        header_layout.addWidget(self.scope_label)

        self.close_button = QToolButton(self.header)
        self.close_button.setText("×")
        self.close_button.setToolTip("删除当前页面 (Ctrl+W)")
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setFixedSize(28, 28)
        self.close_button.clicked.connect(self.close_current_page)
        header_layout.addWidget(self.close_button)

        self.content_layout.addWidget(self.header)
        self.content_layout.addWidget(self.display_widget, stretch=1)

        self.page_tree = PageTreePanel(self)
        self.page_tree.page_activated.connect(self._on_tree_page_activated)
        self.page_tree.rename_committed.connect(self.apply_page_rename)
        self.page_tree.restore_name_requested.connect(self.page_restore_name_requested)
        self.page_tree.delete_requested.connect(self.delete_page)
        self.page_tree.reorder_requested.connect(self.move_page_within_siblings)

        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setObjectName("result_splitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        self.page_tree.setMinimumWidth(NAV_MIN_WIDTH)
        self.splitter.addWidget(self.page_tree)
        self.content_frame.setMinimumWidth(CANVAS_MIN_WIDTH)
        self.splitter.addWidget(self.content_frame)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([self._load_nav_width(), 1000])
        root.addWidget(self.splitter)

        # 拖动分隔条时不要每个像素都写一次设置。
        self._nav_width_timer = QTimer(self)
        self._nav_width_timer.setSingleShot(True)
        self._nav_width_timer.setInterval(400)
        self._nav_width_timer.timeout.connect(self.save_nav_width)
        self.splitter.splitterMoved.connect(lambda *_: self._nav_width_timer.start())

        self.setStyleSheet(
            """
            QWidget#result_workspace {
                background-color: %(BG1)s;
                border-radius: 12px;
            }
            QFrame#result_content_frame {
                background-color: %(BG1)s;
                border-top-right-radius: 12px;
                border-bottom-right-radius: 12px;
            }
            QFrame#result_header {
                background-color: transparent;
                border: none;
            }
            QToolButton {
                background-color: %(BG3)s;
                color: %(T2)s;
                border: none;
                border-radius: 6px;
                font-weight: 700;
            }
            QToolButton:hover {
                background-color: %(DANGER_SOFT)s;
                color: %(DANGER)s;
            }
            QSplitter#result_splitter::handle {
                background-color: %(BORD)s;
            }
            QSplitter#result_splitter::handle:hover {
                background-color: %(ACC_DIM)s;
            }
            """ % theme.QSS_TOKENS
        )

    # ------------------------------------------------------------------
    # 栏宽
    # ------------------------------------------------------------------
    def _load_nav_width(self) -> int:
        stored = self.settings.value("result_workspace/nav_width", 0)
        try:
            width = int(stored)
        except (TypeError, ValueError):
            width = 0
        return width if width >= NAV_MIN_WIDTH else NAV_INITIAL_WIDTH

    def save_nav_width(self):
        try:
            self.settings.setValue("result_workspace/nav_width", int(self.nav_width()))
        except Exception:
            pass

    def nav_width(self) -> int:
        sizes = self.splitter.sizes()
        return int(sizes[0]) if sizes else NAV_INITIAL_WIDTH

    def set_nav_width(self, width: int):
        """设置页面树宽度（受最小宽度与画布最小宽度约束）。"""
        total = sum(self.splitter.sizes())
        target = max(NAV_MIN_WIDTH, int(width))
        target = min(target, max(NAV_MIN_WIDTH, total - CANVAS_MIN_WIDTH))
        self.splitter.setSizes([target, max(1, total - target)])

    # ------------------------------------------------------------------
    # 页面登记
    # ------------------------------------------------------------------
    def set_footer_widget(self, widget: QWidget):
        """在画布正下方挂一个常驻底部条（如时间轴横条），随所有显示页共现。"""
        self.footer_widget = widget
        self.content_layout.addWidget(widget)

    def set_delete_confirm_handler(self, handler: Optional[Callable[[dict], bool]]):
        """宿主提供的删除确认：``handler(plan) -> bool``。未设置时直接删除。"""
        self._delete_confirm_handler = handler

    def set_home_page(self, spec: AnalysisPageSpec):
        spec.closeable = False
        self.home_page_id = spec.page_id
        self._add_page(spec)
        self.activate_page(spec.page_id)

    def add_pinned_page(self, spec: AnalysisPageSpec, activate: bool = False) -> AnalysisPageSpec:
        spec.closeable = False
        if spec.page_id in self.page_specs:
            self.page_specs[spec.page_id].closeable = False
            if activate:
                self.activate_page(spec.page_id)
            return self.page_specs[spec.page_id]

        self._add_page(spec)
        if activate:
            self.activate_page(spec.page_id)
        return spec

    def ensure_page(self, spec: AnalysisPageSpec) -> AnalysisPageSpec:
        existing_id = self.title_to_page_id.get(spec.title)
        if existing_id is not None:
            self.activate_page(existing_id)
            return self.page_specs[existing_id]

        self._add_page(spec)
        self.activate_page(spec.page_id)
        return spec

    def add_page(self, spec: AnalysisPageSpec) -> AnalysisPageSpec:
        self._add_page(spec)
        self.activate_page(spec.page_id)
        return spec

    def _add_page(self, spec: AnalysisPageSpec):
        if spec.auto_title is None:
            # 建页时的标题就是该页的自动名字；此后自动命名只更新它。
            spec.auto_title = str(spec.title)
        self.page_specs[spec.page_id] = spec
        self._ensure_source_title(spec)
        self._add_to_tab_order(spec.page_id)
        self._rebuild_title_index()
        self._refresh_navigation_layout()
        if spec.page_id != self.home_page_id:
            # 新页面自动展开祖先并定位；被搜索隐藏时先清空搜索。
            self.page_tree.reveal_page(spec.page_id)

    def _rebuild_title_index(self):
        self.title_to_page_id = {}
        for page_id, spec in self.page_specs.items():
            self.title_to_page_id[spec.title] = page_id

    def _parent_page_id(self, spec):
        source_id = spec.source_page_id
        if source_id and source_id != spec.page_id and source_id in self.page_specs:
            return source_id
        return None

    def _parent_page_id_for_id(self, page_id):
        spec = self.page_specs.get(page_id)
        if spec is None:
            return None
        return self._parent_page_id(spec)

    def _ensure_source_title(self, spec):
        if spec.source_title:
            return
        source_id = spec.source_page_id
        if source_id and source_id in self.page_specs:
            spec.source_title = self.page_specs[source_id].title

    def _is_top_level_page(self, page_id):
        parent_id = self._parent_page_id_for_id(page_id)
        if parent_id is None:
            return True
        return parent_id == self.home_page_id

    # ------------------------------------------------------------------
    # 顺序
    # ------------------------------------------------------------------
    def _ordered_page_ids(self) -> List[str]:
        """主页在最前，其余按 ``_tab_order`` 排序；未登记的页面排在最后。"""
        ordered: List[str] = []
        seen = set()
        for page_id in [self.home_page_id] + list(self._tab_order) + list(self.page_specs):
            if page_id is None or page_id in seen or page_id not in self.page_specs:
                continue
            seen.add(page_id)
            ordered.append(page_id)
        return ordered

    def _rebuild_children_map(self):
        """按 ``_tab_order`` 的顺序列出各组同级页面（真正的显示顺序）。"""
        self.children_by_parent = {}
        for page_id in self._ordered_page_ids():
            parent_id = self._parent_page_id(self.page_specs[page_id])
            self.children_by_parent.setdefault(parent_id, []).append(page_id)

    def _ordered_top_level_ids(self) -> List[str]:
        return [pid for pid in self._ordered_page_ids() if self._is_top_level_page(pid)]

    def move_page_within_siblings(self, dragged_id, reference_id, before=True) -> bool:
        """同级排序：只挪动 ``_tab_order`` 里的相对位置，其它同级组不受影响。"""
        if dragged_id == reference_id:
            return False
        if self.page_tree.search_text().strip():
            return False
        if dragged_id not in self.page_specs or reference_id not in self.page_specs:
            return False
        if dragged_id == self.home_page_id or reference_id == self.home_page_id:
            return False
        if self._parent_page_id_for_id(dragged_id) != self._parent_page_id_for_id(reference_id):
            return False

        order = [pid for pid in self._tab_order if pid in self.page_specs]
        if dragged_id not in order or reference_id not in order:
            return False
        order.remove(dragged_id)
        index = order.index(reference_id)
        order.insert(index if before else index + 1, dragged_id)
        for page_id in self.page_specs:
            if page_id != self.home_page_id and page_id not in order:
                order.append(page_id)
        self._tab_order = order
        self._save_tab_order()
        self._refresh_navigation_layout()
        return True

    def _save_tab_order(self):
        try:
            self.settings.setValue(
                "result_workspace/tab_order",
                json.dumps(self._tab_order, ensure_ascii=False),
            )
        except Exception:
            pass

    def _add_to_tab_order(self, page_id):
        if page_id == self.home_page_id:
            return
        if page_id not in self._tab_order:
            self._tab_order.append(page_id)
            self._save_tab_order()

    def _drop_from_tab_order(self, page_ids) -> bool:
        removed = set(page_ids)
        remaining = [pid for pid in self._tab_order if pid not in removed]
        if len(remaining) == len(self._tab_order):
            return False
        self._tab_order = remaining
        self._save_tab_order()
        return True

    # ------------------------------------------------------------------
    # 导航刷新
    # ------------------------------------------------------------------
    def _page_tree_entries(self) -> Dict[str, PageTreeEntry]:
        return {
            page_id: PageTreeEntry(
                page_id=page_id,
                title=str(spec.title),
                page_kind=str(spec.page_kind),
                scope_label=str(spec.params.get("data_scope_label") or ""),
                renameable=spec.page_kind != "control_panel",
                deletable=bool(spec.closeable),
            )
            for page_id, spec in self.page_specs.items()
        }

    def _refresh_navigation_layout(self):
        self._rebuild_children_map()
        self.page_tree.set_pages(
            self._page_tree_entries(), self.children_by_parent, self.current_page_id
        )

    def refresh_navigation(self):
        self._refresh_navigation_layout()
        self.page_tree.set_current_page(self.current_page_id)

    def refresh_page_row(self, page_id):
        """只刷新单页在树里的标题/提示，避免整树重建。"""
        spec = self.page_specs.get(page_id)
        if spec is None:
            self._refresh_navigation_layout()
            return
        self.page_tree.update_entry(self._page_tree_entries()[page_id])

    def visible_page_ids(self) -> List[str]:
        """树中当前可见行的顺序；数字切页以此为准。"""
        return self.page_tree.visible_page_ids()

    # ------------------------------------------------------------------
    # 激活与页眉
    # ------------------------------------------------------------------
    def _on_tree_page_activated(self, page_id):
        if page_id in self.page_specs:
            self.activate_page(page_id)

    def _scroll_active_page_into_view(self):
        self.page_tree.set_current_page(self.current_page_id)

    def activate_page(self, page_id: str):
        if page_id not in self.page_specs:
            return

        self.current_page_id = page_id
        self.activation_counter += 1

        spec = self.page_specs[page_id]
        spec.activation_seq = self.activation_counter

        self.activation_history = [pid for pid in self.activation_history if pid != page_id]
        self.activation_history.append(page_id)

        self._refresh_navigation_layout()
        self._scroll_active_page_into_view()
        self._refresh_header()
        self.page_activated.emit(page_id)

    def _refresh_header(self):
        spec = self.current_spec()
        if spec is None:
            self.page_title.setText("分析工作区")
            self.page_title.setToolTip("双击重命名页面")
            self.page_title.set_rename_state(False, False)
            self.scope_label.hide()
            self.close_button.hide()
            return
        title = str(spec.title)
        # 工具页（控制面板等）没有页面语义，不提供改名。
        renameable = spec.page_kind != "control_panel"
        self.page_title.setText(title)
        self.page_title.setToolTip(f"{title}（双击重命名）" if renameable else title)
        self.page_title.set_rename_state(renameable, bool(spec.title_overridden))
        scope = str(spec.params.get("data_scope_label") or "")
        self.scope_label.setText(scope)
        self.scope_label.setVisible(bool(scope))
        self.close_button.setVisible(spec.closeable)

    def _on_rename_requested(self):
        if self.current_page_id is not None:
            self.page_rename_requested.emit(self.current_page_id)

    def _on_restore_requested(self):
        if self.current_page_id is not None:
            self.page_restore_name_requested.emit(self.current_page_id)

    # ------------------------------------------------------------------
    # 改名
    # ------------------------------------------------------------------
    def unique_page_title(self, base_title, exclude_page_id=None) -> str:
        """同名时追加编号；排除页面自身，重复改名不会多出无谓后缀。"""
        return unique_title_among(
            (
                spec.title
                for page_id, spec in self.page_specs.items()
                if page_id != exclude_page_id
            ),
            base_title,
        )

    def apply_page_rename(self, page_id, title) -> Optional[str]:
        """落地树内改名：去首尾空白，空名称保持原名，重名自动编号。"""
        spec = self.page_specs.get(page_id)
        if spec is None or spec.page_kind == "control_panel":
            return None
        text = str(title or "").strip()
        if not text:
            return spec.title
        return self.set_page_title(
            page_id, self.unique_page_title(text, exclude_page_id=page_id)
        )

    def _refresh_page_surfaces(self, page_id, *, notify):
        """页面展示面统一刷新：页眉、页面树与当前来源名称。"""
        self._rebuild_title_index()
        self._refresh_navigation_layout()
        if page_id == self.current_page_id:
            self._refresh_header()
        if notify:
            self.page_updated.emit(page_id)

    def set_page_title(self, page_id, title, *, auto=False):
        """落地页面名称：``auto=True`` 表示这是自动名称（不是用户命名）。"""
        spec = self.page_specs.get(page_id)
        if spec is None:
            return None
        text = str(title)
        if text == spec.title and spec.title_overridden != auto:
            return spec.title
        spec.title = text
        spec.title_overridden = not auto
        self._refresh_page_surfaces(page_id, notify=True)
        return spec.title

    def update_page(self, page_id: str, *, title: Optional[str] = None, params: Optional[Dict[str, Any]] = None, source_page_id: Optional[str] = None):
        spec = self.page_specs.get(page_id)
        if spec is None:
            return

        if title is not None:
            spec.title = title

        if params is not None:
            spec.params = dict(params)

        if source_page_id is not None:
            spec.source_page_id = source_page_id

        self._ensure_source_title(spec)
        # 当前页的页眉（标题 / 范围徽标）也在这条路径上刷新：单靠 activate_page
        # 会留下过期标题。
        self._refresh_page_surfaces(page_id, notify=title is not None)

    # ------------------------------------------------------------------
    # 删除
    # ------------------------------------------------------------------
    def page_delete_plan(self, page_id) -> Optional[dict]:
        """删除预案：目标、完整删除集（先子后父）、数量、保护状态与回退页。"""
        spec = self.page_specs.get(page_id)
        if spec is None:
            return None

        ordered = self._order_for_removal(self._descendant_page_ids(page_id) + [page_id])
        blocked = None
        if not spec.closeable:
            blocked = "protected_root"
        else:
            for candidate_id in ordered:
                candidate = self.page_specs.get(candidate_id)
                if candidate is None or not candidate.closeable:
                    blocked = "protected_descendant"
                    break

        parent_id = self._parent_page_id_for_id(page_id)
        return {
            "page_id": page_id,
            "title": str(spec.title),
            "ids": ordered,
            "count": len(ordered),
            "blocked": blocked,
            "current_removed": self.current_page_id in set(ordered),
            "fallback_id": parent_id if parent_id in self.page_specs else self.home_page_id,
        }

    def delete_page(self, page_id, *, confirm=True) -> bool:
        """统一删除入口：父页连同全部后代一起删，逐页发既有关闭通知。"""
        plan = self.page_delete_plan(page_id)
        if plan is None or plan["blocked"]:
            if plan is not None and confirm:
                self._confirm_delete(plan)
            return False
        if confirm and not self._confirm_delete(plan):
            return False
        self._apply_delete(plan["ids"], plan["fallback_id"])
        return True

    def _confirm_delete(self, plan) -> bool:
        handler = self._delete_confirm_handler
        if handler is None:
            return True
        return bool(handler(plan))

    def _apply_delete(self, page_ids, fallback_id=None):
        """批量核心：先摘记录，再逐页通知，最后统一刷新并激活存活页面。"""
        removed = self._order_for_removal(
            [pid for pid in page_ids if pid in self.page_specs]
        )
        if not removed:
            return
        removed_set = set(removed)
        current_removed = self.current_page_id in removed_set

        self.activation_history = [
            pid for pid in self.activation_history if pid not in removed_set
        ]
        for page_id in removed:
            self.page_specs.pop(page_id, None)
        self._drop_from_tab_order(removed)
        self._rebuild_title_index()
        self._rebuild_children_map()

        for page_id in removed:
            self.page_closed.emit(page_id)

        self._refresh_navigation_layout()
        if not current_removed:
            self.page_tree.set_current_page(self.current_page_id)
            self._refresh_header()
            return

        self.current_page_id = None
        target_id = fallback_id if fallback_id in self.page_specs else self.home_page_id
        if target_id in self.page_specs:
            self.activate_page(target_id)
        else:
            self.page_tree.set_current_page(None)
            self._refresh_header()

    def _descendant_page_ids(self, page_id: str) -> List[str]:
        """全部后代：不依赖展开状态，被搜索隐藏的页面也算。"""
        self._rebuild_children_map()
        result: List[str] = []
        visited = set()
        stack = list(self.children_by_parent.get(page_id, []))
        while stack:
            child_id = stack.pop()
            if child_id in visited:
                continue
            visited.add(child_id)
            result.append(child_id)
            stack.extend(self.children_by_parent.get(child_id, []))
        return result

    def _depth_of(self, page_id: str) -> int:
        cursor, steps, seen = page_id, 0, set()
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            cursor = self._parent_page_id_for_id(cursor)
            steps += 1
        return steps

    def _order_for_removal(self, page_ids) -> List[str]:
        """清理顺序：深的后代先走，父页最后；同一层的保持传入顺序。"""
        return sorted(page_ids, key=self._depth_of, reverse=True)

    def close_current_page(self):
        if self.current_page_id is not None:
            self.delete_page(self.current_page_id)

    def reset_to_home(self):
        """换数据时的内部复位：不弹删除确认，直接清到只剩主页。"""
        home_id = self.home_page_id
        removable = [pid for pid in self._ordered_page_ids() if pid != home_id]
        self._apply_delete(removable, home_id)

        if home_id is not None and home_id in self.page_specs:
            self.activate_page(home_id)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def current_spec(self) -> Optional[AnalysisPageSpec]:
        if self.current_page_id is None:
            return None
        return self.page_specs.get(self.current_page_id)

    def page_by_id(self, page_id: Optional[str]) -> Optional[AnalysisPageSpec]:
        if page_id is None:
            return None
        return self.page_specs.get(page_id)

    def home_spec(self) -> Optional[AnalysisPageSpec]:
        return self.page_by_id(self.home_page_id)
