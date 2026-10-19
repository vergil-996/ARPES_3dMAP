"""Editable enhancement-region selector built on the SiliconUI capsule combo."""
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QComboBox, QSizePolicy
from siui.components.combobox_ import SiCapsuleComboBox

from bandscope.ui import theme

#: 下拉框「全部显示」条目的标识：不是任何区域 id（区域 id 为 uuid 十六进制）。
SHOW_ALL_REGIONS_ID = "__show_all__"


class RegionComboBox(SiCapsuleComboBox):
    """区域选择下拉框：选择调整对象；编辑文字提交即重命名当前区域。"""

    target_changed = pyqtSignal(str)
    rename_committed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("区域")
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        theme.raise_well_on_card(self)
        self.currentIndexChanged.connect(self._emit_target_change)
        self.lineEdit().editingFinished.connect(self._commit_rename)

    def setCurrentIndex(self, index):
        """Keep the editable text in step with programmatic selections."""
        super().setCurrentIndex(index)
        edit = self.lineEdit()
        current = self.currentIndex()
        if edit is not None and 0 <= current < self.count():
            edit.setText(self.itemText(current))

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
            self.setItemText(index, text)
            self.rename_committed.emit(text)
        else:
            self.lineEdit().setText(self.itemText(index))

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
