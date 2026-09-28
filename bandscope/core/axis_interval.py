# -*- coding: utf-8 -*-
"""物理积分区间模型 —— 纯数值，不依赖 Qt。

积分区间的唯一真值。控件、页面参数和计算都从这里取值，不再各自维护
中心 / 上下限 / 锁定半宽。端点保留精确物理值（可以落在采样点之间），
只有计算时才由 :meth:`AxisInterval.to_indices` 映射到最近采样点。

交互规则（``locked`` 为假时端点各自独立，为真时整体平移）::

    操作        未锁定                      已锁定
    修改上限    下限固定，中心和长度更新     按上限位移整体平移
    修改下限    上限固定，中心和长度更新     按下限位移整体平移
    平移位置    保持长度整体平移             保持长度整体平移
    修改长度    以当前中心向两侧调整         输入框只读（调用方禁用）

未锁定时端点不能越过另一端，越过就停在另一端，允许零长度（单层采样）。
平移触边时限制位移，两端一起停，长度不变。
"""

from __future__ import annotations

import math

import numpy as np

__all__ = ["AxisInterval", "AxisSpace", "nearest_index", "physical_decimals"]

AXIS_DISPLAY_LABELS = {"X": "kx", "Y": "ky", "E": "E", "delay": "时间"}


def nearest_index(coords, value):
    """返回物理值最近的采样下标；等距时取较小的原始下标。

    ``coords`` 可以是递增或反向的坐标。``np.argmin`` 取第一个最小值，
    正好就是「等距时取较小原始下标」这条约定，反向坐标也一并覆盖。
    """

    values = np.asarray(coords, dtype=np.float64).reshape(-1)
    if values.size == 0:
        return 0

    distances = np.abs(values - float(value))
    finite = np.isfinite(distances)
    if not finite.any():
        return 0
    distances = np.where(finite, distances, np.inf)
    return int(np.argmin(distances))


def physical_decimals(step):
    """按坐标步长给出数值框需要的位数（步长细时不要只留两位小数）。"""

    try:
        step = abs(float(step))
    except (TypeError, ValueError):
        return 4
    if not math.isfinite(step) or step <= 0:
        return 4
    return int(min(9, max(3, int(math.ceil(-math.log10(step))) + 1)))


class AxisSpace:
    """一根轴的物理空间描述：范围、采样坐标、显示标签与单位。

    单位只复用数据元信息：文件声明了就用，没声明就不猜。坐标本身是索引
    回退出来的（``coord_sources`` 标为 ``index``）时，标签明确标注 ``index``。
    """

    __slots__ = ("key", "label", "unit", "source", "coords", "minimum", "maximum", "step")

    def __init__(self, key, coords, *, label=None, unit=None, source="file"):
        values = np.asarray(coords, dtype=np.float64).reshape(-1)
        if values.size == 0:
            values = np.zeros(1, dtype=np.float64)

        key = str(key)
        self.key = key
        self.label = label or AXIS_DISPLAY_LABELS.get(key, key)
        self.unit = (unit or "").strip()
        self.source = str(source or "file")
        self.coords = values

        finite = values[np.isfinite(values)]
        if finite.size == 0:
            finite = np.array([0.0])
        self.minimum = float(np.min(finite))
        self.maximum = float(np.max(finite))
        if math.isclose(self.minimum, self.maximum):
            self.maximum = self.minimum + 0.01
        self.step = self._spacing(finite)

    @property
    def decimals(self):
        return physical_decimals(self.step)

    @property
    def sample_count(self):
        return int(self.coords.size)

    @property
    def describes_index(self):
        return self.source == "index"

    @property
    def display_label(self):
        """位置滑条前的说明文字，例如 ``kx / Å⁻¹``、``kx (index)``。"""

        text = self.label
        if self.describes_index:
            return f"{text} (index)"
        if self.unit:
            return f"{text} / {self.unit}"
        return text

    @property
    def physical_span(self):
        return self.maximum - self.minimum

    def as_interval(self, *, low=None, up=None, locked=False):
        return AxisInterval(
            self.minimum,
            self.maximum,
            low=low,
            up=up,
            locked=locked,
            axis_key=self.key,
        )

    @staticmethod
    def _spacing(finite):
        values = np.asarray(finite, dtype=np.float64).reshape(-1)
        if values.size < 2:
            return 1.0
        diffs = np.abs(np.diff(values))
        diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
        if diffs.size == 0:
            return 1.0
        return float(np.median(diffs))


class AxisInterval:
    """一根轴上的物理积分区间。"""

    __slots__ = ("_axis_key", "_minimum", "_maximum", "_low", "_up", "_locked")

    def __init__(self, minimum, maximum, low=None, up=None, *, locked=False, axis_key=None):
        minimum = float(minimum)
        maximum = float(maximum)
        if not math.isfinite(minimum) or not math.isfinite(maximum) or maximum < minimum:
            raise ValueError(f"无效的轴范围: minimum={minimum!r}, maximum={maximum!r}")

        self._axis_key = None if axis_key is None else str(axis_key)
        self._minimum = minimum
        self._maximum = maximum
        self._locked = bool(locked)
        self._low = minimum if low is None else self._clamp_endpoint(low, minimum, maximum)
        self._up = maximum if up is None else self._clamp_endpoint(up, minimum, maximum)
        self._normalize()

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------
    @property
    def axis_key(self):
        return self._axis_key

    @property
    def minimum(self):
        return self._minimum

    @property
    def maximum(self):
        return self._maximum

    @property
    def low(self):
        return self._low

    @property
    def up(self):
        return self._up

    @property
    def locked(self):
        return self._locked

    @property
    def center(self):
        return (self._low + self._up) / 2.0

    @property
    def length(self):
        return self._up - self._low

    @property
    def span(self):
        return self._maximum - self._minimum

    @property
    def is_full_range(self):
        return math.isclose(self._low, self._minimum) and math.isclose(self._up, self._maximum)

    # ------------------------------------------------------------------
    # 修改
    # ------------------------------------------------------------------
    def set_up(self, value):
        """修改上限；锁定时按上限位移整体平移。"""
        value = self._coerce(value)
        if value is None:
            return False
        if self._locked:
            return self._translate(value - self._up)
        # 未锁定：下限固定，端点不能越过另一端。
        return self._assign(up=self._clamp_endpoint(value, self._low, self._maximum))

    def set_low(self, value):
        """修改下限；锁定时按下限位移整体平移。"""
        value = self._coerce(value)
        if value is None:
            return False
        if self._locked:
            return self._translate(value - self._low)
        return self._assign(low=self._clamp_endpoint(value, self._minimum, self._up))

    def set_center(self, value):
        """保持长度整体平移。"""
        value = self._coerce(value)
        if value is None:
            return False
        return self._translate(value - self.center)

    def set_length(self, value):
        """以当前中心向两侧调整长度；触边时平移整个区间容纳目标长度。"""
        value = self._coerce(value)
        if value is None:
            return False

        target = min(max(value, 0.0), self.span)
        center = self.center
        low = center - target / 2.0
        up = center + target / 2.0
        if low < self._minimum:
            shift = self._minimum - low
            low += shift
            up += shift
        elif up > self._maximum:
            shift = up - self._maximum
            low -= shift
            up -= shift
        return self._assign(low=low, up=up)

    def set_locked(self, locked):
        locked = bool(locked)
        if self._locked == locked:
            return False
        self._locked = locked
        return True

    def set_bounds(self, minimum, maximum, *, reset=False):
        """换轴或换数据：更新物理跨度，必要时整体重置为完整范围。"""
        minimum = float(minimum)
        maximum = float(maximum)
        if not math.isfinite(minimum) or not math.isfinite(maximum) or maximum < minimum:
            raise ValueError(f"无效的轴范围: minimum={minimum!r}, maximum={maximum!r}")

        changed = not (math.isclose(minimum, self._minimum) and math.isclose(maximum, self._maximum))
        self._minimum = minimum
        self._maximum = maximum

        if reset:
            changed = changed or not self.is_full_range
            self._low = minimum
            self._up = maximum
            return changed

        previous = (self._low, self._up)
        self._normalize()
        return changed or previous != (self._low, self._up)

    def set_axis(self, axis_key, minimum, maximum, *, reset=True):
        """绑定到一根新轴；默认重置为完整范围并解锁。"""
        self._axis_key = None if axis_key is None else str(axis_key)
        changed = self.set_bounds(minimum, maximum, reset=reset)
        if reset and self._locked:
            self._locked = False
            changed = True
        return changed

    # ------------------------------------------------------------------
    # 采样点映射
    # ------------------------------------------------------------------
    def to_indices(self, coords):
        """两端映射到最近采样点，按原始下标排序后返回包含两端的闭区间。"""

        low_idx = nearest_index(coords, self._low)
        up_idx = nearest_index(coords, self._up)
        if low_idx > up_idx:
            low_idx, up_idx = up_idx, low_idx
        return low_idx, up_idx

    def single_index(self, coords):
        """零长度区间（单层采样）的切片下标。"""

        return nearest_index(coords, self.center)

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    def as_dict(self):
        return {
            "axis_key": self._axis_key,
            "minimum": self._minimum,
            "maximum": self._maximum,
            "low": self._low,
            "up": self._up,
            "locked": self._locked,
        }

    @classmethod
    def from_dict(cls, state, *, default_axis_key=None):
        """从页面参数恢复；字段缺失或损坏时退回完整范围、未锁定。"""

        if not isinstance(state, dict):
            return None
        minimum = _finite_or_none(state.get("minimum"))
        maximum = _finite_or_none(state.get("maximum"))
        if minimum is None or maximum is None or maximum < minimum:
            return None
        return cls(
            minimum,
            maximum,
            low=_finite_or_none(state.get("low")),
            up=_finite_or_none(state.get("up")),
            locked=bool(state.get("locked", False)),
            axis_key=state.get("axis_key", default_axis_key),
        )

    @classmethod
    def from_indices(cls, coords, low_idx, up_idx, *, locked=False, axis_key=None):
        """从旧的整数索引范围恢复物理区间（旧页面迁移用）。

        旧 ``locked_half_width`` 不在这里解释成新锁定状态：调用方显式传
        ``locked``，默认未锁定。
        """

        values = np.asarray(coords, dtype=np.float64).reshape(-1)
        if values.size == 0:
            return None
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            return None

        low_idx = int(np.clip(int(low_idx), 0, values.size - 1))
        up_idx = int(np.clip(int(up_idx), 0, values.size - 1))
        return cls(
            float(np.min(finite)),
            float(np.max(finite)),
            low=float(values[min(low_idx, up_idx)]),
            up=float(values[max(low_idx, up_idx)]),
            locked=bool(locked),
            axis_key=axis_key,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _normalize(self):
        self._low = self._clamp_endpoint(self._low, self._minimum, self._maximum)
        self._up = self._clamp_endpoint(self._up, self._minimum, self._maximum)
        if self._low > self._up:
            self._low, self._up = self._up, self._low

    def _translate(self, delta):
        delta = self._coerce(delta)
        if delta is None or delta == 0.0:
            return False
        # 整体平移触边时限制位移，两端一起停，长度不变。
        if delta > 0:
            delta = min(delta, self._maximum - self._up)
        else:
            delta = max(delta, self._minimum - self._low)
        if delta == 0.0:
            return False
        return self._assign(low=self._low + delta, up=self._up + delta)

    def _assign(self, *, low=None, up=None):
        new_low = self._low if low is None else low
        new_up = self._up if up is None else up
        new_low = self._clamp_endpoint(new_low, self._minimum, self._maximum)
        new_up = self._clamp_endpoint(new_up, self._minimum, self._maximum)
        if new_low > new_up:
            new_low, new_up = new_up, new_low
        if new_low == self._low and new_up == self._up:
            return False
        self._low = new_low
        self._up = new_up
        return True

    @staticmethod
    def _clamp_endpoint(value, minimum, maximum):
        value = float(value)
        if not math.isfinite(value):
            return minimum
        return min(max(value, minimum), maximum)

    @staticmethod
    def _coerce(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None


def _finite_or_none(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None
