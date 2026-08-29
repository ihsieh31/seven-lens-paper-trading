"""Shared test configuration and fail-closed PostgreSQL integration gating."""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

import pytest

_REQUIRE_POSTGRES_ENV = "REQUIRE_POSTGRES_INTEGRATION"
_DATABASE_URL_ENV = "TEST_DATABASE_URL"
_MISSING_URL_ERROR = "PostgreSQL integration gate requires TEST_DATABASE_URL."
_URL_ERROR = "PostgreSQL integration gate requires a PostgreSQL URL."
_DRIVER_ERROR = "PostgreSQL integration gate requires the psycopg dependency."
_CONNECTION_ERROR = "PostgreSQL integration gate could not connect to PostgreSQL."
_VERSION_ERROR = "PostgreSQL integration gate requires PostgreSQL major version 16."
_SKIP_ERROR = "PostgreSQL integration gate forbids skipped integration tests."

_required_postgres_integration = False
_integration_skip_count = 0


class _PostgresGateError(RuntimeError):
    """Internal fixed-message gate failure."""


def _required_gate_enabled() -> bool:
    return os.environ.get(_REQUIRE_POSTGRES_ENV, "") == "1"


def _validated_database_url(*, required: bool) -> str | None:
    database_url = os.environ.get(_DATABASE_URL_ENV, "").strip()
    if not database_url:
        if required:
            raise _PostgresGateError(_MISSING_URL_ERROR)
        return None

    try:
        parsed = urlsplit(database_url)
        hostname = parsed.hostname
    except ValueError:
        raise _PostgresGateError(_URL_ERROR) from None
    if parsed.scheme.lower() not in {"postgres", "postgresql"} or not hostname:
        raise _PostgresGateError(_URL_ERROR)
    return database_url


def _load_psycopg() -> Any:
    try:
        return importlib.import_module("psycopg")
    except (ImportError, ModuleNotFoundError):
        raise _PostgresGateError(_DRIVER_ERROR) from None


def _assert_postgres_16(database_url: str, psycopg: Any) -> None:
    connection: Any | None = None
    try:
        connection = psycopg.connect(database_url, connect_timeout=5)
        row = connection.execute("SHOW server_version_num").fetchone()
    except Exception:
        raise _PostgresGateError(_CONNECTION_ERROR) from None
    finally:
        if connection is not None:
            with suppress(Exception):
                connection.close()

    try:
        version_num = int(row[0])
    except (IndexError, TypeError, ValueError):
        raise _PostgresGateError(_VERSION_ERROR) from None
    if version_num // 10_000 != 16:
        raise _PostgresGateError(_VERSION_ERROR)


def pytest_sessionstart(session: pytest.Session) -> None:
    """Validate the required PostgreSQL gate before test collection."""

    del session
    global _integration_skip_count, _required_postgres_integration
    _integration_skip_count = 0
    _required_postgres_integration = _required_gate_enabled()
    if not _required_postgres_integration:
        return
    try:
        database_url = _validated_database_url(required=True)
        assert database_url is not None
        _assert_postgres_16(database_url, _load_psycopg())
    except _PostgresGateError as failure:
        raise pytest.UsageError(str(failure)) from None


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Count integration skips while the required gate is active."""

    global _integration_skip_count
    if _required_postgres_integration and report.skipped and "integration" in report.keywords:
        _integration_skip_count += 1


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Make any integration skip a failing required-gate result."""

    del exitstatus
    if (
        _required_postgres_integration
        and _integration_skip_count
        and session.exitstatus == pytest.ExitCode.OK
    ):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: Any) -> None:
    """Emit one bounded message without environment or credential data."""

    if _required_postgres_integration and _integration_skip_count:
        terminalreporter.write_line(f"ERROR: {_SKIP_ERROR}", red=True, bold=True)


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Return a validated PostgreSQL URL or skip an optional local integration run."""

    try:
        database_url = _validated_database_url(required=_required_gate_enabled())
    except _PostgresGateError as failure:
        pytest.fail(str(failure), pytrace=False)
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not set; PostgreSQL integration test skipped")
    return database_url


@pytest.fixture
def postgres_connection(test_database_url: str) -> Iterator[Any]:
    """Yield a real psycopg connection and roll back test-local changes."""

    try:
        psycopg = _load_psycopg()
        connection = psycopg.connect(test_database_url, connect_timeout=5)
    except _PostgresGateError as failure:
        pytest.fail(str(failure), pytrace=False)
    except Exception:
        pytest.fail(_CONNECTION_ERROR, pytrace=False)
    try:
        yield connection
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


@pytest.fixture
def postgres_cursor(postgres_connection: Any) -> Iterator[Any]:
    """Yield a cursor bound to the real PostgreSQL test connection."""

    with postgres_connection.cursor() as cursor:
        yield cursor


@pytest.fixture
def migrated_postgres(test_database_url: str) -> Iterator[str]:
    """Provide one clean, migrated PostgreSQL database per integration test."""

    from seven_lens.infrastructure.migrations import (
        current_version,
        migrate,
        rollback,
        verify_schema,
    )

    while current_version(test_database_url):
        rollback(test_database_url)
    migrate(test_database_url)
    verify_schema(test_database_url)
    try:
        yield test_database_url
    finally:
        try:
            while current_version(test_database_url):
                rollback(test_database_url)
        except Exception:
            # A test may have written rows whose route identity makes the down
            # migration refuse by design (e.g. live generic-route audits).  The
            # disposable test database is then reset out-of-band.
            import psycopg

            with psycopg.connect(test_database_url, autocommit=True) as connection:
                connection.execute("DROP SCHEMA public CASCADE")
                connection.execute("CREATE SCHEMA public")
                connection.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto SCHEMA public")
            migrate(test_database_url)
