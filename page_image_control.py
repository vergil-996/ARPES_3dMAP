from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QMessageBox
from PyQt5.QtGui import QColor
from siui.components.widgets import SiScrollArea, SiLabel, SiPushButton
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.slider_ import SiSlider
from siui.components.editbox import SiLabeledLineEdit
from siui.components.button import SiSwitchRefactor
from siui.core import SiColor

class ImageControlPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.edits = {}
        self.init_ui()
        self.bind_events()

    def _apply_group_style(self, grp):
        for child in grp.findChildren(SiLabel):
            child.colorGroup().assign(SiColor.TEXT_A, "#FF69B4")
            child.reloadStyleSheet()

    def _create_slider(self):
        slider = SiSlider(self)
        slider.setFixedHeight(32)
        slider.style_data.main_color = QColor("#FF69B4")
        slider.style_data.background_color = QColor(255, 105, 180, 64)
        slider.style_data.handle_color = QColor("#FFFFFF")
        return slider

    def _create_red_btn(self, text):
        btn = SiPushButton(self)
        btn.setFixedHeight(32) # 统一小尺寸
        btn.attachment().setText(text)
        btn.colorGroup().assign(SiColor.BUTTON_PANEL, "#E81123")
        btn.colorGroup().assign(SiColor.TEXT_B, "#FFFFFF")
        btn.reloadStyleSheet()
        return btn

    def init_ui(self):
        layout = QVBoxLayout(self)
        self.scroll = SiScrollArea(self)
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setContentsMargins(15, 15, 15, 15)
        self.vbox.setSpacing(20)

        # --- 1. 时间轴控制 ---
        grp_time = SiTitledWidgetGroup(self)
        grp_time.addTitle("时间轴控制")
        self.slider_time = self._create_slider()
        v_time = QVBoxLayout(grp_time)
        v_time.setContentsMargins(15, 55, 15, 20)
        v_time.addWidget(self.slider_time)
        self._apply_group_style(grp_time)
        self.vbox.addWidget(grp_time)

        # --- 2. 切片立方体设置 ---
        grp_slice = SiTitledWidgetGroup(self)
        grp_slice.addTitle("切片立方体设置")
        v_slice = QVBoxLayout(grp_slice)
        v_slice.setContentsMargins(15, 55, 15, 20)
        v_slice.setSpacing(12)

        axes_cfg = [("X轴下限", "X轴上限"), ("Y轴下限", "Y轴上限"), ("Z轴下限", "Z轴上限")]
        for min_label, max_label in axes_cfg:
            h_row = QHBoxLayout()
            e_min = SiLabeledLineEdit(self)
            e_min.setTitle(min_label)
            e_min.setFixedHeight(45)
            e_max = SiLabeledLineEdit(self)
            e_max.setTitle(max_label)
            e_max.setFixedHeight(45)
            h_row.addWidget(e_min)
            h_row.addWidget(e_max)
            v_slice.addLayout(h_row)
            self.edits[min_label], self.edits[max_label] = e_min, e_max
        self._apply_group_style(grp_slice)
        self.vbox.addWidget(grp_slice)

        # --- 3. 开关组 ---
        h_sw = QHBoxLayout()
        self.switch_axes = SiSwitchRefactor(self)
        lbl_axes = SiLabel("显示标尺")
        lbl_axes.setStyleSheet("color: white; font-weight: bold;")
        self.switch_coord = SiSwitchRefactor(self)
        lbl_coord = SiLabel("切片交互")
        lbl_coord.setStyleSheet("color: white; font-weight: bold;")
        self.switch_flip = SiSwitchRefactor(self)
        lbl_flip = SiLabel("E轴反转")
        lbl_flip.setStyleSheet("color: white; font-weight: bold;")

        h_sw.addStretch()
        h_sw.addWidget(lbl_axes)
        h_sw.addWidget(self.switch_axes)
        h_sw.addWidget(lbl_coord)
        h_sw.addWidget(self.switch_coord)
        h_sw.addStretch()
        h_sw.addWidget(lbl_flip)
        h_sw.addWidget(self.switch_flip)
        self.vbox.addLayout(h_sw)

        # --- 4. 按钮组 ---
        h_btns = QHBoxLayout()
        self.btn_load = self._create_red_btn("加载")
        self.btn_cut = self._create_red_btn("截取")
        self.btn_save = self._create_red_btn("截图") # 原代码功能是截图
        self.btn_back = self._create_red_btn("返回")
        for b in [self.btn_load, self.btn_cut, self.btn_save, self.btn_back]: h_btns.addWidget(b)
        self.vbox.addLayout(h_btns)

        self.vbox.addStretch()
        self.scroll.setCenterWidget(self.container)
        layout.addWidget(self.scroll)

    # --- 逻辑接口层 ---
    def bind_events(self):
        """在这里绑定所有古早代码的信号"""
        # 注意：具体的业务逻辑(如 self.on_load) 建议在 main.py 中 connect，
        # 或者通过信号(Signal)传出。为了简单，这里直接预留逻辑函数。
        self.btn_load.clicked.connect(self.request_load)
        self.btn_cut.clicked.connect(self.request_cut)
        self.btn_save.clicked.connect(self.request_screenshot)
        self.btn_back.clicked.connect(self.request_back)

    def get_slice_values(self):
        """获取输入框坐标值"""
        try:
            return {k: v.text().strip() for k, v in self.edits.items()}
        except:
            return {}

    def set_slice_values(self, bounds):
        """同步交互盒坐标到输入框 (sync_box_to_edits)"""
        try:
            self.edits["X轴下限"].setText(f"{int(round(bounds[0]))}")
            self.edits["X轴上限"].setText(f"{int(round(bounds[1]))}")
            self.edits["Y轴下限"].setText(f"{int(round(bounds[2]))}")
            self.edits["Y轴上限"].setText(f"{int(round(bounds[3]))}")
            self.edits["Z轴下限"].setText(f"{int(round(bounds[4]))}")
            self.edits["Z轴上限"].setText(f"{int(round(bounds[5]))}")
        except:
            pass

    # 这里的函数通常发射信号给 main_window，或者直接由 main_window 这里的对象进行连接
    def request_load(self): pass
    def request_cut(self): pass
    def request_screenshot(self): pass
    def request_back(self): pass