# -*- coding: utf-8 -*-
"""独立 Blender 工程导出、回读和逐条目显示编辑对话框。"""
from __future__ import annotations

import datetime
from pathlib import Path
import sys
import threading
import uuid

from PyQt5.QtCore import Qt, QThreadPool, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

import bandscope.ui.theme as theme
from bandscope.exporting.blender_export import (
    blender_version, detect_blender, remember_blender_path,
)
from bandscope.exporting.blender_jobs import BlenderProjectUpdateJob
from bandscope.exporting.blender_panel import BlenderExportPanel, _BlenderTask
from bandscope.exporting.blender_project import (
    BlenderProjectError, load_blender_project, update_snapshot_display,
)
from bandscope.processing.display_transfer import channels
from bandscope.ui.page_render_control import RenderControlPage


class BlenderProjectEditor(QWidget):
    busyChanged = pyqtSignal(bool)
    updateSucceeded = pyqtSignal(str)

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.main_window = window
        self.settings = getattr(window, "settings", None)
        self.project = None
        self._snapshots = {}
        self._dirty_ids = set()
        self._loaded_entry_id = None
        self._loading = False
        self._task = None
        self._cancel_event = None
        self._plotter = None
        self._volume_session = None
        self._display_token = 0
        self._build_ui()
        self.refresh_detection()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.source_label = QLabel("尚未打开 Blender 工程")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)
        self.warning_label = QLabel(
            "这里恢复的是 BandScope 导出的冻结 3D 结果，不是原始实验数据或完整处理链。"
            "宿主使用 PyVista/VTK；Cycles 与 VTK 的体积分不同，不承诺逐像素一致。"
        )
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")
        layout.addWidget(self.warning_label)

        body = QHBoxLayout()
        controls = QVBoxLayout()
        self.entry_combo = QComboBox()
        self.entry_combo.currentIndexChanged.connect(self._on_entry_changed)
        controls.addWidget(QLabel("工程条目（逐条目独立编辑）"))
        controls.addWidget(self.entry_combo)
        form = QFormLayout()
        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(RenderControlPage.CMAP_OPTIONS)
        form.addRow("Colormap", self.cmap_combo)
        self.level_spins = []
        for name, value in (("黑点", 0.0), ("Gamma", 50.0), ("白点", 100.0)):
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 100.0)
            spin.setDecimals(2)
            spin.setValue(value)
            self.level_spins.append(spin)
            form.addRow(name, spin)
        self.include_zero = QCheckBox("显示域包含零")
        form.addRow("色阶范围", self.include_zero)
        self.lock_check = QCheckBox("使用工程自身锁定范围")
        self.lock_check.toggled.connect(self._on_lock_toggled)
        form.addRow("锁定色阶", self.lock_check)
        self.lock_low = QDoubleSpinBox()
        self.lock_high = QDoubleSpinBox()
        for spin in (self.lock_low, self.lock_high):
            spin.setRange(-1.0e12, 1.0e12)
            spin.setDecimals(6)
            spin.setEnabled(False)
        form.addRow("锁定下限", self.lock_low)
        form.addRow("锁定上限", self.lock_high)
        self.opacity_combo = QComboBox()
        for text, value in (("线性", "linear"), ("对数", "log"), ("幂函数", "power"), ("Sigmoid", "sigmoid")):
            self.opacity_combo.addItem(text, value)
        form.addRow("不透明度映射", self.opacity_combo)
        controls.addLayout(form)
        controls.addStretch(1)
        body.addLayout(controls, 1)

        self.preview_host = QWidget()
        self.preview_host.setMinimumSize(520, 390)
        preview_layout = QVBoxLayout(self.preview_host)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_label = QLabel("打开工程后在这里显示宿主 VTK 冻结体预览")
        self.preview_label.setAlignment(Qt.AlignCenter)
        preview_layout.addWidget(self.preview_label)
        body.addWidget(self.preview_host, 2)
        layout.addLayout(body, 1)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Blender 路径"))
        self.path_edit = QLineEdit()
        path_row.addWidget(self.path_edit, 1)
        self.browse_button = QPushButton("选择…")
        self.browse_button.clicked.connect(self._browse_blender)
        path_row.addWidget(self.browse_button)
        layout.addLayout(path_row)
        theme.style_push_button(self.browse_button, "secondary")

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("打开已有工程后，可调整显示参数并原位置更新资产。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        actions = QHBoxLayout()
        self.update_button = QPushButton("更新工程")
        self.update_button.clicked.connect(self.start_update)
        self.update_button.setEnabled(False)
        theme.style_push_button(self.update_button, "primary")
        actions.addWidget(self.update_button)
        self.cancel_button = QPushButton("取消更新")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        theme.style_push_button(self.cancel_button, "secondary")
        actions.addWidget(self.cancel_button)
        self.open_project_button = QPushButton("打开工程")
        self.open_project_button.clicked.connect(self.open_project)
        self.open_folder_button = QPushButton("打开文件夹")
        self.open_folder_button.clicked.connect(self.open_folder)
        for button in (self.open_project_button, self.open_folder_button):
            theme.style_push_button(button, "secondary")
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self._refresh_preview)
        for widget in (self.cmap_combo, self.include_zero, self.opacity_combo, *self.level_spins,
                       self.lock_check, self.lock_low, self.lock_high):
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._on_display_changed)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(self._on_display_changed)
            else:
                widget.valueChanged.connect(self._on_display_changed)

    @property
    def busy(self):
        return self._task is not None

    @property
    def current_entry_id(self):
        value = self.entry_combo.currentData()
        return str(value) if value else None

    @property
    def current_snapshot(self):
        return self._snapshots.get(self.current_entry_id)

    def refresh_detection(self):
        path = detect_blender(self.settings, self.path_edit.text().strip())
        if path is not None:
            self.path_edit.setText(str(path))
            remember_blender_path(self.settings, path)

    def _browse_blender(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Blender 可执行文件", self.path_edit.text() or str(Path.home()),
            "Blender (blender.exe);;所有文件 (*)",
        )
        if path:
            self.path_edit.setText(path)
            remember_blender_path(self.settings, path)

    def load_project(self, blend_path):
        loaded = load_blender_project(blend_path)
        self.project = loaded
        self._snapshots = {entry.entry_id: entry.snapshot for entry in loaded.entries}
        self._dirty_ids.clear()
        self.source_label.setText(
            f"工程：{loaded.blend_path}\n权威配置：{loaded.assets_path / 'scene.json'}；"
            "原位更新不会改写 .blend。"
        )
        warnings = list(loaded.warnings)
        if warnings:
            self.warning_label.setText("\n".join(warnings))
        else:
            self.warning_label.setText(
                "冻结 3D 结果；不恢复原始数据、时间轴或完整处理链。"
                "宿主使用 VTK，Cycles 与 VTK 的体积分差异仍需保留。"
            )
        self._loading = True
        try:
            self.entry_combo.clear()
            for entry in loaded.entries:
                self.entry_combo.addItem(
                    f"{entry.entry_id} · {entry.snapshot.source_page_title}", entry.entry_id,
                )
            self.entry_combo.setCurrentIndex(0)
            self._load_current_controls()
            self._loaded_entry_id = self.current_entry_id
        finally:
            self._loading = False
        self.status_label.setText("已打开冻结工程；修改后点击“更新工程”。Blender 中已打开的工程更新后需重载 VDB 或重开。")
        self._sync_update_button()
        self._refresh_preview()
        return loaded

    def _load_current_controls(self):
        snapshot = self.current_snapshot
        if snapshot is None:
            return
        previous = self._loading
        self._loading = True
        try:
            cmap = snapshot.cmap_name
            if not cmap:
                if self.cmap_combo.itemData(0) != "":
                    self.cmap_combo.insertItem(0, "请选择色带（旧工程未保存）", "")
                self.cmap_combo.setCurrentIndex(0)
            else:
                index = self.cmap_combo.findText(cmap)
                if index < 0:
                    self.cmap_combo.addItem(cmap)
                    index = self.cmap_combo.count() - 1
                self.cmap_combo.setCurrentIndex(index)
            for spin, value in zip(self.level_spins, snapshot.levels_params):
                spin.setValue(float(value))
            self.include_zero.setChecked(bool(snapshot.payload.get("include_zero")))
            locked = snapshot.locked_range
            self.lock_check.setChecked(locked is not None)
            info = snapshot.payload.get("level_info") or {}
            finite = snapshot.payload["volume"][__import__("numpy").isfinite(snapshot.payload["volume"])]
            fallback = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 1.0)
            low, high = locked or (
                info.get("data_min", fallback[0]), info.get("data_max", fallback[1]),
            )
            self.lock_low.setValue(float(low))
            self.lock_high.setValue(float(high))
            index = self.opacity_combo.findData(snapshot.payload.get("opacity_mode", "linear"))
            self.opacity_combo.setCurrentIndex(max(0, index))
        finally:
            self._loading = previous

    def _on_entry_changed(self, *_args):
        if self._loading:
            return
        self._store_controls()
        self._load_current_controls()
        self._loaded_entry_id = self.current_entry_id
        self._sync_update_button()
        self._refresh_preview()

    def _on_lock_toggled(self, checked):
        self.lock_low.setEnabled(bool(checked))
        self.lock_high.setEnabled(bool(checked))

    def _display_values(self):
        locked = None
        if self.lock_check.isChecked():
            low, high = self.lock_low.value(), self.lock_high.value()
            if high <= low:
                raise BlenderProjectError("锁定上限必须大于下限。")
            locked = (low, high)
        cmap = self.cmap_combo.currentText()
        if self.cmap_combo.currentData() == "" and cmap.startswith("请选择色带"):
            cmap = ""
        return {
            "cmap_name": str(cmap),
            "levels_params": tuple(spin.value() for spin in self.level_spins),
            "locked_range": locked,
            "include_zero": self.include_zero.isChecked(),
            "opacity_mode": str(self.opacity_combo.currentData()),
        }

    def _store_controls(self):
        snapshot = self._snapshots.get(self._loaded_entry_id)
        if snapshot is None:
            return False
        values = self._display_values()
        update_snapshot_display(snapshot, **values)
        return True

    def _on_display_changed(self, *_args):
        if self._loading or self._loaded_entry_id not in self._snapshots:
            return
        try:
            self._store_controls()
        except BlenderProjectError as exc:
            self.status_label.setText(str(exc))
            self._sync_update_button()
            return
        self._dirty_ids.add(self._loaded_entry_id)
        self.status_label.setText(f"条目 {self._loaded_entry_id} 的显示参数已修改，尚未写入工程。")
        self._sync_update_button()
        self._preview_timer.start()

    def _sync_update_button(self):
        valid = bool(self.project is not None and self._dirty_ids and not self.busy)
        if valid:
            try:
                self._store_controls()
            except BlenderProjectError:
                valid = False
        self.update_button.setEnabled(valid)
        self.update_button.setText(f"更新工程（{len(self._dirty_ids)} 条目）" if self._dirty_ids else "更新工程")

    def _ensure_preview(self):
        if self._plotter is not None:
            return True
        if __import__("os").environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
            return False
        try:
            from pyvistaqt import QtInteractor
            from bandscope.rendering.render_core import VolumeRenderSession

            self._plotter = QtInteractor(self.preview_host)
            self.preview_host.layout().addWidget(self._plotter)
            self.preview_label.hide()
            self._volume_session = VolumeRenderSession(self._plotter)
            self._plotter.set_background("white")
            return True
        except Exception as exc:
            self.preview_label.setText(f"VTK 预览不可用：{exc}")
            self._plotter = None
            self._volume_session = None
            return False

    def _refresh_preview(self):
        snapshot = self.current_snapshot
        if snapshot is None or not snapshot.cmap_name or not self._ensure_preview():
            return
        try:
            import numpy as np
            from bandscope.exporting.publication_renderers import _apply_snapshot_camera

            volume = np.asarray(snapshot.payload["volume"], dtype=np.float32)
            info = snapshot.payload["level_info"]
            color, alpha = channels(volume, info, snapshot.payload["opacity_mode"])
            self._display_token += 1
            self._volume_session.render(
                volume, snapshot.levels_params, snapshot.payload["opacity_mode"],
                clip_ranges=snapshot.payload.get("clip_render_bounds"),
                show_axes=False, core_coords=snapshot.coords, cmap=snapshot.cmap_name,
                data_bounds=snapshot.payload["data_bounds"],
                full_shape=snapshot.payload["full_shape"],
                include_zero=snapshot.payload.get("include_zero", False),
                opacity_multiplier=None,
                axis_titles=snapshot.payload.get("axis_titles"),
                display_fields={"background_level_info": info, "color": color, "alpha": alpha},
                display_token=self._display_token,
            )
            _apply_snapshot_camera(self._plotter, snapshot.camera)
            self._plotter.render()
        except Exception as exc:
            self.preview_label.setText(f"VTK 预览失败：{exc}")
            self.preview_label.show()

    def _baked_warnings(self, updates):
        records = []
        for entry_id in updates:
            state = self._snapshots[entry_id].payload.get("blender_project") or {}
            baked = state.get("baked_effects") or ()
            if baked:
                records.append(f"{entry_id}: {', '.join(baked)}")
        return records

    def start_update(self):
        if self.busy or not self._dirty_ids:
            return False
        try:
            self._store_controls()
        except BlenderProjectError as exc:
            self.status_label.setText(str(exc))
            return False
        updates = {entry_id: self._snapshots[entry_id] for entry_id in sorted(self._dirty_ids)}
        warnings = self._baked_warnings(updates)
        if warnings:
            answer = QMessageBox.warning(
                self, "确认丢弃已烘焙效果",
                "以下条目含局部增强、插件透明度倍率或旧格式未知效果。基础显示重算会改变或丢弃这些效果，"
                "不能宣称无损还原：\n\n" + "\n".join(warnings) + "\n\n是否继续原位更新？",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return False
        blender = detect_blender(self.settings, self.path_edit.text().strip())
        if blender is None:
            self.status_label.setText("未检测到有效 Blender；无法只读核对工程并写出 VDB。")
            return False
        try:
            version = blender_version(blender)
        except Exception as exc:
            self.status_label.setText(f"Blender 检测失败：{exc}")
            return False
        remember_blender_path(self.settings, blender)
        log_root = Path.cwd() / ".local" / "logs"
        if getattr(sys, "_MEIPASS", None):
            from PyQt5.QtCore import QStandardPaths
            log_root = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / "logs"
        log_path = log_root / (
            f"blender-update-{datetime.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.log"
        )
        self._cancel_event = threading.Event()
        job = BlenderProjectUpdateJob(
            self.project, updates, blender, cancel_event=self._cancel_event, log_path=log_path,
        )
        task = _BlenderTask(job)
        task.signals.progress.connect(self._on_progress)
        task.signals.succeeded.connect(self._on_succeeded)
        task.signals.failed.connect(self._on_failed)
        self._task = task
        self.progress_bar.setValue(0)
        self.status_label.setText(f"已检测 Blender {version}；正在核对工程并暂存更新，当前数据源不会改变。")
        self._set_busy_widgets(True)
        self.busyChanged.emit(True)
        QThreadPool.globalInstance().start(task)
        return True

    def _set_busy_widgets(self, busy):
        for widget in (self.entry_combo, self.cmap_combo, self.include_zero, self.opacity_combo,
                       self.path_edit, self.browse_button, *self.level_spins, self.lock_check,
                       self.lock_low, self.lock_high):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.update_button.setEnabled(not busy and bool(self._dirty_ids))

    def _on_progress(self, percent, stage):
        self.progress_bar.setValue(int(percent))
        self.status_label.setText(f"{stage}（{int(percent)}%）")

    def _on_succeeded(self, result):
        self._task = None
        self._cancel_event = None
        self._set_busy_widgets(False)
        self.load_project(result.blend_path)
        self.progress_bar.setValue(100)
        self.status_label.setText(
            "工程已原位更新；.blend 字节未改写。若已在 Blender 中打开，请重新载入 VDB 或重开工程。"
        )
        self.busyChanged.emit(False)
        self.updateSucceeded.emit(str(result.blend_path))

    def _on_failed(self, message, cancelled):
        self._task = None
        self._cancel_event = None
        self._set_busy_widgets(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("已取消；旧工程保持不变。" if cancelled else f"更新失败，旧工程保持不变：{message}")
        self.busyChanged.emit(False)
        if not cancelled:
            show = getattr(self.main_window, "_show_message", None)
            if show is not None:
                show("Blender 工程更新失败", message)
        self._sync_update_button()

    def cancel(self):
        if self._cancel_event is not None and self.busy:
            self._cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("正在取消；提交前将保留旧工程…")

    def open_project(self):
        if self.project is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.blend_path)))

    def open_folder(self):
        if self.project is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.blend_path.parent)))

    def close_preview(self):
        self._preview_timer.stop()
        if self._plotter is not None:
            try:
                self._plotter.close()
            except Exception:
                pass
            self._plotter = None
            self._volume_session = None


class BlenderProjectDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.main_window = window
        self.setWindowTitle("Blender 工程")
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        width = 1180 if available is None else min(1180, max(900, available.width() - 80))
        height = 920 if available is None else min(920, max(640, available.height() - 100))
        self.resize(width, height)
        self._close_after_task = False
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title_label = QLabel("Blender 工程导出")
        self.title_label.setStyleSheet("font-size: 17px; font-weight: 600;")
        header.addWidget(self.title_label)
        header.addStretch(1)
        self.new_button = QPushButton("新建导出")
        self.new_button.clicked.connect(self.show_export)
        self.open_existing_button = QPushButton("打开已有工程")
        self.open_existing_button.clicked.connect(lambda: self.open_existing())
        for button in (self.new_button, self.open_existing_button):
            theme.style_push_button(button, "secondary")
            header.addWidget(button)
        layout.addLayout(header)
        self.stack = QStackedWidget()
        # 125% 缩放的 1080p 屏幕上，导出面板的完整高度会超过可用桌面高度；
        # 用滚动区承载面板，避免控件被压缩重叠，底部操作按钮仍固定可见。
        self.export_scroll = QScrollArea()
        self.export_scroll.setWidgetResizable(True)
        self.export_scroll.setFrameShape(QFrame.NoFrame)
        self.export_panel = BlenderExportPanel(window, self.export_scroll)
        self.export_scroll.setWidget(self.export_panel)
        self.editor = BlenderProjectEditor(window, self)
        self.stack.addWidget(self.export_scroll)
        self.stack.addWidget(self.editor)
        layout.addWidget(self.stack, 1)
        footer = QHBoxLayout()
        self.export_button = QPushButton("导出 Blender 工程")
        # clicked 会附带 checked(bool)；用无参 lambda 隔离，避免它被当作
        # output_path 传入导出方法（Path(False) 的 TypeError 会触发 qFatal 闪退）。
        self.export_button.clicked.connect(lambda: self.export_panel.start_export())
        theme.style_push_button(self.export_button, "primary")
        footer.addWidget(self.export_button)
        footer.addStretch(1)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        theme.style_push_button(self.close_button, "secondary")
        footer.addWidget(self.close_button)
        layout.addLayout(footer)
        self.export_panel.availabilityChanged.connect(self._sync_buttons)
        self.export_panel.busyChanged.connect(self._on_busy)
        self.editor.busyChanged.connect(self._on_busy)
        self.export_panel.exportSucceeded.connect(self._toast)
        self.editor.updateSucceeded.connect(self._toast)
        self._sync_buttons()

    @property
    def busy(self):
        return self.export_panel.busy or self.editor.busy

    def _sync_buttons(self):
        exporting = self.stack.currentWidget() is self.export_scroll
        self.export_button.setVisible(exporting)
        self.export_button.setEnabled(exporting and self.export_panel.can_export)
        self.new_button.setEnabled(not self.busy)
        self.open_existing_button.setEnabled(not self.busy)

    def _on_busy(self, _busy):
        self._sync_buttons()
        if not self.busy and self._close_after_task:
            QTimer.singleShot(0, self.reject)

    def _toast(self, path):
        toast = getattr(self.main_window, "_toast_success", None)
        if toast is not None:
            toast("Blender 工程已写入", path)

    def show_export(self):
        if self.busy:
            return
        self.stack.setCurrentWidget(self.export_scroll)
        self.title_label.setText("Blender 工程导出")
        self.export_panel.refresh_pages()
        self._sync_buttons()
        self.show()
        self.raise_()
        self.activateWindow()

    def open_existing(self, blend_path=None):
        if self.busy:
            return False
        if blend_path is None:
            blend_path, _ = QFileDialog.getOpenFileName(
                self, "打开 BandScope Blender 工程", str(Path.home()), "Blender 工程 (*.blend)",
            )
        if not blend_path:
            return False
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.editor.load_project(blend_path)
        except Exception as exc:
            QMessageBox.warning(self, "无法打开 Blender 工程", str(exc))
            return False
        finally:
            QApplication.restoreOverrideCursor()
        self.stack.setCurrentWidget(self.editor)
        self.title_label.setText("Blender 工程回读与显示编辑")
        self._sync_buttons()
        self.show()
        self.raise_()
        self.activateWindow()
        return True

    def reject(self):
        if self.busy:
            self._close_after_task = True
            self.export_panel.cancel()
            self.editor.cancel()
            return
        self._close_after_task = False
        self.editor.close_preview()
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            self._close_after_task = True
            self.export_panel.cancel()
            self.editor.cancel()
            event.ignore()
            return
        self.editor.close_preview()
        super().closeEvent(event)
