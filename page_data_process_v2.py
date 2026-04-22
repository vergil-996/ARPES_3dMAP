from PyQt5.QtCore import QSignalBlocker
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy
from PyQt5.QtGui import QColor
from siui.components.widgets import SiScrollArea, SiLabel, SiPushButton
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.slider_ import SiSlider
from siui.components.combobox_ import SiCapsuleComboBox
from siui.core import SiColor


class DataProcessPage(QWidget):
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.locked_half_width = 0
        self._is_updating = False
        self.init_ui()

    def _apply_style(self, grp):
        for child in grp.findChildren(SiLabel):
            try:
                child.colorGroup().assign(SiColor.TEXT_A, "#FFFFFF")
                child.reloadStyleSheet()
            except Exception:
                pass

    def _create_red_btn(self, text):
        btn = SiPushButton(self)
        btn.setFixedHeight(28)
        btn.setFixedWidth(self.BUTTON_WIDTH)
        btn.attachment().setText(text)
        btn.colorGroup().assign(SiColor.BUTTON_PANEL, "#E81123")
        btn.colorGroup().assign(SiColor.TEXT_B, "#FFFFFF")
        btn.reloadStyleSheet()
        return btn

    def _create_pink_slider(self):
        slider = SiSlider(self)
        slider.setFixedHeight(32)
        slider.setFixedWidth(self.SLIDER_BLOCK_WIDTH)
        slider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        slider.style_data.main_color = QColor("#FF69B4")
        slider.style_data.background_color = QColor(255, 105, 180, 64)
        slider.style_data.handle_color = QColor("#FFFFFF")
        return slider

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

        grp_t = SiTitledWidgetGroup(self)
        grp_t.setFixedWidth(self.SLIDER_GROUP_WIDTH)
        grp_t.addTitle("对时间轴积分")
        v_t = QVBoxLayout(grp_t)
        v_t.setContentsMargins(*self.GROUP_MARGINS)
        v_t.setSpacing(self.GROUP_SPACING)

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
        for _ in range(2):
            item = v_t.takeAt(v_t.count() - 1)
            row = item.layout() if item is not None else None
            if row is None:
                continue
            while row.count():
                child = row.takeAt(0)
                widget = child.widget()
                if widget is not None:
                    widget.setParent(None)
        for _ in range(2):
            item = v_t.takeAt(v_t.count() - 1)
            row = item.layout() if item is not None else None
            if row is None:
                continue
            while row.count():
                child = row.takeAt(0)
                widget = child.widget()
                if widget is not None:
                    widget.setParent(None)
        self._add_centered_slider_block(v_t, lbl1.text(), self.s_t_up)
        self._add_centered_slider_block(v_t, lbl2.text(), self.s_t_low)
        v_t.addLayout(self._center_widget(self.btn_t_apply, self.BUTTON_WIDTH))
        self._apply_style(grp_t)
        self.vbox.addLayout(self._center_widget(grp_t, self.SLIDER_GROUP_WIDTH))

        grp_ax = SiTitledWidgetGroup(self)
        grp_ax.setFixedWidth(self.SLIDER_GROUP_WIDTH)
        grp_ax.addTitle("对坐标轴积分")
        v_ax = QVBoxLayout(grp_ax)
        v_ax.setContentsMargins(*self.GROUP_MARGINS)
        v_ax.setSpacing(self.GROUP_SPACING)

        self.combo_ax = SiCapsuleComboBox(self)
        self.combo_ax.setTitle("选择轴向")
        self.combo_ax.setFixedHeight(30)
        self.combo_ax.setFixedWidth(self.COMBO_WIDTH)
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
        self.s_ax_mid = self._create_pink_slider()
        h_ax3.addWidget(self.s_ax_mid)

        self.s_ax_low.valueChanged.connect(self._on_axe_low_changed)
        self.s_ax_up.valueChanged.connect(self._on_axe_up_changed)
        self.s_ax_mid.valueChanged.connect(self._on_axe_mid_changed)

        self.btn_ax_apply = self._create_red_btn("应用")

        v_ax.addLayout(self._center_widget(self.combo_ax, self.COMBO_WIDTH))
        v_ax.addLayout(h_ax1)
        v_ax.addLayout(h_ax2)
        v_ax.addLayout(h_ax3)
        for _ in range(3):
            item = v_ax.takeAt(v_ax.count() - 1)
            row = item.layout() if item is not None else None
            if row is None:
                continue
            while row.count():
                child = row.takeAt(0)
                widget = child.widget()
                if widget is not None:
                    widget.setParent(None)
        for _ in range(3):
            item = v_ax.takeAt(v_ax.count() - 1)
            row = item.layout() if item is not None else None
            if row is None:
                continue
            while row.count():
                child = row.takeAt(0)
                widget = child.widget()
                if widget is not None:
                    widget.setParent(None)
        self._add_centered_slider_block(v_ax, lbl3.text(), self.s_ax_up)
        self._add_centered_slider_block(v_ax, lbl4.text(), self.s_ax_low)
        self._add_centered_slider_block(v_ax, lbl5.text(), self.s_ax_mid)
        v_ax.addLayout(self._center_widget(self.btn_ax_apply, self.BUTTON_WIDTH))
        self._apply_style(grp_ax)
        self.vbox.addLayout(self._center_widget(grp_ax, self.SLIDER_GROUP_WIDTH))

        grp_other = SiTitledWidgetGroup(self)
        grp_other.setFixedWidth(self.SLIDER_GROUP_WIDTH)
        grp_other.addTitle("其他积分")
        v_other = QVBoxLayout(grp_other)
        v_other.setContentsMargins(*self.GROUP_MARGINS)
        v_other.setSpacing(self.GROUP_SPACING)

        self.combo_other = SiCapsuleComboBox(self)
        self.combo_other.setTitle("\u79ef\u5206\u7c7b\u578b")
        self.combo_other.setFixedHeight(30)
        self.combo_other.setFixedWidth(self.COMBO_WIDTH)
        self.combo_other.setEditable(False)
        self.combo_other.addItems([
            "\u5207\u7247\u5185\u5f3a\u5ea6\u79ef\u5206",
            "\u80fd\u7ea7\u6001\u5bc6\u5ea6",
            "EDC\u7011\u5e03\u56fe",
            "\u4e8c\u9636\u5bfc",
        ])
        self.btn_other_apply = self._create_red_btn("应用")
        self.btn_other_save = self._create_red_btn("保存")

        v_other.addLayout(self._center_widget(self.combo_other, self.COMBO_WIDTH))
        v_other.addLayout(self._center_widget(self.btn_other_apply, self.BUTTON_WIDTH))
        v_other.addLayout(self._center_widget(self.btn_other_save, self.BUTTON_WIDTH))

        self._apply_style(grp_other)
        self.vbox.addLayout(self._center_widget(grp_other, self.SLIDER_GROUP_WIDTH))

        self.vbox.addStretch()
        self.scroll.setCenterWidget(self.container)
        layout.addWidget(self.scroll)

    def _on_t_low_changed(self, value):
        if value > self.s_t_up.value():
            self.s_t_up.setValue(value)

    def _on_t_up_changed(self, value):
        if value < self.s_t_low.value():
            self.s_t_low.setValue(value)

    def _on_axe_low_changed(self, value):
        if self._is_updating:
            return
        if value > self.s_ax_up.value():
            self.s_ax_up.setValue(value)
        self.locked_half_width = abs(self.s_ax_up.value() - self.s_ax_low.value()) // 2

    def _on_axe_up_changed(self, value):
        if self._is_updating:
            return
        if value < self.s_ax_low.value():
            self.s_ax_low.setValue(value)
        self.locked_half_width = abs(self.s_ax_up.value() - self.s_ax_low.value()) // 2

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

    @staticmethod
    def _combo_index_for_text(combo_box, text):
        if text == "切片态密度":
            text = "切片内强度积分"
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
            "s_ax_up": {
                "minimum": int(self.s_ax_up.minimum()),
                "maximum": int(self.s_ax_up.maximum()),
                "value": int(self.s_ax_up.value()),
            },
            "s_ax_mid": {
                "minimum": int(self.s_ax_mid.minimum()),
                "maximum": int(self.s_ax_mid.maximum()),
                "value": int(self.s_ax_mid.value()),
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
                self._sync_slider_visual(slider)

            combo_ax_state = state.get("combo_ax") or {}
            combo_ax_index = combo_ax_state.get("index")
            if combo_ax_index is None and "text" in combo_ax_state:
                combo_ax_index = self._combo_index_for_text(self.combo_ax, combo_ax_state["text"])
            if combo_ax_index is not None and 0 <= int(combo_ax_index) < self.combo_ax.count():
                self.combo_ax.setCurrentIndex(int(combo_ax_index))

            for slider_name, slider in (("s_ax_low", self.s_ax_low), ("s_ax_up", self.s_ax_up), ("s_ax_mid", self.s_ax_mid)):
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

            combo_other_state = state.get("combo_other") or {}
            combo_other_index = combo_other_state.get("index")
            if combo_other_index is None and "text" in combo_other_state:
                combo_other_index = self._combo_index_for_text(self.combo_other, combo_other_state["text"])
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
