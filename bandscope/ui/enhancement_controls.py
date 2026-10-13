"""Animated region controls, including deletion inside the dropdown popup."""
from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QDoubleValidator
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListView, QPushButton, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from bandscope.ui import theme


class _RegionDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if index.data(Qt.UserRole):
            painter.save()
            painter.setPen(QColor(theme.TEXT_2))
            rect = option.rect.adjusted(option.rect.width() - 28, 0, -4, 0)
            painter.drawText(rect, Qt.AlignCenter, "×")
            painter.restore()


class RegionComboBox(QComboBox):
    delete_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        view = QListView(self)
        self.setView(view)
        view.setItemDelegate(_RegionDelegate(view))
        view.viewport().installEventFilter(self)
        self.setMinimumHeight(30)
        self.setToolTip("选择调整对象；点击区域条目右侧 × 删除，数据将并入背景重新归一化。")
        self.setStyleSheet(
            f"QComboBox {{ background: {theme.BG_2}; color: {theme.TEXT_1}; padding: 5px 10px; border-radius: 6px; }}"
            f"QListView {{ background: {theme.BG_2}; color: {theme.TEXT_1}; selection-background-color: {theme.ACCENT}; }}"
            "QListView::item { min-height: 30px; padding-right: 30px; }"
        )

    def eventFilter(self, watched, event):
        if watched is self.view().viewport() and event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            index = self.view().indexAt(event.pos())
            region_id = index.data(Qt.UserRole) if index.isValid() else None
            if region_id and event.pos().x() >= self.view().visualRect(index).right() - 28:
                if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                    self.delete_requested.emit(str(region_id))
                return True
        return super().eventFilter(watched, event)


class EnhancementControls(QWidget):
    target_changed = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    rename_requested = pyqtSignal()
    bounds_requested = pyqtSignal()
    common_changed = pyqtSignal(bool)
    locked_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QCheckBox {{ color: {theme.TEXT_1}; spacing: 7px; }}"
            f"QCheckBox:disabled {{ color: {theme.TEXT_3}; }}"
            f"QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid {theme.TEXT_3}; border-radius: 3px; background: {theme.BG_2}; }}"
            f"QCheckBox::indicator:checked {{ background: {theme.ACCENT}; border-color: {theme.ACCENT}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = RegionComboBox(self)
        self.combo.currentIndexChanged.connect(lambda _index: self.target_changed.emit(self.combo.currentData() or ""))
        self.combo.delete_requested.connect(self.delete_requested)
        layout.addWidget(self.combo)
        row = QHBoxLayout()
        for label, signal in (("重命名", self.rename_requested), ("范围", self.bounds_requested)):
            button = QPushButton(label, self)
            button.setMinimumHeight(28)
            theme.style_push_button(button, "secondary")
            button.clicked.connect(signal)
            row.addWidget(button)
            if label == "重命名":
                self.rename_button = button
            else:
                self.bounds_button = button
        layout.addLayout(row)
        self.common = QCheckBox("共用范围与设置（强度对比）", self)
        self.common.toggled.connect(self.common_changed)
        layout.addWidget(self.common)
        self.locked = QCheckBox("锁定区域归一化范围", self)
        self.locked.toggled.connect(self.locked_changed)
        layout.addWidget(self.locked)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")
        layout.addWidget(self.summary)
        self.hide()

    def sync(self, regions, selected_id, *, common=False, locked=False, summary=""):
        widgets = (self.combo, self.common, self.locked)
        for widget in widgets:
            widget.blockSignals(True)
        try:
            items = [("全局（背景）", "")] + [
                (str(r["name"]), r["region_id"])
                for r in regions]
            old = [(self.combo.itemText(i), self.combo.itemData(i)) for i in range(self.combo.count())]
            if old != items:
                self.combo.clear()
                for text, region_id in items:
                    self.combo.addItem(text, region_id)
            index = self.combo.findData(selected_id or "")
            self.combo.setCurrentIndex(max(0, index))
            region = next((r for r in regions if r["region_id"] == selected_id), None)
            self.rename_button.setEnabled(region is not None)
            self.bounds_button.setEnabled(region is not None)
            self.common.setChecked(common)
            self.locked.setEnabled(region is not None)
            self.locked.setChecked(locked)
            self.summary.setText(summary)
        finally:
            for widget in widgets:
                widget.blockSignals(False)


class EnhancementBoundsDialog(QDialog):
    def __init__(self, axes, bounds, parent=None):
        super().__init__(parent)
        self.setWindowTitle("增强区域范围")
        self.setStyleSheet(f"QDialog {{ background: {theme.BG_1}; }} QLabel {{ color: {theme.TEXT_1}; }}")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.edits = []
        for i, key in enumerate(axes):
            for j, label in enumerate(("下限", "上限")):
                edit = QLineEdit(f"{bounds[2*i+j]:.8g}", self)
                edit.setStyleSheet(theme.value_input_qss())
                edit.setValidator(QDoubleValidator(edit))
                form.addRow(f"{key} {label}", edit)
                self.edits.append(edit)
        layout.addLayout(form)
        self.error = QLabel(self)
        self.error.setStyleSheet(f"color: {theme.TEXT_2};")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self._accept_bounds)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_bounds(self):
        import math
        try:
            values = tuple(float(edit.text()) for edit in self.edits)
            if not all(math.isfinite(v) for v in values) or any(values[i] >= values[i+1] for i in range(0, len(values), 2)):
                raise ValueError
        except ValueError:
            self.error.setText("请输入有限数值，并使每轴下限小于上限。")
            return
        self.bounds = values
        self.accept()
