from PyQt5.QtCore import Qt, QSignalBlocker
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy, QDoubleSpinBox
from PyQt5.QtGui import QColor
from siui.components.widgets import SiScrollArea, SiLabel, SiPushButton
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.slider_ import SiSlider
from siui.components.combobox_ import SiCapsuleComboBox
from siui.core import SiColor

from control_layout_utils import (
    align_scroll_content,
    apply_label_color,
    bounded_width,
    centered_widget_row,
    combo_index_for_text,
    scroll_content_width,
    sync_slider_visual,
)


class DataProcessPage(QWidget):
    PAGE_MARGIN = 13
    SECTION_MARGIN = 10
    SECTION_SPACING = 14
    GROUP_MARGINS = (12, 45, 12, 16)
    GROUP_SPACING = 10
    SLIDER_BLOCK_WIDTH = 310
    SLIDER_GROUP_WIDTH = 360
    AXIS_VALUE_BOX_WIDTH = 86
    AXIS_VALUE_BOX_SPACING = 8
    CONTROL_ROW_WIDTH = 290
    COMBO_WIDTH = 190
    BUTTON_WIDTH = 92
    MIN_GROUP_WIDTH = 340
    MAX_GROUP_WIDTH = 470
    MIN_CONTENT_WIDTH = MIN_GROUP_WIDTH + SECTION_MARGIN * 2
    MAX_CONTENT_WIDTH = MAX_GROUP_WIDTH + SECTION_MARGIN * 2
    MIN_SLIDER_BLOCK_WIDTH = 290
    MAX_SLIDER_BLOCK_WIDTH = 420
    MIN_CONTROL_ROW_WIDTH = 260
    MAX_CONTROL_ROW_WIDTH = 360
    MIN_COMBO_WIDTH = 180
    MAX_COMBO_WIDTH = 230
    MIN_BUTTON_WIDTH = 92
    MAX_BUTTON_WIDTH = 112
    COMBO_TEXT_ALIASES = {"切片态密度": "切片内强度积分"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.locked_half_width = 0
        self._is_updating = False
        self._adaptive_groups = []
        self._adaptive_sliders = []
        self._adaptive_axis_sliders = []
        self._adaptive_slider_blocks = []
        self._adaptive_axis_value_boxes = []
        self._adaptive_row_controls = []
        self._adaptive_combo_controls = []
        self._adaptive_buttons = []
        self.init_ui()

    def _create_red_btn(self, text):
        btn = SiPushButton(self)
        btn.setFixedHeight(28)
        btn.setFixedWidth(self.BUTTON_WIDTH)
        self._adaptive_buttons.append(btn)
        btn.attachment().setText(text)
        btn.colorGroup().assign(SiColor.BUTTON_PANEL, "#E81123")
        btn.colorGroup().assign(SiColor.TEXT_B, "#FFFFFF")
        btn.reloadStyleSheet()
        return btn

    def _create_pink_slider(self):
        slider = SiSlider(self)
        slider.setFixedHeight(32)
        slider.setFixedWidth(self.SLIDER_BLOCK_WIDTH)
        self._adaptive_sliders.append(slider)
        slider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        slider.style_data.main_color = QColor("#FF69B4")
        slider.style_data.background_color = QColor(255, 105, 180, 64)
        slider.style_data.handle_color = QColor("#FFFFFF")
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
        spin_box.setStyleSheet(
            "QDoubleSpinBox {"
            "background-color: rgba(255, 255, 255, 24);"
            "color: white;"
            "border: 1px solid rgba(255, 255, 255, 54);"
            "border-radius: 6px;"
            "padding-left: 4px;"
            "padding-right: 4px;"
            "}"
            "QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {"
            "width: 0px;"
            "border: none;"
            "}"
        )
        self._adaptive_axis_value_boxes.append(spin_box)
        return spin_box

    def _create_labeled_slider_block(self, text, slider, value_box=None):
        container = QWidget(self)
        container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        container.setFixedWidth(self.SLIDER_BLOCK_WIDTH)
        self._adaptive_slider_blocks.append(container)
        block = QVBoxLayout(container)
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(6)
        label = SiLabel(text)
        label.setStyleSheet("color: white; font-weight: bold;")
        block.addWidget(label)
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

    def init_ui(self):
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

        grp_t = SiTitledWidgetGroup(self)
        grp_t.setFixedWidth(self.SLIDER_GROUP_WIDTH)
        self._adaptive_groups.append(grp_t)
        grp_t.addTitle("对时间轴积分")
        v_t = QVBoxLayout(grp_t)
        v_t.setContentsMargins(*self.GROUP_MARGINS)
        v_t.setSpacing(self.GROUP_SPACING)

        self.s_t_up = self._create_pink_slider()
        self.s_t_low = self._create_pink_slider()

        self.s_t_low.valueChanged.connect(self._on_t_low_changed)
        self.s_t_up.valueChanged.connect(self._on_t_up_changed)

        self.btn_t_apply = self._create_red_btn("应用")
        self._add_centered_slider_block(v_t, "积分上限", self.s_t_up)
        self._add_centered_slider_block(v_t, "积分下限", self.s_t_low)
        v_t.addLayout(centered_widget_row(self.btn_t_apply, self.BUTTON_WIDTH))
        apply_label_color(grp_t, "#FFFFFF")
        self.vbox.addLayout(centered_widget_row(grp_t, self.SLIDER_GROUP_WIDTH))

        grp_ax = SiTitledWidgetGroup(self)
        grp_ax.setFixedWidth(self.SLIDER_GROUP_WIDTH)
        self._adaptive_groups.append(grp_ax)
        grp_ax.addTitle("对坐标轴积分")
        v_ax = QVBoxLayout(grp_ax)
        v_ax.setContentsMargins(*self.GROUP_MARGINS)
        v_ax.setSpacing(self.GROUP_SPACING)

        self.combo_ax = SiCapsuleComboBox(self)
        self.combo_ax.setTitle("选择轴向")
        self.combo_ax.setFixedHeight(30)
        self.combo_ax.setFixedWidth(self.COMBO_WIDTH)
        self._adaptive_combo_controls.append(self.combo_ax)
        self.combo_ax.setEditable(False)
        self.combo_ax.addItems(["X轴", "Y轴", "Z轴"])

        self.s_ax_up = self._create_pink_slider()
        self._adaptive_axis_sliders.append(self.s_ax_up)
        self.input_ax_up = self._create_axis_value_box()

        self.s_ax_low = self._create_pink_slider()
        self._adaptive_axis_sliders.append(self.s_ax_low)
        self.input_ax_low = self._create_axis_value_box()

        self.s_ax_mid = self._create_pink_slider()
        self._adaptive_axis_sliders.append(self.s_ax_mid)
        self.input_ax_mid = self._create_axis_value_box()

        self.s_ax_low.valueChanged.connect(self._on_axe_low_changed)
        self.s_ax_up.valueChanged.connect(self._on_axe_up_changed)
        self.s_ax_mid.valueChanged.connect(self._on_axe_mid_changed)

        self.btn_ax_apply = self._create_red_btn("应用")

        v_ax.addLayout(centered_widget_row(self.combo_ax, self.COMBO_WIDTH))
        self._add_centered_slider_block(v_ax, "积分上限", self.s_ax_up, self.input_ax_up)
        self._add_centered_slider_block(v_ax, "积分下限", self.s_ax_low, self.input_ax_low)
        self._add_centered_slider_block(v_ax, "中心位置", self.s_ax_mid, self.input_ax_mid)
        v_ax.addLayout(centered_widget_row(self.btn_ax_apply, self.BUTTON_WIDTH))
        apply_label_color(grp_ax, "#FFFFFF")
        self.vbox.addLayout(centered_widget_row(grp_ax, self.SLIDER_GROUP_WIDTH))

        grp_other = SiTitledWidgetGroup(self)
        grp_other.setFixedWidth(self.SLIDER_GROUP_WIDTH)
        self._adaptive_groups.append(grp_other)
        grp_other.addTitle("其他积分")
        v_other = QVBoxLayout(grp_other)
        v_other.setContentsMargins(*self.GROUP_MARGINS)
        v_other.setSpacing(self.GROUP_SPACING)

        self.combo_other = SiCapsuleComboBox(self)
        self.combo_other.setTitle("\u79ef\u5206\u7c7b\u578b")
        self.combo_other.setFixedHeight(30)
        self.combo_other.setFixedWidth(self.COMBO_WIDTH)
        self._adaptive_row_controls.append(self.combo_other)
        self.combo_other.setEditable(False)
        self.combo_other.addItems([
            "\u5207\u7247\u5185\u5f3a\u5ea6\u79ef\u5206",
            "\u80fd\u7ea7\u6001\u5bc6\u5ea6",
            "EDC\u7011\u5e03\u56fe",
            "\u5355\u6761 EDC \u66f2\u7ebf",
            "\u4e8c\u9636\u5bfc",
        ])
        self.btn_other_apply = self._create_red_btn("应用")
        self.btn_other_save = self._create_red_btn("保存")

        v_other.addLayout(centered_widget_row(self.combo_other, self.COMBO_WIDTH))
        v_other.addLayout(centered_widget_row(self.btn_other_apply, self.BUTTON_WIDTH))
        v_other.addLayout(centered_widget_row(self.btn_other_save, self.BUTTON_WIDTH))

        apply_label_color(grp_other, "#FFFFFF")
        self.vbox.addLayout(centered_widget_row(grp_other, self.SLIDER_GROUP_WIDTH))

        self.vbox.addStretch()
        self.container.adjustSize()
        self.scroll.setAttachment(self.container)
        layout.addWidget(self.scroll)
        self._apply_adaptive_layout()

    def _apply_adaptive_layout(self):
        if not hasattr(self, "scroll"):
            return

        content_width = scroll_content_width(
            self.scroll,
            self.MIN_CONTENT_WIDTH,
            self.MAX_CONTENT_WIDTH,
        )
        group_width = max(self.MIN_GROUP_WIDTH, content_width - self.SECTION_MARGIN * 2)
        slider_width = bounded_width(
            group_width - 50,
            self.MIN_SLIDER_BLOCK_WIDTH,
            self.MAX_SLIDER_BLOCK_WIDTH,
        )
        axis_slider_width = max(
            180,
            slider_width - self.AXIS_VALUE_BOX_WIDTH - self.AXIS_VALUE_BOX_SPACING,
        )
        row_width = bounded_width(
            group_width - 70,
            self.MIN_CONTROL_ROW_WIDTH,
            self.MAX_CONTROL_ROW_WIDTH,
        )
        combo_width = bounded_width(
            row_width - self.BUTTON_WIDTH - 12,
            self.MIN_COMBO_WIDTH,
            self.MAX_COMBO_WIDTH,
        )
        button_width = bounded_width(
            group_width * 0.25,
            self.MIN_BUTTON_WIDTH,
            self.MAX_BUTTON_WIDTH,
        )

        self.container.setFixedWidth(content_width)
        for group in self._adaptive_groups:
            group.setFixedWidth(group_width)
        axis_sliders = set(self._adaptive_axis_sliders)
        for slider in self._adaptive_sliders:
            if slider not in axis_sliders:
                slider.setFixedWidth(slider_width)
        for slider in self._adaptive_axis_sliders:
            slider.setFixedWidth(axis_slider_width)
        for block in self._adaptive_slider_blocks:
            block.setFixedWidth(slider_width)
        for value_box in self._adaptive_axis_value_boxes:
            value_box.setFixedWidth(self.AXIS_VALUE_BOX_WIDTH)
        for control in self._adaptive_row_controls:
            control.setFixedWidth(row_width)
        for control in self._adaptive_combo_controls:
            control.setFixedWidth(combo_width)
        for button in self._adaptive_buttons:
            button.setFixedWidth(button_width)

        self.container.adjustSize()
        align_scroll_content(self.scroll, self.container)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_adaptive_layout()

    def _on_t_low_changed(self, value):
        if value > self.s_t_up.value():
            self.s_t_up.setValue(value)

    def _on_t_up_changed(self, value):
        if value < self.s_t_low.value():
            self.s_t_low.setValue(value)

    def _on_axe_low_changed(self, value):
        if self._is_updating:
            return
        mid = int(self.s_ax_mid.value())
        max_limit = int(self.s_ax_up.maximum())

        if int(value) >= mid:
            self._is_updating = True
            self.s_ax_low.setValue(mid)
            if self.s_ax_up.value() < mid:
                self.s_ax_up.setValue(mid)
            self._is_updating = False
            self.locked_half_width = 0
            return

        target_up = 2 * mid - int(value)

        if target_up > max_limit:
            clamped_low = max(0, 2 * mid - max_limit)
            self._is_updating = True
            self.s_ax_low.setValue(clamped_low)
            self._is_updating = False
            self.locked_half_width = max_limit - mid
            return

        self._is_updating = True
        self.s_ax_up.setValue(target_up)
        self._is_updating = False
        self.locked_half_width = target_up - mid

    def _on_axe_up_changed(self, value):
        if self._is_updating:
            return
        mid = int(self.s_ax_mid.value())
        max_limit = int(self.s_ax_up.maximum())

        if int(value) <= mid:
            self._is_updating = True
            self.s_ax_up.setValue(mid)
            if self.s_ax_low.value() > mid:
                self.s_ax_low.setValue(mid)
            self._is_updating = False
            self.locked_half_width = 0
            return

        target_low = 2 * mid - int(value)

        if target_low < 0:
            clamped_up = min(max_limit, 2 * mid)
            self._is_updating = True
            self.s_ax_up.setValue(clamped_up)
            self._is_updating = False
            self.locked_half_width = mid
            return

        self._is_updating = True
        self.s_ax_low.setValue(target_low)
        self._is_updating = False
        self.locked_half_width = int(value) - mid

    def _on_axe_mid_changed(self, new_mid):
        if self._is_updating:
            return

        if self.locked_half_width == 0:
            self.locked_half_width = (self.s_ax_up.value() - self.s_ax_low.value()) // 2

        target_low = new_mid - self.locked_half_width
        target_up = new_mid + self.locked_half_width
        max_limit = self.s_ax_up.maximum()

        actual_low = max(0, target_low)
        actual_up = min(max_limit, target_up)

        self._is_updating = True
        self.s_ax_low.setValue(actual_low)
        self.s_ax_up.setValue(actual_up)
        self._is_updating = False
        self.locked_half_width = min(new_mid - actual_low, actual_up - new_mid)

    def export_state(self):
        return {
            "s_t_low": {
                "minimum": int(self.s_t_low.minimum()),
                "maximum": int(self.s_t_low.maximum()),
                "value": int(self.s_t_low.value()),
            },
            "s_t_up": {
                "minimum": int(self.s_t_up.minimum()),
                "maximum": int(self.s_t_up.maximum()),
                "value": int(self.s_t_up.value()),
            },
            "combo_ax": {
                "index": int(self.combo_ax.currentIndex()),
                "text": self.combo_ax.currentText(),
            },
            "s_ax_low": {
                "minimum": int(self.s_ax_low.minimum()),
                "maximum": int(self.s_ax_low.maximum()),
                "value": int(self.s_ax_low.value()),
            },
            "input_ax_low": {
                "minimum": float(self.input_ax_low.minimum()),
                "maximum": float(self.input_ax_low.maximum()),
                "value": float(self.input_ax_low.value()),
            },
            "s_ax_up": {
                "minimum": int(self.s_ax_up.minimum()),
                "maximum": int(self.s_ax_up.maximum()),
                "value": int(self.s_ax_up.value()),
            },
            "input_ax_up": {
                "minimum": float(self.input_ax_up.minimum()),
                "maximum": float(self.input_ax_up.maximum()),
                "value": float(self.input_ax_up.value()),
            },
            "s_ax_mid": {
                "minimum": int(self.s_ax_mid.minimum()),
                "maximum": int(self.s_ax_mid.maximum()),
                "value": int(self.s_ax_mid.value()),
            },
            "input_ax_mid": {
                "minimum": float(self.input_ax_mid.minimum()),
                "maximum": float(self.input_ax_mid.maximum()),
                "value": float(self.input_ax_mid.value()),
            },
            "locked_half_width": int(self.locked_half_width),
            "combo_other": {
                "index": int(self.combo_other.currentIndex()),
                "text": self.combo_other.currentText(),
            },
        }

    def restore_state(self, state, *, block_signals=True):
        state = state or {}
        widgets = [
            self.s_t_low,
            self.s_t_up,
            self.combo_ax,
            self.s_ax_low,
            self.s_ax_up,
            self.s_ax_mid,
            self.input_ax_low,
            self.input_ax_up,
            self.input_ax_mid,
            self.combo_other,
        ]
        blockers = [QSignalBlocker(widget) for widget in widgets] if block_signals else []
        previous_updating = self._is_updating
        self._is_updating = True

        try:
            for slider_name, slider in (("s_t_low", self.s_t_low), ("s_t_up", self.s_t_up)):
                slider_state = state.get(slider_name) or {}
                minimum = slider_state.get("minimum")
                maximum = slider_state.get("maximum")
                if minimum is not None and maximum is not None:
                    slider.setRange(int(minimum), int(maximum))
                if "value" in slider_state:
                    value = int(slider_state["value"])
                    value = max(int(slider.minimum()), min(int(slider.maximum()), value))
                    slider.setValue(value)
                sync_slider_visual(slider)

            combo_ax_state = state.get("combo_ax") or {}
            combo_ax_index = combo_ax_state.get("index")
            if combo_ax_index is None and "text" in combo_ax_state:
                combo_ax_index = combo_index_for_text(
                    self.combo_ax,
                    combo_ax_state["text"],
                    aliases=self.COMBO_TEXT_ALIASES,
                )
            if combo_ax_index is not None and 0 <= int(combo_ax_index) < self.combo_ax.count():
                self.combo_ax.setCurrentIndex(int(combo_ax_index))

            axis_controls = (
                ("s_ax_low", "input_ax_low", self.s_ax_low, self.input_ax_low),
                ("s_ax_up", "input_ax_up", self.s_ax_up, self.input_ax_up),
                ("s_ax_mid", "input_ax_mid", self.s_ax_mid, self.input_ax_mid),
            )
            for slider_name, input_name, slider, value_box in axis_controls:
                slider_state = state.get(slider_name) or {}
                input_state = state.get(input_name) or {}
                slider_minimum = slider_state.get("minimum")
                slider_maximum = slider_state.get("maximum")
                if slider_minimum is not None and slider_maximum is not None:
                    slider.setRange(int(slider_minimum), int(slider_maximum))

                if "value" in slider_state:
                    value = int(slider_state["value"])
                    value = max(int(slider.minimum()), min(int(slider.maximum()), value))
                    slider.setValue(value)

                if "value" in input_state:
                    box_value = float(input_state["value"])
                    box_value = max(float(value_box.minimum()), min(float(value_box.maximum()), box_value))
                    value_box.setValue(box_value)
                elif "value" in slider_state:
                    value_box.setValue(float(slider.value()))
                sync_slider_visual(slider)

            combo_other_state = state.get("combo_other") or {}
            combo_other_index = combo_other_state.get("index")
            if combo_other_index is None and "text" in combo_other_state:
                combo_other_index = combo_index_for_text(
                    self.combo_other,
                    combo_other_state["text"],
                    aliases=self.COMBO_TEXT_ALIASES,
                )
            if combo_other_index is not None and 0 <= int(combo_other_index) < self.combo_other.count():
                self.combo_other.setCurrentIndex(int(combo_other_index))

            self.locked_half_width = int(
                state.get(
                    "locked_half_width",
                    abs(int(self.s_ax_up.value()) - int(self.s_ax_low.value())) // 2,
                )
            )
        finally:
            self._is_updating = previous_updating
            del blockers
