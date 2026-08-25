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
    started is cancelled, running work is drained, and every result is discarded.
    Draining prevents a late completion from escaping the barrier after its caller
    has already handled the group failure.
    """

    if type(tasks) is not tuple or not tasks:
        raise ValueError("parallel group requires a non-empty exact task tuple")
    if type(max_workers) is not int or not 1 <= max_workers <= len(tasks):
        raise ValueError("parallel group worker bound is invalid")

    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="seven-lens-model")
    futures: tuple[Future[T], ...] = tuple(executor.submit(task) for task in tasks)
    try:
        _, pending = wait(futures, return_when=FIRST_EXCEPTION)
        failure = next((future.exception() for future in futures if future.done()), None)
        if failure is not None:
            for future in pending:
                future.cancel()
            wait(futures)
            raise failure
        wait(futures)
        return tuple(future.result() for future in futures)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
