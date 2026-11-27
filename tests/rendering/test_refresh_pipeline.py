import time
import unittest
from unittest.mock import patch

from PyQt5.QtCore import QCoreApplication

from bandscope.rendering.refresh_pipeline import ComputeResult, RefreshCause, RefreshCoordinator, RenderQuality


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

    def test_next_preview_delay_is_measured_from_previous_frame_start(self):
        coordinator = RefreshCoordinator(preview_interval_ms=250, exact_idle_ms=1000)
        coordinator._last_preview_started_at = 100.0
        request = coordinator.make_request("home", RefreshCause.TRANSFER_FUNCTION, RenderQuality.PREVIEW)
        with patch("bandscope.rendering.refresh_pipeline.time.monotonic", return_value=100.2):
            coordinator.schedule_preview(request)
        self.assertEqual(coordinator._preview_timer.interval(), 50)
        coordinator._preview_timer.stop()
        with patch("bandscope.rendering.refresh_pipeline.time.monotonic", return_value=100.4):
            coordinator.schedule_preview(request)
        self.assertEqual(coordinator._preview_timer.interval(), 0)
        coordinator.shutdown()

    def test_exact_frame_cancels_older_pending_preview(self):
        coordinator = RefreshCoordinator(preview_interval_ms=250, exact_idle_ms=30)
        dispatched = []
        coordinator.dispatch_requested.connect(lambda request: dispatched.append(request.quality))
        preview, exact = coordinator.make_quality_pair("home", RefreshCause.TRANSFER_FUNCTION)
        coordinator.schedule_interactive(preview, exact)
        self.assertTrue(process_until(lambda: bool(dispatched)))
        self.assertEqual(dispatched, [RenderQuality.EXACT])
        self.assertFalse(coordinator._preview_timer.isActive())
        self.assertIsNone(coordinator._pending_preview)
        coordinator.shutdown()

    def test_immediate_release_cancels_pending_interactive_preview(self):
        coordinator = RefreshCoordinator(preview_interval_ms=250, exact_idle_ms=1000)
        dispatched = []
        coordinator.dispatch_requested.connect(lambda request: dispatched.append(request.quality))
        preview, exact = coordinator.make_quality_pair("home", RefreshCause.TRANSFER_FUNCTION)
        coordinator.schedule_interactive(preview, exact)
        coordinator.schedule_exact(exact, immediate=True)
        self.assertEqual(dispatched, [RenderQuality.EXACT])
        self.assertFalse(coordinator._preview_timer.isActive())
        self.assertIsNone(coordinator._pending_preview)
        coordinator.shutdown()

    def test_completed_preview_is_presented_while_newer_drag_is_pending(self):
        coordinator = RefreshCoordinator(preview_interval_ms=10_000, exact_idle_ms=10_000)
        delivered = []
        coordinator.result_ready.connect(
            lambda request, _result: delivered.append(request.generation)
        )
        first, _exact = coordinator.make_quality_pair("home", RefreshCause.ROTATION)
        coordinator.submit_compute(
            first,
            lambda: (time.sleep(0.04) or ComputeResult(value=1)),
        )
        second, second_exact = coordinator.make_quality_pair("home", RefreshCause.ROTATION)
        coordinator.schedule_interactive(second, second_exact)

        self.assertTrue(process_until(lambda: coordinator._running is None and delivered))
        self.assertEqual(delivered, [first.generation])
        coordinator.shutdown()

    def test_exact_release_blocks_an_older_preview_result(self):
        coordinator = RefreshCoordinator(preview_interval_ms=10_000, exact_idle_ms=10_000)
        delivered = []
        coordinator.result_ready.connect(
            lambda request, _result: delivered.append(request.generation)
        )
        first, _exact = coordinator.make_quality_pair("home", RefreshCause.ROTATION)
        coordinator.submit_compute(
            first,
            lambda: (time.sleep(0.04) or ComputeResult(value=1)),
        )
        second, second_exact = coordinator.make_quality_pair("home", RefreshCause.ROTATION)
        coordinator.schedule_interactive(second, second_exact)
        coordinator.schedule_exact(second_exact, immediate=True)

        self.assertTrue(process_until(lambda: coordinator._running is None))
        self.assertEqual(delivered, [])
        coordinator.shutdown()

    def test_older_preview_does_not_replace_a_newer_presented_frame(self):
        coordinator = RefreshCoordinator(preview_interval_ms=10_000, exact_idle_ms=10_000)
        delivered = []
        coordinator.result_ready.connect(
            lambda request, _result: delivered.append(request.generation)
        )
        first, _exact = coordinator.make_quality_pair("home", RefreshCause.ROTATION)
        coordinator.submit_compute(
            first,
            lambda: (time.sleep(0.04) or ComputeResult(value=1)),
        )
        second, second_exact = coordinator.make_quality_pair("home", RefreshCause.ROTATION)
        coordinator.schedule_interactive(second, second_exact)
        coordinator.mark_presented(second)

        self.assertTrue(process_until(lambda: coordinator._running is None))
        self.assertEqual(delivered, [])
        coordinator.shutdown()

    def test_late_preview_from_another_interaction_is_not_presented(self):
        coordinator = RefreshCoordinator(preview_interval_ms=10_000, exact_idle_ms=10_000)
        delivered = []
        coordinator.result_ready.connect(
            lambda request, _result: delivered.append(request.generation)
        )
        first, _exact = coordinator.make_quality_pair("home", RefreshCause.ROTATION)
        coordinator.submit_compute(
            first,
            lambda: (time.sleep(0.04) or ComputeResult(value=1)),
        )
        second, second_exact = coordinator.make_quality_pair("home", RefreshCause.DATA_SOURCE)
        coordinator.schedule_interactive(second, second_exact)

        self.assertTrue(process_until(lambda: coordinator._running is None))
        self.assertEqual(delivered, [])
        coordinator.shutdown()

    def test_cancel_blocks_old_preview_but_not_the_next_rotation(self):
        coordinator = RefreshCoordinator(preview_interval_ms=10_000, exact_idle_ms=10_000)
        delivered = []
        coordinator.result_ready.connect(
            lambda request, _result: delivered.append(request.generation)
        )
        first, _exact = coordinator.make_quality_pair("home", RefreshCause.ROTATION)
        coordinator.submit_compute(
            first,
            lambda: (time.sleep(0.04) or ComputeResult(value=1)),
        )
        coordinator.cancel_page("home")
        self.assertTrue(process_until(lambda: coordinator._running is None))
        self.assertEqual(delivered, [])

        second, second_exact = coordinator.make_quality_pair("home", RefreshCause.ROTATION)
        coordinator.submit_compute(
            second,
            lambda: (time.sleep(0.04) or ComputeResult(value=2)),
        )
        third, third_exact = coordinator.make_quality_pair("home", RefreshCause.ROTATION)
        coordinator.schedule_interactive(third, third_exact)
        self.assertTrue(process_until(lambda: coordinator._running is None and delivered))
        self.assertEqual(delivered, [second.generation])
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
