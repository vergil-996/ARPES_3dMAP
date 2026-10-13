# -*- coding: utf-8 -*-
"""「插件设置」页里的 EDC 瀑布图设置面板：自动 / 手动 k 步长。

面板只负责编辑一份可序列化的设置状态（``{"k_step_mode", "k_step"}``），由插件
在 ``export_settings`` / ``restore_settings`` 里同步；「保存为新建默认值」与
「应用到当前瀑布图」两个按钮由插件管理窗口提供，属宿主操作，不在本面板里。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from PyQt5.QtWidgets import QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from bandscope.extensions.ui import theme

from .edc import MAX_STEP, MIN_STEP, MODE_AUTO, MODE_CHOICES, MODE_MANUAL, step_mode_label


class EdcSettingsPanel(QWidget):
    """自动 / 手动步长编辑；改动即时写回插件持有的设置状态。"""

    def __init__(self, state: Optional[Mapping[str, Any]] = None, on_changed: Optional[Callable[[Dict[str, Any]], None]] = None, parent=None):
        super().__init__(parent)
        self._on_changed = on_changed
        self._state: Dict[str, Any] = {
            "k_step_mode": MODE_AUTO,
            "k_step": 0.01,
        }
        self._build_ui()
        self.restore(state or {})

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)

        self.combo_mode = QComboBox(self)
        self.combo_mode.setFixedHeight(28)
        for mode in MODE_CHOICES:
            self.combo_mode.addItem(step_mode_label(mode), mode)
        self.combo_mode.setStyleSheet(
            f"QComboBox {{ color: {theme.TEXT_1}; background-color: {theme.BG_3};"
            f" border: 1px solid {theme.BORDER_HEX}; border-radius: 4px; padding: 2px 8px; }}"
        )
        self.combo_mode.currentIndexChanged.connect(self._on_mode_changed)
        row.addWidget(self.combo_mode, 1)

        self.box_step = QDoubleSpinBox(self)
        self.box_step.setFixedHeight(28)
        self.box_step.setDecimals(4)
        self.box_step.setRange(float(MIN_STEP), float(MAX_STEP))
        self.box_step.setSingleStep(0.01)
        self.box_step.valueChanged.connect(self._on_step_changed)
        row.addWidget(self.box_step, 1)
        layout.addLayout(row)

        self.hint = QLabel(
            "自动：按完整未裁剪 k 轴的中位相邻间距（下限 0.0001，回退 0.01）。"
            "手动：0.0001–10。设置只影响之后新建的瀑布图；"
            "「应用当前瀑布图」才会更新已经打开的页面。",
            self,
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.TEXT_3}; font-size: 11px;")
        layout.addWidget(self.hint)
        self._sync_enabled()

    # ------------------------------------------------------------------ 状态
    def export_state(self) -> Dict[str, Any]:
        return {
            "k_step_mode": str(self.combo_mode.currentData() or MODE_AUTO),
            "k_step": float(self.box_step.value()),
        }

    def restore(self, state: Mapping[str, Any]) -> None:
        state = dict(state or {})
        mode = str(state.get("k_step_mode") or MODE_AUTO)
        if mode not in MODE_CHOICES:
            mode = MODE_AUTO
        try:
            step = float(state.get("k_step", self.box_step.value()))
        except (TypeError, ValueError):
            step = self.box_step.value()
        step = min(max(step, float(MIN_STEP)), float(MAX_STEP))
        self.combo_mode.blockSignals(True)
        self.box_step.blockSignals(True)
        try:
            index = self.combo_mode.findData(mode)
            self.combo_mode.setCurrentIndex(index if index >= 0 else 0)
            self.box_step.setValue(step)
        finally:
            self.combo_mode.blockSignals(False)
            self.box_step.blockSignals(False)
        self._state = self.export_state()
        self._sync_enabled()

    # ------------------------------------------------------------------ 事件
    def _sync_enabled(self):
        manual = str(self.combo_mode.currentData() or MODE_AUTO) == MODE_MANUAL
        self.box_step.setEnabled(manual)

    def _on_mode_changed(self, _index):
        self._sync_enabled()
        self._emit()

    def _on_step_changed(self, _value):
        self._emit()

    def _emit(self):
        self._state = self.export_state()
        callback = self._on_changed
        if callback is not None:
            callback(dict(self._state))


__all__ = ["EdcSettingsPanel"]
