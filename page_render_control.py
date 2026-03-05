from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from PyQt5.QtGui import QColor
from PyQt5.QtCore import Qt
from siui.components.widgets import SiScrollArea, SiLabel, SiPushButton
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.slider_ import SiSlider
from siui.components.combobox_ import SiCapsuleComboBox
from siui.core import SiColor


class RenderControlPage(QWidget):
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
        btn.attachment().setText(text)
        btn.colorGroup().assign(SiColor.BUTTON_PANEL, "#E81123")
        btn.colorGroup().assign(SiColor.TEXT_B, "#FFFFFF")
        btn.reloadStyleSheet()
        return btn

    def _create_pink_slider(self):
        s = SiSlider(self)
        s.setFixedHeight(24)
        s.style_data.main_color = QColor("#FF69B4")
        s.style_data.background_color = QColor(255, 105, 180, 64)
        s.style_data.handle_color = QColor("#FFFFFF")
        return s

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.scroll = SiScrollArea(self)
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setContentsMargins(15, 15, 15, 15)
        self.vbox.setSpacing(15)

        #  色阶调整
        grp_exp = SiTitledWidgetGroup(self)
        grp_exp.addTitle("色阶调整")
        v_exp = QVBoxLayout(grp_exp)
        v_exp.setContentsMargins(15, 50, 15, 15)
        v_exp.setSpacing(10)

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

        self.combo_map = SiCapsuleComboBox(self)
        self.combo_map.setTitle("强度映射方式")
        self.combo_map.setFixedHeight(30)
        self.combo_map.setEditable(False)
        self.combo_map.addItems(["线性", "对数", "幂函数", "sigmoid"])

        self.btn_apply_map = self._create_red_btn("应用设置")

        v_exp.addWidget(self.combo_map)
        v_exp.addWidget(self.btn_apply_map)

        self._apply_style(grp_exp)
        self.vbox.addWidget(grp_exp)

        # 去噪处理
        grp_noise = SiTitledWidgetGroup(self)
        grp_noise.addTitle("去噪处理")
        v_noise = QVBoxLayout(grp_noise)
        v_noise.setContentsMargins(15, 50, 15, 15)
        v_noise.setSpacing(8)
        lbl_noise = SiLabel("自上向下依次生效：")
        lbl_noise.setStyleSheet("color: white; font-weight: bold;")
        v_noise.addWidget(lbl_noise)

        self.combo_n1 = SiCapsuleComboBox(self)
        self.combo_n1.setTitle("一级去噪")
        self.combo_n1.setFixedHeight(30)
        self.combo_n1.setEditable(False)
        self.combo_n1.addItems(["None", "频域平滑", "滑动平均", "Savitzky-Golay滤波", "小波去噪", "卡尔曼滤波", "贝叶斯去噪"])

        self.combo_n2 = SiCapsuleComboBox(self)
        self.combo_n2.setTitle("二级去噪")
        self.combo_n2.setFixedHeight(30)
        self.combo_n2.setEditable(False)
        self.combo_n2.addItems(["None", "频域平滑", "滑动平均", "Savitzky-Golay滤波", "小波去噪", "卡尔曼滤波", "贝叶斯去噪"])

        self.combo_n3 = SiCapsuleComboBox(self)
        self.combo_n3.setTitle("三级去噪")
        self.combo_n3.setFixedHeight(30)
        self.combo_n3.setEditable(False)
        self.combo_n3.addItems(["None", "频域平滑", "滑动平均", "Savitzky-Golay滤波", "小波去噪", "卡尔曼滤波", "贝叶斯去噪"])

        self.btn_apply_noise = self._create_red_btn("应用所有去噪设置")

        v_noise.addWidget(self.combo_n1)
        v_noise.addWidget(self.combo_n2)
        v_noise.addWidget(self.combo_n3)
        v_noise.addWidget(self.btn_apply_noise)

        self._apply_style(grp_noise)
        self.vbox.addWidget(grp_noise)

        self.vbox.addStretch()
        self.scroll.setCenterWidget(self.container)
        layout.addWidget(self.scroll)