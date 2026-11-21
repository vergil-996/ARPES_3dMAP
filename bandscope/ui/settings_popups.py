"""去噪设置详情页。参数范围、选项及约束由现有处理控件提供。"""
from PyQt5.QtCore import QSignalBlocker
from PyQt5.QtWidgets import QDoubleSpinBox, QFormLayout, QGroupBox, QSpinBox, QVBoxLayout, QWidget

from bandscope.ui.ui_controls import ChevronComboBox


class DenoiseSettingsPanel(QWidget):
    def __init__(self, controls, parent=None):
        super().__init__(parent)
        self._spins = []
        self._combos = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        savgol = QGroupBox(controls.SG_METHOD_NAME)
        layout.addWidget(savgol)
        form = QFormLayout(savgol)
        self.sg_window_box = self._spin(form, "滑动窗口长度", controls.sg_window_box,
                                        QSpinBox(), controls._sync_savgol_constraints)
        self.sg_polyorder_box = self._spin(form, "多项式阶数", controls.sg_polyorder_box,
                                           QSpinBox(), controls._sync_savgol_constraints)
        self.sg_axis_combo = self._combo(form, "窗口滑动轴", controls.sg_axis_combo)

        wavelet = QGroupBox(controls.WAVELET_METHOD_NAME)
        layout.addWidget(wavelet)
        form = QFormLayout(wavelet)
        self.wavelet_combo = self._combo(form, "小波基", controls.wavelet_combo)
        self.wavelet_level_box = self._spin(form, "分解层数", controls.wavelet_level_box,
                                            QSpinBox(), controls._sync_wavelet_constraints)
        self.wavelet_threshold_rule_combo = self._combo(form, "阈值规则", controls.wavelet_threshold_rule_combo)
        self.wavelet_threshold_mode_combo = self._combo(form, "阈值模式", controls.wavelet_threshold_mode_combo)
        strength = QDoubleSpinBox()
        strength.setDecimals(10)
        self.wavelet_strength_box = self._spin(form, "阈值强度", controls.wavelet_strength_box,
                                               strength, controls._sync_wavelet_constraints)
        layout.addStretch()
        controls.state_restored.connect(self.sync)
        self.sync()

    def _spin(self, form, label, source, editor, validate):
        editor.setSingleStep(source.singleStep())
        self._spins.append((editor, source))
        form.addRow(label, editor)

        def commit():
            source.setValue(editor.value())
            validate()
            self.sync()

        editor.editingFinished.connect(commit)
        source.editingFinished.connect(self.sync)
        return editor

    def _combo(self, form, label, source):
        editor = ChevronComboBox()
        editor.addItems([source.itemText(index) for index in range(source.count())])
        self._combos.append((editor, source))
        form.addRow(label, editor)
        editor.currentIndexChanged.connect(source.setCurrentIndex)
        source.currentIndexChanged.connect(self.sync)
        return editor

    def sync(self):
        for editor, source in self._spins:
            with QSignalBlocker(editor):
                editor.setRange(source.minimum(), source.maximum())
                editor.setValue(source.value())
        for editor, source in self._combos:
            with QSignalBlocker(editor):
                editor.setCurrentIndex(source.currentIndex())
