from dataclasses import dataclass

from PyQt5.QtWidgets import QSizePolicy
from siui.components.combobox_ import SiCapsuleComboBox
from siui.components.container import SiTriSectionPanelCard
from siui.components.editbox import SiDoubleSpinBox, SiSpinBox
from siui.components.titled_widget_group import SiTitledWidgetGroup

from bandscope.ui.control_layout_utils import apply_label_color, combo_index_for_text
import bandscope.ui.theme as theme
from bandscope.processing.denoise_config import (
    DEFAULT_GAUSSIAN_PARAMS,
    DEFAULT_LOW_RANK_PARAMS,
    DEFAULT_MEDIAN_PARAMS,
    DEFAULT_NLMEANS_PARAMS,
    DEFAULT_SG_PARAMS,
    DEFAULT_WAVELET_PARAMS,
    GAUSSIAN_METHOD_NAME,
    LOW_RANK_METHOD_NAME,
    MAX_SG_POLYORDER,
    MEDIAN_METHOD_NAME,
    NLM_METHOD_NAME,
    SG_AXIS_KEY_TO_LABEL,
    SG_AXIS_LABEL_TO_KEY,
    SG_METHOD_NAME,
    THRESHOLD_MODE_OPTIONS,
    THRESHOLD_RULE_OPTIONS,
    WAVELET_METHOD_NAME,
    WAVELET_OPTIONS,
)


@dataclass(frozen=True)
class GaussianControlGroup:
    group: object
    sigma_kx_box: object
    sigma_ky_box: object
    sigma_e_box: object


@dataclass(frozen=True)
class MedianControlGroup:
    group: object
    size_kx_box: object
    size_ky_box: object
    size_e_box: object


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
    noise_sigma_box: object


@dataclass(frozen=True)
class NLMeansControlGroup:
    group: object
    patch_size_box: object
    patch_distance_box: object
    h_scale_box: object
    noise_sigma_box: object


@dataclass(frozen=True)
class LowRankControlGroup:
    group: object
    rank_box: object


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
    theme.raise_well_on_card(combo_box, adjust_layout=False)
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
    apply_label_color(group, theme.TEXT_1)
    return group


def create_gaussian_control_group(parent, group_width, control_width):
    group, card = create_parameter_group(parent, GAUSSIAN_METHOD_NAME, group_width)
    sigma_boxes = []
    for axis, parameter_key in (("Kx", "sigma_kx"), ("Ky", "sigma_ky"), ("E", "sigma_e")):
        sigma_box = create_double_spin_box(
            parent=card,
            width=control_width,
            title=f"{axis} σ（采样点）",
            value=DEFAULT_GAUSSIAN_PARAMS[parameter_key],
            minimum=0.0,
            maximum=20.0,
            single_step=0.1,
        )
        sigma_box.setToolTip(
            f"沿 {axis} 轴的高斯平滑标准差，单位为采样点；0 表示不平滑该轴。"
        )
        sigma_boxes.append(sigma_box)
    finish_parameter_group(group, card, sigma_boxes)
    return GaussianControlGroup(group, *sigma_boxes)


def create_median_control_group(parent, group_width, control_width):
    group, card = create_parameter_group(parent, MEDIAN_METHOD_NAME, group_width)
    size_boxes = []
    for axis, parameter_key in (("Kx", "size_kx"), ("Ky", "size_ky"), ("E", "size_e")):
        size_box = create_spin_box(
            parent=card,
            width=control_width,
            title=f"{axis} 窗口（采样点）",
            value=DEFAULT_MEDIAN_PARAMS[parameter_key],
            minimum=1,
            maximum=31,
            single_step=2,
        )
        size_box.setToolTip(
            f"沿 {axis} 轴的中值滤波窗口长度，单位为采样点；必须为奇数，1 表示不平滑该轴。"
        )
        size_boxes.append(size_box)
    finish_parameter_group(group, card, size_boxes)
    return MedianControlGroup(group, *size_boxes)


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
    window_box.setToolTip("SG 拟合窗口长度，单位为采样点；必须为不小于 3 的奇数。")
    polyorder_box.setToolTip("SG 局部拟合的多项式阶数（无量纲），必须小于窗口长度。")
    axis_combo.setToolTip("选择执行 Savitzky-Golay 平滑的采样轴。")
    finish_parameter_group(group, card, (window_box, polyorder_box, axis_combo))
    return SavgolControlGroup(group, window_box, polyorder_box, axis_combo)


def create_wavelet_control_group(parent, group_width, control_width):
    group, card = create_parameter_group(parent, WAVELET_METHOD_NAME, group_width)
    wavelet_combo = create_combo_box(
        parent=card,
        width=control_width,
        title="小波基",
        items=WAVELET_OPTIONS,
        current_text=DEFAULT_WAVELET_PARAMS["wavelet"],
    )
    level_box = create_spin_box(
        parent=card,
        width=control_width,
        title="分解层数",
        value=DEFAULT_WAVELET_PARAMS["level"],
        minimum=1,
        maximum=6,
        single_step=1,
    )
    threshold_rule_combo = create_combo_box(
        parent=card,
        width=control_width,
        title="阈值规则",
        items=THRESHOLD_RULE_OPTIONS,
        current_text=DEFAULT_WAVELET_PARAMS["threshold_rule"],
    )
    threshold_mode_combo = create_combo_box(
        parent=card,
        width=control_width,
        title="阈值模式",
        items=THRESHOLD_MODE_OPTIONS,
        current_text=DEFAULT_WAVELET_PARAMS["threshold_mode"],
    )
    strength_box = create_double_spin_box(
        parent=card,
        width=control_width,
        title="阈值强度（无量纲）",
        value=DEFAULT_WAVELET_PARAMS["strength"],
        minimum=0.0,
        maximum=10.0,
        single_step=0.1,
    )
    noise_sigma_box = create_double_spin_box(
        parent=card,
        width=control_width,
        title="噪声 σ（0=自动）",
        value=DEFAULT_WAVELET_PARAMS["noise_sigma"],
        minimum=0.0,
        maximum=1.0e9,
        single_step=0.01,
    )
    wavelet_combo.setToolTip("选择小波基；名称对应 PyWavelets 小波族。")
    level_box.setToolTip("小波分解层数（无量纲）；实际可用层数由引擎按数据形状校验。")
    threshold_rule_combo.setToolTip("细节子带阈值规则：通用阈值、SURE 或 Bayes。")
    threshold_mode_combo.setToolTip("细节系数的软阈值或硬阈值处理方式。")
    strength_box.setToolTip("阈值缩放倍率（无量纲）；0 表示不收缩细节系数。")
    noise_sigma_box.setToolTip(
        "噪声标准差，与数据强度同单位；0 表示从最细细节子带的 MAD 自动估计，正值表示手动指定。"
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
            noise_sigma_box,
        ),
    )
    return WaveletControlGroup(
        group,
        wavelet_combo,
        level_box,
        threshold_rule_combo,
        threshold_mode_combo,
        strength_box,
        noise_sigma_box,
    )


def create_nlmeans_control_group(parent, group_width, control_width):
    group, card = create_parameter_group(parent, NLM_METHOD_NAME, group_width)
    patch_size_box = create_spin_box(
        parent=card,
        width=control_width,
        title="相似块边长（采样点）",
        value=DEFAULT_NLMEANS_PARAMS["patch_size"],
        minimum=1,
        maximum=15,
        single_step=2,
    )
    patch_distance_box = create_spin_box(
        parent=card,
        width=control_width,
        title="搜索半径（采样点）",
        value=DEFAULT_NLMEANS_PARAMS["patch_distance"],
        minimum=1,
        maximum=20,
        single_step=1,
    )
    h_scale_box = create_double_spin_box(
        parent=card,
        width=control_width,
        title="滤波倍率（无量纲）",
        value=DEFAULT_NLMEANS_PARAMS["h_scale"],
        minimum=0.01,
        maximum=100.0,
        single_step=0.05,
    )
    noise_sigma_box = create_double_spin_box(
        parent=card,
        width=control_width,
        title="噪声 σ（0=自动）",
        value=DEFAULT_NLMEANS_PARAMS["noise_sigma"],
        minimum=0.0,
        maximum=1.0e9,
        single_step=0.01,
    )
    patch_size_box.setToolTip("NLM 相似块边长，单位为采样点；必须为正奇数。")
    patch_distance_box.setToolTip("NLM 相似块搜索半径，单位为采样点；必须为正整数。")
    h_scale_box.setToolTip("NLM 滤波强度倍率（无量纲）；实际 h 为该倍率乘以噪声标准差。")
    noise_sigma_box.setToolTip(
        "噪声标准差，与数据强度同单位；0 表示自动估计，正值表示手动指定。"
    )
    finish_parameter_group(
        group,
        card,
        (patch_size_box, patch_distance_box, h_scale_box, noise_sigma_box),
    )
    return NLMeansControlGroup(
        group,
        patch_size_box,
        patch_distance_box,
        h_scale_box,
        noise_sigma_box,
    )


def create_low_rank_control_group(parent, group_width, control_width):
    group, card = create_parameter_group(parent, LOW_RANK_METHOD_NAME, group_width)
    rank_box = create_spin_box(
        parent=card,
        width=control_width,
        title="保留秩（无量纲）",
        value=DEFAULT_LOW_RANK_PARAMS["rank"],
        minimum=1,
        maximum=9999,
        single_step=1,
    )
    rank_box.setToolTip(
        "每个时间帧独立保留的矩阵秩（无量纲）；有效上限由引擎按数据形状校验，不跨时间帧重构。"
    )
    finish_parameter_group(group, card, (rank_box,))
    return LowRankControlGroup(group, rank_box)
