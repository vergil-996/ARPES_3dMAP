"""画布右侧的视角预设与常用操作工具条。"""
from PyQt5.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen, QRegion
from PyQt5.QtWidgets import QFrame, QVBoxLayout

from bandscope.ui import theme
from bandscope.ui.ui_controls import ActionButton


class CanvasToolOverlay(QFrame):
    # 给原生窗口的像素取整留出余量，特别是在 Windows 分数缩放下。
    _MASK_INSET = 2
    reset_requested = pyqtSignal()
    focus_requested = pyqtSignal(bool)
    view_preset_requested = pyqtSignal(str)
    slice_requested = pyqtSignal(str)
    return_3d_requested = pyqtSignal()

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

        self.crop_button = crop_button
        self.erase_button = erase_button
        self.export_button = export_button
        for button, name, icon in (
            (self.crop_button, "裁剪", "ic_fluent_crop_regular"),
            (self.erase_button, "裁空", "ic_fluent_eraser_regular"),
            (self.export_button, "导出结果", "ic_fluent_arrow_export_regular"),
        ):
            button.setParent(self)
            self._configure_button(button, name, icon)
            layout.addWidget(button, 0, Qt.AlignHCenter)

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
        layout.insertWidget(3, button, 0, Qt.AlignHCenter)
        button.hide()
        self.adjustSize()
        self.reposition()

    def set_path_available(self, available):
        button = getattr(self, "path_button", None)
        if button is not None:
            button.setVisible(bool(available))
            self.adjustSize()
            self.reposition()

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
        theme.style_push_button(button, "quiet")

    @staticmethod
    def _separator():
        line = QFrame()
        line.setFixedSize(18, 1)
        line.setStyleSheet(f"background-color: {theme.BORDER_HEX}; border: none;")
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
        show_presets = (self._3d_active and parent.height() >= 320) or self._return_active
        self.preset_container.setVisible(show_presets)
        self.adjustSize()
        self.move(max(0, parent.width() - self.width() - 12), 12)
        self.raise_()
