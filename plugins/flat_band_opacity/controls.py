# -*- coding: utf-8 -*-
"""「平带增强」卡片内容：效果总开关、背景保留、可增删的平带列表。

设计约定：

* 面板只读写 :class:`effect.EffectState`，不接触渲染、数据或宿主私有字段。
  任何会改变倍率的操作都发 :attr:`FlatBandPanel.changed`，由插件转成宿主刷新。
* **无效编辑保留上一次有效预览**：数值框解析失败时只标红，不写入模型，模型里
  始终是上一次有效的中心/宽度/增强量，因此画面不会因为输入到一半而跳变。
* 新增条目的能量留空，填写有效中心后自动启用该行；此后开关完全由用户决定。
* 条目多时列表自身滚动，不影响卡片外的页面滚动。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PyQt5.QtCore import QLocale, QSignalBlocker, Qt, pyqtSignal
from PyQt5.QtGui import QDoubleValidator
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bandscope.extensions.ui import theme
from bandscope.extensions.ui import ActionButton, SyncedSlider, SyncedSwitch

from .effect import (
    MAX_GAIN,
    MAX_BANDS,
    MIN_GAIN,
    EffectState,
    FlatBand,
    minimum_fwhm,
)

#: 没有单位时也要说清楚显示的是什么，不能默认标成 eV。
UNIT_FALLBACK = "坐标值"

#: 列表滚动区的配色：贴近页面自身的滚动条，避免标准控件的默认外观突兀。
SCROLL_AREA_QSS = f"""
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0px; }}
QScrollBar::handle:vertical {{
    background: {theme.BG_4}; border-radius: 4px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {theme.ACCENT_DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""


class NumberField(QLineEdit):
    """可留空的数值输入框。

    用 ``QLineEdit`` 而不是 ``QDoubleSpinBox``：能量中心需要真正的“空”状态
    （新增条目还没填峰位），而 QDoubleSpinBox 总有一个数值，只能靠哨兵值假装
    空；同时用户输入到一半的文本也不需要被 Qt 立刻改写成合法数字。
    """

    edited = pyqtSignal()

    def __init__(self, *, step=0.01, decimals=4, width=76, parent=None):
        super().__init__(parent)
        self._step = float(step)
        self._decimals = int(decimals)
        self.setFixedHeight(28)
        self.setFixedWidth(int(width))
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(theme.value_input_qss())
        validator = QDoubleValidator(self)
        validator.setNotation(QDoubleValidator.StandardNotation)
        validator.setDecimals(self._decimals)
        # 固定用 C 区域：逗号小数点的系统区域下，"0.5" 会被校验器拒掉，而这个
        # 字符串随后是要交给 float() 解析的。
        validator.setLocale(QLocale.c())
        self.setValidator(validator)
        self.textEdited.connect(self._on_text_edited)

    # -- 读写 -----------------------------------------------------------
    def value(self) -> Optional[float]:
        text = self.text().strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        return number if number == number and number not in (float("inf"), float("-inf")) else None

    def setValue(self, value):
        if value is None:
            text = ""
        else:
            number = float(value)
            text = f"{number:.{self._decimals}f}".rstrip("0").rstrip(".")
            if not text or text == "-":
                text = "0"
        blocker = QSignalBlocker(self)
        try:
            self.setText(text)
        finally:
            del blocker
        self._set_invalid(False)

    def set_step(self, step):
        self._step = float(step)

    # -- 步进与非法态 ---------------------------------------------------
    def _on_text_edited(self, _text):
        self._set_invalid(self.text().strip() != "" and self.value() is None)
        self.edited.emit()

    def _set_invalid(self, invalid):
        self.setProperty("arpesInvalid", bool(invalid))
        self.setStyleSheet(
            theme.value_input_qss()
            + (
                f"QLineEdit {{ border: 1px solid {theme.DANGER}; }}"
                if invalid
                else ""
            )
        )

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            current = self.value()
            base = 0.0 if current is None else current
            delta = self._step if event.key() == Qt.Key_Up else -self._step
            self.setValue(base + delta)
            self.edited.emit()
            return
        super().keyPressEvent(event)


class BandRow(QWidget):
    """一条平带：启用、能量中心、厚度（FWHM）、增强量、删除。"""

    changed = pyqtSignal()
    removed = pyqtSignal(str)

    def __init__(self, band: FlatBand, parent=None):
        super().__init__(parent)
        self.band = band
        self._axis = None
        self._syncing = False
        # 新增行填写完有效中心后自动启用一次；之后开关完全交给用户。
        self._auto_enable_pending = not band.enabled and band.center is None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 6)
        outer.setSpacing(4)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        self.switch = SyncedSwitch(self)
        self.switch.setFixedSize(36, 20)
        self.switch.setChecked(bool(band.enabled))
        self.switch.toggled.connect(self._on_enabled_toggled)

        self.field_center = NumberField(step=0.01, decimals=5, width=84, parent=self)
        self.field_center.setToolTip(
            "该条能带的能量中心。与坐标轴同单位；留空表示还没填写。"
        )
        self.field_center.setValue(band.center)
        self.field_center.edited.connect(self._on_center_edited)

        self.field_fwhm = NumberField(step=0.005, decimals=5, width=74, parent=self)
        self.field_fwhm.setToolTip(
            "覆盖厚度，用高斯半高全宽（FWHM）表示，与能量中心同单位。\n"
            "中心 ± 厚度/2 处的倍率是峰值的一半。"
        )
        self.field_fwhm.setValue(band.fwhm)
        self.field_fwhm.edited.connect(self._on_fwhm_edited)

        self.field_gain = NumberField(step=0.1, decimals=3, width=64, parent=self)
        self.field_gain.setToolTip(
            f"该条在中心处额外增加的不透明度倍率，范围 {MIN_GAIN:g}–{MAX_GAIN:g}。\n"
            "分离良好的峰用 0.8：中心倍率 = 背景 0.2 + 0.8 = 1，主要靠压低背景突出能带。\n"
            "弱带可以单独提高这一项。"
        )
        self.field_gain.setValue(band.gain)
        self.field_gain.edited.connect(self._on_gain_edited)

        self.btn_remove = ActionButton(self)
        self.btn_remove.setFixedSize(26, 26)
        self.btn_remove.setText("×")
        self.btn_remove.setToolTip("删除这条能带；其他条目的参数不受影响。")
        theme.style_push_button(self.btn_remove, "danger")
        self.btn_remove.clicked.connect(lambda: self.removed.emit(self.band.band_id))

        self.lbl_center = self._field_label("中心", self.field_center)
        self.lbl_fwhm = self._field_label("厚度", self.field_fwhm)
        self.lbl_gain = self._field_label("增强", self.field_gain)

        grid.addWidget(self.switch, 0, 0, 2, 1, Qt.AlignTop | Qt.AlignHCenter)
        grid.addWidget(self.lbl_center, 0, 1)
        grid.addWidget(self.field_center, 0, 2)
        grid.addWidget(self.lbl_fwhm, 0, 3)
        grid.addWidget(self.field_fwhm, 0, 4)
        grid.addWidget(self.btn_remove, 0, 5, 2, 1, Qt.AlignTop)
        grid.addWidget(self.lbl_gain, 1, 1)
        grid.addWidget(self.field_gain, 1, 2)
        grid.setColumnStretch(4, 1)
        outer.addLayout(grid)

        self.hint = QLabel("", self)
        self.hint.setStyleSheet(theme.field_label_qss())
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        self.line = QFrame(self)
        self.line.setFixedHeight(1)
        self.line.setStyleSheet(f"background-color: {theme.BORDER_HEX};")
        outer.addWidget(self.line)

        self.sync_axis(None)

    @staticmethod
    def _field_label(text, buddy):
        label = QLabel(text, buddy.parentWidget() or buddy)
        label.setStyleSheet(theme.field_label_qss())
        label.setBuddy(buddy)
        return label

    # -- 同步 -----------------------------------------------------------
    def sync_axis(self, axis, *, roi_warning=True):
        """更新单位后缀与提示；参数本身不变。"""
        self._axis = axis
        # 单位如实转述：eV / 坐标值 / index，三个都要显示出来，不能默认标 eV，
        # 也不能把索引情形显示成没有单位。
        unit = axis.display_unit if axis is not None else ""
        suffix = f" ({unit})" if unit else ""
        self.lbl_center.setText(f"中心{suffix}")
        self.lbl_fwhm.setText(f"厚度{suffix}")

        messages = []
        reason = self.band.invalid_reason()
        if reason is not None and self.band.enabled:
            messages.append(reason)
        if (
            roi_warning
            and axis is not None
            and self.band.enabled
            and self.band.center is not None
            and not axis_check(axis, self.band.center)
        ):
            messages.append("能量中心不在当前能量范围内；参数已保留。")
        self.hint.setText("　".join(messages))
        self.hint.setVisible(bool(messages))

        step = float(axis.sample_spacing()) if axis is not None else 0.0
        if step > 0:
            self.field_center.set_step(step)
            self.field_fwhm.set_step(max(step, 1e-6))
        floor = minimum_fwhm(axis)
        self.field_fwhm.setToolTip(
            "覆盖厚度，用高斯半高全宽（FWHM）表示，与能量中心同单位。\n"
            "中心 ± 厚度/2 处的倍率是峰值的一半。"
            + (f"\n不低于 {floor:.5g}（半个采样间距）才看得见。" if floor > 0 else "")
        )

    # -- 用户操作 -------------------------------------------------------
    def _on_enabled_toggled(self, checked):
        if self._syncing:
            return
        self.band.enabled = bool(checked)
        if not checked:
            self._auto_enable_pending = False
        self.sync_axis(self._axis, roi_warning=True)
        self.changed.emit()

    def _on_center_edited(self):
        value = self.field_center.value()
        if value is None:
            # 无效输入不改模型：画面保持上一次有效预览。
            self.sync_axis(self._axis, roi_warning=True)
            return
        self.band.center = value
        if self._auto_enable_pending:
            self._auto_enable_pending = False
            if not self.band.enabled:
                self.band.enabled = True
                blocker = QSignalBlocker(self.switch)
                try:
                    self.switch.setChecked(True)
                finally:
                    del blocker
        self.sync_axis(self._axis, roi_warning=True)
        self.changed.emit()

    def _on_fwhm_edited(self):
        value = self.field_fwhm.value()
        if value is None:
            self.sync_axis(self._axis, roi_warning=True)
            return
        self.band.fwhm = value if value > 0 else self.band.fwhm
        if value <= 0:
            self.field_fwhm.setValue(self.band.fwhm)
        self.sync_axis(self._axis, roi_warning=True)
        self.changed.emit()

    def _on_gain_edited(self):
        value = self.field_gain.value()
        if value is None:
            self.sync_axis(self._axis, roi_warning=True)
            return
        clamped = max(MIN_GAIN, min(MAX_GAIN, value))
        if clamped != value:
            self.field_gain.setValue(clamped)
        self.band.gain = clamped
        self.sync_axis(self._axis, roi_warning=True)
        self.changed.emit()

    # -- 回填 -----------------------------------------------------------
    def reset_fields(self, axis=None):
        self._axis = axis
        self._syncing = True
        try:
            self.switch.setChecked(bool(self.band.enabled))
            self.field_center.setValue(self.band.center)
            self.field_fwhm.setValue(self.band.fwhm)
            self.field_gain.setValue(self.band.gain)
        finally:
            self._syncing = False
        self.sync_axis(axis)


def axis_check(axis, center: float) -> bool:
    """峰位是否落在当前能量范围内（含首尾）。"""
    low, high = axis.roi_range
    lo, hi = (low, high) if low <= high else (high, low)
    return lo <= float(center) <= hi


class FlatBandPanel(QWidget):
    """卡片内容。所有参数读写都落在构造时传入的 :class:`EffectState` 上。"""

    #: 可能影响倍率的参数变化；宿主据此节流刷新。
    changed = pyqtSignal()
    #: 插件把预设导入导出交给入口实现（需要当前能量轴与宿主提示）。
    preset_export_requested = pyqtSignal()
    preset_import_requested = pyqtSignal()

    #: 列表滚动区的高度区间：条目少时贴合内容，多了就在卡片内部滚动。
    LIST_MAX_HEIGHT = 240
    MIN_LIST_HEIGHT = 32

    def __init__(self, state: EffectState, parent=None):
        super().__init__(parent)
        self.state = state
        self._axis = None
        self._syncing = True
        self._rows: Dict[str, BandRow] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        layout.addLayout(self._build_switch_row())

        self.slider_background = SyncedSlider(self)
        self.slider_background.setRange(0, 100)
        self.slider_background.setFixedHeight(32)
        theme.style_accent_slider(self.slider_background)
        self.slider_background.setValue(int(round(self.state.background * 100)))
        self.slider_background.valueChanged.connect(self._on_background_changed)
        layout.addLayout(self._build_background_block())

        layout.addLayout(self._build_button_row())
        layout.addWidget(self._build_status_label())
        layout.addWidget(self._build_list())

        self._syncing = False
        self.rebuild()
        self._sync_controls()
        self._update_status()

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------
    def _build_switch_row(self):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.switch_enabled = SyncedSwitch(self)
        self.switch_enabled.setFixedSize(36, 20)
        self.switch_enabled.setChecked(bool(self.state.enabled))
        self.switch_enabled.setToolTip(
            "随时与原图比较：关闭时绕过插件效果，恢复原有渲染，参数全部保留。"
        )
        self.switch_enabled.toggled.connect(self._on_enabled_changed)
        label = QLabel("平带增强", self)
        label.setStyleSheet(theme.field_label_qss())
        row.addWidget(self.switch_enabled)
        row.addWidget(label)
        row.addStretch()
        self.lbl_status = QLabel("", self)
        self.lbl_status.setStyleSheet(theme.field_label_qss())
        row.addWidget(self.lbl_status)
        return row

    def _build_background_block(self):
        block = QVBoxLayout()
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(6)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        title = QLabel("背景保留", self)
        title.setStyleSheet(theme.field_label_qss())
        title.setBuddy(self.slider_background)
        self.lbl_background = QLabel("", self)
        self.lbl_background.setStyleSheet(theme.slider_value_qss())
        head.addWidget(title)
        head.addStretch()
        head.addWidget(self.lbl_background)
        block.addLayout(head)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch()
        row.addWidget(self.slider_background)
        row.addStretch()
        block.addLayout(row)
        return block

    def _build_button_row(self):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.btn_add = ActionButton(self)
        self.btn_add.setFixedHeight(30)
        self.btn_add.setText("添加平带")
        self.btn_add.setToolTip("新增一条独立参数。条数由你决定，不预设固定数量。")
        theme.style_push_button(self.btn_add, "primary")
        self.btn_add.clicked.connect(self.add_band)

        self.btn_reset = ActionButton(self)
        self.btn_reset.setFixedHeight(30)
        self.btn_reset.setText("重置显示参数")
        self.btn_reset.setToolTip(
            "恢复背景、增强量和厚度的建议值；保留已添加的数量与峰位。"
        )
        theme.style_push_button(self.btn_reset, "secondary")
        self.btn_reset.clicked.connect(self.reset_display_parameters)

        self.btn_export = ActionButton(self)
        self.btn_export.setFixedHeight(30)
        self.btn_export.setText("导出预设")
        theme.style_push_button(self.btn_export, "secondary")
        self.btn_export.clicked.connect(self.preset_export_requested)

        self.btn_import = ActionButton(self)
        self.btn_import.setFixedHeight(30)
        self.btn_import.setText("导入预设")
        theme.style_push_button(self.btn_import, "secondary")
        self.btn_import.clicked.connect(self.preset_import_requested)

        row.addWidget(self.btn_add)
        row.addWidget(self.btn_reset)
        row.addStretch()
        row.addWidget(self.btn_export)
        row.addWidget(self.btn_import)
        return row

    def _build_status_label(self):
        self.lbl_message = QLabel("", self)
        self.lbl_message.setStyleSheet(theme.field_label_qss())
        self.lbl_message.setWordWrap(True)
        return self.lbl_message

    def _build_list(self):
        # 用普通 QScrollArea 而不是 siui 的 SiScrollArea：后者的 resizeEvent 直接
        # 除以附件尺寸，附件为空（列表为空时必然是空）就 ZeroDivisionError，而这个
        # 异常出在 Qt 槽里，会直接 abort 整个进程。标准控件没有这个陷阱。
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet(SCROLL_AREA_QSS)
        self.scroll.setVisible(False)

        self.list_container = QWidget()
        self.list_container.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(2)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.list_container)
        return self.scroll

    # ------------------------------------------------------------------
    # 列表维护
    # ------------------------------------------------------------------
    def rebuild(self):
        """按当前状态重建全部条目行。"""
        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows = {}
        for band in self.state.bands:
            self._insert_row(band)
        self._relayout_list()

    def _insert_row(self, band: FlatBand):
        row = BandRow(band, self.list_container)
        row.changed.connect(self._on_row_changed)
        row.removed.connect(self.remove_band)
        row.sync_axis(self._axis)
        self.list_layout.insertWidget(self.list_layout.count() - 1, row)
        self._rows[band.band_id] = row
        return row

    def _relayout_list(self):
        """按条目数决定列表高度；空列表直接收起，不做任何尺寸调整。

        绝不把滚动区高度设成 0：SiUI 的 SiScrollArea 在 resizeEvent 里直接除以
        附件尺寸，附件为 0 时会 ZeroDivisionError，而 Qt 槽里的异常会直接结束
        进程。这里用普通 QScrollArea，并且连收起也只走 setVisible。
        """
        if self.list_layout.count() <= 1:
            self.scroll.setVisible(False)
            return
        self.scroll.setVisible(True)
        natural = int(self.list_container.sizeHint().height())
        self.scroll.setFixedHeight(max(self.MIN_LIST_HEIGHT, min(natural, self.LIST_MAX_HEIGHT)))

    def add_band(self):
        if len(self.state.bands) >= MAX_BANDS:
            self.set_message(f"最多支持 {MAX_BANDS} 条平带。")
            return
        band = self.state.new_band(self._axis)
        band.enabled = False
        self.state.bands.append(band)
        row = self._insert_row(band)
        self._relayout_list()
        self._update_status()
        row.field_center.setFocus()
        self.changed.emit()

    def remove_band(self, band_id: str):
        remaining = [band for band in self.state.bands if band.band_id != band_id]
        if len(remaining) == len(self.state.bands):
            return
        self.state.bands = remaining
        row = self._rows.pop(band_id, None)
        if row is not None:
            row.setParent(None)
            row.deleteLater()
        self._relayout_list()
        self._update_status()
        self.changed.emit()

    # ------------------------------------------------------------------
    # 用户操作
    # ------------------------------------------------------------------
    def _on_enabled_changed(self, checked):
        if self._syncing:
            return
        self.state.enabled = bool(checked)
        self._update_status()
        self.changed.emit()

    def _on_background_changed(self, value):
        self.lbl_background.setText(f"{int(value)}%")
        if self._syncing:
            return
        self.state.background = max(0.0, min(1.0, int(value) / 100.0))
        self.changed.emit()

    def _on_row_changed(self):
        self._update_status()
        self.changed.emit()

    def reset_display_parameters(self):
        self.state.reset_display_parameters(self._axis)
        self._syncing = True
        try:
            self.slider_background.setValue(int(round(self.state.background * 100)))
        finally:
            self._syncing = False
        for band in self.state.bands:
            row = self._rows.get(band.band_id)
            if row is not None:
                row.reset_fields(self._axis)
        self._update_status()
        self.changed.emit()

    # ------------------------------------------------------------------
    # 状态显示
    # ------------------------------------------------------------------
    def sync_context(self, context):
        """视图/能量轴变化时更新单位与提示；不修改任何参数。"""
        axis = getattr(context, "energy", None)
        self._axis = axis
        for row in self._rows.values():
            row.sync_axis(axis)
        self._update_status()

    def refresh_from_state(self, axis=None):
        """外部（恢复页面状态、导入预设）改写 state 后整体回填控件。"""
        if axis is not None:
            self._axis = axis
        self._syncing = True
        try:
            self.switch_enabled.setChecked(bool(self.state.enabled))
            self.slider_background.setValue(int(round(self.state.background * 100)))
            self.lbl_background.setText(f"{int(round(self.state.background * 100))}%")
        finally:
            self._syncing = False
        self.rebuild()
        self._update_status()

    def _sync_controls(self):
        self.lbl_background.setText(f"{int(round(self.state.background * 100))}%")

    def set_message(self, text):
        self.lbl_message.setText(text or "")
        self.lbl_message.setVisible(bool(text))

    def _update_status(self):
        if not self.state.enabled:
            self.lbl_status.setText("已关闭")
            self.set_message("效果已关闭，当前显示原图；参数全部保留。")
            return
        self.lbl_status.setText("")

        active = [band for band in self.state.bands if band.active]
        if not self.state.bands:
            self.set_message(
                "尚未添加平带。点击「添加平带」为每条要突出的能带建立一行，"
                "再填写各自的能量中心。"
            )
            return
        if not active:
            self.set_message("没有已启用的平带，当前显示原图。填写能量中心或打开对应开关。")
            return

        outside = [
            band
            for band in active
            if self._axis is not None and not axis_check(self._axis, band.center)
        ]
        message = f"已启用 {len(active)} 条平带。"
        if outside:
            message += f"其中 {len(outside)} 条的能量中心不在当前能量范围内（参数已保留）。"
        self.set_message(message)


__all__ = ["BandRow", "FlatBandPanel", "NumberField", "UNIT_FALLBACK"]
