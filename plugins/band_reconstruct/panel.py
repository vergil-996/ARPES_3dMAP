# -*- coding: utf-8 -*-
"""能带重构的参数面板：带数、逐带初始化面与对齐参数、重构超参数。

面板只做三件事：读用户设置、调宿主接口、显示状态。它在三维视图的「处理分析」
页显示（宿主按能力控制显隐），拿到的宿主句柄绑定本插件 id，队列与限制按插件计。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bandscope.extensions.api import (
    AnalysisTaskHandle,
    AnalysisUnavailable,
    PluginHostV3,
)
from bandscope.extensions.ui import theme

from .worker import band_defaults, run_reconstruction

#: 初始化面类型：``(值, 显示名, 形状参数标签)``。
INIT_KINDS = (
    ("parabolic", "抛物面", "抬升"),
    ("gaussian", "高斯面", "幅度"),
    ("plane", "平面", "斜率"),
)

#: 带数上限（与面板可维护性一致；契约上限见 ``api.MAX_SURFACE_BANDS``）。
MAX_PANEL_BANDS = 8


def _double_box(value: float, *, minimum=-1e6, maximum=1e6, step=0.005, decimals=4):
    box = QDoubleSpinBox()
    box.setRange(minimum, maximum)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setValue(float(value))
    box.setFixedHeight(26)
    box.setStyleSheet(theme.value_input_qss())
    return box


class _BandRow(QWidget):
    """一条带的设置：初值类型 + 形状参数 + E₀ + 动量缩放 + 能量平移。"""

    def __init__(self, index: int, defaults: Mapping[str, Any], parent=None):
        super().__init__(parent)
        self.index = int(index)
        self._defaults = dict(defaults)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(4)

        self.header = QLabel(f"Band {self.index + 1}")
        self.header.setStyleSheet(theme.field_label_qss())
        layout.addWidget(self.header, 0, 0)

        self.combo_init = QComboBox(self)
        for value, text, _shape in INIT_KINDS:
            self.combo_init.addItem(text, value)
        self.combo_init.setFixedHeight(26)
        self.combo_init.setStyleSheet(
            f"QComboBox {{ color: {theme.TEXT_1}; background-color: {theme.BG_3};"
            f" border: 1px solid {theme.BORDER_HEX}; border-radius: 4px; padding: 2px 8px; }}"
        )
        layout.addWidget(self.combo_init, 0, 1)

        self.label_shape = QLabel()
        self.label_shape.setStyleSheet(theme.field_label_qss())
        layout.addWidget(self.label_shape, 0, 2)

        self.box_shape = _double_box(defaults.get("curvature", 0.2))
        layout.addWidget(self.box_shape, 0, 3)

        layout.addWidget(self._tag("E₀ (E单位)"), 1, 0)
        self.box_e0 = _double_box(defaults.get("e0", 0.0), step=0.01)
        layout.addWidget(self.box_e0, 1, 1)

        layout.addWidget(self._tag("动量缩放"), 1, 2)
        self.box_scale = _double_box(1.0, minimum=0.05, maximum=20.0, step=0.05)
        layout.addWidget(self.box_scale, 1, 3)

        layout.addWidget(self._tag("能量平移"), 2, 0)
        self.box_shift = _double_box(0.0, step=0.005)
        layout.addWidget(self.box_shift, 2, 1)

        self.combo_init.currentIndexChanged.connect(self._sync_shape_label)
        self._sync_shape_label()
        self.set_values(defaults)

    @staticmethod
    def _tag(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(theme.field_label_qss())
        return label

    def _sync_shape_label(self) -> None:
        kind = self.init_kind()
        text = {value: shape for value, _name, shape in INIT_KINDS}.get(kind, "形状参数")
        self.label_shape.setText(text)
        self.box_shape.setToolTip(
            "抛物面：中心到动量网格角落的能量抬升；"
            "高斯面：峰的幅度（宽度固定为动量跨度的 35%）；"
            "平面：沿 x 的斜率（y 方向为 0）。"
        )

    # ------------------------------------------------------------------ 读写
    def init_kind(self) -> str:
        return str(self.combo_init.currentData() or "parabolic")

    def set_values(self, values: Mapping[str, Any]) -> None:
        data = dict(values)
        kind = str(data.get("init") or "parabolic").lower()
        position = self.combo_init.findData(kind)
        if position >= 0:
            self.combo_init.setCurrentIndex(position)
        shape_key = "amplitude" if kind == "gaussian" else "slope_x" if kind == "plane" else "curvature"
        fallback = {
            "amplitude": self._defaults.get("amplitude", 0.0),
            "slope_x": 0.0,
            "curvature": self._defaults.get("curvature", 0.0),
        }[shape_key]
        self.box_shape.setValue(float(data.get(shape_key, fallback) or fallback))
        self.box_e0.setValue(float(data.get("e0", self._defaults.get("e0", 0.0)) or 0.0))
        self.box_scale.setValue(float(data.get("momentum_scale", 1.0) or 1.0))
        self.box_shift.setValue(float(data.get("energy_shift", 0.0) or 0.0))

    def settings(self) -> Dict[str, Any]:
        """当前设置（完整参数集，未在界面上暴露的用默认值补齐）。"""
        kind = self.init_kind()
        shape = float(self.box_shape.value())
        values = dict(self._defaults)
        values.update(
            {
                "label": f"Band {self.index + 1}",
                "init": kind,
                "e0": float(self.box_e0.value()),
                "momentum_scale": float(self.box_scale.value()),
                "energy_shift": float(self.box_shift.value()),
                "color": self._defaults.get("color", ""),
            }
        )
        if kind == "gaussian":
            values["amplitude"] = shape
        elif kind == "plane":
            values["slope_x"] = shape
            values["slope_y"] = 0.0
        else:
            values["curvature"] = shape
        return values


class BandReconstructPanel(QWidget):
    """能带重构参数与运行状态。"""

    def __init__(self, host: PluginHostV3, parent=None):
        super().__init__(parent)
        self.host = host
        self._handle: Optional[AnalysisTaskHandle] = None
        self._band_rows: List[_BandRow] = []
        self._defaults_source = None
        self._build_ui()

    # ------------------------------------------------------------------ 构建
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(QLabel("带数"))
        self.spin_bands = QSpinBox(self)
        self.spin_bands.setRange(1, MAX_PANEL_BANDS)
        self.spin_bands.setValue(1)
        self.spin_bands.setFixedHeight(26)
        self.spin_bands.setFixedWidth(70)
        self.spin_bands.setStyleSheet(theme.value_input_qss())
        self.spin_bands.valueChanged.connect(self._rebuild_band_rows)
        head.addWidget(self.spin_bands)

        head.addSpacing(12)
        head.addWidget(QLabel("η"))
        self.box_eta = _double_box(0.1, minimum=1e-6, maximum=1e6, step=0.01)
        self.box_eta.setFixedWidth(90)
        self.box_eta.setToolTip("平滑先验强度，单位与能量轴相同（eV 量级的窗口用 0.1）")
        head.addWidget(self.box_eta)

        head.addWidget(QLabel("最大迭代"))
        self.spin_maxiter = QSpinBox(self)
        self.spin_maxiter.setRange(10, 5000)
        self.spin_maxiter.setValue(200)
        self.spin_maxiter.setFixedHeight(26)
        self.spin_maxiter.setFixedWidth(90)
        self.spin_maxiter.setStyleSheet(theme.value_input_qss())
        head.addWidget(self.spin_maxiter)
        head.addStretch(1)
        layout.addLayout(head)

        self.check_smooth = QCheckBox("高斯平滑", self)
        self.check_smooth.setChecked(True)
        self.check_clahe = QCheckBox("MCLAHE 对比度增强", self)
        self.check_clahe.setChecked(True)
        toggles = QHBoxLayout()
        toggles.setSpacing(16)
        toggles.addWidget(self.check_smooth)
        toggles.addWidget(self.check_clahe)
        toggles.addStretch(1)
        layout.addLayout(toggles)

        self._bands_host = QWidget(self)
        self._bands_layout = QVBoxLayout(self._bands_host)
        self._bands_layout.setContentsMargins(0, 0, 0, 0)
        self._bands_layout.setSpacing(10)
        layout.addWidget(self._bands_host)
        self._rebuild_band_rows(1)

        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        self.btn_check = _button(self, "检查数据", "secondary", self.on_check_data)
        self.btn_run = _button(self, "重构能带", "primary", self.on_run)
        self.btn_cancel = _button(self, "取消", "secondary", self.on_cancel)
        self.btn_cancel.setEnabled(False)
        run_row.addWidget(self.btn_check)
        run_row.addWidget(self.btn_run)
        run_row.addWidget(self.btn_cancel)
        run_row.addStretch(1)
        layout.addLayout(run_row)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        layout.addWidget(self.progress)

        self.status = QLabel(
            "在三维视图上点「检查数据」看当前快照，再点「重构能带」。"
            "结果作为新页面出现在左侧结果树里（一条带一页，页内可切换）。",
            self,
        )
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {theme.TEXT_3}; font-size: 11px;")
        layout.addWidget(self.status)

    def _rebuild_band_rows(self, count: int):
        while self._bands_layout.count():
            item = self._bands_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._band_rows = []
        for index in range(int(count)):
            defaults = band_defaults(index, self._energy_axis())
            row = _BandRow(index, defaults, self._bands_host)
            self._bands_layout.addWidget(row)
            self._band_rows.append(row)

    def _energy_axis(self):
        """能量轴估计：用最近一次快照的 E 轴；没有时用 [0, 1]。"""
        axis = getattr(self, "_energy_values", None)
        if axis is None:
            return [0.0, 1.0]
        return axis

    # ------------------------------------------------------------------ 参数
    def band_settings(self) -> List[Dict[str, Any]]:
        return [row.settings() for row in self._band_rows]

    def job_params(self, *, title: str = "") -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "bands": self.band_settings(),
            "eta": float(self.box_eta.value()),
            "maxiter": int(self.spin_maxiter.value()),
            "smooth": bool(self.check_smooth.isChecked()),
            "clahe": bool(self.check_clahe.isChecked()),
        }
        if title:
            params["title"] = title
        return params

    def settings_snapshot(self) -> Dict[str, Any]:
        return {
            "bands": self.band_settings(),
            "eta": float(self.box_eta.value()),
            "maxiter": int(self.spin_maxiter.value()),
            "smooth": bool(self.check_smooth.isChecked()),
            "clahe": bool(self.check_clahe.isChecked()),
        }

    def apply_settings(self, state: Mapping[str, Any]) -> None:
        """恢复参数；结构不完整时保留当前值（缺什么用什么）。"""
        data = dict(state or {})
        bands = [item for item in (data.get("bands") or ()) if isinstance(item, Mapping)]
        if bands:
            self.spin_bands.setValue(max(1, min(len(bands), MAX_PANEL_BANDS)))
            for row, item in zip(self._band_rows, bands):
                row.set_values(item)
        if "eta" in data:
            self.box_eta.setValue(float(data["eta"]))
        if "maxiter" in data:
            self.spin_maxiter.setValue(int(data["maxiter"]))
        if "smooth" in data:
            self.check_smooth.setChecked(bool(data["smooth"]))
        if "clahe" in data:
            self.check_clahe.setChecked(bool(data["clahe"]))

    # ------------------------------------------------------------------ 操作
    def _capture(self):
        """抓三维快照；不可用时把原因写到状态行并返回 None。"""
        try:
            snapshot = self.host.capture_analysis_input_3d()
        except AnalysisUnavailable as exc:
            self.status.setText(str(exc))
            return None
        self._energy_values = snapshot.e
        return snapshot

    def _describe_snapshot(self, snapshot) -> str:
        megabytes = snapshot.nbytes / (1024 * 1024)
        parts = [
            "快照 " + "×".join(str(size) for size in snapshot.shape),
            f"≈ {megabytes:.1f} MB",
        ]
        if getattr(snapshot, "scope_label", ""):
            parts.append(str(snapshot.scope_label))
        if getattr(snapshot, "frame_label", ""):
            parts.append(str(snapshot.frame_label))
        return " · ".join(parts)

    def on_check_data(self):
        snapshot = self._capture()
        if snapshot is None:
            return
        self.status.setText(f"{self._describe_snapshot(snapshot)}；可直接重构。")
        self._refresh_default_bands(snapshot)

    def _refresh_default_bands(self, snapshot) -> None:
        """用真实能量轴刷新逐带默认值（只在用户没改过初值时）。"""
        energy = snapshot.e
        for index, row in enumerate(self._band_rows):
            defaults = band_defaults(index, energy)
            row._defaults = defaults
            if row.box_e0.value() == 0.0:
                row.box_e0.setValue(defaults["e0"])

    def on_run(self):
        snapshot = self._capture()
        if snapshot is None:
            return
        self._refresh_default_bands(snapshot)
        title = f"{snapshot.page_title} 能带重构"
        handle = self.host.submit_analysis(
            snapshot,
            run_reconstruction,
            title=title,
            params=self.job_params(title=title),
        )
        if handle is None:
            self.status.setText("上一个任务还没结束或队列已满，稍后再试。")
            return
        self._handle = handle
        self._set_running(True)
        self.status.setText(f"已提交：{self._describe_snapshot(snapshot)}，逐带重构中…")

    def on_cancel(self):
        if self._handle is None:
            return
        self.host.cancel_analysis(self._handle)
        self.status.setText("已请求取消；任务会在下一个检查点退出。")

    def _set_running(self, running: bool):
        running = bool(running)
        self.btn_run.setEnabled(not running)
        self.btn_check.setEnabled(not running)
        self.btn_cancel.setEnabled(running)
        if not running:
            self.progress.setValue(0)

    # ------------------------------------------------------------------ 宿主回调
    def on_progress(self, fraction: float, message: str = ""):
        self.progress.setValue(int(max(0.0, min(1.0, float(fraction))) * 1000))
        if message:
            self.status.setText(str(message))

    def on_task_finished(self, message: str):
        self._handle = None
        self._set_running(False)
        self.status.setText(message)


def _button(parent, text: str, kind: str, slot) -> QPushButton:
    button = QPushButton(text, parent)
    button.setCursor(Qt.PointingHandCursor)
    button.setMinimumHeight(28)
    theme.style_push_button(button, kind)
    button.clicked.connect(slot)
    return button


__all__ = ["BandReconstructPanel", "INIT_KINDS", "MAX_PANEL_BANDS"]
