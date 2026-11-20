"""Compact, theme-aware path overview and per-segment editor."""
from __future__ import annotations

from typing import Optional, Sequence

from PyQt5.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QScrollBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import bandscope.ui.theme as theme


class PathNodeRenameDialog(QDialog):
    def __init__(self, parent, current_name: str):
        super().__init__(parent)
        self.setWindowTitle("重命名路径节点")
        self.setModal(True)
        self.setMinimumWidth(340)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        label = QLabel("节点名称", self)
        label.setStyleSheet(theme.field_label_qss())
        root.addWidget(label)
        self.edit = QLineEdit(str(current_name), self)
        self.edit.setFixedHeight(32)
        self.edit.setStyleSheet(theme.value_input_qss())
        self.edit.selectAll()
        root.addWidget(self.edit)
        self.error_label = QLabel("名称不能为空。", self)
        self.error_label.setStyleSheet(f"color: {theme.DANGER}; background: transparent;")
        self.error_label.hide()
        root.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.buttons.button(QDialogButtonBox.Ok).setText("确定")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        for button in self.buttons.buttons():
            button.setFixedHeight(32)
            theme.style_push_button(
                button,
                "primary" if self.buttons.buttonRole(button) == QDialogButtonBox.AcceptRole else "secondary",
            )
        self.buttons.accepted.connect(self._accept_name)
        self.buttons.rejected.connect(self.reject)
        self.edit.returnPressed.connect(self._accept_name)
        root.addWidget(self.buttons)

    def _accept_name(self):
        if not self.edit.text().strip():
            self.error_label.show()
            return
        self.accept()

    def node_name(self) -> str:
        return self.edit.text().strip()


class PathOverview(QWidget):
    segment_clicked = pyqtSignal(int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("path_overview")
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self._names = []
        self._lengths = []
        self._selected = set()
        self._node_positions = []
        self._viewport = None
        self._hit_regions = []

    def set_path(self, names: Sequence[str], lengths: Sequence[float], selected: Sequence[int]):
        self._names = [str(name) for name in names]
        self._lengths = [max(0.0, float(value)) for value in lengths]
        self._selected = {int(index) for index in selected}
        count = max(0, len(self._names) - 1)
        total = sum(self._lengths)
        positions = [0.0]
        if total > 0:
            positions.extend(value / total for value in np_cumsum(self._lengths))
        else:
            positions = [index / max(count, 1) for index in range(count + 1)]
        self._node_positions = [float(value) for value in positions]
        self.setToolTip("单击选择分段；Shift 单击跳选或取消")
        self.update()

    def set_viewport(self, viewport: Optional[Sequence[float]], full: Optional[Sequence[float]]):
        if viewport is None or full is None or len(viewport) != 2 or len(full) != 2:
            self._viewport = None
        else:
            low, high = sorted((float(full[0]), float(full[1])))
            start, end = sorted((float(viewport[0]), float(viewport[1])))
            span = high - low
            self._viewport = None if span <= 0 else (
                max(0.0, min(1.0, (start - low) / span)),
                max(0.0, min(1.0, (end - low) / span)),
            )
        self.update()

    def _track_rect(self) -> QRectF:
        return QRectF(14.0, 12.0, max(1.0, self.width() - 28.0), 12.0)

    def _segment_rects(self):
        rect = self._track_rect()
        return [
            QRectF(
                rect.left() + self._node_positions[index] * rect.width(),
                rect.top(),
                max(0.0, (self._node_positions[index + 1] - self._node_positions[index]) * rect.width()),
                rect.height(),
            )
            for index in range(len(self._lengths))
        ]

    def paintEvent(self, _event):
        if not self._lengths:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = self._track_rect()
        if self._viewport is not None:
            start, end = self._viewport
            visible = QRectF(
                track.left() + start * track.width(), 3.0,
                max(2.0, (end - start) * track.width()), 32.0,
            )
            painter.setPen(QPen(QColor(theme.BORDER_HEX), 1))
            viewport_fill = QColor(theme.ACCENT)
            viewport_fill.setAlphaF(0.07)
            painter.setBrush(viewport_fill)
            painter.drawRoundedRect(visible, 5, 5)

        self._hit_regions = []
        for index, rect in enumerate(self._segment_rects()):
            selected = index in self._selected
            hit = rect.adjusted(0.0, -7.0, 0.0, 7.0)
            if hit.width() < 18.0:
                extra = (18.0 - hit.width()) / 2.0
                hit.adjust(-extra, 0.0, extra, 0.0)
            self._hit_regions.append((hit, rect.center().x(), index))
            color = QColor(theme.ACCENT if selected else theme.TEXT_3)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            line = QRectF(rect.left(), 15.0 if selected else 16.5, max(rect.width(), 1.5), 5.0 if selected else 2.5)
            painter.drawRoundedRect(line, 2.0, 2.0)

        dense = [
            index > 0 and (self._node_positions[index] - self._node_positions[index - 1]) * track.width() < 48
            for index in range(len(self._node_positions))
        ]
        for index, position in enumerate(self._node_positions):
            x = track.left() + position * track.width()
            adjacent_selected = bool({index - 1, index} & self._selected)
            painter.setBrush(QColor(theme.ACCENT if adjacent_selected else theme.BG_2))
            painter.setPen(QPen(QColor(theme.ACCENT if adjacent_selected else theme.TEXT_2), 1.5))
            painter.drawEllipse(QRectF(x - 3.5, 13.5, 7.0, 7.0))
            font = QFont(self.font())
            font.setPixelSize(10)
            font.setBold(adjacent_selected)
            painter.setFont(font)
            painter.setPen(QColor(theme.ACCENT if adjacent_selected else theme.TEXT_2))
            y = 43 if dense[index] else 34
            label_rect = QRectF(x - 42, y - 8, 84, 16)
            painter.drawText(label_rect, Qt.AlignCenter, self._names[index])

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or not self._hit_regions:
            super().mousePressEvent(event)
            return
        x = float(event.pos().x())
        candidates = [item for item in self._hit_regions if item[0].contains(event.pos())]
        if not candidates:
            candidates = list(self._hit_regions)
        _, _, index = min(candidates, key=lambda item: abs(item[1] - x))
        self.segment_clicked.emit(index, bool(event.modifiers() & Qt.ShiftModifier))
        event.accept()

    def sizeHint(self):
        return QSize(240, 56)


def np_cumsum(values):
    result = []
    total = 0.0
    for value in values:
        total += float(value)
        result.append(total)
    return result


class MultiValueSpinBox(QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._multi_value = False
        self.lineEdit().textEdited.connect(self._clear_multi)

    def _clear_multi(self, *_args):
        self._multi_value = False
        self.update()

    def set_multi_value(self, multi: bool):
        self._multi_value = bool(multi)
        self.update()

    def textFromValue(self, value):
        return "多值" if self._multi_value else super().textFromValue(value)

    def stepBy(self, steps):
        self._clear_multi()
        super().stepBy(steps)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if self._multi_value:
            self.lineEdit().selectAll()


class PathSegmentBar(QFrame):
    selection_changed = pyqtSignal(list)
    thickness_changed = pyqtSignal(float)
    viewport_start_changed = pyqtSignal(float)
    layoutChanged = pyqtSignal()

    HEIGHT = 76
    NARROW_HEIGHT = 116

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("path_segment_bar")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._wrapped = False
        self._segment_count = 0
        self._selected = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 7, 12, 6)
        outer.setSpacing(3)
        outer.setSizeConstraint(QLayout.SetNoConstraint)
        self._outer = outer

        self.primary_row = QWidget(self)
        self.primary = QHBoxLayout(self.primary_row)
        self.primary.setContentsMargins(0, 0, 0, 0)
        self.primary.setSpacing(12)
        outer.addWidget(self.primary_row)

        self.title_group = QWidget(self.primary_row)
        title_layout = QHBoxLayout(self.title_group)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(8)
        self.accent = QFrame(self.title_group)
        self.accent.setFixedSize(3, 28)
        title_layout.addWidget(self.accent, 0, Qt.AlignVCenter)
        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        self.title_label = QLabel("路径分段", self.title_group)
        self.selection_label = QLabel("1 段已选", self.title_group)
        text_column.addWidget(self.title_label)
        text_column.addWidget(self.selection_label)
        title_layout.addLayout(text_column)
        self.primary.addWidget(self.title_group, 0, Qt.AlignVCenter)

        self.overview = PathOverview(self.primary_row)
        self.primary.addWidget(self.overview, 1)
        self.overview.segment_clicked.connect(self._on_segment_clicked)

        self.editor = QWidget(self.primary_row)
        self.editor.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        editor_layout = QHBoxLayout(self.editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(7)
        self.thickness_label = QLabel("切片厚度", self.editor)
        editor_layout.addWidget(self.thickness_label, 0, Qt.AlignVCenter)
        self.thickness = MultiValueSpinBox(self.editor)
        self.thickness.setRange(0.0, 1_000_000.0)
        self.thickness.setDecimals(4)
        self.thickness.setSingleStep(1.0)
        self.thickness.setKeyboardTracking(False)
        self.thickness.setAlignment(Qt.AlignCenter)
        self.thickness.setFixedSize(94, 30)
        self.thickness.setToolTip("选中段的法线方向总厚度；0 表示中心线")
        editor_layout.addWidget(self.thickness, 0, Qt.AlignVCenter)
        self.unit_label = QLabel("index", self.editor)
        self.unit_label.setMinimumWidth(38)
        editor_layout.addWidget(self.unit_label, 0, Qt.AlignVCenter)
        self.primary.addWidget(self.editor, 0, Qt.AlignVCenter)

        self.secondary_row = QWidget(self)
        secondary = QHBoxLayout(self.secondary_row)
        secondary.setContentsMargins(0, 0, 0, 0)
        secondary.setSpacing(0)
        outer.addWidget(self.secondary_row)
        self.secondary_row.hide()

        self.scrollbar = QScrollBar(Qt.Horizontal, self)
        self.scrollbar.setRange(0, 1000)
        self.scrollbar.setSingleStep(30)
        self.scrollbar.setFixedHeight(12)
        self.scrollbar.valueChanged.connect(self._on_scroll)
        outer.addWidget(self.scrollbar)
        self.scrollbar.hide()

        self.thickness.valueChanged.connect(self._on_thickness_value)
        self.thickness.editingFinished.connect(self._commit_thickness)
        self.update_theme()
        self.setFixedHeight(self.HEIGHT)

    def update_theme(self):
        self.setStyleSheet(
            f"QFrame#path_segment_bar {{ background: {theme.BG_2};"
            f"border: 1px solid {theme.BORDER_HEX}; border-radius: {theme.R_M}px; }}"
            f"QScrollBar:horizontal {{ background: {theme.BG_1}; border: none; border-radius: 4px; }}"
            f"QScrollBar::handle:horizontal {{ background: {theme.TEXT_3}; border-radius: 3px; min-width: 24px; }}"
            f"QScrollBar::handle:horizontal:hover {{ background: {theme.ACCENT}; }}"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
            "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }"
        )
        self.accent.setStyleSheet(f"background: {theme.ACCENT}; border: none; border-radius: 1px;")
        self.title_label.setStyleSheet(theme.card_title_qss())
        self.selection_label.setStyleSheet(f"color: {theme.TEXT_3}; background: transparent; font-size: 10px;")
        self.thickness_label.setStyleSheet(theme.field_label_qss())
        self.unit_label.setStyleSheet(
            f"color: {theme.TEXT_3}; background: transparent; font-size: 10px; font-family: {theme.FONT_MONO};"
        )
        self.thickness.setStyleSheet(theme.value_input_qss())
        self.overview.update()

    def _on_segment_clicked(self, index, additive):
        selected = set(self._selected)
        if not additive:
            selected = {index}
        elif index in selected:
            selected.remove(index)
        else:
            selected.add(index)
        self.selection_changed.emit(sorted(selected))

    def _on_thickness_value(self, value):
        if not self.thickness._multi_value and self.thickness.isEnabled():
            self.thickness_changed.emit(float(value))

    def _commit_thickness(self):
        if self.thickness.isEnabled() and not self.thickness._multi_value:
            self.thickness_changed.emit(float(self.thickness.value()))

    def _on_scroll(self, value):
        self.viewport_start_changed.emit(float(value) / 1000.0)

    def set_state(
        self,
        *,
        names: Sequence[str],
        lengths: Sequence[float],
        selected: Sequence[int],
        thickness: Optional[float],
        multi_thickness: bool,
        unit: str,
        step: float,
        viewport=None,
        full_viewport=None,
    ):
        self._segment_count = len(lengths)
        self._selected = list(selected)
        self.overview.set_path(names, lengths, selected)
        self.overview.set_viewport(viewport, full_viewport)
        self.selection_label.setText(
            f"已选 {len(selected)} / {len(lengths)} 段" if selected else "选择分段后可编辑"
        )
        self.editor.setEnabled(bool(selected))
        self.thickness.blockSignals(True)
        self.thickness.set_multi_value(bool(multi_thickness))
        if thickness is not None:
            self.thickness.setValue(max(0.0, float(thickness)))
        self.thickness.setSingleStep(max(float(step), 1e-6))
        self.thickness.blockSignals(False)
        self.unit_label.setText(str(unit or "源坐标"))
        self.thickness.setToolTip(
            f"选中段的法线方向总厚度（{unit or '源坐标单位'}）；0 表示中心线"
        )
        show_scroll = False
        if viewport is not None and full_viewport is not None:
            full_span = float(full_viewport[1]) - float(full_viewport[0])
            visible_span = float(viewport[1]) - float(viewport[0])
            show_scroll = full_span > 0 and visible_span < full_span - 1e-9
            if show_scroll:
                fraction = (float(viewport[0]) - float(full_viewport[0])) / full_span
                self.scrollbar.blockSignals(True)
                self.scrollbar.setValue(round(max(0.0, min(1.0, fraction)) * 1000))
                self.scrollbar.setPageStep(round(max(0.0, min(1.0, visible_span / full_span)) * 1000))
                self.scrollbar.blockSignals(False)
        self.scrollbar.setVisible(show_scroll)
        self._apply_wrap(self._should_wrap())

    def _should_wrap(self):
        return self.width() > 0 and self.width() < 610

    def _apply_wrap(self, wrapped):
        wrapped = bool(wrapped)
        if wrapped == self._wrapped:
            self.setFixedHeight((self.NARROW_HEIGHT if wrapped else self.HEIGHT) + (12 if not self.scrollbar.isHidden() else 0))
            return
        self._wrapped = wrapped
        self.primary.removeWidget(self.overview)
        if wrapped:
            self.secondary_row.layout().addWidget(self.overview)
            self.secondary_row.show()
        else:
            self.primary.insertWidget(1, self.overview, 1)
            self.secondary_row.hide()
        self.setFixedHeight((self.NARROW_HEIGHT if wrapped else self.HEIGHT) + (12 if not self.scrollbar.isHidden() else 0))
        self.updateGeometry()
        self.layoutChanged.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_wrap(self._should_wrap())

    def minimumSizeHint(self):
        return QSize(300, self.height())
