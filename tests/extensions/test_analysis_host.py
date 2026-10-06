# -*- coding: utf-8 -*-
"""阶段 F：分析任务执行器（一个工作线程、四个排队位、每插件一个未结束任务）。"""
from __future__ import annotations

import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from bandscope.extensions.analysis_host import AnalysisTaskRunner
from bandscope.extensions.api import (
    AnalysisCancelled,
    AnalysisCurve1D,
    AnalysisInput2D,
    read_only_array,
)


def make_snapshot(snapshot_id: str = "s1", *, page_id: str = "p1") -> AnalysisInput2D:
    return AnalysisInput2D(
        plugin_id="demo",
        page_id=page_id,
        page_title="二维结果",
        snapshot_id=snapshot_id,
        data_generation=1,
        data=read_only_array([[1.0, 2.0], [3.0, 4.0]]),
        x=read_only_array([0.0, 1.0]),
        y=read_only_array([10.0, 20.0]),
    )


def simple_work(snapshot, params, cancel):
    return AnalysisCurve1D(x=snapshot.x, y=snapshot.y, title="done")


class RunnerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.runner = AnalysisTaskRunner(max_queue=2)
        self.addCleanup(self.runner.shutdown)
        self.succeeded = []
        self.failed = []
        self.cancelled = []
        self.busy = []
        self.runner.succeeded.connect(lambda h, c: self.succeeded.append((h, c)))
        self.runner.failed.connect(lambda h, m: self.failed.append((h, m)))
        self.runner.cancelled.connect(lambda h, m: self.cancelled.append((h, m)))
        self.runner.busy.connect(lambda p, m: self.busy.append((p, m)))

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        QApplication.processEvents()
        return predicate()

    def test_success_returns_handle_and_emits_result(self):
        snapshot = make_snapshot()
        handle = self.runner.submit("demo", snapshot, simple_work, title="t")
        self.assertIsNotNone(handle)
        self.assertTrue(self.wait_for(lambda: self.succeeded))
        result_handle, curve = self.succeeded[0]
        self.assertEqual(result_handle.task_id, handle.task_id)
        self.assertIsInstance(curve, AnalysisCurve1D)

    def test_second_submit_for_the_same_plugin_is_busy(self):
        gate = threading.Event()
        self.addCleanup(gate.set)

        def blocking_work(snapshot, params, cancel):
            gate.wait(5)
            return simple_work(snapshot, params, cancel)

        first = self.runner.submit("demo", make_snapshot(), blocking_work)
        self.assertIsNotNone(first)
        self.assertTrue(self.wait_for(lambda: self.runner.pending_count() == 1))

        second = self.runner.submit("demo", make_snapshot(), simple_work)
        self.assertIsNone(second)
        self.assertTrue(self.wait_for(lambda: self.busy))
        self.assertIn("上一个分析任务", self.busy[0][1])

        gate.set()
        self.assertTrue(self.wait_for(lambda: self.succeeded))

    def test_queue_limit_is_enforced_per_runner(self):
        gate = threading.Event()
        self.addCleanup(gate.set)

        def blocking_work(snapshot, params, cancel):
            gate.wait(5)
            return simple_work(snapshot, params, cancel)

        self.assertIsNotNone(self.runner.submit("a", make_snapshot(), blocking_work))
        self.assertIsNotNone(self.runner.submit("b", make_snapshot(), blocking_work))
        self.assertTrue(self.wait_for(lambda: self.runner.pending_count() == 2))

        self.assertIsNone(self.runner.submit("c", make_snapshot(), simple_work))
        self.assertTrue(self.wait_for(lambda: self.busy))
        self.assertIn("队列已满", self.busy[-1][1])

        gate.set()
        self.assertTrue(self.wait_for(lambda: self.succeeded, timeout=8))

    def test_failure_is_reported_with_type_and_message(self):
        def broken(snapshot, params, cancel):
            raise ValueError("算不动")

        self.runner.submit("demo", make_snapshot(), broken)
        self.assertTrue(self.wait_for(lambda: self.failed))
        self.assertIn("ValueError", self.failed[0][1])
        self.assertIn("算不动", self.failed[0][1])

    def test_cancelling_a_queued_task_never_runs_it(self):
        started = threading.Event()
        gate = threading.Event()
        self.addCleanup(gate.set)
        calls = []

        def blocking_work(snapshot, params, cancel):
            started.set()
            gate.wait(5)
            return simple_work(snapshot, params, cancel)

        def never_work(snapshot, params, cancel):
            calls.append(snapshot.snapshot_id)
            return simple_work(snapshot, params, cancel)

        self.runner.submit("first", make_snapshot("s-first"), blocking_work)
        self.assertTrue(started.wait(5))
        queued = self.runner.submit("demo", make_snapshot("s-queued"), never_work)
        self.assertIsNotNone(queued)

        # 取消只置位：工作线程还堵在第一个任务上，等它让开后才会走到队头。
        self.assertTrue(self.runner.cancel(queued))
        gate.set()
        self.assertTrue(self.wait_for(
            lambda: any(h.task_id == queued.task_id for h, _ in self.cancelled)
        ))
        self.assertEqual(calls, [], "被取消的排队任务不能执行工作函数")

    def test_cancelling_a_running_task_is_cooperative(self):
        entered = threading.Event()

        def cooperative(snapshot, params, cancel):
            entered.set()
            for _ in range(200):
                time.sleep(0.01)
                cancel.raise_if_cancelled()
            return simple_work(snapshot, params, cancel)

        handle = self.runner.submit("demo", make_snapshot(), cooperative)
        self.assertTrue(entered.wait(5))
        self.assertTrue(self.runner.cancel(handle))
        self.assertTrue(self.wait_for(lambda: self.cancelled, timeout=5))
        self.assertEqual(self.succeeded, [])

    def test_cancel_page_cancels_only_that_page(self):
        gate = threading.Event()
        self.addCleanup(gate.set)

        def blocking_work(snapshot, params, cancel):
            gate.wait(5)
            return simple_work(snapshot, params, cancel)

        page_a = self.runner.submit("alpha", make_snapshot("s1", page_id="page-a"), blocking_work)
        page_b = self.runner.submit("beta", make_snapshot("s2", page_id="page-b"), blocking_work)
        self.assertTrue(self.wait_for(lambda: self.runner.pending_count() == 2))

        # 只取消 page-a：另一页的任务不受影响，也不会整队列一起取消。
        self.assertEqual(self.runner.cancel_page("page-a"), 1)
        with self.runner._lock:
            self.assertTrue(self.runner._tokens[page_a.task_id].cancelled)
            self.assertFalse(self.runner._tokens[page_b.task_id].cancelled)
        self.assertEqual(self.runner.cancel_page("page-missing"), 0)

        gate.set()
        self.assertTrue(self.wait_for(lambda: self.succeeded, timeout=8))

    def test_shutdown_stops_the_worker_and_refuses_new_tasks(self):
        def slow(snapshot, params, cancel):
            for _ in range(400):
                time.sleep(0.005)
                cancel.raise_if_cancelled()
            return simple_work(snapshot, params, cancel)

        self.runner.submit("demo", make_snapshot(), slow)
        self.assertTrue(self.wait_for(lambda: self.runner.pending_count() == 1))
        self.runner.shutdown(wait_ms=3000)
        self.assertIsNone(self.runner.submit("demo", make_snapshot(), simple_work))

    def test_missing_analysis_cancel_does_not_force_threads(self):
        # 取消只置位，不结束线程；超时后让步返回，不阻塞界面。
        entered = threading.Event()

        def stubborn(snapshot, params, cancel):
            entered.set()
            time.sleep(0.4)
            return simple_work(snapshot, params, cancel)

        handle = self.runner.submit("demo", make_snapshot(), stubborn)
        self.assertTrue(entered.wait(5))
        self.assertEqual(self.runner.cancel(handle), True)
        start = time.monotonic()
        self.runner.shutdown(wait_ms=50)
        self.assertLess(time.monotonic() - start, 3.0)


if __name__ == "__main__":
    unittest.main()
