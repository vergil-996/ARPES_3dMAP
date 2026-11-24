"""Toolbar crop mode, per-page drafts and the draggable numeric range window."""
from dataclasses import replace

from PyQt5.QtCore import QObject, QPoint, Qt, pyqtSignal
from PyQt5.QtGui import QCursor
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
from bandscope.core.crop_model import axis_labels, reorient_volume_selection, selection_for_context
from bandscope.ui.numeric_wheel import enable_numeric_wheel


_USE_CACHED_E_FLIP = object()


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
            enable_numeric_wheel(edit, edit.editingFinished.emit)
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
        # 裁剪与裁空共用同一个十字光标；不要按操作区分光标样式。
        self.cursor = QCursor(Qt.CrossCursor)
        self.operation = "crop"
        self.enabled = False
        self.page_id = None
        self.selection = None
        self.selections = {}
        self._active_selection_ids = {}
        self._next_selection_id = 1
        self._context = None
        self._e_flip = False
        self._syncing = False
        self.popup.edited.connect(self.commit_edits)
        self.popup.apply_requested.connect(self._apply)

    @property
    def active_selection_id(self):
        selection_id = self._active_selection_ids.get(self.page_id)
        if selection_id not in self.selections.get(self.page_id, {}):
            return None
        return selection_id

    def selections_for_page(self, page_id=None):
        page_id = self.page_id if page_id is None else page_id
        return tuple(self.selections.get(page_id, {}).items())

    def _new_selection_id(self):
        selection_id = self._next_selection_id
        self._next_selection_id += 1
        return selection_id

    def _default_selection(self, context, e_flip, existing_count):
        default = selection_for_context(context, e_flip=e_flip)
        if default is None:
            return None
        fraction = 0
        if default.view == "2d":
            fraction = .1 if not existing_count else .4 - .2 / (2 ** (existing_count - 1))
        elif existing_count:
            fraction = .4 - .3 / (2 ** (existing_count - 1))
        if fraction:
            bounds = []
            for low, high in zip(default.bounds[::2], default.bounds[1::2]):
                inset = (high - low) * fraction
                bounds.extend((low + inset, high - inset))
            default = replace(default, bounds=tuple(bounds))
        return replace(default, operation=self.operation)

    def _set_active_selection(self, selection_id, selection, *, notify=True):
        if self.page_id is not None and selection_id is not None:
            self._active_selection_ids[self.page_id] = selection_id
        elif self.page_id is not None:
            self._active_selection_ids.pop(self.page_id, None)
        self.selection = selection
        self._syncing = True
        try:
            self.popup.set_selection(selection)
        finally:
            self._syncing = False
        if notify:
            self.selection_changed.emit()

    def activate(self, page_id, context, *, e_flip=False):
        if self.page_id != page_id:
            self.popup.hide()
        self.page_id = page_id
        if context is not None:
            self._context = context
        self._e_flip = e_flip
        drafts = self.selections.get(page_id, {})
        selection_id = self._active_selection_ids.get(page_id)
        if selection_id not in drafts:
            selection_id = next(iter(drafts), None)
        saved = drafts.get(selection_id)
        default = selection_for_context(context, e_flip=e_flip) if context is not None else None
        if default and saved and (saved.view, saved.axes) == (default.view, default.axes):
            selection = reorient_volume_selection(context, saved, e_flip)
        elif default:
            existing_count = max(0, len(drafts) - 1) if saved is not None else len(drafts)
            selection = self._default_selection(context, e_flip, existing_count)
        elif saved is not None:
            selection = saved
        else:
            selection = None
        if selection is not None:
            selection = replace(selection, operation=self.operation)
            if selection_id is None:
                selection_id = self._new_selection_id()
            self.selections.setdefault(page_id, {})[selection_id] = selection
        self._set_active_selection(selection_id, selection, notify=False)

    def add_selection(self, context=None, *, e_flip=_USE_CACHED_E_FLIP):
        if context is None:
            context = self._context
            if e_flip is _USE_CACHED_E_FLIP:
                e_flip = self._e_flip
        elif e_flip is _USE_CACHED_E_FLIP:
            e_flip = False
        if self.page_id is None or context is None:
            return None
        drafts = self.selections.setdefault(self.page_id, {})
        selection = self._default_selection(context, e_flip, len(drafts))
        if selection is None:
            return None
        selection_id = self._new_selection_id()
        drafts[selection_id] = selection
        self._set_active_selection(selection_id, selection)
        return selection_id

    def activate_selection(self, selection_id, *, notify=True):
        drafts = self.selections.get(self.page_id, {})
        if selection_id not in drafts:
            return False
        self._set_active_selection(selection_id, drafts[selection_id], notify=notify)
        return True

    def update_selection(self, selection_id, selection, *, notify=True):
        drafts = self.selections.get(self.page_id, {})
        if selection_id not in drafts:
            return False
        drafts[selection_id] = selection
        if selection_id == self.active_selection_id:
            self._set_active_selection(selection_id, selection, notify=notify)
        elif notify:
            self.selection_changed.emit()
        return True

    def set_operation(self, operation):
        self.operation = operation
        self.popup.hide()
        if self.selection is not None:
            self.set_selection(replace(self.selection, operation=operation), notify=False)

    def set_selection(self, selection, *, notify=True):
        selection_id = self.active_selection_id
        if selection is not None and self.page_id is not None:
            drafts = self.selections.setdefault(self.page_id, {})
            if selection_id is None:
                selection_id = self._new_selection_id()
            drafts[selection_id] = selection
        elif selection is None:
            selection_id = None
        self._set_active_selection(selection_id, selection, notify=notify)

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
        self._active_selection_ids.clear()
        self._context = None
        self._e_flip = False
        self.page_id = None
        self._set_active_selection(None, None, notify=False)

    def remove_page(self, page_id):
        self.selections.pop(page_id, None)
        self._active_selection_ids.pop(page_id, None)
        if self.page_id == page_id:
            self.popup.hide()
            self._set_active_selection(None, None, notify=False)
