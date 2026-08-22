"""Alpaca Paper REST adapter behind an injectable transport.

The base URL can only be the exact Paper endpoint from ``PaperBrokerConfig``;
there is no live URL, no mode switch, and no fallback.  Every response is
strictly parsed: an unknown or missing field fails closed instead of being
coerced.  Credentials are revealed only when request headers are built.

Per the accepted P2 direction, only read-only use of this adapter against the
real endpoint is authorized; real order submission is deferred to P7.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final, Protocol
from urllib.parse import quote, urlencode

from seven_lens.application.composition import AlpacaPaperCredentials
from seven_lens.application.ports.broker import (
    AssetClass,
    AssetStatus,
    BrokerConflictError,
    BrokerTransportError,
    DuplicateClientOrderIdUnknown,
    PaperAccount,
    PaperAsset,
    PaperPosition,
    RejectionReason,
    SubmitAccepted,
    SubmitRejected,
    SubmitResult,
)
from seven_lens.config.broker import PAPER_API_BASE_URL, BrokerEnvironment, PaperBrokerConfig
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.execution.orders import (
    BrokerOrder,
    BrokerOrderStatus,
    ClientOrderId,
    Fill,
    OrderIntent,
    OrderQuantity,
    OrderSide,
    Price,
    Symbol,
    UsdAmount,
)

_TIMEOUT_STATUS_CODES: Final[frozenset[int]] = frozenset({408, 429, 500, 502, 503, 504})
_MAX_PAGINATION_PAGES: Final[int] = 100


class AlpacaResponse:
    """One transport-level response with a status and a parsed JSON body."""

    __slots__ = ("body", "status")

    def __init__(self, status: int, body: object) -> None:
        if type(status) is not int or status < 100 or status > 599:
            raise ValueError("response status must be a three-digit integer")
        self.status = status
        self.body = body


class AlpacaTransport(Protocol):
    """The only network seam; tests inject a deterministic fake."""

    def request(
        self, method: str, url: str, headers: dict[str, str], body: dict[str, object] | None
    ) -> AlpacaResponse: ...


class AlpacaPaperAdapter:
    """Implements ``PaperBrokerPort`` against the Paper REST API."""

    def __init__(
        self,
        *,
        config: PaperBrokerConfig,
        credentials: AlpacaPaperCredentials,
        transport: AlpacaTransport,
    ) -> None:
        if not isinstance(config, PaperBrokerConfig) or config.base_url != PAPER_API_BASE_URL:
            raise ValueError("adapter requires the exact Paper endpoint configuration")
        if config.environment is not BrokerEnvironment.PAPER:
            raise ValueError("adapter requires the PAPER environment")
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise ValueError("adapter requires resolved Alpaca credentials")
        self._config = config
        self._credentials = credentials
        self._transport = transport

    def account(self) -> PaperAccount:
        payload = self._get_json("/v2/account")
        return PaperAccount(
            account_id=_required_text(payload, "account_number"),
            environment=BrokerEnvironment.PAPER,
            cash=_usd(payload, "cash"),
            equity=_usd(payload, "equity"),
            buying_power=_usd(payload, "buying_power"),
        )

    def submit_order(self, intent: OrderIntent) -> SubmitResult:
        response = self._request(
            "POST",
            "/v2/orders",
            {
                "client_order_id": intent.client_order_id.value,
                "symbol": intent.symbol.value,
                "qty": str(intent.quantity.value),
                "side": intent.side.value.lower(),
                "type": "limit",
                "time_in_force": "day",
                "limit_price": str(intent.limit_price.value),
                "extended_hours": False,
            },
        )
        if response.status in _TIMEOUT_STATUS_CODES:
            raise BrokerTransportError("Alpaca submission outcome is unknown")
        if response.status in (400, 422):
            if _is_duplicate_rejection(response.body):
                return self._resolve_duplicate(intent)
            return _rejection(response.body)
        if response.status // 100 != 2:
            raise BrokerTransportError("Alpaca submission failed")
        return SubmitAccepted(order=_broker_order_from_payload(response.body))

    def _resolve_duplicate(self, intent: OrderIntent) -> SubmitAccepted:
        """Turn a duplicate client-order-id rejection into an idempotent accept.

        The broker explicitly says the deterministic id is already known, so
        this is a recovery signal, never an ordinary rejection.  Identity is
        proven by querying the existing order: a full parameter match is an
        idempotent accept, a contradiction is a structural conflict, and a
        missing or unreadable order keeps the outcome ambiguous (the engine
        must stay UNKNOWN, never conclude REJECTED).
        """
        query = urlencode({"client_order_id": intent.client_order_id.value})
        response = self._request("GET", f"/v2/orders:by_client_order_id?{query}", None)
        if response.status == 404:
            raise DuplicateClientOrderIdUnknown(
                "broker rejected a duplicate client order id but the follow-up "
                "query found no order; submission outcome is unknown"
            )
        if response.status in _TIMEOUT_STATUS_CODES:
            raise BrokerTransportError("Alpaca duplicate-resolution outcome is unknown")
        if response.status // 100 != 2:
            raise DuplicateClientOrderIdUnknown(
                "broker duplicate-resolution query failed; submission outcome is unknown"
            )
        order = _broker_order_from_payload(response.body)
        if (
            order.client_order_id != intent.client_order_id
            or order.symbol != intent.symbol
            or order.side != intent.side
            or order.quantity != intent.quantity
            or order.limit_price != intent.limit_price
        ):
            raise BrokerConflictError(
                "broker holds a duplicate client order id with different parameters"
            )
        return SubmitAccepted(order=order)

    def get_order(self, client_order_id: ClientOrderId) -> BrokerOrder | None:
        payload = self._get_json(
            "/v2/orders:by_client_order_id", client_order_id=client_order_id.value
        )
        if payload is None:
            return None
        return _broker_order_from_payload(payload)

    def list_open_orders(self) -> tuple[BrokerOrder, ...]:
        orders: list[BrokerOrder] = []
        seen: set[str] = set()
        cursor: str | None = None
        for _ in range(100):
            query = {"status": "open", "limit": "500", "direction": "asc"}
            if cursor is not None:
                query["after_order_id"] = cursor
            payload = self._get_json("/v2/orders", **query)
            if type(payload) is not list:
                raise BrokerTransportError("Alpaca returned a non-list open-orders body")
            page = [_broker_order_from_payload(item) for item in payload]
            for order in page:
                if order.broker_order_id not in seen:
                    seen.add(order.broker_order_id)
                    orders.append(order)
            if len(page) < 500:
                break
            next_cursor = page[-1].broker_order_id
            if next_cursor == cursor:
                raise BrokerTransportError("Alpaca open-order pagination is not advancing")
            cursor = next_cursor
        else:
            raise BrokerTransportError(
                "Alpaca open-order pagination exceeded the bounded page limit"
            )
        return tuple(orders)

    def list_recent_orders(self, *, since: UtcTimestamp) -> tuple[BrokerOrder, ...]:
        """Pull every order updated inside the horizon, fail-closed on gaps.

        Alpaca filters ``after`` by submission time, not update time.  Query
        from the start of the horizon's UTC day, paginate with the documented
        ``after_order_id`` cursor, then apply the update-time horizon locally.
        This preserves orders submitted earlier in the day and closed after a
        previous reconciliation.
        """
        orders: list[BrokerOrder] = []
        seen: set[str] = set()
        cursor: str | None = None
        submitted_after = datetime.combine(since.value.date(), datetime.min.time(), tzinfo=UTC)
        for _ in range(100):
            query = {"status": "all", "limit": "500", "direction": "asc"}
            if cursor is None:
                query["after"] = _canonical_timestamp(str(submitted_after))
            else:
                query["after_order_id"] = cursor
            payload = self._get_json("/v2/orders", **query)
            if type(payload) is not list:
                raise BrokerTransportError("Alpaca returned a non-list orders body")
            page = [_broker_order_from_payload(item) for item in payload]
            if not page:
                break
            for order in page:
                if order.updated_at.value >= since.value and order.broker_order_id not in seen:
                    seen.add(order.broker_order_id)
                    orders.append(order)
            if len(page) < 500:
                break
            next_cursor = page[-1].broker_order_id
            if next_cursor == cursor:
                raise BrokerTransportError("Alpaca order pagination is not advancing")
            cursor = next_cursor
        else:
            raise BrokerTransportError("Alpaca order pagination exceeded the bounded page limit")
        return tuple(orders)

    def list_fills(self, broker_order_id: str) -> tuple[Fill, ...]:
        fills: list[Fill] = []
        seen: set[str] = set()
        page_token: str | None = None
        seen_page_tokens: set[str] = set()
        for _ in range(_MAX_PAGINATION_PAGES):
            if page_token is not None:
                if page_token in seen_page_tokens:
                    raise BrokerTransportError("Alpaca fill pagination cursor cycle detected")
                seen_page_tokens.add(page_token)
            query: dict[str, str] = {
                "order_id": broker_order_id,
                "page_size": "100",
                "direction": "asc",
            }
            if page_token is not None:
                query["page_token"] = page_token
            payload = self._get_json("/v2/account/activities/FILL", **query)
            if type(payload) is not list:
                raise BrokerTransportError("Alpaca returned a non-list activities body")
            page = [_parse_fill_activity(item, broker_order_id) for item in payload]
            if not page:
                break
            for fill in page:
                if fill.execution_id not in seen:
                    seen.add(fill.execution_id)
                    fills.append(fill)
            if len(page) < 100:
                break
            next_page_token = page[-1].execution_id
            if next_page_token in seen_page_tokens:
                raise BrokerTransportError("Alpaca fill pagination cursor cycle detected")
            page_token = next_page_token
        else:
            raise BrokerTransportError("Alpaca fill pagination exceeded the bounded page limit")
        return tuple(fills)

    def list_positions(self) -> tuple[PaperPosition, ...]:
        payload = self._get_json("/v2/positions")
        if type(payload) is not list:
            raise BrokerTransportError("Alpaca returned a non-list positions body")
        positions: list[PaperPosition] = []
        for item in payload:
            positions.append(
                PaperPosition(
                    symbol=Symbol(_required_text(item, "symbol")),
                    quantity=_positive_int(item, "qty"),
                    average_entry_price=_price(item, "avg_entry_price"),
                )
            )
        return tuple(positions)

    def get_asset(self, symbol: Symbol) -> PaperAsset | None:
        payload = self._get_json(f"/v2/assets/{symbol.value}")
        if payload is None:
            return None
        if type(payload) is not dict:
            raise BrokerTransportError("Alpaca asset payload must be an object")
        tradable = payload.get("tradable")
        if type(tradable) is not bool:
            raise BrokerTransportError("Alpaca asset payload field tradable must be a boolean")
        status = _required_text(payload, "status")
        if status not in ("active", "inactive"):
            raise BrokerTransportError("Alpaca asset payload field status is not documented")
        return PaperAsset(
            symbol=symbol,
            asset_class=_map_asset_class(payload.get("class")),
            status=AssetStatus.ACTIVE if status == "active" else AssetStatus.INACTIVE,
            tradable=tradable,
            exchange=_required_text(payload, "exchange"),
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        encoded_id = quote(broker_order_id, safe="")
        response = self._request("DELETE", f"/v2/orders/{encoded_id}", None)
        if response.status == 404:
            return False
        if response.status // 100 != 2:
            raise BrokerTransportError("Alpaca order cancellation failed")
        return True

    def _get_json(self, path: str, **params: str) -> object:
        if params:
            query = urlencode(sorted(params.items()))
            path = f"{path}?{query}"
        return self._request_json("GET", path, None)

    def _request_json(self, method: str, path: str, body: dict[str, object] | None) -> object:
        response = self._request(method, path, body)
        if response.status == 404:
            if method == "GET":
                return None
            raise BrokerTransportError("Alpaca returned not-found for a write request")
        if response.status in _TIMEOUT_STATUS_CODES:
            raise BrokerTransportError("Alpaca request outcome is unknown")
        if response.status // 100 != 2:
            raise BrokerTransportError("Alpaca request failed")
        return response.body

    def _request(self, method: str, path: str, body: dict[str, object] | None) -> AlpacaResponse:
        url = f"{self._config.base_url}{path}"
        headers = {
            "APCA-API-KEY-ID": self._credentials.key_id.reveal_text(),
            "APCA-API-SECRET-KEY": self._credentials.secret_key.reveal_text(),
        }
        return self._transport.request(method, url, headers, body)


def _broker_order_from_payload(payload: object) -> BrokerOrder:
    if type(payload) is not dict:
        raise BrokerTransportError("Alpaca order payload must be an object")
    status = _map_order_status(_required_text(payload, "status"))
    submitted_at = _required_text(payload, "submitted_at")
    filled_qty = _non_negative_int(payload, "filled_qty")
    quantity = _positive_int(payload, "qty")
    if filled_qty > quantity:
        raise BrokerTransportError("Alpaca filled quantity exceeds the order quantity")
    order = BrokerOrder(
        broker_order_id=_required_text(payload, "id"),
        client_order_id=ClientOrderId(_required_text(payload, "client_order_id")),
        symbol=Symbol(_required_text(payload, "symbol")),
        side=OrderSide(_required_text(payload, "side").upper()),
        quantity=OrderQuantity(quantity),
        filled_quantity=filled_qty,
        limit_price=_price(payload, "limit_price"),
        status=status,
        submitted_at=UtcTimestamp.from_isoformat(_canonical_timestamp(submitted_at)),
        updated_at=UtcTimestamp.from_isoformat(
            _canonical_timestamp(_required_text(payload, "updated_at"))
        ),
    )
    return order


def _map_order_status(status: str) -> BrokerOrderStatus:
    """Map every documented Alpaca status onto the typed broker status set.

    Documented statuses the engine does not act on map to typed review
    statuses instead of raising; only a string the API never documents fails
    closed.
    """
    mapping = {
        "new": BrokerOrderStatus.RECEIVED,
        "pending_new": BrokerOrderStatus.RECEIVED,
        "accepted": BrokerOrderStatus.ACCEPTED,
        "accepted_for_bidding": BrokerOrderStatus.ACCEPTED_FOR_BIDDING,
        "partially_filled": BrokerOrderStatus.PARTIALLY_FILLED,
        "filled": BrokerOrderStatus.FILLED,
        "done_for_day": BrokerOrderStatus.DONE_FOR_DAY,
        "canceled": BrokerOrderStatus.CANCELED,
        "cancelled": BrokerOrderStatus.CANCELED,
        "expired": BrokerOrderStatus.EXPIRED,
        "rejected": BrokerOrderStatus.REJECTED,
        "pending_cancel": BrokerOrderStatus.PENDING_CANCEL,
        "pending_replace": BrokerOrderStatus.PENDING_REPLACE,
        "replaced": BrokerOrderStatus.REPLACED,
        "stopped": BrokerOrderStatus.STOPPED,
        "suspended": BrokerOrderStatus.SUSPENDED,
        "calculated": BrokerOrderStatus.CALCULATED,
    }
    if status not in mapping:
        raise BrokerTransportError("Alpaca returned an undocumented order status")
    return mapping[status]


def _map_asset_class(value: object) -> AssetClass:
    if type(value) is not str:
        raise BrokerTransportError("Alpaca asset payload field class must be text")
    mapping = {
        "us_equity": AssetClass.US_EQUITY,
        "us_option": AssetClass.US_OPTION,
        "crypto": AssetClass.CRYPTO,
        "future": AssetClass.FUTURE,
    }
    if value not in mapping:
        raise BrokerTransportError("Alpaca asset payload field class is not documented")
    return mapping[value]


def _canonical_timestamp(value: str) -> str:
    """Normalize an RFC3339 timestamp into the canonical UTC wire format."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise BrokerTransportError("Alpaca returned an unparsable timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BrokerTransportError("Alpaca timestamp must be timezone-aware")
    return str(UtcTimestamp(parsed.astimezone(UTC)))


def _parse_fill_activity(item: object, expected_broker_order_id: str) -> Fill:
    """Parse one Alpaca trade-activity entry into a domain fill."""
    broker_order_id = _required_text(item, "order_id")
    if broker_order_id != expected_broker_order_id:
        raise BrokerTransportError(
            "Alpaca fill activity order_id does not match the requested broker order"
        )
    return Fill(
        execution_id=_required_text(item, "id"),
        broker_order_id=broker_order_id,
        quantity=OrderQuantity(_positive_int(item, "qty")),
        price=_price(item, "price"),
        occurred_at=UtcTimestamp.from_isoformat(
            _canonical_timestamp(_required_text(item, "transaction_time"))
        ),
    )


def _is_duplicate_rejection(body: object) -> bool:
    """Detect Alpaca's client-order-id collision rejection message."""
    if type(body) is not dict:
        return False
    message = body.get("message")
    if type(message) is not str:
        return False
    lowered = message.lower()
    return ("client order id" in lowered or "client_order_id" in lowered) and (
        "already" in lowered or "duplicate" in lowered or "exist" in lowered
    )


def _rejection(body: object) -> SubmitRejected:
    reason = RejectionReason.ORDER_PARAMETERS_REJECTED
    if type(body) is dict:
        message = body.get("message")
        if type(message) is str:
            lowered = message.lower()
            if "buying power" in lowered or "cash" in lowered:
                reason = RejectionReason.INSUFFICIENT_CASH
            elif "not tradable" in lowered or "symbol" in lowered:
                reason = RejectionReason.SYMBOL_NOT_TRADEABLE
    return SubmitRejected(reason=reason)


def _required_text(payload: object, field: str) -> str:
    if type(payload) is not dict:
        raise BrokerTransportError("Alpaca payload must be an object")
    value = payload.get(field)
    if type(value) is not str or not value.strip():
        raise BrokerTransportError(f"Alpaca payload field {field} must be text")
    return value


def _price(payload: object, field: str) -> Price:
    value = _required_text(payload, field)
    try:
        return Price(_two_decimal_decimal(value, field))
    except (ValueError, ArithmeticError) as error:
        raise BrokerTransportError(f"Alpaca payload field {field} is not a price") from error


def _usd(payload: object, field: str) -> UsdAmount:
    value = _required_text(payload, field)
    try:
        return UsdAmount(_two_decimal_decimal(value, field))
    except (ValueError, ArithmeticError) as error:
        raise BrokerTransportError(f"Alpaca payload field {field} is not an amount") from error


def _two_decimal_decimal(value: str, field: str) -> Decimal:
    """Normalize a wire amount or price to exactly two decimal places."""
    amount = Decimal(value)
    if not amount.is_finite():
        raise ValueError(f"Alpaca payload field {field} is not finite")
    quantized = amount.quantize(Decimal("0.01"))
    if quantized != amount:
        raise ValueError(f"Alpaca payload field {field} exceeds two decimal places")
    return quantized


def _positive_int(payload: object, field: str) -> int:
    value = _required_text(payload, field)
    if not value.isdecimal() or int(value) < 1:
        raise BrokerTransportError(f"Alpaca payload field {field} must be a positive integer")
    return int(value)


def _non_negative_int(payload: object, field: str) -> int:
    value = _required_text(payload, field)
    if value == "":
        return 0
    if not value.isdecimal():
        raise BrokerTransportError(f"Alpaca payload field {field} must be an integer")
    return int(value)
