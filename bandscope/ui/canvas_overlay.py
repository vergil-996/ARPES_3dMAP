"""画布右侧的视角预设与常用操作工具条。"""
from PyQt5.QtCore import QPoint, QRectF, QSize, Qt, QSignalBlocker, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen, QRegion
from PyQt5.QtWidgets import QApplication, QFrame, QVBoxLayout

from bandscope.ui import theme
from bandscope.ui.rotation_dial import RotationDialPanel
from bandscope.ui.ui_controls import ActionButton


class _RotationActionButton(ActionButton):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setPen(QColor(theme.ACCENT_ON if self.isChecked() else theme.TEXT_2))
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(9)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(12, 10, -2, -1), Qt.AlignRight | Qt.AlignBottom, "Z")


class CanvasToolOverlay(QFrame):
    # 给原生窗口的像素取整留出余量，特别是在 Windows 分数缩放下。
    _MASK_INSET = 2
    reset_requested = pyqtSignal()
    focus_requested = pyqtSignal(bool)
    view_preset_requested = pyqtSignal(str)
    slice_requested = pyqtSignal(str)
    return_3d_requested = pyqtSignal()
    rotation_preview = pyqtSignal(float)
    rotation_committed = pyqtSignal(float)
    selection_toggled = pyqtSignal(bool)
    edc_requested = pyqtSignal()
    slice_dos_requested = pyqtSignal()
    new_region_requested = pyqtSignal()

    def __init__(self, parent, crop_button, erase_button, export_button):
        super().__init__(parent)
        self.setObjectName("canvas_tool_overlay")
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setStyleSheet(
            "QFrame#canvas_tool_overlay { background: transparent; border: none; }"
        )
        layout = QVBoxLayout(self)
        padding = 4 + self._MASK_INSET
        layout.setContentsMargins(padding, padding, padding, padding)
        layout.setSpacing(4)
        self._3d_active = False
        self._return_active = False
        self._separators = []

        self.preset_container = QFrame(self)
        preset_layout = QVBoxLayout(self.preset_container)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(2)
        self.preset_buttons = {}
        for key, label in (("3d", "3D"), ("xy", "XY"), ("xz", "XZ"), ("yz", "YZ")):
            button = ActionButton(self.preset_container)
            button.setText(label)
            button.setCheckable(key == "3d")
            button.setChecked(key == "3d")
            button.setFixedSize(34, 26)
            button.setToolTip({
                "3d": "等轴三维视图；在切片页返回三维来源",
                "xy": "新建 XY 切片（沿 E 轴积分）",
                "xz": "新建 XZ 切片（沿 Y 轴积分）",
                "yz": "新建 YZ 切片（沿 X 轴积分）",
            }[key])
            button.setStyleSheet(theme.nav_tab_qss())
            button.clicked.connect(lambda _checked=False, preset=key: self._select_preset(preset))
            self.preset_buttons[key] = button
            preset_layout.addWidget(button)
        layout.addWidget(self.preset_container, 0, Qt.AlignHCenter)

        self.reset_button = ActionButton(self)
        self._configure_button(self.reset_button, "重置视角", "ic_fluent_arrow_reset_regular")
        self.reset_button.clicked.connect(self.reset_requested.emit)
        layout.addWidget(self.reset_button, 0, Qt.AlignHCenter)
        layout.addWidget(self._separator(), 0, Qt.AlignHCenter)

        self.view_container = QFrame(self)
        view_layout = QVBoxLayout(self.view_container)
        self._view_layout = view_layout
        view_layout.setContentsMargins(0, 0, 0, 0)
        view_layout.setSpacing(4)
        self.switch_axes = ActionButton(self.view_container)
        self.switch_axes.setCheckable(True)
        self._configure_button(self.switch_axes, "显示坐标", "ic_fluent_grid_regular")
        self.switch_axes.setToolTip("显示坐标")
        self.switch_flip = ActionButton(self.view_container)
        self.switch_flip.setCheckable(True)
        self._configure_button(self.switch_flip, "图像翻转", "ic_fluent_flip_horizontal_regular")
        self.switch_flip.setToolTip("图像翻转")
        self.rotation_button = _RotationActionButton(self.view_container)
        self.rotation_button.setCheckable(True)
        self._configure_button(
            self.rotation_button, "Z 轴旋转", "ic_fluent_arrow_rotate_clockwise_regular"
        )
        for button in (self.switch_axes, self.switch_flip, self.rotation_button):
            view_layout.addWidget(button, 0, Qt.AlignHCenter)
        layout.addWidget(self.view_container, 0, Qt.AlignHCenter)
        layout.addWidget(self._separator(), 0, Qt.AlignHCenter)

        self.rotation_panel = RotationDialPanel(self)
        self.edit_rotation = self.rotation_panel.edit_rotation
        self._suppress_rotation_click = False
        self.rotation_button.clicked.connect(self.toggle_rotation_panel)
        self.rotation_panel.rotation_preview.connect(self.rotation_preview)
        self.rotation_panel.rotation_committed.connect(self.rotation_committed)
        self.rotation_panel.angle_presented.connect(self._on_rotation_angle_presented)
        self.rotation_panel.closed.connect(self._on_rotation_panel_closed)
        self._on_rotation_angle_presented(0.0)

        self.crop_button = crop_button
        self.erase_button = erase_button
        self.export_button = export_button
        self.selection_button = ActionButton(self)
        self.selection_button.setCheckable(True)
        self._configure_button(
            self.selection_button, "框选范围", "ic_fluent_select_object_regular"
        )
        layout.addWidget(self.selection_button, 0, Qt.AlignHCenter)
        for button, name, icon in (
            (self.crop_button, "裁剪", "ic_fluent_crop_regular"),
            (self.erase_button, "裁空", "ic_fluent_eraser_regular"),
        ):
            button.setParent(self)
            self._configure_button(button, name, icon)
            layout.addWidget(button, 0, Qt.AlignHCenter)
        self.export_button.setParent(self)
        self._configure_button(
            self.export_button, "导出结果", "ic_fluent_arrow_export_regular"
        )
        layout.addWidget(self.export_button, 0, Qt.AlignHCenter)

        self.selection_panel = QFrame(parent)
        self.selection_panel.setObjectName("canvas_selection_panel")
        self.selection_panel.setAttribute(Qt.WA_StyledBackground, True)
        self.selection_panel.setStyleSheet(
            f"QFrame#canvas_selection_panel {{ background-color: {theme.BG_2};"
            f"border: 1px solid {theme.BORDER_HEX}; border-radius: 7px; }}"
        )
        selection_layout = QVBoxLayout(self.selection_panel)
        self._selection_layout = selection_layout
        selection_layout.setContentsMargins(4, 4, 4, 4)
        selection_layout.setSpacing(4)
        self.edc_button = ActionButton(self.selection_panel)
        self.slice_dos_button = ActionButton(self.selection_panel)
        self.new_region_button = ActionButton(self.selection_panel)
        for button, name, icon in (
            (self.edc_button, "生成 EDC", "ic_fluent_data_trending_regular"),
            (self.slice_dos_button, "切片内强度积分", "ic_fluent_data_trending_filled"),
            (self.new_region_button, "新增框选范围", "ic_fluent_add_regular"),
        ):
            button.setParent(self.selection_panel)
            self._configure_button(button, name, icon)
            selection_layout.addWidget(button, 0, Qt.AlignHCenter)
        self.selection_panel.hide()
        self.selection_button.toggled.connect(self._on_selection_toggled)
        self.edc_button.clicked.connect(self.edc_requested.emit)
        self.slice_dos_button.clicked.connect(self.slice_dos_requested.emit)
        self.new_region_button.clicked.connect(self.new_region_requested.emit)

        layout.addWidget(self._separator(), 0, Qt.AlignHCenter)
        self.focus_button = ActionButton(self)
        self.focus_button.setCheckable(True)
        self._configure_button(self.focus_button, "专注画布", "ic_fluent_full_screen_maximize_regular")
        self.focus_button.toggled.connect(self._on_focus_toggled)
        self.focus_button.toggled.connect(self.focus_requested)
        layout.addWidget(self.focus_button, 0, Qt.AlignHCenter)
        self.adjustSize()

    def install_path_button(self, button):
        self.path_button = button
        button.setParent(self)
        self._configure_button(button, "折线路径切片", "ic_fluent_bezier_curve_square_regular")
        button.setCheckable(True)
        button.setToolTip("折线路径切片：左键加点，双击节点改名，右键生成")
        layout = self.layout()
        layout.insertWidget(max(0, layout.indexOf(self.selection_button)), button, 0, Qt.AlignHCenter)
        button.hide()
        self.adjustSize()
        self.reposition()

    def set_path_available(self, available):
        button = getattr(self, "path_button", None)
        if button is not None:
            button.setVisible(bool(available))
            self.adjustSize()
            self.reposition()

    def set_selection_panel_visible(self, visible):
        visible = bool(visible)
        self.selection_button.setChecked(visible)
        self._set_selection_panel_visible(visible)

    def close_selection_panel(self):
        self.selection_button.setChecked(False)
        self.selection_panel.hide()

    def _on_selection_toggled(self, checked):
        self._set_selection_panel_visible(checked)
        self.selection_toggled.emit(checked)

    def _set_selection_panel_visible(self, visible):
        if visible:
            self.close_rotation_panel(commit=False)
            self._position_selection_panel()
            self.selection_panel.show()
            self.selection_panel.raise_()
        else:
            self.selection_panel.hide()

    def _position_selection_panel(self):
        parent = self.parentWidget()
        if parent is None:
            return
        panel = self.selection_panel
        available_height = max(0, parent.height() - 24)
        compact = panel.sizeHint().height() > available_height
        self._selection_layout.setSpacing(0 if compact else 4)
        self._selection_layout.setContentsMargins(4, 2 if compact else 4, 4, 2 if compact else 4)
        panel.adjustSize()
        anchor = self.selection_button.mapTo(parent, QPoint(0, 0))
        x = anchor.x() - panel.width() - 8
        y = anchor.y() + (self.selection_button.height() - panel.height()) // 2
        x = max(0, min(x, max(0, parent.width() - panel.width())))
        y = max(0, min(y, max(0, parent.height() - panel.height())))
        panel.move(x, y)

    def rotation_angle(self):
        return self.rotation_panel.current_angle()

    def set_rotation_angle(self, angle):
        self.rotation_panel.set_angle(angle)

    def toggle_rotation_panel(self):
        if self._suppress_rotation_click:
            self._suppress_rotation_click = False
            return
        if self.rotation_panel.isVisible():
            self.close_rotation_panel()
        else:
            self.show_rotation_panel()

    def show_rotation_panel(self):
        self._suppress_rotation_click = False
        self.close_selection_panel()
        self._position_rotation_panel()
        self.rotation_panel.show()
        self.rotation_panel.raise_()
        self._sync_rotation_button()

    def close_rotation_panel(self, *, commit=True):
        if not self.rotation_panel.isVisible():
            self._sync_rotation_button()
            return
        if commit:
            self.rotation_panel.finish_editing()
            self.rotation_panel.hide()
        else:
            self.rotation_panel.dismiss()
        self._sync_rotation_button()

    def _position_rotation_panel(self):
        panel = self.rotation_panel
        panel.adjustSize()
        anchor = self.rotation_button.mapToGlobal(QPoint(0, 0))
        x = anchor.x() - panel.width() - 8
        y = anchor.y() + (self.rotation_button.height() - panel.height()) // 2
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            x = max(available.left(), min(x, available.right() - panel.width() + 1))
            y = max(available.top(), min(y, available.bottom() - panel.height() + 1))
        panel.move(x, y)

    def _on_rotation_angle_presented(self, angle):
        text = f"{float(angle):.2f}".rstrip("0").rstrip(".")
        text = text.replace("-", "−")
        self.rotation_button.setToolTip(f"Kx–Ky 平面旋转 {text}°")
        self._sync_rotation_button()

    def _sync_rotation_button(self):
        blocker = QSignalBlocker(self.rotation_button)
        self.rotation_button.setChecked(abs(self.rotation_angle()) >= 0.005)
        del blocker

    def _on_rotation_panel_closed(self):
        self._sync_rotation_button()
        if not QApplication.mouseButtons() & Qt.LeftButton:
            return
        local = self.rotation_button.mapFromGlobal(QCursor.pos())
        if self.rotation_button.rect().contains(local):
            self._suppress_rotation_click = True

    def _rounded_rect_path(self, pen_inset=0):
        inset = self._MASK_INSET + pen_inset
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()).adjusted(inset, inset, -inset, -inset), 7, 7
        )
        return path

    def paintEvent(self, _event):
        painter = QPainter(self)
        # 原生 VTK 画布由窗口遮罩露出，遮罩内的抗锯齿像素需要实色底层。
        border = QColor(theme.BORDER_HEX)
        painter.fillRect(self.rect(), border)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(border, 1))
        painter.setBrush(QColor(theme.BG_2))
        painter.drawPath(self._rounded_rect_path(pen_inset=0.5))

    def _update_corner_mask(self):
        mask = QRegion(self._rounded_rect_path().toFillPolygon().toPolygon())
        window = self.windowHandle()
        if window is None:
            self.setMask(mask)
        else:
            # QWidget 画满底层，只让原生窗口裁剪，避免两次取整留下黑线。
            if not self.mask().isEmpty():
                self.clearMask()
            window.setMask(mask)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_corner_mask()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_corner_mask()

    def hideEvent(self, event):
        self.close_rotation_panel(commit=False)
        self.close_selection_panel()
        super().hideEvent(event)

    @staticmethod
    def _configure_button(button, name, icon):
        button.setProperty("compactIconButton", True)
        button.setText("")
        button.setAccessibleName(name)
        button.setToolTip(name)
        theme.set_button_icon(button, icon, 16)
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(28, 26)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.StrongFocus)
        theme.style_push_button(button, "quiet")

    def _separator(self):
        line = QFrame()
        line.setFixedSize(18, 1)
        line.setStyleSheet(f"background-color: {theme.BORDER_HEX}; border: none;")
        self._separators.append(line)
        return line

    def _select_preset(self, preset):
        if preset != "3d":
            self.slice_requested.emit(preset)
            return
        if self._return_active and not self._3d_active:
            self.return_3d_requested.emit()
            return
        self.preset_buttons["3d"].setChecked(True)
        self.view_preset_requested.emit(preset)

    def _on_focus_toggled(self, checked):
        name = "ic_fluent_full_screen_minimize_regular" if checked else "ic_fluent_full_screen_maximize_regular"
        theme.set_button_icon(self.focus_button, name, 16)
        self.focus_button.setToolTip("恢复分析侧栏" if checked else "专注画布：隐藏分析与页面侧栏")

    def set_focus_mode(self, checked):
        self.focus_button.blockSignals(True)
        self.focus_button.setChecked(bool(checked))
        self.focus_button.blockSignals(False)
        self._on_focus_toggled(bool(checked))

    def set_3d_active(self, active):
        self._3d_active = bool(active)
        if active:
            self._return_active = False
        self.reset_button.setEnabled(self._3d_active)
        for button in self.preset_buttons.values():
            button.setEnabled(self._3d_active)
        self.reposition()

    def set_return_active(self, active):
        self._return_active = bool(active)
        self.preset_buttons["3d"].setEnabled(self._3d_active or self._return_active)
        self.reposition()

    def reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        for key, button in self.preset_buttons.items():
            button.setVisible(self._3d_active or (key == "3d" and self._return_active))
        available_height = max(0, parent.height() - 24)
        padding = 4 + self._MASK_INSET
        layout = self.layout()
        layout.setSpacing(4)
        self._view_layout.setSpacing(4)
        layout.setContentsMargins(padding, padding, padding, padding)
        for separator in self._separators:
            separator.show()

        presets_allowed = self._3d_active or self._return_active
        self.preset_container.setVisible(presets_allowed)
        layout.activate()
        full_height = layout.sizeHint().height()
        self.preset_container.setVisible(presets_allowed and full_height <= available_height)
        layout.activate()
        compact = layout.sizeHint().height() > available_height

        layout.setSpacing(0 if compact else 4)
        self._view_layout.setSpacing(0 if compact else 4)
        layout.setContentsMargins(
            padding, 2 if compact else padding, padding, 2 if compact else padding
        )
        compact_buttons = [
            *self.preset_buttons.values(),
            self.reset_button,
            self.switch_axes,
            self.switch_flip,
            self.rotation_button,
            self.selection_button,
            self.crop_button,
            self.erase_button,
            self.export_button,
            self.focus_button,
        ]
        path_button = getattr(self, "path_button", None)
        if path_button is not None:
            compact_buttons.append(path_button)
        for button in compact_buttons:
            button.setFixedHeight(22 if compact else 26)
        for separator in self._separators:
            separator.setVisible(not compact)
        layout.activate()
        self.resize(layout.sizeHint())
        self.move(max(0, parent.width() - self.width() - 12), 2 if compact else 12)
        self.raise_()
        if self.selection_panel.isVisible():
            self._position_selection_panel()
            self.selection_panel.raise_()
        if self.rotation_panel.isVisible():
            self._position_rotation_panel()
