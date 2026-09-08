from PyQt5.QtCore import Qt, QSignalBlocker
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy, QSpinBox, QLabel, QLineEdit
from siui.components.widgets import SiLabel

import theme
from control_layout_utils import bounded_width
from control_page_base import ControlPageBase
from ui_controls import SyncedSlider, SyncedSwitch, RotationSpinBox


class ContinuousFrameSlider(SyncedSlider):
    """A discrete frame slider whose thumb still follows the mouse smoothly."""

    def _onValueChanged(self, value):
        if self._is_dragging:
            self._updateToolTip(flash=False)
            return
        super()._onValueChanged(value)

    def _onRangeChanged(self, minimum, maximum):
        if maximum == minimum:
            self.progress_ani.stop()
            self.setProperty(self.Property.TrackProgress, 0.0)
            self.progress_ani.setCurrentValue(0.0)
            self.progress_ani.setEndValue(0.0)
            return
        super()._onRangeChanged(minimum, maximum)

    def _setValueToMousePos(self, pos):
        thumb_width = self.style_data.thumb_width
        if self.orientation() == Qt.Horizontal:
            available = max(self.width() - thumb_width, 1)
            progress = min(1.0, max((pos.x() - thumb_width / 2) / available, 0.0))
        else:
            available = max(self.height() - thumb_width, 1)
            progress = min(1.0, max(1.0 - (pos.y() - thumb_width / 2) / available, 0.0))

        region = self.maximum() - self.minimum()
        # SyncedSlider.setValue 会把滑块吸附到整数帧进度；随后立即用精确的
        # 鼠标进度覆盖，保证 2~3 帧的数据集也能在整个轨道上拖动。
        self.setValue(int(self.minimum() + region * progress + 0.5))

        self.progress_ani.stop()
        self.setProperty(self.Property.TrackProgress, progress)
        self.progress_ani.setCurrentValue(progress)
        self.progress_ani.setEndValue(progress)
        self.update()


class ImageControlPage(ControlPageBase):
    SECTION_SPACING = 12
    GROUP_MARGINS = (15, 55, 15, 20)
    GROUP_SPACING = 12
    MIN_GROUP_WIDTH = 344
    MAX_GROUP_WIDTH = 470
    TIME_VALUE_BOX_WIDTH = 72
    TIME_VALUE_BOX_SPACING = 8
    SLICE_EDIT_WIDTH = 140
    BUTTON_WIDTH = 64
    MAX_EDIT_WIDTH = 220

    def __init__(self, parent=None):
        self.edits = {}
        self._slice_edits = []
        super().__init__(parent)
        self.bind_events()

    def _create_slider(self):
        slider = ContinuousFrameSlider(self)
        slider.setFixedHeight(32)
        slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        theme.style_accent_slider(slider)
        return slider

    def _create_time_value_box(self):
        spin_box = QSpinBox(self)
        spin_box.setFixedSize(self.TIME_VALUE_BOX_WIDTH, 30)
        spin_box.setRange(0, 99)
        spin_box.setSingleStep(1)
        spin_box.setKeyboardTracking(False)
        spin_box.setAlignment(Qt.AlignCenter)
        spin_box.setFocusPolicy(Qt.StrongFocus)
        spin_box.setToolTip("当前帧")
        spin_box.setStyleSheet(theme.value_input_qss())
        return spin_box

    def build_body(self):
        grp_time, v_time = self._create_group("时间轴")
        self.time_hint = QLabel("当前帧 · ← → 逐帧切换", self)
        self.time_hint.setStyleSheet(theme.field_label_qss())
        v_time.addWidget(self.time_hint)
        self.slider_time = self._create_slider()
        self.input_time = self._create_time_value_box()
        time_row = QHBoxLayout()
        time_row.setContentsMargins(0, 0, 0, 0)
        time_row.setSpacing(self.TIME_VALUE_BOX_SPACING)
        time_row.addWidget(self.slider_time)
        time_row.addWidget(self.input_time)
        v_time.addLayout(time_row)
        self.vbox.addWidget(grp_time)

        grp_slice, v_slice = self._create_group("切片范围")

        axes_cfg = [("X轴下限", "X轴上限"), ("Y轴下限", "Y轴上限"), ("Z轴下限", "Z轴上限")]
        for min_label, max_label in axes_cfg:
            h_row = QHBoxLayout()
            h_row.setSpacing(10)
            for text in (min_label, max_label):
                field = QVBoxLayout()
                field.setSpacing(5)
                label = QLabel(text.replace("轴", "  "), self)
                label.setStyleSheet(theme.field_label_qss())
                edit = QLineEdit(self)
                edit.setAccessibleName(text)
                edit.setFixedHeight(32)
                edit.setStyleSheet(theme.value_input_qss())
                self._slice_edits.append(edit)
                self.edits[text] = edit
                label.setBuddy(edit)
                field.addWidget(label)
                field.addWidget(edit)
                h_row.addLayout(field)
            v_slice.addLayout(h_row)

        # Z轴旋转角度
        h_rot = QHBoxLayout()
        rotation_label = QLabel("Z 轴旋转 / °", self)
        rotation_label.setStyleSheet(theme.field_label_qss())
        h_rot.addWidget(rotation_label)
        h_rot.addStretch()
        self.edit_rotation = RotationSpinBox(self)
        self.edit_rotation.setAccessibleName("Z轴旋转角度")
        self.edit_rotation.setMinimum(-360.0)
        self.edit_rotation.setMaximum(360.0)
        self.edit_rotation.setSingleStep(1.0)
        self.edit_rotation.setValue(0.0)
        self.edit_rotation.setFixedSize(108, 32)
        self.edit_rotation.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        h_rot.addWidget(self.edit_rotation)
        v_slice.addLayout(h_rot)

        self.vbox.addWidget(grp_slice)

        grp_disp, v_disp = self._create_group("显示选项")
        h_sw = QHBoxLayout()
        self.switch_axes = SyncedSwitch(self)
        lbl_axes = SiLabel("显示坐标")
        lbl_axes.setStyleSheet(theme.field_label_qss())
        self.switch_coord = SyncedSwitch(self)
        lbl_coord = SiLabel("切片交互")
        lbl_coord.setStyleSheet(theme.field_label_qss())
        self.switch_flip = SyncedSwitch(self)
        lbl_flip = SiLabel("E轴翻转")
        lbl_flip.setStyleSheet(theme.field_label_qss())

        h_sw.setSpacing(6)
        for switch in (self.switch_axes, self.switch_coord, self.switch_flip):
            switch.setFixedSize(40, 20)

        h_sw.addWidget(lbl_axes)
        h_sw.addWidget(self.switch_axes)
        h_sw.addStretch()
        h_sw.addWidget(lbl_coord)
        h_sw.addWidget(self.switch_coord)
        h_sw.addStretch()
        h_sw.addWidget(lbl_flip)
        h_sw.addWidget(self.switch_flip)
        v_disp.addLayout(h_sw)
        self.vbox.addWidget(grp_disp)

        grp_actions, v_actions = self._create_group("切片操作")
        h_btns = QHBoxLayout()
        h_btns.setSpacing(10)
        self.btn_load = self._create_btn("加载", "primary", height=32)
        self.btn_cut = self._create_btn("应用切片", "primary", height=36)
        self.btn_export = self._create_btn("保存", "secondary", height=32)
        self.btn_save = self._create_btn("截图", "secondary", height=32)
        self.btn_back = self._create_btn("返回原始", "secondary", height=36)
        for btn in [self.btn_load, self.btn_cut, self.btn_export, self.btn_save, self.btn_back]:
            self._adaptive_buttons.remove(btn)
        for btn in (self.btn_load, self.btn_export, self.btn_save):
            btn.hide()  # Global actions live in the toolbar; keep existing signal wiring.
        self.btn_cut.setMinimumWidth(0)
        self.btn_cut.setMaximumWidth(16777215)
        self.btn_cut.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_back.setFixedWidth(100)
        self.btn_back.setToolTip("返回原始数据视图")
        h_btns.addWidget(self.btn_cut, 1)
        h_btns.addWidget(self.btn_back)
        v_actions.addLayout(h_btns)
        self.vbox.addWidget(grp_actions)

    def _apply_extra_widths(self, group_width, widths):
        edit_width = bounded_width((group_width - 40) // 2, self.SLICE_EDIT_WIDTH, self.MAX_EDIT_WIDTH)
        for edit in self._slice_edits:
            edit.setFixedWidth(edit_width)

    def bind_events(self):
        self.slider_time.valueChanged.connect(self.input_time.setValue)
        self.slider_time.rangeChanged.connect(self.input_time.setRange)
        self.input_time.valueChanged.connect(self.slider_time.setValue)

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

    def get_rotation_angle(self):
        # Read the visible text so live typing and delayed exact refresh share
        # the same angle snapshot, including before editingFinished.
        text = self.edit_rotation.text().strip()
        try:
            return float(text)
        except (TypeError, ValueError):
            return float(self.edit_rotation.value())

    def set_rotation_angle(self, angle):
        self.edit_rotation.setValue(float(angle))

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
            "rotation_angle": self.get_rotation_angle(),
        }

    def restore_state(self, state, *, block_signals=True):
        state = state or {}
        blockers = []
        if block_signals:
            blockers = [
                QSignalBlocker(self.slider_time),
                QSignalBlocker(self.input_time),
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
                self.input_time.setRange(int(minimum), int(maximum))
            if "value" in slider_state:
                value = int(slider_state["value"])
                value = max(int(self.slider_time.minimum()), min(int(self.slider_time.maximum()), value))
                self.slider_time.setValue(value)
                self.input_time.setValue(value)

            slice_values = state.get("slice_values") or {}
            for key, value in slice_values.items():
                widget = self.edits.get(key)
                if widget is not None:
                    widget.setText(str(value))

            if "switch_axes" in state:
                self.switch_axes.setChecked(bool(state["switch_axes"]))
            if "switch_coord" in state:
                self.switch_coord.setChecked(bool(state["switch_coord"]))
            rotation_angle = state.get("rotation_angle")
            if rotation_angle is not None:
                self.set_rotation_angle(str(rotation_angle))
            else:
                self.set_rotation_angle(0.0)

            if "switch_flip" in state:
                self.switch_flip.setChecked(bool(state["switch_flip"]))
        finally:
            del blockers
