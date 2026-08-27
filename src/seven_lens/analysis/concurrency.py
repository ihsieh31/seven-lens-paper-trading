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
    started is cancelled and already-running work is drained before the failure
    is selected.  The failure is selected from the final futures in the supplied
    order, so scheduler timing cannot change the canonical error.  The group
    never persists a partial result after the barrier has failed.
    """

    if type(tasks) is not tuple or not tasks:
        raise ValueError("parallel group requires a non-empty exact task tuple")
    if type(max_workers) is not int or not 1 <= max_workers <= len(tasks):
        raise ValueError("parallel group worker bound is invalid")

    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="seven-lens-model")
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
        _, pending = wait(futures, return_when=FIRST_EXCEPTION)
        for future in pending:
            future.cancel()
        # FIRST_EXCEPTION is only the failure barrier.  A running provider call
        # cannot be forcefully stopped, so drain every final future before
        # choosing the canonical failure or returning results.
        wait(futures)
        for future in futures:
            if future.cancelled():
                continue
            error = future.exception()
            if error is not None:
                raise error
        return tuple(future.result() for future in futures)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
