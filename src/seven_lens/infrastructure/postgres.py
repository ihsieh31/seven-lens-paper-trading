"""psycopg-backed adapters for the P1-B authoritative PostgreSQL schema.

Lease fields are never updated directly by this adapter.  The repository calls the
database lease functions so PostgreSQL owns expiry, takeover history, and fencing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Self, cast
from urllib.parse import quote_plus
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from seven_lens.application.composition import RuntimeDatabaseConfig
from seven_lens.application.ports.secrets import SecretProvider
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
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.control import ControlCommandRecord, ControlStateSnapshot
from seven_lens.execution.orders import (
    BrokerOrder,
    BrokerOrderStatus,
    ClientOrderId,
    Fill,
    OrderIntent,
    OrderIntentType,
    OrderQuantity,
    OrderSide,
    OrderStatus,
    Price,
    PriceCollar,
    Symbol,
)
from seven_lens.execution.reconciliation import (
    MismatchKind,
    ReconciliationMismatch,
    ReconciliationResult,
    ReconciliationScope,
    ReconciliationStatus,
)


class PersistenceInvariantError(RuntimeError):
    """Raised when data returned by PostgreSQL violates a domain invariant."""


class RuntimeDsn:
    """A connection info string that never discloses itself by accident."""

    __slots__ = ("_conninfo",)

    def __init__(self, conninfo: str) -> None:
        if type(conninfo) is not str or not conninfo.startswith("postgresql://"):
            raise PersistenceInvariantError("runtime DSN must be a postgresql connection string")
        self._conninfo = conninfo

    def conninfo(self) -> str:
        """The single bounded reveal point; callers must not log or store it."""
        return self._conninfo

    def __str__(self) -> str:
        return "postgresql://<redacted>"

    def __repr__(self) -> str:
        return "RuntimeDsn(<redacted>)"


def compose_runtime_dsn(config: RuntimeDatabaseConfig, provider: SecretProvider) -> RuntimeDsn:
    """Resolve the scoped password and build the runtime connection string."""
    password = provider.get_secret(config.password_ref)
    return RuntimeDsn(
        "postgresql://"
        f"{quote_plus(config.user)}:{quote_plus(password.reveal_text())}"
        f"@{config.host}:{config.port}/{config.dbname}?sslmode={config.sslmode}"
    )


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
        self.orders = PostgresOrderRepository(self)
        self.reconciliations = PostgresReconciliationRepository(self)
        self.control = PostgresControlRepository(self)
        self.account_baselines = PostgresAccountBaselineRepository(self)

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


_INTENT_COLUMNS = (
    "intent_id, client_order_id, strategy, trading_date, window_name, target_version,"
    " symbol, side, quantity, intent_type, limit_price, collar_reference_price,"
    " collar_offset_bps, earliest_submit_at, cancel_at, status, run_id, created_at"
)
_BROKER_ORDER_COLUMNS = (
    "broker_order_id, client_order_id, symbol, side, quantity, filled_quantity,"
    " limit_price, status, submitted_at, broker_updated_at, updated_at"
)


class PostgresOrderRepository:
    """Order-intent, broker-mirror, and fill persistence under DB-enforced guards."""

    def __init__(self, unit_of_work: PostgresUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def add(self, intent: OrderIntent) -> OrderIntent:
        """Create an intent; a duplicate client id must map to the identical intent."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO order_intents (
                    intent_id, client_order_id, strategy, trading_date, window_name,
                    target_version, symbol, side, quantity, intent_type, limit_price,
                    collar_reference_price, collar_offset_bps, earliest_submit_at,
                    cancel_at, status, run_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_order_id) DO NOTHING
                RETURNING {_INTENT_COLUMNS}
                """,
                (
                    intent.intent_id,
                    intent.client_order_id.value,
                    intent.strategy,
                    intent.trading_date.value,
                    intent.window,
                    intent.target_version,
                    intent.symbol.value,
                    intent.side.value,
                    intent.quantity.value,
                    intent.intent_type.value,
                    intent.limit_price.value,
                    intent.collar.reference.value,
                    intent.collar.offset_bps,
                    intent.earliest_submit_at.value,
                    intent.cancel_at.value,
                    intent.status.value,
                    intent.run_id.value,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                self._unit_of_work._mark_write()
                return _order_intent(_row(row, "order intent insert"))
            existing = self.get(intent.client_order_id)
        if existing is None or existing.intent_id != intent.intent_id:
            raise PersistenceInvariantError("client order id is bound to a different order intent")
        return existing

    def get(self, client_order_id: ClientOrderId) -> OrderIntent | None:
        """Load one intent by its deterministic client order id."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {_INTENT_COLUMNS}
                FROM order_intents
                WHERE client_order_id = %s
                """,
                (client_order_id.value,),
            )
            row = cursor.fetchone()
        return None if row is None else _order_intent(_row(row, "order intent lookup"))

    def list_by_status(self, status: OrderStatus) -> tuple[OrderIntent, ...]:
        """Load every intent currently in exactly this status."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {_INTENT_COLUMNS}
                FROM order_intents
                WHERE status = %s
                ORDER BY client_order_id
                """,
                (status.value,),
            )
            rows = cursor.fetchall()
        return tuple(_order_intent(_row(row, "order intent lookup")) for row in rows)

    def transition_status(self, client_order_id: ClientOrderId, target: OrderStatus) -> OrderIntent:
        """Persist one guarded status transition; database triggers reject illegal maps."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE order_intents SET status = %s
                WHERE client_order_id = %s
                RETURNING {_INTENT_COLUMNS}
                """,
                (target.value, client_order_id.value),
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceInvariantError("order intent disappeared during transition")
        self._unit_of_work._mark_write()
        return _order_intent(_row(row, "order intent transition"))

    def record_broker_order(self, order: BrokerOrder) -> BrokerOrder:
        """Insert or idempotently refresh the local mirror of a broker order."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO broker_orders (
                    broker_order_id, client_order_id, symbol, side, quantity,
                    filled_quantity, limit_price, status, submitted_at, broker_updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (broker_order_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    filled_quantity = EXCLUDED.filled_quantity,
                    broker_updated_at = EXCLUDED.broker_updated_at
                RETURNING {_BROKER_ORDER_COLUMNS}
                """,
                (
                    order.broker_order_id,
                    order.client_order_id.value,
                    order.symbol.value,
                    order.side.value,
                    order.quantity.value,
                    order.filled_quantity,
                    order.limit_price.value,
                    order.status.value,
                    order.submitted_at.value,
                    order.updated_at.value,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceInvariantError("broker order upsert did not return a row")
        self._unit_of_work._mark_write()
        return _broker_order(_row(row, "broker order upsert"))

    def get_broker_order(self, client_order_id: ClientOrderId) -> BrokerOrder | None:
        """Load the local mirror for one client order id."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {_BROKER_ORDER_COLUMNS}
                FROM broker_orders
                WHERE client_order_id = %s
                """,
                (client_order_id.value,),
            )
            row = cursor.fetchone()
        return None if row is None else _broker_order(_row(row, "broker order lookup"))

    def get_broker_order_by_id(self, broker_order_id: str) -> BrokerOrder | None:
        """Load the local mirror for one broker order id."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {_BROKER_ORDER_COLUMNS}
                FROM broker_orders
                WHERE broker_order_id = %s
                """,
                (broker_order_id,),
            )
            row = cursor.fetchone()
        return None if row is None else _broker_order(_row(row, "broker order lookup"))

    def update_broker_order_status(
        self,
        broker_order_id: str,
        status: BrokerOrderStatus,
        filled_quantity: int,
        *,
        broker_observed_at: UtcTimestamp | None = None,
    ) -> BrokerOrder:
        """Refresh the mutable mirror columns under the guarded broker status map."""
        observed_sql = ", broker_updated_at = %s" if broker_observed_at is not None else ""
        observed_value = broker_observed_at.value if broker_observed_at is not None else None
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE broker_orders SET status = %s, filled_quantity = %s{observed_sql}
                WHERE broker_order_id = %s
                RETURNING {_BROKER_ORDER_COLUMNS}
                """,
                (
                    status.value,
                    filled_quantity,
                    observed_value,
                    broker_order_id,
                )
                if broker_observed_at is not None
                else (status.value, filled_quantity, broker_order_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceInvariantError("broker order disappeared during refresh")
        self._unit_of_work._mark_write()
        return _broker_order(_row(row, "broker order refresh"))

    def add_fill(self, fill: Fill) -> bool:
        """Append one fill; a repeated broker execution id is an idempotent no-op."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO fills (
                    execution_id, broker_order_id, quantity, price, occurred_at
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (execution_id) DO NOTHING
                RETURNING fill_id
                """,
                (
                    fill.execution_id,
                    fill.broker_order_id,
                    fill.quantity.value,
                    fill.price.value,
                    fill.occurred_at.value,
                ),
            )
            row = cursor.fetchone()
        inserted = row is not None
        if inserted:
            self._unit_of_work._mark_write()
        return inserted

    def list_fills(self, broker_order_id: str) -> tuple[Fill, ...]:
        """Load fills for one broker order in recorded order."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                SELECT execution_id, broker_order_id, quantity, price, occurred_at
                FROM fills
                WHERE broker_order_id = %s
                ORDER BY fill_id
                """,
                (broker_order_id,),
            )
            rows = cursor.fetchall()
        return tuple(_fill(_row(row, "fill lookup")) for row in rows)

    def list_open_broker_orders(self) -> tuple[BrokerOrder, ...]:
        """Load every mirror whose broker status is not terminal."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {_BROKER_ORDER_COLUMNS}
                FROM broker_orders
                WHERE status NOT IN ('FILLED', 'CANCELED', 'EXPIRED', 'REJECTED')
                ORDER BY broker_order_id
                """
            )
            rows = cursor.fetchall()
        return tuple(_broker_order(_row(row, "broker order lookup")) for row in rows)

    def list_all_broker_orders(self) -> tuple[BrokerOrder, ...]:
        """Load every recorded broker order mirror."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {_BROKER_ORDER_COLUMNS}
                FROM broker_orders
                ORDER BY broker_order_id
                """
            )
            rows = cursor.fetchall()
        return tuple(_broker_order(_row(row, "broker order lookup")) for row in rows)

    def list_all_fills(self) -> tuple[Fill, ...]:
        """Load the entire append-only fill ledger in recorded order."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                SELECT execution_id, broker_order_id, quantity, price, occurred_at
                FROM fills
                ORDER BY fill_id
                """
            )
            rows = cursor.fetchall()
        return tuple(_fill(_row(row, "fill lookup")) for row in rows)


class PostgresReconciliationRepository:
    """Append-only reconciliation runs; PostgreSQL owns the recorded timestamp."""

    def __init__(self, unit_of_work: PostgresUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def add(self, result: ReconciliationResult) -> UtcTimestamp:
        """Insert one run and the verbatim detail of every mismatch."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO reconciliation_runs (
                    run_id, trading_date, status, mismatch_count, mismatch_kinds,
                    checked_orders, checked_fills, observed_at, scope
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING recorded_at
                """,
                (
                    result.run_id,
                    result.trading_date.value,
                    result.status.value,
                    len(result.mismatches),
                    [mismatch.kind.value for mismatch in result.mismatches],
                    result.checked_orders,
                    result.checked_fills,
                    result.observed_at.value,
                    result.scope.value,
                ),
            )
            row = _row(cursor.fetchone(), "reconciliation run insert")
            for ordinal, mismatch in enumerate(result.mismatches, start=1):
                cursor.execute(
                    """
                    INSERT INTO reconciliation_mismatches (run_id, ordinal, kind, detail)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (result.run_id, ordinal, mismatch.kind.value, mismatch.detail),
                )
        self._unit_of_work._mark_write()
        return _timestamp(row[0], "recorded_at")

    def latest(self) -> ReconciliationResult | None:
        """Load the most recently recorded reconciliation run with verified detail."""
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                SELECT run_id, trading_date, status, mismatch_count, mismatch_kinds,
                       checked_orders, checked_fills, observed_at, scope
                FROM reconciliation_runs
                ORDER BY recorded_at DESC, run_id DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if row is None:
                return None
            parent = _row(row, "reconciliation latest")
            if len(parent) != 9:
                raise PersistenceInvariantError(
                    "reconciliation latest query returned an invalid column count"
                )
            run_id = _uuid(parent[0], "run_id")
            mismatch_count = _integer(parent[3], "mismatch_count")
            kinds_raw = parent[4]
            if type(kinds_raw) is not list:
                raise PersistenceInvariantError("database mismatch_kinds must be an array")
            status = ReconciliationStatus(_text(parent[2], "status"))
            cursor.execute(
                """
                SELECT ordinal, kind, detail
                FROM reconciliation_mismatches
                WHERE run_id = %s
                ORDER BY ordinal
                """,
                (run_id,),
            )
            child_rows = cursor.fetchall()
            if len(child_rows) != mismatch_count:
                raise PersistenceInvariantError(
                    "reconciliation parent mismatch_count does not match child rows"
                )
            child_kinds: list[str] = []
            mismatches: list[ReconciliationMismatch] = []
            for index, child in enumerate(child_rows, start=1):
                c_row = _row(child, "reconciliation mismatch")
                if len(c_row) != 3:
                    raise PersistenceInvariantError(
                        "reconciliation mismatch query returned an invalid column count"
                    )
                ordinal = _integer(c_row[0], "ordinal")
                if ordinal != index:
                    raise PersistenceInvariantError(
                        "reconciliation mismatch ordinal must be contiguous from 1"
                    )
                kind_text = _text(c_row[1], "kind")
                detail = _text(c_row[2], "detail")
                child_kinds.append(kind_text)
                mismatches.append(
                    ReconciliationMismatch(kind=MismatchKind(kind_text), detail=detail)
                )
            if child_kinds != kinds_raw:
                raise PersistenceInvariantError(
                    "reconciliation parent mismatch_kinds does not match child rows"
                )
            if (status is ReconciliationStatus.CLEAN) is bool(mismatches):
                raise PersistenceInvariantError(
                    "reconciliation status does not match mismatch presence"
                )
            return ReconciliationResult(
                run_id=run_id,
                trading_date=_trading_date(parent[1]),
                status=status,
                mismatches=tuple(mismatches),
                checked_orders=_integer(parent[5], "checked_orders"),
                checked_fills=_integer(parent[6], "checked_fills"),
                observed_at=_timestamp(parent[7], "observed_at"),
                scope=ReconciliationScope(_text(parent[8], "scope")),
            )


class PostgresControlRepository:
    """Control state and the append-only operator command log."""

    def __init__(self, unit_of_work: PostgresUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def state(self) -> ControlStateSnapshot:
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                SELECT entries_paused, paused_reason, updated_at, flatten_generation
                FROM control_state
                WHERE singleton
                """
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceInvariantError("control state row is missing")
        paused_reason = row[1]
        if paused_reason is not None and type(paused_reason) is not str:
            raise PersistenceInvariantError("database paused_reason must be text or null")
        return ControlStateSnapshot(
            entries_paused=_boolean(row[0], "entries_paused"),
            paused_reason=paused_reason,
            updated_at=_timestamp(row[2], "updated_at"),
            flatten_generation=_integer(row[3], "flatten_generation"),
        )

    @contextmanager
    def submission_guard(self) -> Iterator[ControlStateSnapshot]:
        """Hold the new-entry authority row exclusively until submission resolves.

        New-entry submissions are linearized with FOR UPDATE so two concurrent
        entries cannot both cross the broker boundary while the first is still
        racing toward UNKNOWN.  RISK_EXIT bypasses this guard entirely.
        """
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                SELECT entries_paused, paused_reason, updated_at, flatten_generation
                FROM control_state
                WHERE singleton
                FOR UPDATE
                """
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceInvariantError("control state row is missing")
        paused_reason = row[1]
        if paused_reason is not None and type(paused_reason) is not str:
            raise PersistenceInvariantError("database paused_reason must be text or null")
        try:
            yield ControlStateSnapshot(
                entries_paused=_boolean(row[0], "entries_paused"),
                paused_reason=paused_reason,
                updated_at=_timestamp(row[2], "updated_at"),
                flatten_generation=_integer(row[3], "flatten_generation"),
            )
        except BaseException:
            self._unit_of_work.rollback()
            raise
        else:
            # Release the row lock even when the control repository is backed
            # by a dedicated UoW rather than the order-writing UoW.
            self._unit_of_work.commit()

    def bump_flatten_generation(self) -> int:
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                UPDATE control_state
                SET flatten_generation = flatten_generation + 1
                WHERE singleton
                RETURNING flatten_generation
                """
            )
            row = _row(cursor.fetchone(), "control state flatten generation bump")
        self._unit_of_work._mark_write()
        return _integer(row[0], "flatten_generation")

    def set_entries_paused(self, paused: bool, reason: str | None) -> ControlStateSnapshot:
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                UPDATE control_state
                SET entries_paused = %s, paused_reason = %s
                WHERE singleton
                RETURNING entries_paused, paused_reason, updated_at, flatten_generation
                """,
                (paused, reason),
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceInvariantError("control state row is missing")
        paused_reason = row[1]
        if paused_reason is not None and type(paused_reason) is not str:
            raise PersistenceInvariantError("database paused_reason must be text or null")
        self._unit_of_work._mark_write()
        return ControlStateSnapshot(
            entries_paused=_boolean(row[0], "entries_paused"),
            paused_reason=paused_reason,
            updated_at=_timestamp(row[2], "updated_at"),
            flatten_generation=_integer(row[3], "flatten_generation"),
        )

    def add_command(self, record: ControlCommandRecord) -> UtcTimestamp | None:
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO control_commands (
                    command_id, command, reason, actor, run_id, requested_at, applied_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING applied_at
                """,
                (
                    record.command_id,
                    record.command.value,
                    record.reason,
                    record.actor,
                    record.run_id,
                    record.requested_at.value,
                    None if record.applied_at is None else record.applied_at.value,
                ),
            )
            row = _row(cursor.fetchone(), "control command insert")
        self._unit_of_work._mark_write()
        if row[0] is None:
            return None
        return _timestamp(row[0], "applied_at")


@dataclass(frozen=True, slots=True)
class AccountBaseline:
    """Authoritative opening cash for one Paper account."""

    account_id: str
    opening_cash_cents: int
    effective_at: UtcTimestamp
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    revision_id: UUID | None = None
    cutoff_occurred_at: UtcTimestamp | None = None
    cutoff_execution_id: str | None = None
    reason: str | None = None
    actor: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.account_id) is not str
            or not self.account_id.strip()
            or len(self.account_id) > 100
        ):
            raise ValueError("account_id must be non-empty text up to 100 characters")
        if type(self.opening_cash_cents) is not int or self.opening_cash_cents < 0:
            raise ValueError("opening_cash_cents must be a non-negative integer")
        if not isinstance(self.effective_at, UtcTimestamp):
            raise ValueError("effective_at must be a UtcTimestamp")
        if not isinstance(self.created_at, UtcTimestamp):
            raise ValueError("created_at must be a UtcTimestamp")
        if not isinstance(self.updated_at, UtcTimestamp):
            raise ValueError("updated_at must be a UtcTimestamp")
        if self.revision_id is not None and not isinstance(self.revision_id, UUID):
            raise ValueError("revision_id must be a UUID")
        if self.cutoff_occurred_at is not None and not isinstance(
            self.cutoff_occurred_at, UtcTimestamp
        ):
            raise ValueError("cutoff_occurred_at must be a UtcTimestamp")
        if self.cutoff_execution_id is not None and (
            type(self.cutoff_execution_id) is not str
            or not self.cutoff_execution_id.strip()
            or len(self.cutoff_execution_id) > 100
        ):
            raise ValueError("cutoff_execution_id must be bounded text")
        if self.reason is not None and (
            type(self.reason) is not str or not self.reason.strip() or len(self.reason) > 200
        ):
            raise ValueError("reason must be bounded text")
        if self.actor is not None and (
            type(self.actor) is not str or not self.actor.strip() or len(self.actor) > 100
        ):
            raise ValueError("actor must be bounded text")


class PostgresAccountBaselineRepository:
    """Persistence for the explicit opening-cash baseline (append-only revisions)."""

    def __init__(self, unit_of_work: PostgresUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def set_baseline(
        self, account_id: str, opening_cash_cents: int, effective_at: UtcTimestamp
    ) -> AccountBaseline:
        """Create the genesis baseline; fails if the account already has one.

        Genesis has no ledger cutoff and is only allowed when the fill ledger
        is empty.  The check is enforced transactionally with a table lock so
        a concurrent first fill cannot race the genesis creation.
        """
        if type(account_id) is not str or not account_id.strip() or len(account_id) > 100:
            raise ValueError("account_id must be non-empty text up to 100 characters")
        if type(opening_cash_cents) is not int or opening_cash_cents < 0:
            raise ValueError("opening_cash_cents must be a non-negative integer")
        if not isinstance(effective_at, UtcTimestamp):
            raise ValueError("effective_at must be a UtcTimestamp")
        with self._unit_of_work._require_connection().cursor() as cursor:
            # Serialize genesis against the first fill.
            cursor.execute("LOCK TABLE fills IN EXCLUSIVE MODE")
            cursor.execute("SELECT 1 FROM fills LIMIT 1")
            if cursor.fetchone() is not None:
                raise ValueError("genesis baseline requires empty fill ledger")
            # Append-only: plain INSERT, no ON CONFLICT, so duplicate fails.
            cursor.execute(
                """
                INSERT INTO account_baselines (account_id, opening_cash_cents, effective_at)
                VALUES (%s, %s, %s)
                RETURNING account_id, opening_cash_cents, effective_at, created_at, updated_at
                """,
                (account_id, opening_cash_cents, effective_at.value),
            )
            row = _row(cursor.fetchone(), "account baseline insert")
            # Also record as initial revision for cutoff-aware reads.
            cursor.execute(
                """
                INSERT INTO account_baseline_revisions
                    (account_id, opening_cash_cents, effective_at,
                     cutoff_occurred_at, cutoff_execution_id, reason, actor)
                VALUES (%s, %s, %s, NULL, NULL, 'genesis', 'system')
                RETURNING revision_id, account_id, opening_cash_cents,
                    effective_at, cutoff_occurred_at, cutoff_execution_id,
                    reason, actor, created_at
                """,
                (account_id, opening_cash_cents, effective_at.value),
            )
            _row(cursor.fetchone(), "account baseline revision insert")
        self._unit_of_work._mark_write()
        return _account_baseline(_row(row, "account baseline insert"))

    def add_revision(
        self,
        account_id: str,
        opening_cash_cents: int,
        effective_at: UtcTimestamp,
        cutoff_occurred_at: UtcTimestamp | None,
        cutoff_execution_id: str | None,
        reason: str,
        actor: str,
    ) -> AccountBaseline:
        if type(account_id) is not str or not account_id.strip() or len(account_id) > 100:
            raise ValueError("account_id must be bounded text")
        if type(opening_cash_cents) is not int or opening_cash_cents < 0:
            raise ValueError("opening_cash_cents must be non-negative")
        if not isinstance(effective_at, UtcTimestamp):
            raise ValueError("effective_at must be a UtcTimestamp")
        if cutoff_occurred_at is not None and not isinstance(cutoff_occurred_at, UtcTimestamp):
            raise ValueError("cutoff_occurred_at must be a UtcTimestamp or None")
        if cutoff_execution_id is not None and (
            type(cutoff_execution_id) is not str
            or not cutoff_execution_id.strip()
            or len(cutoff_execution_id) > 100
        ):
            raise ValueError("cutoff_execution_id must be bounded text")
        if type(reason) is not str or not reason.strip() or len(reason) > 200:
            raise ValueError("reason must be bounded text")
        if type(actor) is not str or not actor.strip() or len(actor) > 100:
            raise ValueError("actor must be bounded text")
        if (cutoff_occurred_at is None) != (cutoff_execution_id is None):
            raise ValueError(
                "cutoff_occurred_at and cutoff_execution_id must be both set or both None"
            )
        with self._unit_of_work._require_connection().cursor() as cursor:
            # If fills exist, a revision must carry an explicit deterministic cutoff
            # referencing a real ledger boundary (occurred_at, execution_id).
            cursor.execute("SELECT 1 FROM fills LIMIT 1")
            has_fills = cursor.fetchone() is not None
            if has_fills and cutoff_occurred_at is None:
                raise ValueError("revision after fills requires explicit cutoff")
            if cutoff_occurred_at is not None and cutoff_execution_id is not None:
                cursor.execute(
                    "SELECT 1 FROM fills WHERE execution_id = %s AND occurred_at = %s",
                    (cutoff_execution_id, cutoff_occurred_at.value),
                )
                if cursor.fetchone() is None:
                    raise ValueError("cutoff must reference an existing fill")
            cursor.execute(
                """
                INSERT INTO account_baseline_revisions
                    (account_id, opening_cash_cents, effective_at,
                     cutoff_occurred_at, cutoff_execution_id, reason, actor)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING revision_id, account_id, opening_cash_cents,
                    effective_at, cutoff_occurred_at, cutoff_execution_id,
                    reason, actor, created_at
                """,
                (
                    account_id,
                    opening_cash_cents,
                    effective_at.value,
                    None if cutoff_occurred_at is None else cutoff_occurred_at.value,
                    cutoff_execution_id,
                    reason,
                    actor,
                ),
            )
            rev_row = _row(cursor.fetchone(), "account baseline revision insert")
            # Upsert the materialized latest row for backward compat reads is no longer allowed;
            # keep account_baselines as genesis-only, so we do not touch it on revision.
        self._unit_of_work._mark_write()
        return _account_baseline_revision(_row(rev_row, "account baseline revision insert"))

    def get_baseline(self, account_id: str) -> AccountBaseline | None:
        if type(account_id) is not str or not account_id.strip():
            raise ValueError("account_id must be non-empty text")
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                SELECT revision_id, account_id, opening_cash_cents, effective_at,
                    cutoff_occurred_at, cutoff_execution_id, reason, actor, created_at
                FROM account_baseline_revisions
                WHERE account_id = %s
                ORDER BY effective_at DESC, created_at DESC
                LIMIT 1
                """,
                (account_id,),
            )
            row = cursor.fetchone()
            if row is not None:
                return _account_baseline_revision(_row(row, "account baseline revision lookup"))
            # Fallback to genesis table for DBs that have not yet been migrated via 0009's data copy
            cursor.execute(
                """
                SELECT account_id, opening_cash_cents, effective_at, created_at, updated_at
                FROM account_baselines
                WHERE account_id = %s
                """,
                (account_id,),
            )
            row = cursor.fetchone()
        return None if row is None else _account_baseline(_row(row, "account baseline lookup"))

    def list_baselines(self) -> tuple[AccountBaseline, ...]:
        with self._unit_of_work._require_connection().cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT ON (account_id)
                    revision_id, account_id, opening_cash_cents, effective_at,
                    cutoff_occurred_at, cutoff_execution_id, reason, actor, created_at
                FROM account_baseline_revisions
                ORDER BY account_id, effective_at DESC, created_at DESC
                """
            )
            rows = cursor.fetchall()
            if rows:
                return tuple(
                    _account_baseline_revision(_row(row, "account baseline revision lookup"))
                    for row in rows
                )
            cursor.execute(
                """
                SELECT account_id, opening_cash_cents, effective_at, created_at, updated_at
                FROM account_baselines
                ORDER BY account_id
                """
            )
            rows = cursor.fetchall()
        return tuple(_account_baseline(_row(row, "account baseline lookup")) for row in rows)


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


def _boolean(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise PersistenceInvariantError(f"database {field_name} must be a boolean")
    return value


def _reconciliation_result(row: tuple[object, ...]) -> ReconciliationResult:
    if len(row) not in (8, 9):
        raise PersistenceInvariantError("reconciliation query returned an invalid column count")
    scope = ReconciliationScope.PARTIAL
    if len(row) == 9:
        scope = ReconciliationScope(_text(row[8], "scope"))
    kinds_raw = row[4]
    if type(kinds_raw) is not list:
        raise PersistenceInvariantError("database mismatch_kinds must be an array")
    mismatches = tuple(
        ReconciliationMismatch(kind=MismatchKind(kind), detail=kind) for kind in kinds_raw
    )
    return ReconciliationResult(
        run_id=_uuid(row[0], "run_id"),
        trading_date=_trading_date(row[1]),
        status=ReconciliationStatus(_text(row[2], "status")),
        mismatches=mismatches,
        checked_orders=_integer(row[5], "checked_orders"),
        checked_fills=_integer(row[6], "checked_fills"),
        observed_at=_timestamp(row[7], "observed_at"),
        scope=scope,
    )


def _uuid(value: object, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise PersistenceInvariantError(f"database {field_name} must be a UUID")
    return value


def _decimal(value: object, field_name: str) -> Decimal:
    if type(value) is not Decimal:
        raise PersistenceInvariantError(f"database {field_name} must be a numeric")
    return value


def _order_intent(row: tuple[object, ...]) -> OrderIntent:
    if len(row) != 18:
        raise PersistenceInvariantError("order intent query returned an invalid column count")
    return OrderIntent(
        intent_id=_uuid(row[0], "intent_id"),
        client_order_id=ClientOrderId(_text(row[1], "client_order_id")),
        strategy=_text(row[2], "strategy"),
        trading_date=_trading_date(row[3]),
        window=_text(row[4], "window_name"),
        target_version=_integer(row[5], "target_version"),
        symbol=Symbol(_text(row[6], "symbol")),
        side=OrderSide(_text(row[7], "side")),
        quantity=OrderQuantity(_integer(row[8], "quantity")),
        intent_type=OrderIntentType(_text(row[9], "intent_type")),
        limit_price=Price(_decimal(row[10], "limit_price")),
        collar=PriceCollar(
            reference=Price(_decimal(row[11], "collar_reference_price")),
            offset_bps=_integer(row[12], "collar_offset_bps"),
        ),
        earliest_submit_at=_timestamp(row[13], "earliest_submit_at"),
        cancel_at=_timestamp(row[14], "cancel_at"),
        status=OrderStatus(_text(row[15], "status")),
        run_id=RunId(_uuid(row[16], "run_id")),
        created_at=_timestamp(row[17], "created_at"),
    )


def _broker_order(row: tuple[object, ...]) -> BrokerOrder:
    if len(row) != 11:
        raise PersistenceInvariantError("broker order query returned an invalid column count")
    broker_watermark = row[9]
    if broker_watermark is not None:
        updated_at = _timestamp(broker_watermark, "broker_updated_at")
    else:
        # A NULL watermark means the broker timestamp is unknown (0007 cleared
        # the suspect 0006 backfill).  The submitted_at lower bound can never
        # fabricate a barrier that hides a real broker event.
        updated_at = _timestamp(row[8], "submitted_at")
    return BrokerOrder(
        broker_order_id=_text(row[0], "broker_order_id"),
        client_order_id=ClientOrderId(_text(row[1], "client_order_id")),
        symbol=Symbol(_text(row[2], "symbol")),
        side=OrderSide(_text(row[3], "side")),
        quantity=OrderQuantity(_integer(row[4], "quantity")),
        filled_quantity=_integer(row[5], "filled_quantity"),
        limit_price=Price(_decimal(row[6], "limit_price")),
        status=BrokerOrderStatus(_text(row[7], "status")),
        submitted_at=_timestamp(row[8], "submitted_at"),
        updated_at=updated_at,
    )


def _fill(row: tuple[object, ...]) -> Fill:
    if len(row) != 5:
        raise PersistenceInvariantError("fill query returned an invalid column count")
    return Fill(
        execution_id=_text(row[0], "execution_id"),
        broker_order_id=_text(row[1], "broker_order_id"),
        quantity=OrderQuantity(_integer(row[2], "quantity")),
        price=Price(_decimal(row[3], "price")),
        occurred_at=_timestamp(row[4], "occurred_at"),
    )


def _account_baseline(row: tuple[object, ...]) -> AccountBaseline:
    if len(row) != 5:
        raise PersistenceInvariantError("account baseline query returned an invalid column count")
    return AccountBaseline(
        account_id=_text(row[0], "account_id"),
        opening_cash_cents=_integer(row[1], "opening_cash_cents"),
        effective_at=_timestamp(row[2], "effective_at"),
        created_at=_timestamp(row[3], "created_at"),
        updated_at=_timestamp(row[4], "updated_at"),
    )


def _account_baseline_revision(row: tuple[object, ...]) -> AccountBaseline:
    if len(row) != 9:
        raise PersistenceInvariantError(
            "account baseline revision query returned an invalid column count"
        )
    cutoff_at_raw = row[4]
    cutoff_id_raw = row[5]
    return AccountBaseline(
        account_id=_text(row[1], "account_id"),
        opening_cash_cents=_integer(row[2], "opening_cash_cents"),
        effective_at=_timestamp(row[3], "effective_at"),
        created_at=_timestamp(row[8], "created_at"),
        updated_at=_timestamp(row[8], "created_at"),
        revision_id=_uuid(row[0], "revision_id"),
        cutoff_occurred_at=None
        if cutoff_at_raw is None
        else _timestamp(cutoff_at_raw, "cutoff_occurred_at"),
        cutoff_execution_id=None
        if cutoff_id_raw is None
        else _text(cutoff_id_raw, "cutoff_execution_id"),
        reason=_text(row[6], "reason"),
        actor=_text(row[7], "actor"),
    )
