# -*- coding: utf-8 -*-
"""插件管理界面：安装、启用/停用、卸载与失败恢复。

安装根目录固定为 ``%LOCALAPPDATA%\\BandScope\\extensions``，与基础软件安装目录
分离。所有插件管理操作都在这个「插件管理」窗口内完成——顶部工具栏按钮打开、
重复点击激活同一个窗口；安装、启用/停用、卸载与恢复都**重启后生效**：正在使用
的文件在 Windows 上删不掉，正在运行的插件代码也不该在半途被换掉，界面会把这一
点说清楚。

界面只调用 ``PluginManager`` 的公开方法；包校验、兼容性判断、路径解析和登记表
事务都在下层完成，这里不做任何路径拼接。选中项按插件 id 定位，列表刷新重排不会
让操作落到别的插件上。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from PyQt5.QtCore import QModelIndex, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import bandscope.ui.theme as theme
from bandscope.extensions.catalog import (
    STATUS_INCOMPATIBLE,
    STATUS_INSTALLABLE,
    STATUS_NOT_IN_CATALOG,
    STATUS_UPDATE_AVAILABLE,
    STATUS_UP_TO_DATE,
    CatalogClient,
    CatalogError,
    CatalogMatch,
    cached_verified_at,
    match_catalog_entry,
    read_cached_catalog,
    store_cached_catalog,
)

import bandscope.ui.theme as theme
from bandscope.extensions.api import PluginCompatibilityError, PluginError
from bandscope.extensions.plugin_manager import (
    PLUGIN_SUFFIX,
    InstallSource,
    PluginArchiveError,
    PluginManager,
    inspect_archive,
)
from bandscope.extensions.trust import (
    SOURCE_LOCAL,
    SOURCE_OFFICIAL,
    describe_source,
    inspect_package_source,
)

PACKAGE_FILTER = f"BandScope 插件包 (*{PLUGIN_SUFFIX})"

#: 列表里每列的含义与宽度。
COLUMNS = (
    ("名称", 150),
    ("版本", 70),
    ("状态", 300),
)

#: 官方插件页的列。
CATALOG_COLUMNS = (
    ("名称", 150),
    ("版本", 90),
    ("状态", 290),
)

#: 目录来源文案。
CATALOG_SOURCE_NETWORK = "network"
CATALOG_SOURCE_CACHE = "cache"

RESTART_HINT = "安装、启用/停用或卸载后，请重启 BandScope 使设置完整生效。"


def restore_target_version(record):
    """“恢复上一版本”会选中的版本；没有可恢复的版本时返回 None。

    规则与 ``plugin_store.apply_restore_requests`` 一致：失败候选不同于
    ``last_good`` 时恢复 ``last_good``；当前候选已经是 ``last_good`` 时才
    选择 ``previous_good``。
    """
    if record is None:
        return None
    if record.last_good_version and record.last_good_digest != record.digest:
        return record.last_good_version
    if record.previous_good_version and record.previous_good_digest != record.digest:
        return record.previous_good_version
    return None


def status_text(record) -> str:
    """一行状态文案：当前运行与待生效操作分开说清楚。"""
    if record.unconfirmed_reason:
        return f"条目无法确认（只读）：{record.unconfirmed_reason}"
    if record.running:
        current = record.running_version or record.version
        pending = []
        if record.version and current and record.version != current:
            pending.append(f"待重启更新至 {record.version}")
        if not record.enabled:
            pending.append("待重启停用")
        if record.pending_removal:
            pending.append("待重启卸载")
        if pending:
            return f"运行中（当前 {current}）；" + "；".join(pending)
        return "运行中"
    if record.pending_removal:
        return "待重启卸载"
    if record.load_error:
        if getattr(record, "extra", {}).get("incompatible"):
            return f"不兼容：{record.load_error}"
        return f"未加载：{record.load_error}"
    if not record.enabled:
        return "已停用"
    return "待重启加载"


class PluginManagerDialog(QDialog):
    """单实例插件管理面板。"""

    def __init__(self, window, session, *, catalog_client=None):
        # 父窗口只用于定位与提示；不是 QWidget 时（测试桩）退化成无父窗口，
        # 免得整个面板因为一个可选参数构造不出来。
        super().__init__(window if isinstance(window, QWidget) else None)
        self.window = window
        self.session = session
        self.manager: PluginManager = session.manager
        self.setWindowTitle("插件管理")
        self.setModal(False)
        # QDialog 在 Windows 默认带标题栏「这是什么？」问号按钮；本面板没有
        # 任何 whats-this 文案，点它只会进入空帮助模式，直接去掉。
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.resize(760, 560)
        self.setStyleSheet(
            f"QDialog {{ background-color: {theme.BG_0}; }}"
            f"QLabel {{ color: {theme.TEXT_1}; background: transparent; }}"
            f"QTableWidget {{ color: {theme.TEXT_1}; background-color: {theme.BG_2};"
            f" gridline-color: {theme.BORDER_HEX}; border: 1px solid {theme.BORDER_HEX};"
            f" border-radius: 6px; }}"
            f"QHeaderView::section {{ color: {theme.TEXT_2}; background-color: {theme.BG_3};"
            f" border: none; padding: 6px; }}"
        )
        # 官方目录：后台线程取，主线程更新界面；关闭窗口时取消。
        self.catalog_client = catalog_client or CatalogClient()
        self._catalog = None
        self._catalog_source = ""
        self._catalog_error = ""
        self._catalog_verified_at = ""
        self._catalog_rows = []
        self._catalog_requested = False
        self._catalog_tab_index = 1
        self._catalog_thread = None
        self._download_thread = None
        self._download_dir = None
        self._build_ui()
        self.refresh()
        self._load_cached_catalog()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        title = QLabel("插件管理")
        title.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {theme.TEXT_1};"
        )
        layout.addWidget(title)

        self.location_label = QLabel(f"安装位置：{self.manager.root}")
        self.location_label.setStyleSheet(f"color: {theme.TEXT_3}; font-size: 11px;")
        self.location_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.location_label)

        self.tabs = QTabWidget(self)
        self.tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: 1px solid {theme.BORDER_HEX};"
            f" border-radius: 6px; background-color: {theme.BG_1}; }}"
            f"QTabBar::tab {{ color: {theme.TEXT_2}; background: transparent;"
            f" padding: 6px 14px; }}"
            f"QTabBar::tab:selected {{ color: {theme.TEXT_1}; font-weight: 600; }}"
        )
        self.tabs.addTab(self._build_installed_tab(), "已安装")
        self._catalog_tab_index = self.tabs.addTab(self._build_catalog_tab(), "官方插件")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_install = self._button("安装插件…", "primary", self.on_install)
        self.btn_toggle = self._button("启用 / 停用", "secondary", self.on_toggle)
        self.btn_uninstall = self._button("卸载", "danger", self.on_uninstall)
        self.btn_retry = self._button("重试加载", "secondary", self.on_retry)
        self.btn_restore = self._button("恢复上一版本", "secondary", self.on_restore)
        self.btn_relaunch = self._button("关闭", "secondary", self.close)
        buttons.addWidget(self.btn_install)
        buttons.addWidget(self.btn_toggle)
        buttons.addWidget(self.btn_uninstall)
        buttons.addWidget(self.btn_retry)
        buttons.addWidget(self.btn_restore)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_relaunch)
        layout.addLayout(buttons)

    def _build_installed_tab(self):
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(10)

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

        self.details = QLabel("")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details.setMinimumHeight(64)
        self.details.setStyleSheet(
            f"color: {theme.TEXT_2}; font-size: 12px; background-color: {theme.BG_2};"
            f" border: 1px solid {theme.BORDER_HEX}; border-radius: 6px; padding: 8px;"
        )
        layout.addWidget(self.details)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 12px;")
        layout.addWidget(self.hint)
        return page

    def _build_catalog_tab(self):
        """官方插件页：刷新目录、查看兼容结论、安装或更新。"""
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.btn_catalog_refresh = self._button("刷新目录", "secondary", self.on_catalog_refresh)
        top.addWidget(self.btn_catalog_refresh)
        self.catalog_status = QLabel("")
        self.catalog_status.setWordWrap(True)
        self.catalog_status.setStyleSheet(f"color: {theme.TEXT_3}; font-size: 11px;")
        top.addWidget(self.catalog_status, 1)
        layout.addLayout(top)

        self.catalog_table = QTableWidget(0, len(CATALOG_COLUMNS), self)
        self.catalog_table.setHorizontalHeaderLabels([name for name, _ in CATALOG_COLUMNS])
        self.catalog_table.verticalHeader().setVisible(False)
        self.catalog_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.catalog_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.catalog_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.catalog_table.itemSelectionChanged.connect(self._update_catalog_buttons)
        header = self.catalog_table.horizontalHeader()
        for index, (_, width) in enumerate(CATALOG_COLUMNS):
            header.setSectionResizeMode(index, QHeaderView.Fixed)
            self.catalog_table.setColumnWidth(index, width)
        header.setStretchLastSection(True)
        layout.addWidget(self.catalog_table, 1)

        self.catalog_details = QLabel("")
        self.catalog_details.setWordWrap(True)
        self.catalog_details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.catalog_details.setMinimumHeight(64)
        self.catalog_details.setStyleSheet(
            f"color: {theme.TEXT_2}; font-size: 12px; background-color: {theme.BG_2};"
            f" border: 1px solid {theme.BORDER_HEX}; border-radius: 6px; padding: 8px;"
        )
        layout.addWidget(self.catalog_details)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.btn_catalog_install = self._button("安装 / 更新…", "primary", self.on_catalog_install)
        bottom.addWidget(self.btn_catalog_install)
        bottom.addStretch(1)
        layout.addLayout(bottom)
        return page

    def _button(self, text, kind, slot):
        button = QPushButton(text, self)
        button.setCursor(Qt.PointingHandCursor)
        button.setMinimumHeight(32)
        theme.style_push_button(button, kind)
        button.clicked.connect(slot)
        return button

    # ------------------------------------------------------------------ 数据
    def refresh(self, *, select=None):
        previous = self._selected_plugin_id()
        records = self.manager.plugins()
        self.table.blockSignals(True)
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            name_item = QTableWidgetItem(
                record.manifest.name if record.manifest else (record.plugin_id)
            )
            # 名称项保存插件 id：刷新重排后按 id 恢复选择，操作也按 id 定位，
            # 不依赖易变的行号。
            name_item.setData(Qt.UserRole, record.plugin_id)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(record.version or "—"))
            status = QTableWidgetItem(status_text(record))
            if record.unconfirmed_reason:
                status.setForeground(QColor(theme.TEXT_3))
            elif record.load_error and record.enabled and not record.running:
                status.setForeground(Qt.red)
            elif record.pending_removal or record.restore_pending or record.failed_candidate:
                status.setForeground(Qt.yellow)
            self.table.setItem(row, 2, status)
        target = select if select is not None else previous
        if target is not None:
            for row, record in enumerate(records):
                if record.plugin_id == target:
                    self.table.selectRow(row)
                    break
        self.table.blockSignals(False)
        self.table.resizeRowsToContents()
        self._update_hint(bool(records))
        self._update_buttons()

    def _update_hint(self, has_records):
        if self.manager.registry_error:
            self.hint.setStyleSheet(f"color: {theme.DANGER}; font-size: 12px;")
            self.hint.setText(
                f"插件登记表异常：{self.manager.registry_error} "
                "当前为只读模式，修复前不能安装或修改插件。"
            )
            return
        self.hint.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 12px;")
        parts = []
        if getattr(self.manager, "cleanup_deferred", False):
            parts.append(
                "检测到另一个 BandScope 实例正在运行：待执行的卸载与清理已推迟，"
                "关闭其他窗口后重启即可执行。"
            )
        if has_records:
            parts.append(
                RESTART_HINT
                + "扩展只改变显示效果，不修改原始数据、色阶与导出的数值。"
            )
        else:
            parts.append(
                "尚未安装任何插件。从项目 Release 下载与当前主程序匹配的插件包，"
                "点「安装插件…」安装，重启后生效。"
            )
        self.hint.setText(" ".join(parts))

    def _update_buttons(self):
        record = self.selected_record()
        read_only = bool(self.manager.registry_error)
        usable = (
            record is not None
            and not read_only
            and not record.unconfirmed_reason
        )
        pending = bool(record is not None and record.pending_removal)
        self.btn_install.setEnabled(not read_only)
        self.btn_toggle.setEnabled(usable and not pending)
        self.btn_uninstall.setEnabled(usable and not pending)
        self.btn_retry.setEnabled(
            usable and not pending and record.failed_candidate is not None
        )
        self.btn_restore.setEnabled(
            usable and not pending and restore_target_version(record) is not None
        )
        enabled = bool(record is not None and record.enabled)
        self.btn_toggle.setText("停用" if enabled else "启用")
        self._update_details(record)

    def _update_details(self, record):
        if record is None:
            self.details.setText("选中一个插件查看详情。")
            return
        lines = []
        if record.running:
            lines.append(f"当前运行：{record.running_version or record.version}")
        if record.version:
            lines.append(f"期望版本（重启后）：{record.version}")
        if record.last_good_version:
            lines.append(f"上次成功：{record.last_good_version}")
        if record.previous_good_version:
            lines.append(f"可回退版本：{record.previous_good_version}")
        if record.pending_removal:
            lines.append("已登记卸载：下次启动时移除。")
        if record.restore_pending:
            lines.append("已登记恢复上一版本：重启后执行。")
        if record.failed_candidate:
            lines.append(
                f"失败候选 {record.failed_candidate.get('version', '?')}："
                f"{record.failed_candidate.get('message', '')}"
            )
        if record.last_operation_error:
            lines.append(f"最近操作错误：{record.last_operation_error}")
        session_error = self._session_errors().get(record.plugin_id)
        if session_error:
            lines.append(f"运行期错误：{session_error}")
        source = record.source or {}
        if source:
            lines.append(f"来源：{describe_source(source)}")
        self.details.setText("\n".join(lines) if lines else "没有更多信息。")

    def _session_errors(self):
        errors = getattr(self.session, "errors", None)
        if callable(errors):
            try:
                return dict(errors() or {})
            except Exception:
                return {}
        return {}

    def _selected_plugin_id(self):
        row = self.table.currentRow()
        if not 0 <= row < self.table.rowCount():
            return None
        # 只认真正被选中的行：currentRow 可以被程序单独移动（清空选择、刷新后
        # 行数变少），而卸载不可撤销，不该在「什么都没选中」时可用。
        if not self.table.selectionModel().isRowSelected(row, QModelIndex()):
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def selected_record(self):
        plugin_id = self._selected_plugin_id()
        if plugin_id is None:
            return None
        # 按 id 定位记录，行序变化不会把操作落到别的插件上。
        return self.manager.record(plugin_id)

    # ------------------------------------------------------------------ 操作
    def on_install(self):
        if self.manager.registry_error:
            self._report("插件管理不可用", self.manager.registry_error)
            return
        path, _ = QFileDialog.getOpenFileName(self, "安装插件包", "", PACKAGE_FILTER)
        if not path:
            return
        try:
            manifest, _prefix = inspect_archive(Path(path))
        except PluginArchiveError as exc:
            self._report("插件包不合规范", str(exc))
            return
        except PluginError as exc:
            self._report("安装失败", str(exc))
            return
        except Exception as exc:
            self._report("安装失败", f"{type(exc).__name__}: {exc}")
            return

        existing = self.manager.record(manifest.plugin_id)
        if existing is not None and existing.pending_removal:
            if not self._confirm_cancel_uninstall(manifest):
                return
        source = self._resolve_install_source(Path(path), manifest, existing)
        if source is None:
            return
        try:
            installed = self.manager.install(path, source=source)
        except PluginArchiveError as exc:
            self._report("插件包不合规范", str(exc))
            return
        except PluginCompatibilityError as exc:
            self._report("插件不兼容", str(exc))
            return
        except PluginError as exc:
            self._report("安装失败", str(exc))
            return
        except Exception as exc:
            # 兜底：这个槽由按钮直接触发，未捕获异常会结束整个进程。压缩包损坏、
            # 加密包、磁盘错误都可能抛出 PluginError 之外的异常。
            self._report("安装失败", f"{type(exc).__name__}: {exc}")
            return
        # 只登记候选，不加载：新装的插件要等重启才生效。
        self.refresh(select=installed.plugin_id)
        self._report(
            "安装完成",
            f"「{installed.name}」{installed.version} 已安装。\n重启 BandScope 后生效。",
            warning=False,
        )

    def _resolve_install_source(self, path, manifest, existing):
        """确定本次安装的来源，并按需向用户确认；取消返回 None。

        来源判定只看内置公钥的验签结果：签名有效是官方，没有签名是本地未验证，
        有签名但验不过直接拒绝（不降级）。安装事务里会再验一次，这里只是为了
        在提交前把选择交给用户。
        """
        detected = inspect_package_source(path)
        if detected.rejected:
            self._report("插件包签名校验失败", detected.reason)
            return None
        if detected.verified:
            return InstallSource(kind=SOURCE_OFFICIAL)
        downgrade = bool(
            existing is not None
            and existing.source.get("kind") == SOURCE_OFFICIAL
            and existing.source.get("verified")
        )
        if not self._confirm_unverified(manifest, existing, downgrade=downgrade):
            return None
        return InstallSource(
            kind=SOURCE_LOCAL,
            accepted_unverified=True,
            accepted_downgrade=downgrade,
        )

    def _confirm_unverified(self, manifest, existing, *, downgrade: bool) -> bool:
        """未签名包的安装确认；默认取消，替换官方来源时文案更重。"""
        box = QMessageBox(self)
        box.setWindowTitle("安装未验证的插件包")
        if downgrade:
            box.setIcon(QMessageBox.Critical)
            box.setText(f"「{manifest.name}」将替换官方已验证的插件。")
            box.setInformativeText(
                "当前安装的来源是官方已验证"
                f"（{existing.source.get('key_id') or '内置密钥'}）。\n"
                "继续安装会把来源换成未验证的本地包，之后不再视为官方来源。\n"
                "默认取消。"
            )
        else:
            box.setIcon(QMessageBox.Warning)
            box.setText(f"「{manifest.name}」{manifest.version} 没有官方签名。")
            box.setInformativeText(
                "无法确认该插件包的来源与完整性，安装后不会显示为官方已验证。\n"
                "默认取消。"
            )
        accept = box.addButton("仍要安装", QMessageBox.DestructiveRole)
        cancel = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec_()
        return box.clickedButton() is accept

    def _confirm_cancel_uninstall(self, manifest) -> bool:
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Question)
        confirm.setWindowTitle("撤销卸载")
        confirm.setText(
            f"「{manifest.name}」已有待执行的卸载请求。"
        )
        confirm.setInformativeText("重新安装将撤销该卸载请求，是否继续？")
        confirm.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        confirm.setDefaultButton(QMessageBox.No)
        return confirm.exec_() == QMessageBox.Yes

    def on_toggle(self):
        record = self.selected_record()
        if record is None or record.unconfirmed_reason or record.pending_removal:
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
        if record is None or record.unconfirmed_reason or record.pending_removal:
            return
        name = record.manifest.name if record.manifest else record.plugin_id
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Question)
        confirm.setWindowTitle("卸载插件")
        confirm.setText(f"要卸载「{name}」吗？")
        confirm.setInformativeText("卸载将在下次启动 BandScope 时执行。")
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

    def on_retry(self):
        record = self.selected_record()
        if record is None or record.unconfirmed_reason or record.pending_removal:
            return
        if record.failed_candidate is None:
            return
        try:
            self.manager.retry_load(record.plugin_id)
        except PluginError as exc:
            self._report("操作失败", str(exc))
            return
        self._report(
            "需要重启",
            f"已登记重试加载「{record.plugin_id}」，重启 BandScope 后执行。",
            warning=False,
        )
        self.refresh()

    def on_restore(self):
        record = self.selected_record()
        if record is None or record.unconfirmed_reason or record.pending_removal:
            return
        target = restore_target_version(record)
        if target is None:
            return
        try:
            self.manager.request_restore(record.plugin_id)
        except PluginError as exc:
            self._report("操作失败", str(exc))
            return
        self._report(
            "需要重启",
            f"已登记恢复「{record.plugin_id}」到 {target}，重启 BandScope 后执行。",
            warning=False,
        )
        self.refresh()

    def _report(self, title, text, *, warning=True):
        # 主窗口的 _show_message 走非模态 Toast；测试桩或精简宿主没有这个方法时
        # 回退到模态对话框。
        show = getattr(self.window, "_show_message", None)
        if callable(show):
            show(title, text, QMessageBox.Warning if warning else QMessageBox.Information)
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning if warning else QMessageBox.Information)
        box.setWindowTitle(title)
        box.setText(text)
        box.exec_()

    # ------------------------------------------------------------------ 官方目录
    def _load_cached_catalog(self):
        """开面板时先用缓存填一遍：断网也能浏览上次验证通过的目录。"""
        cached = read_cached_catalog()
        if cached is None:
            self._render_catalog()
            return
        self._catalog = cached
        self._catalog_source = CATALOG_SOURCE_CACHE
        self._catalog_verified_at = cached_verified_at()
        self._render_catalog()

    def _on_tab_changed(self, index):
        """打开官方页时后台取一次目录；不弹窗、不自动下载安装。"""
        if index != self._catalog_tab_index or self._catalog_requested:
            return
        self._catalog_requested = True
        self._start_catalog_fetch()

    def on_catalog_refresh(self):
        self._catalog_requested = True
        self._start_catalog_fetch()

    def _start_catalog_fetch(self):
        thread = self._catalog_thread
        if thread is not None and thread.isRunning():
            return
        self._catalog_error = ""
        self.btn_catalog_refresh.setEnabled(False)
        self.catalog_status.setText("正在获取官方目录…")
        thread = _CatalogFetchThread(self.catalog_client, self)
        thread.completed.connect(self._on_catalog_fetched)
        thread.failed.connect(self._on_catalog_failed)
        thread.finished.connect(self._on_catalog_thread_finished)
        self._catalog_thread = thread
        thread.start()

    def _on_catalog_fetched(self, fetched):
        stored = store_cached_catalog(
            fetched.catalog,
            fetched.payload,
            fetched.signature,
            verified_at=fetched.verified_at,
        )
        self._catalog = fetched.catalog
        self._catalog_source = CATALOG_SOURCE_NETWORK
        self._catalog_verified_at = fetched.verified_at
        self._catalog_error = ""
        if not stored:
            self._catalog_error = (
                "获取到的目录修订号低于本机已接受的版本，已保留较新的缓存。"
            )
        self._render_catalog()

    def _on_catalog_failed(self, message):
        self._catalog_error = message
        # 取新目录失败不影响浏览缓存；但若本机没有任何可用目录，就不能安装。
        self._render_catalog()

    def _on_catalog_thread_finished(self):
        thread = self._catalog_thread
        self._catalog_thread = None
        if thread is not None:
            thread.deleteLater()
        self.btn_catalog_refresh.setEnabled(True)

    def _installed_record(self, plugin_id):
        return self.manager.record(plugin_id)

    def _render_catalog(self):
        """按目录与已安装状态重画官方页表格与说明。"""
        catalog = self._catalog
        rows = []
        if catalog is not None:
            installed = {record.plugin_id: record for record in self.manager.plugins()}
            plugin_ids = sorted({entry.plugin_id for entry in catalog.entries} | set(installed))
            for plugin_id in plugin_ids:
                record = installed.get(plugin_id)
                match = match_catalog_entry(
                    catalog,
                    plugin_id,
                    installed_version=(record.version if record is not None else ""),
                    app_version=catalog.host_version,
                )
                rows.append((plugin_id, match, record))
        self._catalog_rows = rows

        previous = self._selected_catalog_plugin_id()
        self.catalog_table.blockSignals(True)
        self.catalog_table.setRowCount(len(rows))
        for row, (plugin_id, match, record) in enumerate(rows):
            entry = match.entry
            name = ""
            if entry is not None:
                name = entry.name
            elif record is not None and record.manifest is not None:
                name = record.manifest.name
            name_item = QTableWidgetItem(name or plugin_id)
            name_item.setData(Qt.UserRole, plugin_id)
            self.catalog_table.setItem(row, 0, name_item)
            self.catalog_table.setItem(row, 1, QTableWidgetItem(entry.version if entry else "—"))
            status = QTableWidgetItem(_catalog_status_text(match, record))
            if match.status == STATUS_INCOMPATIBLE:
                status.setForeground(Qt.red)
            elif match.status in (STATUS_INSTALLABLE, STATUS_UPDATE_AVAILABLE):
                status.setForeground(QColor(theme.ACCENT))
            self.catalog_table.setItem(row, 2, status)
        # 刷新（含取目录后重画）按 id 恢复选中：行序变化不会把操作落到别的插件上。
        if previous is not None:
            for row, (plugin_id, _match, _record) in enumerate(rows):
                if plugin_id == previous:
                    self.catalog_table.selectRow(row)
                    break
        self.catalog_table.blockSignals(False)
        self.catalog_table.resizeRowsToContents()
        # 先写状态行再刷新按钮/详情：状态行会把详情区重置成占位文案，顺序反了会
        # 把刚选中的那行说明冲掉。
        self._update_catalog_status()
        self._update_catalog_buttons()

    def _update_catalog_status(self):
        if self._catalog is None:
            text = "尚未获取官方目录。"
            if self._catalog_error:
                text = f"无法获取官方目录：{self._catalog_error}"
            self.catalog_status.setText(text)
            return
        if self._catalog_source == CATALOG_SOURCE_NETWORK:
            prefix = "目录已更新"
        else:
            prefix = "离线：显示上次验证通过的缓存"
        stamp = f"（{self._catalog_verified_at}）" if self._catalog_verified_at else ""
        text = f"{prefix}{stamp}；目标宿主 {self._catalog.host_version}。"
        if self._catalog_error:
            text += f" {self._catalog_error}"
        self.catalog_status.setText(text)
        self.catalog_details.setText("选中一行查看说明。")

    def _selected_catalog_plugin_id(self):
        row = self.catalog_table.currentRow()
        if not 0 <= row < len(self._catalog_rows):
            return None
        if not self.catalog_table.selectionModel().isRowSelected(row, QModelIndex()):
            return None
        item = self.catalog_table.item(row, 0)
        return item.data(Qt.UserRole) if item is not None else None

    def selected_catalog_row(self):
        plugin_id = self._selected_catalog_plugin_id()
        for entry in self._catalog_rows:
            if entry[0] == plugin_id:
                return entry
        return None

    def _update_catalog_buttons(self):
        row = self.selected_catalog_row()
        match = row[1] if row is not None else None
        self.btn_catalog_install.setEnabled(bool(match is not None and match.can_install))
        self.btn_catalog_install.setText(
            "更新…" if match is not None and match.status == STATUS_UPDATE_AVAILABLE else "安装…"
        )
        self._update_catalog_details(row)

    def _update_catalog_details(self, row):
        if row is None:
            self.catalog_details.setText("选中一个插件查看官方目录说明。")
            return
        plugin_id, match, record = row
        lines = []
        if match.entry is not None:
            entry = match.entry
            lines.append(f"{entry.name} {entry.version}")
            lines.append(f"应用范围：{entry.requires_app}；接口版本 {entry.api_version}")
            lines.append(f"接口能力：{'、'.join(entry.capabilities) or '（无）'}")
            lines.append("来源：官方目录（已签名）" if entry.signed else "来源：官方目录（未签名，需确认）")
            if entry.notes:
                lines.append(entry.notes)
        if record is not None:
            lines.append(f"本机已安装：{record.version or '—'}")
        if match.reason:
            lines.append(match.reason)
        self.catalog_details.setText("\n".join(lines))

    def on_catalog_install(self):
        row = self.selected_catalog_row()
        if row is None:
            return
        plugin_id, match, record = row
        entry = match.entry
        if entry is None or not match.can_install:
            return
        if not self._confirm_catalog_install(entry, record):
            return
        self._start_package_download(entry)

    def _confirm_catalog_install(self, entry, record) -> bool:
        """在线安装前的确认：来源、当前版本与目标版本都要说清楚。"""
        box = QMessageBox(self)
        box.setWindowTitle("从官方目录安装")
        box.setIcon(QMessageBox.Information if entry.signed else QMessageBox.Warning)
        current = record.version if record is not None else "未安装"
        box.setText(f"「{entry.name}」{current} → {entry.version}")
        lines = [
            "来源：官方目录（已签名）" if entry.signed else "来源：官方目录（未签名，将按未验证来源安装）",
        ]
        if record is not None and record.source and record.source.get("kind") != SOURCE_OFFICIAL:
            # 本地/历史来源换成官方来源属于来源转换，必须显式确认。
            lines.append("安装后会更换为官方来源；原来源设置不再保留。")
        if entry.notes:
            lines.append(entry.notes)
        box.setInformativeText("\n".join(lines))
        accept = box.addButton("下载并安装", QMessageBox.AcceptRole)
        cancel = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(accept)
        box.setEscapeButton(cancel)
        box.exec_()
        return box.clickedButton() is accept

    def _start_package_download(self, entry):
        thread = self._download_thread
        if thread is not None and thread.isRunning():
            self._report("下载进行中", "已有插件包正在下载，请稍候。")
            return
        if self._download_dir is None:
            self._download_dir = Path(tempfile.mkdtemp(prefix="bandscope-plugin-"))
        self.btn_catalog_install.setEnabled(False)
        self.catalog_status.setText(f"正在下载 {entry.package.name}…")
        thread = _PackageDownloadThread(
            self.catalog_client, entry, self._download_dir, self
        )
        thread.progress_changed.connect(self._on_download_progress)
        thread.completed.connect(self._on_download_completed)
        thread.failed.connect(self._on_download_failed)
        thread.finished.connect(self._on_download_thread_finished)
        self._download_thread = thread
        thread.start()

    def _on_download_progress(self, received, total):
        if total:
            text = f"正在下载插件包… {received / (1024 ** 2):.1f} / {total / (1024 ** 2):.1f} MiB"
        else:
            text = f"正在下载插件包… {received / (1024 ** 2):.1f} MiB"
        self.catalog_status.setText(text)

    def _on_download_completed(self, package):
        entry = package.entry
        source = self.catalog_client.install_source(entry, verified=package.verified)
        try:
            installed = self.manager.install(package.path, source=source)
        except PluginArchiveError as exc:
            self._report("插件包不合规范", str(exc))
            return
        except PluginCompatibilityError as exc:
            self._report("插件不兼容", str(exc))
            return
        except PluginError as exc:
            self._report("安装失败", str(exc))
            return
        except Exception as exc:
            self._report("安装失败", f"{type(exc).__name__}: {exc}")
            return
        self.refresh(select=installed.plugin_id)
        self._render_catalog()
        self._report(
            "安装完成",
            f"「{installed.name}」{installed.version} 已从官方目录安装。\n"
            "重启 BandScope 后生效。",
            warning=False,
        )

    def _on_download_failed(self, message):
        if message != "下载已取消。":
            self._report("下载失败", message)
        self._render_catalog()

    def _on_download_thread_finished(self):
        thread = self._download_thread
        self._download_thread = None
        if thread is not None:
            thread.deleteLater()
        self._update_catalog_buttons()

    # ------------------------------------------------------------------ 事件
    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def changeEvent(self, event):
        from PyQt5.QtCore import QEvent

        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self.refresh()
        super().changeEvent(event)

    def closeEvent(self, event):
        """关窗即取消下载与目录请求：不留后台线程，也不留临时文件。"""
        for thread in (self._catalog_thread, self._download_thread):
            if thread is not None and thread.isRunning():
                thread.requestInterruption()
                thread.wait(2000)
        if self._download_dir is not None:
            shutil.rmtree(self._download_dir, ignore_errors=True)
            self._download_dir = None
        super().closeEvent(event)


def _catalog_status_text(match, record) -> str:
    """官方页一行的状态文案：把“为什么不能装”直接写出来。"""
    if match.status == STATUS_UPDATE_AVAILABLE:
        return f"可更新：{record.version if record else '—'} → {match.entry.version}"
    if match.status == STATUS_INSTALLABLE:
        if match.reason:
            return f"可安装：{match.entry.version}（{match.reason}）"
        return f"可安装：{match.entry.version}"
    if match.status == STATUS_UP_TO_DATE:
        return f"已是最新（{match.installed_version or '—'}）"
    if match.status == STATUS_INCOMPATIBLE:
        return f"不兼容：{match.reason}"
    if match.status == STATUS_NOT_IN_CATALOG:
        return "官方目录中没有此插件（可能是本地安装或历史包）。"
    return "无法确认。"


class _CatalogFetchThread(QThread):
    """后台取目录：网络与验签都不在界面线程做。"""

    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client

    def run(self):
        try:
            fetched = self.client.fetch_catalog()
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(fetched)


class _PackageDownloadThread(QThread):
    """后台下载插件包；取消是协作式的，取消后不留下半截文件。"""

    progress_changed = pyqtSignal(object, object)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, client, entry, destination, parent=None):
        super().__init__(parent)
        self.client = client
        self.entry = entry
        self.destination = destination

    def _report(self, received, total):
        self.progress_changed.emit(received, total)

    def run(self):
        try:
            package = self.client.download_package(
                self.entry,
                self.destination,
                progress=self._report,
                cancelled=self.isInterruptionRequested,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        if self.isInterruptionRequested():
            self.failed.emit("下载已取消。")
            return
        self.completed.emit(package)


def open_plugin_manager(window, session) -> PluginManagerDialog:
    """打开（或复用）插件管理面板。"""
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
