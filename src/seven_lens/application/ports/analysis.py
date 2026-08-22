"""Persistence-neutral analysis stage authority port."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol


class AnalysisStage(StrEnum):
    PLANNED = "PLANNED"
    ANALYSTS = "ANALYSTS"
    DEBATE = "DEBATE"
    RESEARCH = "RESEARCH"
    TRADER = "TRADER"
    COMPLETE = "COMPLETE"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"


# The single authoritative adjacency whitelist shared by every repository layer.
# COMPLETE, INVALID and EXPIRED are sinks: they are never a transition source,
# self-transitions and regressions are illegal, and every non-terminal stage may
# only move to its direct successor or fail closed into a terminal state.
LEGAL_TRANSITIONS: frozenset[tuple[AnalysisStage, AnalysisStage]] = frozenset(
    {
        (AnalysisStage.PLANNED, AnalysisStage.ANALYSTS),
        (AnalysisStage.ANALYSTS, AnalysisStage.DEBATE),
        (AnalysisStage.DEBATE, AnalysisStage.RESEARCH),
        (AnalysisStage.RESEARCH, AnalysisStage.TRADER),
        (AnalysisStage.TRADER, AnalysisStage.COMPLETE),
        (AnalysisStage.PLANNED, AnalysisStage.INVALID),
        (AnalysisStage.ANALYSTS, AnalysisStage.INVALID),
        (AnalysisStage.DEBATE, AnalysisStage.INVALID),
        (AnalysisStage.RESEARCH, AnalysisStage.INVALID),
        (AnalysisStage.TRADER, AnalysisStage.INVALID),
        (AnalysisStage.PLANNED, AnalysisStage.EXPIRED),
        (AnalysisStage.ANALYSTS, AnalysisStage.EXPIRED),
        (AnalysisStage.DEBATE, AnalysisStage.EXPIRED),
        (AnalysisStage.RESEARCH, AnalysisStage.EXPIRED),
        (AnalysisStage.TRADER, AnalysisStage.EXPIRED),
    }
)

MAX_STAGE_ATTEMPTS: Final = 8


@dataclass(frozen=True, slots=True)
class StoredStageResult:
    run_id: str
    stage: AnalysisStage
    result_hash: str
    payload: str


class AnalysisStateRepository(Protocol):
    def create_run(
        self, run_id: str, input_id: str, packet_hash: str, snapshot_hash: str
    ) -> None: ...
    def current_stage(self, run_id: str) -> AnalysisStage: ...
    def load(self, run_id: str, stage: AnalysisStage) -> StoredStageResult | None: ...
    def advance(self, result: StoredStageResult, expected_current: AnalysisStage) -> bool: ...


class InMemoryAnalysisStateRepository:
    def __init__(self) -> None:
        self._runs: dict[str, AnalysisStage] = {}
        self._identities: dict[str, tuple[str, str, str]] = {}
        self._run_by_input: dict[str, str] = {}
        self._results: dict[tuple[str, AnalysisStage], StoredStageResult] = {}
        self._attempts: dict[tuple[str, AnalysisStage], int] = {}

    def create_run(self, run_id: str, input_id: str, packet_hash: str, snapshot_hash: str) -> None:
        identity = (input_id, packet_hash, snapshot_hash)
        existing = self._identities.get(run_id)
        if existing is not None and existing != identity:
            raise ValueError("analysis run identity collision")
        existing_run = self._run_by_input.get(input_id)
        if existing_run is not None and existing_run != run_id:
            raise ValueError("analysis input already has an authority run")
        self._identities.setdefault(run_id, identity)
        self._run_by_input.setdefault(input_id, run_id)
        self._runs.setdefault(run_id, AnalysisStage.PLANNED)

    def current_stage(self, run_id: str) -> AnalysisStage:
        return self._runs[run_id]

    def load(self, run_id: str, stage: AnalysisStage) -> StoredStageResult | None:
        return self._results.get((run_id, stage))

    def attempt_count(self, run_id: str, stage: AnalysisStage) -> int:
        return self._attempts.get((run_id, stage), 0)

    def advance(self, result: StoredStageResult, expected_current: AnalysisStage) -> bool:
        if (expected_current, result.stage) not in LEGAL_TRANSITIONS:
            raise ValueError("analysis stage transition is not legal")
        key = (result.run_id, result.stage)
        existing = self._results.get(key)
        if existing is not None:
            if existing != result:
                raise ValueError("stage retry changed immutable output")
            attempt = self._attempts.get(key, 1) + 1
            if attempt > MAX_STAGE_ATTEMPTS:
                raise ValueError("analysis stage retry budget is exhausted")
            self._attempts[key] = attempt
            return False
        if self._runs.get(result.run_id) is not expected_current:
            raise ValueError("analysis stage transition is out of order")
        self._results[key] = result
        self._runs[result.run_id] = result.stage
        self._attempts[key] = 1
        return True
