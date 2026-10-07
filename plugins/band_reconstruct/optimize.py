# -*- coding: utf-8 -*-
"""逐带重构：L-BFGS-B 驱动、收敛记录与协作取消。

- 每条带独立优化：体数据与 η 固定，优化变量是该带能量面的全部网格点，上下界取
  能量轴量程；
- **逐带顺序**执行，共享同一个 :class:`~plugins.band_reconstruct.mrf_loss.BandProblem`
  （体数据只建一次取样器）；带之间互相不做扣减（deflation），与 fuller 的基础
  流程一致；
- 取消走宿主 ``CancelToken`` 的 ``raise_if_cancelled()``：迭代回调与目标函数里
  都有检查点，所以「还没开始的任务」和「正在跑的任务」都能及时退出；本模块不
  依赖 Qt，异常类型由令牌自己给出（宿主那边就是 ``AnalysisCancelled``）；
- 收敛历史与迭代次数进 :class:`BandResult`，面板与交接文档都用它。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize

from .mrf_loss import BandProblem

__all__ = [
    "BandResult",
    "reconstruct_band",
    "reconstruct_bands",
]

#: 默认最大迭代次数（fuller 参考预算为 100 epoch，这里给 L-BFGS-B 留出余量）。
DEFAULT_MAXITER = 200

#: 传给 L-BFGS-B 的默认容差；比 scipy 默认更严一档，配合最多 200 次迭代。
DEFAULT_OPTIONS: Dict[str, Any] = {
    "ftol": 1e-12,
    "gtol": 1e-10,
    "maxls": 30,
}


def _cancel_hook(cancel) -> Callable[[], None]:
    """把取消令牌转成无参检查函数；令牌必须提供 ``raise_if_cancelled()``。"""
    if cancel is None:
        return lambda: None
    checker = getattr(cancel, "raise_if_cancelled", None)
    if not callable(checker):
        raise TypeError(
            "cancel 必须是提供 raise_if_cancelled() 的取消令牌（如宿主 CancelToken）。"
        )
    return checker


@dataclass
class BandResult:
    """一条带的重构结果与收敛记录。"""

    index: int
    label: str
    surface: np.ndarray
    initial: np.ndarray
    loss: float
    loss_history: Tuple[float, ...]
    n_iter: int
    n_eval: int
    elapsed: float
    success: bool
    message: str
    eta: float
    bounds: Tuple[float, float]
    params: Dict[str, Any] = field(default_factory=dict)

    @property
    def tail_delta(self) -> float:
        """最后两次迭代的损失变化。

        ``success`` 只反映 L-BFGS-B 自己的终止状态：达到迭代上限时它是 False，
        但损失可能已经平掉（本问题是病态尺度，尾部变化常在 ``1e-3`` 量级以下）。
        判断"是否收敛"看这个值，而不是只看 ``success``。
        """
        if len(self.loss_history) < 2:
            return float("nan")
        return float(self.loss_history[-1] - self.loss_history[-2])

    def as_dict(self) -> Dict[str, Any]:
        """可序列化摘要（不含数组本身）。"""
        return {
            "index": int(self.index),
            "label": self.label,
            "loss": float(self.loss),
            "n_iter": int(self.n_iter),
            "n_eval": int(self.n_eval),
            "elapsed": float(self.elapsed),
            "success": bool(self.success),
            "message": self.message,
            "eta": float(self.eta),
            "bounds": [float(item) for item in self.bounds],
            **dict(self.params),
        }


def reconstruct_band(
    problem: BandProblem,
    initial,
    *,
    index: int = 0,
    label: str = "",
    maxiter: int = DEFAULT_MAXITER,
    bounds: Optional[Tuple[float, float]] = None,
    cancel=None,
    on_iteration: Optional[Callable[[int, float], None]] = None,
    options: Optional[Mapping[str, Any]] = None,
) -> BandResult:
    """从初始化面出发优化一条带，返回带收敛记录的 :class:`BandResult`。"""
    check_cancel = _cancel_hook(cancel)
    shape = problem.shape
    start_surface = np.asarray(initial, dtype=np.float64).reshape(shape)
    if not np.all(np.isfinite(start_surface)):
        raise ValueError("初始化面含非有限值。")

    low, high = problem.energy_bounds()
    if bounds is None:
        bounds = (low, high)
    lo, hi = float(bounds[0]), float(bounds[1])
    if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
        raise ValueError(f"能量面上下界无效（需要 下界 < 上界）：{bounds!r}")
    # 初始化面按上下界夹一次：起点必须在可行域内，L-BFGS-B 不会替你修正。
    start_surface = np.clip(start_surface, lo, hi)

    margins = {**DEFAULT_OPTIONS, **dict(options or {})}
    margins["maxiter"] = int(maxiter)

    state: Dict[str, Any] = {"loss": float("nan"), "history": []}

    def objective(flat):
        check_cancel()
        terms = problem.energy_terms(flat.reshape(shape))
        state["loss"] = terms.total
        return terms.total, terms.gradient.reshape(-1)

    def callback(flat):
        loss = state["loss"]
        if np.isfinite(loss):
            state["history"].append(float(loss))
        check_cancel()
        if on_iteration is not None:
            on_iteration(len(state["history"]), float(loss))

    started = time.perf_counter()
    result = minimize(
        objective,
        start_surface.reshape(-1),
        jac=True,
        method="L-BFGS-B",
        bounds=[(lo, hi)] * start_surface.size,
        callback=callback,
        options=margins,
    )
    elapsed = time.perf_counter() - started

    surface = np.asarray(result.x, dtype=np.float64).reshape(shape)
    return BandResult(
        index=int(index),
        label=str(label or f"Band {index + 1}"),
        surface=surface,
        initial=start_surface.copy(),
        loss=float(result.fun),
        loss_history=tuple(state["history"]),
        n_iter=int(getattr(result, "nit", 0)),
        n_eval=int(getattr(result, "nfev", 0)),
        elapsed=float(elapsed),
        success=bool(result.success),
        message=str(result.message or ""),
        eta=float(problem.eta),
        bounds=(lo, hi),
        params={"floor": float(problem.floor), "shape": [int(item) for item in shape]},
    )


def reconstruct_bands(
    volume,
    e_values,
    initials: Sequence[np.ndarray],
    *,
    eta: float = 0.1,
    floor: Optional[float] = None,
    labels: Optional[Sequence[str]] = None,
    maxiter: int = DEFAULT_MAXITER,
    bounds: Optional[Tuple[float, float]] = None,
    cancel=None,
    on_band_start: Optional[Callable[[int, str], None]] = None,
    on_iteration: Optional[Callable[[int, int, float], None]] = None,
    options: Optional[Mapping[str, Any]] = None,
) -> List[BandResult]:
    """逐带顺序重构；``volume`` 必须是**已预处理**的体数据。

    :param on_band_start: ``(index, label)``，某条带开始前回调；
    :param on_iteration: ``(band_index, iteration, loss)``，每次迭代回调（面板进度
        由调用方节流，本模块不节流）。
    """
    starts = [np.asarray(item, dtype=np.float64) for item in initials]
    if not starts:
        raise ValueError("至少需要一条带的初始化面。")
    problem = BandProblem(volume, e_values, eta=eta, floor=floor)
    shape = problem.shape
    for position, start in enumerate(starts):
        if start.shape != shape:
            raise ValueError(
                f"第 {position + 1} 条初始化面形状 {start.shape} 与体数据动量网格 {shape} 不一致。"
            )

    results: List[BandResult] = []
    for position, start in enumerate(starts):
        label = ""
        if labels is not None and position < len(labels):
            label = str(labels[position])
        label = label or f"Band {position + 1}"
        if on_band_start is not None:
            on_band_start(position, label)

        def _iteration(iteration: int, loss: float, _position=position) -> None:
            if on_iteration is not None:
                on_iteration(_position, iteration, loss)

        result = reconstruct_band(
            problem,
            start,
            index=position,
            label=label,
            maxiter=maxiter,
            bounds=bounds,
            cancel=cancel,
            on_iteration=_iteration,
            options=options,
        )
        results.append(result)
    return results
