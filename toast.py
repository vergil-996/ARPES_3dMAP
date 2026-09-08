# -*- coding: utf-8 -*-
"""非模态 Toast 通知 —— 替代轻量级 QMessageBox 提示。

用法::

    self.toast_manager = ToastManager(self)          # self 为主窗口
    self.toast_manager.show("切片已应用", level="success", title="完成")

- 主窗口顶部居中堆叠，最多同时 4 条，自动下沉让位
- 左缘 3px 语义色条：info 蓝 / success 绿 / warning 黄 / error 红
- 自动消失（info/success 2.5s，warning 4s，error 5s），点击立即关闭
- 淡入淡出动画，不阻塞任何操作
"""

from PyQt5.QtCore import QEvent, QEasingCurve, QObject, QPropertyAnimation, Qt, QTimer
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

import theme


_LEVEL_STYLE = {
    "info": (theme.INFO, "ℹ"),
    "success": (theme.SUCCESS, "✓"),
    "warning": (theme.WARNING, "⚠"),
    "error": (theme.DANGER, "✕"),
}

_LEVEL_DURATION_MS = {
    "info": 2500,
    "success": 2500,
    "warning": 4000,
    "error": 5000,
}

_MAX_TOASTS = 4
_TOP_MARGIN = 16
_SPACING = 8
_MAX_WIDTH = 480


class _Toast(QWidget):
    """单条通知。作为父窗口的子控件浮在内容上方。"""

    def __init__(self, parent, text, level, title=None):
        super().__init__(parent)
        color, icon_text = _LEVEL_STYLE.get(level, _LEVEL_STYLE["info"])

        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            """
            _Toast {
                background-color: %(BG3)s;
                border: 1px solid %(BORD)s;
                border-left: 3px solid %(LEVEL)s;
                border-radius: %(RS)spx;
            }
            QLabel { background: transparent; border: none; }
            """
            .replace("_Toast", "_Toast")
            % {**theme.QSS_TOKENS, "LEVEL": color, "RS": theme.R_S}
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 9, 14, 9)
        root.setSpacing(10)

        icon = QLabel(icon_text, self)
        icon.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: 700;")
        icon.setFixedWidth(16)
        icon.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        root.addWidget(icon)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        if title:
            title_label = QLabel(str(title), self)
            title_label.setStyleSheet(f"color: {theme.TEXT_1}; font-size: 13px; font-weight: 600;")
            text_col.addWidget(title_label)
        body = QLabel(str(text), self)
        body.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 12px;")
        body.setWordWrap(True)
        body.setMaximumWidth(_MAX_WIDTH - 80)
        text_col.addWidget(body)
        root.addLayout(text_col, 1)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.dismiss)

        # Word-wrapped QLabel's sizeHint favors a narrow column. Reserve enough
        # room for a normal sentence before calculating the wrapped height.
        text_width = max(body.fontMetrics().horizontalAdvance(line) for line in (str(text).splitlines() or [""]))
        self.setFixedWidth(min(_MAX_WIDTH, max(300, text_width + 68), max(1, parent.width() - 32)))
        self.adjustSize()

    def show_animated(self, duration_ms):
        self.show()
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._dismiss_timer.start(duration_ms)

    def dismiss(self):
        self._dismiss_timer.stop()
        self._fade.stop()
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(0.0)
        try:
            self._fade.finished.disconnect()
        except TypeError:
            pass
        self._fade.finished.connect(self._finish_close)
        self._fade.start()

    def _finish_close(self):
        self.close()
        self.deleteLater()

    def dismiss_now(self):
        """立即停止定时器/动画并销毁（用于窗口关闭或测试清理）。"""
        self._dismiss_timer.stop()
        self._fade.stop()
        self.close()
        self.deleteLater()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dismiss()
        super().mouseReleaseEvent(event)


class ToastManager(QObject):
    """管理一个窗口上的 Toast 堆叠与定位。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._parent = parent
        self._toasts = []
        parent.installEventFilter(self)

    def show(self, text, level="info", title=None, duration_ms=None):
        if level not in _LEVEL_STYLE:
            level = "info"
        if duration_ms is None:
            duration_ms = _LEVEL_DURATION_MS[level]

        toast = _Toast(self._parent, text, level, title=title)
        toast.destroyed.connect(lambda *_args, t=toast: self._forget(t))
        self._toasts.append(toast)

        while len(self._toasts) > _MAX_TOASTS:
            oldest = self._toasts.pop(0)
            oldest.dismiss()

        toast.show_animated(duration_ms)
        self._reposition()
        return toast

    def clear(self):
        """立即清空所有 Toast（窗口关闭或测试清理时调用）。"""
        toasts = list(self._toasts)
        self._toasts.clear()
        for toast in toasts:
            toast.dismiss_now()

    def _forget(self, toast):
        # 应用退出阶段管理器自身的 C++ 对象可能先于 Toast 被销毁，
        # 此时访问 self 的任何属性都会抛 RuntimeError，直接放弃收尾。
        from PyQt5.QtWidgets import QApplication
        try:
            if QApplication.closingDown():
                return
            if toast in self._toasts:
                self._toasts.remove(toast)
            self._reposition()
        except RuntimeError:
            pass

    def _reposition(self):
        try:
            parent_width = self._parent.width()
        except RuntimeError:
            return
        y = _TOP_MARGIN
        toolbar = getattr(self._parent, "toolbar", None)
        if toolbar is not None:
            y = max(y, toolbar.geometry().bottom() + 12)
        for toast in self._toasts:
            try:
                x = max(0, (parent_width - toast.width()) // 2)
                toast.move(x, y)
                toast.raise_()
                y += toast.height() + _SPACING
            except RuntimeError:
                continue

    def eventFilter(self, watched, event):
        if watched is self._parent and event.type() == QEvent.Resize:
            try:
                self._reposition()
            except RuntimeError:
                pass
        return False
