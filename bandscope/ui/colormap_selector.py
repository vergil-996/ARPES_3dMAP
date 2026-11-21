# -*- coding: utf-8 -*-
"""输出色带选择：色阶卡片标题行 + 带渐变色样和勾选的可滚动单列菜单。

主界面不再保留独立的「色带选择」卡片：色阶卡片里的「输出色带」标题行
右侧显示当前色带名和下拉箭头，点击展开菜单。菜单每项绘制完整渐变色样和
名称，当前项带勾。Blender 工程导出窗口复用同一份 ``COLORMAP_OPTIONS``，
保证两处列表顺序一致。
"""
from __future__ import annotations

import numpy as np
from matplotlib import colormaps
from PyQt5.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

import bandscope.ui.theme as theme

#: 新视图与缺少色带值的旧状态使用的默认色带。
DEFAULT_COLORMAP = "magma"

#: 主界面菜单与 Blender 导出窗口共用的色带顺序：magma、coolwarm 置顶，
#: 其余保持原有相对顺序（coolwarm 仅出现一次）。
COLORMAP_OPTIONS = [
    "magma", "coolwarm", "inferno", "plasma", "viridis", "cividis", "turbo",
    "afmhot", "hot", "gist_heat", "RdBu_r", "seismic", "Spectral", "jet",
    "rainbow", "nipy_spectral", "cubehelix", "twilight", "twilight_shifted",
    "Greys", "gray", "bone", "pink", "spring", "summer", "autumn", "winter",
    "cool", "hsv", "terrain", "ocean", "gnuplot", "gnuplot2",
]


def _fill_gradient(painter, rect, cmap_name, *, radius=2.5):
    """在 rect 内绘制 cmap 的完整渐变；色样固定带一圈细边框，深浅主题都可辨。"""
    palette = colormaps.get_cmap(cmap_name)
    gradient = QLinearGradient(float(rect.left()), 0.0, float(rect.right()), 0.0)
    for position, rgba in zip(np.linspace(0.0, 1.0, 64), palette(np.linspace(0.0, 1.0, 64))):
        gradient.setColorAt(float(position), QColor.fromRgbF(*rgba))
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect), float(radius), float(radius))
    painter.setPen(QPen(QColor(theme.BORDER_HEX), 1.0))
    painter.setBrush(gradient)
    painter.drawPath(path)


class ColormapMenuRow(QWidget):
    """菜单中的一行：完整渐变色样 + 色带名 + 当前项勾选标记。"""

    activated = pyqtSignal(str)

    HEIGHT = 30
    PADDING = 8
    SWATCH_WIDTH = 54
    SWATCH_HEIGHT = 12
    CHECK_WIDTH = 20

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = str(name)
        self._current = False
        self._hovered = False
        self._pressed = False
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(f"色带 {self.name}")

    def is_current(self):
        return self._current

    def set_current(self, current):
        current = bool(current)
        if current == self._current:
            return
        self._current = current
        self.setAccessibleDescription("当前色带" if current else "")
        self.update()

    def is_hovered(self):
        return self._hovered

    def set_hovered(self, hovered):
        hovered = bool(hovered)
        if hovered == self._hovered:
            return
        self._hovered = hovered
        self.update()

    def sizeHint(self):
        return QSize(self.SWATCH_WIDTH + self.CHECK_WIDTH + 3 * self.PADDING + 72, self.HEIGHT)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.BG_4))
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 4.0, 4.0,
            )
        swatch = QRectF(
            float(self.PADDING),
            (self.height() - self.SWATCH_HEIGHT) / 2.0,
            float(self.SWATCH_WIDTH),
            float(self.SWATCH_HEIGHT),
        )
        _fill_gradient(painter, swatch, self.name)
        font = QFont(self.font())
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT_1))
        text_left = swatch.right() + 10.0
        text_width = max(
            1.0,
            self.width() - text_left - self.CHECK_WIDTH - self.PADDING - 2.0,
        )
        painter.drawText(
            QRectF(text_left, 0.0, text_width, float(self.height())),
            Qt.AlignLeft | Qt.AlignVCenter,
            self.name,
        )
        if self._current:
            check_font = QFont(font)
            check_font.setBold(True)
            painter.setFont(check_font)
            painter.setPen(QColor(theme.ACCENT))
            painter.drawText(
                QRectF(self.width() - self.CHECK_WIDTH - self.PADDING, 0.0,
                       float(self.CHECK_WIDTH), float(self.height())),
                Qt.AlignCenter,
                "✓",
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            if self.rect().contains(event.pos()):
                self.activated.emit(self.name)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self.set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.set_hovered(False)
        super().leaveEvent(event)


class ColormapMenu(QFrame):
    """可滚动的单列色带菜单；关闭（Esc／点外面）不产生选择。"""

    colormap_selected = pyqtSignal(str)

    CONTENT_MARGIN = 6
    ROW_SPACING = 2
    MIN_WIDTH = 208
    MAX_HEIGHT = 380
    GAP = 4

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("colormap_menu")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("输出色带选择")
        self._current_name = DEFAULT_COLORMAP
        self._active_index = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.viewport().setStyleSheet("background: transparent;")

        self._content = QWidget()
        self._content.setAutoFillBackground(False)
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(
            self.CONTENT_MARGIN, self.CONTENT_MARGIN,
            self.CONTENT_MARGIN, self.CONTENT_MARGIN,
        )
        content_layout.setSpacing(self.ROW_SPACING)
        self.rows = []
        for name in COLORMAP_OPTIONS:
            row = ColormapMenuRow(name, self._content)
            row.activated.connect(self._activate)
            content_layout.addWidget(row)
            self.rows.append(row)
        content_layout.addStretch(1)
        self.scroll.setWidget(self._content)
        outer.addWidget(self.scroll)

    # ------------------------------------------------------------------
    # 内容与定位
    # ------------------------------------------------------------------
    def row_for(self, name):
        for row in self.rows:
            if row.name == name:
                return row
        return None

    def set_current(self, name):
        self._current_name = str(name)
        for row in self.rows:
            row.set_current(row.name == self._current_name)
        self._active_index = max(
            0, next((index for index, row in enumerate(self.rows)
                     if row.name == self._current_name), 0),
        )

    def content_height(self):
        count = len(self.rows)
        return (2 * self.CONTENT_MARGIN + count * ColormapMenuRow.HEIGHT
                + max(0, count - 1) * self.ROW_SPACING)

    def present(self, anchor, current):
        """在 anchor（标题行）下方展开；空间不足时上翻并限制高度。"""
        self.set_current(current)
        self._fit_to(anchor)
        self.show()
        self.raise_()
        self.setFocus(Qt.PopupFocusReason)
        QTimer.singleShot(0, self._scroll_current_into_view)

    def _fit_to(self, anchor):
        anchor_origin = anchor.mapToGlobal(QPoint(0, 0))
        screen = QApplication.screenAt(anchor_origin) or anchor.screen()
        available = (
            screen.availableGeometry() if screen is not None
            else QRect(0, 0, 1920, 1080)
        )
        width = max(int(anchor.width()), self.MIN_WIDTH)
        width = min(width, available.width() - 4)
        content = self.content_height()
        minimum = ColormapMenuRow.HEIGHT + 2 * self.CONTENT_MARGIN
        below_top = anchor_origin.y() + anchor.height() + self.GAP
        space_below = available.bottom() - below_top + 1
        above_bottom = anchor_origin.y() - self.GAP
        space_above = above_bottom - available.top() + 1
        wanted = min(content, self.MAX_HEIGHT)
        if space_below >= wanted or space_below >= space_above:
            height = max(minimum, min(wanted, space_below))
            y = below_top
        else:
            height = max(minimum, min(wanted, space_above))
            y = above_bottom - height + 1
        y = max(available.top() + 2, y)
        x = max(available.left() + 2, min(anchor_origin.x(), available.right() - width - 1))
        self.resize(width, height)
        self.move(x, y)

    def _scroll_current_into_view(self):
        row = self.row_for(self._current_name)
        if row is None or not self.rows:
            return
        bar = self.scroll.verticalScrollBar()
        # 当前项滚到视口顶部，避免第一行被截成半行。
        bar.setValue(max(bar.minimum(), min(bar.maximum(), row.y() - self.CONTENT_MARGIN)))

    def _activate(self, name):
        self.close()
        self.colormap_selected.emit(str(name))

    def _set_active(self, index):
        if not self.rows:
            return
        index = max(0, min(len(self.rows) - 1, int(index)))
        self._active_index = index
        for position, row in enumerate(self.rows):
            row.set_hovered(position == index)
        self.scroll.ensureWidgetVisible(self.rows[index], 0, 0)

    # ------------------------------------------------------------------
    # 绘制与键盘
    # ------------------------------------------------------------------
    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(theme.BORDER_HEX), 1.0))
        painter.setBrush(QColor(theme.BG_3))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 7.0, 7.0)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            event.accept()
            return
        if event.key() in (Qt.Key_Down, Qt.Key_Up):
            self._set_active(self._active_index + (1 if event.key() == Qt.Key_Down else -1))
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.rows:
                self._activate(self.rows[self._active_index].name)
            event.accept()
            return
        super().keyPressEvent(event)


class OutputColormapSelector(QWidget):
    """「输出色带」标题行：左侧标题，右侧当前色带名与下拉箭头。

    只在用户从菜单选中另一条色带时发出 :attr:`colormap_changed`；
    程序化同步（切页、恢复配方）不会发出，避免触发额外的刷新。
    """

    colormap_changed = pyqtSignal(str)

    HEIGHT = 22

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current = DEFAULT_COLORMAP
        self._menu = None
        self._hovered = False
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip("点击展开色带列表；选择后立即刷新当前视图。")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 2, 0)
        layout.setSpacing(6)
        self.title_label = QLabel("输出色带", self)
        self.title_label.setStyleSheet(theme.field_label_qss())
        layout.addWidget(self.title_label, 0, Qt.AlignVCenter)
        layout.addStretch(1)
        self.name_label = QLabel(self._current, self)
        self.name_label.setStyleSheet(self._name_qss())
        layout.addWidget(self.name_label, 0, Qt.AlignVCenter)
        self.arrow_label = QLabel("▾", self)
        self.arrow_label.setStyleSheet(self._name_qss())
        layout.addWidget(self.arrow_label, 0, Qt.AlignVCenter)
        for label in (self.title_label, self.name_label, self.arrow_label):
            # 让整条标题行都是点击区域，而不是只有色带名文字本身。
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._sync_accessibility()

    @staticmethod
    def _name_qss():
        return f"color: {theme.TEXT_1}; background: transparent; font-size: 12px; font-weight: 600;"

    def _sync_accessibility(self):
        self.setAccessibleName(f"输出色带：{self._current}")
        self.setAccessibleDescription("点击展开色带列表")

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def current_colormap(self):
        return self._current

    def options(self):
        return list(COLORMAP_OPTIONS)

    def set_colormap(self, name, *, notify=False):
        """按名称同步标题与菜单勾选；未知名称忽略并返回 False。

        默认不发出 :attr:`colormap_changed`，供切页／恢复配方时静默同步。
        """
        name = str(name or DEFAULT_COLORMAP)
        if name not in COLORMAP_OPTIONS:
            return False
        changed = name != self._current
        self._current = name
        self.name_label.setText(name)
        self._sync_accessibility()
        if self._menu is not None:
            self._menu.set_current(name)
        if changed and notify:
            self.colormap_changed.emit(name)
        return changed

    # ------------------------------------------------------------------
    # 菜单
    # ------------------------------------------------------------------
    def menu(self):
        return self._menu

    def menu_visible(self):
        return self._menu is not None and self._menu.isVisible()

    def open_menu(self):
        if self._menu is None:
            self._menu = ColormapMenu(self)
            self._menu.colormap_selected.connect(self._on_menu_selected)
        self._menu.present(self, self._current)
        return self._menu

    def close_menu(self):
        if self._menu is not None and self._menu.isVisible():
            self._menu.close()

    def _on_menu_selected(self, name):
        self.set_colormap(name, notify=True)

    # ------------------------------------------------------------------
    # 交互与绘制
    # ------------------------------------------------------------------
    def _set_hovered(self, hovered):
        hovered = bool(hovered)
        if hovered != self._hovered:
            self._hovered = hovered
            self.update()

    def enterEvent(self, event):
        self._set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._set_hovered(False)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.open_menu()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Down):
            self.open_menu()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event):
        if not self._hovered:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BG_4))
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 4.0, 4.0,
        )
