import time
import unittest

from PyQt5.QtCore import QCoreApplication

from refresh_pipeline import ComputeResult, RefreshCause, RefreshCoordinator, RenderQuality


def process_until(predicate, timeout=1.5):
    app = QCoreApplication.instance() or QCoreApplication([])
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.002)
    return False


class RefreshCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_interactive_pair_dispatches_preview_then_exact(self):
        coordinator = RefreshCoordinator(preview_interval_ms=12, exact_idle_ms=35)
        dispatched = []
        coordinator.dispatch_requested.connect(lambda request: dispatched.append(request.quality))
        preview, exact = coordinator.make_quality_pair("home", RefreshCause.ANALYSIS)
        coordinator.schedule_interactive(preview, exact)
        self.assertTrue(process_until(lambda: len(dispatched) == 2))
        self.assertEqual(dispatched, [RenderQuality.PREVIEW, RenderQuality.EXACT])
        coordinator.shutdown()

    def test_one_interaction_can_use_a_faster_preview_interval(self):
        coordinator = RefreshCoordinator(preview_interval_ms=66, exact_idle_ms=180)
        preview, exact = coordinator.make_quality_pair("home", RefreshCause.ANALYSIS)

        coordinator.schedule_interactive(
            preview,
            exact,
            preview_interval_ms=33,
        )

        self.assertEqual(coordinator._preview_timer.interval(), 33)
        self.assertEqual(coordinator.preview_interval_ms, 66)
        coordinator.shutdown()

    def test_latest_preview_wins_during_66ms_coalescing(self):
        coordinator = RefreshCoordinator(preview_interval_ms=20, exact_idle_ms=50)
        dispatched = []
        coordinator.dispatch_requested.connect(lambda request: dispatched.append(request.generation))
        first = coordinator.make_request("home", RefreshCause.DATA_SOURCE, RenderQuality.PREVIEW)
        coordinator.schedule_preview(first)
        second = coordinator.make_request("home", RefreshCause.DATA_SOURCE, RenderQuality.PREVIEW)
        coordinator.schedule_preview(second)
        self.assertTrue(process_until(lambda: bool(dispatched)))
        self.assertEqual(dispatched, [second.generation])
        coordinator.shutdown()

    def test_running_task_keeps_only_latest_pending_result(self):
        coordinator = RefreshCoordinator()
        delivered = []
        coordinator.result_ready.connect(lambda request, _result: delivered.append(request.generation))
        first = coordinator.make_request("home", RefreshCause.ROTATION, RenderQuality.EXACT)
        coordinator.submit_compute(
            first,
            lambda: (time.sleep(0.04) or ComputeResult(value=1)),
        )
        second = coordinator.make_request("home", RefreshCause.ROTATION, RenderQuality.EXACT)
        coordinator.submit_compute(second, lambda: ComputeResult(value=2))
        self.assertTrue(process_until(lambda: coordinator._running is None and delivered))
        self.assertEqual(delivered, [second.generation])
        coordinator.shutdown()


if __name__ == "__main__":
    unittest.main()
