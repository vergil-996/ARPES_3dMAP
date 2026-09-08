from PyQt5.QtCore import QSignalBlocker
from siui.components.combobox_ import SiCapsuleComboBox

import theme
from control_layout_utils import centered_widget_row, combo_index_for_text
from control_page_base import ControlPageBase


class DataProcessPage(ControlPageBase):
    COMBO_TEXT_ALIASES = {"切片态密度": "切片内强度积分"}

    def __init__(self, parent=None):
        self.locked_half_width = 0
        self._is_updating = False
        super().__init__(parent)

    def _create_combo(self, title, items, registry):
        combo = SiCapsuleComboBox(self)
        combo.setTitle(title)
        combo.setFixedHeight(30)
        combo.setFixedWidth(self.MIN_COMBO_WIDTH)
        registry.append(combo)
        combo.setEditable(False)
        combo.addItems(items)
        theme.raise_well_on_card(combo)
        return combo

    def build_body(self):
        grp_t, v_t = self._create_group("对时间轴积分")

        self.s_t_up = self._create_accent_slider()
        self.s_t_low = self._create_accent_slider()

        self.s_t_low.valueChanged.connect(self._on_t_low_changed)
        self.s_t_up.valueChanged.connect(self._on_t_up_changed)

        self.btn_t_apply = self._create_btn("应用", "primary")
        self._add_centered_slider_block(v_t, "积分上限", self.s_t_up)
        self._add_centered_slider_block(v_t, "积分下限", self.s_t_low)
        v_t.addLayout(centered_widget_row(self.btn_t_apply, self.BUTTON_WIDTH))
        self.vbox.addLayout(centered_widget_row(grp_t, self.MIN_GROUP_WIDTH))

        grp_ax, v_ax = self._create_group("对坐标轴积分")

        self.combo_ax = self._create_combo("选择轴向", ["X轴", "Y轴", "Z轴"], self._adaptive_combo_controls)

        self.s_ax_up = self._create_accent_slider(axis=True)
        self.input_ax_up = self._create_axis_value_box()

        self.s_ax_low = self._create_accent_slider(axis=True)
        self.input_ax_low = self._create_axis_value_box()

        self.s_ax_mid = self._create_accent_slider(axis=True)
        self.input_ax_mid = self._create_axis_value_box()

        self.s_ax_low.valueChanged.connect(self._on_axe_low_changed)
        self.s_ax_up.valueChanged.connect(self._on_axe_up_changed)
        self.s_ax_mid.valueChanged.connect(self._on_axe_mid_changed)

        self.btn_ax_apply = self._create_btn("应用", "primary")

        v_ax.addLayout(centered_widget_row(self.combo_ax, self.MIN_COMBO_WIDTH))
        self._add_centered_slider_block(v_ax, "积分上限", self.s_ax_up, self.input_ax_up)
        self._add_centered_slider_block(v_ax, "积分下限", self.s_ax_low, self.input_ax_low)
        self._add_centered_slider_block(v_ax, "中心位置", self.s_ax_mid, self.input_ax_mid)
        v_ax.addLayout(centered_widget_row(self.btn_ax_apply, self.BUTTON_WIDTH))
        self.vbox.addLayout(centered_widget_row(grp_ax, self.MIN_GROUP_WIDTH))

        grp_other, v_other = self._create_group("其他积分")

        self.combo_other = self._create_combo("积分类型", [
            "切片内强度积分",
            "能级态密度",
            "EDC瀑布图",
            "单条 EDC 曲线",
            "二阶导",
        ], self._adaptive_row_controls)
        self.btn_other_apply = self._create_btn("应用", "primary")

        v_other.addLayout(centered_widget_row(self.combo_other, self.MIN_COMBO_WIDTH))
        v_other.addLayout(centered_widget_row(self.btn_other_apply, self.BUTTON_WIDTH))

        self.vbox.addLayout(centered_widget_row(grp_other, self.MIN_GROUP_WIDTH))

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
