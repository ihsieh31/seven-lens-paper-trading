"""Repository and unit-of-work contracts with no database-library dependency."""

from __future__ import annotations

from contextlib import AbstractContextManager
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
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.execution.control import ControlCommandRecord, ControlStateSnapshot
from seven_lens.execution.orders import (
    BrokerOrder,
    BrokerOrderStatus,
    ClientOrderId,
    Fill,
    OrderIntent,
    OrderStatus,
)
from seven_lens.execution.reconciliation import ReconciliationResult


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


class OrderRepository(Protocol):
    """Authoritative persistence for order intents, broker mirrors, and fills."""

    def add(self, intent: OrderIntent) -> OrderIntent:
        """Create an intent, or return the identical existing one by client id."""
        ...

    def get(self, client_order_id: ClientOrderId) -> OrderIntent | None:
        """Load one intent by its deterministic client order id."""
        ...

    def list_by_status(self, status: OrderStatus) -> tuple[OrderIntent, ...]:
        """Load every intent currently in exactly this status."""
        ...

    def transition_status(self, client_order_id: ClientOrderId, target: OrderStatus) -> OrderIntent:
        """Persist one closed-map status transition; fail on any state drift."""
        ...

    def record_broker_order(self, order: BrokerOrder) -> BrokerOrder:
        """Insert or idempotently refresh the local mirror of a broker order."""
        ...

    def get_broker_order(self, client_order_id: ClientOrderId) -> BrokerOrder | None:
        """Load the local mirror for one client order id."""
        ...

    def get_broker_order_by_id(self, broker_order_id: str) -> BrokerOrder | None:
        """Load the local mirror for one broker order id."""
        ...

    def add_fill(self, fill: Fill) -> bool:
        """Append one fill; return False when the execution id already exists."""
        ...

    def list_fills(self, broker_order_id: str) -> tuple[Fill, ...]:
        """Load fills for one broker order in recorded order."""
        ...

    def list_open_broker_orders(self) -> tuple[BrokerOrder, ...]:
        """Load every mirror whose broker status is not terminal."""
        ...

    def list_all_broker_orders(self) -> tuple[BrokerOrder, ...]:
        """Load every recorded broker order mirror."""
        ...

    def list_all_fills(self) -> tuple[Fill, ...]:
        """Load the entire append-only fill ledger in recorded order."""
        ...

    def update_broker_order_status(
        self,
        broker_order_id: str,
        status: BrokerOrderStatus,
        filled_quantity: int,
        *,
        broker_observed_at: UtcTimestamp | None = None,
    ) -> BrokerOrder:
        """Refresh the mutable mirror columns under the guarded broker map.

        ``broker_observed_at`` is the broker's own timestamp for the change; it
        becomes the mirror's ``updated_at`` and must never move backwards.
        """
        ...


class ReconciliationRepository(Protocol):
    """Append-only storage for reconciliation runs."""

    def add(self, result: ReconciliationResult) -> UtcTimestamp:
        """Record one run; PostgreSQL supplies the authoritative timestamp."""
        ...

    def latest(self) -> ReconciliationResult | None:
        """Load the most recently recorded reconciliation run."""
        ...


class ControlRepository(Protocol):
    """Control-plane state and its append-only command audit log."""

    def state(self) -> ControlStateSnapshot:
        """Load the current singleton control state."""
        ...

    def submission_guard(self) -> AbstractContextManager[ControlStateSnapshot]:
        """Hold an exclusive new-entry lock across one broker submission."""
        ...

    def set_entries_paused(self, paused: bool, reason: str | None) -> ControlStateSnapshot:
        """Flip the pause flag with a mandatory reason and audit stamp."""
        ...

    def add_command(self, record: ControlCommandRecord) -> UtcTimestamp | None:
        """Append one operator command; UPDATE and DELETE are forbidden."""
        ...


class UnitOfWork(Protocol):
    @property
    def domain_events(self) -> DomainEventRepository: ...

    @property
    def audit_events(self) -> AuditEventRepository: ...

    @property
    def jobs(self) -> JobRepository: ...

    @property
    def orders(self) -> OrderRepository: ...

    @property
    def reconciliations(self) -> ReconciliationRepository: ...

    @property
    def control(self) -> ControlRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
