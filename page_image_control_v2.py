from PyQt5.QtCore import QSignalBlocker
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy
from PyQt5.QtGui import QColor
from siui.components.widgets import SiScrollArea, SiLabel, SiPushButton
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.slider_ import SiSlider
from siui.components.editbox import SiLabeledLineEdit
from siui.components.button import SiSwitchRefactor
from siui.core import SiColor


class ImageControlPage(QWidget):
    PAGE_MARGIN = 13
    SECTION_MARGIN = 15
    SECTION_SPACING = 20
    GROUP_MARGINS = (15, 55, 15, 20)
    GROUP_SPACING = 12

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
        slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        slider.style_data.main_color = QColor("#FF69B4")
        slider.style_data.background_color = QColor(255, 105, 180, 64)
        slider.style_data.handle_color = QColor("#FFFFFF")
        return slider

    def _create_red_btn(self, text):
        btn = SiPushButton(self)
        btn.setFixedHeight(32)
        btn.attachment().setText(text)
        btn.colorGroup().assign(SiColor.BUTTON_PANEL, "#E81123")
        btn.colorGroup().assign(SiColor.TEXT_B, "#FFFFFF")
        btn.reloadStyleSheet()
        return btn

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

        grp_time = SiTitledWidgetGroup(self)
        grp_time.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        grp_time.addTitle("时间轴控制")
        self.slider_time = self._create_slider()
        v_time = QVBoxLayout(grp_time)
        v_time.setContentsMargins(*self.GROUP_MARGINS)
        v_time.setSpacing(self.GROUP_SPACING)
        v_time.addWidget(self.slider_time)
        self._apply_group_style(grp_time)
        self.vbox.addWidget(grp_time)

        grp_slice = SiTitledWidgetGroup(self)
        grp_slice.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        grp_slice.addTitle("切片立方体设置")
        v_slice = QVBoxLayout(grp_slice)
        v_slice.setContentsMargins(*self.GROUP_MARGINS)
        v_slice.setSpacing(self.GROUP_SPACING)

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

        h_sw = QHBoxLayout()
        self.switch_axes = SiSwitchRefactor(self)
        lbl_axes = SiLabel("显示坐标")
        lbl_axes.setStyleSheet("color: white; font-weight: bold;")
        self.switch_coord = SiSwitchRefactor(self)
        lbl_coord = SiLabel("切片交互")
        lbl_coord.setStyleSheet("color: white; font-weight: bold;")
        self.switch_flip = SiSwitchRefactor(self)
        lbl_flip = SiLabel("图像翻转")
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

        h_btns = QHBoxLayout()
        self.btn_load = self._create_red_btn("加载")
        self.btn_cut = self._create_red_btn("截取")
        self.btn_export = self._create_red_btn("保存")
        self.btn_save = self._create_red_btn("截图")
        self.btn_back = self._create_red_btn("返回")
        for btn in [self.btn_load, self.btn_cut, self.btn_export, self.btn_save, self.btn_back]:
            h_btns.addWidget(btn)
        self.vbox.addLayout(h_btns)

        self.vbox.addStretch()
        self.scroll.setCenterWidget(self.container)
        layout.addWidget(self.scroll)

    def bind_events(self):
        self.btn_load.clicked.connect(self.request_load)
        self.btn_cut.clicked.connect(self.request_cut)
        self.btn_export.clicked.connect(self.request_export)
        self.btn_save.clicked.connect(self.request_screenshot)
        self.btn_back.clicked.connect(self.request_back)

    def get_slice_values(self):
        try:
            return {k: v.text().strip() for k, v in self.edits.items()}
        except Exception:
            return {}

    def set_slice_values(self, bounds):
        try:
            self.edits["X轴下限"].setText(f"{float(bounds[0]):.2f}")
            self.edits["X轴上限"].setText(f"{float(bounds[1]):.2f}")
            self.edits["Y轴下限"].setText(f"{float(bounds[2]):.2f}")
            self.edits["Y轴上限"].setText(f"{float(bounds[3]):.2f}")
            self.edits["Z轴下限"].setText(f"{float(bounds[4]):.2f}")
            self.edits["Z轴上限"].setText(f"{float(bounds[5]):.2f}")
        except Exception:
            pass

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
            "slider_time": {
                "minimum": int(self.slider_time.minimum()),
                "maximum": int(self.slider_time.maximum()),
                "value": int(self.slider_time.value()),
            },
            "slice_values": self.get_slice_values(),
            "switch_axes": bool(self.switch_axes.isChecked()),
            "switch_coord": bool(self.switch_coord.isChecked()),
            "switch_flip": bool(self.switch_flip.isChecked()),
        }

    def restore_state(self, state, *, block_signals=True):
        state = state or {}
        blockers = []
        if block_signals:
            blockers = [
                QSignalBlocker(self.slider_time),
                QSignalBlocker(self.switch_axes),
                QSignalBlocker(self.switch_coord),
                QSignalBlocker(self.switch_flip),
            ]

        try:
            slider_state = state.get("slider_time") or {}
            minimum = slider_state.get("minimum")
            maximum = slider_state.get("maximum")
            if minimum is not None and maximum is not None:
                self.slider_time.setRange(int(minimum), int(maximum))
            if "value" in slider_state:
                value = int(slider_state["value"])
                value = max(int(self.slider_time.minimum()), min(int(self.slider_time.maximum()), value))
                self.slider_time.setValue(value)
            self._sync_slider_visual(self.slider_time)

            slice_values = state.get("slice_values") or {}
            for key, value in slice_values.items():
                widget = self.edits.get(key)
                if widget is not None:
                    widget.setText(str(value))

            if "switch_axes" in state:
                self.switch_axes.setChecked(bool(state["switch_axes"]))
            if "switch_coord" in state:
                self.switch_coord.setChecked(bool(state["switch_coord"]))
            if "switch_flip" in state:
                self.switch_flip.setChecked(bool(state["switch_flip"]))
        finally:
            del blockers

    def request_load(self):
        pass

    def request_cut(self):
        pass

    def request_export(self):
        pass

    def request_screenshot(self):
        pass

    def request_back(self):
        pass
