from __future__ import annotations

import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from bandscope.core.axis_assignment import (
    ANALYSIS_ROLES,
    ROLE_LABELS,
    SCAN_KINDS,
    SCAN_LABELS,
    SCAN_UNKNOWN,
    default_scan_name,
    validate_assignment,
)
from bandscope.core.axis_interval import (
    AUTO_DOCKING,
    CONTINUOUS_DOCKING,
    DISCRETE_DOCKING,
    PAN_FRAMES,
    PAN_PHYSICAL,
    FourthAxisDragSettings,
)
from bandscope.ui import theme


_SINGLETON = -2
_STATIC = -1
_UNSET = object()


class AxisAssignmentDialog(QDialog):
    calibration_requested = pyqtSignal()
    CALIBRATION_APPLIED = QDialog.Accepted + 1

    def __init__(
        self,
        assignment,
        automatic,
        candidates,
        *,
        candidate_units=None,
        fourth_axis_drag=None,
        as_new_source=False,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("重新分配轴")
        self.setModal(True)
        self.resize(760, 550)
        self._current = assignment.clone()
        self._automatic = automatic.clone()
        self._candidates = dict(candidates)
        self._candidate_units = candidate_units or {}
        self._initial_fourth_axis_drag = fourth_axis_drag or FourthAxisDragSettings()
        self._as_new_source = bool(as_new_source)
        self._loading = False
        self._assignment_dirty = False
        self._rows = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        shape = " × ".join(str(int(value)) for value in assignment.original_shape)
        self.shape_label = QLabel(f"原始数组：{assignment.array_key} · {shape}", self)
        self.shape_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.shape_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(9)
        for column, text in enumerate(("分析槽位", "原始维度", "文件坐标或索引", "坐标首末值")):
            label = QLabel(text, self)
            label.setStyleSheet(f"color: {theme.TEXT_2}; font-weight: 600;")
            grid.addWidget(label, 0, column)
        for row, role in enumerate(("X", "Y", "E", "T"), start=1):
            slot = QLabel(ROLE_LABELS[role], self)
            dimension = QComboBox(self)
            dimension.setAccessibleName(f"{ROLE_LABELS[role]} 原始维度")
            coordinate = QComboBox(self)
            coordinate.setAccessibleName(f"{ROLE_LABELS[role]} 坐标来源")
            preview = QLabel("–", self)
            preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(slot, row, 0)
            grid.addWidget(dimension, row, 1)
            grid.addWidget(coordinate, row, 2)
            grid.addWidget(preview, row, 3)
            self._rows[role] = {
                "dimension": dimension,
                "coordinate": coordinate,
                "preview": preview,
            }
            dimension.currentIndexChanged.connect(
                lambda _index, selected_role=role: self._on_dimension_changed(selected_role)
            )
            coordinate.currentIndexChanged.connect(
                lambda _index, selected_role=role: self._on_coordinate_changed(selected_role)
            )
        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(3, 1)
        root.addLayout(grid)

        scan_form = QFormLayout()
        scan_form.setHorizontalSpacing(12)
        scan_form.setVerticalSpacing(8)
        self.scan_kind = QComboBox(self)
        self.scan_kind.setAccessibleName("扫描轴类型")
        for kind in SCAN_KINDS:
            self.scan_kind.addItem(SCAN_LABELS[kind], kind)
        self.scan_name = QLineEdit(self)
        self.scan_name.setAccessibleName("扫描轴名称")
        self.scan_unit = QLineEdit(self)
        self.scan_unit.setAccessibleName("扫描轴单位")
        self.scan_unit.setPlaceholderText("无单位时留空；时间缺省按 fs 显示")
        scan_form.addRow("扫描类型", self.scan_kind)
        scan_form.addRow("显示名称", self.scan_name)
        scan_form.addRow("单位", self.scan_unit)

        self.docking_combo = QComboBox(self)
        self.docking_combo.setAccessibleName("滑块停靠")
        self.docking_combo.setToolTip("仅影响扫描轴滑块，不改变 Kx、Ky、E 轴或当前选区")
        self.docking_combo.addItem("", AUTO_DOCKING)
        self.docking_combo.addItem("离散停靠", DISCRETE_DOCKING)
        self.docking_combo.addItem("连续拖动", CONTINUOUS_DOCKING)
        self.pan_combo = QComboBox(self)
        self.pan_combo.setAccessibleName("连续拖动时整段平移")
        self.pan_combo.setToolTip("仅影响连续拖动扫描轴选区时的整段平移方式")
        self.pan_combo.addItem("保持物理宽度", PAN_PHYSICAL)
        self.pan_combo.addItem("保持帧数", PAN_FRAMES)
        docking_index = self.docking_combo.findData(self._initial_fourth_axis_drag.docking)
        pan_index = self.pan_combo.findData(self._initial_fourth_axis_drag.continuous_pan)
        self.docking_combo.setCurrentIndex(max(0, docking_index))
        self.pan_combo.setCurrentIndex(max(0, pan_index))
        scan_form.addRow("滑块停靠", self.docking_combo)
        scan_form.addRow("连续拖动时整段平移", self.pan_combo)
        root.addLayout(scan_form)
        self.scan_kind.currentIndexChanged.connect(self._on_scan_kind_changed)
        self.scan_name.textChanged.connect(self._on_scan_metadata_changed)
        self.scan_unit.textChanged.connect(self._on_scan_metadata_changed)
        self.drag_scope_label = QLabel("滑块停靠与整段平移仅作用于扫描轴，不改变当前选区。", self)
        self.drag_scope_label.setStyleSheet(f"color: {theme.TEXT_2};")
        root.addWidget(self.drag_scope_label)

        self.warning_label = QLabel(self)
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(f"color: {theme.TEXT_2};")
        root.addWidget(self.warning_label)
        root.addStretch(1)

        buttons_row = QHBoxLayout()
        self.restore_button = QPushButton("恢复自动分配", self)
        self.restore_button.clicked.connect(self.restore_automatic)
        buttons_row.addWidget(self.restore_button)
        self.calibration_button = QPushButton("Kx / Ky 定标", self)
        self.calibration_button.setAccessibleName("Kx / Ky 两点距离定标")
        self.calibration_button.setToolTip(
            "在 XY 切片上选择两点并输入实际间距；统一缩放 Kx / Ky，不旋转或平移数据。"
        )
        self.calibration_button.clicked.connect(self.calibration_requested.emit)
        buttons_row.addWidget(self.calibration_button)
        buttons_row.addStretch(1)
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        self.button_box.button(QDialogButtonBox.Ok).setText(
            "作为新数据源打开" if as_new_source else "应用"
        )
        self.button_box.button(QDialogButtonBox.Ok).setAccessibleName(
            "作为新数据源打开" if as_new_source else "应用轴分配"
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        buttons_row.addWidget(self.button_box)
        root.addLayout(buttons_row)

        self.setStyleSheet(
            f"QDialog {{ background: {theme.BG_2}; color: {theme.TEXT_1}; }}"
            f"QComboBox, QLineEdit {{ background: {theme.BG_1}; color: {theme.TEXT_1};"
            f"border: 1px solid {theme.BORDER}; border-radius: 5px; padding: 5px 7px; }}"
            f"QPushButton {{ background: {theme.BG_3}; color: {theme.TEXT_1};"
            f"border: 1px solid {theme.BORDER}; border-radius: 5px; padding: 6px 12px; }}"
        )
        self._set_assignment(self._current)
        self.docking_combo.currentIndexChanged.connect(self._update_ok_button)
        self.pan_combo.currentIndexChanged.connect(self._update_ok_button)
        self._update_ok_button()

    def _set_assignment(self, assignment):
        self._loading = True
        try:
            for role in ("X", "Y", "E", "T"):
                row = self._rows[role]
                dimension = row["dimension"]
                dimension.blockSignals(True)
                dimension.clear()
                for axis, length in enumerate(assignment.original_shape):
                    dimension.addItem(f"维 {axis + 1}（长度 {length}）", axis)
                if role in ANALYSIS_ROLES:
                    dimension.addItem("单例（补齐）", _SINGLETON)
                else:
                    dimension.addItem("无扫描轴（静态）", _STATIC)
                selected = assignment.axis_roles.get(role, _SINGLETON if role in ANALYSIS_ROLES else _STATIC)
                index = dimension.findData(selected)
                dimension.setCurrentIndex(max(index, 0))
                dimension.blockSignals(False)
                self._update_coordinates(
                    role,
                    assignment.coord_keys.get(role),
                    original_shape=assignment.original_shape,
                )
            kind_index = self.scan_kind.findData(assignment.scan_kind)
            self.scan_kind.setCurrentIndex(max(kind_index, 0))
            self.scan_name.setText(assignment.display_scan_name)
            self.scan_unit.setText(str(assignment.scan_unit or ""))
            self.warning_label.setText("；".join(assignment.warnings))
        finally:
            self._loading = False
        self._assignment_dirty = False
        self._update_scan_enabled()
        self._update_ok_button()

    def _selected_dimension(self, role):
        return self._rows[role]["dimension"].currentData()

    def _update_coordinates(self, role, preferred=_UNSET, *, original_shape=None):
        row = self._rows[role]
        shape = original_shape or self._current.original_shape
        axis = self._selected_dimension(role)
        expected = 1 if axis in (_SINGLETON, _STATIC, None) else int(shape[axis])
        previous = row["coordinate"].currentData() if preferred is _UNSET else preferred
        row["coordinate"].blockSignals(True)
        row["coordinate"].clear()
        row["coordinate"].addItem("索引", "")
        selected_index = 0
        for key, values in self._candidates.items():
            array = np.asarray(values).reshape(-1)
            if array.size != expected or not np.isfinite(array).all():
                continue
            row["coordinate"].addItem(str(key), str(key))
            if key == previous:
                selected_index = row["coordinate"].count() - 1
        row["coordinate"].setCurrentIndex(selected_index)
        row["coordinate"].setEnabled(axis not in (_STATIC, None))
        row["coordinate"].blockSignals(False)
        self._update_preview(role)
        if role == "T":
            self._update_scan_enabled()

    def _update_preview(self, role):
        row = self._rows[role]
        key = row["coordinate"].currentData()
        axis = self._selected_dimension(role)
        if axis in (_STATIC, None):
            row["preview"].setText("–")
            return
        if key:
            values = np.asarray(self._candidates[key], dtype=np.float64).reshape(-1)
        else:
            length = 1 if axis == _SINGLETON else self._current.original_shape[axis]
            values = np.arange(length, dtype=np.float64)
        if values.size:
            row["preview"].setText(f"{values[0]:.6g} … {values[-1]:.6g}")
        else:
            row["preview"].setText("–")

    def _on_dimension_changed(self, role):
        if self._loading:
            return
        self._update_coordinates(role)
        self._mark_assignment_dirty()

    def _on_coordinate_changed(self, role):
        self._update_preview(role)
        self._mark_assignment_dirty()

    def _mark_assignment_dirty(self):
        if self._loading:
            return
        self._assignment_dirty = True
        self._update_ok_button()

    def _update_scan_enabled(self):
        enabled = self._selected_dimension("T") not in (_STATIC, None)
        for widget in (
            self.scan_kind,
            self.scan_name,
            self.scan_unit,
            self.docking_combo,
            self.pan_combo,
        ):
            widget.setEnabled(enabled)
        axis = self._selected_dimension("T")
        sample_count = int(self._current.original_shape[axis]) if enabled else 0
        automatic = FourthAxisDragSettings().resolved_docking(sample_count)
        automatic_text = "离散停靠" if automatic == DISCRETE_DOCKING else "连续拖动"
        current = self.fourth_axis_drag().resolved_docking(sample_count)
        current_text = "离散停靠" if current == DISCRETE_DOCKING else "连续拖动"
        self.docking_combo.setItemText(0, f"自动（当前：{automatic_text}）")
        self.docking_combo.setToolTip(
            f"仅影响扫描轴；{int(sample_count)} 个采样点，"
            f"自动为{automatic_text}，当前为{current_text}"
        )

    def fourth_axis_drag(self):
        return FourthAxisDragSettings(
            str(self.docking_combo.currentData()), str(self.pan_combo.currentData())
        )

    def assignment_changed(self):
        return self._assignment_dirty

    def fourth_axis_drag_changed(self):
        return self.fourth_axis_drag() != self._initial_fourth_axis_drag

    def _update_ok_button(self, *_args):
        button_box = getattr(self, "button_box", None)
        if button_box is None:
            return
        settings_only = self.fourth_axis_drag_changed() and not self._assignment_dirty
        create_new = self._as_new_source and not settings_only
        text = "作为新数据源打开" if create_new else "应用"
        button = button_box.button(QDialogButtonBox.Ok)
        button.setText(text)
        button.setAccessibleName("作为新数据源打开" if create_new else "应用轴分配")

    def _on_scan_kind_changed(self):
        if self._loading:
            return
        kind = self.scan_kind.currentData()
        key = self._rows["T"]["coordinate"].currentData() or None
        self.scan_name.setText(default_scan_name(kind, key))
        self._mark_assignment_dirty()

    def _on_scan_metadata_changed(self, *_args):
        self._mark_assignment_dirty()

    def restore_automatic(self):
        self._current = self._automatic.clone()
        self._set_assignment(self._automatic)
        self._mark_assignment_dirty()

    def assignment(self):
        updated = self._current.clone()
        updated.axis_roles = {}
        updated.coord_keys = {}
        updated.coord_units = {}
        updated.role_order = list(ANALYSIS_ROLES)
        for role in ANALYSIS_ROLES:
            axis = self._selected_dimension(role)
            if axis != _SINGLETON:
                updated.axis_roles[role] = int(axis)
            key = self._rows[role]["coordinate"].currentData() or None
            updated.coord_keys[role] = key
            if key == self._current.coord_keys.get(role):
                updated.coord_units[role] = self._current.coord_units.get(role)
            else:
                updated.coord_units[role] = (self._candidate_units.get(role) or {}).get(key)

        scan_axis = self._selected_dimension("T")
        if scan_axis not in (_STATIC, None):
            updated.role_order.append("T")
            updated.axis_roles["T"] = int(scan_axis)
            key = self._rows["T"]["coordinate"].currentData() or None
            updated.coord_keys["T"] = key
            updated.coord_units["T"] = self.scan_unit.text().strip() or None
            updated.scan_kind = self.scan_kind.currentData() or SCAN_UNKNOWN
            updated.scan_name = self.scan_name.text().strip() or default_scan_name(
                updated.scan_kind, key
            )
        else:
            updated.coord_keys["T"] = None
            updated.coord_units["T"] = None
            updated.scan_kind = SCAN_UNKNOWN
            updated.scan_name = "扫描轴"
        updated.manual = True
        updated.warnings = (
            ("第四轴类型待确认，按扫描帧号浏览",)
            if "T" in updated.role_order and updated.scan_kind == SCAN_UNKNOWN
            else ()
        )
        return validate_assignment(updated, self._candidates)

    def accept(self):
        try:
            self.assignment()
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "无法应用轴分配", str(exc))
            return
        super().accept()
