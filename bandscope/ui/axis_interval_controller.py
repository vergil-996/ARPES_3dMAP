# -*- coding: utf-8 -*-
"""积分区间统一控制器 —— 控件只提交意图，模型负责约束，一次同步所有显示。

右侧积分卡片的上下限、可输入长度、锁定按钮，底栏的位置滑条/输入框，以及
左下角状态栏的锁定指示灯，全部指向同一个 :class:`AxisInterval`。以前中心
位置、上下限、锁定半宽各存一份，滑条的嵌套事件会用旧值回写输入框，计算又
从输入框反算索引，同一份状态在三个地方不一致。这里收敛成一条链：

    控件意图 → 模型约束 → 阻断信号统一同步 → 一次性保存/刷新

滑条按物理坐标映射（``SLIDER_STEPS`` 等分整个轴跨度），因此拖动是连续的，
端点也能停在采样点之间；需要采样下标时由 ``AxisInterval.to_indices`` 映射。
"""

from __future__ import annotations

from PyQt5.QtCore import QObject, QSignalBlocker, pyqtSignal

from bandscope.core.axis_interval import AxisSpace
from bandscope.ui import theme

#: 位置类滑条的等分数。滑条本身只认整数，等分整段物理跨度后拖动就是连续的，
#: 精确物理值仍由模型和输入框持有。
SLIDER_STEPS = 1000


class IntervalEditMode:
    """区间控件的可用范围。"""

    FULL = "full"           # 上下限 / 长度 / 锁定都可编辑（积分页及其裁剪结果）
    POSITION = "position"   # 只能移动位置（单层切片页）
    DISABLED = "disabled"   # 完全不参与区间编辑（3D 页面）


class AxisIntervalController(QObject):
    """把区间模型接到一组控件上，并保证同一份状态只产生一次业务更新。"""

    #: 区间值变化（拖动中）：主窗口保存页面状态并请求预览刷新。
    intervalChanged = pyqtSignal()
    #: 一次操作落定（释放滑条 / 输入提交）：主窗口请求精确刷新。
    intervalCommitted = pyqtSignal()
    #: 锁定状态变化：只影响保存状态，不触发重算。
    lockChanged = pyqtSignal(bool)

    def __init__(self, parent=None, *, slider_steps=SLIDER_STEPS):
        super().__init__(parent)
        self._interval = None
        self._space = None
        self._mode = IntervalEditMode.DISABLED
        self._slider_steps = max(int(slider_steps), 1)
        self._syncing = False
        self._widgets = {}
        self._connected = []

    # ------------------------------------------------------------------
    # 装配
    # ------------------------------------------------------------------
    def attach(
        self,
        *,
        slider_up=None,
        box_up=None,
        slider_low=None,
        box_low=None,
        box_length=None,
        button_lock=None,
        slider_position=None,
        box_position=None,
        label_lock=None,
    ):
        self._widgets = {
            "slider_up": slider_up,
            "box_up": box_up,
            "slider_low": slider_low,
            "box_low": box_low,
            "box_length": box_length,
            "button_lock": button_lock,
            "slider_position": slider_position,
            "box_position": box_position,
            "label_lock": label_lock,
        }
        self._connect()

    def _connect(self):
        for widget, signal_name, handler in self._signal_plan():
            if widget is None:
                continue
            signal = getattr(widget, signal_name, None)
            if signal is None:
                continue
            signal.connect(handler)
            self._connected.append((widget, signal, handler))

    def _signal_plan(self):
        return (
            (self._widgets.get("slider_up"), "valueChanged", self._on_slider_up),
            (self._widgets.get("slider_low"), "valueChanged", self._on_slider_low),
            (self._widgets.get("slider_position"), "valueChanged", self._on_slider_position),
            (self._widgets.get("box_up"), "editingFinished", self._on_box_up),
            (self._widgets.get("box_low"), "editingFinished", self._on_box_low),
            (self._widgets.get("box_length"), "editingFinished", self._on_box_length),
            (self._widgets.get("box_position"), "editingFinished", self._on_box_position),
            (self._widgets.get("button_lock"), "toggled", self._on_lock_toggled),
        )

    def detach(self):
        for widget, signal, handler in self._connected:
            try:
                signal.disconnect(handler)
            except (TypeError, RuntimeError):
                pass
        self._connected = []
        self._widgets = {}

    # ------------------------------------------------------------------
    # 绑定 / 读取
    # ------------------------------------------------------------------
    @property
    def interval(self):
        return self._interval

    @property
    def space(self):
        return self._space

    @property
    def mode(self):
        return self._mode

    def bind(self, space, interval):
        """换轴或换页：绑定新的轴描述和区间，然后整体同步一次。"""
        self._space = space
        self._interval = interval
        self.sync_widgets()

    def set_mode(self, mode):
        if self._mode == mode:
            return
        self._mode = mode
        self.sync_widgets()

    def set_locked(self, locked):
        if self._interval is None:
            return False
        if not self._interval.set_locked(locked):
            return False
        self.sync_widgets()
        self.lockChanged.emit(self._interval.locked)
        return True

    def reset_for_space(self, space, *, low=None, up=None, locked=False):
        """按新数据 / 新方向初始化：默认完整轴范围、未锁定。"""
        interval = space.as_interval(low=low, up=up, locked=locked)
        self.bind(space, interval)
        return interval

    # ------------------------------------------------------------------
    # 同步
    # ------------------------------------------------------------------
    def sync_widgets(self):
        """一次同步全部控件；期间阻断业务信号，避免嵌套回写。"""
        interval = self._interval
        space = self._space
        if interval is None or space is None:
            return

        widgets = self._widgets
        targets = [widget for widget in widgets.values() if widget is not None]
        blockers = [QSignalBlocker(widget) for widget in targets]
        previous = self._syncing
        self._syncing = True
        try:
            self._sync_sliders(interval, space)
            self._sync_boxes(interval, space)
            self._sync_lock(interval)
            self._sync_enabled()
        finally:
            self._syncing = previous
            del blockers

    def _sync_sliders(self, interval, space):
        steps = self._slider_steps
        tooltip = self._tooltip_func(space)
        for name, physical in (
            ("slider_up", interval.up),
            ("slider_low", interval.low),
            ("slider_position", interval.center),
        ):
            slider = self._widgets.get(name)
            if slider is None:
                continue
            slider.setRange(0, steps)
            setter = getattr(slider, "setToolTipConvertionFunc", None)
            if callable(setter):
                setter(tooltip)
            slider.setValue(self._to_slider(physical, space))

    def _sync_boxes(self, interval, space):
        decimals = space.decimals
        step = max(float(space.step), abs(space.physical_span) / self._slider_steps)
        for name, physical in (
            ("box_up", interval.up),
            ("box_low", interval.low),
            ("box_position", interval.center),
        ):
            box = self._widgets.get(name)
            if box is None:
                continue
            box.setDecimals(decimals)
            box.setRange(space.minimum, space.maximum)
            box.setSingleStep(step)
            box.setValue(float(physical))

        length_box = self._widgets.get("box_length")
        if length_box is not None:
            length_box.setDecimals(decimals)
            length_box.setRange(0.0, float(space.physical_span))
            length_box.setSingleStep(step)
            length_box.setValue(float(interval.length))

    def _sync_lock(self, interval):
        button = self._widgets.get("button_lock")
        if button is not None:
            button.setChecked(bool(interval.locked))
            button.setText("已锁定" if interval.locked else "锁定区间")

        label = self._widgets.get("label_lock")
        if label is not None:
            if interval.locked:
                label.setText("● 区间已锁定")
                label.setStyleSheet(
                    f"color: {theme.SUCCESS}; font-size: 11px; background: transparent;"
                )
            else:
                label.setText("● 区间未锁定")
                label.setStyleSheet(
                    f"color: {theme.TEXT_3}; font-size: 11px; background: transparent;"
                )

    def _sync_enabled(self):
        mode = self._mode
        full = mode == IntervalEditMode.FULL
        position = mode in (IntervalEditMode.FULL, IntervalEditMode.POSITION)

        locked = bool(self._interval is not None and self._interval.locked)
        for name in ("slider_up", "box_up", "slider_low", "box_low"):
            self._set_enabled(name, full)
        # 锁定后长度只能由端点平移改变，输入框只读。
        length_box = self._widgets.get("box_length")
        if length_box is not None:
            length_box.setEnabled(full)
            length_box.setReadOnly(bool(full and locked))
        button = self._widgets.get("button_lock")
        if button is not None:
            button.setEnabled(full)
        for name in ("slider_position", "box_position"):
            self._set_enabled(name, position)

    def _set_enabled(self, name, enabled):
        widget = self._widgets.get(name)
        if widget is not None:
            widget.setEnabled(bool(enabled))

    # ------------------------------------------------------------------
    # 控件意图
    # ------------------------------------------------------------------
    def _on_slider_up(self, value):
        self._apply(lambda interval: interval.set_up(self._from_slider(value)))

    def _on_slider_low(self, value):
        self._apply(lambda interval: interval.set_low(self._from_slider(value)))

    def _on_slider_position(self, value):
        self._apply(lambda interval: interval.set_center(self._from_slider(value)))

    def _on_box_up(self):
        self._commit(lambda interval, value: interval.set_up(value), "box_up")

    def _on_box_low(self):
        self._commit(lambda interval, value: interval.set_low(value), "box_low")

    def _on_box_position(self):
        self._commit(lambda interval, value: interval.set_center(value), "box_position")

    def _on_box_length(self):
        if self._interval is not None and self._interval.locked:
            # 锁定后长度输入框只读；仍然同步一次以清掉可能的中间文本。
            self.sync_widgets()
            return
        self._commit(lambda interval, value: interval.set_length(value), "box_length")

    def _on_lock_toggled(self, checked):
        if self._syncing:
            return
        self.set_locked(bool(checked))

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _commit(self, operation, widget_name):
        """输入框提交：一次操作只落定一次，不额外发预览。"""
        widget = self._widgets.get(widget_name)
        if widget is None or self._interval is None or self._syncing:
            return
        changed = operation(self._interval, float(widget.value()))
        # 无论是否真的改变，都要把显示规范化回模型（输入可能被约束掉）。
        self.sync_widgets()
        if changed:
            self.intervalCommitted.emit()

    def _apply(self, operation):
        if self._syncing or self._interval is None:
            return False
        if not operation(self._interval):
            return False
        self.sync_widgets()
        self.intervalChanged.emit()
        return True

    def _tooltip_func(self, space):
        unit = "" if space.describes_index or not space.unit else f" {space.unit}"

        def convert(value):
            physical = self._from_slider(value)
            return f"{space.label}: {physical:.{space.decimals}f}{unit}"

        return convert

    def _to_slider(self, physical, space):
        span = space.physical_span
        if span <= 0:
            return 0
        ratio = (float(physical) - space.minimum) / span
        ratio = min(max(ratio, 0.0), 1.0)
        return int(round(ratio * self._slider_steps))

    def _from_slider(self, value):
        space = self._space
        if space is None:
            return 0.0
        span = space.physical_span
        if span <= 0:
            return space.minimum
        ratio = min(max(float(value) / self._slider_steps, 0.0), 1.0)
        return space.minimum + span * ratio


__all__ = ["AxisIntervalController", "IntervalEditMode", "SLIDER_STEPS", "AxisSpace"]
