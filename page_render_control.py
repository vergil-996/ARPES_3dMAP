from PyQt5.QtCore import QSignalBlocker
from PyQt5.QtWidgets import QHBoxLayout
from siui.components.combobox_ import SiCapsuleComboBox
from siui.components.widgets import SiLabel

import theme
from control_layout_utils import centered_widget_row, combo_index_for_text
from control_page_base import ControlPageBase
from denoise_config import DENOISE_METHODS as CONFIG_DENOISE_METHODS


class RenderControlPage(ControlPageBase):
    CMAP_OPTIONS = [
        "magma", "inferno", "plasma", "viridis", "cividis", "turbo",
        "afmhot", "hot", "gist_heat", "coolwarm", "RdBu_r", "seismic",
        "Spectral", "jet", "rainbow", "nipy_spectral", "cubehelix",
        "twilight", "twilight_shifted", "Greys", "gray", "bone", "pink",
        "spring", "summer", "autumn", "winter", "cool", "hsv", "terrain",
        "ocean", "gnuplot", "gnuplot2",
    ]
    DENOISE_METHODS = CONFIG_DENOISE_METHODS

    def build_body(self):
        # 色带选择
        grp_cmap, v_cmap = self._create_group("色带选择")
        h_cmap = QHBoxLayout()
        self.combo_cmap = SiCapsuleComboBox(self)
        self.combo_cmap.setTitle("渲染色带")
        self.combo_cmap.setFixedHeight(30)
        self.combo_cmap.setFixedWidth(self.MIN_COMBO_WIDTH)
        self._adaptive_combo_controls.append(self.combo_cmap)
        self.combo_cmap.setEditable(False)
        self.combo_cmap.addItems(self.CMAP_OPTIONS)
        theme.raise_well_on_card(self.combo_cmap)
        self.btn_apply_cmap = self._create_btn("确定", "primary")

        h_cmap.addStretch()
        h_cmap.addWidget(self.combo_cmap)
        h_cmap.addWidget(self.btn_apply_cmap)
        h_cmap.addStretch()
        v_cmap.addLayout(h_cmap)

        self.vbox.addLayout(centered_widget_row(grp_cmap, self.MIN_GROUP_WIDTH))

        # 色阶调整
        grp_exp, v_exp = self._create_group("色阶调整")

        self.s_up = self._create_accent_slider()
        self.s_up.setRange(0, 100)
        self.s_up.setValue(100)  # 默认不截断高光

        self.s_gamma = self._create_accent_slider()
        self.s_gamma.setRange(0, 100)
        self.s_gamma.setValue(50)  # 默认线性映射 (Gamma 1.0)

        self.s_low = self._create_accent_slider()
        self.s_low.setRange(0, 100)
        self.s_low.setValue(0)    # 默认不截断低光

        self._add_centered_slider_block(v_exp, "白场", self.s_up)
        self._add_centered_slider_block(v_exp, "灰场", self.s_gamma)
        self._add_centered_slider_block(v_exp, "黑场", self.s_low)

        self.combo_map = self._create_denoise_combo("强度映射方式", ["线性", "对数", "幂函数", "sigmoid"])
        self.btn_apply_map = self._create_btn("应用设置", "primary")

        v_exp.addLayout(centered_widget_row(self.combo_map, self.MIN_CONTROL_ROW_WIDTH))
        v_exp.addLayout(centered_widget_row(self.btn_apply_map, self.BUTTON_WIDTH))

        self.vbox.addLayout(centered_widget_row(grp_exp, self.MIN_GROUP_WIDTH))

        # 去噪处理
        grp_noise, v_noise = self._create_group("去噪处理")
        lbl_noise = SiLabel("自上向下依次生效：")
        lbl_noise.setStyleSheet(theme.field_label_qss())
        v_noise.addWidget(lbl_noise)

        self.combo_n1 = self._create_denoise_combo("一级去噪", self.DENOISE_METHODS)
        self.combo_n2 = self._create_denoise_combo("二级去噪", self.DENOISE_METHODS)
        self.combo_n3 = self._create_denoise_combo("三级去噪", self.DENOISE_METHODS)

        self.btn_apply_noise = self._create_btn("应用设置", "primary")

        v_noise.addLayout(centered_widget_row(self.combo_n1, self.MIN_CONTROL_ROW_WIDTH))
        v_noise.addLayout(centered_widget_row(self.combo_n2, self.MIN_CONTROL_ROW_WIDTH))
        v_noise.addLayout(centered_widget_row(self.combo_n3, self.MIN_CONTROL_ROW_WIDTH))
        v_noise.addLayout(centered_widget_row(self.btn_apply_noise, self.BUTTON_WIDTH))

        self.vbox.addLayout(centered_widget_row(grp_noise, self.MIN_GROUP_WIDTH))

        # 全局计算后端（不随分析结果页保存）
        grp_backend, v_backend = self._create_group("性能与硬件")
        self.combo_backend = self._create_denoise_combo("计算后端", ["Auto", "CPU", "NVIDIA GPU"])
        v_backend.addLayout(centered_widget_row(self.combo_backend, self.MIN_CONTROL_ROW_WIDTH))

        self.vbox.addLayout(centered_widget_row(grp_backend, self.MIN_GROUP_WIDTH))

    def get_selected_cmap(self):
        return self.combo_cmap.currentText()

    def get_denoise_settings(self):
        """ 获取当前选中的三级去噪配置 """
        return [
            self.combo_n1.currentText(),
            self.combo_n2.currentText(),
            self.combo_n3.currentText()
        ]

    def set_backend_mode(self, mode):
        index = combo_index_for_text(self.combo_backend, mode)
        if index >= 0:
            self.combo_backend.setCurrentIndex(index)

    def set_nvidia_backend_available(self, available):
        index = combo_index_for_text(self.combo_backend, "NVIDIA GPU")
        if index < 0:
            return
        item = self.combo_backend.model().item(index)
        if item is not None:
            item.setEnabled(bool(available))
        self.combo_backend.setItemData(
            index,
            None if available else "当前环境未安装或无法使用 CuPy/CUDA，Auto 将使用 CPU。",
            3,
        )

    def export_state(self):
        return {
            "combo_cmap": self.combo_cmap.currentText(),
            "s_low": {
                "minimum": int(self.s_low.minimum()),
                "maximum": int(self.s_low.maximum()),
                "value": int(self.s_low.value()),
            },
            "s_gamma": {
                "minimum": int(self.s_gamma.minimum()),
                "maximum": int(self.s_gamma.maximum()),
                "value": int(self.s_gamma.value()),
            },
            "s_up": {
                "minimum": int(self.s_up.minimum()),
                "maximum": int(self.s_up.maximum()),
                "value": int(self.s_up.value()),
            },
            "combo_map": self.combo_map.currentText(),
            "combo_n1": self.combo_n1.currentText(),
            "combo_n2": self.combo_n2.currentText(),
            "combo_n3": self.combo_n3.currentText(),
        }

    def restore_state(self, state, *, block_signals=True):
        state = state or {}
        widgets = [
            self.combo_cmap,
            self.s_low,
            self.s_gamma,
            self.s_up,
            self.combo_map,
            self.combo_n1,
            self.combo_n2,
            self.combo_n3,
        ]
        blockers = [QSignalBlocker(widget) for widget in widgets] if block_signals else []

        try:
            for slider_name, slider in (("s_low", self.s_low), ("s_gamma", self.s_gamma), ("s_up", self.s_up)):
                slider_state = state.get(slider_name) or {}
                minimum = slider_state.get("minimum")
                maximum = slider_state.get("maximum")
                if minimum is not None and maximum is not None:
                    slider.setRange(int(minimum), int(maximum))
                if "value" in slider_state:
                    value = int(slider_state["value"])
                    value = max(int(slider.minimum()), min(int(slider.maximum()), value))
                    slider.setValue(value)

            for combo_name, combo in (
                ("combo_cmap", self.combo_cmap),
                ("combo_map", self.combo_map),
                ("combo_n1", self.combo_n1),
                ("combo_n2", self.combo_n2),
                ("combo_n3", self.combo_n3),
            ):
                if combo_name not in state:
                    continue
                index = combo_index_for_text(combo, state[combo_name])
                if index >= 0:
                    combo.setCurrentIndex(index)
        finally:
            del blockers
