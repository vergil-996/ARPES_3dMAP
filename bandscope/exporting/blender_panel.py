# -*- coding: utf-8 -*-
"""独立 Blender 工程导出面板；冻结在 GUI 线程，数组与外部进程在 worker。"""
from __future__ import annotations

import datetime
from pathlib import Path
import sys
import threading
import uuid

from PyQt5.QtCore import QObject, QRunnable, Qt, QThreadPool, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QDesktopServices, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

import bandscope.ui.theme as theme
from bandscope.exporting.blender_batch import capture_blender_snapshots, list_blender_pages
from bandscope.exporting.blender_export import (
    BlenderExportCancelled, assets_path_for, blender_version,
    detect_blender, remember_blender_path,
)
from bandscope.exporting.blender_jobs import BlenderProjectExportJob
from bandscope.exporting.blender_project import (
    PRESET_KEYS, PRESET_LABELS, BlenderProjectRequest,
)
from bandscope.exporting.publication_export import safe_filename


class _BlenderTaskSignals(QObject):
    progress = pyqtSignal(int, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str, bool)


class _BlenderTask(QRunnable):
    def __init__(self, job):
        super().__init__()
        self.job = job
        self.signals = _BlenderTaskSignals()

    @pyqtSlot()
    def run(self):
        try:
            result = self.job.run()
        except BlenderExportCancelled as exc:
            self.signals.failed.emit(str(exc), True)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}", False)
        else:
            self.signals.succeeded.emit(result)


class BlenderExportPanel(QWidget):
    busyChanged = pyqtSignal(bool)
    availabilityChanged = pyqtSignal()
    exportSucceeded = pyqtSignal(str)

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.main_window = window
        self.settings = getattr(window, "settings", None)
        self.snapshot = None
        self._legacy_snapshots = []
        self.output_path = None
        self._task = None
        self._freezing = False
        self._cancel_event = None
        self._detection_valid = False
        self._tree_updating = False
        self._build_ui()
        self.refresh_detection(check_version=False)
        self.refresh_pages()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)
        self.source_label = QLabel("选择要导出的数据源与 3D 页面")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)
        self.captured_label = QLabel("导出前会提交当前编辑，逐页等待精确结果并冻结；后台任务不再读取活动窗口。")
        self.captured_label.setWordWrap(True)
        self.captured_label.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")
        layout.addWidget(self.captured_label)

        body = QHBoxLayout()
        self.page_tree = QTreeWidget()
        self.page_tree.setHeaderLabels(["数据源 / 3D 页面"])
        self.page_tree.setMinimumWidth(330)
        self.page_tree.itemChanged.connect(self._on_tree_item_changed)
        body.addWidget(self.page_tree, 3)
        right = QVBoxLayout()
        self.reference_label = QLabel("参考缩略图\n（不是 Blender 渲染预览）")
        self.reference_label.setAlignment(Qt.AlignCenter)
        self.reference_label.setMinimumHeight(150)
        self.reference_label.setMaximumHeight(180)
        self.reference_label.setStyleSheet(
            f"background-color: {theme.BG_1}; border: 1px solid {theme.BORDER_HEX};"
            f"border-radius: 8px; color: {theme.TEXT_2}; padding: 8px;"
        )
        right.addWidget(self.reference_label, 1)
        self.reference_note = QLabel("缩略图仅核对冻结状态，不是 Blender 渲染预览。")
        self.reference_note.setWordWrap(True)
        self.reference_note.setStyleSheet(f"color: {theme.TEXT_3}; font-size: 11px;")
        right.addWidget(self.reference_note)
        presets = QGroupBox("应用于所有选中页面的预设")
        preset_grid = QGridLayout(presets)
        self.preset_checks = {}
        for index, key in enumerate(PRESET_KEYS):
            check = QCheckBox(PRESET_LABELS[key])
            check.setChecked(True)
            self.preset_checks[key] = check
            preset_grid.addWidget(check, index // 2, index % 2)
        right.addWidget(presets)
        body.addLayout(right, 2)
        layout.addLayout(body, 1)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Blender 路径"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("选择 blender.exe；路径可包含空格和中文")
        self.path_edit.textChanged.connect(self._on_path_changed)
        path_row.addWidget(self.path_edit, 1)
        self.browse_button = QPushButton("手动选择…")
        self.browse_button.clicked.connect(self._browse)
        path_row.addWidget(self.browse_button)
        self.detect_button = QPushButton("重新检测")
        self.detect_button.clicked.connect(lambda: self.refresh_detection(check_version=True))
        path_row.addWidget(self.detect_button)
        layout.addLayout(path_row)
        for button in (self.browse_button, self.detect_button):
            button.setFixedHeight(28)
            theme.style_push_button(button, "secondary")
        self.detection_label = QLabel("")
        self.detection_label.setWordWrap(True)
        self.detection_label.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")
        layout.addWidget(self.detection_label)
        install = QLabel('未安装时请从 <a href="https://www.blender.org/download/">Blender 官网</a>安装；BandScope 不会自动安装软件。')
        install.setOpenExternalLinks(True)
        install.setWordWrap(True)
        install.setStyleSheet(f"color: {theme.TEXT_3}; font-size: 11px;")
        layout.addWidget(install)
        assets_note = QLabel("输出为 名称.blend + 名称.assets/。移动或分享时必须携带整个资产目录。")
        assets_note.setWordWrap(True)
        assets_note.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")
        layout.addWidget(assets_note)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("勾选 3D 页面并选择 Blender 后即可导出。")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")
        layout.addWidget(self.status_label)
        actions = QHBoxLayout()
        self.cancel_button = QPushButton("取消导出")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        self.open_project_button = QPushButton("打开工程")
        self.open_project_button.clicked.connect(self.open_project)
        self.open_folder_button = QPushButton("打开文件夹")
        self.open_folder_button.clicked.connect(self.open_folder)
        self.open_project_button.hide()
        self.open_folder_button.hide()
        for button in (self.cancel_button, self.open_project_button, self.open_folder_button):
            button.setFixedHeight(28)
            theme.style_push_button(button, "secondary")
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)

    @property
    def busy(self):
        return self._task is not None or self._freezing

    @property
    def can_export(self):
        return bool(
            not self.busy and self.selected_keys() and self._detection_valid
            and self.path_edit.text().strip() and Path(self.path_edit.text().strip()).is_file()
        )

    def selected_keys(self):
        if self._legacy_snapshots:
            return [("legacy", self.snapshot.snapshot_id)]
        result = []
        for source_index in range(self.page_tree.topLevelItemCount()):
            source = self.page_tree.topLevelItem(source_index)
            for page_index in range(source.childCount()):
                item = source.child(page_index)
                if item.checkState(0) == Qt.Checked:
                    result.append(tuple(item.data(0, Qt.UserRole)))
        return result

    def selected_presets(self):
        return {key: self.preset_checks[key].isChecked() for key in PRESET_KEYS}

    def refresh_pages(self):
        if self.busy:
            return
        previous = set(self.selected_keys())
        had_pages = self.page_tree.topLevelItemCount() > 0
        candidates = list_blender_pages(self.main_window)
        self._legacy_snapshots = []
        self.snapshot = None
        self._tree_updating = True
        self.page_tree.clear()
        try:
            by_source = {}
            for candidate in candidates:
                by_source.setdefault((candidate.session_id, candidate.source_title), []).append(candidate)
            for (session_id, source_title), pages in by_source.items():
                source = QTreeWidgetItem([source_title])
                source.setFlags(source.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                source.setCheckState(0, Qt.Unchecked)
                source.setData(0, Qt.UserRole + 1, session_id)
                self.page_tree.addTopLevelItem(source)
                for candidate in pages:
                    item = QTreeWidgetItem([candidate.page_title])
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setData(0, Qt.UserRole, candidate.key)
                    checked = candidate.key in previous if had_pages else candidate.current
                    item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
                    source.addChild(item)
                source.setExpanded(True)
        finally:
            self._tree_updating = False
        self._sync_selection_label()
        self.availabilityChanged.emit()

    def _on_tree_item_changed(self, item, _column):
        if self._tree_updating:
            return
        self._tree_updating = True
        try:
            if item.childCount() and item.checkState(0) != Qt.PartiallyChecked:
                state = item.checkState(0)
                for index in range(item.childCount()):
                    item.child(index).setCheckState(0, state)
        finally:
            self._tree_updating = False
        self._sync_selection_label()
        self.availabilityChanged.emit()

    def _sync_selection_label(self):
        count = len(self.selected_keys())
        self.source_label.setText(f"已选择 {count} 个 3D 页面；按页面树顺序沿世界 X 方向排列。")
        if not count:
            self.status_label.setText("空选择不能导出；请勾选至少一个有效 3D 页面。")

    def set_snapshot(self, snapshot):
        """首版测试/调用兼容：把一个已冻结快照作为单条目来源。"""
        self.snapshot = snapshot
        self._legacy_snapshots = [snapshot] if snapshot is not None else []
        self.page_tree.clear()
        if snapshot is None:
            self.source_label.setText("尚未冻结 3D 视图")
            self.captured_label.clear()
        else:
            self.source_label.setText(f"数据来源：{snapshot.source_desc}")
            self.captured_label.setText(f"冻结时间：{snapshot.captured_at}")
        self.reference_label.setText("正在生成参考缩略图…\n（不是 Blender 渲染预览）")
        self.reference_label.setPixmap(QPixmap())
        self.output_path = None
        self.open_project_button.hide()
        self.open_folder_button.hide()
        self.progress_bar.setValue(0)
        self.availabilityChanged.emit()

    def set_reference_pixmap(self, pixmap):
        if pixmap is None or pixmap.isNull():
            return
        scaled = pixmap.scaled(300, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.reference_label.setPixmap(scaled)

    def _update_reference(self, snapshots):
        """用首个冻结条目生成参考图；与后续工程任务使用同一份快照。"""
        import io
        from bandscope.exporting.publication_export import committed_style_for, output_options_for
        from bandscope.exporting.publication_renderers import background_kwargs, render_snapshot

        snapshot = snapshots[0]
        style, overrides = committed_style_for(self.settings, "3d")
        options = output_options_for(self.settings, "3d")
        figure = render_snapshot(snapshot, style, overrides, options, dpi=80)
        try:
            buffer = io.BytesIO()
            figure.savefig(buffer, format="png", dpi=int(figure.get_dpi()), **background_kwargs(options, figure))
            payload = buffer.getvalue()
        finally:
            figure.clear()
        pixmap = QPixmap()
        if not pixmap.loadFromData(payload):
            raise RuntimeError("冻结参考缩略图无法解码。")
        self.snapshot = snapshot
        self.set_reference_pixmap(pixmap)

    def _on_path_changed(self):
        self.output_path = None
        self.open_project_button.hide()
        self.open_folder_button.hide()
        path = self.path_edit.text().strip()
        self._detection_valid = bool(path and Path(path).is_file())
        if path and not Path(path).is_file():
            self.detection_label.setText("路径不存在；请手动选择 blender.exe，或重新自动检测。")
        else:
            self.detection_label.setText("已选择可执行文件；开始导出时检查版本、OpenVDB、NumPy 和体积节点。")
        self.availabilityChanged.emit()

    def refresh_detection(self, check_version=True):
        path = detect_blender(self.settings, self.path_edit.text().strip())
        if path is None:
            self._detection_valid = False
            self.detection_label.setText("未检测到 Blender。可手动选择 blender.exe；未检测到时不能开始导出。")
            self.availabilityChanged.emit()
            return False
        self.path_edit.setText(str(path))
        remember_blender_path(self.settings, path)
        if check_version:
            try:
                version = blender_version(path)
            except Exception as exc:
                self._detection_valid = False
                self.detection_label.setText(f"Blender 检测失败：{exc}")
                self.availabilityChanged.emit()
                return False
            self.detection_label.setText(f"已检测到 Blender {version}；导出前还会检查 OpenVDB 与体积材质能力。")
        else:
            self.detection_label.setText("已检测到 Blender 可执行文件；导出前检查版本与必要能力。")
        self._detection_valid = True
        self.availabilityChanged.emit()
        return True

    def _browse(self):
        path, _selected = QFileDialog.getOpenFileName(
            self, "选择 Blender 可执行文件", self.path_edit.text() or str(Path.home()),
            "Blender (blender.exe);;所有文件 (*)",
        )
        if not path:
            return
        self.path_edit.setText(path)
        remember_blender_path(self.settings, path)
        self.refresh_detection(check_version=True)

    def _default_output_directory(self):
        return self.output_path.parent if self.output_path is not None else Path.cwd()

    def _default_stem(self):
        if self.snapshot is not None:
            return safe_filename(self.snapshot.source_page_title)
        keys = self.selected_keys()
        if keys:
            for candidate in list_blender_pages(self.main_window):
                if candidate.key == keys[0]:
                    return safe_filename(candidate.display_title)
        return "BandScope-Blender"

    def _freeze_selected(self, cancel_event):
        if self._legacy_snapshots:
            return list(self._legacy_snapshots)
        return capture_blender_snapshots(
            self.main_window, self.selected_keys(), cancel_event=cancel_event,
            progress=lambda index, total, text: self.status_label.setText(f"{text}（{index}/{total}）"),
        )

    def start_export(self, output_path=None, overwrite=None):
        # QPushButton.clicked 会把 checked(bool) 作为第一个位置参数传进来。
        # 直接把它连接到本方法时，False 会落到 output_path 上并让 Path(False)
        # 抛 TypeError；PyQt5 会把槽函数里未捕获的异常升级为 qFatal，表现为闪退。
        if isinstance(output_path, bool):
            output_path = None
        if self.busy or not self.selected_keys():
            self.status_label.setText("空选择或任务进行中，不能开始导出。")
            return False
        blender_path = detect_blender(self.settings, self.path_edit.text().strip())
        if blender_path is None:
            self._detection_valid = False
            self.detection_label.setText("未检测到有效的 Blender；禁止开始导出。")
            self.availabilityChanged.emit()
            return False
        try:
            version = blender_version(blender_path)
        except Exception as exc:
            self._detection_valid = False
            self.detection_label.setText(f"Blender 检测失败，未开始导出：{exc}")
            self.availabilityChanged.emit()
            return False
        self.path_edit.setText(str(blender_path))
        self._detection_valid = True
        self.detection_label.setText(f"已检测到 Blender {version}；后台继续检查 OpenVDB 与体积材质能力。")
        remember_blender_path(self.settings, blender_path)
        if output_path is None:
            default = self._default_output_directory() / f"{self._default_stem()}.blend"
            output_path, _selected = QFileDialog.getSaveFileName(
                self, "导出 Blender 工程", str(default), "Blender 工程 (*.blend)",
            )
            if not output_path:
                return False
        output_path = Path(output_path)
        if output_path.suffix.lower() != ".blend":
            output_path = output_path.with_suffix(output_path.suffix + ".blend" if output_path.suffix else ".blend")
        exists = output_path.exists() or assets_path_for(output_path).exists()
        if exists and overwrite is None:
            answer = QMessageBox.question(
                self, "替换 Blender 工程与资产",
                f"目标工程或配套资产已存在。只有新工程完整生成并校验后才会替换：\n{output_path}\n{assets_path_for(output_path)}\n\n是否继续？",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return False
        if exists and overwrite is False:
            self.status_label.setText("目标已存在，未开始导出。")
            return False
        log_root = Path.cwd() / ".local" / "logs"
        if getattr(sys, "_MEIPASS", None):
            from PyQt5.QtCore import QStandardPaths
            log_root = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / "logs"
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = log_root / f"blender-export-{stamp}-{uuid.uuid4().hex[:8]}.log"
        self._cancel_event = threading.Event()
        self._freezing = True
        self.output_path = None
        self.open_project_button.hide()
        self.open_folder_button.hide()
        self.progress_bar.setValue(0)
        self.status_label.setText("正在逐源逐页冻结精确结果…")
        self._set_busy_widgets(True)
        self.busyChanged.emit(True)
        self.availabilityChanged.emit()
        try:
            snapshots = self._freeze_selected(self._cancel_event)
            self.status_label.setText("正在用首个冻结条目生成参考缩略图…")
            self._update_reference(snapshots)
            request = BlenderProjectRequest(snapshots, self.selected_presets())
            job = BlenderProjectExportJob(
                request, blender_path, output_path, overwrite=bool(overwrite or exists),
                cancel_event=self._cancel_event, log_path=log_path,
            )
        except Exception as exc:
            self._freezing = False
            self._set_busy_widgets(False)
            self.busyChanged.emit(False)
            self.status_label.setText(f"冻结失败，未开始导出：{exc}")
            self.availabilityChanged.emit()
            show = getattr(self.main_window, "_show_message", None)
            if show is not None:
                show("Blender 页面冻结失败", str(exc))
            return False
        task = _BlenderTask(job)
        task.signals.progress.connect(self._on_progress)
        task.signals.succeeded.connect(self._on_succeeded)
        task.signals.failed.connect(self._on_failed)
        self._task = task
        self._freezing = False
        self.status_label.setText("页面已冻结；后台转换不受后续页面、时间帧或数据源变化影响。")
        QThreadPool.globalInstance().start(task)
        return True

    def _set_busy_widgets(self, busy):
        for widget in (self.path_edit, self.browse_button, self.detect_button, self.page_tree):
            widget.setEnabled(not busy)
        for check in self.preset_checks.values():
            check.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    @pyqtSlot(int, str)
    def _on_progress(self, percent, stage):
        self.progress_bar.setValue(int(percent))
        self.status_label.setText(f"{stage}（{int(percent)}%）")

    @pyqtSlot(object)
    def _on_succeeded(self, result):
        self._task = None
        self._cancel_event = None
        self.output_path = Path(result.blend_path)
        self.progress_bar.setValue(100)
        self.status_label.setText(f"已导出：{result.blend_path}\n移动或分享时请同时携带 {result.assets_path.name}/。")
        self.open_project_button.show()
        self.open_folder_button.show()
        self._set_busy_widgets(False)
        self.busyChanged.emit(False)
        self.availabilityChanged.emit()
        self.exportSucceeded.emit(str(result.blend_path))

    @pyqtSlot(str, bool)
    def _on_failed(self, message, cancelled):
        self._task = None
        self._freezing = False
        self._cancel_event = None
        self._set_busy_widgets(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("已取消；未修改已有输出。" if cancelled else f"导出失败：{message}")
        self.busyChanged.emit(False)
        self.availabilityChanged.emit()
        if not cancelled:
            show = getattr(self.main_window, "_show_message", None)
            if show is not None:
                show("Blender 工程导出失败", message)

    def cancel(self):
        if not self.busy or self._cancel_event is None:
            return
        self._cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.status_label.setText("正在取消并清理本次临时产物；已有输出保持不变…")

    def open_project(self):
        if self.output_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_path)))

    def open_folder(self):
        if self.output_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_path.parent)))
