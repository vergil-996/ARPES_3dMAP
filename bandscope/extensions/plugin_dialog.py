# -*- coding: utf-8 -*-
"""本地扩展管理界面：导入、启用/停用、卸载。

安装根目录固定为 ``%LOCALAPPDATA%\\BandScope\\extensions``，与基础软件安装目录
分离。安装与卸载都需要重启才生效——正在使用的文件在 Windows 上删不掉，正在
运行的插件代码也不该在半途被换掉，界面会把这一点说清楚。

界面只调用 ``plugin_manager`` 的公开函数；包校验、兼容性判断和路径解析都在那
一层完成，这里不做任何路径拼接。
"""
from __future__ import annotations

from PyQt5.QtCore import QModelIndex, Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

import bandscope.ui.theme as theme
from bandscope.extensions.api import PluginError
from bandscope.extensions.plugin_manager import (
    PLUGIN_SUFFIX,
    PluginArchiveError,
    PluginManager,
    install_package,
)

PACKAGE_FILTER = f"BandScope 扩展包 (*{PLUGIN_SUFFIX})"

#: 列表里每列的含义与宽度。
COLUMNS = (
    ("名称", 150),
    ("版本", 70),
    ("状态", 240),
)


class PluginManagerDialog(QDialog):
    """单实例扩展管理面板。"""

    def __init__(self, window, session):
        # 父窗口只用于定位与提示；不是 QWidget 时（测试桩）退化成无父窗口，
        # 免得整个面板因为一个可选参数构造不出来。
        from PyQt5.QtWidgets import QWidget

        super().__init__(window if isinstance(window, QWidget) else None)
        self.window = window
        self.session = session
        self.manager: PluginManager = session.manager
        self.setWindowTitle("扩展管理")
        self.setModal(False)
        self.resize(720, 460)
        self.setStyleSheet(
            f"QDialog {{ background-color: {theme.BG_0}; }}"
            f"QLabel {{ color: {theme.TEXT_1}; background: transparent; }}"
            f"QTableWidget {{ color: {theme.TEXT_1}; background-color: {theme.BG_2};"
            f" gridline-color: {theme.BORDER_HEX}; border: 1px solid {theme.BORDER_HEX};"
            f" border-radius: 6px; }}"
            f"QHeaderView::section {{ color: {theme.TEXT_2}; background-color: {theme.BG_3};"
            f" border: none; padding: 6px; }}"
        )
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        title = QLabel("扩展管理")
        title.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {theme.TEXT_1};"
        )
        layout.addWidget(title)

        self.location_label = QLabel(f"安装位置：{self.manager.root}")
        self.location_label.setStyleSheet(f"color: {theme.TEXT_3}; font-size: 11px;")
        self.location_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.location_label)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels([name for name, _ in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # 选中行要立刻反映到按钮上：只在 refresh() 里更新会漏掉用户点击选择，
        # 按钮就一直停在打开面板时的「未选中」灰色状态。
        self.table.itemSelectionChanged.connect(self._update_buttons)
        header = self.table.horizontalHeader()
        for index, (_, width) in enumerate(COLUMNS):
            header.setSectionResizeMode(index, QHeaderView.Fixed)
            self.table.setColumnWidth(index, width)
        header.setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 12px;")
        layout.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_install = self._button("导入扩展包…", "primary", self.on_install)
        self.btn_toggle = self._button("启用 / 停用", "secondary", self.on_toggle)
        self.btn_uninstall = self._button("卸载", "danger", self.on_uninstall)
        self.btn_relaunch = self._button("关闭", "secondary", self.close)
        buttons.addWidget(self.btn_install)
        buttons.addWidget(self.btn_toggle)
        buttons.addWidget(self.btn_uninstall)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_relaunch)
        layout.addLayout(buttons)

    def _button(self, text, kind, slot):
        button = QPushButton(text, self)
        button.setCursor(Qt.PointingHandCursor)
        button.setMinimumHeight(32)
        theme.style_push_button(button, kind)
        button.clicked.connect(slot)
        return button

    # ------------------------------------------------------------------ 数据
    def refresh(self):
        records = self.manager.plugins()
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            name = record.manifest.name if record.manifest else record.plugin_id
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(record.version or "—"))
            status = QTableWidgetItem(self._status_text(record))
            if record.load_error:
                status.setForeground(Qt.red if not record.enabled else Qt.yellow)
            self.table.setItem(row, 2, status)
        self.table.resizeRowsToContents()
        self._update_hint(bool(records))
        self._update_buttons()

    @staticmethod
    def _status_text(record) -> str:
        if record.pending_removal:
            return "待重启卸载"
        if record.load_error:
            if getattr(record, "extra", {}).get("incompatible"):
                return f"不兼容：{record.load_error}"
            if record.enabled:
                return f"未加载：{record.load_error}"
            return "已停用（待重启生效）"
        return "已启用"

    def _update_hint(self, has_records):
        if has_records:
            self.hint.setText(
                "安装或卸载在重启后生效。扩展只改变显示效果，不修改原始数据、"
                "色阶与导出的数值。"
            )
        else:
            self.hint.setText(
                "尚未安装任何扩展。从项目 Release 下载与当前主程序匹配的扩展包，"
                "点「导入扩展包…」安装，重启后生效。"
            )

    def _update_buttons(self):
        record = self.selected_record()
        self.btn_toggle.setEnabled(record is not None)
        self.btn_uninstall.setEnabled(record is not None)

    def selected_record(self):
        row = self.table.currentRow()
        records = self.manager.plugins()
        if not 0 <= row < len(records):
            return None
        # 只认真正被选中的行：currentRow 可以被程序单独移动（清空选择、刷新后
        # 行数变少），而卸载不可撤销，不该在「什么都没选中」时可用。
        if not self.table.selectionModel().isRowSelected(row, QModelIndex()):
            return None
        return records[row]

    # ------------------------------------------------------------------ 操作
    def on_install(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入扩展包", "", PACKAGE_FILTER
        )
        if not path:
            return
        try:
            manifest = install_package(path, app_version=self.manager.app_version)
        except PluginArchiveError as exc:
            self._report("扩展包不合规范", str(exc))
            return
        except PluginError as exc:
            self._report("扩展不兼容", str(exc))
            return
        except Exception as exc:
            # 兜底：这个槽由按钮直接触发，未捕获异常会结束整个进程。压缩包损坏、
            # 加密包、磁盘错误都可能抛出 PluginError 之外的异常。
            self._report("安装失败", f"{type(exc).__name__}: {exc}")
            return
        # 只登记，不加载：新装的扩展要等重启才生效。
        self.manager.register_installed(manifest)
        self._report(
            "安装完成",
            f"「{manifest.name}」{manifest.version} 已安装。\n重启 BandScope 后生效。",
            warning=False,
        )
        self.refresh()

    def on_toggle(self):
        record = self.selected_record()
        if record is None:
            return
        target = not record.enabled
        try:
            self.manager.set_enabled(record.plugin_id, target)
        except PluginError as exc:
            self._report("操作失败", str(exc))
            return
        self._report(
            "需要重启",
            f"已{'启用' if target else '停用'}，重启 BandScope 后生效。",
            warning=False,
        )
        self.refresh()

    def on_uninstall(self):
        record = self.selected_record()
        if record is None:
            return
        name = record.manifest.name if record.manifest else record.plugin_id
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Question)
        confirm.setWindowTitle("卸载扩展")
        confirm.setText(f"要卸载「{name}」吗？")
        confirm.setInformativeText("卸载在下次启动时执行，之前不会影响当前会话。")
        confirm.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        confirm.setDefaultButton(QMessageBox.No)
        if confirm.exec_() != QMessageBox.Yes:
            return
        try:
            self.manager.note_removal(record.plugin_id)
        except PluginError as exc:
            self._report("卸载失败", str(exc))
            return
        self._report(
            "需要重启",
            f"「{name}」将在下次启动时移除。",
            warning=False,
        )
        self.refresh()

    def _report(self, title, text, *, warning=True):
        show = self.window.__dict__.get("_show_message")
        if callable(show):
            show(title, text, QMessageBox.Warning if warning else QMessageBox.Information)
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning if warning else QMessageBox.Information)
        box.setWindowTitle(title)
        box.setText(text)
        box.exec_()

    # ------------------------------------------------------------------ 事件
    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def changeEvent(self, event):
        from PyQt5.QtCore import QEvent

        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self.refresh()
        super().changeEvent(event)


def open_plugin_manager(window, session) -> PluginManagerDialog:
    """打开（或复用）扩展管理面板。"""
    existing = getattr(window, "_plugin_dialog", None)
    if existing is not None:
        try:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing
        except RuntimeError:
            pass
    dialog = PluginManagerDialog(window, session)
    window._plugin_dialog = dialog
    dialog.show()
    return dialog
