"""顶部数据源切换器：可编辑下拉框 + 220 ms 宽度收放。

交互约定（与多数据源工作区计划一致）：

- 条目是"数据源别名"，``itemData`` 保存会话标识（uuid），界面不感知页面 ID。
- 名称编辑提交后交给宿主落地：宿主负责空名称恢复默认名、重名追加序号，再把
  最终别名 ``sync`` 回来。
- Enter / 失焦提交，Esc 撤销；``QComboBox.NoInsert`` 保证输入文字不会变成新条目。
- 只有 2 份及以上数据源时才占布局空间；1→2 宽度展开并淡入，2→1 反向收起，
  连续操作从当前动画位置衔接（``animate_widget_width`` 自带打断续播）。
"""
from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtWidgets import QComboBox, QHBoxLayout, QSizePolicy, QWidget
from siui.components.combobox_ import SiCapsuleComboBox

from bandscope.ui import theme
from bandscope.ui.control_layout_utils import animate_widget_width

#: 展开动画时长（与底栏时间轴、位置滑条共用同一套节奏）。
EXPAND_DURATION_MS = 220

#: 展开后的固定宽度：工具栏里给"加载数据"按钮右侧留出的位置。
SWITCHER_WIDTH = 220


class SourceSwitcher(SiCapsuleComboBox):
    """数据源下拉框：选择即切源，编辑文字提交即改别名。"""

    source_selected = pyqtSignal(str)
    alias_committed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("数据源")
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        theme.raise_well_on_card(self)
        self.currentIndexChanged.connect(self._emit_source_change)
        editor = self.lineEdit()
        if editor is not None:
            editor.installEventFilter(self)
            editor.editingFinished.connect(self._commit_alias)

    # ------------------------------------------------------------------
    # 条目同步
    # ------------------------------------------------------------------
    def setCurrentIndex(self, index):
        """让可编辑框的文字跟随程序化选中的条目。"""
        super().setCurrentIndex(index)
        self._restore_editor_text()

    def sync(self, items: Sequence[Tuple[str, str]], active_id=None):
        """按 ``(session_id, alias)`` 重建条目并选中活动源。"""
        wanted = [(str(session_id), str(alias)) for session_id, alias in (items or ())]
        old = [
            (self.itemData(index), self.itemText(index))
            for index in range(self.count())
        ]
        wanted_data = [(session_id, alias) for session_id, alias in wanted]
        self.blockSignals(True)
        try:
            if old != wanted_data:
                self.clear()
                for session_id, alias in wanted:
                    self.addItem(alias, session_id)
            if active_id is not None:
                index = self.findData(str(active_id))
                if index >= 0:
                    self.setCurrentIndex(index)
            if self.count() and self.currentIndex() < 0:
                self.setCurrentIndex(0)
            if not self.count():
                self.setCurrentIndex(-1)
            self._restore_editor_text()
        finally:
            self.blockSignals(False)

    def session_items(self) -> List[Tuple[str, str]]:
        return [
            (str(self.itemData(index)), str(self.itemText(index)))
            for index in range(self.count())
        ]

    def current_session_id(self) -> str:
        index = self.currentIndex()
        return str(self.itemData(index) or "") if index >= 0 else ""

    # ------------------------------------------------------------------
    # 选择与改名
    # ------------------------------------------------------------------
    def _emit_source_change(self, index):
        if index < 0:
            return
        session_id = self.itemData(index)
        if session_id:
            self.source_selected.emit(str(session_id))

    def _restore_editor_text(self):
        editor = self.lineEdit()
        if editor is None:
            return
        index = self.currentIndex()
        text = self.itemText(index) if 0 <= index < self.count() else ""
        editor.setText(text)

    def _commit_alias(self):
        index = self.currentIndex()
        if index < 0:
            return
        session_id = self.itemData(index)
        if not session_id:
            self._restore_editor_text()
            return
        editor = self.lineEdit()
        text = editor.text().strip() if editor is not None else ""
        if text == self.itemText(index):
            # 未改名（含空名称直接失焦）：恢复展示文字，不触发宿主事务。
            if not text:
                self._restore_editor_text()
            return
        # 先乐观显示，宿主去重后会把最终别名 sync 回来。
        self.setItemText(index, text or self.itemText(index))
        self.alias_committed.emit(str(session_id), text)

    def eventFilter(self, watched, event):
        editor = self.lineEdit()
        if (
            editor is not None
            and watched is editor
            and event.type() == QEvent.KeyPress
            and event.key() == Qt.Key_Escape
        ):
            self._restore_editor_text()
            editor.deselect()
            return True
        return super().eventFilter(watched, event)


class SourceSwitcherBar(QWidget):
    """切换器容器：负责条目的进出与宽度展开/收起。"""

    source_selected = pyqtSignal(str)
    alias_committed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("source_switcher_bar")
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setMinimumWidth(0)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.switcher = SourceSwitcher(self)
        self.switcher.setFixedWidth(SWITCHER_WIDTH)
        self.switcher.source_selected.connect(self.source_selected)
        self.switcher.alias_committed.connect(self.alias_committed)
        layout.addWidget(self.switcher)

        # 0 或 1 份数据源时不占布局空间。
        self._expanded = False
        self._items: List[Tuple[str, str]] = []
        self.setVisible(False)

    # ------------------------------------------------------------------
    def apply_sources(self, items: Iterable[Tuple[str, str]], active_id=None):
        """同步条目并按数量收放；切源与改别名都走这里。"""
        self._items = [(str(session_id), str(alias)) for session_id, alias in (items or ())]
        self.switcher.sync(self._items, active_id)
        self._apply_visibility(len(self._items) >= 2)

    def items(self) -> List[Tuple[str, str]]:
        return list(self._items)

    def is_expanded(self) -> bool:
        """目标状态（动画进行中也返回最终状态）。"""
        return self._expanded

    def is_animating(self) -> bool:
        return getattr(self, "_visibility_animation", None) is not None

    def _apply_visibility(self, visible: bool):
        visible = bool(visible)
        if (
            visible == self._expanded
            and not self.is_animating()
            and self.isHidden() == (not visible)
        ):
            # 已经是目标状态且没有进行中的收放：不必重启动画。
            return
        self._expanded = visible
        animate_widget_width(
            self,
            visible,
            duration=EXPAND_DURATION_MS,
            target_width=SWITCHER_WIDTH,
        )
        if visible:
            self.switcher.setFocusPolicy(Qt.StrongFocus)
        else:
            self.switcher.setFocusPolicy(Qt.NoFocus)
