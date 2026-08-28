from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from PyQt5.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QCursor, QFont
from PyQt5.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from siui.core import SiColor, SiGlobal
from siui.templates.application.components.page_view.page_view import PageButton


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


class ResultPageButton(PageButton):
    activated_with_id = pyqtSignal(str)
    clicked_with_id = pyqtSignal(str)
    hovered_with_id = pyqtSignal(str)
    unhovered_with_id = pyqtSignal(str)

    ICON_MAP = {
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

    def __init__(self, spec: AnalysisPageSpec, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.resize(40, 40)
        self.refresh_hint()
        self.attachment().setSvgSize(20, 20)
        self.attachment().load(SiGlobal.siui.iconpack.get(self.ICON_MAP.get(spec.page_kind, "ic_fluent_document_data_filled")))
        self.colorGroup().assign(SiColor.BUTTON_OFF, "#00FFFFFF")
        self.colorGroup().assign(SiColor.BUTTON_ON, "#12FFFFFF")
        self.reloadStyleSheet()
        self.activated.connect(self._emit_page_activated)

    def _emit_page_activated(self):
        self.activated_with_id.emit(self.spec.page_id)

    def _on_clicked(self):
        super()._on_clicked()
        self.clicked_with_id.emit(self.spec.page_id)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.hovered_with_id.emit(self.spec.page_id)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.unhovered_with_id.emit(self.spec.page_id)

    def refresh_hint(self):
        scope_label = self.spec.params.get(
            "data_scope_label",
            "完整数据" if self.spec.data_scope_id == "full" else self.spec.data_scope_id,
        )
        title = str(self.spec.title)
        self.setHint(title if scope_label in title else f"{title} · {scope_label}")

    def set_active(self, active: bool):
        self.setChecked(active)
        self.active_indicator.setOpacityTo(1 if active else 0)


class ResultTreePopup(QFrame):
    page_activated = pyqtSignal(str)
    pin_toggled = pyqtSignal(bool)
    close_requested = pyqtSignal()
    mouse_entered = pyqtSignal()
    mouse_left = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("result_tree_popup")
        self.setFixedWidth(292)
        self.setMinimumHeight(160)
        self.setMaximumHeight(480)
        self._pinned = False
        self._root_page_id = None

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 170))
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)

        self.accent = QLabel()
        self.accent.setObjectName("popup_accent")
        self.accent.setFixedSize(4, 18)
        header.addWidget(self.accent)

        self.title_label = QLabel("结果页导航")
        self.title_label.setObjectName("popup_title")
        header.addWidget(self.title_label, 1)

        self.pin_button = QToolButton()
        self.pin_button.setObjectName("popup_pin_button")
        self.pin_button.setText("固定")
        self.pin_button.setCheckable(True)
        self.pin_button.setCursor(Qt.PointingHandCursor)
        self.pin_button.setFixedHeight(26)
        self.pin_button.setMinimumWidth(48)
        self.pin_button.toggled.connect(self._on_pin_toggled)
        header.addWidget(self.pin_button)

        self.close_button = QToolButton()
        self.close_button.setObjectName("popup_close_button")
        self.close_button.setText("×")
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setFixedSize(26, 26)
        self.close_button.clicked.connect(lambda: self.close_requested.emit())
        header.addWidget(self.close_button)

        layout.addLayout(header)

        self.tree = QTreeWidget()
        self.tree.setObjectName("popup_tree")
        self.tree.setHeaderHidden(True)
        self.tree.setFrameShape(QFrame.NoFrame)
        self.tree.setAnimated(True)
        self.tree.setIndentation(18)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree, 1)

        self.hint_label = QLabel("单击页面切换  单击箭头展开/收起")
        self.hint_label.setObjectName("popup_hint")
        layout.addWidget(self.hint_label)

        self.setStyleSheet(
            """
            QFrame#result_tree_popup {
                background-color: #232336;
                border: 1px solid #3A3A58;
                border-radius: 12px;
            }
            QLabel#popup_accent {
                background-color: #5B8DEF;
                border-radius: 2px;
            }
            QLabel#popup_title {
                color: #F0F0F8;
                font-weight: 700;
                font-size: 13px;
            }
            QLabel#popup_hint {
                color: #6E6E8E;
                font-size: 10px;
            }
            QTreeWidget#popup_tree {
                background-color: transparent;
                color: #D8D8E8;
                border: none;
                outline: none;
                font-size: 12px;
            }
            QTreeWidget#popup_tree::item {
                height: 30px;
                padding: 4px 6px;
                margin: 1px 0;
                border-radius: 6px;
            }
            QTreeWidget#popup_tree::item:hover {
                background-color: #2E2E48;
            }
            QTreeWidget#popup_tree::item:selected {
                background-color: #3B3B62;
                color: #FFFFFF;
            }
            QTreeWidget#popup_tree QScrollBar:vertical {
                width: 8px;
                background: transparent;
                margin: 0;
            }
            QTreeWidget#popup_tree QScrollBar::handle:vertical {
                background-color: #3A3A55;
                border-radius: 4px;
                min-height: 24px;
            }
            QTreeWidget#popup_tree QScrollBar::handle:vertical:hover {
                background-color: #4A4A70;
            }
            QTreeWidget#popup_tree QScrollBar::add-line:vertical,
            QTreeWidget#popup_tree QScrollBar::sub-line:vertical {
                height: 0;
            }
            QTreeWidget#popup_tree QScrollBar::add-page:vertical,
            QTreeWidget#popup_tree QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QToolButton#popup_pin_button {
                background-color: #2B2B45;
                color: #D0D0E0;
                border: 1px solid #3A3A55;
                border-radius: 7px;
                font-size: 11px;
                padding: 0 8px;
            }
            QToolButton#popup_pin_button:hover {
                background-color: #353558;
            }
            QToolButton#popup_pin_button:checked {
                background-color: #4A4A75;
                color: #FFFFFF;
                border-color: #5B5B8A;
            }
            QToolButton#popup_close_button {
                background-color: transparent;
                color: #A0A0C0;
                border: none;
                border-radius: 7px;
                font-size: 12px;
                font-weight: 700;
            }
            QToolButton#popup_close_button:hover {
                background-color: #3A3A55;
                color: #FFFFFF;
            }
            """
        )

    def is_pinned(self):
        return self._pinned

    def root_page_id(self):
        return self._root_page_id

    def set_pinned(self, pinned: bool):
        self._pinned = bool(pinned)
        self.pin_button.blockSignals(True)
        self.pin_button.setChecked(self._pinned)
        self.pin_button.setText("取消固定" if self._pinned else "固定")
        self.pin_button.blockSignals(False)

    def _on_pin_toggled(self, checked):
        self._pinned = bool(checked)
        self.pin_button.setText("取消固定" if self._pinned else "固定")
        self.pin_toggled.emit(self._pinned)

    def _on_item_clicked(self, item, column):
        page_id = item.data(0, Qt.UserRole)
        if page_id:
            self.page_activated.emit(page_id)

    def set_page_tree(self, root_page_id, page_specs, children_by_parent):
        self._root_page_id = root_page_id
        self.tree.clear()
        visited = set()

        def add_nodes(parent_item, page_id):
            if page_id in visited:
                return
            visited.add(page_id)
            spec = page_specs.get(page_id)
            if spec is None:
                return
            item = QTreeWidgetItem([str(spec.title)])
            item.setData(0, Qt.UserRole, page_id)
            if parent_item is None:
                self.tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            for child_id in children_by_parent.get(page_id, []):
                add_nodes(item, child_id)

        add_nodes(None, root_page_id)
        self.tree.expandAll()
        if root_page_id in page_specs:
            self.title_label.setText(f"来自：{page_specs[root_page_id].title}")
        else:
            self.title_label.setText("结果页导航")

    def set_current_page(self, page_id):
        def walk(item):
            item_page_id = item.data(0, Qt.UserRole)
            if item_page_id == page_id:
                font = QFont()
                font.setBold(True)
                item.setFont(0, font)
                item.setForeground(0, QBrush(QColor("#FFFFFF")))
            else:
                font = QFont()
                font.setBold(False)
                item.setFont(0, font)
                item.setForeground(0, QBrush(QColor("#D0D0E0")))
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def enterEvent(self, event):
        self.mouse_entered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.mouse_left.emit()
        super().leaveEvent(event)

class ResultWorkspace(QWidget):
    page_activated = pyqtSignal(str)
    page_closed = pyqtSignal(str)

    def __init__(self, display_widget: QWidget, parent=None):
        super().__init__(parent)
        self.display_widget = display_widget
        self.home_page_id: Optional[str] = None
        self.current_page_id: Optional[str] = None
        self.activation_counter = 0
        self.page_specs: Dict[str, AnalysisPageSpec] = {}
        self.title_to_page_id: Dict[str, str] = {}
        self.page_buttons: Dict[str, ResultPageButton] = {}
        self.activation_history = []
        self.children_by_parent: Dict[Optional[str], list] = {}
        self.hover_page_id: Optional[str] = None
        self.popup_page_id: Optional[str] = None

        self.setObjectName("result_workspace")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = QFrame(self)
        self.sidebar.setObjectName("result_sidebar")
        self.sidebar.setFixedWidth(56)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(8, 12, 8, 12)
        sidebar_layout.setSpacing(0)

        self.nav_scroll = QScrollArea(self.sidebar)
        self.nav_scroll.setWidgetResizable(True)
        self.nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav_scroll.setFrameShape(QFrame.NoFrame)

        self.nav_buttons = QWidget(self.nav_scroll)
        self.nav_buttons_layout = QVBoxLayout(self.nav_buttons)
        self.nav_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.nav_buttons_layout.setSpacing(8)
        self.nav_buttons_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.nav_scroll.setWidget(self.nav_buttons)


        sidebar_layout.addWidget(self.nav_scroll)

        self.content_frame = QFrame(self)
        self.content_frame.setObjectName("result_content_frame")
        content_layout = QVBoxLayout(self.content_frame)
        content_layout.setContentsMargins(14, 14, 14, 14)
        content_layout.setSpacing(12)

        self.header = QFrame(self.content_frame)
        self.header.setObjectName("result_header")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)

        header_layout.addStretch(1)

        self.close_button = QToolButton(self.header)
        self.close_button.setText("X")
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setFixedSize(28, 28)
        self.close_button.clicked.connect(self.close_current_page)
        header_layout.addWidget(self.close_button)

        content_layout.addWidget(self.header)
        content_layout.addWidget(self.display_widget, stretch=1)

        root.addWidget(self.sidebar)
        root.addWidget(self.content_frame, stretch=1)

        self.setStyleSheet(
            """
            QWidget#result_workspace {
                background-color: #1A1A2E;
                border-radius: 12px;
            }
            QFrame#result_sidebar {
                background-color: #171728;
                border-top-left-radius: 12px;
                border-bottom-left-radius: 12px;
                border-right: 1px solid #2A2A3A;
            }
            QFrame#result_content_frame {
                background-color: #1A1A2E;
                border-top-right-radius: 12px;
                border-bottom-right-radius: 12px;
            }
            QFrame#result_header {
                background-color: transparent;
                border: none;
            }
            QToolButton {
                background-color: #E81123;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                font-weight: 700;
            }
            QToolButton:hover {
                background-color: #F33A4A;
            }
            QToolButton#popup_pin_button {
                background-color: #2B2B45;
                color: #D0D0E0;
                border: 1px solid #3A3A55;
                border-radius: 6px;
                font-size: 11px;
            }
            QToolButton#popup_pin_button:hover {
                background-color: #3A3A55;
            }
            QToolButton#popup_pin_button:checked {
                background-color: #4A4A70;
                color: #FFFFFF;
            }
            QScrollArea {
                background: transparent;
            }
            """
        )

        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.setInterval(200)
        self.hover_timer.timeout.connect(self._on_hover_timer_timeout)

        self.close_timer = QTimer(self)
        self.close_timer.setSingleShot(True)
        self.close_timer.setInterval(280)
        self.close_timer.timeout.connect(self._on_close_timer_timeout)

        self.tree_popup = ResultTreePopup(self)
        self.tree_popup.page_activated.connect(self._on_tree_page_activated)
        self.tree_popup.pin_toggled.connect(self._on_popup_pin_toggled)
        self.tree_popup.close_requested.connect(self._on_popup_close_requested)
        self.tree_popup.mouse_entered.connect(self.close_timer.stop)
        self.tree_popup.mouse_left.connect(self._on_popup_mouse_left)
        self.tree_popup.hide()

        self.nav_scroll.verticalScrollBar().valueChanged.connect(self._on_nav_scrolled)

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

    def _refresh_navigation_layout(self):
        self._rebuild_children_map()
        rail_ids = self._rail_page_ids()
        self._sync_rail_buttons(rail_ids)
        self.nav_buttons.updateGeometry()
        self.nav_buttons.adjustSize()
        self.nav_scroll.viewport().update()

    def _rebuild_title_index(self):
        self.title_to_page_id = {}
        for page_id, spec in self.page_specs.items():
            self.title_to_page_id[spec.title] = page_id

    def _rebuild_children_map(self):
        self.children_by_parent = {}
        for page_id, spec in self.page_specs.items():
            parent_id = self._parent_page_id(spec)
            self.children_by_parent.setdefault(parent_id, []).append(page_id)

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

    def _has_children(self, page_id):
        return len(self.children_by_parent.get(page_id, [])) > 0

    def _is_top_level_page(self, page_id):
        if page_id == self.home_page_id:
            return True
        parent_id = self._parent_page_id_for_id(page_id)
        if parent_id is None:
            return True
        return parent_id == self.home_page_id

    def _ancestor_path(self, page_id):
        chain = []
        cursor = self._parent_page_id_for_id(page_id)
        visited = set()
        while cursor is not None and cursor not in visited:
            visited.add(cursor)
            if cursor != self.home_page_id:
                chain.append(cursor)
            cursor = self._parent_page_id_for_id(cursor)
        chain.reverse()
        return chain

    def _rail_page_ids(self):
        ids = []
        if self.home_page_id and self.home_page_id in self.page_specs:
            ids.append(self.home_page_id)

        current = self.current_page_id
        if current and current in self.page_specs and current != self.home_page_id:
            for page_id in self._ancestor_path(current):
                if page_id not in ids:
                    ids.append(page_id)
            if current not in ids:
                ids.append(current)

        for page_id, spec in self.page_specs.items():
            if page_id in ids:
                continue
            if self._is_top_level_page(page_id):
                ids.append(page_id)
        return ids

    def rail_page_ids(self):
        return self._rail_page_ids()

    def _sync_rail_buttons(self, rail_ids):
        for page_id in list(self.page_buttons.keys()):
            if page_id not in rail_ids:
                button = self.page_buttons.pop(page_id)
                self.nav_buttons_layout.removeWidget(button)
                button.hide()
                button.deleteLater()

        for page_id in rail_ids:
            if page_id not in self.page_buttons:
                spec = self.page_specs[page_id]
                button = ResultPageButton(spec, self.nav_buttons)
                button.clicked_with_id.connect(self._on_nav_button_clicked)
                button.hovered_with_id.connect(self._on_nav_button_hover_begin)
                button.unhovered_with_id.connect(self._on_nav_button_hover_end)
                self.page_buttons[page_id] = button
            else:
                button = self.page_buttons[page_id]
                button.spec = self.page_specs[page_id]
                button.refresh_hint()

        while self.nav_buttons_layout.count():
            item = self.nav_buttons_layout.takeAt(0)

        for page_id in rail_ids:
            button = self.page_buttons[page_id]
            self.nav_buttons_layout.addWidget(button, 0, Qt.AlignHCenter)
            button.show()

    def _on_nav_button_clicked(self, page_id):
        if page_id in self.page_specs:
            self.activate_page(page_id)
        if self._has_children(page_id):
            self._show_tree_popup(page_id, pinned=False)
        elif self.tree_popup.isVisible() and not self.tree_popup.is_pinned():
            self._hide_tree_popup()

    def _on_nav_button_hover_begin(self, page_id):
        if page_id not in self.page_specs:
            return
        self.hover_page_id = page_id
        self.close_timer.stop()
        if self.tree_popup.isVisible() and self.popup_page_id == page_id:
            return
        if self.tree_popup.isVisible() and self.popup_page_id != page_id:
            if self.tree_popup.is_pinned():
                return
            self._hide_tree_popup()
        if self._has_children(page_id):
            self.hover_timer.stop()
            self.hover_timer.start()

    def _on_nav_button_hover_end(self, page_id):
        if page_id != self.hover_page_id:
            return
        self.hover_timer.stop()
        if self.tree_popup.isVisible() and self.popup_page_id == page_id and not self.tree_popup.is_pinned():
            self.close_timer.start()

    def _on_hover_timer_timeout(self):
        page_id = self.hover_page_id
        if page_id and page_id in self.page_specs and self._has_children(page_id):
            self._show_tree_popup(page_id, pinned=False)

    def _on_close_timer_timeout(self):
        if self.tree_popup.isVisible() and not self.tree_popup.is_pinned():
            if self._is_cursor_over_popup():
                return
            if self.hover_page_id and self._is_cursor_over_button(self.hover_page_id):
                return
            self._hide_tree_popup()

    def _on_popup_mouse_left(self):
        if not self.tree_popup.is_pinned():
            self.close_timer.start()

    def _is_cursor_over_popup(self):
        popup = self.tree_popup
        if popup is None or not popup.isVisible():
            return False
        pos = popup.mapFromGlobal(QCursor.pos())
        return popup.rect().contains(pos)

    def _is_cursor_over_button(self, page_id):
        button = self.page_buttons.get(page_id)
        if button is None or not button.isVisible():
            return False
        pos = button.mapFromGlobal(QCursor.pos())
        return button.rect().contains(pos)

    def _show_tree_popup(self, page_id, pinned=False):
        if page_id not in self.page_specs or not self._has_children(page_id):
            return
        self.hover_timer.stop()
        self.close_timer.stop()
        if self.popup_page_id != page_id or not self.tree_popup.isVisible():
            self.tree_popup.set_page_tree(page_id, self.page_specs, self.children_by_parent)
            self.popup_page_id = page_id
        self.tree_popup.set_pinned(pinned if pinned else self.tree_popup.is_pinned())
        self.tree_popup.set_current_page(self.current_page_id)
        self._position_tree_popup(page_id)
        self.tree_popup.show()
        self.tree_popup.raise_()

    def _position_tree_popup(self, page_id):
        button = self.page_buttons.get(page_id)
        if button is None:
            return
        popup = self.tree_popup
        height = min(popup.maximumHeight(), max(popup.minimumHeight(), popup.sizeHint().height()))
        popup.resize(popup.width(), height)
        x = self.sidebar.width() + 2
        if x + popup.width() > self.width() - 4:
            x = max(4, self.width() - popup.width() - 4)
        button_top = button.mapTo(self, QPoint(0, 0)).y()
        y = button_top
        if y + height > self.height() - 8:
            y = max(8, self.height() - height - 8)
        popup.move(x, y)

    def _hide_tree_popup(self):
        self.tree_popup.hide()
        self.popup_page_id = None

    def _on_tree_page_activated(self, page_id):
        if page_id in self.page_specs:
            self.activate_page(page_id)
        if not self.tree_popup.is_pinned():
            self._hide_tree_popup()

    def _on_popup_pin_toggled(self, pinned):
        if pinned:
            self.close_timer.stop()
        else:
            self.close_timer.start()

    def _on_popup_close_requested(self):
        self.tree_popup.set_pinned(False)
        self._hide_tree_popup()

    def _on_nav_scrolled(self, value):
        if self.tree_popup.isVisible():
            self._hide_tree_popup()

    def _rebuild_tree_popup_if_visible(self):
        if not self.tree_popup.isVisible() or self.popup_page_id is None:
            return
        if self.popup_page_id not in self.page_specs:
            self._hide_tree_popup()
            return
        self.tree_popup.set_page_tree(self.popup_page_id, self.page_specs, self.children_by_parent)
        self.tree_popup.set_current_page(self.current_page_id)

    def _add_page(self, spec: AnalysisPageSpec):
        self.page_specs[spec.page_id] = spec
        self._ensure_source_title(spec)
        self._rebuild_title_index()
        self._refresh_navigation_layout()

    def _refresh_header(self):
        spec = self.current_spec()
        if spec is None:
            self.close_button.hide()
            return
        self.close_button.setVisible(spec.closeable)

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
        for current_id, button in self.page_buttons.items():
            button.set_active(current_id == page_id)

        if self.tree_popup.isVisible():
            self.tree_popup.set_current_page(page_id)

        self._refresh_header()
        self.page_activated.emit(page_id)

    def close_current_page(self):
        if self.current_page_id is not None:
            self.close_page(self.current_page_id)

    def close_page(self, page_id: str):
        spec = self.page_specs.get(page_id)
        if spec is None or not spec.closeable:
            return

        was_current = page_id == self.current_page_id

        self.activation_history = [pid for pid in self.activation_history if pid != page_id]
        self.page_specs.pop(page_id, None)
        self._rebuild_title_index()
        self._hide_tree_popup()
        self._refresh_navigation_layout()

        self.page_closed.emit(page_id)

        if not was_current:
            return

        fallback_id = self.activation_history[-1] if self.activation_history else self.home_page_id
        if fallback_id is not None:
            self.activate_page(fallback_id)
        else:
            self._refresh_header()

    def reset_to_home(self):
        for page_id in list(self.page_specs.keys()):
            if page_id != self.home_page_id:
                self.close_page(page_id)

        if self.home_page_id is not None:
            self.activate_page(self.home_page_id)

    def current_spec(self) -> Optional[AnalysisPageSpec]:
        if self.current_page_id is None:
            return None
        return self.page_specs.get(self.current_page_id)

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
        self._rebuild_title_index()
        self._refresh_navigation_layout()
        self._rebuild_tree_popup_if_visible()

        button = self.page_buttons.get(page_id)
        if button is not None:
            button.spec = spec
            button.refresh_hint()

    def page_by_id(self, page_id: Optional[str]) -> Optional[AnalysisPageSpec]:
        if page_id is None:
            return None
        return self.page_specs.get(page_id)

    def home_spec(self) -> Optional[AnalysisPageSpec]:
        return self.page_by_id(self.home_page_id)
