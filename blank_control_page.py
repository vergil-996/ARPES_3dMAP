from PyQt5.QtCore import QSignalBlocker, Qt
from PyQt5.QtWidgets import QSizePolicy, QVBoxLayout, QWidget
from siui.components.combobox_ import SiCapsuleComboBox
from siui.components.container import SiDenseContainer, SiTriSectionPanelCard
from siui.components.editbox import SiDoubleSpinBox, SiSpinBox
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.widgets import SiLabel, SiScrollArea
from siui.core import SiColor


class BlankControlPage(QWidget):
    SG_METHOD_NAME = "Savitzky-Golay滤波"
    GROUP_WIDTH = 560
    DEFAULT_SG_PARAMS = {
        "window_length": 5,
        "polyorder": 2,
        "smoothing_axis": "e",
    }
    SG_AXIS_LABEL_TO_KEY = {
        "E轴": "e",
        "Kx轴": "kx",
        "Ky轴": "ky",
    }
    SG_AXIS_KEY_TO_LABEL = {value: key for key, value in SG_AXIS_LABEL_TO_KEY.items()}
    MAX_SG_POLYORDER = 4
    WAVELET_METHOD_NAME = "小波去噪"
    DEFAULT_WAVELET_PARAMS = {
        "wavelet": "db4",
        "level": 3,
        "threshold_rule": "universal",
        "threshold_mode": "soft",
        "strength": 1.0,
    }
    WAVELET_OPTIONS = ["haar", "db2", "db4", "sym4", "coif1"]
    THRESHOLD_RULE_OPTIONS = ["universal", "sure", "bayes"]
    THRESHOLD_MODE_OPTIONS = ["soft", "hard"]

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

        self.group_savgol = self._create_method_group(self.SG_METHOD_NAME)
        self.group_wavelet = self._create_wavelet_method_group()
        self.content_layout.addWidget(self.group_savgol, 0, Qt.AlignTop | Qt.AlignLeft)
        self.content_layout.addWidget(self.group_wavelet, 0, Qt.AlignTop | Qt.AlignLeft)
        self.content_layout.addStretch(1)
        self.container.adjustSize()

        self.scroll.setAttachment(self.container)
        root.addWidget(self.scroll)

    def _create_method_group(self, title):
        group = SiTitledWidgetGroup(self.container)
        group.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        group.setFixedWidth(self.GROUP_WIDTH)
        group.addTitle(title)

        card = SiTriSectionPanelCard(group)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        card.header().hide()
        card.footer().hide()
        card.body().layout().setSpacing(12)

        self.sg_window_box = self._create_spin_box(
            parent=card,
            title="滑动窗口长度",
            value=self.DEFAULT_SG_PARAMS["window_length"],
            minimum=3,
            maximum=9999,
            single_step=2,
        )
        self.sg_polyorder_box = self._create_spin_box(
            parent=card,
            title="多项式阶数",
            value=self.DEFAULT_SG_PARAMS["polyorder"],
            minimum=0,
            maximum=min(self.MAX_SG_POLYORDER, self.DEFAULT_SG_PARAMS["window_length"] - 1),
            single_step=1,
        )
        self.sg_axis_combo = self._create_combo_box(
            parent=card,
            title="窗口滑动轴",
            items=list(self.SG_AXIS_LABEL_TO_KEY.keys()),
            current_text=self.SG_AXIS_KEY_TO_LABEL[self.DEFAULT_SG_PARAMS["smoothing_axis"]],
        )

        self.sg_window_box.editingFinished.connect(self._sync_savgol_constraints)
        self.sg_polyorder_box.editingFinished.connect(self._sync_savgol_constraints)

        card.body().addWidget(self.sg_window_box)
        card.body().addWidget(self.sg_polyorder_box)
        card.body().addWidget(self.sg_axis_combo)
        card.adjustSize()

        group.addWidget(card)
        self._apply_group_style(group)
        self._sync_savgol_constraints()
        return group

    def _create_wavelet_method_group(self):
        group = SiTitledWidgetGroup(self.container)
        group.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        group.setFixedWidth(self.GROUP_WIDTH)
        group.addTitle(self.WAVELET_METHOD_NAME)

        card = SiTriSectionPanelCard(group)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        card.header().hide()
        card.footer().hide()
        card.body().layout().setSpacing(12)

        self.wavelet_combo = self._create_combo_box(
            parent=card,
            title="wavelet",
            items=self.WAVELET_OPTIONS,
            current_text=self.DEFAULT_WAVELET_PARAMS["wavelet"],
        )
        self.wavelet_level_box = self._create_spin_box(
            parent=card,
            title="level",
            value=self.DEFAULT_WAVELET_PARAMS["level"],
            minimum=1,
            maximum=6,
            single_step=1,
        )
        self.wavelet_threshold_rule_combo = self._create_combo_box(
            parent=card,
            title="threshold_rule",
            items=self.THRESHOLD_RULE_OPTIONS,
            current_text=self.DEFAULT_WAVELET_PARAMS["threshold_rule"],
        )
        self.wavelet_threshold_mode_combo = self._create_combo_box(
            parent=card,
            title="threshold_mode",
            items=self.THRESHOLD_MODE_OPTIONS,
            current_text=self.DEFAULT_WAVELET_PARAMS["threshold_mode"],
        )
        self.wavelet_strength_box = self._create_double_spin_box(
            parent=card,
            title="strength",
            value=self.DEFAULT_WAVELET_PARAMS["strength"],
            minimum=0.0,
            maximum=10.0,
            single_step=0.1,
        )

        self.wavelet_level_box.editingFinished.connect(self._sync_wavelet_constraints)
        self.wavelet_strength_box.editingFinished.connect(self._sync_wavelet_constraints)

        card.body().addWidget(self.wavelet_combo)
        card.body().addWidget(self.wavelet_level_box)
        card.body().addWidget(self.wavelet_threshold_rule_combo)
        card.body().addWidget(self.wavelet_threshold_mode_combo)
        card.body().addWidget(self.wavelet_strength_box)
        card.adjustSize()

        group.addWidget(card)
        self._apply_group_style(group)
        self._sync_wavelet_constraints()
        return group

    def _create_spin_box(self, *, parent, title, value, minimum, maximum, single_step):
        spin_box = SiSpinBox(parent)
        spin_box.setTitle(title)
        spin_box.setMinimum(minimum)
        spin_box.setMaximum(maximum)
        spin_box.setSingleStep(single_step)
        spin_box.setValue(value)
        spin_box.resize(self.GROUP_WIDTH - 80, 58)
        spin_box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        return spin_box

    def _create_double_spin_box(self, *, parent, title, value, minimum, maximum, single_step):
        spin_box = SiDoubleSpinBox(parent)
        spin_box.setTitle(title)
        spin_box.setMinimum(minimum)
        spin_box.setMaximum(maximum)
        spin_box.setSingleStep(single_step)
        spin_box.setValue(value)
        spin_box.resize(self.GROUP_WIDTH - 80, 58)
        spin_box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        return spin_box

    def _create_combo_box(self, *, parent, title, items, current_text):
        combo_box = SiCapsuleComboBox(parent)
        combo_box.setTitle(title)
        combo_box.setMinimumHeight(36)
        combo_box.setEditable(False)
        combo_box.addItems(items)
        combo_box.resize(self.GROUP_WIDTH - 80, 36)
        combo_box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        current_index = self._find_combo_index(combo_box, current_text)
        if current_index >= 0:
            combo_box.setCurrentIndex(current_index)
        return combo_box

    @staticmethod
    def _find_combo_index(combo_box, text):
        for index in range(combo_box.count()):
            if combo_box.itemText(index) == text:
                return index
        return -1

    @staticmethod
    def _apply_group_style(group):
        for child in group.findChildren(SiLabel):
            try:
                child.colorGroup().assign(SiColor.TEXT_A, "#FFFFFF")
                child.reloadStyleSheet()
            except Exception:
                pass

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

    def reset_savgol_defaults(self):
        self.sg_window_box.setMaximum(9999)
        self.sg_window_box.setValue(self.DEFAULT_SG_PARAMS["window_length"])
        self.sg_polyorder_box.setMaximum(
            min(self.MAX_SG_POLYORDER, self.DEFAULT_SG_PARAMS["window_length"] - 1)
        )
        self.sg_polyorder_box.setValue(self.DEFAULT_SG_PARAMS["polyorder"])
        self.sg_axis_combo.setCurrentIndex(
            self._find_combo_index(
                self.sg_axis_combo,
                self.SG_AXIS_KEY_TO_LABEL[self.DEFAULT_SG_PARAMS["smoothing_axis"]]
            )
        )
        self._sync_savgol_constraints()

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
        ]
        blockers = [QSignalBlocker(widget) for widget in widgets] if block_signals else []

        try:
            sg_state = state.get("sg") or {}
            if "window_length" in sg_state:
                self.sg_window_box.setValue(int(sg_state["window_length"]))
            if "polyorder" in sg_state:
                self.sg_polyorder_box.setValue(int(sg_state["polyorder"]))
            if "smoothing_axis" in sg_state:
                axis_index = self._find_combo_index(self.sg_axis_combo, sg_state["smoothing_axis"])
                if axis_index >= 0:
                    self.sg_axis_combo.setCurrentIndex(axis_index)
            self._sync_savgol_constraints()

            wavelet_state = state.get("wavelet") or {}
            if "wavelet" in wavelet_state:
                wavelet_index = self._find_combo_index(self.wavelet_combo, wavelet_state["wavelet"])
                if wavelet_index >= 0:
                    self.wavelet_combo.setCurrentIndex(wavelet_index)
            if "level" in wavelet_state:
                self.wavelet_level_box.setValue(int(wavelet_state["level"]))
            if "threshold_rule" in wavelet_state:
                threshold_rule_index = self._find_combo_index(
                    self.wavelet_threshold_rule_combo,
                    wavelet_state["threshold_rule"],
                )
                if threshold_rule_index >= 0:
                    self.wavelet_threshold_rule_combo.setCurrentIndex(threshold_rule_index)
            if "threshold_mode" in wavelet_state:
                threshold_mode_index = self._find_combo_index(
                    self.wavelet_threshold_mode_combo,
                    wavelet_state["threshold_mode"],
                )
                if threshold_mode_index >= 0:
                    self.wavelet_threshold_mode_combo.setCurrentIndex(threshold_mode_index)
            if "strength" in wavelet_state:
                self.wavelet_strength_box.setValue(float(wavelet_state["strength"]))
            self._sync_wavelet_constraints()
        finally:
            del blockers
