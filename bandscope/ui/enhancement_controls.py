"""Editable region selector combo with deletion inside the dropdown popup."""
from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QComboBox, QListView, QStyledItemDelegate

from bandscope.ui import theme

#: 下拉框「全部显示」条目的标识：不是任何区域 id（区域 id 为 uuid 十六进制）。
SHOW_ALL_REGIONS_ID = "__show_all__"


class _RegionDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        region_id = index.data(Qt.UserRole)
        if region_id and region_id != SHOW_ALL_REGIONS_ID:
            painter.save()
            painter.setPen(QColor(theme.TEXT_2))
            rect = option.rect.adjusted(option.rect.width() - 28, 0, -4, 0)
            painter.drawText(rect, Qt.AlignCenter, "×")
            painter.restore()


class RegionComboBox(QComboBox):
    """区域选择下拉框：固定显示；编辑文字提交即重命名当前区域；条目右侧 × 删除。"""

    target_changed = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    rename_committed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        view = QListView(self)
        self.setView(view)
        view.setItemDelegate(_RegionDelegate(view))
        view.viewport().installEventFilter(self)
        self.setMinimumHeight(30)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setToolTip(
            "色阶滑条与强度映射只调整当前选中的区域；\n"
            "选择「全局（背景）」只调整背景；选择「全部显示」\n"
            "展示所有区域的色带与轮廓，调节时同时调整背景与全部区域。\n"
            "各区域按自己的强度范围归一化；锁定色带对所有对象统一生效。\n"
            "直接在框内编辑文字可重命名当前区域；\n"
            "点击区域条目右侧 × 删除，数据将并入背景重新归一化。"
        )
        self.setStyleSheet(
            f"QComboBox {{ background: {theme.BG_2}; color: {theme.TEXT_1}; padding: 5px 10px; border-radius: 6px; }}"
            f"QLineEdit {{ background: {theme.BG_2}; color: {theme.TEXT_1}; border: none; }}"
            f"QListView {{ background: {theme.BG_2}; color: {theme.TEXT_1}; selection-background-color: {theme.ACCENT}; }}"
            "QListView::item { min-height: 30px; padding-right: 30px; }"
        )
        self.currentIndexChanged.connect(self._emit_target_change)
        self.lineEdit().editingFinished.connect(self._commit_rename)

    def _emit_target_change(self, index):
        if index >= 0:
            self.target_changed.emit(self.itemData(index) or "")

    def _commit_rename(self):
        index = self.currentIndex()
        if index < 0:
            return
        if not self.itemData(index) or self.itemData(index) == SHOW_ALL_REGIONS_ID:
            # 「全局（背景）」与「全部显示」不可重命名：直接恢复显示文字。
            self.lineEdit().setText(self.itemText(index))
            return
        text = self.lineEdit().text().strip()
        if text and text != self.itemText(index):
            self.rename_committed.emit(text)
        else:
            self.lineEdit().setText(self.itemText(index))

    def eventFilter(self, watched, event):
        if watched is self.view().viewport() and event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            index = self.view().indexAt(event.pos())
            region_id = index.data(Qt.UserRole) if index.isValid() else None
            if region_id and region_id != SHOW_ALL_REGIONS_ID and event.pos().x() >= self.view().visualRect(index).right() - 28:
                if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                    self.delete_requested.emit(str(region_id))
                return True
        return super().eventFilter(watched, event)

    def sync(self, regions, selected_id, show_all=False):
        """重建条目并选中目标；regions 为匹配当前视图的 region dict 列表。"""
        items = [("全局（背景）", ""), ("全部显示", SHOW_ALL_REGIONS_ID)] + [
            (str(r["name"]), r["region_id"])
            for r in regions]
        old = [(self.itemText(i), self.itemData(i)) for i in range(self.count())]
        self.blockSignals(True)
        try:
            if old != items:
                self.clear()
                for text, region_id in items:
                    self.addItem(text, region_id)
            index = self.findData(SHOW_ALL_REGIONS_ID if show_all else (selected_id or ""))
            self.setCurrentIndex(max(0, index))
            # 可编辑框里未提交的文字不会随同步自动撤回，统一回写当前项文字。
            self.lineEdit().setText(self.itemText(self.currentIndex()))
        finally:
            self.blockSignals(False)
