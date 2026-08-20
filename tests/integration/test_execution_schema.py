# mypy: ignore-errors
"""PostgreSQL-only enforcement tests for the P2-A execution order schema."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from seven_lens.infrastructure.migrations import current_version, migrate, rollback

pytestmark = pytest.mark.integration

_CLIENT_ORDER_ID = "slv1-seven-lens-2026-08-17-open-t{version}-AAPL-buy"
_EARLIEST = datetime(2026, 8, 17, 13, 35, 0, tzinfo=UTC)
_CANCEL_AT = datetime(2026, 8, 17, 13, 45, 0, tzinfo=UTC)


@contextmanager
def _connection(dsn: str) -> Iterator[Any]:
    connection = psycopg.connect(dsn)
    try:
        yield connection
    finally:
        connection.close()


def _intent_row(version: int = 1, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "intent_id": uuid4(),
        "client_order_id": _CLIENT_ORDER_ID.format(version=version),
        "strategy": "seven-lens",
        "trading_date": date(2026, 8, 17),
        "window_name": "open",
        "target_version": version,
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": 10,
        "intent_type": "REBALANCE",
        "limit_price": "100.00",
        "collar_reference_price": "100.00",
        "collar_offset_bps": 100,
        "earliest_submit_at": _EARLIEST,
        "cancel_at": _CANCEL_AT,
        "status": "CREATED",
        "run_id": uuid4(),
    }
    row.update(overrides)
    return row


def _insert_intent(cursor: Any, row: dict[str, object]) -> None:
    columns = sorted(row)
    cursor.execute(
        sql.SQL("INSERT INTO public.order_intents ({}) VALUES ({})").format(
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            sql.SQL(", ").join(sql.Placeholder(column) for column in columns),
        ),
        row,
    )


def _insert_broker_order(
    cursor: Any,
    *,
    client_order_id: str,
    broker_order_id: str = "broker-000001",
    status: str = "ACCEPTED",
) -> None:
    cursor.execute(
        """
        INSERT INTO public.broker_orders (
            broker_order_id, client_order_id, symbol, side, quantity,
            filled_quantity, limit_price, status, submitted_at, broker_updated_at
        ) VALUES (%s, %s, 'AAPL', 'BUY', 10, 0, 100.00, %s, %s, %s)
        """,
        (broker_order_id, client_order_id, status, _EARLIEST, _EARLIEST),
    )


def _insert_fill(cursor: Any, *, broker_order_id: str, execution_id: str) -> None:
    cursor.execute(
        """
        INSERT INTO public.fills (
            execution_id, broker_order_id, quantity, price, occurred_at
        ) VALUES (%s, %s, 4, 99.98, %s)
        """,
        (execution_id, broker_order_id, _CANCEL_AT),
    )


def _expect_guard_failure(cursor: Any, statement: str, parameters: tuple[object, ...]) -> None:
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState) as excinfo:
        cursor.execute(statement, parameters)
    assert excinfo.value.sqlstate == "55000"
    cursor.connection.rollback()


def test_valid_intent_is_persisted(migrated_postgres: str) -> None:
    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        _insert_intent(cursor, _intent_row(version=1))
        connection.commit()
        cursor.execute(
            "SELECT status, target_version FROM public.order_intents WHERE client_order_id = %s",
            (_CLIENT_ORDER_ID.format(version=1),),
        )
        assert cursor.fetchone() == ("CREATED", 1)


def test_duplicate_client_order_id_is_rejected(migrated_postgres: str) -> None:
    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        _insert_intent(cursor, _intent_row(version=1))
        connection.commit()
        with pytest.raises(psycopg.errors.UniqueViolation) as excinfo:
            _insert_intent(cursor, _intent_row(version=1))
        assert excinfo.value.sqlstate == "23505"
        connection.rollback()


@pytest.mark.parametrize(
    ("field", "value", "description"),
    [
        ("symbol", "aapl", "lowercase symbol"),
        ("symbol", "TOOLONGSYMBOL", "oversized symbol"),
        ("target_version", 0, "zero target version"),
        ("target_version", -3, "negative target version"),
        ("quantity", 0, "zero quantity"),
        ("side", "SHORT", "unknown side"),
        ("intent_type", "ALPHA", "unknown intent type"),
        ("limit_price", "0.00", "zero limit price"),
        ("collar_offset_bps", 0, "zero collar offset"),
        ("collar_offset_bps", 501, "oversized collar offset"),
        ("status", "SUBMITTED", "unknown status"),
        (
            "client_order_id",
            "slv1-seven-lens-2026-08-17-open-t1-MSFT-buy",
            "composition mismatch",
        ),
        ("strategy", "SevenLens", "non-canonical strategy"),
        ("window_name", "Open", "non-canonical window"),
    ],
)
def test_invalid_intent_rows_fail_closed(
    migrated_postgres: str, field: str, value: object, description: str
) -> None:
    del description
    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
            _insert_intent(cursor, _intent_row(version=1, **{field: value}))
        assert excinfo.value.sqlstate == "23514"
        connection.rollback()


def test_cancel_deadline_must_follow_earliest_submit(migrated_postgres: str) -> None:
    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
            _insert_intent(
                cursor,
                _intent_row(version=1, cancel_at=_EARLIEST, earliest_submit_at=_EARLIEST),
            )
        assert excinfo.value.sqlstate == "23514"
        connection.rollback()


def test_intent_identity_is_immutable_and_transitions_are_guarded(migrated_postgres: str) -> None:
    client_order_id = _CLIENT_ORDER_ID.format(version=1)
    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        _insert_intent(cursor, _intent_row(version=1))
        connection.commit()

        cursor.execute(
            "SELECT updated_at FROM public.order_intents WHERE client_order_id = %s",
            (client_order_id,),
        )
        first_updated_at = cursor.fetchone()[0]

        for column, value in (
            ("strategy", "other-lens"),
            ("client_order_id", _CLIENT_ORDER_ID.format(version=2)),
            ("quantity", 20),
            ("limit_price", "101.00"),
            ("cancel_at", _CANCEL_AT.replace(minute=59)),
        ):
            _expect_guard_failure(
                cursor,
                f"UPDATE public.order_intents SET {column} = %s WHERE client_order_id = %s",
                (value, client_order_id),
            )

        def _transition(status: str) -> None:
            cursor.execute(
                "UPDATE public.order_intents SET status = %s WHERE client_order_id = %s",
                (status, client_order_id),
            )
            connection.commit()

        _transition("RISK_APPROVED")
        _expect_guard_failure(
            cursor,
            "UPDATE public.order_intents SET status = 'FILLED' WHERE client_order_id = %s",
            (client_order_id,),
        )
        cursor.execute(
            "SELECT status FROM public.order_intents WHERE client_order_id = %s",
            (client_order_id,),
        )
        assert cursor.fetchone()[0] == "RISK_APPROVED"

        for status in (
            "OUTBOX_PENDING",
            "SUBMITTING",
            "UNKNOWN",
            "ACKNOWLEDGED",
            "PARTIALLY_FILLED",
            "FILLED",
        ):
            _transition(status)
        _expect_guard_failure(
            cursor,
            "UPDATE public.order_intents SET status = 'CANCELED' WHERE client_order_id = %s",
            (client_order_id,),
        )
        cursor.execute(
            "SELECT status, updated_at FROM public.order_intents WHERE client_order_id = %s",
            (client_order_id,),
        )
        row = cursor.fetchone()
        assert row[0] == "FILLED"
        assert row[1] > first_updated_at


def test_broker_orders_mirror_constraints(migrated_postgres: str) -> None:
    client_order_id = _CLIENT_ORDER_ID.format(version=1)
    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        _insert_intent(cursor, _intent_row(version=1))
        connection.commit()

        with pytest.raises(psycopg.errors.ForeignKeyViolation) as foreign_key_error:
            _insert_broker_order(cursor, client_order_id="slv1-unknown-2026-08-17-open-t9-AAPL-buy")
        assert foreign_key_error.value.sqlstate == "23503"
        connection.rollback()

        with pytest.raises(psycopg.errors.CheckViolation) as status_error:
            _insert_broker_order(cursor, client_order_id=client_order_id, status="UNKNOWN")
        assert status_error.value.sqlstate == "23514"
        connection.rollback()

        with pytest.raises(psycopg.errors.CheckViolation) as quantity_error:
            cursor.execute(
                """
                INSERT INTO public.broker_orders (
                    broker_order_id, client_order_id, symbol, side, quantity,
                    filled_quantity, limit_price, status, submitted_at, broker_updated_at
                ) VALUES ('broker-x', %s, 'AAPL', 'BUY', 10, 11, 100.00, 'ACCEPTED', %s, %s)
                """,
                (client_order_id, _EARLIEST, _EARLIEST),
            )
        assert quantity_error.value.sqlstate == "23514"
        connection.rollback()

        _insert_broker_order(cursor, client_order_id=client_order_id, status="RECEIVED")
        connection.commit()

        for column, value in (
            ("quantity", 20),
            ("limit_price", "99.00"),
            ("client_order_id", _CLIENT_ORDER_ID.format(version=2)),
        ):
            _expect_guard_failure(
                cursor,
                f"UPDATE public.broker_orders SET {column} = %s"
                " WHERE broker_order_id = 'broker-000001'",
                (value,),
            )

        cursor.execute(
            "UPDATE public.broker_orders SET status = 'ACCEPTED', filled_quantity = 4"
            " WHERE broker_order_id = 'broker-000001'"
        )
        connection.commit()
        _expect_guard_failure(
            cursor,
            "UPDATE public.broker_orders SET status = 'RECEIVED'"
            " WHERE broker_order_id = 'broker-000001'",
            (),
        )


def test_fills_are_append_only(migrated_postgres: str) -> None:
    client_order_id = _CLIENT_ORDER_ID.format(version=1)
    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        _insert_intent(cursor, _intent_row(version=1))
        _insert_broker_order(cursor, client_order_id=client_order_id)
        _insert_fill(cursor, broker_order_id="broker-000001", execution_id="exec-000001")
        connection.commit()

        with pytest.raises(psycopg.errors.UniqueViolation) as duplicate_error:
            _insert_fill(cursor, broker_order_id="broker-000001", execution_id="exec-000001")
        assert duplicate_error.value.sqlstate == "23505"
        connection.rollback()

        with pytest.raises(psycopg.errors.ForeignKeyViolation) as missing_order_error:
            _insert_fill(cursor, broker_order_id="broker-missing", execution_id="exec-000002")
        assert missing_order_error.value.sqlstate == "23503"
        connection.rollback()

        _insert_fill(cursor, broker_order_id="broker-000001", execution_id="exec-000002")
        connection.commit()

        _expect_guard_failure(
            cursor,
            "UPDATE public.fills SET quantity = 5 WHERE execution_id = %s",
            ("exec-000001",),
        )
        _expect_guard_failure(
            cursor,
            "DELETE FROM public.fills WHERE execution_id = %s",
            ("exec-000001",),
        )
        cursor.execute("SELECT count(*) FROM public.fills")
        assert cursor.fetchone()[0] == 2


def test_migration_0003_down_and_up_restores_execution_schema(migrated_postgres: str) -> None:
    assert current_version(migrated_postgres) == 8
    assert rollback(migrated_postgres) == 7
    assert rollback(migrated_postgres) == 6
    assert rollback(migrated_postgres) == 5
    assert rollback(migrated_postgres) == 4
    assert rollback(migrated_postgres) == 3
    assert rollback(migrated_postgres) == 2
    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT to_regclass('public.order_intents'),
                   to_regclass('public.broker_orders'),
                   to_regclass('public.fills')
            """
        )
        assert cursor.fetchone() == (None, None, None)
    assert migrate(migrated_postgres) == 8
    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        _insert_intent(cursor, _intent_row(version=1))
        connection.commit()


def test_sql_transition_functions_match_the_python_maps(migrated_postgres: str) -> None:
    """The database guards and the domain maps must never drift apart."""
    from seven_lens.execution.orders import (
        BROKER_ORDER_STATUS_TRANSITIONS,
        ORDER_STATUS_TRANSITIONS,
        BrokerOrderStatus,
        OrderStatus,
    )

    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        for intent_current in OrderStatus:
            for intent_target in OrderStatus:
                cursor.execute(
                    "SELECT public.order_status_transition_is_valid(%s, %s)",
                    (intent_current.value, intent_target.value),
                )
                row = cursor.fetchone()
                assert row is not None
                assert row[0] is (intent_target in ORDER_STATUS_TRANSITIONS[intent_current]), (
                    f"intent map drift: {intent_current.value} -> {intent_target.value}"
                )
        for current in BrokerOrderStatus:
            for target in BrokerOrderStatus:
                cursor.execute(
                    "SELECT public.broker_order_status_transition_is_valid(%s, %s)",
                    (current.value, target.value),
                )
                row = cursor.fetchone()
                assert row is not None
                assert row[0] is (target in BROKER_ORDER_STATUS_TRANSITIONS[current]), (
                    f"broker map drift: {current.value} -> {target.value}"
                )
