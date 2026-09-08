# -*- coding: utf-8 -*-
"""视觉自同步控件 —— 把 SiUI 控件的程序化状态同步收进控件内部。

SiSlider / SiSwitchRefactor 的绘制状态由独立动画属性驱动，只在用户
交互时更新；程序化 ``setValue()`` / ``setChecked()`` 不会改变视觉位置，
导致恢复页面状态时滑条/开关显示陈旧。以前每个页面的 ``restore_state``
都要记得手动调 ``sync_slider_visual`` / ``sync_switch_visual`` 补丁，
现在由控件自身保证：任何 ``setValue`` / ``setChecked`` 后视觉立即一致。

经核实 SiSwitchRefactor._onClicked 直接驱动动画、不经过 setChecked，
ContinuousFrameSlider 拖动后也会自行覆盖精确进度，因此重写这两个方法
不会破坏用户交互动画。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QDoubleSpinBox, QPushButton
from siui.components.button import SiSwitchRefactor
from siui.components.slider_ import SiSlider

import theme


class ActionButton(QPushButton):
    """Flat, keyboard-accessible button retaining the SiUI text attachment API."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)

    def attachment(self):
        return self


class RotationSpinBox(QDoubleSpinBox):
    """Expose live text edits for the existing rotation preview pipeline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDecimals(10)
        self.setStyleSheet(theme.value_input_qss())

    def textFromValue(self, value):
        text = f"{value:.10f}".rstrip("0").rstrip(".")
        return text if "." in text else text + ".0"


class SyncedSlider(SiSlider):
    """setValue / setRange 后自动同步滑块视觉位置的 SiSlider。"""

    def setValue(self, value):
        super().setValue(value)
        self.sync_visual()

    def setRange(self, minimum, maximum):
        super().setRange(minimum, maximum)
        self.sync_visual()

    def sync_visual(self):
        minimum = int(self.minimum())
        maximum = int(self.maximum())
        value = int(self.value())
        progress = 0.0 if maximum == minimum else (value - minimum) / (maximum - minimum)

        try:
            self.setProperty(self.Property.TrackProgress, progress)
        except Exception:
            pass

        progress_ani = getattr(self, "progress_ani", None)
        if progress_ani is not None:
            try:
                progress_ani.fromProperty()
                progress_ani.setCurrentValue(progress)
                progress_ani.setEndValue(progress)
            except Exception:
                pass

        update_tooltip = getattr(self, "_updateToolTip", None)
        if callable(update_tooltip):
            try:
                update_tooltip(flash=False)
            except Exception:
                pass

        self.update()


class SyncedSwitch(SiSwitchRefactor):
    """setChecked 后自动同步拨杆视觉位置的 SiSwitchRefactor。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # SwitchStyleData 颜色是硬编码类属性（亮紫轨道），这里按实例覆盖为设计令牌
        self.style_data.background_color_starting = QColor(theme.ACCENT)
        self.style_data.background_color_ending = QColor(theme.ACCENT)
        self.style_data.frame_color = QColor(theme.BG_4)
        self.style_data.thumb_color_checked = QColor(theme.ACCENT_ON)
        self.style_data.thumb_color_unchecked = QColor(theme.TEXT_1)

    def setChecked(self, checked):
        super().setChecked(checked)
        self.sync_visual()

    def sync_visual(self):
        progress = 1.0 if bool(self.isChecked()) else 0.0

        try:
            self.setProperty(self.Property.Progress, progress)
        except Exception:
            pass

        progress_ani = getattr(self, "progress_ani", None)
        if progress_ani is not None:
            try:
                progress_ani.fromProperty()
                progress_ani.setCurrentValue(progress)
                progress_ani.setEndValue(progress)
            except Exception:
                pass

        self.update()
