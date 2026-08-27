"""Bounded, deterministic group execution for model-backed analysis stages."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
from threading import Event


def run_bounded_group[T](
    tasks: tuple[Callable[[], T], ...],
    *,
    max_workers: int,
) -> tuple[T, ...]:
    """Run one logical barrier and return results in the supplied canonical order.

    Every task is submitted at most once.  On a member failure, work that has not
    started is cancelled.  Only already-running futures earlier than the first
    observed failure may be observed long enough to settle the submission-order
    winner; later unrelated work is never joined.  The failure is selected from
    completed non-cancelled futures in the supplied order, so scheduler/set
    ordering cannot change the canonical error.  Running callables cannot be
    force-stopped by Python and may finish in the background; their results are
    discarded and the group never persists a partial result after the barrier has
    failed.
    """

    if type(tasks) is not tuple or not tasks:
        raise ValueError("parallel group requires a non-empty exact task tuple")
    if type(max_workers) is not int or not 1 <= max_workers <= len(tasks):
        raise ValueError("parallel group worker bound is invalid")

    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="seven-lens-model")
    failure_observed = False
    try:
        submitted: list[Future[T]] = []
        failure_seen = Event()

        def cancel_pending(completed: Future[T]) -> None:
            if completed.cancelled() or completed.exception() is None:
                return
            failure_seen.set()
            for future in tuple(submitted):
                if future is not completed:
                    future.cancel()

        for task in tasks:
            future = executor.submit(task)
            submitted.append(future)
            future.add_done_callback(cancel_pending)
            if failure_seen.is_set():
                future.cancel()
        futures = tuple(submitted)
        done, pending = wait(futures, return_when=FIRST_EXCEPTION)

        first_failure_index: int | None = None
        for index, future in enumerate(futures):
            if future not in done or future.cancelled():
                continue
            if future.exception() is not None:
                first_failure_index = index
                break

        for future in pending:
            future.cancel()

        # Do not call wait(futures) here: it would turn a failure barrier into a
        # join on an unrelated later provider call.  If a later-submitted future
        # wins the observation race, settle only earlier work that was already
        # running so a simultaneous earlier failure cannot be hidden.
        if first_failure_index is not None:
            earlier_running = tuple(
                future
                for index, future in enumerate(futures[:first_failure_index])
                if future.running()
            )
            if earlier_running:
                wait(earlier_running)

        # Only completed failures participate in canonical selection; cancelled
        # and still-running futures cannot mask the observed error.
        for future in futures:
            if future.cancelled() or not future.done():
                continue
            error = future.exception()
            if error is not None:
                failure_observed = True
                raise error
        return tuple(future.result() for future in futures)
    finally:
        executor.shutdown(wait=not failure_observed, cancel_futures=True)
