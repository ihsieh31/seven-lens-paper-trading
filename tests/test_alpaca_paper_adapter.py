# mypy: ignore-errors
"""Transport-faked tests for the Alpaca Paper adapter contract."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from seven_lens.application.composition import (
    AlpacaPaperCredentials,
    ExecutionStackConfig,
)
from seven_lens.application.ports.broker import (
    BrokerConflictError,
    BrokerTransportError,
    DuplicateClientOrderIdUnknown,
    RejectionReason,
    SubmitAccepted,
    SubmitRejected,
)
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.orders import (
    BrokerOrderStatus,
    OrderIntent,
    OrderIntentType,
    OrderQuantity,
    OrderSide,
    OrderStatus,
    Price,
    PriceCollar,
    Symbol,
)
from seven_lens.infrastructure.alpaca_paper import (
    AlpacaPaperAdapter,
    AlpacaResponse,
    AlpacaTransport,
)
from seven_lens.security.secret_values import SecretValue

_BASE_TIME = UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z")
_CANCEL_AT = UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z")
_PAPER_URL = "https://paper-api.alpaca.markets"

_ORDER_PAYLOAD: dict[str, object] = {
    "id": "broker-000001",
    "client_order_id": "slv1-seven-lens-2026-08-17-open-t1-AAPL-buy",
    "symbol": "AAPL",
    "side": "buy",
    "qty": "10",
    "filled_qty": "0",
    "limit_price": "100.00",
    "status": "accepted",
    "submitted_at": "2026-08-17T13:35:01.123456Z",
    "updated_at": "2026-08-17T13:35:01.123456Z",
}

_ASSET_PAYLOAD: dict[str, object] = {
    "symbol": "AAPL",
    "class": "us_equity",
    "status": "active",
    "tradable": True,
    "exchange": "ARCA",
}


Responder = "Callable[[str, str], AlpacaResponse]"


class RecordingTransport:
    def __init__(
        self,
        responder: Callable[[str, str], AlpacaResponse] | None = None,
    ) -> None:
        self.requests: list[tuple[str, str, dict[str, str], object | None]] = []
        self._responder = responder

    def request(
        self, method: str, url: str, headers: dict[str, str], body: dict[str, object] | None
    ) -> AlpacaResponse:
        self.requests.append((method, url, headers, body))
        if self._responder is None:
            raise AssertionError("recording transport requires a responder")
        return self._responder(method, url)


class _FixedClock:
    def __call__(self) -> UtcTimestamp:
        return _BASE_TIME


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(
        key_id=SecretValue(b"fake-key-id"),
        secret_key=SecretValue(b"fake-secret-key"),
    )


def _config_mapping() -> dict[str, object]:
    return {
        "paper": {"environment": "PAPER", "base_url": _PAPER_URL},
        "database": {
            "host": "localhost",
            "port": 5432,
            "dbname": "seven_lens",
            "user": "seven_lens_runtime",
            "sslmode": "require",
            "password_account": "primary",
        },
        "account": {
            "expected_account_id": "fake-paper-primary",
            "cash_tolerance_cents": 100,
            "nav_tolerance_cents": 100,
        },
        "alpaca_key_account": "primary",
        "alpaca_secret_account": "primary",
    }


def _intent() -> OrderIntent:
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=TradingDate.from_isoformat("2026-08-17"),
        window="open",
        target_version=1,
        symbol=Symbol("AAPL"),
        side=OrderSide.BUY,
        quantity=OrderQuantity(10),
        intent_type=OrderIntentType.REBALANCE,
        limit_price=Price.from_cents(10_000),
        collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
        earliest_submit_at=_BASE_TIME,
        cancel_at=_CANCEL_AT,
        run_id=RunId.new(),
        created_at=_BASE_TIME,
    )


def _adapter(transport: AlpacaTransport) -> AlpacaPaperAdapter:
    config = ExecutionStackConfig.from_mapping(_config_mapping())
    return AlpacaPaperAdapter(config=config.paper, credentials=_credentials(), transport=transport)


class TestEndpointAndHeaderBoundary:
    def test_every_request_targets_the_exact_paper_endpoint(self) -> None:
        transport = RecordingTransport(
            responder=lambda method, url: AlpacaResponse(200, dict(_ORDER_PAYLOAD))
        )
        adapter = _adapter(transport)
        adapter.get_order(_intent().client_order_id)

        _, url, headers, _ = transport.requests[0]
        assert url.startswith(_PAPER_URL + "/")
        assert headers["APCA-API-KEY-ID"] == "fake-key-id"
        assert headers["APCA-API-SECRET-KEY"] == "fake-secret-key"

    def test_adapter_rejects_any_non_paper_configuration(self) -> None:
        from seven_lens.config.broker import PaperBrokerConfig
        from seven_lens.config.errors import ConfigurationError

        with pytest.raises((ValueError, ConfigurationError)):
            AlpacaPaperAdapter(
                config=PaperBrokerConfig.from_mapping(
                    {"environment": "PAPER", "base_url": "https://api.alpaca.markets"}
                ),
                credentials=_credentials(),
                transport=RecordingTransport(),
            )

    def test_single_asset_uses_the_documented_symbol_path(self) -> None:
        transport = RecordingTransport(
            responder=lambda _method, _url: AlpacaResponse(200, dict(_ASSET_PAYLOAD))
        )

        asset = _adapter(transport).get_asset(Symbol("AAPL"))

        assert asset is not None
        assert transport.requests[0][1] == _PAPER_URL + "/v2/assets/AAPL"


class TestOrderParsing:
    def test_accepted_order_is_mapped_to_the_mirror(self) -> None:
        transport = RecordingTransport(
            responder=lambda method, url: AlpacaResponse(200, dict(_ORDER_PAYLOAD))
        )
        adapter = _adapter(transport)

        order = adapter.get_order(_intent().client_order_id)

        assert order is not None
        assert order.status is BrokerOrderStatus.ACCEPTED
        assert order.submitted_at.value.microsecond == 123456

    def test_unknown_order_returns_none(self) -> None:
        transport = RecordingTransport(responder=lambda method, url: AlpacaResponse(404, {}))
        adapter = _adapter(transport)
        assert adapter.get_order(_intent().client_order_id) is None

    def test_unknown_status_or_missing_field_fails_closed(self) -> None:
        def responder_for(payload: dict[str, object]) -> Callable[[str, str], AlpacaResponse]:
            def responder(_method: str, _url: str) -> AlpacaResponse:
                return AlpacaResponse(200, payload)

            return responder

        for unknown_status in ("quantum", "held"):
            broken = dict(_ORDER_PAYLOAD)
            broken["status"] = unknown_status
            transport = RecordingTransport(responder=responder_for(broken))
            with pytest.raises(BrokerTransportError, match="undocumented order status"):
                _adapter(transport).get_order(_intent().client_order_id)

        broken = dict(_ORDER_PAYLOAD)
        del broken["limit_price"]
        transport = RecordingTransport(responder=lambda m, u: AlpacaResponse(200, broken))
        with pytest.raises(BrokerTransportError, match="limit_price"):
            _adapter(transport).get_order(_intent().client_order_id)

        broken = dict(_ORDER_PAYLOAD)
        broken["status"] = "filled"
        broken["filled_qty"] = "0"
        transport = RecordingTransport(responder=lambda m, u: AlpacaResponse(200, broken))
        with pytest.raises(BrokerTransportError, match="full order quantity"):
            _adapter(transport).get_order(_intent().client_order_id)

    def test_timeout_status_codes_are_transport_errors(self) -> None:
        for status in (408, 429, 500, 503):

            def responder(method: str, url: str, expected: int = status) -> AlpacaResponse:
                del method, url
                return AlpacaResponse(expected, {})

            transport = RecordingTransport(responder=responder)
            with pytest.raises(BrokerTransportError):
                _adapter(transport).get_order(_intent().client_order_id)


class TestSubmission:
    def test_submit_builds_a_limited_day_order_with_the_client_id(self) -> None:
        transport = RecordingTransport(
            responder=lambda method, url: AlpacaResponse(200, dict(_ORDER_PAYLOAD))
        )
        adapter = _adapter(transport)

        result = adapter.submit_order(_intent())

        assert isinstance(result, SubmitAccepted)
        method, url, _, body = transport.requests[0]
        assert method == "POST" and url == _PAPER_URL + "/v2/orders"
        assert isinstance(body, dict)
        assert body["client_order_id"] == "slv1-seven-lens-2026-08-17-open-t1-AAPL-buy"
        assert body["type"] == "limit"
        assert body["time_in_force"] == "day"
        assert body["extended_hours"] is False
        assert body["limit_price"] == "100.00"

    def test_message_only_rejection_does_not_infer_a_fine_grained_reason(self) -> None:
        transport = RecordingTransport(
            responder=lambda m, u: AlpacaResponse(400, {"message": "insufficient buying power"})
        )
        result = _adapter(transport).submit_order(_intent())
        assert result == SubmitRejected(reason=RejectionReason.ORDER_PARAMETERS_REJECTED)

    def test_known_structured_error_code_uses_the_closed_mapping(self) -> None:
        transport = RecordingTransport(
            responder=lambda m, u: AlpacaResponse(
                422,
                {"code": 42210000, "message": "provider wording may change"},
            )
        )

        result = _adapter(transport).submit_order(_intent())

        assert result == SubmitRejected(reason=RejectionReason.ORDER_PARAMETERS_REJECTED)

    @pytest.mark.parametrize(
        "body",
        (
            {"code": 99999999, "message": "cash is unavailable"},
            {"code": 99999999, "message": "symbol is not tradable"},
            {"message": "symbol is not tradable"},
        ),
    )
    def test_unknown_code_or_message_uses_generic_rejection(self, body: dict[str, object]) -> None:
        transport = RecordingTransport(responder=lambda m, u: AlpacaResponse(422, body))

        result = _adapter(transport).submit_order(_intent())

        assert result == SubmitRejected(reason=RejectionReason.ORDER_PARAMETERS_REJECTED)

    def test_generic_parameter_rejection_is_classified(self) -> None:
        transport = RecordingTransport(
            responder=lambda m, u: AlpacaResponse(422, {"message": "invalid limit_price"})
        )
        result = _adapter(transport).submit_order(_intent())
        assert result == SubmitRejected(reason=RejectionReason.ORDER_PARAMETERS_REJECTED)


class TestPositionsAndFills:
    def test_positions_are_strictly_mapped(self) -> None:
        payload = [
            {"symbol": "AAPL", "qty": "10", "avg_entry_price": "100.00"},
        ]
        transport = RecordingTransport(responder=lambda m, u: AlpacaResponse(200, payload))
        positions = _adapter(transport).list_positions()
        assert len(positions) == 1
        assert positions[0].symbol == Symbol("AAPL")
        assert positions[0].quantity == 10

    def test_position_payload_missing_field_fails_closed(self) -> None:
        transport = RecordingTransport(
            responder=lambda m, u: AlpacaResponse(200, [{"symbol": "AAPL", "qty": "10"}])
        )
        with pytest.raises(BrokerTransportError, match="avg_entry_price"):
            _adapter(transport).list_positions()

    def test_fills_are_mapped_from_activities(self) -> None:
        payload = [
            {
                "id": "exec-000001",
                "order_id": "broker-000001",
                "qty": "4",
                "price": "99.98",
                "transaction_time": "2026-08-17T13:35:02.500000Z",
            }
        ]
        transport = RecordingTransport(responder=lambda m, u: AlpacaResponse(200, payload))
        fills = _adapter(transport).list_fills("broker-000001")
        assert fills[0].execution_id == "exec-000001"
        assert fills[0].quantity.value == 4

    def test_fill_from_a_different_order_fails_closed(self) -> None:
        payload = [_activity(1, "2026-08-17T13:35:02.500000Z")]
        payload[0]["order_id"] = "broker-other"
        transport = RecordingTransport(responder=lambda m, u: AlpacaResponse(200, payload))

        with pytest.raises(BrokerTransportError, match="order_id"):
            _adapter(transport).list_fills("broker-000001")


def test_adapter_plugs_into_the_engine_without_network() -> None:
    """The adapter satisfies the broker port the engine is built against."""
    from seven_lens.application.execution_service import ExecutionEngine

    def responder(method: str, url: str) -> AlpacaResponse:
        del method
        if "/assets" in url:
            return AlpacaResponse(200, dict(_ASSET_PAYLOAD))
        if "/activities" in url:
            return AlpacaResponse(200, [])
        return AlpacaResponse(200, dict(_ORDER_PAYLOAD))

    transport = RecordingTransport(responder=responder)
    adapter = _adapter(transport)
    engine = ExecutionEngine(broker=adapter, clock=_FixedClock())
    unit_of_work = _TransportUnitOfWork(adapter)
    intent = _intent()
    unit_of_work.orders.add(intent)
    unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
    unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)

    result = engine.submit_from_outbox(unit_of_work, intent.client_order_id)

    assert result.status is OrderStatus.ACKNOWLEDGED


class _TransportUnitOfWork:
    def __init__(self, broker: AlpacaPaperAdapter) -> None:
        from fakes.orders import FakeOrderRepository

        self._broker = broker
        self.orders = FakeOrderRepository()
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class TestDuplicateClientOrderId:
    def test_duplicate_id_rejection_is_resolved_not_classified_rejected(self) -> None:
        def responder(method: str, url: str) -> AlpacaResponse:
            del url
            if method == "POST":
                return AlpacaResponse(422, {"message": "client order id already exists"})
            return AlpacaResponse(200, dict(_ORDER_PAYLOAD))

        transport = RecordingTransport(responder=responder)
        adapter = _adapter(transport)

        result = adapter.submit_order(_intent())

        assert isinstance(result, SubmitAccepted)
        assert result.order.broker_order_id == "broker-000001"
        methods = [request[0] for request in transport.requests]
        assert methods[0] == "POST"
        assert "GET" in methods[1:]

    def test_duplicate_with_contradicting_parameters_raises_conflict(self) -> None:
        """A contradictory duplicate is a structural conflict, never a rejection."""

        def responder(method: str, url: str) -> AlpacaResponse:
            del url
            if method == "POST":
                return AlpacaResponse(422, {"message": "duplicate client order id"})
            conflicting = dict(_ORDER_PAYLOAD)
            conflicting["qty"] = "20"
            return AlpacaResponse(200, conflicting)

        with pytest.raises(BrokerConflictError, match="different parameters"):
            _adapter(RecordingTransport(responder=responder)).submit_order(_intent())

    def test_duplicate_id_with_missing_order_is_ambiguity_not_rejection(self) -> None:
        """GET none after a duplicate rejection means the outcome is UNKNOWN-grade."""

        def responder(method: str, url: str) -> AlpacaResponse:
            del url
            if method == "POST":
                return AlpacaResponse(422, {"message": "duplicate client order id"})
            return AlpacaResponse(404, {})

        with pytest.raises(DuplicateClientOrderIdUnknown, match="unknown"):
            _adapter(RecordingTransport(responder=responder)).submit_order(_intent())


def _activity(execution_id: int, transaction_time: str) -> dict[str, object]:
    return {
        "id": f"exec-{execution_id:06d}",
        "order_id": "broker-000001",
        "qty": "1",
        "price": "99.98",
        "transaction_time": transaction_time,
    }


class TestFillPagination:
    def test_more_than_one_hundred_fills_are_all_returned(self) -> None:
        def responder(method: str, url: str) -> AlpacaResponse:
            del method
            if "page_token=exec-000100" in url:
                return AlpacaResponse(200, [_activity(101, "2026-08-17T13:36:40.000000Z")])
            return AlpacaResponse(
                200,
                [
                    _activity(
                        index,
                        "2026-08-17T13:"
                        f"{35 + (index - 1) // 60:02d}:{(index - 1) % 60:02d}.000000Z",
                    )
                    for index in range(1, 101)
                ],
            )

        fills = _adapter(RecordingTransport(responder=responder)).list_fills("broker-000001")

        assert len(fills) == 101
        assert fills[0].execution_id == "exec-000001"
        assert fills[100].execution_id == "exec-000101"

    def test_uses_documented_fill_activity_cursor(self) -> None:
        transport = RecordingTransport(responder=lambda _method, _url: AlpacaResponse(200, []))

        _adapter(transport).list_fills("broker-000001")

        url = transport.requests[0][1]
        assert "/v2/account/activities/FILL?" in url
        assert "page_size=100" in url
        assert "direction=asc" in url
        assert "limit=" not in url

    def test_fill_cursor_cycle_fails_closed(self) -> None:
        first_page = [_activity(index, "2026-08-17T13:35:00.000000Z") for index in range(1, 101)]
        second_page = [_activity(index, "2026-08-17T13:36:00.000000Z") for index in range(101, 201)]

        def responder(_method: str, url: str) -> AlpacaResponse:
            if "page_token=exec-000100" in url:
                return AlpacaResponse(200, second_page)
            if "page_token=exec-000200" in url:
                return AlpacaResponse(200, first_page)
            return AlpacaResponse(200, first_page)

        transport = RecordingTransport(responder=responder)
        with pytest.raises(BrokerTransportError, match="cursor cycle"):
            _adapter(transport).list_fills("broker-000001")

        assert len(transport.requests) == 3

    def test_fill_pagination_has_a_bounded_page_limit(self) -> None:
        request_count = 0

        def responder(_method: str, _url: str) -> AlpacaResponse:
            nonlocal request_count
            start = request_count * 100 + 1
            request_count += 1
            page = [
                _activity(index, "2026-08-17T13:35:00.000000Z")
                for index in range(start, start + 100)
            ]
            return AlpacaResponse(200, page)

        transport = RecordingTransport(responder=responder)
        with pytest.raises(BrokerTransportError, match="bounded page limit"):
            _adapter(transport).list_fills("broker-000001")

        assert len(transport.requests) == 100


class TestRecentOrderPagination:
    def test_uses_order_id_cursor_and_filters_by_broker_update_time(self) -> None:
        first_page = []
        for index in range(500):
            payload = dict(_ORDER_PAYLOAD)
            payload["id"] = f"broker-{index:06d}"
            payload["client_order_id"] = f"slv1-seven-lens-2026-08-17-open-t{index + 1}-AAPL-buy"
            payload["submitted_at"] = "2026-08-17T13:00:00.000000Z"
            payload["updated_at"] = "2026-08-17T13:30:00.000000Z"
            first_page.append(payload)
        recent = dict(_ORDER_PAYLOAD)
        recent["id"] = "broker-recent"
        recent["client_order_id"] = "slv1-seven-lens-2026-08-17-open-t501-AAPL-buy"
        recent["submitted_at"] = "2026-08-16T23:55:00.000000Z"
        recent["updated_at"] = "2026-08-17T13:40:00.000000Z"

        def responder(_method: str, url: str) -> AlpacaResponse:
            if "after_order_id=broker-000499" in url:
                return AlpacaResponse(200, [recent])
            return AlpacaResponse(200, first_page)

        transport = RecordingTransport(responder=responder)
        orders = _adapter(transport).list_recent_orders(
            since=UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z")
        )

        assert [order.broker_order_id for order in orders] == ["broker-recent"]
        assert "after=" in transport.requests[0][1]
        assert parse_qs(urlsplit(transport.requests[0][1]).query)["after"] == [
            "2026-08-16T00:00:00.000000Z"
        ]
        assert "after_order_id=broker-000499" in transport.requests[1][1]


class TestOpenOrderPagination:
    def test_more_than_five_hundred_open_orders_are_all_returned(self) -> None:
        first_page = []
        for index in range(500):
            payload = dict(_ORDER_PAYLOAD)
            payload["id"] = f"broker-{index:06d}"
            payload["client_order_id"] = f"slv1-seven-lens-2026-08-17-open-t{index + 1}-AAPL-buy"
            first_page.append(payload)
        final_order = dict(_ORDER_PAYLOAD)
        final_order["id"] = "broker-000500"
        final_order["client_order_id"] = "slv1-seven-lens-2026-08-17-open-t501-AAPL-buy"

        def responder(_method: str, url: str) -> AlpacaResponse:
            if "after_order_id=broker-000499" in url:
                return AlpacaResponse(200, [final_order])
            return AlpacaResponse(200, first_page)

        transport = RecordingTransport(responder=responder)
        orders = _adapter(transport).list_open_orders()

        assert len(orders) == 501
        assert orders[-1].broker_order_id == "broker-000500"
        assert "direction=asc" in transport.requests[0][1]
        assert "after_order_id=broker-000499" in transport.requests[1][1]

    def test_nonadvancing_open_order_cursor_fails_closed(self) -> None:
        page = []
        for index in range(500):
            payload = dict(_ORDER_PAYLOAD)
            payload["id"] = f"broker-{index:06d}"
            payload["client_order_id"] = f"slv1-seven-lens-2026-08-17-open-t{index + 1}-AAPL-buy"
            page.append(payload)
        transport = RecordingTransport(responder=lambda _method, _url: AlpacaResponse(200, page))

        with pytest.raises(BrokerTransportError, match="not advancing"):
            _adapter(transport).list_open_orders()


class TestQueryAndPathEncoding:
    """Broker-sourced identifiers must stay whole URL components, never syntax."""

    def test_query_values_with_reserved_characters_are_url_encoded(self) -> None:
        malicious_order_id = "broker/one&status=all?x=1%25"
        transport = RecordingTransport(responder=lambda _method, _url: AlpacaResponse(200, []))

        _adapter(transport).list_fills(malicious_order_id)

        url = transport.requests[0][1]
        parsed_query = parse_qs(urlsplit(url).query)
        assert set(parsed_query) == {"order_id", "page_size", "direction"}
        assert parsed_query["order_id"] == [malicious_order_id]

    def test_broker_sourced_fill_cursor_stays_one_whole_parameter(self) -> None:
        tricky_cursor = "exec-000100?next=/evil&x=1"
        first_page = [_activity(index, "2026-08-17T13:35:00.000000Z") for index in range(1, 100)]
        cursor_activity = dict(_activity(0, "2026-08-17T13:36:00.000000Z"))
        cursor_activity["id"] = tricky_cursor
        first_page.append(cursor_activity)

        def responder(_method: str, url: str) -> AlpacaResponse:
            if "page_token=" in url:
                return AlpacaResponse(200, [])
            return AlpacaResponse(200, first_page)

        transport = RecordingTransport(responder=responder)
        fills = _adapter(transport).list_fills("broker-000001")

        assert len(fills) == 100
        second_url = transport.requests[1][1]
        parsed_query = parse_qs(urlsplit(second_url).query)
        assert set(parsed_query) == {"order_id", "page_size", "direction", "page_token"}
        assert parsed_query["page_token"] == [tricky_cursor]

    def test_cancel_order_encodes_only_the_path_segment(self) -> None:
        tricky_order_id = "b/1?x=1#f%2F&s=2"
        transport = RecordingTransport(responder=lambda _m, _u: AlpacaResponse(204, None))

        assert _adapter(transport).cancel_order(tricky_order_id) is True

        method, url = transport.requests[0][0], transport.requests[0][1]
        parsed = urlsplit(url)
        assert method == "DELETE"
        assert parsed.query == ""
        assert parsed.fragment == ""
        assert parsed.path == "/v2/orders/" + quote(tricky_order_id, safe="")

    def test_safe_identifiers_keep_their_exact_request_semantics(self) -> None:
        transport = RecordingTransport(responder=lambda _method, _url: AlpacaResponse(200, []))

        _adapter(transport).list_fills("broker-000001")

        parsed_query = parse_qs(urlsplit(transport.requests[0][1]).query)
        assert parsed_query["order_id"] == ["broker-000001"]
        assert set(parsed_query) == {"order_id", "page_size", "direction"}
