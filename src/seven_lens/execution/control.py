"""Control-plane contracts: the human emergency levers over execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from seven_lens.domain.value_objects import UtcTimestamp

_MAX_REASON_LENGTH = 200
_MAX_ACTOR_LENGTH = 100


class ControlCommand(StrEnum):
    """The closed set of operator commands; every application is audited."""

    PAUSE_ENTRIES = "PAUSE_ENTRIES"
    RESUME_ENTRIES = "RESUME_ENTRIES"
    CANCEL_OPEN_ORDERS = "CANCEL_OPEN_ORDERS"
    FLATTEN_PAPER = "FLATTEN_PAPER"
    SHUTDOWN_AFTER_RECONCILE = "SHUTDOWN_AFTER_RECONCILE"


@dataclass(frozen=True, slots=True)
class ControlCommandRecord:
    command_id: UUID
    command: ControlCommand
    reason: str
    actor: str
    run_id: UUID | None
    requested_at: UtcTimestamp
    applied_at: UtcTimestamp | None

    def __post_init__(self) -> None:
        if not isinstance(self.command_id, UUID) or self.command_id.int == 0:
            raise ValueError("command_id must be a non-nil UUID")
        if type(self.command) is not ControlCommand:
            raise ValueError("command must be a ControlCommand")
        if (
            type(self.reason) is not str
            or not self.reason.strip()
            or len(self.reason) > _MAX_REASON_LENGTH
            or "\x00" in self.reason
        ):
            raise ValueError("reason must be bounded text")
        if (
            type(self.actor) is not str
            or not self.actor.strip()
            or len(self.actor) > _MAX_ACTOR_LENGTH
            or "\x00" in self.actor
        ):
            raise ValueError("actor must be bounded text")
        if self.run_id is not None and not isinstance(self.run_id, UUID):
            raise ValueError("run_id must be a UUID or None")
        if not isinstance(self.requested_at, UtcTimestamp):
            raise ValueError("requested_at must be a UtcTimestamp")
        if self.applied_at is not None:
            if not isinstance(self.applied_at, UtcTimestamp):
                raise ValueError("applied_at must be a UtcTimestamp or None")
            if self.applied_at.value < self.requested_at.value:
                raise ValueError("applied_at must not precede requested_at")


@dataclass(frozen=True, slots=True)
class ControlStateSnapshot:
    entries_paused: bool
    paused_reason: str | None
    updated_at: UtcTimestamp
    flatten_generation: int = 0

    def __post_init__(self) -> None:
        if type(self.entries_paused) is not bool:
            raise ValueError("entries_paused must be a boolean")
        if self.paused_reason is not None and (
            type(self.paused_reason) is not str
            or not self.paused_reason.strip()
            or len(self.paused_reason) > _MAX_REASON_LENGTH
        ):
            raise ValueError("paused_reason must be bounded text or None")
        if (self.entries_paused and self.paused_reason is None) or (
            not self.entries_paused and self.paused_reason is not None
        ):
            raise ValueError("a pause requires a reason; a resume clears it")
        if not isinstance(self.updated_at, UtcTimestamp):
            raise ValueError("updated_at must be a UtcTimestamp")
        if type(self.flatten_generation) is not int or self.flatten_generation < 0:
            raise ValueError("flatten_generation must be a non-negative counter")
