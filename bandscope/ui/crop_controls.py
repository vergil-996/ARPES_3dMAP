"""Toolbar crop mode, per-page drafts and the draggable numeric range window."""
from dataclasses import replace

from PyQt5.QtCore import QObject, QPoint, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import bandscope.ui.theme as theme
from bandscope.core.crop_model import axis_labels, selection_for_context


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

    #: 标题行右端的拖动提示：整行可拖动，无边框窗口靠它表明自己是浮窗。
    DRAG_HINT_TEXT = "按住拖动"

    def __init__(self, parent=None):
        # 用 Qt.Tool 而不是 Qt.Popup：窗口由工具栏的裁剪/裁空开关打开，
        # 是常驻浮窗——不抢鼠标、不因点击别处消失，可以一边拖选区一边改范围。
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setObjectName("crop_popup")
        self.setFixedWidth(360)
        self.setStyleSheet(f"QFrame#crop_popup {{ background: {theme.BG_2}; border: 1px solid {theme.BORDER}; border-radius: 8px; }}")
        self.setCursor(Qt.ArrowCursor)
        self._drag_offset = None
        self._user_position = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(self._create_header())
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

    def _create_header(self):
        header = QWidget(self)
        header.setCursor(Qt.SizeAllCursor)
        header.setToolTip("按住拖动窗口")
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.title_label = QLabel("裁剪范围", header)
        self.title_label.setStyleSheet(theme.field_label_qss())
        row.addWidget(self.title_label, 1, Qt.AlignVCenter)
        hint = QLabel(self.DRAG_HINT_TEXT, header)
        hint.setStyleSheet(
            f"color: {theme.TEXT_3}; background: transparent; font-size: 11px;"
        )
        row.addWidget(hint, 0, Qt.AlignVCenter)
        return header

    def set_selection(self, selection):
        action = "裁空" if selection and selection.operation == "erase" else "裁剪"
        self.title_label.setText("裁空范围" if action == "裁空" else "裁剪范围")
        self.apply_button.setText(action)
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

    def _clamp_position(self, position):
        screen = (
            QApplication.screenAt(position)
            or QApplication.screenAt(self.pos())
            or QApplication.primaryScreen()
        )
        if screen is None:
            return position
        area = screen.availableGeometry()
        x = max(area.left(), min(position.x(), area.right() - self.width() + 1))
        y = max(area.top(), min(position.y(), area.bottom() - self.height() + 1))
        return QPoint(x, y)

    def show_at(self, position):
        self.adjustSize()
        self.move(self._clamp_position(position))
        self.show()
        self.raise_()

    def show_near(self, anchor=None):
        """在工具栏按钮正下方打开；用户拖动过则沿用上次拖到的位置。"""
        self.adjustSize()
        if self._user_position is not None:
            self.show_at(self._user_position)
            return
        if anchor is not None:
            self.show_at(
                anchor.mapToGlobal(
                    QPoint((anchor.width() - self.width()) // 2, anchor.height() + 8)
                )
            )
            return
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        center = area.center() if area is not None else QPoint(self.width(), self.height())
        self.show_at(center - QPoint(self.width() // 2, self.height() // 2))

    # ------------------------------------------------------------------
    # 拖动：标题行与空白处按下即整窗移动，输入框等子控件照常吃自己的事件
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_offset is None:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(self._clamp_position(event.globalPos() - self._drag_offset))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_offset is not None and event.button() == Qt.LeftButton:
            self._drag_offset = None
            self._user_position = self.pos()
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
        self.operation = "crop"
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
        self.set_selection(replace(default, operation=self.operation) if default else None, notify=False)

    def set_operation(self, operation):
        self.operation = operation
        self.cursor = QCursor(Qt.CrossCursor) if operation == "erase" else scissors_cursor()
        self.popup.hide()
        if self.selection is not None:
            self.set_selection(replace(self.selection, operation=operation), notify=False)

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
