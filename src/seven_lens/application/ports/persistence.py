"""Repository and unit-of-work contracts with no database-library dependency."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from seven_lens.domain.events import (
    AuditEvent,
    DomainEvent,
    RecordedAuditEvent,
    RecordedDomainEvent,
)
from seven_lens.domain.jobs import (
    JobInstance,
    JobSpec,
    JobStatus,
    LeaseDuration,
    LeaseGrant,
)


class DomainEventRepository(Protocol):
    def add(self, event: DomainEvent) -> RecordedDomainEvent:
        """Append an event and return its database-recorded time."""
        ...


class AuditEventRepository(Protocol):
    def add(self, event: AuditEvent) -> RecordedAuditEvent:
        """Append a validated audit event in the current transaction."""
        ...


class JobRepository(Protocol):
    def add(self, spec: JobSpec) -> JobInstance:
        """Create or return the deterministic job instance."""
        ...

    def get(self, job_key: str) -> JobInstance | None: ...

    def acquire(
        self,
        job_key: str,
        lease_owner: str,
        duration: LeaseDuration,
    ) -> LeaseGrant | None:
        """Atomically acquire an unowned or expired job lease."""
        ...

    def renew(self, grant: LeaseGrant, duration: LeaseDuration) -> LeaseGrant | None:
        """Renew only the current, unexpired owner/token pair."""
        ...

    def release(self, grant: LeaseGrant) -> bool:
        """Release only the current, unexpired owner/token pair."""
        ...

    def set_status(self, grant: LeaseGrant, status: JobStatus) -> JobInstance | None:
        """Fence a protected job-state write by owner, token, and DB expiry."""
        ...


class UnitOfWork(Protocol):
    @property
    def domain_events(self) -> DomainEventRepository: ...

    @property
    def audit_events(self) -> AuditEventRepository: ...

    @property
    def jobs(self) -> JobRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
