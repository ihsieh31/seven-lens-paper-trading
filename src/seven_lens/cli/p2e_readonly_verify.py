"""P2-E read-only live verification against the real Alpaca Paper endpoint.

This CLI never sends POST or DELETE requests: the transport is hard limited to
GET, the URL is bound to the Paper endpoint allowlist, and every failure
(timeout, 429, 5xx, unparsable body) is surfaced as a fail-closed error that
cannot be mistaken for a successful read.  Credentials follow the P2 boundary:
the exact Keychain references, resolved through ``ScopedSecretProvider`` with
the execution capability allowlist, and the runtime DSN is composed by
``compose_runtime_dsn`` from ``RuntimeDatabaseConfig``.

The read-only evidence is persisted through the closed reconciliation contract:
``Reconciler.run`` compares the broker's own account, order, fill, and position
views against the local authoritative tables and appends one
``reconciliation_runs`` row, exactly as the production control loop would.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from seven_lens.application.composition import (
    CompositionError,
    ExecutionStackConfig,
    execution_secret_refs,
    resolve_alpaca_credentials,
)
from seven_lens.application.ports.broker import (
    BrokerTransportError,
    PaperAccount,
    PaperPosition,
)
from seven_lens.application.reconciliation_service import Reconciler
from seven_lens.application.secret_service import ScopedSecretProvider
from seven_lens.config.broker import PAPER_API_BASE_URL
from seven_lens.domain.session import session_trading_date
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.execution.orders import BrokerOrder, Fill
from seven_lens.execution.reconciliation import ReconciliationResult
from seven_lens.infrastructure.alpaca_paper import (
    AlpacaPaperAdapter,
    AlpacaResponse,
)
from seven_lens.infrastructure.macos_keychain import MacOSKeychainSecretProvider
from seven_lens.infrastructure.postgres import (
    PostgresUnitOfWork,
    RuntimeDsn,
    compose_runtime_dsn,
)

_MAX_RESPONSE_BYTES: Final = 1_048_576
_MAX_TIMEOUT_SECONDS: Final = 120.0
_MAX_RETRY_ATTEMPTS: Final = 5
_API_PATH_PREFIX: Final = "/v2/"


def _parse_retry_after(value: object) -> float | None:
    if type(value) is not str or not value.strip():
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def _request_path(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.query:
        return f"{parsed.path}?{parsed.query}"
    return parsed.path


def _backoff(retry_after: float | None, maximum_wait: float) -> float:
    return max(0.25, min(maximum_wait, retry_after if retry_after is not None else 1.0))


class RealHttpTransport:
    """GET-only stdlib HTTPS transport with bounded 429 retries and fail-closed mapping.

    The request journal records only the method and path of every request so
    evidence can assert that no POST or DELETE was ever issued; headers, bodies,
    and credentials are never stored.
    """

    __slots__ = (
        "_max_retry_wait_seconds",
        "_request_log",
        "_retry_429_attempts",
        "_timeout_seconds",
        "_url_allowlist",
    )

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        retry_429_attempts: int = 3,
        max_retry_wait_seconds: float = 30.0,
        url_allowlist: tuple[str, ...] = (PAPER_API_BASE_URL,),
    ) -> None:
        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("transport timeout must be between 0 and 120 seconds")
        if (
            type(retry_429_attempts) is not int
            or not 1 <= retry_429_attempts <= _MAX_RETRY_ATTEMPTS
        ):
            raise ValueError("transport 429 retry attempts must be between 1 and 5")
        if (
            type(max_retry_wait_seconds) not in {int, float}
            or not math.isfinite(max_retry_wait_seconds)
            or max_retry_wait_seconds <= 0
        ):
            raise ValueError("transport retry wait must be a positive bounded number")
        if (
            type(url_allowlist) is not tuple
            or not url_allowlist
            or any(type(origin) is not str or not origin for origin in url_allowlist)
        ):
            raise ValueError("transport requires a non-empty URL allowlist")
        self._timeout_seconds = float(timeout_seconds)
        self._retry_429_attempts = retry_429_attempts
        self._max_retry_wait_seconds = float(max_retry_wait_seconds)
        self._url_allowlist = url_allowlist
        self._request_log: tuple[tuple[str, str], ...] = ()

    @property
    def request_log(self) -> tuple[tuple[str, str], ...]:
        return self._request_log

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, object] | None,
    ) -> AlpacaResponse:
        if method != "GET":
            raise ValueError("P2-E verification transport only allows GET requests")
        if body is not None:
            raise ValueError("P2-E verification transport never sends a request body")
        self._assert_allowed_url(url)
        self._request_log = (*self._request_log, (method, _request_path(url)))
        attempt = 0
        while True:
            attempt += 1
            status, raw_body, retry_after = self._single_request(url, headers)
            if status == 429 and attempt < self._retry_429_attempts:
                time.sleep(_backoff(retry_after, self._max_retry_wait_seconds))
                continue
            return AlpacaResponse(status, _parse_body(status, raw_body))

    def _assert_allowed_url(self, url: str) -> None:
        try:
            parsed = urlsplit(url)
        except ValueError as error:
            raise BrokerTransportError("Alpaca URL is not parsable") from error
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._url_allowlist:
            raise BrokerTransportError("Alpaca URL is not in the transport allowlist")
        if not parsed.path.startswith(_API_PATH_PREFIX):
            raise BrokerTransportError("Alpaca URL is not under the API path prefix")

    def _single_request(self, url: str, headers: dict[str, str]) -> tuple[int, bytes, float | None]:
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as raw:
                return (
                    raw.status,
                    _bounded_read(raw),
                    _parse_retry_after(raw.headers.get("Retry-After")),
                )
        except urllib.error.HTTPError as error:
            return (
                error.code,
                _bounded_read(error),
                _parse_retry_after(error.headers.get("Retry-After")),
            )
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise BrokerTransportError(
                "Alpaca read failed or timed out at the transport layer"
            ) from error


def _bounded_read(raw: object) -> bytes:
    try:
        body = raw.read(_MAX_RESPONSE_BYTES)  # type: ignore[attr-defined]
    except OSError as error:
        raise BrokerTransportError("Alpaca response could not be read") from error
    if type(body) is not bytes:
        raise BrokerTransportError("Alpaca response body is not bytes")
    if len(body) >= _MAX_RESPONSE_BYTES:
        raise BrokerTransportError("Alpaca response body exceeds the bounded size")
    return body


def _parse_body(status: int, raw_body: bytes) -> object:
    if not raw_body.strip():
        if status == 404:
            return None
        raise BrokerTransportError("Alpaca returned an empty response body")
    try:
        return json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BrokerTransportError("Alpaca returned an unparsable response body") from error


@dataclass(frozen=True, slots=True)
class ReadOnlyVerificationReport:
    """The complete, non-secret snapshot produced by one verification run."""

    observed_at: UtcTimestamp
    account: PaperAccount
    positions: tuple[PaperPosition, ...]
    open_orders: tuple[BrokerOrder, ...]
    fills: tuple[Fill, ...]
    reconciliation: ReconciliationResult
    request_log: tuple[tuple[str, str], ...]

    def to_json(self) -> str:
        payload: dict[str, object] = {
            "read_only": True,
            "observed_at": str(self.observed_at),
            "account": {
                "account_id": self.account.account_id,
                "environment": self.account.environment.value,
                "cash": str(self.account.cash.value),
                "equity": str(self.account.equity.value),
            },
            "positions": [_position_to_json(position) for position in self.positions],
            "open_orders": [_order_to_json(order) for order in self.open_orders],
            "fills": [_fill_to_json(fill) for fill in self.fills],
            "reconciliation": {
                "run_id": str(self.reconciliation.run_id),
                "trading_date": str(self.reconciliation.trading_date),
                "status": self.reconciliation.status.value,
                "mismatches": [
                    {"kind": mismatch.kind.value, "detail": mismatch.detail}
                    for mismatch in self.reconciliation.mismatches
                ],
                "checked_orders": self.reconciliation.checked_orders,
                "checked_fills": self.reconciliation.checked_fills,
                "observed_at": str(self.reconciliation.observed_at),
            },
            "requests": [{"method": method, "path": path} for method, path in self.request_log],
        }
        return json.dumps(payload, indent=2, sort_keys=True)


def _position_to_json(position: PaperPosition) -> dict[str, object]:
    return {
        "symbol": position.symbol.value,
        "quantity": position.quantity,
        "average_entry_price": str(position.average_entry_price.value),
    }


def _order_to_json(order: BrokerOrder) -> dict[str, object]:
    return {
        "broker_order_id": order.broker_order_id,
        "client_order_id": order.client_order_id.value,
        "symbol": order.symbol.value,
        "side": order.side.value,
        "quantity": order.quantity.value,
        "filled_quantity": order.filled_quantity,
        "limit_price": str(order.limit_price.value),
        "status": order.status.value,
        "submitted_at": str(order.submitted_at),
        "updated_at": str(order.updated_at),
    }


def _fill_to_json(fill: Fill) -> dict[str, object]:
    return {
        "execution_id": fill.execution_id,
        "broker_order_id": fill.broker_order_id,
        "quantity": fill.quantity.value,
        "price": str(fill.price.value),
        "occurred_at": str(fill.occurred_at),
    }


def run_verification(config: Path) -> ReadOnlyVerificationReport:
    """Resolve one typed config, read the real Paper account read-only, and persist."""
    try:
        raw = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CompositionError("execution configuration is unreadable or invalid JSON") from error
    if type(raw) is not dict:
        raise CompositionError("execution configuration must be a JSON object")
    stack_config = ExecutionStackConfig.from_mapping(raw)
    provider = ScopedSecretProvider(
        MacOSKeychainSecretProvider(timeout_seconds=6.0), execution_secret_refs()
    )
    credentials = resolve_alpaca_credentials(provider)
    runtime_dsn = compose_runtime_dsn(stack_config.database, provider)
    transport = RealHttpTransport()
    adapter = AlpacaPaperAdapter(
        config=stack_config.paper,
        credentials=credentials,
        transport=transport,
    )
    observed_at = UtcTimestamp.now()
    account = adapter.account()
    positions = adapter.list_positions()
    open_orders = adapter.list_open_orders()
    fills: list[Fill] = []
    for order in open_orders:
        fills.extend(adapter.list_fills(order.broker_order_id))
    reconciliation = _record_reconciliation(runtime_dsn, adapter, observed_at)
    return ReadOnlyVerificationReport(
        observed_at=observed_at,
        account=account,
        positions=positions,
        open_orders=open_orders,
        fills=tuple(fills),
        reconciliation=reconciliation,
        request_log=transport.request_log,
    )


def _record_reconciliation(
    runtime_dsn: RuntimeDsn,
    adapter: AlpacaPaperAdapter,
    observed_at: UtcTimestamp,
) -> ReconciliationResult:
    reconciler = Reconciler(broker=adapter, clock=lambda: observed_at)
    trading_date = session_trading_date(observed_at)
    with PostgresUnitOfWork(runtime_dsn.conninfo()) as unit_of_work:
        return reconciler.run(unit_of_work, trading_date)


def _human_report(report: ReadOnlyVerificationReport) -> str:
    lines = [
        "P2-E read-only verification (Alpaca Paper, GET only)",
        f"observed_at: {report.observed_at}",
        f"account_id: {report.account.account_id}",
        f"environment: {report.account.environment.value}",
        f"cash: {report.account.cash.value}",
        f"equity: {report.account.equity.value}",
        f"positions: {len(report.positions)}",
    ]
    lines.extend(
        f"  {position.symbol.value} {position.quantity} @ {position.average_entry_price.value}"
        for position in report.positions
    )
    lines.append(f"open_orders: {len(report.open_orders)}")
    lines.extend(
        f"  {order.side.value} {order.symbol.value} {order.quantity.value} "
        f"{order.status.value} {order.broker_order_id}"
        for order in report.open_orders
    )
    lines.append(f"known_fills: {len(report.fills)}")
    lines.extend(
        f"  {fill.broker_order_id} {fill.quantity.value} @ {fill.price.value} {fill.occurred_at}"
        for fill in report.fills
    )
    reconciliation = report.reconciliation
    lines.extend(
        [
            f"reconciliation: {reconciliation.status.value}",
            f"  run_id: {reconciliation.run_id}",
            f"  checked_orders: {reconciliation.checked_orders}",
            f"  checked_fills: {reconciliation.checked_fills}",
            f"  mismatch_count: {len(reconciliation.mismatches)}",
        ]
    )
    lines.extend(
        f"  MISMATCH {mismatch.kind.value}: {mismatch.detail}"
        for mismatch in reconciliation.mismatches
    )
    if reconciliation.status.value == "MISMATCH":
        lines.append("  control plane auto-paused entries on the local database")
    lines.append("requests issued:")
    lines.extend(f"  {method} {path}" for method, path in report.request_log)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="P2-E read-only verification against the real Alpaca Paper endpoint"
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="JSON execution configuration with the exact ExecutionStackConfig schema",
    )
    args = parser.parse_args(argv)
    try:
        report = run_verification(args.config)
    except Exception as error:
        print(f"P2-E read-only verification FAILED: {error}", file=sys.stderr)
        return 1
    print(_human_report(report))
    print(report.to_json())
    print("P2-E read-only verification COMPLETED; only GET requests were issued.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
