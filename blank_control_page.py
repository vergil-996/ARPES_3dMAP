from PyQt5.QtCore import QSignalBlocker, Qt
from PyQt5.QtWidgets import QVBoxLayout, QWidget
from siui.components.widgets import SiScrollArea

from control_layout_utils import combo_index_for_text
from denoise_config import (
    DEFAULT_SG_PARAMS as CONFIG_DEFAULT_SG_PARAMS,
    DEFAULT_WAVELET_PARAMS as CONFIG_DEFAULT_WAVELET_PARAMS,
    MAX_SG_POLYORDER as CONFIG_MAX_SG_POLYORDER,
    SG_AXIS_KEY_TO_LABEL as CONFIG_SG_AXIS_KEY_TO_LABEL,
    SG_AXIS_LABEL_TO_KEY as CONFIG_SG_AXIS_LABEL_TO_KEY,
    SG_METHOD_NAME as CONFIG_SG_METHOD_NAME,
    THRESHOLD_MODE_OPTIONS as CONFIG_THRESHOLD_MODE_OPTIONS,
    THRESHOLD_RULE_OPTIONS as CONFIG_THRESHOLD_RULE_OPTIONS,
    WAVELET_METHOD_NAME as CONFIG_WAVELET_METHOD_NAME,
    WAVELET_OPTIONS as CONFIG_WAVELET_OPTIONS,
)
from denoise_control_utils import (
    create_double_spin_box,
    create_parameter_group,
    create_savgol_control_group,
    create_wavelet_control_group,
    finish_parameter_group,
)


class BlankControlPage(QWidget):
    SG_METHOD_NAME = CONFIG_SG_METHOD_NAME
    GROUP_WIDTH = 560
    DEFAULT_WATERFALL_STEP = 0.01
    DEFAULT_SG_PARAMS = CONFIG_DEFAULT_SG_PARAMS
    SG_AXIS_LABEL_TO_KEY = CONFIG_SG_AXIS_LABEL_TO_KEY
    SG_AXIS_KEY_TO_LABEL = CONFIG_SG_AXIS_KEY_TO_LABEL
    MAX_SG_POLYORDER = CONFIG_MAX_SG_POLYORDER
    WAVELET_METHOD_NAME = CONFIG_WAVELET_METHOD_NAME
    DEFAULT_WAVELET_PARAMS = CONFIG_DEFAULT_WAVELET_PARAMS
    WAVELET_OPTIONS = CONFIG_WAVELET_OPTIONS
    THRESHOLD_RULE_OPTIONS = CONFIG_THRESHOLD_RULE_OPTIONS
    THRESHOLD_MODE_OPTIONS = CONFIG_THRESHOLD_MODE_OPTIONS

    DEFAULT_SECOND_DERIVATIVE_PARAMS = {
        "threshold": 0.0,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._waterfall_step_custom = False
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(0)

        self.scroll = SiScrollArea(self)
        self.container = QWidget(self)
        self.container.setFixedWidth(self.GROUP_WIDTH + 40)
        self.content_layout = QVBoxLayout(self.container)
        self.content_layout.setContentsMargins(20, 20, 20, 20)
        self.content_layout.setSpacing(18)
        self.content_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.group_savgol = self._create_method_group()
        self.group_wavelet = self._create_wavelet_method_group()
        self.group_second_derivative = self._create_second_derivative_group()
        self.group_waterfall = self._create_waterfall_group()
        self.content_layout.addWidget(self.group_savgol, 0, Qt.AlignTop | Qt.AlignLeft)
        self.content_layout.addWidget(self.group_wavelet, 0, Qt.AlignTop | Qt.AlignLeft)
        self.content_layout.addWidget(self.group_second_derivative, 0, Qt.AlignTop | Qt.AlignLeft)
        self.content_layout.addWidget(self.group_waterfall, 0, Qt.AlignTop | Qt.AlignLeft)
        self.content_layout.addStretch(1)
        self.container.adjustSize()

        self.scroll.setAttachment(self.container)
        root.addWidget(self.scroll)

    def _create_method_group(self):
        controls = create_savgol_control_group(
            self.container,
            self.GROUP_WIDTH,
            self.GROUP_WIDTH - 80,
        )
        self.sg_window_box = controls.window_box
        self.sg_polyorder_box = controls.polyorder_box
        self.sg_axis_combo = controls.axis_combo

        self.sg_window_box.editingFinished.connect(self._sync_savgol_constraints)
        self.sg_polyorder_box.editingFinished.connect(self._sync_savgol_constraints)
        self._sync_savgol_constraints()
        return controls.group

    def _create_wavelet_method_group(self):
        controls = create_wavelet_control_group(
            self.container,
            self.GROUP_WIDTH,
            self.GROUP_WIDTH - 80,
        )
        self.wavelet_combo = controls.wavelet_combo
        self.wavelet_level_box = controls.level_box
        self.wavelet_threshold_rule_combo = controls.threshold_rule_combo
        self.wavelet_threshold_mode_combo = controls.threshold_mode_combo
        self.wavelet_strength_box = controls.strength_box

        self.wavelet_level_box.editingFinished.connect(self._sync_wavelet_constraints)
        self.wavelet_strength_box.editingFinished.connect(self._sync_wavelet_constraints)
        self._sync_wavelet_constraints()
        return controls.group

    def _create_waterfall_group(self):
        group, card = create_parameter_group(self.container, "瀑布图参数", self.GROUP_WIDTH)

        self.waterfall_step_box = create_double_spin_box(
            parent=card,
            width=self.GROUP_WIDTH - 80,
            title="k步长 (1/Å)",
            value=self.DEFAULT_WATERFALL_STEP,
            minimum=0.0001,
            maximum=10.0,
            single_step=0.01,
        )
        self.waterfall_step_box.editingFinished.connect(self._on_waterfall_step_edited)
        value_changed = getattr(self.waterfall_step_box, "valueChanged", None)
        if value_changed is not None:
            value_changed.connect(self._on_waterfall_step_value_changed)
        text_changed = getattr(self.waterfall_step_box, "textChanged", None)
        if text_changed is not None:
            text_changed.connect(self._on_waterfall_step_value_changed)

        finish_parameter_group(group, card, (self.waterfall_step_box,))
        self._sync_waterfall_constraints()
        return group

    def _create_second_derivative_group(self):
        group, card = create_parameter_group(self.container, "二阶导参数", self.GROUP_WIDTH)

        self.sd_threshold_box = create_double_spin_box(
            parent=card,
            width=self.GROUP_WIDTH - 80,
            title="曲率裁剪阈值",
            value=self.DEFAULT_SECOND_DERIVATIVE_PARAMS["threshold"],
            minimum=0.0,
            maximum=1.0,
            single_step=0.01,
        )

        self.sd_threshold_box.editingFinished.connect(self._sync_second_derivative_constraints)

        finish_parameter_group(group, card, (self.sd_threshold_box,))
        return group

    def _sync_second_derivative_constraints(self):
        threshold = self._commit_double_spinbox_value(
            self.sd_threshold_box,
            self.DEFAULT_SECOND_DERIVATIVE_PARAMS["threshold"],
        )
        threshold = max(0.0, min(float(threshold), 1.0))
        self.sd_threshold_box.setValue(threshold)

    def get_second_derivative_params(self):
        self._sync_second_derivative_constraints()
        return {
            "threshold": float(self.sd_threshold_box.value()),
        }

    @staticmethod
    def _commit_spinbox_value(spin_box, fallback):
        text = spin_box.text().strip()
        if not text:
            spin_box.setValue(fallback)
            return spin_box.value()

        try:
            value = int(text)
        except ValueError:
            spin_box.setValue(fallback)
            return spin_box.value()

        spin_box.setValue(value)
        return spin_box.value()

    @staticmethod
    def _commit_double_spinbox_value(spin_box, fallback):
        text = spin_box.text().strip()
        if not text:
            spin_box.setValue(fallback)
            return spin_box.value()

        try:
            value = float(text)
        except ValueError:
            spin_box.setValue(fallback)
            return spin_box.value()

        spin_box.setValue(value)
        return spin_box.value()

    def _sync_savgol_constraints(self):
        window_length = self._commit_spinbox_value(
            self.sg_window_box,
            self.DEFAULT_SG_PARAMS["window_length"],
        )
        if window_length < 3:
            window_length = 3
        if window_length % 2 == 0:
            window_length += 1
        self.sg_window_box.setValue(window_length)

        self.sg_polyorder_box.setMaximum(max(0, min(self.MAX_SG_POLYORDER, window_length - 1)))
        polyorder = self._commit_spinbox_value(
            self.sg_polyorder_box,
            self.DEFAULT_SG_PARAMS["polyorder"],
        )
        polyorder = max(0, min(polyorder, self.MAX_SG_POLYORDER, window_length - 1))
        self.sg_polyorder_box.setValue(polyorder)

    def _sync_wavelet_constraints(self):
        level = self._commit_spinbox_value(
            self.wavelet_level_box,
            self.DEFAULT_WAVELET_PARAMS["level"],
        )
        level = max(1, min(level, 6))
        self.wavelet_level_box.setValue(level)

        strength = self._commit_double_spinbox_value(
            self.wavelet_strength_box,
            self.DEFAULT_WAVELET_PARAMS["strength"],
        )
        strength = max(0.0, min(float(strength), 10.0))
        self.wavelet_strength_box.setValue(strength)

    def _sync_waterfall_constraints(self):
        step = self._commit_double_spinbox_value(
            self.waterfall_step_box,
            self.DEFAULT_WATERFALL_STEP,
        )
        step = max(0.0001, min(float(step), 10.0))
        self.waterfall_step_box.setValue(step)
        return float(step)

    def _on_waterfall_step_edited(self):
        self._sync_waterfall_constraints()
        self._waterfall_step_custom = True

    def _on_waterfall_step_value_changed(self, *_):
        self._waterfall_step_custom = True

    def get_savgol_params(self, data_shape=None):
        self._sync_savgol_constraints()

        window_length = self.sg_window_box.value()
        polyorder = self.sg_polyorder_box.value()
        smoothing_axis_label = self.sg_axis_combo.currentText()
        smoothing_axis = self.SG_AXIS_LABEL_TO_KEY.get(
            smoothing_axis_label,
            self.DEFAULT_SG_PARAMS["smoothing_axis"],
        )

        axis_index = {"kx": 0, "ky": 1, "e": 2}[smoothing_axis]
        axis_size = None
        if data_shape is not None and len(data_shape) >= 3:
            axis_size = int(data_shape[axis_index])

        if axis_size is not None and window_length > axis_size:
            raise ValueError(
                f"Savitzky-Golay 的滑动窗口长度 {window_length} 不能大于当前滑动轴长度 {axis_size}。"
            )

        return {
            "window_length": int(window_length),
            "polyorder": int(polyorder),
            "smoothing_axis": smoothing_axis,
        }

    def get_wavelet_params(self):
        self._sync_wavelet_constraints()
        return {
            "wavelet": self.wavelet_combo.currentText(),
            "level": int(self.wavelet_level_box.value()),
            "threshold_rule": self.wavelet_threshold_rule_combo.currentText(),
            "threshold_mode": self.wavelet_threshold_mode_combo.currentText(),
            "strength": float(self.wavelet_strength_box.value()),
        }

    def get_waterfall_step(self):
        return self._sync_waterfall_constraints()

    def set_waterfall_step(self, value, *, custom=None):
        self.waterfall_step_box.setValue(float(value))
        self._sync_waterfall_constraints()
        if custom is not None:
            self._waterfall_step_custom = bool(custom)

    def is_waterfall_step_custom(self):
        return bool(self._waterfall_step_custom)

    def build_method_specs(self, selected_methods, data_shape=None):
        self._sync_savgol_constraints()
        self._sync_wavelet_constraints()

        resolved_methods = []
        for method in selected_methods:
            if method == self.SG_METHOD_NAME:
                resolved_methods.append(
                    {
                        "label": method,
                        "params": self.get_savgol_params(data_shape),
                    }
                )
            elif method == self.WAVELET_METHOD_NAME:
                resolved_methods.append(
                    {
                        "label": method,
                        "params": self.get_wavelet_params(),
                    }
                )
            else:
                resolved_methods.append(method)
        return resolved_methods

    def export_state(self):
        return {
            "sg": {
                "window_length": int(self.sg_window_box.value()),
                "polyorder": int(self.sg_polyorder_box.value()),
                "smoothing_axis": self.sg_axis_combo.currentText(),
            },
            "wavelet": {
                "wavelet": self.wavelet_combo.currentText(),
                "level": int(self.wavelet_level_box.value()),
                "threshold_rule": self.wavelet_threshold_rule_combo.currentText(),
                "threshold_mode": self.wavelet_threshold_mode_combo.currentText(),
                "strength": float(self.wavelet_strength_box.value()),
            },
            "waterfall": {
                "k_step": float(self.waterfall_step_box.value()),
                "custom": bool(self._waterfall_step_custom),
            },
            "second_derivative": {
                "threshold": float(self.sd_threshold_box.value()),
            },
        }

    def restore_state(self, state, *, block_signals=True):
        state = state or {}
        widgets = [
            self.sg_window_box,
            self.sg_polyorder_box,
            self.sg_axis_combo,
            self.wavelet_combo,
            self.wavelet_level_box,
            self.wavelet_threshold_rule_combo,
            self.wavelet_threshold_mode_combo,
            self.wavelet_strength_box,
            self.waterfall_step_box,
            self.sd_threshold_box,
        ]
        blockers = [QSignalBlocker(widget) for widget in widgets] if block_signals else []

        try:
            sg_state = state.get("sg") or {}
            if "window_length" in sg_state:
                self.sg_window_box.setValue(int(sg_state["window_length"]))
            if "polyorder" in sg_state:
                self.sg_polyorder_box.setValue(int(sg_state["polyorder"]))
            if "smoothing_axis" in sg_state:
                axis_index = combo_index_for_text(self.sg_axis_combo, sg_state["smoothing_axis"])
                if axis_index >= 0:
                    self.sg_axis_combo.setCurrentIndex(axis_index)
            self._sync_savgol_constraints()

            wavelet_state = state.get("wavelet") or {}
            if "wavelet" in wavelet_state:
                wavelet_index = combo_index_for_text(self.wavelet_combo, wavelet_state["wavelet"])
                if wavelet_index >= 0:
                    self.wavelet_combo.setCurrentIndex(wavelet_index)
            if "level" in wavelet_state:
                self.wavelet_level_box.setValue(int(wavelet_state["level"]))
            if "threshold_rule" in wavelet_state:
                threshold_rule_index = combo_index_for_text(
                    self.wavelet_threshold_rule_combo,
                    wavelet_state["threshold_rule"],
                )
                if threshold_rule_index >= 0:
                    self.wavelet_threshold_rule_combo.setCurrentIndex(threshold_rule_index)
            if "threshold_mode" in wavelet_state:
                threshold_mode_index = combo_index_for_text(
                    self.wavelet_threshold_mode_combo,
                    wavelet_state["threshold_mode"],
                )
                if threshold_mode_index >= 0:
                    self.wavelet_threshold_mode_combo.setCurrentIndex(threshold_mode_index)
            if "strength" in wavelet_state:
                self.wavelet_strength_box.setValue(float(wavelet_state["strength"]))
            self._sync_wavelet_constraints()

            waterfall_state = state.get("waterfall") or {}
            if "k_step" in waterfall_state:
                self.waterfall_step_box.setValue(float(waterfall_state["k_step"]))
            self._sync_waterfall_constraints()
            self._waterfall_step_custom = bool(waterfall_state.get("custom", False))

            sd_state = state.get("second_derivative") or {}
            if "threshold" in sd_state:
                self.sd_threshold_box.setValue(float(sd_state["threshold"]))
            self._sync_second_derivative_constraints()
        finally:
            del blockers
