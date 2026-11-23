"""去噪设置详情页。参数范围、选项及约束由现有处理控件提供。"""
from PyQt5.QtCore import QSignalBlocker
from PyQt5.QtWidgets import QDoubleSpinBox, QFormLayout, QGroupBox, QSpinBox, QVBoxLayout, QWidget

from bandscope.processing.denoise_config import (
    GAUSSIAN_METHOD_NAME,
    LOW_RANK_METHOD_NAME,
    MEDIAN_METHOD_NAME,
    NLM_METHOD_NAME,
)
from bandscope.ui.ui_controls import ChevronComboBox


class DenoiseSettingsPanel(QWidget):
    def __init__(self, controls, parent=None):
        super().__init__(parent)
        self._spins = []
        self._combos = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.group_gaussian = QGroupBox(GAUSSIAN_METHOD_NAME)
        layout.addWidget(self.group_gaussian)
        form = QFormLayout(self.group_gaussian)
        self.gaussian_sigma_kx_box = self._spin(
            form, "Kx 高斯 σ（采样点，0=关闭）", controls.gaussian_sigma_kx_box,
            self._double_spin(), controls._sync_gaussian_constraints
        )
        self.gaussian_sigma_ky_box = self._spin(
            form, "Ky 高斯 σ（采样点，0=关闭）", controls.gaussian_sigma_ky_box,
            self._double_spin(), controls._sync_gaussian_constraints
        )
        self.gaussian_sigma_e_box = self._spin(
            form, "E 高斯 σ（采样点，0=关闭）", controls.gaussian_sigma_e_box,
            self._double_spin(), controls._sync_gaussian_constraints
        )

        self.group_median = QGroupBox(MEDIAN_METHOD_NAME)
        layout.addWidget(self.group_median)
        form = QFormLayout(self.group_median)
        self.median_size_kx_box = self._spin(
            form, "Kx 核尺寸（采样点，正奇数，1=关闭）", controls.median_size_kx_box,
            QSpinBox(), controls._sync_median_constraints
        )
        self.median_size_ky_box = self._spin(
            form, "Ky 核尺寸（采样点，正奇数，1=关闭）", controls.median_size_ky_box,
            QSpinBox(), controls._sync_median_constraints
        )
        self.median_size_e_box = self._spin(
            form, "E 核尺寸（采样点，正奇数，1=关闭）", controls.median_size_e_box,
            QSpinBox(), controls._sync_median_constraints
        )

        self.group_savgol = QGroupBox(controls.SG_METHOD_NAME)
        layout.addWidget(self.group_savgol)
        form = QFormLayout(self.group_savgol)
        self.sg_window_box = self._spin(form, "滑动窗口长度（采样点）", controls.sg_window_box,
                                        QSpinBox(), controls._sync_savgol_constraints)
        self.sg_polyorder_box = self._spin(form, "多项式阶数（无量纲）", controls.sg_polyorder_box,
                                           QSpinBox(), controls._sync_savgol_constraints)
        self.sg_axis_combo = self._combo(form, "窗口滑动轴", controls.sg_axis_combo)

        self.group_wavelet = QGroupBox(controls.WAVELET_METHOD_NAME)
        layout.addWidget(self.group_wavelet)
        form = QFormLayout(self.group_wavelet)
        self.wavelet_combo = self._combo(form, "小波基", controls.wavelet_combo)
        self.wavelet_level_box = self._spin(form, "分解层数（无量纲）", controls.wavelet_level_box,
                                            QSpinBox(), controls._sync_wavelet_constraints)
        self.wavelet_threshold_rule_combo = self._combo(form, "阈值规则", controls.wavelet_threshold_rule_combo)
        self.wavelet_threshold_mode_combo = self._combo(form, "阈值模式", controls.wavelet_threshold_mode_combo)
        self.wavelet_strength_box = self._spin(
            form, "阈值强度（无量纲）", controls.wavelet_strength_box,
            self._double_spin(decimals=10), controls._sync_wavelet_constraints
        )
        self.wavelet_noise_sigma_box = self._spin(
            form, "噪声标准差（数据强度单位，0=自动）", controls.wavelet_noise_sigma_box,
            self._double_spin(), controls._sync_wavelet_constraints
        )

        self.group_nlmeans = QGroupBox(NLM_METHOD_NAME)
        layout.addWidget(self.group_nlmeans)
        form = QFormLayout(self.group_nlmeans)
        self.nlmeans_patch_size_box = self._spin(
            form, "邻域尺寸（采样点，正奇数）", controls.nlmeans_patch_size_box,
            QSpinBox(), controls._sync_nlmeans_constraints
        )
        self.nlmeans_patch_distance_box = self._spin(
            form, "搜索半径（采样点）", controls.nlmeans_patch_distance_box,
            QSpinBox(), controls._sync_nlmeans_constraints
        )
        self.nlmeans_h_scale_box = self._spin(
            form, "滤波强度系数（无量纲）", controls.nlmeans_h_scale_box,
            self._double_spin(), controls._sync_nlmeans_constraints
        )
        self.nlmeans_noise_sigma_box = self._spin(
            form, "噪声标准差（数据强度单位，0=自动）", controls.nlmeans_noise_sigma_box,
            self._double_spin(), controls._sync_nlmeans_constraints
        )

        self.group_low_rank = QGroupBox(LOW_RANK_METHOD_NAME)
        layout.addWidget(self.group_low_rank)
        form = QFormLayout(self.group_low_rank)
        self.low_rank_rank_box = self._spin(
            form, "保留秩（无量纲）", controls.low_rank_rank_box,
            QSpinBox(), controls._sync_low_rank_constraints
        )

        layout.addStretch()
        controls.state_restored.connect(self.sync)
        self.sync()

    @staticmethod
    def _double_spin(*, decimals=6):
        editor = QDoubleSpinBox()
        editor.setDecimals(decimals)
        return editor

    def _spin(self, form, label, source, editor, validate=None):
        editor.setSingleStep(source.singleStep())
        self._spins.append((editor, source))
        form.addRow(label, editor)

        def commit():
            source.setValue(editor.value())
            if validate is not None:
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

    def sync(self, *_):
        for editor, source in self._spins:
            with QSignalBlocker(editor):
                editor.setRange(source.minimum(), source.maximum())
                editor.setValue(source.value())
        for editor, source in self._combos:
            with QSignalBlocker(editor):
                editor.setCurrentIndex(source.currentIndex())
