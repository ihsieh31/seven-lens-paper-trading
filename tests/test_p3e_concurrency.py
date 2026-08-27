from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest

from seven_lens.analysis.concurrency import run_bounded_group
from seven_lens.analysis.pipeline import AnalysisPipeline, AnalysisPipelineError
from seven_lens.analysis.ports import ProviderOutput, ProviderRequest, ScriptedAnalysisProvider
from seven_lens.analysis.proposal_ports import ProposalOutput, ProposalRequest
from seven_lens.application.ports.analysis import AnalysisStage, InMemoryAnalysisStateRepository
from test_analysis_contracts import analysis_input, timestamp
from test_p3bc_analysis_pipeline import scripted_outputs
from test_p3bc_evidence_and_infrastructure import evidence_packet
from test_p3d_proposal_contracts import bundle as fixture_bundle
from test_p3d_proposal_contracts import parent_input as fixture_parent
from test_p3d_research_and_proposal_pipeline import (
    ProposalFakeProvider,
    make_proposal_pipeline,
)


def test_bounded_group_is_parallel_bounded_and_joins_in_canonical_order() -> None:
    lock = threading.Lock()
    active = 0
    maximum = 0
    gate = threading.Barrier(3)

    def task(value: int) -> Callable[[], int]:
        def execute() -> int:
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            gate.wait(timeout=1)
            time.sleep((3 - value) * 0.005)
            with lock:
                active -= 1
            return value

        return execute

    assert run_bounded_group(tuple(task(value) for value in (1, 2, 3)), max_workers=3) == (
        1,
        2,
        3,
    )
    assert maximum == 3


def test_bounded_group_drains_running_work_after_failure() -> None:
    def fast_success() -> str:
        time.sleep(0.01)
        return "success"

    failure_ready = threading.Event()

    def delayed_failure() -> str:
        time.sleep(0.05)
        failure_ready.set()
        raise RuntimeError("delayed group failure")

    slow_started = threading.Event()
    release_slow = threading.Event()

    def slow_success() -> str:
        slow_started.set()
        assert release_slow.wait(timeout=2)
        return "slow"

    failures: list[BaseException] = []
    returned = threading.Event()

    def invoke() -> None:
        try:
            run_bounded_group(
                (fast_success, delayed_failure, slow_success),
                max_workers=3,
            )
        except BaseException as error:
            failures.append(error)
        finally:
            returned.set()

    runner = threading.Thread(target=invoke)
    runner.start()
    assert slow_started.wait(timeout=1)
    assert failure_ready.wait(timeout=1)
    # The caller remains in the barrier until the already-running task drains.
    assert not returned.wait(timeout=0.1)
    release_slow.set()
    runner.join(timeout=2)

    assert not runner.is_alive()
    assert len(failures) == 1
    assert type(failures[0]) is RuntimeError
    assert str(failures[0]) == "delayed group failure"


def test_bounded_group_cancels_pending_work_and_ignores_cancelled_future() -> None:
    pending_started = threading.Event()

    def failure() -> str:
        raise RuntimeError("first task failed")

    def pending() -> str:
        pending_started.set()
        raise AssertionError("cancelled task must not run")

    with pytest.raises(RuntimeError, match="first task failed"):
        run_bounded_group((failure, pending), max_workers=1)

    assert not pending_started.is_set()


def test_bounded_group_selects_simultaneous_failure_in_submission_order() -> None:
    def run_once(submitted_labels: tuple[str, str]) -> str:
        started = threading.Barrier(3)

        def failing(label: str) -> Callable[[], str]:
            def execute() -> str:
                started.wait(timeout=1)
                raise RuntimeError(label)

            return execute

        failures: list[BaseException] = []

        def invoke() -> None:
            try:
                run_bounded_group(
                    tuple(failing(label) for label in submitted_labels),
                    max_workers=2,
                )
            except BaseException as error:
                failures.append(error)

        runner = threading.Thread(target=invoke)
        runner.start()
        started.wait(timeout=1)
        runner.join(timeout=1)
        assert not runner.is_alive()
        assert len(failures) == 1
        assert type(failures[0]) is RuntimeError
        return str(failures[0])

    for submitted_labels in (("A", "B"), ("B", "A")):
        expected = submitted_labels[0]
        assert [run_once(submitted_labels) for _ in range(100)] == [expected] * 100


def test_analysis_round_barrier_and_late_success_never_persist_partial_stage() -> None:
    delegate = ScriptedAnalysisProvider(scripted_outputs())
    lock = threading.Lock()
    starts: dict[str, float] = {}
    finishes: dict[str, float] = {}

    class TimedProvider:
        def execute(self, request: ProviderRequest) -> ProviderOutput:
            with lock:
                starts[request.key] = time.monotonic()
            if request.key in {"BULL::1", "BEAR::1"}:
                time.sleep(0.015)
            result = delegate.execute(request)
            with lock:
                finishes[request.key] = time.monotonic()
            return result

    repository = InMemoryAnalysisStateRepository()
    AnalysisPipeline(TimedProvider(), repository, now=lambda: timestamp().value).run(
        analysis_input(), evidence_packet(), "MSFT"
    )
    assert min(starts["BULL::2"], starts["BEAR::2"]) >= max(
        finishes["BULL::1"], finishes["BEAR::1"]
    )

    outputs = scripted_outputs()
    failed_delegate = ScriptedAnalysisProvider(outputs)

    class FailingGroupProvider:
        def execute(self, request: ProviderRequest) -> ProviderOutput:
            if request.key == "ANALYST:TECHNICAL:":
                raise TimeoutError("private marker")
            if request.key == "ANALYST:NEWS:":
                time.sleep(0.02)  # cannot be cancelled after starting
            return failed_delegate.execute(request)

    failed_repository = InMemoryAnalysisStateRepository()
    with pytest.raises(AnalysisPipelineError, match="failed closed"):
        AnalysisPipeline(
            FailingGroupProvider(), failed_repository, now=lambda: timestamp().value
        ).run(analysis_input(), evidence_packet(), "MSFT")
    run_id = str(analysis_input().meta.run_id)
    assert failed_repository.current_stage(run_id) is AnalysisStage.PLANNED
    assert failed_repository.load(run_id, AnalysisStage.ANALYSTS) is None


def test_real_analysis_pipeline_uses_exact_four_and_two_worker_barriers() -> None:
    delegate = ScriptedAnalysisProvider(scripted_outputs())
    lock = threading.Lock()
    barriers = {
        "analysts": threading.Barrier(4),
        "debate-1": threading.Barrier(2),
        "debate-2": threading.Barrier(2),
    }
    active = {key: 0 for key in barriers}
    maximum = {key: 0 for key in barriers}
    starts: dict[str, float] = {}
    finishes: dict[str, float] = {}

    class BarrierProvider:
        def execute(self, request: ProviderRequest) -> ProviderOutput:
            if request.key.startswith("ANALYST:"):
                group = "analysts"
            elif request.key in {"BULL::1", "BEAR::1"}:
                group = "debate-1"
            elif request.key in {"BULL::2", "BEAR::2"}:
                group = "debate-2"
            else:
                return delegate.execute(request)
            with lock:
                starts[request.key] = time.monotonic()
                active[group] += 1
                maximum[group] = max(maximum[group], active[group])
            barriers[group].wait(timeout=2)
            output = delegate.execute(request)
            with lock:
                active[group] -= 1
                finishes[request.key] = time.monotonic()
            return output

    AnalysisPipeline(
        BarrierProvider(), InMemoryAnalysisStateRepository(), now=lambda: timestamp().value
    ).run(analysis_input(), evidence_packet(), "MSFT")

    assert maximum == {"analysts": 4, "debate-1": 2, "debate-2": 2}
    assert min(starts["BULL::2"], starts["BEAR::2"]) >= max(
        finishes["BULL::1"], finishes["BEAR::1"]
    )


def test_real_proposal_pipeline_uses_exact_three_worker_round_barriers() -> None:
    delegate = ProposalFakeProvider()
    lock = threading.Lock()
    barriers = {1: threading.Barrier(3), 2: threading.Barrier(3)}
    active = {1: 0, 2: 0}
    maximum = {1: 0, 2: 0}
    starts: dict[str, float] = {}
    finishes: dict[str, float] = {}

    class BarrierProvider:
        calls = delegate.calls

        def execute(self, request: ProposalRequest) -> ProposalOutput:
            if request.round_number not in {1, 2}:
                return delegate.execute(request)
            round_number = request.round_number
            assert round_number is not None
            with lock:
                starts[request.key] = time.monotonic()
                active[round_number] += 1
                maximum[round_number] = max(maximum[round_number], active[round_number])
            barriers[round_number].wait(timeout=2)
            output = delegate.execute(request)
            with lock:
                active[round_number] -= 1
                finishes[request.key] = time.monotonic()
            return output

    pipeline, _ = make_proposal_pipeline(BarrierProvider())
    pipeline.run(fixture_bundle(), fixture_parent())

    assert maximum == {1: 3, 2: 3}
    assert min(starts["AGGRESSIVE:2"], starts["CONSERVATIVE:2"], starts["NEUTRAL:2"]) >= max(
        finishes["AGGRESSIVE:1"],
        finishes["CONSERVATIVE:1"],
        finishes["NEUTRAL:1"],
    )
