from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from PyQt5.QtGui import QColor
from PyQt5.QtCore import Qt
from siui.components.widgets import SiScrollArea, SiLabel, SiPushButton
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.slider_ import SiSlider
from siui.components.combobox_ import SiCapsuleComboBox
from siui.core import SiColor


class DataProcessPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def _apply_style(self, grp):
        for child in grp.findChildren(SiLabel):
            try:
                # 统一文字颜色为白色
                child.colorGroup().assign(SiColor.TEXT_A, "#FFFFFF")
                child.reloadStyleSheet()
            except:
                pass

    def _create_red_btn(self, text):
        """统一小尺寸红色按钮"""
        btn = SiPushButton(self)
        btn.setFixedHeight(28)
        btn.attachment().setText(text)
        # 设置红色主题
        btn.colorGroup().assign(SiColor.BUTTON_PANEL, "#E81123")
        btn.colorGroup().assign(SiColor.TEXT_B, "#FFFFFF")
        btn.reloadStyleSheet()
        return btn

    def _create_pink_slider(self):
        """统一粉色滑块样式"""
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
        self.vbox.setSpacing(20)

        #  对时间轴积分
        grp_t = SiTitledWidgetGroup(self)
        grp_t.addTitle("对时间轴积分")
        v_t = QVBoxLayout(grp_t)
        v_t.setContentsMargins(15, 50, 15, 15)
        v_t.setSpacing(8)

        h_t1 = QHBoxLayout()
        lbl1 = SiLabel("积分上限")
        lbl1.setStyleSheet("color: white; font-weight: bold;")
        lbl1.setFixedWidth(60)
        h_t1.addWidget(lbl1)
        self.s_t_up = self._create_pink_slider()
        h_t1.addWidget(self.s_t_up)

        h_t2 = QHBoxLayout()
        lbl2 = SiLabel("积分下限")
        lbl2.setStyleSheet("color: white; font-weight: bold;")
        lbl2.setFixedWidth(60)
        h_t2.addWidget(lbl2)
        self.s_t_low = self._create_pink_slider()
        h_t2.addWidget(self.s_t_low)

        self.s_t_low.valueChanged.connect(self._on_t_low_changed)
        self.s_t_up.valueChanged.connect(self._on_t_up_changed)

        self.btn_t_apply = self._create_red_btn("应用")

        v_t.addLayout(h_t1)
        v_t.addLayout(h_t2)
        v_t.addWidget(self.btn_t_apply)
        self._apply_style(grp_t)
        self.vbox.addWidget(grp_t)

        #  对坐标轴积分
        grp_ax = SiTitledWidgetGroup(self)
        grp_ax.addTitle("对坐标轴积分")
        v_ax = QVBoxLayout(grp_ax)
        v_ax.setContentsMargins(15, 50, 15, 15)
        v_ax.setSpacing(8)

        self.combo_ax = SiCapsuleComboBox(self)
        self.combo_ax.setTitle("选择轴向")
        self.combo_ax.setFixedHeight(30)
        self.combo_ax.setEditable(False)
        self.combo_ax.addItems(["X轴", "Y轴", "Z轴"])

        h_ax1 = QHBoxLayout()
        lbl3 = SiLabel("积分上限")
        lbl3.setStyleSheet("color: white; font-weight: bold;")
        lbl3.setFixedWidth(60)
        h_ax1.addWidget(lbl3)
        self.s_ax_up = self._create_pink_slider()
        h_ax1.addWidget(self.s_ax_up)

        h_ax2 = QHBoxLayout()
        lbl4 = SiLabel("积分下限")
        lbl4.setStyleSheet("color: white; font-weight: bold;")
        lbl4.setFixedWidth(60)
        h_ax2.addWidget(lbl4)
        self.s_ax_low = self._create_pink_slider()
        h_ax2.addWidget(self.s_ax_low)

        h_ax3 = QHBoxLayout()
        lbl5 = SiLabel("中心位置")
        lbl5.setStyleSheet("color: white; font-weight: bold;")
        lbl5.setFixedWidth(60)
        h_ax3.addWidget(lbl5)
        self.s_ax_mid = self._create_pink_slider()  # 新增中点滑块
        h_ax3.addWidget(self.s_ax_mid)

        self.s_ax_low.valueChanged.connect(self._on_axe_low_changed)
        self.s_ax_up.valueChanged.connect(self._on_axe_up_changed)
        self.s_ax_mid.valueChanged.connect(self._on_axe_mid_changed)

        self.btn_ax_apply = self._create_red_btn("应用")

        v_ax.addWidget(self.combo_ax)
        v_ax.addLayout(h_ax1)
        v_ax.addLayout(h_ax2)
        v_ax.addLayout(h_ax3)
        v_ax.addWidget(self.btn_ax_apply)
        self._apply_style(grp_ax)
        self.vbox.addWidget(grp_ax)

        self._is_updating = False

        #  其它积分
        grp_other = SiTitledWidgetGroup(self)
        grp_other.addTitle("其它积分")
        v_other = QVBoxLayout(grp_other)
        v_other.setContentsMargins(15, 50, 15, 15)

        self.combo_other = SiCapsuleComboBox(self)
        self.combo_other.setTitle("积分类型")
        self.combo_other.setFixedHeight(30)
        self.combo_other.setEditable(False)  # 核心修改：设置为只读
        self.combo_other.addItems(["切片态密度", "能级态密度"]) # 补充示例项

        self.btn_other_apply = self._create_red_btn("应用")

        v_other.addWidget(self.combo_other)
        v_other.addWidget(self.btn_other_apply)

        self._apply_style(grp_other)
        self.vbox.addWidget(grp_other)

        self.vbox.addStretch()
        self.scroll.setCenterWidget(self.container)
        layout.addWidget(self.scroll)

    def _on_t_low_changed(self, value):
        # 如果下限超过上限，强迫上限跟着动
        if value > self.s_t_up.value():
            self.s_t_up.setValue(value)

    def _on_t_up_changed(self, value):
        # 如果上限低于下限，强迫下限跟着动
        if value < self.s_t_low.value():
            self.s_t_low.setValue(value)
    def _on_axe_low_changed(self, value):
        # 如果下限超过上限，强迫上限跟着动
        if value > self.s_ax_up.value():
            self.s_ax_up.setValue(value)

    def _on_axe_up_changed(self, value):
        # 如果上限低于下限，强迫下限跟着动
        if value < self.s_ax_low.value():
            self.s_ax_low.setValue(value)

    # 中心点驱动上下限平移
    def _on_axe_mid_changed(self, new_mid):
        if self._is_updating: return

        # 计算当前设定的厚度的一半
        half_width = (self.s_ax_up.value() - self.s_ax_low.value()) // 2

        new_low = new_mid - half_width
        new_up = new_mid + half_width

        # 边界检查防止溢出
        if new_low < 0:
            new_up -= new_low
            new_low = 0
        if new_up > self.s_ax_up.maximum():
            new_low -= (new_up - self.s_ax_up.maximum())
            new_up = self.s_ax_up.maximum()

        self._is_updating = True
        self.s_ax_low.setValue(max(0, new_low))
        self.s_ax_up.setValue(new_up)
        self._is_updating = False