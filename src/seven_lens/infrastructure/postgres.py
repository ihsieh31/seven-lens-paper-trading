"""psycopg-backed adapters for the P1-B authoritative PostgreSQL schema.

Lease fields are never updated directly by this adapter.  The repository calls the
database lease functions so PostgreSQL owns expiry, takeover history, and fencing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import TracebackType
from typing import Self, cast

import psycopg
from psycopg.types.json import Jsonb

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
from seven_lens.domain.value_objects import TradingDate, UtcTimestamp


class PersistenceInvariantError(RuntimeError):
    """Raised when data returned by PostgreSQL violates a domain invariant."""


class UnitOfWorkStateError(RuntimeError):
    """Raised when a repository is used outside an active unit of work."""


class PostgresUnitOfWork:
    """A rollback-by-default unit of work over one direct psycopg connection."""

    def __init__(self, dsn: str) -> None:
        if type(dsn) is not str or not dsn.strip():
            raise ValueError("PostgreSQL DSN must be non-empty text")
        self._dsn = dsn
        self._connection: psycopg.Connection[tuple[object, ...]] | None = None
        self._has_uncommitted_work = False
        self.domain_events = PostgresDomainEventRepository(self)
        self.audit_events = PostgresAuditEventRepository(self)
        self.jobs = PostgresJobRepository(self)

    def __enter__(self) -> Self:
        if self._connection is not None:
            raise UnitOfWorkStateError("unit of work is already active")
        connection = psycopg.connect(self._dsn, autocommit=False)
        try:
            _set_local_utc_timezone(connection)
        except Exception:
            connection.close()
            raise
        self._connection = connection
        self._has_uncommitted_work = False
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        connection = self._connection
        if connection is None:
            return
        try:
            if exc_type is not None or self._has_uncommitted_work:
                connection.rollback()
        finally:
            connection.close()
            self._connection = None
            self._has_uncommitted_work = False

    def commit(self) -> None:
        """Explicitly commit all work written since the previous commit or rollback."""
        connection = self._require_connection()
        connection.commit()
        _set_local_utc_timezone(connection)
        self._has_uncommitted_work = False

    def rollback(self) -> None:
        """Explicitly discard all work written since the previous commit or rollback."""
        connection = self._require_connection()
        connection.rollback()
        _set_local_utc_timezone(connection)
        self._has_uncommitted_work = False

    def _require_connection(self) -> psycopg.Connection[tuple[object, ...]]:
        if self._connection is None:
            raise UnitOfWorkStateError("repository access requires an active unit of work")
        return self._connection

    def _mark_write(self) -> None:
        self._has_uncommitted_work = True


class PostgresDomainEventRepository:
    """Append immutable domain-event envelopes in the current unit-of-work transaction."""

    def __init__(self, unit_of_work: PostgresUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def add(self, event: DomainEvent) -> RecordedDomainEvent:
        """Insert an event; PostgreSQL assigns the authoritative recorded timestamp."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO domain_events (
                    event_id, event_type, schema_version, aggregate_type, aggregate_id,
                    aggregate_sequence, run_id, correlation_id, causation_id, occurred_at,
                    payload, producer_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING recorded_at
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.schema_version.value,
                    event.aggregate_type,
                    event.aggregate_id,
                    event.aggregate_sequence,
                    event.run_id.value,
                    event.correlation_id,
                    event.causation_id,
                    event.occurred_at.value,
                    Jsonb(event.payload.to_json_object().to_dict()),
                    event.producer_version,
                ),
            )
            row = _row(cursor.fetchone(), "domain event insert")
        self._unit_of_work._mark_write()
        return RecordedDomainEvent(event=event, recorded_at=_timestamp(row[0], "recorded_at"))


class PostgresAuditEventRepository:
    """Append audited state changes; database triggers enforce immutability and secrecy."""

    def __init__(self, unit_of_work: PostgresUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def add(self, event: AuditEvent) -> RecordedAuditEvent:
        """Insert an audit envelope in the current transaction without client timestamps."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO audit_events (
                    audit_id, event_type, run_id, correlation_id, causation_id, occurred_at,
                    payload, producer_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING recorded_at
                """,
                (
                    event.audit_id,
                    event.event_type,
                    None if event.run_id is None else event.run_id.value,
                    event.correlation_id,
                    event.causation_id,
                    event.occurred_at.value,
                    Jsonb(event.payload.to_json_object().to_dict()),
                    event.producer_version,
                ),
            )
            row = _row(cursor.fetchone(), "audit event insert")
        self._unit_of_work._mark_write()
        return RecordedAuditEvent(event=event, recorded_at=_timestamp(row[0], "recorded_at"))


class PostgresJobRepository:
    """Job and lease repository whose timing and fencing checks run in PostgreSQL."""

    def __init__(self, unit_of_work: PostgresUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def add(self, spec: JobSpec) -> JobInstance:
        """Create a deterministic job, or return its existing identical instance."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO job_instances (job_key, trading_date, job_type, window_name, status)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (job_key) DO NOTHING
                RETURNING
                    job_key, trading_date, job_type, window_name, status, lease_owner,
                    leased_until, fencing_token, attempt_count, created_at, updated_at
                """,
                (
                    spec.job_key,
                    spec.trading_date.value,
                    spec.job_type,
                    spec.window,
                    JobStatus.PLANNED.value,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                self._unit_of_work._mark_write()
                return _job_instance(_row(row, "job insert"))

            cursor.execute(
                """
                SELECT job_key, trading_date, job_type, window_name, status, lease_owner,
                       leased_until, fencing_token, attempt_count, created_at, updated_at
                FROM job_instances
                WHERE job_key = %s
                """,
                (spec.job_key,),
            )
            existing = cursor.fetchone()
        job = None if existing is None else _job_instance(_row(existing, "job lookup"))
        if job is None or job.spec != spec:
            raise PersistenceInvariantError(
                "job key is bound to a different immutable job specification"
            )
        return job

    def get(self, job_key: str) -> JobInstance | None:
        """Load a job instance without using the process clock."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                SELECT job_key, trading_date, job_type, window_name, status, lease_owner,
                       leased_until, fencing_token, attempt_count, created_at, updated_at
                FROM job_instances
                WHERE job_key = %s
                """,
                (job_key,),
            )
            row = cursor.fetchone()
        return None if row is None else _job_instance(_row(row, "job lookup"))

    def acquire(
        self,
        job_key: str,
        lease_owner: str,
        duration: LeaseDuration,
    ) -> LeaseGrant | None:
        """Atomically acquire only an unowned or DB-clock-expired lease."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                "SELECT * FROM acquire_job_lease(%s, %s, %s)",
                (job_key, lease_owner, duration.value),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        self._unit_of_work._mark_write()
        return _lease_grant(_row(row, "job lease acquisition"))

    def renew(self, grant: LeaseGrant, duration: LeaseDuration) -> LeaseGrant | None:
        """Atomically renew only the current unexpired owner/token pair."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                "SELECT * FROM renew_job_lease(%s, %s, %s, %s)",
                (grant.job_key, grant.lease_owner, grant.fencing_token, duration.value),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        self._unit_of_work._mark_write()
        return _lease_grant(_row(row, "job lease renewal"))

    def release(self, grant: LeaseGrant) -> bool:
        """Atomically release only the current unexpired owner/token pair."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                "SELECT release_job_lease(%s, %s, %s, %s)",
                (grant.job_key, grant.lease_owner, grant.fencing_token, "released_by_owner"),
            )
            row = _row(cursor.fetchone(), "job lease release")
        released = row[0]
        if type(released) is not bool:
            raise PersistenceInvariantError("job lease release returned a non-boolean result")
        if released:
            self._unit_of_work._mark_write()
        return released

    def set_status(self, grant: LeaseGrant, status: JobStatus) -> JobInstance | None:
        """Use a DB-enforced owner/token/expiry fence for a protected status write."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                "SELECT * FROM transition_job_status(%s, %s, %s, %s)",
                (grant.job_key, grant.lease_owner, grant.fencing_token, status.value),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        self._unit_of_work._mark_write()
        return _job_instance(_row(row, "job status transition"))


def _row(value: object, operation: str) -> tuple[object, ...]:
    row = cast(tuple[object, ...] | None, value)
    if row is None:
        raise PersistenceInvariantError(f"{operation} did not return the required row")
    return row


def _set_local_utc_timezone(connection: psycopg.Connection[tuple[object, ...]]) -> None:
    """Pin each UoW transaction's result rendering to UTC before domain conversion."""
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL TIME ZONE 'UTC'")


def _timestamp(value: object, field_name: str) -> UtcTimestamp:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PersistenceInvariantError(f"database {field_name} must be a timezone-aware timestamp")
    return UtcTimestamp(value.astimezone(UTC))


def _trading_date(value: object) -> TradingDate:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise PersistenceInvariantError("database trading_date must be a date")
    return TradingDate(value)


def _text(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise PersistenceInvariantError(f"database {field_name} must be text")
    return value


def _integer(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise PersistenceInvariantError(f"database {field_name} must be an integer")
    return value


def _job_instance(row: tuple[object, ...]) -> JobInstance:
    if len(row) != 11:
        raise PersistenceInvariantError("job query returned an invalid column count")
    lease_owner = row[5]
    leased_until = row[6]
    if lease_owner is not None and type(lease_owner) is not str:
        raise PersistenceInvariantError("database lease_owner must be text or null")
    if leased_until is not None and not isinstance(leased_until, datetime):
        raise PersistenceInvariantError("database leased_until must be a timestamp or null")
    return JobInstance(
        spec=JobSpec(
            trading_date=_trading_date(row[1]),
            job_type=_text(row[2], "job_type"),
            window=_text(row[3], "window_name"),
        ),
        status=JobStatus(_text(row[4], "status")),
        lease_owner=lease_owner,
        leased_until=None if leased_until is None else _timestamp(leased_until, "leased_until"),
        fencing_token=_integer(row[7], "fencing_token"),
        attempt_count=_integer(row[8], "attempt_count"),
        created_at=_timestamp(row[9], "created_at"),
        updated_at=_timestamp(row[10], "updated_at"),
    )


def _lease_grant(row: tuple[object, ...]) -> LeaseGrant:
    if len(row) != 6:
        raise PersistenceInvariantError("lease query returned an invalid column count")
    return LeaseGrant(
        job_key=_text(row[0], "job_key"),
        lease_owner=_text(row[1], "lease_owner"),
        leased_until=_timestamp(row[2], "leased_until"),
        fencing_token=_integer(row[3], "fencing_token"),
        attempt_count=_integer(row[4], "attempt_count"),
        database_time=_timestamp(row[5], "database_time"),
    )
