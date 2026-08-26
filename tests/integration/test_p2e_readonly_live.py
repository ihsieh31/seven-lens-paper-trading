# mypy: ignore-errors
"""P2-E live read-only verification tests (marked live: need real credentials)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Final
from urllib.parse import quote_plus, urlsplit

import psycopg
import pytest
from psycopg import sql

from seven_lens.cli.p2e_readonly_verify import run_verification
from seven_lens.config.broker import BrokerEnvironment
from seven_lens.infrastructure.postgres import PostgresUnitOfWork, RuntimeDsn
from seven_lens.infrastructure.postgres_roles import provision_runtime_role, verify_runtime_role

pytestmark = [pytest.mark.integration, pytest.mark.live]

_LIVE_ENV: Final = "SEVEN_LENS_P2E_LIVE"
_LIVE_RUNTIME_ROLE: Final = "seven_lens_p2e_live"
_LIVE_RUNTIME_PASSWORD: Final = "p2e-disposable-runtime-only"


def _require_live() -> None:
    if os.environ.get(_LIVE_ENV, "") != "1":
        pytest.skip("P2-E live verification requires SEVEN_LENS_P2E_LIVE=1")


def _live_config(test_database_url: str, runtime_role: str, directory: Path) -> Path:
    parsed = urlsplit(test_database_url)
    assert parsed.hostname is not None
    assert parsed.username is not None
    config = {
        "paper": {
            "environment": "PAPER",
            "base_url": "https://paper-api.alpaca.markets",
        },
        "database": {
            "host": parsed.hostname,
            "port": parsed.port,
            "dbname": parsed.path.lstrip("/"),
            "user": runtime_role,
            "sslmode": "require",
            "password_account": "primary",
        },
        "alpaca_key_account": "primary",
        "alpaca_secret_account": "primary",
    }
    path = directory / "p2e_live_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


@pytest.fixture
def live_runtime_postgres(migrated_postgres: str) -> Iterator[RuntimeDsn]:
    _require_live()
    parsed = urlsplit(migrated_postgres)
    assert parsed.hostname is not None
    assert parsed.path.lstrip("/")
    with psycopg.connect(migrated_postgres, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(_LIVE_RUNTIME_ROLE))
        )
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(
                sql.Identifier(_LIVE_RUNTIME_ROLE),
                sql.Literal(_LIVE_RUNTIME_PASSWORD),
            )
        )
    provision_runtime_role(migrated_postgres, _LIVE_RUNTIME_ROLE)
    verify_runtime_role(migrated_postgres, _LIVE_RUNTIME_ROLE)
    runtime_dsn = RuntimeDsn(
        "postgresql://"
        f"{quote_plus(_LIVE_RUNTIME_ROLE)}:{quote_plus(_LIVE_RUNTIME_PASSWORD)}"
        f"@{parsed.hostname}:{parsed.port or 5432}/{parsed.path.lstrip('/')}?sslmode=disable"
    )
    try:
        yield runtime_dsn
    finally:
        with psycopg.connect(migrated_postgres, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP OWNED BY {}").format(sql.Identifier(_LIVE_RUNTIME_ROLE))
            )
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(_LIVE_RUNTIME_ROLE)))


def test_live_read_only_verification_persists_evidence(
    migrated_postgres: str,
    live_runtime_postgres: RuntimeDsn,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _require_live()
    config_path = _live_config(migrated_postgres, _LIVE_RUNTIME_ROLE, tmp_path)
    monkeypatch.setattr(
        "seven_lens.cli.p2e_readonly_verify.compose_runtime_dsn",
        lambda _config, _provider: live_runtime_postgres,
    )
    report = run_verification(config_path)
    assert report.account.environment is BrokerEnvironment.PAPER
    assert report.account.cash.value >= 0
    assert report.account.equity.value >= 0
    assert all(position.quantity >= 1 for position in report.positions)
    assert all(fill.quantity.value >= 1 for fill in report.fills)
    assert report.reconciliation.run_id is not None
    with psycopg.connect(live_runtime_postgres.conninfo(), autocommit=True) as connection:
        identity = connection.execute("SELECT current_user").fetchone()
    assert identity == (_LIVE_RUNTIME_ROLE,)
    methods = {method for method, _ in report.request_log}
    assert methods == {"GET"}
    assert all(path.startswith("/v2/") for _, path in report.request_log)
    assert all(method == "GET" for method, _ in report.request_log)
    parsed = json.loads(report.to_json())
    assert parsed["read_only"] is True
    assert parsed["account"]["environment"] == "PAPER"
    assert parsed["reconciliation"]["run_id"] == str(report.reconciliation.run_id)
    expected_targets = {
        "/v2/account",
        "/v2/positions",
    }
    assert expected_targets.issubset({path.split("?")[0] for _, path in report.request_log})
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        latest = unit_of_work.reconciliations.latest()
        assert latest is not None
        assert latest.run_id == report.reconciliation.run_id
        assert latest.status is report.reconciliation.status
        assert latest.checked_orders == report.reconciliation.checked_orders
        assert latest.checked_fills == report.reconciliation.checked_fills
