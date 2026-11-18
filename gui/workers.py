"""Background QThread + QObject worker runner with cancellation and clean shutdown.

Worker functions run off the GUI thread via ``QObject.moveToThread``.
``WorkerHandle`` stays on the GUI thread and relays signals with
``Qt.QueuedConnection`` so caller slots/lambdas never mutate widgets from a
worker thread.
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

from gui.services.i18n import friendly_error
from gui.services.storage import redact_secrets

logger = logging.getLogger("tiktok-streak")


class TaskWorker(QObject):
    """Executes a callable inside a worker QThread and emits Qt signals."""

    started = Signal()
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(object)
    log_line = Signal(str)

    def __init__(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        cancel_event: threading.Event | None = None,
        pass_cancel_event: bool = False,
        pass_log_cb: bool = False,
        pass_progress_cb: bool = False,
        pass_status_cb: bool = False,
    ) -> None:
        super().__init__()
        self._fn = fn
        self._args = tuple(args)
        self._kwargs = dict(kwargs or {})
        self.cancel_event = cancel_event or threading.Event()
        self._pass_cancel_event = pass_cancel_event
        self._pass_log_cb = pass_log_cb
        self._pass_progress_cb = pass_progress_cb
        self._pass_status_cb = pass_status_cb

    def emit_log(self, text: str) -> None:
        if text is None:
            return
        self.log_line.emit(redact_secrets(str(text)))

    def emit_progress(self, payload: Any) -> None:
        self.progress.emit(payload)

    @Slot()
    def run(self) -> None:
        self.started.emit()
        call_kwargs = dict(self._kwargs)
        if self._pass_cancel_event and "cancel_event" not in call_kwargs:
            call_kwargs["cancel_event"] = self.cancel_event
        if self._pass_log_cb and "log_cb" not in call_kwargs:
            call_kwargs["log_cb"] = self.emit_log
        if self._pass_progress_cb and "progress_cb" not in call_kwargs:
            call_kwargs["progress_cb"] = self.emit_progress
        if self._pass_status_cb and "status_cb" not in call_kwargs:
            call_kwargs["status_cb"] = self.emit_log

        try:
            result = self._fn(*self._args, **call_kwargs)
        except Exception as exc:
            logger.debug("Worker task raised %s: %s", type(exc).__name__, exc)
            safe_raw = redact_secrets(f"[ERROR] {type(exc).__name__}: {exc}")
            self.log_line.emit(safe_raw)
            self.error.emit(redact_secrets(friendly_error(exc)))
        else:
            self.finished.emit(result)


class WorkerHandle(QObject):
    """GUI-thread handle representing one active background task."""

    started = Signal()
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(object)
    log_line = Signal(str)
    completed = Signal(str)

    def __init__(
        self,
        task_key: str,
        thread: QThread,
        worker: TaskWorker,
        cancel_event: threading.Event,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.task_key = task_key
        self.thread = thread
        self.worker = worker
        self.cancel_event = cancel_event
        self._done = False

    def cancel(self) -> None:
        self.cancel_event.set()

    def is_running(self) -> bool:
        try:
            return (not self._done) and self.thread.isRunning()
        except RuntimeError:
            return False

    @Slot()
    def _on_started(self) -> None:
        self.started.emit()

    @Slot(object)
    def _on_finished(self, result: Any) -> None:
        self._done = True
        self.finished.emit(result)

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self._done = True
        self.error.emit(message)

    @Slot(object)
    def _on_progress(self, payload: Any) -> None:
        self.progress.emit(payload)

    @Slot(str)
    def _on_log_line(self, line: str) -> None:
        self.log_line.emit(line)

    @Slot()
    def _on_thread_finished(self) -> None:
        self._done = True
        self.completed.emit(self.task_key)


class WorkerController(QObject):
    """Tracks background QThreads and tears them down safely on window close."""

    global_log = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._active: dict[str, WorkerHandle] = {}

    def is_running(self, task_key: str) -> bool:
        handle = self._active.get(task_key)
        return bool(handle and handle.is_running())

    def active_count(self) -> int:
        return sum(1 for h in self._active.values() if h.is_running())

    def start_task(
        self,
        fn: Callable[..., Any],
        *args: Any,
        task_key: str | None = None,
        cancel_event: threading.Event | None = None,
        pass_cancel_event: bool = False,
        pass_log_cb: bool = False,
        pass_progress_cb: bool = False,
        pass_status_cb: bool = False,
        on_started: Callable[[], Any] | None = None,
        on_finished: Callable[[Any], Any] | None = None,
        on_error: Callable[[str], Any] | None = None,
        on_progress: Callable[[Any], Any] | None = None,
        on_log: Callable[[str], Any] | None = None,
        **kwargs: Any,
    ) -> WorkerHandle:
        """Spawn ``fn`` in a managed QThread and return its GUI-thread ``WorkerHandle``."""
        key = task_key or f"task_{uuid.uuid4().hex[:10]}"
        if key in self._active and self._active[key].is_running():
            raise RuntimeError(f"Task '{key}' is already running.")

        ev = cancel_event or threading.Event()
        thread = QThread()
        worker = TaskWorker(
            fn=fn,
            args=args,
            kwargs=kwargs,
            cancel_event=ev,
            pass_cancel_event=pass_cancel_event,
            pass_log_cb=pass_log_cb,
            pass_progress_cb=pass_progress_cb,
            pass_status_cb=pass_status_cb,
        )
        worker.moveToThread(thread)

        handle = WorkerHandle(key, thread, worker, ev, parent=self)
        self._active[key] = handle

        # Worker -> GUI-thread handle relays (strictly QueuedConnection across threads)
        worker.started.connect(handle._on_started, Qt.QueuedConnection)
        worker.finished.connect(handle._on_finished, Qt.QueuedConnection)
        worker.error.connect(handle._on_error, Qt.QueuedConnection)
        worker.progress.connect(handle._on_progress, Qt.QueuedConnection)
        worker.log_line.connect(handle._on_log_line, Qt.QueuedConnection)
        worker.log_line.connect(self.global_log, Qt.QueuedConnection)

        # Clean QThread lifecycle
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(handle._on_thread_finished, Qt.QueuedConnection)
        thread.finished.connect(thread.deleteLater)
        handle.completed.connect(self._cleanup_handle)

        # Caller callbacks run on GUI thread via WorkerHandle
        if on_started is not None:
            handle.started.connect(on_started)
        if on_finished is not None:
            handle.finished.connect(on_finished)
        if on_error is not None:
            handle.error.connect(on_error)
        if on_progress is not None:
            handle.progress.connect(on_progress)
        if on_log is not None:
            handle.log_line.connect(on_log)

        thread.start()
        return handle

    @Slot(str)
    def _cleanup_handle(self, task_key: str) -> None:
        handle = self._active.get(task_key)
        if handle is not None and not handle.is_running():
            self._active.pop(task_key, None)
            handle.deleteLater()

    def cancel(self, task_key: str) -> bool:
        handle = self._active.get(task_key)
        if handle is None:
            return False
        handle.cancel()
        return True

    def cancel_all(self) -> None:
        for handle in list(self._active.values()):
            handle.cancel()

    def shutdown(self, timeout_ms: int = 3500) -> None:
        """Cancel and wait for all active QThreads before window destruction."""
        self.cancel_all()
        handles = list(self._active.values())
        for handle in handles:
            thread = handle.thread
            try:
                if thread.isRunning():
                    thread.quit()
                    if not thread.wait(timeout_ms):
                        logger.warning("Worker thread %s did not exit cleanly; terminating.", handle.task_key)
                        thread.terminate()
                        thread.wait(1000)
            except RuntimeError:
                pass
        self._active.clear()


__all__ = ["TaskWorker", "WorkerController", "WorkerHandle"]
