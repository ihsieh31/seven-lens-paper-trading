# mypy: ignore-errors
"""P2-E live read-only verification tests (marked live: need real credentials).

The ``live`` tests are skipped unless ``SEVEN_LENS_P2E_LIVE=1`` is set and
``TEST_DATABASE_URL`` points at a migrated PostgreSQL 16 instance.  The offline
fail-closed transport tests in this file make no network call at all and run in
every suite: they exercise the real HTTPS transport against an in-process HTTP
server that injects 429, 5xx, malformed bodies, and dropped connections, and
assert the transport can never issue POST or DELETE.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final
from urllib.parse import quote_plus, urlsplit

import psycopg
import pytest
from psycopg import sql

from seven_lens.application.ports.broker import BrokerTransportError
from seven_lens.cli.p2e_readonly_verify import RealHttpTransport, run_verification
from seven_lens.config.broker import BrokerEnvironment
from seven_lens.infrastructure.postgres import PostgresUnitOfWork, RuntimeDsn
from seven_lens.infrastructure.postgres_roles import provision_runtime_role, verify_runtime_role

pytestmark = [pytest.mark.integration, pytest.mark.live]

_LIVE_ENV: Final = "SEVEN_LENS_P2E_LIVE"
_LIVE_RUNTIME_ROLE: Final = "seven_lens_p2e_live"
_LIVE_RUNTIME_PASSWORD: Final = "p2e-disposable-runtime-only"


class _FaultHandler(BaseHTTPRequestHandler):
    mode: str = "ok"

    def do_GET(self) -> None:
        if self.mode == "rate_limited":
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.end_headers()
            self.wfile.write(b'{"code":42910000,"message":"rate limit exceeded"}')
            return
        if self.mode == "server_error":
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b'{"code":50310000,"message":"service unavailable"}')
            return
        if self.mode == "invalid_json":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"not-json-at-all")
            return
        if self.mode == "empty_body":
            self.send_response(200)
            self.end_headers()
            return
        if self.mode == "silent":
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            b'{"account_number":"TEST7654321","cash":"1000.00","equity":"1000.00","buying_power":"1000.00"}'
        )

    def log_message(self, format: str, *args: object) -> None:
        return


def _server_url(server: ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_port}"


@pytest.fixture
def fault_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FaultHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _server_url(server)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture(autouse=True)
def reset_fault_mode() -> Iterator[None]:
    _FaultHandler.mode = "ok"
    yield


def _transport_for(
    base_url: str,
    *,
    timeout_seconds: float = 10.0,
    retry_429_attempts: int = 3,
    max_retry_wait_seconds: float = 30.0,
) -> RealHttpTransport:
    return RealHttpTransport(
        url_allowlist=(base_url,),
        timeout_seconds=timeout_seconds,
        retry_429_attempts=retry_429_attempts,
        max_retry_wait_seconds=max_retry_wait_seconds,
    )


def test_transport_rejects_post_and_bodies(fault_server: str) -> None:
    transport = _transport_for(fault_server)
    with pytest.raises(ValueError, match="GET"):
        transport.request("POST", f"{fault_server}/v2/orders", {}, {"symbol": "AAPL"})
    with pytest.raises(ValueError, match="body"):
        transport.request("GET", f"{fault_server}/v2/account", {}, {"symbol": "AAPL"})
    assert transport.request_log == ()


def test_transport_rejects_url_outside_allowlist(fault_server: str) -> None:
    transport = _transport_for(fault_server)
    with pytest.raises(BrokerTransportError, match="allowlist"):
        transport.request("GET", "https://api.alpaca.markets/v2/account", {}, None)
    with pytest.raises(BrokerTransportError, match="API path"):
        transport.request("GET", f"{fault_server}/other/path", {}, None)
    assert transport.request_log == ()


def test_transport_fails_closed_on_rate_limit_and_server_error(
    fault_server: str,
) -> None:
    rate_limited = _transport_for(
        fault_server,
        timeout_seconds=2.0,
        retry_429_attempts=1,
        max_retry_wait_seconds=0.25,
    )
    _FaultHandler.mode = "rate_limited"
    response = rate_limited.request("GET", f"{fault_server}/v2/account", {}, None)
    assert response.status == 429
    assert type(response.body) is dict

    server_error = _transport_for(
        fault_server,
        timeout_seconds=2.0,
        retry_429_attempts=1,
        max_retry_wait_seconds=0.25,
    )
    _FaultHandler.mode = "server_error"
    response = server_error.request("GET", f"{fault_server}/v2/account", {}, None)
    assert response.status == 503
    assert type(response.body) is dict


def test_transport_retries_rate_limit_once_then_recovers(fault_server: str) -> None:
    transport = _transport_for(
        fault_server,
        timeout_seconds=2.0,
        retry_429_attempts=3,
        max_retry_wait_seconds=0.25,
    )
    attempts: list[int] = []

    def recording_do_get(self: _FaultHandler) -> None:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.end_headers()
            self.wfile.write(b'{"message":"rate limit"}')
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            b'{"account_number":"TEST1","cash":"1.00","equity":"1.00","buying_power":"1.00"}'
        )
        return

    original = _FaultHandler.do_GET
    try:
        _FaultHandler.do_GET = recording_do_get  # type: ignore[method-assign]
        response = transport.request("GET", f"{fault_server}/v2/account", {}, None)
    finally:
        _FaultHandler.do_GET = original  # type: ignore[method-assign]
    assert response.status == 200
    assert len(attempts) == 2
    assert [method for method, _ in transport.request_log] == ["GET"]


def test_transport_fails_closed_on_malformed_bodies(fault_server: str) -> None:
    transport = _transport_for(
        fault_server, timeout_seconds=2.0, retry_429_attempts=1, max_retry_wait_seconds=0.25
    )
    _FaultHandler.mode = "invalid_json"
    with pytest.raises(BrokerTransportError, match="unparsable"):
        transport.request("GET", f"{fault_server}/v2/account", {}, None)
    _FaultHandler.mode = "empty_body"
    with pytest.raises(BrokerTransportError, match="empty"):
        transport.request("GET", f"{fault_server}/v2/account", {}, None)


def test_transport_fails_closed_on_timeout(fault_server: str) -> None:
    transport = _transport_for(
        fault_server,
        timeout_seconds=0.2,
        retry_429_attempts=1,
        max_retry_wait_seconds=0.25,
    )
    _FaultHandler.mode = "silent"
    with pytest.raises(BrokerTransportError, match="timed out"):
        transport.request("GET", f"{fault_server}/v2/account", {}, None)


def test_transport_fails_closed_on_silent_drop(fault_server: str) -> None:
    transport = _transport_for(
        fault_server,
        timeout_seconds=2.0,
        retry_429_attempts=1,
        max_retry_wait_seconds=0.25,
    )
    _FaultHandler.mode = "silent"
    with pytest.raises(BrokerTransportError):
        transport.request("GET", f"{fault_server}/v2/account", {}, None)


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
