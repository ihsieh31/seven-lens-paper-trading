"""Bounded, deterministic group execution for model-backed analysis stages."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait


def run_bounded_group[T](
    tasks: tuple[Callable[[], T], ...],
    *,
    max_workers: int,
) -> tuple[T, ...]:
    """Run one logical barrier and return results in the supplied canonical order.

    Every task is submitted at most once.  On a member failure, work that has not
    started is cancelled, already-running work is abandoned without consuming its
    result, and the failure returns promptly.  The group never persists a partial
    result after the barrier has failed.
    """

    if type(tasks) is not tuple or not tasks:
        raise ValueError("parallel group requires a non-empty exact task tuple")
    if type(max_workers) is not int or not 1 <= max_workers <= len(tasks):
        raise ValueError("parallel group worker bound is invalid")

    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="seven-lens-model")
    executor_shutdown = False
    futures: tuple[Future[T], ...] = tuple(executor.submit(task) for task in tasks)
    try:
        done, pending = wait(futures, return_when=FIRST_EXCEPTION)
        failure = None
        completed = set(done)
        # ``done`` is a set whose iteration order follows scheduler timing.
        # Use it only as membership information; the submitted tuple is the
        # canonical task order for selecting among simultaneous failures.
        for future in futures:
            if future not in completed:
                continue
            if future.cancelled():
                continue
            error = future.exception()
            if error is not None:
                failure = error
                break
        if failure is not None:
            for future in pending:
                future.cancel()
            # A running provider call cannot be forcefully stopped.  Do not
            # hold the caller behind it after the group has already failed;
            # no result is consumed or persisted from this executor again.
            executor.shutdown(wait=False, cancel_futures=True)
            executor_shutdown = True
            raise failure
        wait(futures)
        return tuple(future.result() for future in futures)
    finally:
        if not executor_shutdown:
            executor.shutdown(wait=True, cancel_futures=True)
