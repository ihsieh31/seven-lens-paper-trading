# mypy: ignore-errors
"""In-memory doubles for the control plane and reconciliation repositories."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.execution.control import ControlCommandRecord, ControlStateSnapshot
from seven_lens.execution.reconciliation import ReconciliationResult


class FakeControlRepository:
    """Mirrors the singleton control state and the append-only command log."""

    def __init__(self, now: UtcTimestamp) -> None:
        self._pause_lock = RLock()
        self._state = ControlStateSnapshot(entries_paused=False, paused_reason=None, updated_at=now)
        self.commands: list[ControlCommandRecord] = []

    def state(self) -> ControlStateSnapshot:
        with self._pause_lock:
            return self._state

    @contextmanager
    def submission_guard(self) -> Iterator[ControlStateSnapshot]:
        with self._pause_lock:
            yield self._state

    def set_entries_paused(self, paused: bool, reason: str | None) -> ControlStateSnapshot:
        with self._pause_lock:
            self._state = ControlStateSnapshot(
                entries_paused=paused,
                paused_reason=reason,
                updated_at=self._state.updated_at,
                flatten_generation=self._state.flatten_generation,
            )
            return self._state

    def bump_flatten_generation(self) -> int:
        self._state = ControlStateSnapshot(
            entries_paused=self._state.entries_paused,
            paused_reason=self._state.paused_reason,
            updated_at=self._state.updated_at,
            flatten_generation=self._state.flatten_generation + 1,
        )
        return self._state.flatten_generation

    def add_command(self, record: ControlCommandRecord) -> UtcTimestamp | None:
        self.commands.append(record)
        return record.requested_at


class FakeReconciliationRepository:
    """Keeps every recorded run in order for latest() gating."""

    def __init__(self) -> None:
        self.runs: list[ReconciliationResult] = []

    def add(self, result: ReconciliationResult) -> UtcTimestamp:
        self.runs.append(result)
        return result.observed_at

    def latest(self) -> ReconciliationResult | None:
        return self.runs[-1] if self.runs else None
