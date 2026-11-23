from PyQt5.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt5.QtWidgets import QVBoxLayout, QWidget
from siui.components.widgets import SiScrollArea

from bandscope.ui.control_layout_utils import combo_index_for_text
from bandscope.processing.denoise_config import (
    DEFAULT_GAUSSIAN_PARAMS as CONFIG_DEFAULT_GAUSSIAN_PARAMS,
    DEFAULT_LOW_RANK_PARAMS as CONFIG_DEFAULT_LOW_RANK_PARAMS,
    DEFAULT_MEDIAN_PARAMS as CONFIG_DEFAULT_MEDIAN_PARAMS,
    DEFAULT_NLMEANS_PARAMS as CONFIG_DEFAULT_NLMEANS_PARAMS,
    DEFAULT_SG_PARAMS as CONFIG_DEFAULT_SG_PARAMS,
    DEFAULT_WAVELET_PARAMS as CONFIG_DEFAULT_WAVELET_PARAMS,
    GAUSSIAN_METHOD_NAME as CONFIG_GAUSSIAN_METHOD_NAME,
    LOW_RANK_METHOD_NAME as CONFIG_LOW_RANK_METHOD_NAME,
    MAX_SG_POLYORDER as CONFIG_MAX_SG_POLYORDER,
    MEDIAN_METHOD_NAME as CONFIG_MEDIAN_METHOD_NAME,
    NLM_METHOD_NAME as CONFIG_NLM_METHOD_NAME,
    SG_AXIS_KEY_TO_LABEL as CONFIG_SG_AXIS_KEY_TO_LABEL,
    SG_AXIS_LABEL_TO_KEY as CONFIG_SG_AXIS_LABEL_TO_KEY,
    SG_METHOD_NAME as CONFIG_SG_METHOD_NAME,
    THRESHOLD_MODE_OPTIONS as CONFIG_THRESHOLD_MODE_OPTIONS,
    THRESHOLD_RULE_OPTIONS as CONFIG_THRESHOLD_RULE_OPTIONS,
    WAVELET_METHOD_NAME as CONFIG_WAVELET_METHOD_NAME,
    WAVELET_OPTIONS as CONFIG_WAVELET_OPTIONS,
    canonical_method_name,
)
from bandscope.ui.denoise_control_utils import (
    create_double_spin_box,
    create_gaussian_control_group,
    create_low_rank_control_group,
    create_median_control_group,
    create_nlmeans_control_group,
    create_parameter_group,
    create_savgol_control_group,
    create_wavelet_control_group,
    finish_parameter_group,
)


class BlankControlPage(QWidget):
    state_restored = pyqtSignal()

    GAUSSIAN_METHOD_NAME = CONFIG_GAUSSIAN_METHOD_NAME
    MEDIAN_METHOD_NAME = CONFIG_MEDIAN_METHOD_NAME
    SG_METHOD_NAME = CONFIG_SG_METHOD_NAME
    WAVELET_METHOD_NAME = CONFIG_WAVELET_METHOD_NAME
    NLM_METHOD_NAME = CONFIG_NLM_METHOD_NAME
    LOW_RANK_METHOD_NAME = CONFIG_LOW_RANK_METHOD_NAME
    GROUP_WIDTH = 560
    DEFAULT_GAUSSIAN_PARAMS = CONFIG_DEFAULT_GAUSSIAN_PARAMS
    DEFAULT_MEDIAN_PARAMS = CONFIG_DEFAULT_MEDIAN_PARAMS
    DEFAULT_SG_PARAMS = CONFIG_DEFAULT_SG_PARAMS
    DEFAULT_WAVELET_PARAMS = CONFIG_DEFAULT_WAVELET_PARAMS
    DEFAULT_NLMEANS_PARAMS = CONFIG_DEFAULT_NLMEANS_PARAMS
    DEFAULT_LOW_RANK_PARAMS = CONFIG_DEFAULT_LOW_RANK_PARAMS
    SG_AXIS_LABEL_TO_KEY = CONFIG_SG_AXIS_LABEL_TO_KEY
    SG_AXIS_KEY_TO_LABEL = CONFIG_SG_AXIS_KEY_TO_LABEL
    MAX_SG_POLYORDER = CONFIG_MAX_SG_POLYORDER
    WAVELET_OPTIONS = CONFIG_WAVELET_OPTIONS
    THRESHOLD_RULE_OPTIONS = CONFIG_THRESHOLD_RULE_OPTIONS
    THRESHOLD_MODE_OPTIONS = CONFIG_THRESHOLD_MODE_OPTIONS

    DEFAULT_SECOND_DERIVATIVE_PARAMS = {
        "threshold": 0.0,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
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

        self.group_gaussian = self._create_gaussian_method_group()
        self.group_median = self._create_median_method_group()
        self.group_savgol = self._create_method_group()
        self.group_wavelet = self._create_wavelet_method_group()
        self.group_nlmeans = self._create_nlmeans_method_group()
        self.group_low_rank = self._create_low_rank_method_group()
        self.group_second_derivative = self._create_second_derivative_group()
        for group in (
            self.group_gaussian,
            self.group_median,
            self.group_savgol,
            self.group_wavelet,
            self.group_nlmeans,
            self.group_low_rank,
            self.group_second_derivative,
        ):
            self.content_layout.addWidget(group, 0, Qt.AlignTop | Qt.AlignLeft)
        self.content_layout.addStretch(1)
        self.container.adjustSize()

        self.scroll.setAttachment(self.container)
        root.addWidget(self.scroll)

    def _create_gaussian_method_group(self):
        controls = create_gaussian_control_group(
            self.container,
            self.GROUP_WIDTH,
            self.GROUP_WIDTH - 80,
        )
        self.gaussian_sigma_kx_box = controls.sigma_kx_box
        self.gaussian_sigma_ky_box = controls.sigma_ky_box
        self.gaussian_sigma_e_box = controls.sigma_e_box
        for spin_box in (
            self.gaussian_sigma_kx_box,
            self.gaussian_sigma_ky_box,
            self.gaussian_sigma_e_box,
        ):
            spin_box.editingFinished.connect(self._sync_gaussian_constraints)
        self._sync_gaussian_constraints()
        return controls.group

    def _create_median_method_group(self):
        controls = create_median_control_group(
            self.container,
            self.GROUP_WIDTH,
            self.GROUP_WIDTH - 80,
        )
        self.median_size_kx_box = controls.size_kx_box
        self.median_size_ky_box = controls.size_ky_box
        self.median_size_e_box = controls.size_e_box
        for spin_box in (
            self.median_size_kx_box,
            self.median_size_ky_box,
            self.median_size_e_box,
        ):
            spin_box.editingFinished.connect(self._sync_median_constraints)
        self._sync_median_constraints()
        return controls.group

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
        self.wavelet_noise_sigma_box = controls.noise_sigma_box

        self.wavelet_level_box.editingFinished.connect(self._sync_wavelet_constraints)
        self.wavelet_strength_box.editingFinished.connect(self._sync_wavelet_constraints)
        self.wavelet_noise_sigma_box.editingFinished.connect(self._sync_wavelet_constraints)
        self._sync_wavelet_constraints()
        return controls.group

    def _create_nlmeans_method_group(self):
        controls = create_nlmeans_control_group(
            self.container,
            self.GROUP_WIDTH,
            self.GROUP_WIDTH - 80,
        )
        self.nlmeans_patch_size_box = controls.patch_size_box
        self.nlmeans_patch_distance_box = controls.patch_distance_box
        self.nlmeans_h_scale_box = controls.h_scale_box
        self.nlmeans_noise_sigma_box = controls.noise_sigma_box
        for spin_box in (
            self.nlmeans_patch_size_box,
            self.nlmeans_patch_distance_box,
            self.nlmeans_h_scale_box,
            self.nlmeans_noise_sigma_box,
        ):
            spin_box.editingFinished.connect(self._sync_nlmeans_constraints)
        self._sync_nlmeans_constraints()
        return controls.group

    def _create_low_rank_method_group(self):
        controls = create_low_rank_control_group(
            self.container,
            self.GROUP_WIDTH,
            self.GROUP_WIDTH - 80,
        )
        self.low_rank_rank_box = controls.rank_box
        self.low_rank_rank_box.editingFinished.connect(self._sync_low_rank_constraints)
        self._sync_low_rank_constraints()
        return controls.group

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

        self.sd_threshold_box.setToolTip("二阶导曲率裁剪阈值（无量纲），范围为 0–1。")
        self.sd_threshold_box.editingFinished.connect(self._sync_second_derivative_constraints)

        finish_parameter_group(group, card, (self.sd_threshold_box,))
        return group

    def _sync_gaussian_constraints(self):
        for parameter_key, spin_box in (
            ("sigma_kx", self.gaussian_sigma_kx_box),
            ("sigma_ky", self.gaussian_sigma_ky_box),
            ("sigma_e", self.gaussian_sigma_e_box),
        ):
            sigma = self._commit_double_spinbox_value(
                spin_box,
                self.DEFAULT_GAUSSIAN_PARAMS[parameter_key],
            )
            spin_box.setValue(max(0.0, min(float(sigma), 20.0)))

    def _sync_median_constraints(self):
        for parameter_key, spin_box in (
            ("size_kx", self.median_size_kx_box),
            ("size_ky", self.median_size_ky_box),
            ("size_e", self.median_size_e_box),
        ):
            size = self._commit_spinbox_value(
                spin_box,
                self.DEFAULT_MEDIAN_PARAMS[parameter_key],
            )
            size = max(1, min(size, 31))
            if size % 2 == 0:
                size += 1
            spin_box.setValue(size)

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

        noise_sigma = self._commit_double_spinbox_value(
            self.wavelet_noise_sigma_box,
            self.DEFAULT_WAVELET_PARAMS["noise_sigma"],
        )
        noise_sigma = max(0.0, min(float(noise_sigma), 1.0e9))
        self.wavelet_noise_sigma_box.setValue(noise_sigma)

    def _sync_nlmeans_constraints(self):
        patch_size = self._commit_spinbox_value(
            self.nlmeans_patch_size_box,
            self.DEFAULT_NLMEANS_PARAMS["patch_size"],
        )
        patch_size = max(1, min(patch_size, 15))
        if patch_size % 2 == 0:
            patch_size += 1
        self.nlmeans_patch_size_box.setValue(patch_size)

        patch_distance = self._commit_spinbox_value(
            self.nlmeans_patch_distance_box,
            self.DEFAULT_NLMEANS_PARAMS["patch_distance"],
        )
        patch_distance = max(1, min(patch_distance, 20))
        self.nlmeans_patch_distance_box.setValue(patch_distance)

        h_scale = self._commit_double_spinbox_value(
            self.nlmeans_h_scale_box,
            self.DEFAULT_NLMEANS_PARAMS["h_scale"],
        )
        h_scale = max(0.01, min(float(h_scale), 100.0))
        self.nlmeans_h_scale_box.setValue(h_scale)

        noise_sigma = self._commit_double_spinbox_value(
            self.nlmeans_noise_sigma_box,
            self.DEFAULT_NLMEANS_PARAMS["noise_sigma"],
        )
        noise_sigma = max(0.0, min(float(noise_sigma), 1.0e9))
        self.nlmeans_noise_sigma_box.setValue(noise_sigma)

    def _sync_low_rank_constraints(self):
        rank = self._commit_spinbox_value(
            self.low_rank_rank_box,
            self.DEFAULT_LOW_RANK_PARAMS["rank"],
        )
        self.low_rank_rank_box.setValue(max(1, min(rank, 9999)))

    def get_gaussian_params(self):
        self._sync_gaussian_constraints()
        return {
            "sigma_kx": float(self.gaussian_sigma_kx_box.value()),
            "sigma_ky": float(self.gaussian_sigma_ky_box.value()),
            "sigma_e": float(self.gaussian_sigma_e_box.value()),
        }

    def get_median_params(self):
        self._sync_median_constraints()
        return {
            "size_kx": int(self.median_size_kx_box.value()),
            "size_ky": int(self.median_size_ky_box.value()),
            "size_e": int(self.median_size_e_box.value()),
        }

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
            "noise_sigma": float(self.wavelet_noise_sigma_box.value()),
        }

    def get_nlmeans_params(self):
        self._sync_nlmeans_constraints()
        return {
            "patch_size": int(self.nlmeans_patch_size_box.value()),
            "patch_distance": int(self.nlmeans_patch_distance_box.value()),
            "h_scale": float(self.nlmeans_h_scale_box.value()),
            "noise_sigma": float(self.nlmeans_noise_sigma_box.value()),
        }

    def get_low_rank_params(self):
        self._sync_low_rank_constraints()
        return {"rank": int(self.low_rank_rank_box.value())}

    def build_method_specs(self, selected_methods, data_shape=None):
        param_getters = {
            self.GAUSSIAN_METHOD_NAME: self.get_gaussian_params,
            self.MEDIAN_METHOD_NAME: self.get_median_params,
            self.WAVELET_METHOD_NAME: self.get_wavelet_params,
            self.NLM_METHOD_NAME: self.get_nlmeans_params,
            self.LOW_RANK_METHOD_NAME: self.get_low_rank_params,
        }

        resolved_methods = []
        for method in selected_methods:
            method_name = canonical_method_name(method)
            if method_name == self.SG_METHOD_NAME:
                resolved_methods.append(
                    {
                        "label": method_name,
                        "params": self.get_savgol_params(data_shape),
                    }
                )
            elif method_name in param_getters:
                resolved_methods.append(
                    {
                        "label": method_name,
                        "params": param_getters[method_name](),
                    }
                )
            else:
                resolved_methods.append(method_name)
        return resolved_methods

    def export_state(self):
        self._sync_savgol_constraints()
        return {
            "gaussian": self.get_gaussian_params(),
            "median": self.get_median_params(),
            "sg": {
                "window_length": int(self.sg_window_box.value()),
                "polyorder": int(self.sg_polyorder_box.value()),
                "smoothing_axis": self.sg_axis_combo.currentText(),
            },
            "wavelet": self.get_wavelet_params(),
            "nlmeans": self.get_nlmeans_params(),
            "low_rank": self.get_low_rank_params(),
            "second_derivative": self.get_second_derivative_params(),
        }

    def restore_state(self, state, *, block_signals=True):
        state = state or {}
        widgets = [
            self.gaussian_sigma_kx_box,
            self.gaussian_sigma_ky_box,
            self.gaussian_sigma_e_box,
            self.median_size_kx_box,
            self.median_size_ky_box,
            self.median_size_e_box,
            self.sg_window_box,
            self.sg_polyorder_box,
            self.sg_axis_combo,
            self.wavelet_combo,
            self.wavelet_level_box,
            self.wavelet_threshold_rule_combo,
            self.wavelet_threshold_mode_combo,
            self.wavelet_strength_box,
            self.wavelet_noise_sigma_box,
            self.nlmeans_patch_size_box,
            self.nlmeans_patch_distance_box,
            self.nlmeans_h_scale_box,
            self.nlmeans_noise_sigma_box,
            self.low_rank_rank_box,
            self.sd_threshold_box,
        ]
        blockers = [QSignalBlocker(widget) for widget in widgets] if block_signals else []

        try:
            gaussian_state = state.get("gaussian") or {}
            for parameter_key, spin_box in (
                ("sigma_kx", self.gaussian_sigma_kx_box),
                ("sigma_ky", self.gaussian_sigma_ky_box),
                ("sigma_e", self.gaussian_sigma_e_box),
            ):
                if parameter_key in gaussian_state:
                    spin_box.setValue(float(gaussian_state[parameter_key]))
            self._sync_gaussian_constraints()

            median_state = state.get("median") or {}
            for parameter_key, spin_box in (
                ("size_kx", self.median_size_kx_box),
                ("size_ky", self.median_size_ky_box),
                ("size_e", self.median_size_e_box),
            ):
                if parameter_key in median_state:
                    spin_box.setValue(int(median_state[parameter_key]))
            self._sync_median_constraints()

            sg_state = state.get("sg") or {}
            if "window_length" in sg_state:
                self.sg_window_box.setValue(int(sg_state["window_length"]))
            if "polyorder" in sg_state:
                self.sg_polyorder_box.setValue(int(sg_state["polyorder"]))
            if "smoothing_axis" in sg_state:
                smoothing_axis = sg_state["smoothing_axis"]
                axis_label = self.SG_AXIS_KEY_TO_LABEL.get(smoothing_axis, smoothing_axis)
                axis_index = combo_index_for_text(self.sg_axis_combo, axis_label)
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
            if "noise_sigma" in wavelet_state:
                self.wavelet_noise_sigma_box.setValue(float(wavelet_state["noise_sigma"]))
            self._sync_wavelet_constraints()

            nlmeans_state = state.get("nlmeans") or {}
            if "patch_size" in nlmeans_state:
                self.nlmeans_patch_size_box.setValue(int(nlmeans_state["patch_size"]))
            if "patch_distance" in nlmeans_state:
                self.nlmeans_patch_distance_box.setValue(int(nlmeans_state["patch_distance"]))
            if "h_scale" in nlmeans_state:
                self.nlmeans_h_scale_box.setValue(float(nlmeans_state["h_scale"]))
            if "noise_sigma" in nlmeans_state:
                self.nlmeans_noise_sigma_box.setValue(float(nlmeans_state["noise_sigma"]))
            self._sync_nlmeans_constraints()

            low_rank_state = state.get("low_rank") or {}
            if "rank" in low_rank_state:
                self.low_rank_rank_box.setValue(int(low_rank_state["rank"]))
            self._sync_low_rank_constraints()

            sd_state = state.get("second_derivative") or {}
            if "threshold" in sd_state:
                self.sd_threshold_box.setValue(float(sd_state["threshold"]))
            self._sync_second_derivative_constraints()
        finally:
            del blockers
        self.state_restored.emit()
