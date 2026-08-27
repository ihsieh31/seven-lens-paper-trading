# mypy: ignore-errors
# ruff: noqa: B017, E501, F401, I001, RUF021, SIM117
"""Integration tests for P2 final ACC-001,003,004,005,007 (PostgreSQL)."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import cast

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from seven_lens.application.execution_service import ExecutionEngine, ExecutionPausedError
from seven_lens.application.ports.broker import BrokerTransportError, PaperPosition, SubmitAccepted
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.fake_broker import FakePaperBroker
from seven_lens.execution.orders import (
    BrokerOrder,
    BrokerOrderStatus,
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
from seven_lens.infrastructure.migrations import current_version, migrate, rollback
from seven_lens.infrastructure.postgres import PostgresUnitOfWork

pytestmark = pytest.mark.integration

_BASE = UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z")
_CANCEL = UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z")
_TD = TradingDate.from_isoformat("2026-08-17")


def _intent(version: int, typ: OrderIntentType = OrderIntentType.REBALANCE) -> OrderIntent:
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=_TD,
        window="open",
        target_version=version,
        symbol=Symbol("AAPL"),
        side=OrderSide.BUY,
        quantity=OrderQuantity(10),
        intent_type=typ,
        limit_price=Price.from_cents(10_000),
        collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
        earliest_submit_at=_BASE,
        cancel_at=_CANCEL,
        run_id=RunId.new(),
        created_at=_BASE,
    )


def _seed_outbox(uow: PostgresUnitOfWork, intent: OrderIntent) -> None:
    uow.orders.add(intent)
    uow.orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
    uow.orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
    uow.commit()


class _BlockingBroker(FakePaperBroker):
    def __init__(self, a_id: str) -> None:
        super().__init__(clock=lambda: _BASE)
        self.a_id = a_id
        self.a_started = threading.Event()
        self.allow_a = threading.Event()
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def submit_order(self, intent: OrderIntent):
        cid = intent.client_order_id.value
        with self._lock:
            self.calls.append(cid)
        if cid == self.a_id:
            self.a_started.set()
            if not self.allow_a.wait(timeout=5):
                raise AssertionError("allow_a not set")
            raise BrokerTransportError("injected timeout")
        return super().submit_order(intent)


class _CountingBroker(FakePaperBroker):
    def __init__(self) -> None:
        super().__init__(clock=lambda: _BASE)
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def submit_order(self, intent: OrderIntent):
        with self._lock:
            self.calls.append(intent.client_order_id.value)
        return super().submit_order(intent)


# ---------------------------------------------------------------------------
# ACC-001
# ---------------------------------------------------------------------------


def test_pg_timeout_unknown_blocks_racing_second_entry_at_broker_boundary(
    migrated_postgres: str,
) -> None:
    a_intent = _intent(101)
    b_intent = _intent(102)
    with PostgresUnitOfWork(migrated_postgres) as uow:
        _seed_outbox(uow, a_intent)
        _seed_outbox(uow, b_intent)

    broker = _BlockingBroker(a_intent.client_order_id.value)
    errors_a: list[BaseException] = []
    errors_b: list[BaseException] = []
    result_a: list[OrderIntent] = []
    result_b: list[OrderIntent] = []

    def run_a() -> None:
        try:
            with PostgresUnitOfWork(migrated_postgres) as uow:
                engine = ExecutionEngine(broker=broker, clock=lambda: _BASE, control=uow.control)
                res = engine.submit_from_outbox(uow, a_intent.client_order_id)
                result_a.append(res)
        except BaseException as e:
            errors_a.append(e)

    def run_b() -> None:
        try:
            # Small delay to ensure A is inside broker call
            assert broker.a_started.wait(timeout=5)
            time.sleep(0.05)
            # Verify B has not yet reached broker before releasing A
            with broker._lock:
                assert b_intent.client_order_id.value not in broker.calls, (
                    "B reached broker before A timeout"
                )
            with PostgresUnitOfWork(migrated_postgres) as uow:
                engine = ExecutionEngine(broker=broker, clock=lambda: _BASE, control=uow.control)
                res = engine.submit_from_outbox(uow, b_intent.client_order_id)
                result_b.append(res)
        except BaseException as e:
            errors_b.append(e)

    t_a = threading.Thread(target=run_a)
    t_b = threading.Thread(target=run_b)
    t_a.start()
    assert broker.a_started.wait(timeout=5)
    t_b.start()
    # Give B time to block on FOR UPDATE before releasing A
    time.sleep(0.3)
    # Still should not have called B
    with broker._lock:
        assert b_intent.client_order_id.value not in broker.calls
    broker.allow_a.set()
    t_a.join(timeout=5)
    t_b.join(timeout=5)
    assert not t_a.is_alive(), "A thread deadlocked"
    assert not t_b.is_alive(), "B thread deadlocked"
    # A should be UNKNOWN
    assert result_a and result_a[0].status is OrderStatus.UNKNOWN
    # Verify A is UNKNOWN in DB
    with psycopg.connect(migrated_postgres) as conn:
        row = conn.execute(
            "SELECT status FROM order_intents WHERE client_order_id=%s",
            (a_intent.client_order_id.value,),
        ).fetchone()
        assert row[0] == "UNKNOWN"
        paused = conn.execute("SELECT entries_paused FROM control_state WHERE singleton").fetchone()
        assert paused[0] is True
    # B should have failed closed without broker call
    with broker._lock:
        assert broker.calls.count(b_intent.client_order_id.value) == 0, (
            f"B broker calls {broker.calls}"
        )
    # B result should be not submitted, or exception ExecutionPausedError
    # In our implementation, B will see UNKNOWN and raise ExecutionPausedError inside guard, then transition to UNKNOWN if it was SUBMITTING? Wait B started as OUTBOX_PENDING and guard checks UNKNOWN before SUBMITTING.
    # Actually B's submit_from_outbox will check entries_paused/UNKNOWN before transitioning to SUBMITTING, so it should raise ExecutionPausedError directly.
    assert (
        errors_b
        and isinstance(errors_b[0], ExecutionPausedError)
        or (result_b and result_b[0].status in (OrderStatus.UNKNOWN, OrderStatus.OUTBOX_PENDING))
    )
    # Ensure B never became ACKNOWLEDGED
    with psycopg.connect(migrated_postgres) as conn:
        b_row = conn.execute(
            "SELECT status FROM order_intents WHERE client_order_id=%s",
            (b_intent.client_order_id.value,),
        ).fetchone()
        assert b_row[0] != "ACKNOWLEDGED"
    assert not errors_a


def test_pg_successful_entry_serialization_releases_next_entry(migrated_postgres: str) -> None:
    a_intent = _intent(201)
    b_intent = _intent(202)
    with PostgresUnitOfWork(migrated_postgres) as uow:
        _seed_outbox(uow, a_intent)
        _seed_outbox(uow, b_intent)
    broker = _CountingBroker()
    with PostgresUnitOfWork(migrated_postgres) as uow:
        engine = ExecutionEngine(broker=broker, clock=lambda: _BASE, control=uow.control)
        res_a = engine.submit_from_outbox(uow, a_intent.client_order_id)
        assert res_a.status is OrderStatus.ACKNOWLEDGED
    with PostgresUnitOfWork(migrated_postgres) as uow:
        engine = ExecutionEngine(broker=broker, clock=lambda: _BASE, control=uow.control)
        res_b = engine.submit_from_outbox(uow, b_intent.client_order_id)
        assert res_b.status is OrderStatus.ACKNOWLEDGED
    assert broker.calls.count(a_intent.client_order_id.value) == 1
    assert broker.calls.count(b_intent.client_order_id.value) == 1


def test_pg_unknown_gate_restart_blocks_entry(migrated_postgres: str) -> None:
    a_intent = _intent(301)
    broker = FakePaperBroker(clock=lambda: _BASE)
    # Make A timeout
    from seven_lens.execution.fake_broker import FakeSubmitOutcome, FakeSubmitPlan

    broker2 = FakePaperBroker(
        clock=lambda: _BASE,
        plans={
            a_intent.client_order_id.value: FakeSubmitPlan(
                outcome=FakeSubmitOutcome.TIMEOUT_BEFORE_ACCEPT
            )
        },
    )
    with PostgresUnitOfWork(migrated_postgres) as uow:
        _seed_outbox(uow, a_intent)
        engine = ExecutionEngine(broker=broker2, clock=lambda: _BASE, control=uow.control)
        res = engine.submit_from_outbox(uow, a_intent.client_order_id)
        assert res.status is OrderStatus.UNKNOWN
    # Restart: new entry should be blocked
    b_intent = _intent(302)
    with PostgresUnitOfWork(migrated_postgres) as uow:
        _seed_outbox(uow, b_intent)
        engine = ExecutionEngine(broker=broker, clock=lambda: _BASE, control=uow.control)
        with pytest.raises(ExecutionPausedError):
            engine.submit_from_outbox(uow, b_intent.client_order_id)


def test_pg_entry_serialization_does_not_block_risk_exit(migrated_postgres: str) -> None:
    a_intent = _intent(401)
    risk_intent = OrderIntent.create(
        strategy="seven-lens",
        trading_date=_TD,
        window="open",
        target_version=402,
        symbol=Symbol("AAPL"),
        side=OrderSide.SELL,
        quantity=OrderQuantity(1),
        intent_type=OrderIntentType.RISK_EXIT,
        limit_price=Price.from_cents(10_000),
        collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
        earliest_submit_at=_BASE,
        cancel_at=_CANCEL,
        run_id=RunId.new(),
        created_at=_BASE,
    )
    with PostgresUnitOfWork(migrated_postgres) as uow:
        _seed_outbox(uow, a_intent)
        uow.orders.add(risk_intent)
        uow.orders.transition_status(risk_intent.client_order_id, OrderStatus.RISK_APPROVED)
        uow.orders.transition_status(risk_intent.client_order_id, OrderStatus.OUTBOX_PENDING)
        uow.commit()
    broker = _BlockingBroker(a_intent.client_order_id.value)
    errors: list[BaseException] = []
    risk_result: list[OrderIntent] = []

    def run_a():
        try:
            with PostgresUnitOfWork(migrated_postgres) as uow:
                engine = ExecutionEngine(broker=broker, clock=lambda: _BASE, control=uow.control)
                engine.submit_from_outbox(uow, a_intent.client_order_id)
        except BaseException as e:
            errors.append(e)

    def run_risk():
        try:
            assert broker.a_started.wait(timeout=5)
            time.sleep(0.1)
            with PostgresUnitOfWork(migrated_postgres) as uow:
                engine = ExecutionEngine(broker=broker, clock=lambda: _BASE, control=uow.control)
                res = engine.submit_from_outbox(uow, risk_intent.client_order_id)
                risk_result.append(res)
        except BaseException as e:
            errors.append(e)

    t_a = threading.Thread(target=run_a)
    t_r = threading.Thread(target=run_risk)
    t_a.start()
    assert broker.a_started.wait(timeout=5)
    t_r.start()
    # Risk exit should succeed even while A holds lock
    t_r.join(timeout=5)
    assert not t_r.is_alive()
    assert risk_result and risk_result[0].status is OrderStatus.ACKNOWLEDGED
    broker.allow_a.set()
    t_a.join(timeout=5)
    assert not errors or all(isinstance(e, BrokerTransportError) is False for e in errors)


def test_pg_entry_guard_does_not_self_deadlock(migrated_postgres: str) -> None:
    intent = _intent(501)
    broker = FakePaperBroker(clock=lambda: _BASE)
    with PostgresUnitOfWork(migrated_postgres) as uow:
        _seed_outbox(uow, intent)
        engine = ExecutionEngine(broker=broker, clock=lambda: _BASE, control=uow.control)
        res = engine.submit_from_outbox(uow, intent.client_order_id)
        assert res.status is OrderStatus.ACKNOWLEDGED
        # Second submit of same id is no-op, should not deadlock
        res2 = engine.submit_from_outbox(uow, intent.client_order_id)
        assert res2.status is OrderStatus.ACKNOWLEDGED


# ---------------------------------------------------------------------------
# ACC-003 runtime role deny
# ---------------------------------------------------------------------------


def test_runtime_role_cannot_insert_account_baseline(migrated_postgres: str) -> None:
    runtime_role = "seven_lens_runtime_test_acc003"
    runtime_pwd = "p1-acc003"
    with psycopg.connect(migrated_postgres, autocommit=True) as conn:
        conn.execute(
            psycopg.sql.SQL("DROP ROLE IF EXISTS {}").format(psycopg.sql.Identifier(runtime_role))
        )
        conn.execute(
            psycopg.sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(psycopg.sql.Identifier(runtime_role), psycopg.sql.Literal(runtime_pwd))
        )
    from seven_lens.infrastructure.postgres_roles import provision_runtime_role

    provision_runtime_role(migrated_postgres, runtime_role)
    runtime_dsn = make_conninfo(migrated_postgres, user=runtime_role, password=runtime_pwd)
    from seven_lens.infrastructure.postgres import PostgresUnitOfWork as PUoW

    # Runtime should be able to SELECT baseline (none yet) but not INSERT
    with PUoW(runtime_dsn) as uow:
        assert uow.account_baselines.get_baseline("paper-1") is None
        uow.commit()
    with pytest.raises(Exception) as exc:
        with PUoW(runtime_dsn) as uow:
            uow.account_baselines.set_baseline("paper-1", 1_000_000, _BASE)
            uow.commit()
    assert exc.value.__class__.__name__ != "AssertionError"
    # Must be permission error
    assert (
        "permission" in str(exc.value).lower()
        or "42501" in str(exc.value)
        or "42501" in str(getattr(exc.value, "sqlstate", ""))
    )
    # Verify privileged path can create genesis (owner)
    with PostgresUnitOfWork(migrated_postgres) as uow:
        b = uow.account_baselines.set_baseline("paper-owner-1", 1_000_000, _BASE)
        assert b.account_id == "paper-owner-1"
        uow.commit()
    # Cleanup role
    with psycopg.connect(migrated_postgres, autocommit=True) as conn:
        conn.execute(
            psycopg.sql.SQL("DROP OWNED BY {}").format(psycopg.sql.Identifier(runtime_role))
        )
        conn.execute(psycopg.sql.SQL("DROP ROLE {}").format(psycopg.sql.Identifier(runtime_role)))


def test_runtime_role_cannot_insert_account_baseline_revision(migrated_postgres: str) -> None:
    runtime_role = "seven_lens_runtime_test_acc003b"
    runtime_pwd = "p1-acc003b"
    with psycopg.connect(migrated_postgres, autocommit=True) as conn:
        conn.execute(
            psycopg.sql.SQL("DROP ROLE IF EXISTS {}").format(psycopg.sql.Identifier(runtime_role))
        )
        conn.execute(
            psycopg.sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(psycopg.sql.Identifier(runtime_role), psycopg.sql.Literal(runtime_pwd))
        )
    from seven_lens.infrastructure.postgres_roles import provision_runtime_role

    provision_runtime_role(migrated_postgres, runtime_role)
    runtime_dsn = make_conninfo(migrated_postgres, user=runtime_role, password=runtime_pwd)
    # First create a baseline via owner
    with PostgresUnitOfWork(migrated_postgres) as uow:
        # Use a fresh account
        uow.account_baselines.set_baseline("paper-rev-1", 1_000_000, _BASE)
        uow.commit()
        # Insert a fill so revision requires cutoff
        # Create minimal order and fill for cutoff reference
        intent = _intent(900)
        uow.orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            uow.orders.transition_status(intent.client_order_id, s)
        mirror = BrokerOrder(
            broker_order_id="b-rev-1",
            client_order_id=intent.client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(10),
            filled_quantity=10,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=_BASE,
            updated_at=_BASE,
        )
        uow.orders.record_broker_order(mirror)
        uow.orders.add_fill(
            Fill(
                execution_id="e-rev-1",
                broker_order_id="b-rev-1",
                quantity=OrderQuantity(10),
                price=Price.from_cents(10_000),
                occurred_at=_BASE,
            )
        )
        uow.commit()
    # Now runtime tries to add revision
    from seven_lens.infrastructure.postgres import PostgresUnitOfWork as PUoW

    with pytest.raises(Exception):
        with PUoW(runtime_dsn) as uow:
            uow.account_baselines.add_revision(
                "paper-rev-1", 1_100_000, _BASE, _BASE, "e-rev-1", "test", "op"
            )
            uow.commit()
    # Cleanup
    with psycopg.connect(migrated_postgres, autocommit=True) as conn:
        conn.execute(
            psycopg.sql.SQL("DROP OWNED BY {}").format(psycopg.sql.Identifier(runtime_role))
        )
        conn.execute(psycopg.sql.SQL("DROP ROLE {}").format(psycopg.sql.Identifier(runtime_role)))


# ---------------------------------------------------------------------------
# ACC-004 migration compatibility
# ---------------------------------------------------------------------------


def _migrate_to_version(dsn: str, target: int) -> None:
    from seven_lens.infrastructure.migrations import _load_migrations
    import psycopg

    migrations = _load_migrations()
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.schema_migrations')")
            if cur.fetchone()[0] is None:
                cur_ver = 0
            else:
                cur.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations")
                cur_ver = cur.fetchone()[0]
            for m in migrations:
                if m.version <= cur_ver:
                    continue
                if m.version > target:
                    break
                cur.execute(m.up_sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version, filename, checksum) VALUES (%s,%s,%s)",
                    (m.version, m.filename, m.checksum),
                )
        conn.commit()


def test_upgrade_0008_mutated_baseline_to_latest_preserves_authority(
    test_database_url: str,
) -> None:
    # Start from clean, migrate to 8, mutate baseline, then migrate to latest
    from seven_lens.infrastructure.migrations import _load_migrations

    # Clean
    while current_version(test_database_url):
        rollback(test_database_url)
    # Migrate to 8
    _migrate_to_version(test_database_url, 8)
    assert current_version(test_database_url) == 8
    # Insert baseline
    with psycopg.connect(test_database_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO account_baselines (account_id, opening_cash_cents, effective_at) VALUES (%s,%s,%s)",
            ("paper-mut-1", 1_000_000, _BASE.value),
        )
        # 0008 permits an effective timestamp later than the original row timestamp.
        conn.execute(
            "UPDATE account_baselines SET effective_at = created_at + interval '1 day', "
            "opening_cash_cents=%s WHERE account_id=%s",
            (1_100_000, "paper-mut-1"),
        )
        row = conn.execute(
            "SELECT effective_at, created_at FROM account_baselines WHERE account_id=%s",
            ("paper-mut-1",),
        ).fetchone()
        assert row[0] > row[1], "mutated effective_at should be > created_at"
        legacy_effective_at = row[0]
        legacy_created_at = row[1]
    # Now migrate to latest (9)
    latest = migrate(test_database_url)
    assert latest >= 9
    # Verify revision preserves authority with GREATEST
    with psycopg.connect(test_database_url, autocommit=True) as conn:
        revision = conn.execute(
            "SELECT opening_cash_cents, effective_at, created_at FROM account_baseline_revisions WHERE account_id=%s",
            ("paper-mut-1",),
        ).fetchone()
        source = conn.execute(
            "SELECT effective_at, created_at FROM account_baselines WHERE account_id=%s",
            ("paper-mut-1",),
        ).fetchone()
        assert revision == (1_100_000, legacy_effective_at, legacy_effective_at)
        assert source == (legacy_effective_at, legacy_created_at)
    # Cleanup
    while current_version(test_database_url):
        rollback(test_database_url)
    assert migrate(test_database_url) >= 9


def test_upgrade_0008_canonical_genesis_to_latest(test_database_url: str) -> None:
    while current_version(test_database_url):
        rollback(test_database_url)
    _migrate_to_version(test_database_url, 8)
    with psycopg.connect(test_database_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO account_baselines (account_id, opening_cash_cents, effective_at) VALUES (%s,%s,%s)",
            ("paper-gen-1", 2_000_000, _BASE.value),
        )
    assert migrate(test_database_url) >= 9
    with psycopg.connect(test_database_url, autocommit=True) as conn:
        rev = conn.execute(
            "SELECT opening_cash_cents FROM account_baseline_revisions WHERE account_id=%s",
            ("paper-gen-1",),
        ).fetchone()
        assert rev[0] == 2_000_000
    while current_version(test_database_url):
        rollback(test_database_url)
    migrate(test_database_url)


# ---------------------------------------------------------------------------
# ACC-005 genesis invariant
# ---------------------------------------------------------------------------


def test_genesis_baseline_allowed_on_empty_fill_ledger(migrated_postgres: str) -> None:
    with PostgresUnitOfWork(migrated_postgres) as uow:
        b = uow.account_baselines.set_baseline("paper-gen-empty", 1_000_000, _BASE)
        assert b.account_id == "paper-gen-empty"
        uow.commit()
    with PostgresUnitOfWork(migrated_postgres) as uow:
        assert uow.account_baselines.get_baseline("paper-gen-empty") is not None
        uow.commit()


def test_genesis_baseline_creation_race_with_first_fill_is_serialized(
    migrated_postgres: str,
) -> None:
    intent = _intent(790)
    with PostgresUnitOfWork(migrated_postgres) as uow:
        uow.orders.add(intent)
        for status in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            uow.orders.transition_status(intent.client_order_id, status)
        uow.orders.record_broker_order(
            BrokerOrder(
                broker_order_id="b-genesis-fill-race",
                client_order_id=intent.client_order_id,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                filled_quantity=0,
                limit_price=intent.limit_price,
                status=BrokerOrderStatus.ACCEPTED,
                submitted_at=_BASE,
                updated_at=_BASE,
            )
        )
        uow.commit()

    genesis_has_lock = threading.Event()
    allow_genesis_commit = threading.Event()
    fill_attempted = threading.Event()
    fill_finished = threading.Event()
    errors: list[BaseException] = []
    commit_order: list[str] = []
    order_lock = threading.Lock()

    def create_genesis() -> None:
        try:
            with PostgresUnitOfWork(migrated_postgres) as uow:
                uow._require_connection().execute("SET LOCAL lock_timeout = '3s'")
                uow.account_baselines.set_baseline("paper-genesis-race", 1_000_000, _BASE)
                genesis_has_lock.set()
                if not allow_genesis_commit.wait(timeout=5):
                    raise AssertionError("genesis commit release was not signaled")
                uow.commit()
                with order_lock:
                    commit_order.append("genesis")
        except BaseException as error:
            errors.append(error)
            genesis_has_lock.set()

    def insert_first_fill() -> None:
        try:
            if not genesis_has_lock.wait(timeout=5):
                raise AssertionError("genesis did not acquire serialization lock")
            with PostgresUnitOfWork(migrated_postgres) as uow:
                uow._require_connection().execute("SET LOCAL lock_timeout = '3s'")
                fill_attempted.set()
                uow.orders.add_fill(
                    Fill(
                        execution_id="e-genesis-fill-race",
                        broker_order_id="b-genesis-fill-race",
                        quantity=OrderQuantity(1),
                        price=Price.from_cents(10_000),
                        occurred_at=_BASE,
                    )
                )
                uow.commit()
                with order_lock:
                    commit_order.append("fill")
        except BaseException as error:
            errors.append(error)
        finally:
            fill_finished.set()

    genesis_thread = threading.Thread(target=create_genesis)
    fill_thread = threading.Thread(target=insert_first_fill)
    genesis_thread.start()
    assert genesis_has_lock.wait(timeout=5), "genesis thread did not acquire fills lock"
    fill_thread.start()
    assert fill_attempted.wait(timeout=5), "fill thread did not attempt insert"
    assert not fill_finished.wait(timeout=0.2), "first fill bypassed genesis serialization lock"
    allow_genesis_commit.set()
    genesis_thread.join(timeout=5)
    fill_thread.join(timeout=5)
    assert not genesis_thread.is_alive(), "genesis thread deadlocked"
    assert not fill_thread.is_alive(), "fill thread deadlocked"
    assert not errors
    assert commit_order == ["genesis", "fill"]

    with psycopg.connect(migrated_postgres, autocommit=True) as conn:
        assert conn.execute(
            "SELECT 1 FROM account_baselines WHERE account_id = %s",
            ("paper-genesis-race",),
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT 1 FROM fills WHERE execution_id = %s",
            ("e-genesis-fill-race",),
        ).fetchone() == (1,)


def test_genesis_baseline_rejected_after_any_fill(migrated_postgres: str) -> None:
    # Insert a fill
    intent = _intent(800)
    with PostgresUnitOfWork(migrated_postgres) as uow:
        uow.orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            uow.orders.transition_status(intent.client_order_id, s)
        mirror = BrokerOrder(
            broker_order_id="b-gen-reject",
            client_order_id=intent.client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(10),
            filled_quantity=10,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=_BASE,
            updated_at=_BASE,
        )
        uow.orders.record_broker_order(mirror)
        uow.orders.add_fill(
            Fill(
                execution_id="e-gen-reject",
                broker_order_id="b-gen-reject",
                quantity=OrderQuantity(10),
                price=Price.from_cents(10_000),
                occurred_at=_BASE,
            )
        )
        uow.commit()
    # Try genesis on different account should still be rejected because fill ledger not empty (global)
    with pytest.raises(ValueError, match="empty fill ledger"):
        with PostgresUnitOfWork(migrated_postgres) as uow:
            uow.account_baselines.set_baseline("paper-gen-reject-2", 1_000_000, _BASE)
            uow.commit()


def test_revision_after_fill_requires_explicit_cutoff(migrated_postgres: str) -> None:
    intent = _intent(850)
    with PostgresUnitOfWork(migrated_postgres) as uow:
        uow.orders.add(intent)
        for status in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            uow.orders.transition_status(intent.client_order_id, status)
        mirror = BrokerOrder(
            broker_order_id="b-rev-cutoff",
            client_order_id=intent.client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(10),
            filled_quantity=10,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=_BASE,
            updated_at=_BASE,
        )
        uow.orders.record_broker_order(mirror)
        uow.orders.add_fill(
            Fill(
                execution_id="e-rev-cutoff",
                broker_order_id="b-rev-cutoff",
                quantity=OrderQuantity(10),
                price=Price.from_cents(10_000),
                occurred_at=_BASE,
            )
        )
        uow.commit()

    with PostgresUnitOfWork(migrated_postgres) as uow:
        with pytest.raises(ValueError, match="explicit cutoff"):
            uow.account_baselines.add_revision(
                "paper-rev-cutoff-test", 1_000_000, _BASE, None, None, "test", "op"
            )
        uow.rollback()
        fills = uow.orders.list_all_fills()
        assert fills
        cutoff = fills[0]
        rev = uow.account_baselines.add_revision(
            "paper-rev-cutoff-test",
            1_100_000,
            _BASE,
            cutoff.occurred_at,
            cutoff.execution_id,
            "test",
            "op",
        )
        assert rev.cutoff_execution_id == cutoff.execution_id
        uow.commit()


# ---------------------------------------------------------------------------
# ACC-007 conflicting fill durable pause
# ---------------------------------------------------------------------------


def test_pg_conflicting_fill_preserves_fact_and_durable_gate(migrated_postgres: str) -> None:
    from seven_lens.execution.trade_updates import TradeUpdateConsumer, fill_update

    intent = _intent(701)
    with PostgresUnitOfWork(migrated_postgres) as uow:
        uow.orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            uow.orders.transition_status(intent.client_order_id, s)
        mirror = BrokerOrder(
            broker_order_id="b-conflict",
            client_order_id=intent.client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(5),
            filled_quantity=0,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.ACCEPTED,
            submitted_at=_BASE,
            updated_at=_BASE,
        )
        uow.orders.record_broker_order(mirror)
        uow.commit()
    # Apply conflicting fill (exceeds quantity)
    consumer = TradeUpdateConsumer()
    with PostgresUnitOfWork(migrated_postgres) as uow:
        with pytest.raises(Exception):
            consumer.apply(
                uow,
                fill_update(
                    execution_id="e-conflict-big",
                    broker_order_id="b-conflict",
                    quantity=6,
                    price_cents=10_000,
                    occurred_at=_BASE,
                ),
            )
        # The apply does commit for fill? Actually consumer commits fill before derived, but on conflict rollback derived and then pause commit.
        # Need to ensure no exception leaves uncommitted pause? Our consumer does commit pause.
        pass
    # Verify from new connection
    with psycopg.connect(migrated_postgres) as conn:
        fill_row = conn.execute(
            "SELECT execution_id FROM fills WHERE execution_id=%s", ("e-conflict-big",)
        ).fetchone()
        assert fill_row is not None, "fill fact must survive"
        mirror_row = conn.execute(
            "SELECT filled_quantity, status FROM broker_orders WHERE broker_order_id=%s",
            ("b-conflict",),
        ).fetchone()
        assert mirror_row[0] == 0, "mirror should not have progressed"
        paused = conn.execute("SELECT entries_paused FROM control_state WHERE singleton").fetchone()
        assert paused[0] is True
        cmd = conn.execute(
            "SELECT command, reason FROM control_commands WHERE reason=%s",
            ("automatic pause on conflicting fill",),
        ).fetchone()
        assert cmd is not None
        assert cmd[0] == "PAUSE_ENTRIES"
    # Restart should still see pause
    b2 = _intent(702)
    with PostgresUnitOfWork(migrated_postgres) as uow:
        uow.orders.add(b2)
        for s in (OrderStatus.RISK_APPROVED, OrderStatus.OUTBOX_PENDING):
            uow.orders.transition_status(b2.client_order_id, s)
        uow.commit()
        from seven_lens.application.execution_service import ExecutionPausedError

        engine = ExecutionEngine(
            broker=FakePaperBroker(clock=lambda: _BASE), clock=lambda: _BASE, control=uow.control
        )
        with pytest.raises(ExecutionPausedError):
            engine.submit_from_outbox(uow, b2.client_order_id)


def test_pg_pause_commit_failure_leaves_review_gate_for_second_actor(
    migrated_postgres: str,
) -> None:
    from seven_lens.execution.trade_updates import (
        OrderStatusUpdate,
        TradeUpdateConsumer,
        TradeUpdateError,
    )

    intent = _intent(703)
    with PostgresUnitOfWork(migrated_postgres) as uow:
        uow.orders.add(intent)
        for status in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            uow.orders.transition_status(intent.client_order_id, status)
        stored_mirror = uow.orders.record_broker_order(
            BrokerOrder(
                broker_order_id="b-pause-commit-failure",
                client_order_id=intent.client_order_id,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                filled_quantity=0,
                limit_price=intent.limit_price,
                status=BrokerOrderStatus.ACCEPTED,
                submitted_at=_BASE,
                updated_at=_BASE,
            )
        )
        conflict_at = stored_mirror.updated_at
        uow.commit()

    with PostgresUnitOfWork(migrated_postgres) as uow:
        real_commit = uow.commit

        def fail_pause_commit() -> None:
            if uow.control.state().entries_paused:
                raise RuntimeError("injected pause commit failure")
            real_commit()

        uow.commit = fail_pause_commit
        with pytest.raises(TradeUpdateError, match="entries_paused"):
            TradeUpdateConsumer().apply(
                uow,
                OrderStatusUpdate(
                    client_order_id=intent.client_order_id,
                    broker_order_id=stored_mirror.broker_order_id,
                    status=BrokerOrderStatus.PARTIALLY_FILLED,
                    filled_quantity=4,
                    observed_at=conflict_at,
                ),
            )

    with psycopg.connect(migrated_postgres) as conn:
        intent_row = conn.execute(
            "SELECT status FROM order_intents WHERE client_order_id=%s",
            (intent.client_order_id.value,),
        ).fetchone()
        assert intent_row == ("REVIEW_REQUIRED",)
        control_row = conn.execute(
            "SELECT entries_paused FROM control_state WHERE singleton"
        ).fetchone()
        assert control_row == (False,)

    second = _intent(704)
    with PostgresUnitOfWork(migrated_postgres) as uow:
        _seed_outbox(uow, second)
        broker = _CountingBroker()
        result = ExecutionEngine(
            broker=broker, clock=lambda: _BASE, control=uow.control
        ).submit_from_outbox(uow, second.client_order_id)
        assert result.status is OrderStatus.UNKNOWN
        assert broker.calls == []


@pytest.mark.parametrize("update_kind", ["status", "fill"])
def test_pg_marker_commit_failure_uses_independent_pause_fallback(
    migrated_postgres: str,
    update_kind: str,
) -> None:
    from seven_lens.execution.trade_updates import (
        OrderStatusUpdate,
        TradeUpdateConsumer,
        TradeUpdateError,
        fill_update,
    )

    intent = _intent(705 if update_kind == "status" else 706)
    broker_order_id = f"b-marker-commit-failure-{update_kind}"
    with PostgresUnitOfWork(migrated_postgres) as uow:
        uow.orders.add(intent)
        for status in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            uow.orders.transition_status(intent.client_order_id, status)
        stored_mirror = uow.orders.record_broker_order(
            BrokerOrder(
                broker_order_id=broker_order_id,
                client_order_id=intent.client_order_id,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                filled_quantity=0,
                limit_price=intent.limit_price,
                status=BrokerOrderStatus.ACCEPTED,
                submitted_at=_BASE,
                updated_at=_BASE,
            )
        )
        uow.commit()

    with PostgresUnitOfWork(migrated_postgres) as uow:
        real_commit = uow.commit

        def fail_marker_commit() -> None:
            current = uow.orders.get(intent.client_order_id)
            if current is not None and current.status is OrderStatus.REVIEW_REQUIRED:
                raise RuntimeError("injected unresolved-marker commit failure")
            real_commit()

        uow.commit = fail_marker_commit
        if update_kind == "status":
            update = OrderStatusUpdate(
                client_order_id=intent.client_order_id,
                broker_order_id=stored_mirror.broker_order_id,
                status=BrokerOrderStatus.PARTIALLY_FILLED,
                filled_quantity=4,
                observed_at=stored_mirror.updated_at,
            )
        else:
            update = fill_update(
                execution_id="e-marker-commit-failure",
                broker_order_id=stored_mirror.broker_order_id,
                quantity=11,
                price_cents=9_998,
                occurred_at=stored_mirror.updated_at,
            )
        with pytest.raises(TradeUpdateError, match="bounded safety pause fallback"):
            TradeUpdateConsumer().apply(uow, update)

    with psycopg.connect(migrated_postgres) as conn:
        intent_row = conn.execute(
            "SELECT status FROM order_intents WHERE client_order_id=%s",
            (intent.client_order_id.value,),
        ).fetchone()
        assert intent_row == ("ACKNOWLEDGED",)
        control_row = conn.execute(
            "SELECT entries_paused, paused_reason FROM control_state WHERE singleton"
        ).fetchone()
        assert control_row == (True, f"reconciliation required; conflicting {update_kind}")
        command_row = conn.execute(
            "SELECT command_id FROM control_commands WHERE reason=%s",
            (f"automatic pause on conflicting {update_kind}",),
        ).fetchone()
        assert command_row is None
        if update_kind == "fill":
            fill_row = conn.execute(
                "SELECT execution_id FROM fills WHERE execution_id=%s",
                ("e-marker-commit-failure",),
            ).fetchone()
            assert fill_row is not None
        mirror_row = conn.execute(
            "SELECT filled_quantity, status FROM broker_orders WHERE broker_order_id=%s",
            (broker_order_id,),
        ).fetchone()
        assert mirror_row == (0, "ACCEPTED")

    second = _intent(707 if update_kind == "status" else 708)
    with PostgresUnitOfWork(migrated_postgres) as uow:
        _seed_outbox(uow, second)
        broker = _CountingBroker()
        with pytest.raises(ExecutionPausedError):
            ExecutionEngine(
                broker=broker, clock=lambda: _BASE, control=uow.control
            ).submit_from_outbox(uow, second.client_order_id)
        assert broker.calls == []


def test_pg_ambiguous_pause_audit_commit_is_idempotent_on_retry(
    migrated_postgres: str,
) -> None:
    from seven_lens.execution.trade_updates import (
        OrderStatusUpdate,
        TradeUpdateConsumer,
        TradeUpdateError,
    )

    intent = _intent(709)
    broker_order_id = "b-ambiguous-pause-audit"
    with PostgresUnitOfWork(migrated_postgres) as uow:
        uow.orders.add(intent)
        for status in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            uow.orders.transition_status(intent.client_order_id, status)
        uow.orders.record_broker_order(
            BrokerOrder(
                broker_order_id=broker_order_id,
                client_order_id=intent.client_order_id,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                filled_quantity=0,
                limit_price=intent.limit_price,
                status=BrokerOrderStatus.ACCEPTED,
                submitted_at=_BASE,
                updated_at=_BASE,
            )
        )
        uow.commit()

    conflict = OrderStatusUpdate(
        client_order_id=intent.client_order_id,
        broker_order_id=broker_order_id,
        status=BrokerOrderStatus.FILLED,
        filled_quantity=4,
        observed_at=UtcTimestamp.from_isoformat("2026-08-17T13:35:05.000000Z"),
    )
    with PostgresUnitOfWork(migrated_postgres) as uow:
        real_commit = uow.commit
        commit_count = 0

        def ambiguous_commit() -> None:
            nonlocal commit_count
            commit_count += 1
            real_commit()
            if commit_count == 3:
                raise RuntimeError("injected ambiguous final audit commit")

        uow.commit = ambiguous_commit
        with pytest.raises(TradeUpdateError, match="pause audit"):
            TradeUpdateConsumer().apply(uow, conflict)
    assert commit_count == 3

    with psycopg.connect(migrated_postgres, autocommit=True) as connection:
        first_command = connection.execute(
            """
            SELECT command_id, command, actor
            FROM public.control_commands
            WHERE reason = 'automatic pause on conflicting status'
            """
        ).fetchone()
    assert first_command is not None
    assert first_command[1:] == ("PAUSE_ENTRIES", "trade_update_consumer")

    # A new connection represents the caller retry after the commit outcome was
    # ambiguous.  The stable command identity must make this a no-op audit replay.
    with PostgresUnitOfWork(migrated_postgres) as uow:
        with pytest.raises(TradeUpdateError):
            TradeUpdateConsumer().apply(uow, conflict)

    with psycopg.connect(migrated_postgres, autocommit=True) as connection:
        commands = connection.execute(
            """
            SELECT command_id
            FROM public.control_commands
            WHERE reason = 'automatic pause on conflicting status'
            """
        ).fetchall()
    assert commands == [first_command[:1]]


# ---------------------------------------------------------------------------
# Status-update conflicts converge on the real PostgreSQL guards
# ---------------------------------------------------------------------------


def test_pg_status_quantity_regression_is_typed_rolled_back_and_durable_paused(
    migrated_postgres: str,
) -> None:
    from seven_lens.execution.trade_updates import (
        OrderStatusUpdate,
        TradeUpdateConsumer,
        TradeUpdateError,
    )

    intent = _intent(711)
    with PostgresUnitOfWork(migrated_postgres) as uow:
        uow.orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            uow.orders.transition_status(intent.client_order_id, s)
        uow.orders.record_broker_order(
            BrokerOrder(
                broker_order_id="b-status-regression",
                client_order_id=intent.client_order_id,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                filled_quantity=5,
                limit_price=intent.limit_price,
                status=BrokerOrderStatus.PARTIALLY_FILLED,
                submitted_at=_BASE,
                updated_at=_BASE,
            )
        )
        uow.commit()

    regression_observed_at = UtcTimestamp.from_isoformat("2026-08-17T13:35:05.000000Z")
    with PostgresUnitOfWork(migrated_postgres) as uow:
        with pytest.raises(TradeUpdateError) as conflict:
            TradeUpdateConsumer().apply(
                uow,
                OrderStatusUpdate(
                    client_order_id=intent.client_order_id,
                    broker_order_id="b-status-regression",
                    status=BrokerOrderStatus.PARTIALLY_FILLED,
                    filled_quantity=4,
                    observed_at=regression_observed_at,
                ),
            )
        assert type(conflict.value) is TradeUpdateError

    # A fresh connection must observe zero derived mutation plus a durable pause.
    with psycopg.connect(migrated_postgres) as conn:
        mirror_row = conn.execute(
            "SELECT filled_quantity, status FROM broker_orders WHERE broker_order_id=%s",
            ("b-status-regression",),
        ).fetchone()
        assert mirror_row == (5, "PARTIALLY_FILLED")
        intent_row = conn.execute(
            "SELECT status FROM order_intents WHERE client_order_id=%s",
            (intent.client_order_id.value,),
        ).fetchone()
        assert intent_row == ("REVIEW_REQUIRED",)
        control_row = conn.execute(
            "SELECT entries_paused, paused_reason FROM control_state WHERE singleton"
        ).fetchone()
        assert control_row[0] is True
        assert control_row[1] == "reconciliation required; conflicting status"
        command_row = conn.execute(
            "SELECT command FROM control_commands WHERE reason=%s",
            ("automatic pause on conflicting status",),
        ).fetchone()
        assert command_row == ("PAUSE_ENTRIES",)

    # The database trigger stays the last authority against a regression write.
    with psycopg.connect(migrated_postgres, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState) as rejected:
            conn.execute(
                "UPDATE broker_orders SET filled_quantity = 4 WHERE broker_order_id=%s",
                ("b-status-regression",),
            )
        assert rejected.value.sqlstate == "55000"
