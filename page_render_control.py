from PyQt5.QtCore import QSignalBlocker
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy
from PyQt5.QtGui import QColor
from PyQt5.QtCore import Qt
from siui.components.widgets import SiScrollArea, SiLabel, SiPushButton
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.slider_ import SiSlider
from siui.components.combobox_ import SiCapsuleComboBox
from siui.core import SiColor


class RenderControlPage(QWidget):
    PAGE_MARGIN = 13
    SECTION_MARGIN = 15
    SECTION_SPACING = 20
    GROUP_MARGINS = (15, 55, 15, 20)
    GROUP_SPACING = 12
    SLIDER_BLOCK_WIDTH = 400
    SLIDER_GROUP_WIDTH = 450
    CONTROL_ROW_WIDTH = 340
    COMBO_WIDTH = 210
    BUTTON_WIDTH = 100
    CMAP_OPTIONS = [
        "magma", "inferno", "plasma", "viridis", "cividis", "turbo",
        "afmhot", "hot", "gist_heat", "coolwarm", "RdBu_r", "seismic",
        "Spectral", "jet", "rainbow", "nipy_spectral", "cubehelix",
        "twilight", "twilight_shifted", "Greys", "gray", "bone", "pink",
        "spring", "summer", "autumn", "winter", "cool", "hsv", "terrain",
        "ocean", "gnuplot", "gnuplot2",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def _apply_style(self, grp):
        for child in grp.findChildren(SiLabel):
            try:
                child.colorGroup().assign(SiColor.TEXT_A, "#FFFFFF")
                child.reloadStyleSheet()
            except:
                pass

    def _create_red_btn(self, text):
        """统一小尺寸红色按钮"""
        btn = SiPushButton(self)
        btn.setFixedHeight(28)
        btn.setFixedWidth(self.BUTTON_WIDTH)
        btn.attachment().setText(text)
        btn.colorGroup().assign(SiColor.BUTTON_PANEL, "#E81123")
        btn.colorGroup().assign(SiColor.TEXT_B, "#FFFFFF")
        btn.reloadStyleSheet()
        return btn

    def _create_pink_slider(self):
        s = SiSlider(self)
        s.setFixedHeight(32)
        s.setFixedWidth(self.SLIDER_BLOCK_WIDTH)
        s.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        s.style_data.main_color = QColor("#FF69B4")
        s.style_data.background_color = QColor(255, 105, 180, 64)
        s.style_data.handle_color = QColor("#FFFFFF")
        return s

    def _center_widget(self, widget, max_width=None):
        if max_width is not None:
            widget.setMaximumWidth(max_width)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(widget)
        row.addStretch()
        return row

    def _create_labeled_slider_block(self, text, slider):
        container = QWidget(self)
        container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        container.setFixedWidth(self.SLIDER_BLOCK_WIDTH)
        block = QVBoxLayout(container)
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(6)
        label = SiLabel(text)
        label.setStyleSheet("color: white; font-weight: bold;")
        block.addWidget(label)
        block.addWidget(slider)
        return container

    def _add_centered_slider_block(self, layout, text, slider):
        layout.addLayout(self._center_widget(self._create_labeled_slider_block(text, slider)))

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

        # 色带选择
        grp_cmap = SiTitledWidgetGroup(self)
        grp_cmap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        grp_cmap.setFixedWidth(self.SLIDER_GROUP_WIDTH)
        grp_cmap.addTitle("色带选择")
        v_cmap = QVBoxLayout(grp_cmap)
        v_cmap.setContentsMargins(*self.GROUP_MARGINS)
        v_cmap.setSpacing(self.GROUP_SPACING)

        h_cmap = QHBoxLayout()
        self.combo_cmap = SiCapsuleComboBox(self)
        self.combo_cmap.setTitle("渲染色带")
        self.combo_cmap.setFixedHeight(30)
        self.combo_cmap.setFixedWidth(self.COMBO_WIDTH)
        self.combo_cmap.setEditable(False)
        self.combo_cmap.addItems(self.CMAP_OPTIONS)
        self.btn_apply_cmap = self._create_red_btn("确定")

        h_cmap.addStretch()
        h_cmap.addWidget(self.combo_cmap)
        h_cmap.addWidget(self.btn_apply_cmap)
        h_cmap.addStretch()
        v_cmap.addLayout(h_cmap)

        self._apply_style(grp_cmap)
        self.vbox.addLayout(self._center_widget(grp_cmap, self.SLIDER_GROUP_WIDTH))

        #  色阶调整
        grp_exp = SiTitledWidgetGroup(self)
        grp_exp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        grp_exp.setFixedWidth(self.SLIDER_GROUP_WIDTH)
        grp_exp.addTitle("色阶调整")
        v_exp = QVBoxLayout(grp_exp)
        v_exp.setContentsMargins(*self.GROUP_MARGINS)
        v_exp.setSpacing(self.GROUP_SPACING)

        # 上限色
        h_up = QHBoxLayout()
        self.s_up = self._create_pink_slider()
        self.s_up.setRange(0, 100)
        self.s_up.setValue(100)  # 默认不截断高光
        lbl_up = SiLabel("白场")
        lbl_up.setStyleSheet("color: white; font-weight: bold;")
        lbl_up.setFixedWidth(50)
        h_up.addWidget(lbl_up)
        h_up.addWidget(self.s_up)

        # 中间调
        h_gamma = QHBoxLayout()
        self.s_gamma = self._create_pink_slider()
        self.s_gamma.setRange(0, 100)
        self.s_gamma.setValue(50)  # 默认线性映射 (Gamma 1.0)
        lbl_gamma = SiLabel("灰场")
        lbl_gamma.setStyleSheet("color: white; font-weight: bold;")
        lbl_gamma.setFixedWidth(50)
        h_gamma.addWidget(lbl_gamma)
        h_gamma.addWidget(self.s_gamma)

        # 下限色
        h_low = QHBoxLayout()
        self.s_low = self._create_pink_slider()
        self.s_low.setRange(0, 100)
        self.s_low.setValue(0)    # 默认不截断低光
        lbl_low = SiLabel("黑场")
        lbl_low.setStyleSheet("color: white; font-weight: bold;")
        lbl_low.setFixedWidth(50)
        h_low.addWidget(lbl_low)
        h_low.addWidget(self.s_low)

        v_exp.addLayout(h_up)
        v_exp.addLayout(h_gamma)
        v_exp.addLayout(h_low)
        for _ in range(3):
            item = v_exp.takeAt(v_exp.count() - 1)
            row = item.layout() if item is not None else None
            if row is None:
                continue
            while row.count():
                child = row.takeAt(0)
                widget = child.widget()
                if widget is not None:
                    widget.setParent(None)
        self._add_centered_slider_block(v_exp, lbl_up.text(), self.s_up)
        self._add_centered_slider_block(v_exp, lbl_gamma.text(), self.s_gamma)
        self._add_centered_slider_block(v_exp, lbl_low.text(), self.s_low)

        self.combo_map = SiCapsuleComboBox(self)
        self.combo_map.setTitle("强度映射方式")
        self.combo_map.setFixedHeight(30)
        self.combo_map.setFixedWidth(self.CONTROL_ROW_WIDTH)
        self.combo_map.setEditable(False)
        self.combo_map.addItems(["线性", "对数", "幂函数", "sigmoid"])

        self.btn_apply_map = self._create_red_btn("应用设置")

        v_exp.addLayout(self._center_widget(self.combo_map, self.CONTROL_ROW_WIDTH))
        v_exp.addLayout(self._center_widget(self.btn_apply_map, self.BUTTON_WIDTH))

        self._apply_style(grp_exp)
        self.vbox.addLayout(self._center_widget(grp_exp, self.SLIDER_GROUP_WIDTH))

        # 去噪处理
        grp_noise = SiTitledWidgetGroup(self)
        grp_noise.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        grp_noise.setFixedWidth(self.SLIDER_GROUP_WIDTH)
        grp_noise.addTitle("去噪处理")
        v_noise = QVBoxLayout(grp_noise)
        v_noise.setContentsMargins(*self.GROUP_MARGINS)
        v_noise.setSpacing(self.GROUP_SPACING)
        lbl_noise = SiLabel("自上向下依次生效：")
        lbl_noise.setStyleSheet("color: white; font-weight: bold;")
        v_noise.addWidget(lbl_noise)

        self.combo_n1 = SiCapsuleComboBox(self)
        self.combo_n1.setTitle("一级去噪")
        self.combo_n1.setFixedHeight(30)
        self.combo_n1.setFixedWidth(self.CONTROL_ROW_WIDTH)
        self.combo_n1.setEditable(False)
        self.combo_n1.addItems(["None", "频域平滑", "滑动平均", "Savitzky-Golay滤波", "小波去噪", "卡尔曼滤波", "贝叶斯去噪"])

        self.combo_n2 = SiCapsuleComboBox(self)
        self.combo_n2.setTitle("二级去噪")
        self.combo_n2.setFixedHeight(30)
        self.combo_n2.setFixedWidth(self.CONTROL_ROW_WIDTH)
        self.combo_n2.setEditable(False)
        self.combo_n2.addItems(["None", "频域平滑", "滑动平均", "Savitzky-Golay滤波", "小波去噪", "卡尔曼滤波", "贝叶斯去噪"])

        self.combo_n3 = SiCapsuleComboBox(self)
        self.combo_n3.setTitle("三级去噪")
        self.combo_n3.setFixedHeight(30)
        self.combo_n3.setFixedWidth(self.CONTROL_ROW_WIDTH)
        self.combo_n3.setEditable(False)
        self.combo_n3.addItems(["None", "频域平滑", "滑动平均", "Savitzky-Golay滤波", "小波去噪", "卡尔曼滤波", "贝叶斯去噪"])

        self.btn_apply_noise = self._create_red_btn("应用设置")


        v_noise.addLayout(self._center_widget(self.combo_n1, self.CONTROL_ROW_WIDTH))
        v_noise.addLayout(self._center_widget(self.combo_n2, self.CONTROL_ROW_WIDTH))
        v_noise.addLayout(self._center_widget(self.combo_n3, self.CONTROL_ROW_WIDTH))
        v_noise.addLayout(self._center_widget(self.btn_apply_noise, self.BUTTON_WIDTH))

        self._apply_style(grp_noise)
        self.vbox.addLayout(self._center_widget(grp_noise, self.SLIDER_GROUP_WIDTH))

        self.vbox.addStretch()
        self.scroll.setCenterWidget(self.container)
        layout.addWidget(self.scroll)

    def get_selected_cmap(self):
        return self.combo_cmap.currentText()

    def get_denoise_settings(self):
        """ 获取当前选中的三级去噪配置 """
        return [
            self.combo_n1.currentText(),
            self.combo_n2.currentText(),
            self.combo_n3.currentText()
        ]
    @staticmethod
    def _combo_index_for_text(combo_box, text):
        for index in range(combo_box.count()):
            if combo_box.itemText(index) == text:
                return index
        return -1

    @staticmethod
    def _sync_slider_visual(slider):
        minimum = int(slider.minimum())
        maximum = int(slider.maximum())
        value = int(slider.value())
        if maximum == minimum:
            progress = 0.0
        else:
            progress = (value - minimum) / (maximum - minimum)

        try:
            slider.setProperty(slider.Property.TrackProgress, progress)
        except Exception:
            pass

        progress_ani = getattr(slider, "progress_ani", None)
        if progress_ani is not None:
            try:
                progress_ani.fromProperty()
                progress_ani.setCurrentValue(progress)
                progress_ani.setEndValue(progress)
            except Exception:
                pass

        update_tooltip = getattr(slider, "_updateToolTip", None)
        if callable(update_tooltip):
            try:
                update_tooltip(flash=False)
            except Exception:
                pass

        slider.update()

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
                self._sync_slider_visual(slider)

            for combo_name, combo in (
                ("combo_cmap", self.combo_cmap),
                ("combo_map", self.combo_map),
                ("combo_n1", self.combo_n1),
                ("combo_n2", self.combo_n2),
                ("combo_n3", self.combo_n3),
            ):
                if combo_name not in state:
                    continue
                index = self._combo_index_for_text(combo, state[combo_name])
                if index >= 0:
                    combo.setCurrentIndex(index)
        finally:
            del blockers
