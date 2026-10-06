# -*- coding: utf-8 -*-
"""演示插件的纯数值部分：沿指定轴对二维结果积分。

这里是**工作线程**里跑的全部内容：只依赖 NumPy，不碰 Qt、不碰主窗口，也不持有
快照之外的任何状态。宿主负责取数、排队与建结果页。

数组顺序按宿主约定是 ``[x, y]``：

- ``axis="x"``：对第一维（x）求和，得到沿 **y** 变化的曲线；
- ``axis="y"``：对第二维（y）求和，得到沿 **x** 变化的曲线。

分块求和是为了在长计算里留出取消检查点。单个分块内的求和使用 NumPy 自己的
成对求和，与 ``ndarray.sum(axis=...)`` 逐位一致；分块大小取得足够大，常见的
二维结果（不超过一个分块）与直接 ``sum`` 的结果完全相同。
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from bandscope.extensions.api import AnalysisCancelled, AnalysisCurve1D, CancelToken

#: 每个分块的行/列数。越大越快，越小取消越灵敏。
CHUNK = 1024

#: 支持的积分方向。
AXIS_X = "x"
AXIS_Y = "y"
AXIS_CHOICES = (AXIS_X, AXIS_Y)


def axis_label(axis: str) -> str:
    return "沿 x 轴积分" if axis == AXIS_X else "沿 y 轴积分"


def integrate_along(data, axis: str, *, cancel: Optional[CancelToken] = None, chunk: int = CHUNK):
    """沿 ``axis`` 求和；每个分块前检查一次取消。"""
    values = np.asarray(data, dtype=np.float64)
    if values.ndim != 2:
        raise ValueError("演示插件只处理二维数组。")
    step = max(1, int(chunk))
    if axis == AXIS_X:
        totals = np.zeros(values.shape[1], dtype=np.float64)
        for start in range(0, values.shape[0], step):
            if cancel is not None:
                cancel.raise_if_cancelled()
            totals += values[start : start + step, :].sum(axis=0)
        return totals
    if axis == AXIS_Y:
        totals = np.zeros(values.shape[0], dtype=np.float64)
        for start in range(0, values.shape[1], step):
            if cancel is not None:
                cancel.raise_if_cancelled()
            totals += values[:, start : start + step].sum(axis=1)
        return totals
    raise ValueError(f"未知的积分方向：{axis!r}")


def curve_axes(snapshot, axis: str) -> Tuple[np.ndarray, str, str]:
    """积分结果的横轴：沿 x 积分得到 y 轴，反之亦然。"""
    if axis == AXIS_X:
        return snapshot.y, snapshot.y_axis_label(), f"{snapshot.title}（沿 x 轴积分）"
    return snapshot.x, snapshot.x_axis_label(), f"{snapshot.title}（沿 y 轴积分）"


def run_integral(snapshot, params, cancel: CancelToken) -> AnalysisCurve1D:
    """宿主工作函数：``(快照, 冻结参数, 取消信号) -> AnalysisCurve1D``。

    只读快照、不做任何写入：``data`` 在宿主侧就是只读缓冲区，这里连临时改写都
    不做，原始强度逐位不变。
    """
    axis = str(params.get("axis") or AXIS_X)
    if axis not in AXIS_CHOICES:
        raise ValueError(f"未知的积分方向：{axis!r}")
    totals = integrate_along(snapshot.data, axis, cancel=cancel)
    x_values, x_label, title = curve_axes(snapshot, axis)
    return AnalysisCurve1D(
        x=np.asarray(x_values, dtype=np.float64),
        y=totals,
        x_label=x_label.split(" (")[0],
        x_unit=x_label.partition("(")[2].rstrip(")") if "(" in x_label else "",
        y_label="Intensity",
        y_unit="a.u.",
        title=title,
        params={
            "axis": axis,
            "chunk": CHUNK,
            "source_page": snapshot.page_id,
            "source_title": snapshot.page_title,
            "source_shape": list(snapshot.shape),
            "data_generation": int(snapshot.data_generation),
        },
    )


__all__ = [
    "AXIS_CHOICES",
    "AXIS_X",
    "AXIS_Y",
    "AnalysisCancelled",
    "axis_label",
    "curve_axes",
    "integrate_along",
    "run_integral",
]
