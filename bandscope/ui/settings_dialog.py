"""应用设置窗口。页面切换只改变导航，用户编辑时才向宿主发送信号。"""
from PyQt5.QtCore import Qt, QSignalBlocker, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QScrollArea,
    QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from bandscope.core.display_style import resolved_render_quality
from bandscope.ui.ui_controls import ChevronComboBox


class SettingsDialog(QDialog):
    backend_changed = pyqtSignal(str)
    quality_changed = pyqtSignal(dict)

    def __init__(self, backend, quality, parent=None, *,
                 plugin_panel=None, update_panel=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(1040, 680)
        self._page_items = {}
        self._panels_need_cleanup = False
        self.navigation = QTreeWidget()
        self.navigation.setHeaderHidden(True)
        self.navigation.setAccessibleName("设置分类")
        self.navigation.setIndentation(14)
        self.navigation.setFixedWidth(174)
        self.stack = QStackedWidget()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addWidget(self.navigation)
        layout.addWidget(self.stack, 1)

        display = self._category("计算与显示")
        backend_page = self._page(display, "计算后端")
        self.backend = ChevronComboBox()
        self.backend.addItems(backend.MODES)
        self.backend.setCurrentText(backend.mode)
        backend_form = QFormLayout()
        backend_form.addRow("后端", self.backend)
        backend_page.addLayout(backend_form)
        gpu_available = backend.gpu_available
        gpu_index = self.backend.findText("NVIDIA GPU")
        self.backend.model().item(gpu_index).setEnabled(gpu_available)
        if not gpu_available:
            reason = "当前环境无法使用 CuPy/CUDA，Auto 将使用 CPU。"
            self.backend.setItemData(gpu_index, reason, Qt.ToolTipRole)
            backend_page.addWidget(QLabel(reason))
        backend_page.addStretch()

        quality_page = self._page(display, "3D渲染质量")
        quality_page.addWidget(QLabel("应用于所有数据源的 3D 视图。"))
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.mode = self._combo(form, "质量模式", [("明确采样", "explicit"), ("兼容质量", "legacy")])
        self.interpolation = self._combo(form, "体插值", [("线性", "linear"), ("最近邻", "nearest")])
        self.resting = self._combo(form, "静止质量", [
            ("标准（0.5 × 最小间距）", "standard"), ("精细（0.25 × 最小间距）", "fine"),
        ])
        quality_page.addLayout(form)
        quality_page.addStretch()
        self.set_quality(quality)
        for combo in (self.mode, self.interpolation, self.resting):
            combo.currentIndexChanged.connect(self._edit_quality)

        app = self._category("应用管理")
        plugins_page = self._page(app, "插件管理", key="plugins", scroll=False)
        self.plugins = plugin_panel or self._unavailable_panel(
            "插件管理不可用：扩展模块未能初始化。"
        )
        plugins_page.addWidget(self.plugins, 1)
        update_page = self._page(app, "更新设置", key="updates")
        self.updates = update_panel or self._unavailable_panel(
            "更新设置不可用：更新控制器未能初始化。"
        )
        update_page.addWidget(self.updates)
        update_page.addStretch(1)
        self.backend.currentTextChanged.connect(self.backend_changed)
        self.navigation.expandAll()
        self.navigation.currentItemChanged.connect(self._select_page)
        self.navigation.setCurrentItem(display.child(0))

    def _category(self, title):
        item = QTreeWidgetItem(self.navigation, [title])
        item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        return item

    def _page(self, category, title, *, key=None, scroll=True):
        item = QTreeWidgetItem(category, [title])
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(16)
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(heading)
        if scroll:
            container = QScrollArea()
            container.setWidgetResizable(True)
            container.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            container.setWidget(page)
        else:
            container = page
        item.setData(0, Qt.UserRole, self.stack.addWidget(container))
        if key is not None:
            self._page_items[key] = item
        return layout

    @staticmethod
    def _unavailable_panel(message):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        label = QLabel(message)
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch()
        return panel

    def select_page(self, key):
        item = self._page_items[key]
        if not item.flags() & Qt.ItemIsEnabled:
            return False
        self.navigation.setCurrentItem(item)
        self._select_page(item, None)
        return True

    def set_page_available(self, key, available, tooltip=""):
        item = self._page_items[key]
        flags = item.flags()
        if available:
            flags |= Qt.ItemIsEnabled
        else:
            flags &= ~Qt.ItemIsEnabled
        item.setFlags(flags)
        item.setToolTip(0, tooltip)
        page = self.stack.widget(item.data(0, Qt.UserRole))
        page.setEnabled(available)
        page.setToolTip(tooltip)
        panel = getattr(self, key, None)
        if panel is not None:
            panel.setEnabled(available)
            panel.setToolTip(tooltip)

    def _select_page(self, item, _previous):
        if item is not None and item.parent() is not None:
            self.stack.setCurrentIndex(item.data(0, Qt.UserRole))
            set_active = getattr(self.updates, "set_active", None)
            if callable(set_active):
                set_active(item is self._page_items.get("updates"))

    def showEvent(self, event):
        super().showEvent(event)
        self._panels_need_cleanup = True
        item = self.navigation.currentItem()
        set_active = getattr(self.updates, "set_active", None)
        if callable(set_active):
            set_active(item is self._page_items.get("updates"))

    def _cleanup_panels(self):
        if not self._panels_need_cleanup:
            return
        self._panels_need_cleanup = False
        shutdown = getattr(self.plugins, "shutdown", None)
        if callable(shutdown):
            shutdown()
        discard = getattr(self.updates, "discard_and_deactivate", None)
        if callable(discard):
            discard()

    def hideEvent(self, event):
        self._cleanup_panels()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._cleanup_panels()
        super().closeEvent(event)

    def reject(self):
        self._cleanup_panels()
        super().reject()

    @staticmethod
    def _combo(form, title, options):
        combo = ChevronComboBox()
        for label, value in options:
            combo.addItem(label, value)
        form.addRow(title, combo)
        return combo

    def set_backend(self, mode):
        with QSignalBlocker(self.backend):
            self.backend.setCurrentText(mode)

    def set_quality(self, quality):
        for combo, key in ((self.mode, "mode"), (self.interpolation, "interpolation"),
                           (self.resting, "resting")):
            with QSignalBlocker(combo):
                combo.setCurrentIndex(combo.findData(quality[key]))
        explicit = quality["mode"] == "explicit"
        self.interpolation.setEnabled(explicit)
        self.resting.setEnabled(explicit)

    def _edit_quality(self):
        quality = resolved_render_quality({
            "mode": self.mode.currentData(), "interpolation": self.interpolation.currentData(),
            "resting": self.resting.currentData(),
        })
        self.set_quality(quality)
        self.quality_changed.emit(quality)
