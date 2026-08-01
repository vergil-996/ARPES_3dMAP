from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QSizePolicy, QVBoxLayout, QWidget
from siui.components.combobox_ import SiCapsuleComboBox
from siui.components.container import SiTriSectionPanelCard
from siui.components.editbox import SiDoubleSpinBox, SiSpinBox
from siui.components.titled_widget_group import SiTitledWidgetGroup
from control_layout_utils import apply_label_color


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
        group = SiTitledWidgetGroup(self)
        group.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        group.setFixedWidth(self.POPUP_WIDTH - 60)
        group.addTitle("Savitzky-Golay滤波")

        card = SiTriSectionPanelCard(group)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        card.header().hide()
        card.footer().hide()
        card.body().layout().setSpacing(12)

        self.sg_window_box = self._create_spin_box(
            parent=card,
            title="滑动窗口长度",
            value=5,
            minimum=3,
            maximum=9999,
            single_step=2,
        )
        self.sg_polyorder_box = self._create_spin_box(
            parent=card,
            title="多项式阶数",
            value=2,
            minimum=0,
            maximum=4,
            single_step=1,
        )
        self.sg_axis_combo = self._create_combo_box(
            parent=card,
            title="窗口滑动轴",
            items=["E轴", "Kx轴", "Ky轴"],
            current_text="E轴",
        )

        self.sg_window_box.editingFinished.connect(self._on_sg_window_changed)
        self.sg_polyorder_box.editingFinished.connect(self._on_sg_polyorder_changed)
        self.sg_axis_combo.currentIndexChanged.connect(self._on_sg_axis_changed)

        card.body().addWidget(self.sg_window_box)
        card.body().addWidget(self.sg_polyorder_box)
        card.body().addWidget(self.sg_axis_combo)
        card.adjustSize()

        group.addWidget(card)
        apply_label_color(group, "#FFFFFF")
        return group

    def _create_wavelet_group(self):
        group = SiTitledWidgetGroup(self)
        group.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        group.setFixedWidth(self.POPUP_WIDTH - 60)
        group.addTitle("小波去噪")

        card = SiTriSectionPanelCard(group)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        card.header().hide()
        card.footer().hide()
        card.body().layout().setSpacing(12)

        self.wavelet_combo = self._create_combo_box(
            parent=card,
            title="wavelet",
            items=["haar", "db2", "db4", "sym4", "coif1"],
            current_text="db4",
        )
        self.wavelet_level_box = self._create_spin_box(
            parent=card,
            title="level",
            value=3,
            minimum=1,
            maximum=6,
            single_step=1,
        )
        self.wavelet_threshold_rule_combo = self._create_combo_box(
            parent=card,
            title="threshold_rule",
            items=["universal", "sure", "bayes"],
            current_text="universal",
        )
        self.wavelet_threshold_mode_combo = self._create_combo_box(
            parent=card,
            title="threshold_mode",
            items=["soft", "hard"],
            current_text="soft",
        )
        self.wavelet_strength_box = self._create_double_spin_box(
            parent=card,
            title="strength",
            value=1.0,
            minimum=0.0,
            maximum=10.0,
            single_step=0.1,
        )

        self.wavelet_combo.currentIndexChanged.connect(self._on_wavelet_combo_changed)
        self.wavelet_level_box.editingFinished.connect(self._on_wavelet_level_changed)
        self.wavelet_threshold_rule_combo.currentIndexChanged.connect(self._on_wavelet_rule_changed)
        self.wavelet_threshold_mode_combo.currentIndexChanged.connect(self._on_wavelet_mode_changed)
        self.wavelet_strength_box.editingFinished.connect(self._on_wavelet_strength_changed)

        card.body().addWidget(self.wavelet_combo)
        card.body().addWidget(self.wavelet_level_box)
        card.body().addWidget(self.wavelet_threshold_rule_combo)
        card.body().addWidget(self.wavelet_threshold_mode_combo)
        card.body().addWidget(self.wavelet_strength_box)
        card.adjustSize()

        group.addWidget(card)
        apply_label_color(group, "#FFFFFF")
        return group

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_from_blank()

    def _sync_from_blank(self):
        blank = self._blank
        self.sg_window_box.setValue(blank.sg_window_box.value())
        self.sg_polyorder_box.setValue(blank.sg_polyorder_box.value())
        sg_axis_text = blank.sg_axis_combo.currentText()
        sg_axis_idx = self._find_combo_index(self.sg_axis_combo, sg_axis_text)
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
        idx = self._find_combo_index(self._blank.sg_axis_combo, sg_axis_text)
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

    @staticmethod
    def _find_combo_index(combo_box, text):
        for index in range(combo_box.count()):
            if combo_box.itemText(index) == text:
                return index
        return -1

    def _create_spin_box(self, *, parent, title, value, minimum, maximum, single_step):
        spin_box = SiSpinBox(parent)
        spin_box.setTitle(title)
        spin_box.setMinimum(minimum)
        spin_box.setMaximum(maximum)
        spin_box.setSingleStep(single_step)
        spin_box.setValue(value)
        spin_box.resize(self.POPUP_WIDTH - 100, 58)
        spin_box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        return spin_box

    def _create_double_spin_box(self, *, parent, title, value, minimum, maximum, single_step):
        spin_box = SiDoubleSpinBox(parent)
        spin_box.setTitle(title)
        spin_box.setMinimum(minimum)
        spin_box.setMaximum(maximum)
        spin_box.setSingleStep(single_step)
        spin_box.setValue(value)
        spin_box.resize(self.POPUP_WIDTH - 100, 58)
        spin_box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        return spin_box

    def _create_combo_box(self, *, parent, title, items, current_text):
        combo_box = SiCapsuleComboBox(parent)
        combo_box.setTitle(title)
        combo_box.setMinimumHeight(36)
        combo_box.setEditable(False)
        combo_box.addItems(items)
        combo_box.resize(self.POPUP_WIDTH - 100, 36)
        combo_box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        current_index = self._find_combo_index(combo_box, current_text)
        if current_index >= 0:
            combo_box.setCurrentIndex(current_index)
        return combo_box


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

        group = SiTitledWidgetGroup(self)
        group.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        group.setFixedWidth(self.POPUP_WIDTH - 60)
        group.addTitle("瀑布图参数")

        card = SiTriSectionPanelCard(group)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        card.header().hide()
        card.footer().hide()
        card.body().layout().setSpacing(12)

        self.waterfall_step_box = self._create_double_spin_box(
            parent=card,
            title="k步长 (1/Å)",
            value=0.01,
            minimum=0.0001,
            maximum=10.0,
            single_step=0.01,
        )
        self.waterfall_step_box.editingFinished.connect(self._on_step_changed)

        card.body().addWidget(self.waterfall_step_box)
        card.adjustSize()

        group.addWidget(card)
        apply_label_color(group, "#FFFFFF")
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

    def _create_double_spin_box(self, *, parent, title, value, minimum, maximum, single_step):
        spin_box = SiDoubleSpinBox(parent)
        spin_box.setTitle(title)
        spin_box.setMinimum(minimum)
        spin_box.setMaximum(maximum)
        spin_box.setSingleStep(single_step)
        spin_box.setValue(value)
        spin_box.resize(self.POPUP_WIDTH - 100, 58)
        spin_box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        return spin_box
