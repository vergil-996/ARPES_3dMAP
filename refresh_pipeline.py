from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from PyQt5.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal, pyqtSlot


class RenderQuality(Enum):
    PREVIEW = "preview"
    EXACT = "exact"


class RefreshCause(Enum):
    DATA_SOURCE = auto()
    ANALYSIS = auto()
    ROTATION = auto()
    TRANSFER_FUNCTION = auto()
    GEOMETRY = auto()
    OVERLAY = auto()
    PAGE_ACTIVATION = auto()


@dataclass(frozen=True)
class RefreshRequest:
    generation: int
    page_id: str
    cause: RefreshCause
    quality: RenderQuality
    snapshot: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComputeJob:
    operation: str
    payload: Mapping[str, Any]


@dataclass
class ComputeResult:
    value: Any
    backend: str = "CPU"
    metadata: Dict[str, Any] = field(default_factory=dict)


class _WorkerSignals(QObject):
    finished = pyqtSignal(object, object)
    failed = pyqtSignal(object, object)


class _ComputeTask(QRunnable):
    def __init__(self, request: RefreshRequest, work: Callable[[], ComputeResult]):
        super().__init__()
        self.request = request
        self.work = work
        self.signals = _WorkerSignals()

    @pyqtSlot()
    def run(self):
        try:
            result = self.work()
        except Exception as exc:  # pragma: no cover - exercised through the signal path
            self.signals.failed.emit(self.request, exc)
            return
        self.signals.finished.emit(self.request, result)


class RefreshCoordinator(QObject):
    """Coalesce UI refreshes and run at most one numerical job at a time.

    VTK/Qt rendering is deliberately not performed here.  The coordinator only
    emits requests/results back to the GUI thread.
    """

    dispatch_requested = pyqtSignal(object)
    result_ready = pyqtSignal(object, object)
    failed = pyqtSignal(object, str)
    status_changed = pyqtSignal(str, str)

    def __init__(self, parent=None, *, preview_interval_ms=66, exact_idle_ms=180):
        super().__init__(parent)
        self.preview_interval_ms = int(preview_interval_ms)
        self.exact_idle_ms = int(exact_idle_ms)
        self._generation = 0
        self._latest_generation: Dict[str, int] = {}
        self._pending_preview: Optional[RefreshRequest] = None
        self._pending_exact: Optional[RefreshRequest] = None
        self._running: Optional[Tuple[RefreshRequest, _ComputeTask]] = None
        self._pending_compute: Optional[Tuple[RefreshRequest, Callable[[], ComputeResult]]] = None

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._dispatch_preview)

        self._exact_timer = QTimer(self)
        self._exact_timer.setSingleShot(True)
        self._exact_timer.timeout.connect(self._dispatch_exact)

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

    def make_request(self, page_id, cause, quality, snapshot=None):
        self._generation += 1
        return self._make_request_with_generation(
            self._generation, page_id, cause, quality, snapshot
        )

    def make_quality_pair(self, page_id, cause, snapshot=None):
        """Create PREVIEW/EXACT requests for one logical UI interaction."""
        self._generation += 1
        generation = self._generation
        preview = self._make_request_with_generation(
            generation, page_id, cause, RenderQuality.PREVIEW, snapshot
        )
        exact = self._make_request_with_generation(
            generation, page_id, cause, RenderQuality.EXACT, snapshot
        )
        return preview, exact

    def _make_request_with_generation(self, generation, page_id, cause, quality, snapshot):
        request = RefreshRequest(
            generation=int(generation),
            page_id=str(page_id or "home"),
            cause=cause,
            quality=quality,
            snapshot=dict(snapshot or {}),
        )
        self._latest_generation[request.page_id] = request.generation
        return request

    def schedule_interactive(self, preview_request, exact_request):
        if preview_request.generation != exact_request.generation:
            raise ValueError("Interactive preview and exact requests must share a generation.")
        self._latest_generation[preview_request.page_id] = preview_request.generation
        self._pending_preview = preview_request
        self._pending_exact = exact_request
        if not self._preview_timer.isActive():
            self._preview_timer.start(self.preview_interval_ms)
        self._exact_timer.start(self.exact_idle_ms)
        self.status_changed.emit("preview", "")

    def schedule_preview(self, request: RefreshRequest):
        self._latest_generation[request.page_id] = request.generation
        self._pending_preview = request
        if not self._preview_timer.isActive():
            self._preview_timer.start(self.preview_interval_ms)
        self.status_changed.emit("preview", "")

    def schedule_exact(self, request: RefreshRequest, *, immediate=False):
        self._latest_generation[request.page_id] = request.generation
        self._pending_exact = request
        self.status_changed.emit("computing", "")
        if immediate:
            self._exact_timer.stop()
            self._dispatch_exact()
        else:
            self._exact_timer.start(self.exact_idle_ms)

    def dispatch_now(self, request: RefreshRequest):
        self._latest_generation[request.page_id] = request.generation
        self.dispatch_requested.emit(request)

    def submit_compute(self, request: RefreshRequest, work: Callable[[], ComputeResult]):
        self._latest_generation[request.page_id] = request.generation
        if self._running is not None:
            self._pending_compute = (request, work)
            return
        self._start_compute(request, work)

    def cancel_page(self, page_id):
        page_id = str(page_id)
        self._latest_generation[page_id] = self._generation + 1
        if self._pending_preview is not None and self._pending_preview.page_id == page_id:
            self._pending_preview = None
        if self._pending_exact is not None and self._pending_exact.page_id == page_id:
            self._pending_exact = None
        if self._pending_compute is not None and self._pending_compute[0].page_id == page_id:
            self._pending_compute = None

    def is_current(self, request: RefreshRequest):
        return self._latest_generation.get(request.page_id) == request.generation

    def shutdown(self, wait_ms=2000):
        self._preview_timer.stop()
        self._exact_timer.stop()
        self._pending_preview = None
        self._pending_exact = None
        self._pending_compute = None
        self._pool.waitForDone(int(wait_ms))

    def _dispatch_preview(self):
        request = self._pending_preview
        self._pending_preview = None
        if request is not None and self.is_current(request):
            self.dispatch_requested.emit(request)

    def _dispatch_exact(self):
        request = self._pending_exact
        self._pending_exact = None
        if request is not None and self.is_current(request):
            self.dispatch_requested.emit(request)

    def _start_compute(self, request, work):
        task = _ComputeTask(request, work)
        task.signals.finished.connect(self._on_compute_finished)
        task.signals.failed.connect(self._on_compute_failed)
        self._running = (request, task)
        self.status_changed.emit("computing", "")
        self._pool.start(task)

    @pyqtSlot(object, object)
    def _on_compute_finished(self, request, result):
        self._running = None
        if self.is_current(request):
            self.result_ready.emit(request, result)
            self.status_changed.emit("complete", getattr(result, "backend", ""))
        self._start_pending_compute()

    @pyqtSlot(object, object)
    def _on_compute_failed(self, request, exc):
        self._running = None
        if self.is_current(request):
            self.failed.emit(request, str(exc))
        self._start_pending_compute()

    def _start_pending_compute(self):
        pending = self._pending_compute
        self._pending_compute = None
        if pending is None:
            return
        request, work = pending
        if self.is_current(request):
            self._start_compute(request, work)
