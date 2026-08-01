from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QVBoxLayout, QWidget

from control_layout_utils import combo_index_for_text
from denoise_control_utils import (
    create_double_spin_box,
    create_parameter_group,
    create_savgol_control_group,
    create_wavelet_control_group,
    finish_parameter_group,
)


class _NonModalPopup(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def show_at(self, global_pos):
        self.adjustSize()
        self.move(global_pos)
        self.show()
        self.raise_()


class DenoiseSettingsPopup(_NonModalPopup):
    POPUP_WIDTH = 500

    def __init__(self, page_control_blank, parent=None):
        super().__init__(parent)
        self._blank = page_control_blank
        self.setWindowTitle("去噪参数设置")
        self.setMinimumWidth(self.POPUP_WIDTH)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.group_savgol = self._create_savgol_group()
        self.group_wavelet = self._create_wavelet_group()
        root.addWidget(self.group_savgol)
        root.addWidget(self.group_wavelet)
        root.addStretch()

    def _create_savgol_group(self):
        controls = create_savgol_control_group(
            self,
            self.POPUP_WIDTH - 60,
            self.POPUP_WIDTH - 100,
        )
        self.sg_window_box = controls.window_box
        self.sg_polyorder_box = controls.polyorder_box
        self.sg_axis_combo = controls.axis_combo

        self.sg_window_box.editingFinished.connect(self._on_sg_window_changed)
        self.sg_polyorder_box.editingFinished.connect(self._on_sg_polyorder_changed)
        self.sg_axis_combo.currentIndexChanged.connect(self._on_sg_axis_changed)

        return controls.group

    def _create_wavelet_group(self):
        controls = create_wavelet_control_group(
            self,
            self.POPUP_WIDTH - 60,
            self.POPUP_WIDTH - 100,
        )
        self.wavelet_combo = controls.wavelet_combo
        self.wavelet_level_box = controls.level_box
        self.wavelet_threshold_rule_combo = controls.threshold_rule_combo
        self.wavelet_threshold_mode_combo = controls.threshold_mode_combo
        self.wavelet_strength_box = controls.strength_box

        self.wavelet_combo.currentIndexChanged.connect(self._on_wavelet_combo_changed)
        self.wavelet_level_box.editingFinished.connect(self._on_wavelet_level_changed)
        self.wavelet_threshold_rule_combo.currentIndexChanged.connect(self._on_wavelet_rule_changed)
        self.wavelet_threshold_mode_combo.currentIndexChanged.connect(self._on_wavelet_mode_changed)
        self.wavelet_strength_box.editingFinished.connect(self._on_wavelet_strength_changed)

        return controls.group

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_from_blank()

    def _sync_from_blank(self):
        blank = self._blank
        self.sg_window_box.setValue(blank.sg_window_box.value())
        self.sg_polyorder_box.setValue(blank.sg_polyorder_box.value())
        sg_axis_text = blank.sg_axis_combo.currentText()
        sg_axis_idx = combo_index_for_text(self.sg_axis_combo, sg_axis_text)
        if sg_axis_idx >= 0:
            self.sg_axis_combo.setCurrentIndex(sg_axis_idx)

        self.wavelet_combo.setCurrentIndex(blank.wavelet_combo.currentIndex())
        self.wavelet_level_box.setValue(blank.wavelet_level_box.value())
        self.wavelet_threshold_rule_combo.setCurrentIndex(blank.wavelet_threshold_rule_combo.currentIndex())
        self.wavelet_threshold_mode_combo.setCurrentIndex(blank.wavelet_threshold_mode_combo.currentIndex())
        self.wavelet_strength_box.setValue(blank.wavelet_strength_box.value())

    def _on_sg_window_changed(self):
        value = int(self.sg_window_box.value())
        self._blank.sg_window_box.setValue(value)
        self._blank._sync_savgol_constraints()
        self._sync_from_blank()

    def _on_sg_polyorder_changed(self):
        value = int(self.sg_polyorder_box.value())
        self._blank.sg_polyorder_box.setValue(value)
        self._blank._sync_savgol_constraints()
        self._sync_from_blank()

    def _on_sg_axis_changed(self, _idx):
        sg_axis_text = self.sg_axis_combo.currentText()
        idx = combo_index_for_text(self._blank.sg_axis_combo, sg_axis_text)
        if idx >= 0:
            self._blank.sg_axis_combo.setCurrentIndex(idx)

    def _on_wavelet_combo_changed(self, _idx):
        self._blank.wavelet_combo.setCurrentIndex(self.wavelet_combo.currentIndex())

    def _on_wavelet_level_changed(self):
        value = int(self.wavelet_level_box.value())
        self._blank.wavelet_level_box.setValue(value)
        self._blank._sync_wavelet_constraints()
        self._sync_from_blank()

    def _on_wavelet_rule_changed(self, _idx):
        self._blank.wavelet_threshold_rule_combo.setCurrentIndex(self.wavelet_threshold_rule_combo.currentIndex())

    def _on_wavelet_mode_changed(self, _idx):
        self._blank.wavelet_threshold_mode_combo.setCurrentIndex(self.wavelet_threshold_mode_combo.currentIndex())

    def _on_wavelet_strength_changed(self):
        value = float(self.wavelet_strength_box.value())
        self._blank.wavelet_strength_box.setValue(value)
        self._blank._sync_wavelet_constraints()
        self._sync_from_blank()


class WaterfallSettingsPopup(_NonModalPopup):
    POPUP_WIDTH = 400

    def __init__(self, page_control_blank, parent=None):
        super().__init__(parent)
        self._blank = page_control_blank
        self.setWindowTitle("瀑布图步长设置")
        self.setMinimumWidth(self.POPUP_WIDTH)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        group, card = create_parameter_group(self, "瀑布图参数", self.POPUP_WIDTH - 60)

        self.waterfall_step_box = create_double_spin_box(
            parent=card,
            width=self.POPUP_WIDTH - 100,
            title="k步长 (1/Å)",
            value=0.01,
            minimum=0.0001,
            maximum=10.0,
            single_step=0.01,
        )
        self.waterfall_step_box.editingFinished.connect(self._on_step_changed)

        finish_parameter_group(group, card, (self.waterfall_step_box,))
        root.addWidget(group)
        root.addStretch()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_from_blank()

    def _sync_from_blank(self):
        blank = self._blank
        self.waterfall_step_box.setValue(blank.waterfall_step_box.value())

    def _on_step_changed(self):
        value = float(self.waterfall_step_box.value())
        self._blank.waterfall_step_box.setValue(value)
        self._blank._sync_waterfall_constraints()
        self._blank._waterfall_step_custom = True
        self._blank.waterfall_step_box.editingFinished.emit()
