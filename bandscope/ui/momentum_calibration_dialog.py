from __future__ import annotations

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5 import sip
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QShortcut,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from bandscope.core.axis_interval import AxisSpace, nearest_index
from bandscope.rendering.path_slice import axis_edges
from bandscope.ui import theme
from bandscope.ui.axis_interval_bar import DualRangeSlider


class CalibrationSliceSlider(DualRangeSlider):
    valueChanged = pyqtSignal(int)
    intervalChanged = pyqtSignal()

    def __init__(
        self,
        axis_key,
        coordinates,
        *,
        label=None,
        unit="",
        source="file",
        index=0,
        parent=None,
    ):
        super().__init__(parent)
        self.rebind(
            axis_key,
            coordinates,
            label=label,
            unit=unit,
            source=source,
            index=index,
        )
        self.endpointPreview.connect(self._change_endpoint)
        self.endpointCommitted.connect(self._change_endpoint)
        self.centerPreview.connect(self._change_center)
        self.lockRequested.connect(self._change_lock)

    def rebind(
        self,
        axis_key,
        coordinates,
        *,
        label=None,
        unit="",
        source="file",
        index=0,
    ):
        self.coordinate_popup.hide()
        self.space = AxisSpace(
            axis_key,
            coordinates,
            label=label,
            unit=unit,
            source=source,
        )
        initial = self.space.coords[self._bounded_value(index)]
        self.interval = self.space.as_interval(low=initial, up=initial, locked=True)
        self.sync(self.space, self.interval)

    def _bounded_value(self, value):
        return max(0, min(int(value), self.space.sample_count - 1))

    def minimum(self):
        return 0

    def maximum(self):
        return self.space.sample_count - 1

    def value(self):
        return nearest_index(self.space.coords, self.interval.center)

    def selected_indices(self):
        return self.interval.to_indices(self.space.coords)

    def setValue(self, value):
        index = self._bounded_value(value)
        previous = self._state()
        previous_value = self.value()
        self.interval.set_length(0.0)
        self.interval.set_center(float(self.space.coords[index]))
        self.interval.set_locked(True)
        self._sync_after_change(previous, previous_value)

    def _state(self):
        return self.interval.low, self.interval.up, self.interval.locked

    def _sync_after_change(self, previous, previous_value):
        self.sync(self.space, self.interval)
        if self._state() != previous:
            self.intervalChanged.emit()
        value = self.value()
        if value != previous_value:
            self.valueChanged.emit(value)

    def _change_endpoint(self, endpoint, value):
        previous = self._state()
        previous_value = self.value()
        if endpoint == "low":
            self.interval.set_low(value)
        else:
            self.interval.set_up(value)
        self._sync_after_change(previous, previous_value)

    def _change_center(self, value):
        previous = self._state()
        previous_value = self.value()
        self.interval.set_center(value)
        self._sync_after_change(previous, previous_value)

    def _change_lock(self, locked):
        previous = self._state()
        previous_value = self.value()
        self.interval.set_locked(locked)
        self._sync_after_change(previous, previous_value)


class MomentumCalibrationDialog(QDialog):
    apply_requested = pyqtSignal(tuple, tuple, float, str)
    save_requested = pyqtSignal()

    def __init__(self, core, *, energy_index=0, scan_index=0, parent=None):
        super().__init__(parent)
        self.core = core
        self.raw_data = np.asarray(core.raw_data)
        if self.raw_data.ndim != 4 or any(length < 1 for length in self.raw_data.shape):
            raise ValueError("两点定标需要非空的 canonical [X, Y, E, T] 数据体。")
        self.x = self._coordinates("X", self.raw_data.shape[0])
        self.y = self._coordinates("Y", self.raw_data.shape[1])
        self.energy = self._coordinates("E", self.raw_data.shape[2])
        self.scan = self._coordinates("delay", self.raw_data.shape[3])
        self._points = []
        self._applied = False
        self._saved = False
        self._distance_touched = False
        self._status_error = False

        self.setWindowTitle("Kx / Ky 两点距离定标")
        self.setModal(False)
        self.resize(920, 720)
        self.setMinimumSize(640, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        title = QLabel("Kx / Ky 两点距离定标", self)
        self.title_label = title
        title.setStyleSheet(theme.card_title_qss())
        root.addWidget(title)
        subtitle = QLabel(
            "在同一 XY 切片上选取两点；切片取区间中心最近采样（不积分），仅统一缩放 Kx、Ky，保留零点、方向、采样比例与强度数据。",
            self,
        )
        self.subtitle_label = subtitle
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(theme.field_label_qss())
        root.addWidget(subtitle)

        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._build_plot())
        self.side_panel = QWidget(self.splitter)
        side_layout = QVBoxLayout(self.side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)
        self.controls = self._build_controls()
        self.scroll_area = QScrollArea(self.side_panel)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setWidget(self.controls)
        side_layout.addWidget(self.scroll_area, 1)
        self.action_area = self._build_action_area(self.side_panel)
        side_layout.addWidget(self.action_area)
        self.side_panel.setMinimumWidth(276)
        self.side_panel.setMaximumWidth(340)
        self.splitter.addWidget(self.side_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([580, 300])
        root.addWidget(self.splitter, 1)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(20)
        root.addWidget(self.status_label)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addStretch(1)
        self.close_button = QPushButton("关闭", self)
        self.close_button.setAccessibleName("关闭两点定标")
        self.close_button.clicked.connect(self.reject)
        action_row.addWidget(self.close_button)
        root.addLayout(action_row)

        self._undo_shortcut = QShortcut(QKeySequence.Undo, self)
        self._undo_shortcut.activated.connect(self.undo_point)
        self._delete_shortcut = QShortcut(QKeySequence.Delete, self)
        self._delete_shortcut.activated.connect(self.clear_points)

        self.energy_slider.setValue(self._bounded_index(energy_index, self.raw_data.shape[2]))
        self.scan_slider.setValue(self._bounded_index(scan_index, self.raw_data.shape[3]))
        self._connect_changes()
        self._apply_theme()
        self._refresh_slice()
        self._refresh_points()
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

    def _build_plot(self):
        card = QFrame(self)
        card.setObjectName("calibrationPlotCard")
        self.plot_card = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        self.figure = Figure(figsize=(5.6, 4.8), dpi=100, constrained_layout=True)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumSize(320, 300)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.canvas.setFocusPolicy(Qt.ClickFocus)
        self.canvas.setAccessibleName("XY 动量切片选点画布")
        self.axes = self.figure.add_subplot(111)
        self.image = self.axes.pcolormesh(
            axis_edges(self.x),
            axis_edges(self.y),
            np.ma.masked_invalid(self.raw_data[:, :, 0, 0].T),
            cmap="magma",
            shading="auto",
            rasterized=True,
        )
        self.axes.set_aspect("equal", adjustable="box")
        self._selection_line, = self.axes.plot(
            [], [], color="#FFFFFF", linewidth=1.6, zorder=20
        )
        self._markers = self.axes.scatter(
            [], [], s=58, facecolors="#FFFFFF", edgecolors="#17191C", linewidths=1.2, zorder=21
        )
        self._point_annotations = [
            self.axes.text(0, 0, name, color="#FFFFFF", fontsize=10, fontweight="bold", zorder=22)
            for name in ("P1", "P2")
        ]
        for annotation in self._point_annotations:
            annotation.set_visible(False)
        self.canvas.mpl_connect("button_press_event", self._on_canvas_press)
        layout.addWidget(self.canvas)
        return card

    def _build_controls(self):
        panel = QWidget(self.splitter)
        panel.setMinimumWidth(270)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 4, 4, 4)
        layout.setSpacing(9)

        slice_title = QLabel("切片", panel)
        self.slice_title_label = slice_title
        slice_title.setStyleSheet(theme.section_label_qss())
        layout.addWidget(slice_title)
        energy_header = QHBoxLayout()
        energy_header.addWidget(QLabel("能量切片", panel))
        energy_header.addStretch(1)
        self.energy_value_label = QLabel(panel)
        self.energy_value_label.setStyleSheet(theme.slider_value_qss())
        energy_header.addWidget(self.energy_value_label)
        layout.addLayout(energy_header)
        self.energy_slider = CalibrationSliceSlider(
            "E",
            self.energy,
            label="能量",
            unit=str(self.core.coord_units.get("E") or ""),
            source=(getattr(self.core, "coord_sources", None) or {}).get("E", "index"),
            parent=panel,
        )
        self.energy_slider.setAccessibleName("定标能量切片区间")
        layout.addWidget(self.energy_slider)

        self.scan_widgets = [QLabel(getattr(self.core, "scan_name", "扫描帧"), panel)]
        scan_header = QHBoxLayout()
        scan_header.addWidget(self.scan_widgets[0])
        scan_header.addStretch(1)
        self.scan_value_label = QLabel(panel)
        self.scan_value_label.setStyleSheet(theme.slider_value_qss())
        self.scan_widgets.append(self.scan_value_label)
        scan_header.addWidget(self.scan_value_label)
        self.scan_header_layout = scan_header
        layout.addLayout(scan_header)
        self.scan_slider = CalibrationSliceSlider(
            "delay",
            self.scan,
            label=getattr(self.core, "scan_name", "扫描帧"),
            unit=str(getattr(self.core, "scan_unit", None) or self.core.coord_units.get("delay") or ""),
            source=(getattr(self.core, "coord_sources", None) or {}).get("delay", "index"),
            parent=panel,
        )
        self.scan_slider.setAccessibleName("定标扫描帧区间")
        layout.addWidget(self.scan_slider)
        self.scan_widgets.append(self.scan_slider)

        separator = QFrame(panel)
        self.separator = separator
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet(f"color: {theme.BORDER_HEX};")
        layout.addWidget(separator)

        points_header = QHBoxLayout()
        points_title = QLabel("两点", panel)
        self.points_title_label = points_title
        points_title.setStyleSheet(theme.section_label_qss())
        points_header.addWidget(points_title)
        points_header.addStretch(1)
        self.undo_button = QPushButton("撤销", panel)
        self.undo_button.setAccessibleName("撤销定标点")
        self.clear_button = QPushButton("清除", panel)
        self.clear_button.setAccessibleName("清除定标点")
        points_header.addWidget(self.undo_button)
        points_header.addWidget(self.clear_button)
        layout.addLayout(points_header)
        self.point_hint_label = QLabel(panel)
        self.point_hint_label.setWordWrap(True)
        self.point_hint_label.setStyleSheet(theme.field_label_qss())
        layout.addWidget(self.point_hint_label)
        self.point_a_label = QLabel("P1  尚未选择", panel)
        self.point_b_label = QLabel("P2  尚未选择", panel)
        for label in (self.point_a_label, self.point_b_label):
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setWordWrap(True)
            label.setStyleSheet(theme.slider_value_qss())
            layout.addWidget(label)

        measurement_form = QFormLayout()
        measurement_form.setHorizontalSpacing(10)
        measurement_form.setVerticalSpacing(7)
        self.source_distance_label = QLabel("–", panel)
        self.scale_label = QLabel("–", panel)
        for label in (self.source_distance_label, self.scale_label):
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setStyleSheet(theme.slider_value_qss())
        measurement_form.addRow("当前间距", self.source_distance_label)
        measurement_form.addRow("统一比例", self.scale_label)
        layout.addLayout(measurement_form)

        self.distance_input = QDoubleSpinBox(panel)
        self.distance_input.setAccessibleName("两点实际间距")
        self.distance_input.setRange(1e-12, 1e12)
        self.distance_input.setDecimals(9)
        self.distance_input.setSingleStep(0.1)
        self.distance_input.setValue(1.0)
        self.distance_input.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.distance_input.setKeyboardTracking(False)
        self.distance_input.setStyleSheet(theme.value_input_qss())
        self.distance_input.setFixedHeight(31)
        measurement_form.addRow("实际间距", self.distance_input)

        x_unit = str(self.core.coord_units.get("X") or "")
        y_unit = str(self.core.coord_units.get("Y") or "")
        common_unit = x_unit if x_unit == y_unit else ""
        self.unit_edit = QLineEdit(common_unit or "Å⁻¹", panel)
        self.unit_edit.setAccessibleName("定标距离单位")
        self.unit_edit.setPlaceholderText("例如 Å⁻¹")
        self.unit_edit.setStyleSheet(theme.value_input_qss())
        self.unit_edit.setFixedHeight(31)
        measurement_form.addRow("单位", self.unit_edit)
        layout.addSpacing(4)

        layout.addStretch(1)
        return panel

    def _build_action_area(self, parent):
        area = QFrame(parent)
        area.setObjectName("calibrationActionArea")
        layout = QVBoxLayout(area)
        layout.setContentsMargins(12, 8, 4, 4)
        layout.setSpacing(8)
        self.apply_button = QPushButton("应用定标", area)
        self.apply_button.setAccessibleName("应用两点动量定标")
        self.apply_button.setToolTip("将两点间距设为实际值，统一缩放 Kx 与 Ky")
        self.save_button = QPushButton("保存数据体…", area)
        self.save_button.setAccessibleName("保存定标数据体")
        self.save_button.setToolTip("应用成功后，导出包含完整强度与坐标元数据的 .mat 或 .npz")
        self.save_button.setEnabled(False)
        layout.addWidget(self.apply_button)
        layout.addWidget(self.save_button)
        return area

    def _connect_changes(self):
        self.energy_slider.intervalChanged.connect(self._on_slice_changed)
        self.scan_slider.intervalChanged.connect(self._on_slice_changed)
        self.distance_input.valueChanged.connect(self._on_distance_changed)
        self.unit_edit.textChanged.connect(self._on_settings_changed)
        self.undo_button.clicked.connect(self.undo_point)
        self.clear_button.clicked.connect(self.clear_points)
        self.apply_button.clicked.connect(self._request_apply)
        self.save_button.clicked.connect(self._request_save)

    def _apply_theme(self):
        self.setStyleSheet(
            f"QDialog {{ background: {theme.BG_2}; color: {theme.TEXT_1}; }}"
            f"QLabel {{ color: {theme.TEXT_1}; }}"
        )
        self.title_label.setStyleSheet(theme.card_title_qss())
        for label in (self.subtitle_label, self.point_hint_label):
            label.setStyleSheet(theme.field_label_qss())
        for label in (self.slice_title_label, self.points_title_label):
            label.setStyleSheet(theme.section_label_qss())
        for label in (
            self.energy_value_label,
            self.scan_value_label,
            self.point_a_label,
            self.point_b_label,
            self.source_distance_label,
            self.scale_label,
        ):
            label.setStyleSheet(theme.slider_value_qss())
        self.separator.setStyleSheet(f"color: {theme.BORDER_HEX};")
        self.plot_card.setStyleSheet(
            f"QFrame#calibrationPlotCard {{ background: {theme.BG_1};"
            f"border: 1px solid {theme.BORDER_HEX}; border-radius: 8px; }}"
        )
        self.action_area.setStyleSheet(
            f"QFrame#calibrationActionArea {{ background: {theme.BG_2};"
            f"border-top: 1px solid {theme.BORDER_HEX}; }}"
        )
        for button in (self.undo_button, self.clear_button, self.close_button, self.save_button):
            theme.style_push_button(button, "secondary")
        theme.style_push_button(self.apply_button, "primary")
        self.distance_input.setStyleSheet(theme.value_input_qss())
        self.unit_edit.setStyleSheet(theme.value_input_qss())
        self.figure.set_facecolor(theme.BG_1)
        self.axes.set_facecolor(theme.CANVAS_BG)
        self.axes.tick_params(colors=theme.TEXT_2, labelsize=9)
        self.axes.xaxis.label.set_color(theme.TEXT_2)
        self.axes.yaxis.label.set_color(theme.TEXT_2)
        for spine in self.axes.spines.values():
            spine.set_color(theme.BORDER_HEX)
        self._set_status(self.status_label.text(), error=self._status_error)
        self.canvas.draw_idle()

    def _on_theme_changed(self, _name):
        if not sip.isdeleted(self):
            self._apply_theme()

    def _disconnect_theme(self, *_args):
        theme.changed.disconnect(self._on_theme_changed)

    @property
    def energy_index(self):
        return int(self.energy_slider.value())

    @property
    def scan_index(self):
        return int(self.scan_slider.value())

    @property
    def point_a(self):
        return tuple(self._points[0]) if self._points else None

    @property
    def point_b(self):
        return tuple(self._points[1]) if len(self._points) > 1 else None

    def _on_canvas_press(self, event):
        if event.button != 1 or event.inaxes is not self.axes:
            return
        if event.xdata is None or event.ydata is None:
            return
        point = (
            float(self.core.physical_to_logical("X", event.xdata, clamp=True)),
            float(self.core.physical_to_logical("Y", event.ydata, clamp=True)),
        )
        if not self._point_in_bounds(point):
            self._set_status("选点超出数据范围，请在有效 Kx / Ky 坐标内点击。", error=True)
            return
        if len(self._points) < 2:
            self._points.append(point)
        else:
            self._points[1] = point
        self._refresh_points()

    def _point_in_bounds(self, point):
        return bool(
            np.isfinite(point).all()
            and 0.0 <= point[0] <= self.raw_data.shape[0] - 1
            and 0.0 <= point[1] <= self.raw_data.shape[1] - 1
        )

    def undo_point(self):
        if not self._points:
            return
        self._points.pop()
        self._refresh_points()

    def clear_points(self):
        self._points.clear()
        self._refresh_points()

    def _refresh_points(self):
        self._invalidate_application()
        self.undo_button.setEnabled(bool(self._points))
        self.clear_button.setEnabled(bool(self._points))
        self._refresh_point_labels()
        if not self._points:
            hint = "左键选择 P1。"
        elif len(self._points) == 1:
            hint = "左键选择 P2；Ctrl+Z 可撤销。"
        else:
            hint = "再次左键点击可替换 P2；Ctrl+Z 撤销，Delete 清除。"
        self.point_hint_label.setText(hint)
        self._refresh_measurement(reset_target=len(self._points) == 2)
        self._refresh_overlay()

    def _refresh_point_labels(self):
        for label, point, name in (
            (self.point_a_label, self.point_a, "P1"),
            (self.point_b_label, self.point_b, "P2"),
        ):
            if point is None:
                label.setText(f"{name}  尚未选择")
                continue
            physical = self._physical_point(point)
            label.setText(
                f"{name}  逻辑 ({point[0]:.3f}, {point[1]:.3f})\n"
                f"     Kx {physical[0]:.5g} · Ky {physical[1]:.5g}"
            )

    def _physical_point(self, point):
        return (
            float(self.core.logical_to_physical("X", point[0], clamp=False)),
            float(self.core.logical_to_physical("Y", point[1], clamp=False)),
        )

    def _refresh_overlay(self):
        physical = [self._physical_point(point) for point in self._points]
        if physical:
            points = np.asarray(physical, dtype=np.float64)
            self._markers.set_offsets(points)
            self._markers.set_visible(True)
        else:
            self._markers.set_offsets(np.empty((0, 2)))
            self._markers.set_visible(False)
        if len(physical) == 2:
            self._selection_line.set_data(points[:, 0], points[:, 1])
        else:
            self._selection_line.set_data([], [])
        for index, annotation in enumerate(self._point_annotations):
            visible = index < len(physical)
            annotation.set_visible(visible)
            if visible:
                annotation.set_position((physical[index][0] + 0.015 * self._x_span(), physical[index][1]))
        self.canvas.draw_idle()

    def _x_span(self):
        return max(float(np.ptp(self.x)), 1.0)

    def _calibration(self):
        if len(self._points) != 2:
            raise ValueError("请先在 XY 切片上选择 P1 和 P2。")
        target = float(self.distance_input.value())
        if not np.isfinite(target) or target <= 0:
            raise ValueError("实际间距必须是有限的正数。")
        from bandscope.processing.momentum_calibration import calibrate_momentum_axes

        return calibrate_momentum_axes(
            self.x,
            self.y,
            self.point_a,
            self.point_b,
            target,
            unit=self.unit_edit.text().strip(),
        )

    def _refresh_measurement(self, *, reset_target=False):
        if len(self._points) != 2:
            self.source_distance_label.setText("–")
            self.scale_label.setText("–")
            return
        try:
            result = self._calibration()
        except (TypeError, ValueError) as exc:
            self.source_distance_label.setText("–")
            self.scale_label.setText("–")
            self._set_status(str(exc), error=True)
            return
        if self._status_error:
            self._set_status("")
        source = float(result.source_distance)
        unit = self.unit_edit.text().strip()
        self.source_distance_label.setText(f"{source:.7g} {unit}".rstrip())
        self.scale_label.setText(f"{float(result.scale):.7g} ×")
        if reset_target and not self._distance_touched:
            self.distance_input.blockSignals(True)
            self.distance_input.setValue(min(max(source, 1e-12), 1e12))
            self.distance_input.blockSignals(False)
            self.scale_label.setText(f"{float(self.distance_input.value()) / source:.7g} ×")

    def _on_slice_changed(self, *_args):
        self._invalidate_application()
        self._refresh_slice()

    def _refresh_slice(self):
        energy_index = self.energy_index
        scan_index = self.scan_index
        values = np.asarray(self.raw_data[:, :, energy_index, scan_index], dtype=np.float64).T
        self.image.set_array(np.ma.masked_invalid(values))
        finite = values[np.isfinite(values)]
        low = float(finite.min()) if finite.size else 0.0
        high = float(finite.max()) if finite.size else 1.0
        self.image.set_clim(low, high if high > low else low + 1.0)
        energy_unit = str(self.core.coord_units.get("E") or "")
        self.energy_value_label.setText(
            self._slice_value_text(
                self.energy, self.energy_slider, energy_index, energy_unit
            )
        )
        scan_unit = str(getattr(self.core, "scan_unit", None) or self.core.coord_units.get("delay") or "")
        self.scan_value_label.setText(
            self._slice_value_text(self.scan, self.scan_slider, scan_index, scan_unit)
        )
        show_scan = self.raw_data.shape[3] > 1
        for widget in self.scan_widgets:
            widget.setVisible(show_scan)
        x_unit = str(self.core.coord_units.get("X") or "")
        y_unit = str(self.core.coord_units.get("Y") or "")
        self.axes.set_xlabel(f"Kx ({x_unit})" if x_unit else "Kx")
        self.axes.set_ylabel(f"Ky ({y_unit})" if y_unit else "Ky")
        self.canvas.draw_idle()

    @staticmethod
    def _slice_value_text(coordinates, slider, index, unit):
        text = f"中心 {coordinates[index]:.5g} {unit} · {index + 1}/{len(coordinates)}"
        if slider.interval.length > 0.0:
            low, up = slider.selected_indices()
            text += f" · 端点 {low + 1}–{up + 1}"
        return text.strip()

    def _on_distance_changed(self, _value):
        self._distance_touched = True
        self._on_settings_changed()

    def _on_settings_changed(self, *_args):
        self._invalidate_application()
        if len(self._points) == 2:
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
            tuple(float(value) for value in result.point_a),
            tuple(float(value) for value in result.point_b),
            float(result.target_distance),
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

        self.image.remove()
        x_edges = axis_edges(self.x)
        y_edges = axis_edges(self.y)
        values = np.asarray(
            self.raw_data[:, :, self.energy_index, self.scan_index], dtype=np.float64
        ).T
        self.image = self.axes.pcolormesh(
            x_edges,
            y_edges,
            np.ma.masked_invalid(values),
            cmap="magma",
            shading="auto",
            rasterized=True,
        )
        self.axes.set_xlim(float(np.min(x_edges)), float(np.max(x_edges)))
        self.axes.set_ylim(float(np.min(y_edges)), float(np.max(y_edges)))
        self._refresh_slice()
        self._refresh_point_labels()
        self._refresh_overlay()
        self._refresh_measurement()

    def set_applied(self, success: bool, message: str = "", core=None):
        if success and core is not None:
            self._adopt_core(core)
        self._applied = bool(success)
        self._saved = False
        self.save_button.setEnabled(self._applied)
        self._set_status(
            message or ("定标已应用，可保存完整数据体。" if success else "应用失败，请检查选点与实际间距。"),
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
