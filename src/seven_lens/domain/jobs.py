"""Persistence-neutral job identity, state, and fencing value objects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from seven_lens.domain.value_objects import TradingDate, UtcTimestamp

_JOB_COMPONENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_LEASE_OWNER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,199}$")


class JobStatus(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class JobSpec:
    trading_date: TradingDate
    job_type: str
    window: str

    def __post_init__(self) -> None:
        if not isinstance(self.trading_date, TradingDate):
            raise ValueError("trading_date must be a TradingDate")
        _validate_job_component(self.job_type, "job_type")
        _validate_job_component(self.window, "window")

    @property
    def job_key(self) -> str:
        """Return the canonical deterministic identity for this scheduled job."""
        return f"{self.trading_date}/{self.job_type}/{self.window}"


@dataclass(frozen=True, slots=True)
class LeaseDuration:
    value: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.value, timedelta):
            raise ValueError("lease duration must be a timedelta")
        if self.value <= timedelta(0) or self.value > timedelta(days=1):
            raise ValueError("lease duration must be greater than zero and at most one day")


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    job_key: str
    lease_owner: str
    leased_until: UtcTimestamp
    fencing_token: int
    attempt_count: int
    database_time: UtcTimestamp

    def __post_init__(self) -> None:
        if type(self.job_key) is not str or not self.job_key:
            raise ValueError("job_key must be non-empty text")
        validate_lease_owner(self.lease_owner)
        if type(self.fencing_token) is not int or self.fencing_token < 1:
            raise ValueError("fencing_token must be a positive integer")
        if type(self.attempt_count) is not int or self.attempt_count < 1:
            raise ValueError("attempt_count must be a positive integer")
        if not isinstance(self.leased_until, UtcTimestamp):
            raise ValueError("leased_until must be a UtcTimestamp")
        if not isinstance(self.database_time, UtcTimestamp):
            raise ValueError("database_time must be a UtcTimestamp")
        if self.leased_until.value <= self.database_time.value:
            raise ValueError("leased_until must be after database_time")


@dataclass(frozen=True, slots=True)
class JobInstance:
    spec: JobSpec
    status: JobStatus
    lease_owner: str | None
    leased_until: UtcTimestamp | None
    fencing_token: int
    attempt_count: int
    created_at: UtcTimestamp
    updated_at: UtcTimestamp

    @property
    def job_key(self) -> str:
        return self.spec.job_key


def validate_lease_owner(value: object) -> str:
    if type(value) is not str or _LEASE_OWNER_PATTERN.fullmatch(value) is None:
        raise ValueError("lease_owner must use the canonical bounded owner format")
    return value


def _validate_job_component(value: object, field_name: str) -> None:
    if type(value) is not str or _JOB_COMPONENT_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must use lowercase letters, digits, '_' or '-'")
