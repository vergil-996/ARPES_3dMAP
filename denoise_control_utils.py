from dataclasses import dataclass

from PyQt5.QtWidgets import QSizePolicy
from siui.components.combobox_ import SiCapsuleComboBox
from siui.components.container import SiTriSectionPanelCard
from siui.components.editbox import SiDoubleSpinBox, SiSpinBox
from siui.components.titled_widget_group import SiTitledWidgetGroup

from control_layout_utils import apply_label_color, combo_index_for_text
from denoise_config import (
    DEFAULT_SG_PARAMS,
    DEFAULT_WAVELET_PARAMS,
    MAX_SG_POLYORDER,
    SG_AXIS_KEY_TO_LABEL,
    SG_AXIS_LABEL_TO_KEY,
    SG_METHOD_NAME,
    THRESHOLD_MODE_OPTIONS,
    THRESHOLD_RULE_OPTIONS,
    WAVELET_METHOD_NAME,
    WAVELET_OPTIONS,
)


@dataclass(frozen=True)
class SavgolControlGroup:
    group: object
    window_box: object
    polyorder_box: object
    axis_combo: object


@dataclass(frozen=True)
class WaveletControlGroup:
    group: object
    wavelet_combo: object
    level_box: object
    threshold_rule_combo: object
    threshold_mode_combo: object
    strength_box: object


def create_spin_box(
    *, parent, width, title, value, minimum, maximum, single_step
):
    spin_box = SiSpinBox(parent)
    spin_box.setTitle(title)
    spin_box.setMinimum(minimum)
    spin_box.setMaximum(maximum)
    spin_box.setSingleStep(single_step)
    spin_box.setValue(value)
    spin_box.resize(width, 58)
    spin_box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return spin_box


def create_double_spin_box(
    *, parent, width, title, value, minimum, maximum, single_step
):
    spin_box = SiDoubleSpinBox(parent)
    spin_box.setTitle(title)
    spin_box.setMinimum(minimum)
    spin_box.setMaximum(maximum)
    spin_box.setSingleStep(single_step)
    spin_box.setValue(value)
    spin_box.resize(width, 58)
    spin_box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return spin_box


def create_combo_box(*, parent, width, title, items, current_text):
    combo_box = SiCapsuleComboBox(parent)
    combo_box.setTitle(title)
    combo_box.setMinimumHeight(36)
    combo_box.setEditable(False)
    combo_box.addItems(items)
    combo_box.resize(width, 36)
    combo_box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    current_index = combo_index_for_text(combo_box, current_text)
    if current_index >= 0:
        combo_box.setCurrentIndex(current_index)
    return combo_box


def create_parameter_group(parent, title, width):
    group = SiTitledWidgetGroup(parent)
    group.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
    group.setFixedWidth(width)
    group.addTitle(title)

    card = SiTriSectionPanelCard(group)
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    card.header().hide()
    card.footer().hide()
    card.body().layout().setSpacing(12)
    return group, card


def finish_parameter_group(group, card, controls):
    for control in controls:
        card.body().addWidget(control)
    card.adjustSize()
    group.addWidget(card)
    apply_label_color(group, "#FFFFFF")
    return group


def create_savgol_control_group(parent, group_width, control_width):
    group, card = create_parameter_group(parent, SG_METHOD_NAME, group_width)
    window_box = create_spin_box(
        parent=card,
        width=control_width,
        title="滑动窗口长度",
        value=DEFAULT_SG_PARAMS["window_length"],
        minimum=3,
        maximum=9999,
        single_step=2,
    )
    polyorder_box = create_spin_box(
        parent=card,
        width=control_width,
        title="多项式阶数",
        value=DEFAULT_SG_PARAMS["polyorder"],
        minimum=0,
        maximum=min(MAX_SG_POLYORDER, DEFAULT_SG_PARAMS["window_length"] - 1),
        single_step=1,
    )
    axis_combo = create_combo_box(
        parent=card,
        width=control_width,
        title="窗口滑动轴",
        items=tuple(SG_AXIS_LABEL_TO_KEY),
        current_text=SG_AXIS_KEY_TO_LABEL[DEFAULT_SG_PARAMS["smoothing_axis"]],
    )
    finish_parameter_group(group, card, (window_box, polyorder_box, axis_combo))
    return SavgolControlGroup(group, window_box, polyorder_box, axis_combo)


def create_wavelet_control_group(parent, group_width, control_width):
    group, card = create_parameter_group(parent, WAVELET_METHOD_NAME, group_width)
    wavelet_combo = create_combo_box(
        parent=card,
        width=control_width,
        title="wavelet",
        items=WAVELET_OPTIONS,
        current_text=DEFAULT_WAVELET_PARAMS["wavelet"],
    )
    level_box = create_spin_box(
        parent=card,
        width=control_width,
        title="level",
        value=DEFAULT_WAVELET_PARAMS["level"],
        minimum=1,
        maximum=6,
        single_step=1,
    )
    threshold_rule_combo = create_combo_box(
        parent=card,
        width=control_width,
        title="threshold_rule",
        items=THRESHOLD_RULE_OPTIONS,
        current_text=DEFAULT_WAVELET_PARAMS["threshold_rule"],
    )
    threshold_mode_combo = create_combo_box(
        parent=card,
        width=control_width,
        title="threshold_mode",
        items=THRESHOLD_MODE_OPTIONS,
        current_text=DEFAULT_WAVELET_PARAMS["threshold_mode"],
    )
    strength_box = create_double_spin_box(
        parent=card,
        width=control_width,
        title="strength",
        value=DEFAULT_WAVELET_PARAMS["strength"],
        minimum=0.0,
        maximum=10.0,
        single_step=0.1,
    )
    finish_parameter_group(
        group,
        card,
        (
            wavelet_combo,
            level_box,
            threshold_rule_combo,
            threshold_mode_combo,
            strength_box,
        ),
    )
    return WaveletControlGroup(
        group,
        wavelet_combo,
        level_box,
        threshold_rule_combo,
        threshold_mode_combo,
        strength_box,
    )
