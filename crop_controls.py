"""Toolbar crop mode, per-page drafts and the transient numeric editor."""
from dataclasses import replace

from PyQt5.QtCore import QObject, QPoint, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QApplication, QFrame, QGridLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

import theme
from crop_model import axis_labels, selection_for_context


def scissors_cursor():
    screen = QApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen else 1.0
    pixmap = QPixmap(round(32 * ratio), round(32 * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    # Contrasting outline keeps the cursor visible on both bright and dark data.
    for color, width in (("#171923", 4.5), ("#ffffff", 2.1)):
        painter.setPen(QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(3, 21, 8, 8)
        painter.drawEllipse(20, 21, 8, 8)
        painter.drawLine(10, 22, 25, 3)
        painter.drawLine(22, 22, 8, 5)
    painter.end()
    return QCursor(pixmap, 25, 3)


class CropPopup(QFrame):
    edited = pyqtSignal()
    apply_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("crop_popup")
        self.setFixedWidth(360)
        self.setStyleSheet(f"QFrame#crop_popup {{ background: {theme.BG_2}; border: 1px solid {theme.BORDER}; border-radius: 8px; }}")
        self.setCursor(Qt.ArrowCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        title = QLabel("裁剪范围", self)
        title.setStyleSheet(theme.field_label_qss())
        layout.addWidget(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)
        self.labels, self.edits = [], []
        for i in range(6):
            label = QLabel(self)
            label.setStyleSheet(theme.field_label_qss())
            edit = QLineEdit(self)
            edit.setFixedHeight(32)
            edit.setStyleSheet(theme.value_input_qss())
            edit.setCursor(Qt.IBeamCursor)
            label.setBuddy(edit)
            edit.editingFinished.connect(self.edited)
            self.labels.append(label)
            self.edits.append(edit)
            grid.addWidget(label, (i // 2) * 2, i % 2)
            grid.addWidget(edit, (i // 2) * 2 + 1, i % 2)
        layout.addLayout(grid)
        self.error_label = QLabel(self)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {theme.TEXT_2};")
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.apply_button = QPushButton("裁剪", self)
        self.apply_button.setFixedHeight(34)
        theme.style_push_button(self.apply_button, "primary")
        self.apply_button.clicked.connect(self.apply_requested)
        layout.addWidget(self.apply_button)

    def set_selection(self, selection):
        labels = axis_labels(selection) if selection else ()
        for i, (label, edit) in enumerate(zip(self.labels, self.edits)):
            active = selection is not None and i < len(selection.bounds)
            name = labels[i // 2] if active else "无对应维度"
            text = f"{name} {'下限' if i % 2 == 0 else '上限'}"
            label.setText(text)
            label.setEnabled(active)
            edit.setEnabled(active)
            edit.setAccessibleName(text)
            edit.setText(format(selection.bounds[i], ".12g") if active else "—")
        self.set_error("")
        self.apply_button.setEnabled(selection is not None)

    def set_error(self, message):
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))
        self.adjustSize()

    def read_bounds(self, count):
        try:
            return tuple(float(edit.text().strip()) for edit in self.edits[:count])
        except ValueError as exc:
            raise ValueError("请输入完整的数值上下限。") from exc

    def show_at(self, position):
        self.adjustSize()
        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            x = max(area.left(), min(position.x(), area.right() - self.width() + 1))
            y = max(area.top(), min(position.y(), area.bottom() - self.height() + 1))
            position = QPoint(x, y)
        self.move(position)
        self.show()
        self.raise_()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
            event.accept()
        else:
            super().keyPressEvent(event)


class CropController(QObject):
    selection_changed = pyqtSignal()
    apply_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.popup = CropPopup(parent)
        self.cursor = scissors_cursor()
        self.enabled = False
        self.page_id = None
        self.selection = None
        self.selections = {}
        self._syncing = False
        self.popup.edited.connect(self.commit_edits)
        self.popup.apply_requested.connect(self._apply)

    def activate(self, page_id, context, *, e_flip=False):
        if self.page_id != page_id:
            self.popup.hide()
        self.page_id = page_id
        default = selection_for_context(context, e_flip=e_flip)
        saved = self.selections.get(page_id)
        if default and saved and (saved.view, saved.axes) == (default.view, default.axes):
            default = replace(saved, e_flip=e_flip)
        self.set_selection(default, notify=False)

    def set_selection(self, selection, *, notify=True):
        self.selection = selection
        if selection is not None and self.page_id is not None:
            self.selections[self.page_id] = selection
        self._syncing = True
        try:
            self.popup.set_selection(selection)
        finally:
            self._syncing = False
        if notify:
            self.selection_changed.emit()

    def commit_edits(self):
        if self._syncing or self.selection is None:
            return False
        try:
            selection = replace(self.selection, bounds=self.popup.read_bounds(len(self.selection.bounds)))
        except ValueError as exc:
            self.popup.set_error(str(exc))
            return False
        changed = selection != self.selection
        self.set_selection(selection, notify=changed)
        return True

    def _apply(self):
        if self.commit_edits():
            self.apply_requested.emit()

    def clear(self):
        self.popup.hide()
        self.selections.clear()
        self.selection = None
        self.page_id = None

    def remove_page(self, page_id):
        self.selections.pop(page_id, None)
        if self.page_id == page_id:
            self.popup.hide()
