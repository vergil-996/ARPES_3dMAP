from __future__ import annotations

import numpy as np
import matplotlib.patheffects as path_effects
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.transforms import blended_transform_factory
from PyQt5 import sip
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from bandscope.rendering.path_slice import axis_edges
from bandscope.ui import theme
from bandscope.ui.momentum_calibration_dialog import CalibrationSliceSlider


class EnergyCalibrationDialog(QDialog):
    apply_requested = pyqtSignal(float, float, float, str)
    save_requested = pyqtSignal()

    def __init__(self, core, *, plane="XZ", normal_index=0, scan_index=0, parent=None):
        super().__init__(parent)
        self.core = core
        self.raw_data = np.asarray(core.raw_data)
        if self.raw_data.ndim != 4 or any(length < 1 for length in self.raw_data.shape):
            raise ValueError("能量定标需要非空的 canonical [X, Y, E, T] 数据体。")
        self.x = self._coordinates("X", self.raw_data.shape[0])
        self.y = self._coordinates("Y", self.raw_data.shape[1])
        self.energy = self._coordinates("E", self.raw_data.shape[2])
        self.scan = self._coordinates("delay", self.raw_data.shape[3])
        plane = str(plane).upper()
        if plane not in {"XZ", "YZ"}:
            raise ValueError("能量定标切面必须是 XZ 或 YZ。")
        self._plane = plane
        self._normal_indices = {
            "XZ": self._bounded_index(normal_index, self.raw_data.shape[1]),
            "YZ": self._bounded_index(normal_index, self.raw_data.shape[0]),
        }
        energy_length = self.raw_data.shape[2]
        self._reference_index = float(min(energy_length // 3, energy_length - 1))
        self._second_index = float(min(max((2 * energy_length) // 3, 1), energy_length - 1))
        self._dragging_line = None
        self._applied = False
        self._saved = False
        self._status_error = False

        self.setWindowTitle("能量两点间距定标")
        self.setModal(False)
        self.resize(960, 740)
        self.setMinimumSize(680, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        self.title_label = QLabel("能量两点间距定标", self)
        self.title_label.setStyleSheet(theme.card_title_qss())
        root.addWidget(self.title_label)
        self.subtitle_label = QLabel(
            "在 XZ 或 YZ 切片上拖动两条能量虚线；以 E1 为零点、E1–E2 为目标间距重新生成均匀能量坐标，不改变强度数据。",
            self,
        )
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setStyleSheet(theme.field_label_qss())
        root.addWidget(self.subtitle_label)

        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._build_plot(scan_index))
        self.side_panel = QWidget(self.splitter)
        side_layout = QVBoxLayout(self.side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)
        self.controls = self._build_controls(scan_index)
        self.scroll_area = QScrollArea(self.side_panel)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setWidget(self.controls)
        side_layout.addWidget(self.scroll_area, 1)
        self.action_area = self._build_action_area(self.side_panel)
        side_layout.addWidget(self.action_area)
        self.side_panel.setMinimumWidth(292)
        self.side_panel.setMaximumWidth(356)
        self.splitter.addWidget(self.side_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([600, 316])
        root.addWidget(self.splitter, 1)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(20)
        root.addWidget(self.status_label)
        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.close_button = QPushButton("关闭", self)
        self.close_button.setAccessibleName("关闭能量定标")
        self.close_button.clicked.connect(self.reject)
        action_row.addWidget(self.close_button)
        root.addLayout(action_row)

        self._connect_changes()
        self._apply_theme()
        self._refresh_slice()
        self._refresh_reference_lines()
        self._refresh_measurement()
        theme.changed.connect(self._on_theme_changed)
        self.destroyed.connect(self._disconnect_theme)

    def _coordinates(self, role, length, core=None):
        source = self.core if core is None else core
        values = source.coords.get(role)
        if values is None:
            values = np.arange(length, dtype=np.float64)
        values = np.asarray(values, dtype=np.float64).reshape(-1)
        if values.size != length or not np.isfinite(values).all():
            raise ValueError(f"{role} 坐标与数据维度不匹配或包含非有限值。")
        return values

    @staticmethod
    def _bounded_index(value, length):
        return max(0, min(int(value), int(length) - 1))

    def _unit(self, role):
        return str((getattr(self.core, "coord_units", None) or {}).get(role) or "")

    def _scan_unit(self):
        return str(getattr(self.core, "scan_unit", None) or self._unit("delay"))

    @property
    def plane(self):
        return self._plane

    @property
    def normal_axis(self):
        return "Y" if self._plane == "XZ" else "X"

    @property
    def normal_coordinates(self):
        return self.y if self._plane == "XZ" else self.x

    @property
    def momentum_axis(self):
        return "X" if self._plane == "XZ" else "Y"

    @property
    def momentum_coordinates(self):
        return self.x if self._plane == "XZ" else self.y

    @property
    def normal_index(self):
        return int(self.normal_slider.value())

    @property
    def scan_index(self):
        return int(self.scan_slider.value())

    @property
    def reference_index(self):
        return float(self._reference_index)

    @property
    def second_index(self):
        return float(self._second_index)

    def _slice_array(self, plane, normal_index, scan_index):
        if plane == "XZ":
            values = self.raw_data[:, normal_index, :, scan_index]
        else:
            values = self.raw_data[normal_index, :, :, scan_index]
        return np.asarray(values, dtype=np.float64).T

    @property
    def current_slice(self):
        return self._slice_array(self._plane, self.normal_index, self.scan_index)

    def _build_plot(self, scan_index):
        card = QFrame(self)
        card.setObjectName("energyCalibrationPlotCard")
        self.plot_card = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        self.figure = Figure(figsize=(5.8, 4.8), dpi=100, constrained_layout=True)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumSize(340, 320)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.canvas.setFocusPolicy(Qt.ClickFocus)
        self.canvas.setAccessibleName("能量定标切片与双虚线画布")
        self.axes = self.figure.add_subplot(111)
        normal_index = self._normal_indices[self._plane]
        scan_index = self._bounded_index(scan_index, self.raw_data.shape[3])
        initial_values = self._slice_array(self._plane, normal_index, scan_index)
        self.image = self._create_image(initial_values)
        self._line_transform = blended_transform_factory(self.axes.transAxes, self.axes.transData)
        self.reference_line, self.reference_handle, self.reference_annotation = self._create_line_artists(
            "E1", "#FFFFFF", "#17191C", "o"
        )
        self.second_line, self.second_handle, self.second_annotation = self._create_line_artists(
            "E2", "#17191C", "#FFFFFF", "s"
        )
        self._line_artists = {
            "reference": (self.reference_line, self.reference_handle, self.reference_annotation),
            "second": (self.second_line, self.second_handle, self.second_annotation),
        }
        self.canvas.mpl_connect("button_press_event", self._on_canvas_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_canvas_motion)
        self.canvas.mpl_connect("button_release_event", self._on_canvas_release)
        layout.addWidget(self.canvas)
        return card

    def _create_image(self, values):
        return self.axes.pcolormesh(
            axis_edges(self.momentum_coordinates),
            axis_edges(self.energy),
            np.ma.masked_invalid(values),
            cmap="magma",
            shading="auto",
            rasterized=True,
            zorder=1,
        )

    def _create_line_artists(self, name, color, edge, marker):
        line, = self.axes.plot(
            [-0.055, 1.065],
            [0.0, 0.0],
            transform=self._line_transform,
            color=color,
            linewidth=1.35,
            linestyle=(0, (5, 4)),
            clip_on=False,
            zorder=20,
        )
        line.set_path_effects([
            path_effects.withStroke(linewidth=2.5, foreground=edge, alpha=0.9)
        ])
        handle, = self.axes.plot(
            [1.045],
            [0.0],
            transform=self._line_transform,
            linestyle="None",
            marker=marker,
            markersize=8.5,
            markerfacecolor=color,
            markeredgecolor=edge,
            markeredgewidth=1.4,
            clip_on=False,
            zorder=22,
        )
        annotation = self.axes.text(
            0.025,
            0.5,
            name,
            transform=self.axes.transAxes,
            color=theme.TEXT_1,
            fontsize=9,
            fontweight="bold",
            ha="left",
            va="center",
            clip_on=True,
            bbox={
                "boxstyle": "round,pad=0.2",
                "facecolor": theme.BG_1,
                "edgecolor": theme.BORDER_HEX,
                "linewidth": 0.6,
                "alpha": 0.88,
            },
            zorder=21,
        )
        return line, handle, annotation

    def _build_controls(self, scan_index):
        panel = QWidget(self.splitter)
        panel.setMinimumWidth(286)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 4, 4, 4)
        layout.setSpacing(9)

        self.slice_title_label = QLabel("切片", panel)
        self.slice_title_label.setStyleSheet(theme.section_label_qss())
        layout.addWidget(self.slice_title_label)
        plane_form = QFormLayout()
        plane_form.setHorizontalSpacing(10)
        self.plane_combo = QComboBox(panel)
        self.plane_combo.addItems(["XZ", "YZ"])
        self.plane_combo.setCurrentText(self._plane)
        self.plane_combo.setAccessibleName("能量定标切面")
        self.plane_combo.setFixedHeight(31)
        plane_form.addRow("切面", self.plane_combo)
        layout.addLayout(plane_form)

        normal_header = QHBoxLayout()
        self.normal_title_label = QLabel(panel)
        normal_header.addWidget(self.normal_title_label)
        normal_header.addStretch(1)
        self.normal_value_label = QLabel(panel)
        self.normal_value_label.setStyleSheet(theme.slider_value_qss())
        normal_header.addWidget(self.normal_value_label)
        layout.addLayout(normal_header)
        self.normal_slider = self._make_normal_slider(panel)
        layout.addWidget(self.normal_slider)

        scan_header = QHBoxLayout()
        self.scan_title_label = QLabel(getattr(self.core, "scan_name", "扫描帧"), panel)
        scan_header.addWidget(self.scan_title_label)
        scan_header.addStretch(1)
        self.scan_value_label = QLabel(panel)
        self.scan_value_label.setStyleSheet(theme.slider_value_qss())
        scan_header.addWidget(self.scan_value_label)
        layout.addLayout(scan_header)
        self.scan_slider = CalibrationSliceSlider(
            "delay",
            self.scan,
            label=getattr(self.core, "scan_name", "扫描帧"),
            unit=self._scan_unit(),
            source=(getattr(self.core, "coord_sources", None) or {}).get("delay", "index"),
            index=self._bounded_index(scan_index, self.raw_data.shape[3]),
            parent=panel,
        )
        self.scan_slider.setAccessibleName("能量定标扫描帧区间")
        layout.addWidget(self.scan_slider)
        self.scan_widgets = [self.scan_title_label, self.scan_value_label, self.scan_slider]
        self.slice_hint_label = QLabel(
            "预览取区间中心最近的采样切片，不做区间平均或求和；分离端点不会混合强度。",
            panel,
        )
        self.slice_hint_label.setWordWrap(True)
        self.slice_hint_label.setStyleSheet(theme.field_label_qss())
        layout.addWidget(self.slice_hint_label)

        self.separator = QFrame(panel)
        self.separator.setFrameShape(QFrame.HLine)
        self.separator.setStyleSheet(f"color: {theme.BORDER_HEX};")
        layout.addWidget(self.separator)

        self.lines_title_label = QLabel("能量参考线", panel)
        self.lines_title_label.setStyleSheet(theme.section_label_qss())
        layout.addWidget(self.lines_title_label)
        self.line_hint_label = QLabel(
            "拖动图内水平虚线或右侧图外手柄。E1 为参考零点，E2 为第二参考点。",
            panel,
        )
        self.line_hint_label.setWordWrap(True)
        self.line_hint_label.setStyleSheet(theme.field_label_qss())
        layout.addWidget(self.line_hint_label)
        self.reference_value_label = QLabel(panel)
        self.second_value_label = QLabel(panel)
        for label in (self.reference_value_label, self.second_value_label):
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setWordWrap(True)
            label.setStyleSheet(theme.slider_value_qss())
            layout.addWidget(label)

        self.flip_checkbox = QCheckBox("翻转能量方向（垂直）", panel)
        self.flip_checkbox.setAccessibleName("翻转能量图像方向")
        layout.addWidget(self.flip_checkbox)

        measurement_form = QFormLayout()
        measurement_form.setHorizontalSpacing(10)
        measurement_form.setVerticalSpacing(7)
        self.source_spacing_label = QLabel("–", panel)
        self.scale_label = QLabel("–", panel)
        for label in (self.source_spacing_label, self.scale_label):
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setStyleSheet(theme.slider_value_qss())
        measurement_form.addRow("逻辑间距", self.source_spacing_label)
        measurement_form.addRow("定标比例", self.scale_label)
        self.spacing_input = QDoubleSpinBox(panel)
        self.spacing_input.setAccessibleName("两点目标能量间距")
        self.spacing_input.setRange(1e-12, 1e12)
        self.spacing_input.setDecimals(9)
        self.spacing_input.setSingleStep(0.1)
        self.spacing_input.setValue(1.0)
        self.spacing_input.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spacing_input.setKeyboardTracking(False)
        self.spacing_input.setStyleSheet(theme.value_input_qss())
        self.spacing_input.setFixedHeight(31)
        measurement_form.addRow("目标间距", self.spacing_input)
        self.unit_edit = QLineEdit(self._unit("E") or "eV", panel)
        self.unit_edit.setAccessibleName("能量间距单位")
        self.unit_edit.setPlaceholderText("例如 eV")
        self.unit_edit.setStyleSheet(theme.value_input_qss())
        self.unit_edit.setFixedHeight(31)
        measurement_form.addRow("单位", self.unit_edit)
        layout.addLayout(measurement_form)
        layout.addStretch(1)
        return panel

    def _make_normal_slider(self, parent):
        axis = self.normal_axis
        return CalibrationSliceSlider(
            axis,
            self.normal_coordinates,
            label=axis,
            unit=self._unit(axis),
            source=(getattr(self.core, "coord_sources", None) or {}).get(axis, "index"),
            index=self._normal_indices[self._plane],
            parent=parent,
        )

    def _build_action_area(self, parent):
        area = QFrame(parent)
        area.setObjectName("energyCalibrationActionArea")
        layout = QVBoxLayout(area)
        layout.setContentsMargins(12, 8, 4, 4)
        layout.setSpacing(8)
        self.apply_button = QPushButton("应用定标", area)
        self.apply_button.setAccessibleName("应用能量定标")
        self.apply_button.setToolTip("以 E1 为零点、E1–E2 为目标间距重新生成均匀能量坐标")
        self.save_button = QPushButton("保存数据体…", area)
        self.save_button.setAccessibleName("保存能量定标数据体")
        self.save_button.setToolTip("应用成功后，导出包含完整强度与坐标元数据的 .mat 或 .npz")
        self.save_button.setEnabled(False)
        layout.addWidget(self.apply_button)
        layout.addWidget(self.save_button)
        return area

    def _connect_changes(self):
        self.plane_combo.currentTextChanged.connect(self._on_plane_changed)
        self.normal_slider.intervalChanged.connect(self._on_slice_changed)
        self.scan_slider.intervalChanged.connect(self._on_slice_changed)
        self.flip_checkbox.toggled.connect(self._on_flip_changed)
        self.spacing_input.valueChanged.connect(self._on_settings_changed)
        self.unit_edit.textChanged.connect(self._on_settings_changed)
        self.apply_button.clicked.connect(self._request_apply)
        self.save_button.clicked.connect(self._request_save)

    def _apply_theme(self):
        self.setStyleSheet(
            f"QDialog {{ background: {theme.BG_2}; color: {theme.TEXT_1}; }}"
            f"QLabel {{ color: {theme.TEXT_1}; }}"
            f"QComboBox {{ color: {theme.TEXT_1}; background: {theme.BG_1};"
            f"border: 1px solid {theme.BORDER_HEX}; border-radius: 5px; padding: 0 8px; }}"
            f"QComboBox QAbstractItemView {{ color: {theme.TEXT_1}; background: {theme.BG_3};"
            f"selection-background-color: {theme.BG_4}; }}"
            f"QCheckBox {{ color: {theme.TEXT_2}; background: transparent; }}"
        )
        self.title_label.setStyleSheet(theme.card_title_qss())
        for label in (self.subtitle_label, self.slice_hint_label, self.line_hint_label):
            label.setStyleSheet(theme.field_label_qss())
        for label in (self.slice_title_label, self.lines_title_label):
            label.setStyleSheet(theme.section_label_qss())
        for label in (
            self.normal_value_label,
            self.scan_value_label,
            self.reference_value_label,
            self.second_value_label,
            self.source_spacing_label,
            self.scale_label,
        ):
            label.setStyleSheet(theme.slider_value_qss())
        self.separator.setStyleSheet(f"color: {theme.BORDER_HEX};")
        self.plot_card.setStyleSheet(
            f"QFrame#energyCalibrationPlotCard {{ background: {theme.BG_1};"
            f"border: 1px solid {theme.BORDER_HEX}; border-radius: 8px; }}"
        )
        self.action_area.setStyleSheet(
            f"QFrame#energyCalibrationActionArea {{ background: {theme.BG_2};"
            f"border-top: 1px solid {theme.BORDER_HEX}; }}"
        )
        for button in (self.close_button, self.save_button):
            theme.style_push_button(button, "secondary")
        theme.style_push_button(self.apply_button, "primary")
        self.spacing_input.setStyleSheet(theme.value_input_qss())
        self.unit_edit.setStyleSheet(theme.value_input_qss())
        self.figure.set_facecolor(theme.BG_1)
        self.axes.set_facecolor(theme.CANVAS_BG)
        self.axes.tick_params(colors=theme.TEXT_2, labelsize=9)
        self.axes.xaxis.label.set_color(theme.TEXT_2)
        self.axes.yaxis.label.set_color(theme.TEXT_2)
        for spine in self.axes.spines.values():
            spine.set_color(theme.BORDER_HEX)
        for annotation in (self.reference_annotation, self.second_annotation):
            annotation.set_color(theme.TEXT_1)
            annotation.set_bbox({
                "boxstyle": "round,pad=0.2",
                "facecolor": theme.BG_1,
                "edgecolor": theme.BORDER_HEX,
                "linewidth": 0.6,
                "alpha": 0.88,
            })
        self.normal_slider.update()
        self.scan_slider.update()
        self._set_status(self.status_label.text(), error=self._status_error)
        self.canvas.draw_idle()

    def _on_theme_changed(self, _name):
        if not sip.isdeleted(self):
            self._apply_theme()

    def _disconnect_theme(self, *_args):
        theme.changed.disconnect(self._on_theme_changed)

    def _on_plane_changed(self, plane):
        plane = str(plane).upper()
        if plane == self._plane:
            return
        self._normal_indices[self._plane] = self.normal_index
        self._plane = plane
        self.normal_slider.rebind(
            self.normal_axis,
            self.normal_coordinates,
            label=self.normal_axis,
            unit=self._unit(self.normal_axis),
            source=(getattr(self.core, "coord_sources", None) or {}).get(self.normal_axis, "index"),
            index=self._normal_indices[self._plane],
        )
        self._invalidate_application()
        self._rebuild_image()
        self._refresh_slice()

    def _on_slice_changed(self, *_args):
        self._normal_indices[self._plane] = self.normal_index
        self._invalidate_application()
        self._refresh_slice()

    def _on_flip_changed(self, _checked):
        self._invalidate_application()
        self._apply_flip()
        self._refresh_reference_lines()
        self.canvas.draw_idle()

    def _apply_flip(self):
        edges = axis_edges(self.energy)
        low = float(np.min(edges))
        high = float(np.max(edges))
        if self.flip_checkbox.isChecked():
            self.axes.set_ylim(high, low)
        else:
            self.axes.set_ylim(low, high)

    def _rebuild_image(self):
        self.image.remove()
        self.image = self._create_image(self.current_slice)
        edges = axis_edges(self.momentum_coordinates)
        self.axes.set_xlim(float(np.min(edges)), float(np.max(edges)))
        self._apply_flip()
        self._refresh_reference_lines()
        self.canvas.draw_idle()

    def _refresh_slice(self):
        normal_index = self.normal_index
        scan_index = self.scan_index
        values = self.current_slice
        self.image.set_array(np.ma.masked_invalid(values))
        finite = values[np.isfinite(values)]
        low = float(finite.min()) if finite.size else 0.0
        high = float(finite.max()) if finite.size else 1.0
        self.image.set_clim(low, high if high > low else low + 1.0)
        axis = self.normal_axis
        self.normal_title_label.setText(f"{axis} 切片")
        self.normal_value_label.setText(
            self._slice_value_text(self.normal_coordinates, self.normal_slider, normal_index, self._unit(axis))
        )
        self.scan_value_label.setText(
            self._slice_value_text(self.scan, self.scan_slider, scan_index, self._scan_unit())
        )
        for widget in self.scan_widgets:
            widget.setVisible(self.raw_data.shape[3] > 1)
        momentum_axis = self.momentum_axis
        momentum_unit = self._unit(momentum_axis)
        energy_unit = self._unit("E")
        self.axes.set_xlabel(
            f"{momentum_axis} ({momentum_unit})" if momentum_unit else momentum_axis
        )
        self.axes.set_ylabel(f"E ({energy_unit})" if energy_unit else "E")
        self.canvas.draw_idle()

    @staticmethod
    def _slice_value_text(coordinates, slider, index, unit):
        text = f"中心 {coordinates[index]:.5g} {unit} · {index + 1}/{len(coordinates)}"
        if slider.interval.length > 0.0:
            low, up = slider.selected_indices()
            text += f" · 端点 {low + 1}–{up + 1}"
        return text.strip()

    def _physical_energy(self, logical_index):
        return float(self.core.logical_to_physical("E", logical_index, clamp=False))

    def _logical_energy(self, physical_value):
        value = float(self.core.physical_to_logical("E", physical_value, clamp=True))
        return min(max(value, 0.0), float(self.raw_data.shape[2] - 1))

    def set_reference_index(self, value):
        self._set_line_index("reference", value)

    def set_second_index(self, value):
        self._set_line_index("second", value)

    def _set_line_index(self, name, value):
        value = float(value)
        if not np.isfinite(value):
            return
        value = min(max(value, 0.0), float(self.raw_data.shape[2] - 1))
        attribute = "_reference_index" if name == "reference" else "_second_index"
        if getattr(self, attribute) == value:
            return
        setattr(self, attribute, value)
        self._invalidate_application()
        self._refresh_reference_lines()
        self._refresh_measurement()
        self.canvas.draw_idle()

    def _annotation_layout(self, name, physical):
        lower, upper = self.axes.get_ylim()
        span = upper - lower
        fraction = 0.5 if span == 0 else (physical - lower) / span
        if name == "reference":
            above = fraction <= 0.88
        else:
            above = fraction < 0.12
        offset = 0.018 if above else -0.018
        axes_y = min(max(fraction + offset, 0.025), 0.975)
        return (0.025, axes_y), "bottom" if above else "top"

    def _refresh_reference_lines(self):
        for name, logical in (
            ("reference", self._reference_index),
            ("second", self._second_index),
        ):
            physical = self._physical_energy(logical)
            line, handle, annotation = self._line_artists[name]
            line.set_data([-0.055, 1.065], [physical, physical])
            handle.set_data([1.045], [physical])
            position, vertical_alignment = self._annotation_layout(name, physical)
            annotation.set_position(position)
            annotation.set_verticalalignment(vertical_alignment)
        energy_unit = self._unit("E")
        self.reference_value_label.setText(
            f"E1  逻辑 {self._reference_index:.3f} · {self._physical_energy(self._reference_index):.6g} {energy_unit}".rstrip()
        )
        self.second_value_label.setText(
            f"E2  逻辑 {self._second_index:.3f} · {self._physical_energy(self._second_index):.6g} {energy_unit}".rstrip()
        )
        self.canvas.draw_idle()

    def _line_at_event(self, event):
        handle_hits = []
        for name, logical in (
            ("reference", self._reference_index),
            ("second", self._second_index),
        ):
            handle = self._line_artists[name][1]
            x, y = handle.get_transform().transform((1.045, self._physical_energy(logical)))
            distance = float(np.hypot(event.x - x, event.y - y))
            if distance <= 12.0:
                handle_hits.append((distance, name))
        if handle_hits:
            return min(handle_hits)[1]
        bounds = self.axes.bbox
        if not bounds.x0 - 35 <= event.x <= bounds.x1 + 55:
            return None
        line_hits = []
        for name, logical in (
            ("reference", self._reference_index),
            ("second", self._second_index),
        ):
            _x, y = self.axes.transData.transform((0.0, self._physical_energy(logical)))
            distance = abs(float(event.y - y))
            if distance <= 7.0:
                line_hits.append((distance, name))
        return min(line_hits)[1] if line_hits else None

    def _on_canvas_press(self, event):
        if event.button != 1:
            return
        self._dragging_line = self._line_at_event(event)
        if self._dragging_line is not None:
            self._drag_line_to_event(event)

    def _on_canvas_motion(self, event):
        if self._dragging_line is not None:
            self._drag_line_to_event(event)

    def _on_canvas_release(self, event):
        if event.button == 1:
            self._dragging_line = None

    def _drag_line_to_event(self, event):
        _x, physical = self.axes.transData.inverted().transform((event.x, event.y))
        self._set_line_index(self._dragging_line, self._logical_energy(physical))

    def _calibration(self):
        target = float(self.spacing_input.value())
        if not np.isfinite(target) or target <= 0:
            raise ValueError("目标能量间距必须是有限的正数。")
        from bandscope.processing.energy_calibration import calibrate_energy_axis

        return calibrate_energy_axis(
            self.energy,
            self._reference_index,
            self._second_index,
            target,
            unit=self.unit_edit.text().strip(),
        )

    def _refresh_measurement(self):
        try:
            result = self._calibration()
        except (TypeError, ValueError) as exc:
            self.source_spacing_label.setText("–")
            self.scale_label.setText("–")
            self._set_status(str(exc), error=True)
            return
        if self._status_error:
            self._set_status("")
        self.source_spacing_label.setText(f"{float(result.source_spacing):.7g} 采样")
        unit = self.unit_edit.text().strip()
        self.scale_label.setText(f"{float(result.scale):.7g} {unit}/采样".rstrip())

    def _on_settings_changed(self, *_args):
        self._invalidate_application()
        self._refresh_measurement()

    def _invalidate_application(self):
        was_applied = self._applied
        self._applied = False
        self._saved = False
        self.save_button.setEnabled(False)
        if was_applied:
            self._set_status("设置已更改，请重新应用定标后再保存。")

    def _request_apply(self):
        try:
            result = self._calibration()
        except (TypeError, ValueError) as exc:
            self._set_status(str(exc), error=True)
            return
        self._invalidate_application()
        self._set_status("正在应用定标…")
        self.apply_requested.emit(
            float(result.reference_index),
            float(result.second_index),
            float(result.target_spacing),
            str(result.unit),
        )

    def _request_save(self):
        if self.save_button.isEnabled():
            self._set_status("正在保存完整数据体…")
            self.save_requested.emit()

    def _adopt_core(self, core):
        raw_data = np.asarray(core.raw_data)
        if raw_data.ndim != 4 or raw_data.shape != self.raw_data.shape:
            raise ValueError("定标后的 canonical 数据维度发生变化。")
        x = self._coordinates("X", raw_data.shape[0], core=core)
        y = self._coordinates("Y", raw_data.shape[1], core=core)
        energy = self._coordinates("E", raw_data.shape[2], core=core)
        scan = self._coordinates("delay", raw_data.shape[3], core=core)
        self.core = core
        self.raw_data = raw_data
        self.x = x
        self.y = y
        self.energy = energy
        self.scan = scan
        self._rebuild_image()
        self._refresh_slice()
        self._refresh_reference_lines()
        self._refresh_measurement()

    def set_applied(self, success: bool, message: str = "", core=None):
        if success and core is not None:
            self._adopt_core(core)
        self._applied = bool(success)
        self._saved = False
        self.save_button.setEnabled(self._applied)
        self._set_status(
            message or ("定标已应用，可保存完整数据体。" if success else "应用失败，请检查参考线与目标间距。"),
            error=not success,
        )

    def set_saved(self, success: bool, message: str = ""):
        self._saved = bool(success)
        self._set_status(
            message or ("完整数据体已保存。" if success else "保存失败，数据体尚未导出。"),
            error=not success,
        )

    def _set_status(self, message, *, error=False):
        self._status_error = bool(error)
        self.status_label.setText(str(message))
        self.status_label.setStyleSheet(
            f"color: {theme.DANGER if error else theme.TEXT_2}; background: transparent;"
        )


__all__ = ["EnergyCalibrationDialog"]
