# -*- coding: utf-8 -*-
"""控制页共享基座 —— 滚动容器脚手架 + 统一自适应布局 + 语义化工厂方法。

三个右侧控制页（图像控制 / 渲染控制 / 处理分析）此前各自维护
``_adaptive_*`` 注册表和一份大同小异的 ``_apply_adaptive_layout``，
现在收敛到这里：

- 子类只需实现 ``build_body()``，把控件加进 ``self.vbox``
- 需要自适应宽度的控件在创建时自动登记（或手动 append 到对应列表）
- 特殊控件（如切片输入框）通过重写 ``_apply_extra_widths`` 钩子处理
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from siui.components.combobox_ import SiCapsuleComboBox
from siui.components.widgets import SiLabel, SiScrollArea

import theme
from control_layout_utils import (
    align_scroll_content,
    bounded_width,
    centered_widget_row,
    scroll_content_width,
)
from ui_controls import ActionButton, SyncedSlider


class CardGroup(QFrame):
    """卡片式分组容器：抬升面 + 细边框 + 圆角，标题行带主色竖条。

    替代 SiTitledWidgetGroup 的飘浮标题——内容全部收进卡片内部，
    分组之间的视觉边界来自卡片本身而不是标题文字。
    """

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("CardGroup")
        self.setStyleSheet(theme.card_qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(10)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        tick = QFrame(self)
        tick.setFixedSize(3, 14)
        tick.setStyleSheet(f"background-color: {theme.ACCENT}; border-radius: 1px;")
        title_label = QLabel(title, self)
        title_label.setStyleSheet(theme.card_title_qss())
        head.addWidget(tick)
        head.addWidget(title_label)
        head.addStretch()
        outer.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 2, 0, 0)
        self.body.setSpacing(10)
        outer.addLayout(self.body)


class ControlPageBase(QWidget):
    PAGE_MARGIN = 0
    SECTION_MARGIN = 12
    SECTION_SPACING = 12
    GROUP_MARGINS = (12, 45, 12, 16)
    GROUP_SPACING = 10
    MIN_GROUP_WIDTH = 340
    MAX_GROUP_WIDTH = 470
    MIN_SLIDER_BLOCK_WIDTH = 290
    MAX_SLIDER_BLOCK_WIDTH = 420
    MIN_CONTROL_ROW_WIDTH = 260
    MAX_CONTROL_ROW_WIDTH = 360
    MIN_COMBO_WIDTH = 180
    MAX_COMBO_WIDTH = 230
    BUTTON_WIDTH = 92
    MIN_BUTTON_WIDTH = 92
    MAX_BUTTON_WIDTH = 112
    AXIS_VALUE_BOX_WIDTH = 86
    AXIS_VALUE_BOX_SPACING = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._adaptive_groups = []
        self._adaptive_sliders = []
        self._adaptive_axis_sliders = []
        self._adaptive_slider_blocks = []
        self._adaptive_axis_value_boxes = []
        self._adaptive_row_controls = []
        self._adaptive_combo_controls = []
        self._adaptive_buttons = []
        self._init_scaffold()

    # ------------------------------------------------------------------
    # 脚手架
    # ------------------------------------------------------------------
    def _init_scaffold(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(self.PAGE_MARGIN, self.PAGE_MARGIN, self.PAGE_MARGIN, self.PAGE_MARGIN)
        layout.setSpacing(0)

        self.scroll = SiScrollArea(self)
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setContentsMargins(
            self.SECTION_MARGIN,
            self.SECTION_MARGIN,
            self.SECTION_MARGIN,
            self.SECTION_MARGIN,
        )
        self.vbox.setSpacing(self.SECTION_SPACING)

        self.build_body()

        self.vbox.addStretch()
        self.container.adjustSize()
        self.scroll.setAttachment(self.container)
        layout.addWidget(self.scroll)
        self._apply_adaptive_layout()

    def build_body(self):
        """子类在此把控件加进 self.vbox。"""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 共享工厂
    # ------------------------------------------------------------------
    def _create_btn(self, text, kind="primary", height=32, width=None):
        """语义化按钮：primary（每组最多一个）/ secondary / danger。"""
        btn = ActionButton(self)
        btn.setFixedHeight(height)
        btn.setFixedWidth(width or self.BUTTON_WIDTH)
        self._adaptive_buttons.append(btn)
        btn.attachment().setText(text)
        theme.style_push_button(btn, kind)
        return btn

    def _create_accent_slider(self, register=True, axis=False):
        slider = SyncedSlider(self)
        slider.setFixedHeight(32)
        slider.setFixedWidth(self.MIN_SLIDER_BLOCK_WIDTH)
        slider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        theme.style_accent_slider(slider)
        if register:
            self._adaptive_sliders.append(slider)
        if axis:
            self._adaptive_axis_sliders.append(slider)
        return slider

    def _create_axis_value_box(self):
        spin_box = QDoubleSpinBox(self)
        spin_box.setFixedSize(self.AXIS_VALUE_BOX_WIDTH, 30)
        spin_box.setDecimals(2)
        spin_box.setKeyboardTracking(False)
        spin_box.setRange(-1e12, 1e12)
        spin_box.setSingleStep(0.01)
        spin_box.setAlignment(Qt.AlignCenter)
        spin_box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        spin_box.setStyleSheet(theme.value_input_qss())
        self._adaptive_axis_value_boxes.append(spin_box)
        return spin_box

    def _create_denoise_combo(self, title, items):
        combo = SiCapsuleComboBox(self)
        combo.setTitle(title)
        combo.setFixedHeight(30)
        combo.setFixedWidth(self.MIN_CONTROL_ROW_WIDTH)
        combo.setEditable(False)
        combo.addItems(items)
        theme.raise_well_on_card(combo)
        self._adaptive_row_controls.append(combo)
        return combo

    def _create_labeled_slider_block(self, text, slider, value_box=None):
        container = QWidget(self)
        container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        container.setFixedWidth(self.MIN_SLIDER_BLOCK_WIDTH)
        self._adaptive_slider_blocks.append(container)
        block = QVBoxLayout(container)
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(6)

        # 标题行：字段名在左；没有专用数值框的滑条在右侧显示当前值
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        label = SiLabel(text)
        label.setStyleSheet(theme.field_label_qss())
        head.addWidget(label)
        head.addStretch()
        if value_box is None:
            value_label = QLabel(str(int(slider.value())), container)
            value_label.setStyleSheet(theme.slider_value_qss())
            slider.valueChanged.connect(
                lambda v, lbl=value_label: lbl.setText(str(int(v)))
            )
            head.addWidget(value_label)
        block.addLayout(head)

        if value_box is None:
            block.addWidget(slider)
        else:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(self.AXIS_VALUE_BOX_SPACING)
            row.addWidget(slider)
            row.addWidget(value_box)
            block.addLayout(row)
        return container

    def _add_centered_slider_block(self, layout, text, slider, value_box=None):
        layout.addLayout(centered_widget_row(self._create_labeled_slider_block(text, slider, value_box)))

    def _create_group(self, title):
        group = CardGroup(title, self)
        group.setFixedWidth(self.MIN_GROUP_WIDTH)
        self._adaptive_groups.append(group)
        return group, group.body

    # ------------------------------------------------------------------
    # 自适应布局
    # ------------------------------------------------------------------
    @property
    def _min_content_width(self):
        return self.MIN_GROUP_WIDTH + self.SECTION_MARGIN * 2

    @property
    def _max_content_width(self):
        return self.MAX_GROUP_WIDTH + self.SECTION_MARGIN * 2

    def _adaptive_widths(self, group_width):
        slider = bounded_width(group_width - 30, self.MIN_SLIDER_BLOCK_WIDTH, self.MAX_SLIDER_BLOCK_WIDTH)
        row = bounded_width(group_width - 30, self.MIN_CONTROL_ROW_WIDTH, self.MAX_CONTROL_ROW_WIDTH)
        return {
            "slider": slider,
            "axis_slider": max(180, slider - self.AXIS_VALUE_BOX_WIDTH - self.AXIS_VALUE_BOX_SPACING),
            "row": row,
            "combo": bounded_width(row - self.MAX_BUTTON_WIDTH - 8, self.MIN_COMBO_WIDTH, self.MAX_COMBO_WIDTH),
            "button": bounded_width(group_width * 0.25, self.MIN_BUTTON_WIDTH, self.MAX_BUTTON_WIDTH),
        }

    def _apply_extra_widths(self, group_width, widths):
        """子类钩子：处理不在通用列表里的控件（如成对输入框）。"""

    def _apply_adaptive_layout(self):
        if not hasattr(self, "scroll"):
            return

        content_width = scroll_content_width(self.scroll, self._min_content_width, self._max_content_width)
        group_width = max(self.MIN_GROUP_WIDTH, content_width - self.SECTION_MARGIN * 2)
        widths = self._adaptive_widths(group_width)

        self.container.setFixedWidth(content_width)
        for group in self._adaptive_groups:
            group.setFixedWidth(group_width)

        axis_sliders = set(self._adaptive_axis_sliders)
        for slider in self._adaptive_sliders:
            if slider not in axis_sliders:
                slider.setFixedWidth(widths["slider"])
        for slider in self._adaptive_axis_sliders:
            slider.setFixedWidth(widths["axis_slider"])
        for block in self._adaptive_slider_blocks:
            block.setFixedWidth(widths["slider"])
        for value_box in self._adaptive_axis_value_boxes:
            value_box.setFixedWidth(self.AXIS_VALUE_BOX_WIDTH)
        for control in self._adaptive_row_controls:
            control.setFixedWidth(widths["row"])
        for control in self._adaptive_combo_controls:
            control.setFixedWidth(widths["combo"])
        for button in self._adaptive_buttons:
            button.setFixedWidth(widths["button"])

        self._apply_extra_widths(group_width, widths)

        self.container.adjustSize()
        align_scroll_content(self.scroll, self.container)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_adaptive_layout()
