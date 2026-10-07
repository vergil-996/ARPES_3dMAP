# -*- coding: utf-8 -*-
"""宿主管理的分析任务执行器（阶段 F）。

一个工作线程、最多四个排队任务、每个插件最多一个未结束任务。它**不**复用
``RefreshCoordinator``——那里只有一个待执行槽，塞进插件任务会把渲染请求挤掉。

线程模型：

- 纯数值工作函数在工作线程里跑；工作线程不碰任何 Qt 控件。
- 完成/失败/取消通过信号回到主线程，由宿主在主线程建结果页。
- 取消是协作式的：还没开始的任务直接出队，正在跑的任务在插件自己的检查点上退出。
  绝不强制终止 Python 线程，也绝不从工作线程访问已销毁的控件。
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Callable, Dict, Mapping, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from bandscope.extensions.api import (
    MAX_ANALYSIS_QUEUE,
    AnalysisCancelled,
    AnalysisInput2D,
    AnalysisTaskHandle,
    CancelToken,
)


class _Task:
    __slots__ = ("handle", "work", "snapshot", "params", "token")

    def __init__(self, handle, work, snapshot, params, token):
        self.handle = handle
        self.work = work
        self.snapshot = snapshot
        self.params = params
        self.token = token


class AnalysisTaskRunner(QObject):
    """分析任务队列。信号一律在主线程投递。"""

    #: 任务成功：(handle, 校验通过的结果)
    succeeded = pyqtSignal(object, object)
    #: 任务失败：(handle, 可展示的原因)
    failed = pyqtSignal(object, str)
    #: 任务取消：(handle, 原因)
    cancelled = pyqtSignal(object, str)
    #: 提交被拒绝：(plugin_id, 可展示的原因)
    busy = pyqtSignal(str, str)
    #: 进度：(handle, 0–1, 说明)；由工作线程发出，宿主在主线程收。
    progress = pyqtSignal(object, float, str)

    #: 进度节流间隔（秒）：工作线程可能每个迭代都上报，界面只按这个频率更新。
    progress_interval = 0.2

    def __init__(self, parent=None, *, max_queue: int = MAX_ANALYSIS_QUEUE):
        super().__init__(parent)
        self.max_queue = int(max_queue)
        self._queue: "queue.Queue[Optional[_Task]]" = queue.Queue()
        self._lock = threading.Lock()
        #: plugin_id -> handle，表示该插件有一个未结束（排队或运行中）的任务。
        self._unfinished: Dict[str, AnalysisTaskHandle] = {}
        self._tokens: Dict[str, CancelToken] = {}
        self._closing = False
        self._thread: Optional[threading.Thread] = None

    # -- 查询 -----------------------------------------------------------
    def pending_count(self) -> int:
        with self._lock:
            return len(self._unfinished)

    def busy_reason(self, plugin_id: str) -> str:
        """该插件现在能不能提交新任务；能则返回空串。"""
        with self._lock:
            if plugin_id in self._unfinished:
                return "上一个分析任务还没有结束，请等它完成或取消后再试。"
            if len(self._unfinished) >= self.max_queue:
                return f"分析任务队列已满（最多 {self.max_queue} 个），请稍后再试。"
        return ""

    # -- 提交与取消 ------------------------------------------------------
    def submit(
        self,
        plugin_id: str,
        snapshot: AnalysisInput2D,
        work: Callable,
        *,
        title: str = "",
        params: Optional[Mapping] = None,
    ) -> Optional[AnalysisTaskHandle]:
        """登记并排队一次分析；忙碌时提示用户并返回 None。"""
        reason = self.busy_reason(plugin_id)
        if reason:
            self.busy.emit(plugin_id, reason)
            return None
        handle = AnalysisTaskHandle(
            task_id=uuid.uuid4().hex,
            plugin_id=plugin_id,
            page_id=snapshot.page_id,
            snapshot_id=snapshot.snapshot_id,
            title=str(title or ""),
        )
        token = CancelToken(progress_sink=self._make_progress_sink(handle))
        with self._lock:
            if self._closing:
                return None
            self._unfinished[plugin_id] = handle
            self._tokens[handle.task_id] = token
        self._queue.put(
            _Task(handle, work, snapshot, dict(params or {}), token)
        )
        self._ensure_worker()
        return handle

    def _make_progress_sink(self, handle: AnalysisTaskHandle) -> Callable[[float, str], None]:
        """给令牌一个节流的进度接收器。

        工作线程里可能每个迭代都上报；这里按 :attr:`progress_interval` 节流后再发
        信号（100% 与最后一条说明永远放行），避免界面被高频事件淹没。
        """
        state = {"last": 0.0}
        lock = threading.Lock()

        def sink(fraction: float, message: str) -> None:
            now = time.monotonic()
            with lock:
                if fraction < 1.0 and now - state["last"] < self.progress_interval:
                    return
                state["last"] = now
            if not self._closing:
                self.progress.emit(handle, float(fraction), str(message))

        return sink

    def cancel(self, handle: Optional[AnalysisTaskHandle]) -> bool:
        """请求取消一个任务；返回是否确实登记了取消。"""
        if handle is None:
            return False
        with self._lock:
            token = self._tokens.get(handle.task_id)
            known = self._unfinished.get(handle.plugin_id) == handle
        if token is None or not known:
            return False
        token.cancel()
        return True

    def cancel_page(self, page_id: str) -> int:
        """来源页关闭时取消它名下所有任务。"""
        key = str(page_id)
        return self._cancel_where(lambda handle: handle.page_id == key)

    def cancel_plugin(self, plugin_id: str) -> int:
        key = str(plugin_id)
        return self._cancel_where(lambda handle: handle.plugin_id == key)

    def cancel_all(self) -> int:
        return self._cancel_where(lambda _handle: True)

    def _cancel_where(self, predicate) -> int:
        """按句柄筛选出未结束任务的取消令牌；令牌一置位，排队任务就不会执行。"""
        with self._lock:
            tokens = [
                self._tokens[handle.task_id]
                for handle in self._unfinished.values()
                if handle.task_id in self._tokens and predicate(handle)
            ]
        for token in tokens:
            token.cancel()
        return len(tokens)

    # -- 工作线程 --------------------------------------------------------
    def _ensure_worker(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="bandscope-analysis", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                return
            if task.token.cancelled:
                # 排队期间已被取消：不执行工作函数，直接回报。
                self._finish(task)
                self._emit_cancelled(task, "分析任务已取消。")
                continue
            try:
                result = task.work(task.snapshot, dict(task.params), task.token)
            except AnalysisCancelled:
                self._finish(task)
                self._emit_cancelled(task, "分析任务已取消。")
            except Exception as exc:
                self._finish(task)
                self._emit_failed(task, f"{type(exc).__name__}: {exc}")
            else:
                cancelled = task.token.cancelled
                self._finish(task)
                if cancelled:
                    self._emit_cancelled(task, "分析任务已取消。")
                else:
                    self._emit_succeeded(task, result)

    def _finish(self, task: _Task) -> None:
        with self._lock:
            if self._unfinished.get(task.handle.plugin_id) == task.handle:
                self._unfinished.pop(task.handle.plugin_id, None)
            self._tokens.pop(task.handle.task_id, None)

    # -- 回调（信号只在没进入关闭流程时发出） -----------------------------
    def _emit_succeeded(self, task, result) -> None:
        if not self._closing:
            self.succeeded.emit(task.handle, result)

    def _emit_failed(self, task, message) -> None:
        if not self._closing:
            self.failed.emit(task.handle, message)

    def _emit_cancelled(self, task, reason) -> None:
        if not self._closing:
            self.cancelled.emit(task.handle, reason)

    # -- 关闭 -----------------------------------------------------------
    def shutdown(self, wait_ms: int = 2000) -> None:
        """停止接受新任务并等待当前任务退出；不强制结束线程。"""
        with self._lock:
            self._closing = True
            tokens = list(self._tokens.values())
        for token in tokens:
            token.cancel()
        self._queue.put(None)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0, wait_ms) / 1000.0)
        self._thread = None
