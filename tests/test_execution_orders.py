# mypy: ignore-errors
"""Unit tests for the execution order domain contracts and state machines."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest

from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.orders import (
    BROKER_ORDER_STATUS_TRANSITIONS,
    ORDER_STATUS_TRANSITIONS,
    TERMINAL_BROKER_ORDER_STATUSES,
    TERMINAL_ORDER_STATUSES,
    BrokerOrder,
    BrokerOrderStatus,
    ClientOrderId,
    Fill,
    InvalidBrokerOrderTransitionError,
    InvalidOrderTransitionError,
    OrderIntent,
    OrderIntentType,
    OrderQuantity,
    OrderSide,
    OrderStatus,
    Price,
    PriceCollar,
    Symbol,
    UsdAmount,
    assert_broker_order_transition,
    assert_order_transition,
    broker_order_transition_allowed,
    order_transition_allowed,
)


def _timestamp(text: str) -> UtcTimestamp:
    return UtcTimestamp.from_isoformat(text)


def _compose_id(**overrides: object) -> ClientOrderId:
    defaults: dict[str, object] = {
        "strategy": "seven-lens",
        "trading_date": TradingDate.from_isoformat("2026-08-17"),
        "window": "open",
        "target_version": 3,
        "symbol": Symbol("AAPL"),
        "side": OrderSide.BUY,
    }
    defaults.update(overrides)
    return ClientOrderId.compose(
        strategy=cast("str", defaults["strategy"]),
        trading_date=cast("TradingDate", defaults["trading_date"]),
        window=cast("str", defaults["window"]),
        target_version=cast("int", defaults["target_version"]),
        symbol=cast("Symbol", defaults["symbol"]),
        side=cast("OrderSide", defaults["side"]),
    )


def _collar(reference_cents: int = 10_000, bps: int = 100) -> PriceCollar:
    return PriceCollar(reference=Price.from_cents(reference_cents), offset_bps=bps)


def _intent(
    *,
    target_version: int = 1,
    symbol: str = "AAPL",
    side: OrderSide = OrderSide.BUY,
    quantity: int = 10,
    limit_cents: int = 10_000,
) -> OrderIntent:
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=TradingDate.from_isoformat("2026-08-17"),
        window="open",
        target_version=target_version,
        symbol=Symbol(symbol),
        side=side,
        quantity=OrderQuantity(quantity),
        intent_type=OrderIntentType.REBALANCE,
        limit_price=Price.from_cents(limit_cents),
        collar=_collar(),
        earliest_submit_at=_timestamp("2026-08-17T13:35:00.000000Z"),
        cancel_at=_timestamp("2026-08-17T13:45:00.000000Z"),
        run_id=RunId.new(),
        created_at=_timestamp("2026-08-17T13:30:00.000000Z"),
    )


class TestSymbol:
    def test_accepts_canonical_tickers(self) -> None:
        for value in ("A", "AAPL", "BRK.B", "BF-B", "ABC123"):
            assert Symbol(value).value == value

    def test_rejects_non_canonical_tickers(self) -> None:
        for value in ("aapl", "", "1ABC", "TOOLONGTICKER", "AA PL", "AAPL!", None, 123):
            with pytest.raises(ValueError, match="symbol must be"):
                Symbol(value)  # type: ignore[arg-type]


class TestQuantity:
    def test_accepts_positive_whole_shares(self) -> None:
        assert OrderQuantity(1).value == 1
        assert OrderQuantity(10_000).value == 10_000

    def test_rejects_non_positive_or_fractional(self) -> None:
        for value in (0, -1, True, 1.5, "3", None):
            with pytest.raises(ValueError, match="quantity must be"):
                OrderQuantity(value)  # type: ignore[arg-type]


class TestPrice:
    def test_from_cents_uses_exact_two_decimals(self) -> None:
        price = Price.from_cents(1_234)
        assert price.value == Decimal("12.34")
        assert price.cents == 1_234

    def test_accepts_boundary_prices(self) -> None:
        assert Price.from_cents(1).value == Decimal("0.01")
        assert Price(Decimal("10000000.00")).value == Decimal("10000000.00")

    def test_rejects_wrong_scale_or_range(self) -> None:
        for value in (
            Decimal("12.3"),
            Decimal("12"),
            Decimal("12.345"),
            Decimal("0.00"),
            Decimal("-1.00"),
            Decimal("10000000.01"),
            12.34,
            "12.34",
            None,
        ):
            with pytest.raises(ValueError, match="price must"):
                Price(value)  # type: ignore[arg-type]

    def test_rejects_non_finite(self) -> None:
        for value in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
            with pytest.raises(ValueError, match="price must"):
                Price(value)

    def test_cents_boundary_validation(self) -> None:
        for cents in (0, -1, 1_000_000_001, True, 1.5):
            with pytest.raises(ValueError, match="cents must be"):
                Price.from_cents(cents)  # type: ignore[arg-type]


class TestUsdAmount:
    def test_zero_is_allowed_for_balances(self) -> None:
        assert UsdAmount.from_cents(0).value == Decimal("0.00")

    def test_negative_or_mis_scaled_amounts_rejected(self) -> None:
        for value in (Decimal("-0.01"), Decimal("1.5"), 1.5, "1.00", None):
            with pytest.raises(ValueError, match="amount must"):
                UsdAmount(value)  # type: ignore[arg-type]


class TestPriceCollar:
    def test_lower_limit_is_clamped_to_one_cent(self) -> None:
        collar = PriceCollar(reference=Price.from_cents(1), offset_bps=500)

        assert collar.lower_limit == Price.from_cents(1)
        assert collar.upper_limit == Price.from_cents(2)
        assert collar.contains(Price.from_cents(1))

    def test_bounds_are_floored_and_ceiled_to_cents(self) -> None:
        collar = PriceCollar(reference=Price.from_cents(3_333), offset_bps=7)
        assert collar.lower_limit.value == Decimal("33.30")
        assert collar.upper_limit.value == Decimal("33.36")

    def test_symmetric_bounds_at_one_percent(self) -> None:
        collar = _collar(reference_cents=10_000, bps=100)
        assert collar.lower_limit.value == Decimal("99.00")
        assert collar.upper_limit.value == Decimal("101.00")
        assert collar.contains(Price.from_cents(9_900))
        assert collar.contains(Price.from_cents(10_100))
        assert not collar.contains(Price.from_cents(9_899))
        assert not collar.contains(Price.from_cents(10_101))

    def test_offset_must_stay_in_closed_provisional_range(self) -> None:
        assert PriceCollar(reference=Price.from_cents(100), offset_bps=1)
        assert PriceCollar(reference=Price.from_cents(100), offset_bps=500)
        for bps in (0, 501, -1, True, 1.0, "100", None):
            with pytest.raises(ValueError, match="collar offset must"):
                PriceCollar(reference=Price.from_cents(100), offset_bps=bps)  # type: ignore[arg-type]

    def test_contains_requires_a_price(self) -> None:
        with pytest.raises(ValueError, match="collar containment"):
            _collar().contains("100.00")  # type: ignore[arg-type]


class TestClientOrderId:
    def test_composition_is_deterministic(self) -> None:
        first = ClientOrderId.compose(
            strategy="seven-lens",
            trading_date=TradingDate.from_isoformat("2026-08-17"),
            window="open",
            target_version=3,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
        )
        second = ClientOrderId.compose(
            strategy="seven-lens",
            trading_date=TradingDate.from_isoformat("2026-08-17"),
            window="open",
            target_version=3,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
        )
        assert first == second
        assert first.value == "slv1-seven-lens-2026-08-17-open-t3-AAPL-buy"

    def test_each_component_changes_the_identity(self) -> None:
        original = _compose_id()
        variants = (
            _compose_id(window="close"),
            _compose_id(target_version=4),
            _compose_id(symbol=Symbol("MSFT")),
            _compose_id(side=OrderSide.SELL),
        )
        for variant in variants:
            assert variant != original

    def test_compose_rejects_non_canonical_components(self) -> None:
        for overrides in (
            {"strategy": "SevenLens"},
            {"strategy": ""},
            {"window": "Open"},
            {"target_version": 0},
            {"target_version": True},
            {"target_version": 10_000_000_001},
        ):
            with pytest.raises(ValueError):
                _compose_id(**overrides)

    def test_overlong_composition_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="canonical slv1 composition"):
            ClientOrderId.compose(
                strategy="s" * 32,
                trading_date=TradingDate.from_isoformat("2026-08-17"),
                window="w" * 64,
                target_version=10_000_000_000,
                symbol=Symbol("ABCDEFGHIT"),
                side=OrderSide.SELL,
            )

    def test_from_string_rejects_foreign_formats(self) -> None:
        for value in (
            "",
            "slv2-seven-lens-2026-08-17-open-t3-AAPL-buy",
            "slv1-seven-lens-2026-08-17-open-t0-AAPL-buy",
            "slv1-seven-lens-2026-8-17-open-t3-AAPL-buy",
            "slv1-seven-lens-2026-08-17-open-t3-aapl-buy",
            "slv1-seven-lens-2026-08-17-open-t3-AAPL-BUY",
            "x" * 129,
            None,
            123,
        ):
            with pytest.raises(ValueError, match="client order id"):
                ClientOrderId(value)  # type: ignore[arg-type]


class TestOrderStateMachine:
    def test_closed_map_matches_the_roadmap_lifecycle(self) -> None:
        assert ORDER_STATUS_TRANSITIONS[OrderStatus.CREATED] == frozenset(
            {OrderStatus.RISK_APPROVED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
        )
        assert ORDER_STATUS_TRANSITIONS[OrderStatus.SUBMITTING] == frozenset(
            {
                OrderStatus.ACKNOWLEDGED,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED,
                OrderStatus.REJECTED,
                OrderStatus.EXPIRED,
                OrderStatus.UNKNOWN,
                OrderStatus.CANCEL_PENDING,
                OrderStatus.CANCELED,
                OrderStatus.REVIEW_REQUIRED,
            }
        )
        assert ORDER_STATUS_TRANSITIONS[OrderStatus.UNKNOWN] == frozenset(
            {
                OrderStatus.ACKNOWLEDGED,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED,
                OrderStatus.CANCEL_PENDING,
                OrderStatus.CANCELED,
                OrderStatus.REJECTED,
                OrderStatus.EXPIRED,
                OrderStatus.REVIEW_REQUIRED,
            }
        )

    def test_every_status_has_a_closed_target_set(self) -> None:
        assert set(ORDER_STATUS_TRANSITIONS) == set(OrderStatus)
        for targets in ORDER_STATUS_TRANSITIONS.values():
            assert all(type(target) is OrderStatus for target in targets)

    def test_terminal_statuses_only_exit_to_reconciliation_review(self) -> None:
        assert (
            frozenset(
                {
                    OrderStatus.FILLED,
                    OrderStatus.CANCELED,
                    OrderStatus.REJECTED,
                    OrderStatus.EXPIRED,
                }
            )
            == TERMINAL_ORDER_STATUSES
        )
        for status in TERMINAL_ORDER_STATUSES:
            assert ORDER_STATUS_TRANSITIONS[status] == frozenset({OrderStatus.REVIEW_REQUIRED})

    def test_full_matrix_is_explicitly_decided(self) -> None:
        for current in OrderStatus:
            for target in OrderStatus:
                assert order_transition_allowed(current, target) == (
                    target in ORDER_STATUS_TRANSITIONS[current]
                )

    def test_non_exact_enum_inputs_fail_closed(self) -> None:
        pairs: list[tuple[object, object]] = [
            ("CREATED", OrderStatus.RISK_APPROVED),
            (OrderStatus.CREATED, "RISK_APPROVED"),
            (None, OrderStatus.FILLED),
        ]
        for current, target in pairs:
            with pytest.raises(ValueError, match="exact OrderStatus"):
                order_transition_allowed(current, target)  # type: ignore[arg-type]

    def test_assertion_raises_typed_error(self) -> None:
        with pytest.raises(InvalidOrderTransitionError, match="CREATED -> FILLED"):
            assert_order_transition(OrderStatus.CREATED, OrderStatus.FILLED)
        assert_order_transition(OrderStatus.CREATED, OrderStatus.RISK_APPROVED)


class TestOrderIntent:
    def test_create_builds_deterministic_client_order_id(self) -> None:
        intent = _intent()
        assert intent.status is OrderStatus.CREATED
        assert intent.client_order_id.value == "slv1-seven-lens-2026-08-17-open-t1-AAPL-buy"

    def test_limit_price_must_stay_inside_the_collar(self) -> None:
        with pytest.raises(ValueError, match="inside the price collar"):
            _intent(limit_cents=10_201)

    def test_cancel_deadline_must_follow_earliest_submit(self) -> None:
        intent = _intent()
        with pytest.raises(ValueError, match="cancel_at must be after"):
            replace(intent, cancel_at=intent.earliest_submit_at)

    def test_client_order_id_must_match_its_components(self) -> None:
        intent = _intent()
        forged = ClientOrderId.compose(
            strategy="other-lens",
            trading_date=intent.trading_date,
            window="close",
            target_version=99,
            symbol=intent.symbol,
            side=intent.side,
        )
        with pytest.raises(ValueError, match="composition components"):
            replace(intent, client_order_id=forged)

    def test_transition_returns_new_frozen_instance(self) -> None:
        intent = _intent()
        approved = intent.transition_to(OrderStatus.RISK_APPROVED)
        assert approved.status is OrderStatus.RISK_APPROVED
        assert intent.status is OrderStatus.CREATED
        assert approved.intent_id == intent.intent_id
        assert approved.client_order_id == intent.client_order_id

    def test_illegal_transition_preserves_original(self) -> None:
        intent = _intent()
        with pytest.raises(InvalidOrderTransitionError):
            intent.transition_to(OrderStatus.FILLED)
        assert intent.status is OrderStatus.CREATED

    def test_happy_path_reaches_filled(self) -> None:
        intent = _intent()
        path = (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
        )
        for status in path:
            intent = intent.transition_to(status)
        assert intent.status is OrderStatus.FILLED

    def test_timeout_resolution_goes_through_unknown(self) -> None:
        intent = _intent()
        submitting = (
            intent.transition_to(OrderStatus.RISK_APPROVED)
            .transition_to(OrderStatus.OUTBOX_PENDING)
            .transition_to(OrderStatus.SUBMITTING)
        )
        unknown = submitting.transition_to(OrderStatus.UNKNOWN)
        assert unknown.transition_to(OrderStatus.CANCELED).status is OrderStatus.CANCELED

    def test_unknown_can_never_return_to_submitting(self) -> None:
        with pytest.raises(InvalidOrderTransitionError):
            assert_order_transition(OrderStatus.UNKNOWN, OrderStatus.SUBMITTING)


class TestBrokerOrderMirror:
    def _mirror(self, **overrides: object) -> BrokerOrder:
        fields: dict[str, object] = {
            "broker_order_id": "broker-1",
            "client_order_id": _intent().client_order_id,
            "symbol": Symbol("AAPL"),
            "side": OrderSide.BUY,
            "quantity": OrderQuantity(10),
            "filled_quantity": 0,
            "limit_price": Price.from_cents(10_000),
            "status": BrokerOrderStatus.ACCEPTED,
            "submitted_at": _timestamp("2026-08-17T13:35:01.000000Z"),
            "updated_at": _timestamp("2026-08-17T13:35:01.000000Z"),
        }
        fields.update(overrides)
        return BrokerOrder(**fields)  # type: ignore[arg-type]

    def test_valid_mirror_and_openness(self) -> None:
        mirror = self._mirror()
        assert mirror.is_open
        assert not self._mirror(status=BrokerOrderStatus.FILLED).is_open
        assert not self._mirror(status=BrokerOrderStatus.CANCELED).is_open

    def test_filled_quantity_cannot_exceed_order(self) -> None:
        with pytest.raises(ValueError, match="filled_quantity"):
            self._mirror(filled_quantity=11)
        with pytest.raises(ValueError, match="filled_quantity"):
            self._mirror(filled_quantity=-1)

    def test_broker_order_id_is_bounded_text(self) -> None:
        with pytest.raises(ValueError, match="broker_order_id"):
            self._mirror(broker_order_id="")
        with pytest.raises(ValueError, match="broker_order_id"):
            self._mirror(broker_order_id="x" * 101)


class TestBrokerOrderStateMachine:
    def test_terminal_broker_statuses_have_no_exits(self) -> None:
        assert (
            frozenset(
                {
                    BrokerOrderStatus.FILLED,
                    BrokerOrderStatus.CANCELED,
                    BrokerOrderStatus.EXPIRED,
                    BrokerOrderStatus.REJECTED,
                }
            )
            == TERMINAL_BROKER_ORDER_STATUSES
        )
        for status in TERMINAL_BROKER_ORDER_STATUSES:
            assert BROKER_ORDER_STATUS_TRANSITIONS[status] == frozenset()

    def test_filled_can_never_become_canceled_or_rejected(self) -> None:
        for target in (BrokerOrderStatus.CANCELED, BrokerOrderStatus.REJECTED):
            with pytest.raises(InvalidBrokerOrderTransitionError):
                assert_broker_order_transition(BrokerOrderStatus.FILLED, target)

    def test_full_matrix_is_explicitly_decided(self) -> None:
        for current in BrokerOrderStatus:
            for target in BrokerOrderStatus:
                assert broker_order_transition_allowed(current, target) == (
                    target in BROKER_ORDER_STATUS_TRANSITIONS[current]
                )

    def test_non_exact_enum_inputs_fail_closed(self) -> None:
        with pytest.raises(ValueError, match="exact BrokerOrderStatus"):
            broker_order_transition_allowed("ACCEPTED", BrokerOrderStatus.FILLED)  # type: ignore[arg-type]


class TestFill:
    def test_valid_fill(self) -> None:
        fill = Fill(
            execution_id="exec-1",
            broker_order_id="broker-1",
            quantity=OrderQuantity(5),
            price=Price.from_cents(10_000),
            occurred_at=_timestamp("2026-08-17T13:35:02.000000Z"),
        )
        assert fill.execution_id == "exec-1"

    def _fill(self, execution_id: str = "exec-1", broker_order_id: str = "broker-1") -> Fill:
        return Fill(
            execution_id=execution_id,
            broker_order_id=broker_order_id,
            quantity=OrderQuantity(5),
            price=Price.from_cents(10_000),
            occurred_at=_timestamp("2026-08-17T13:35:02.000000Z"),
        )

    def test_bounded_identity_text(self) -> None:
        for execution_id, broker_order_id in (
            ("", "broker-1"),
            ("x" * 101, "broker-1"),
            ("exec-1", ""),
            ("exec-1", "y" * 101),
        ):
            with pytest.raises(ValueError):
                self._fill(execution_id=execution_id, broker_order_id=broker_order_id)


def test_domain_clock_interop_rejects_naive_datetimes() -> None:
    naive = datetime(2026, 8, 17, 13, 35, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        UtcTimestamp(naive)
    assert UtcTimestamp(datetime(2026, 8, 17, 13, 35, 0, tzinfo=UTC)).value.tzinfo is UTC


def test_intent_ids_are_unique_per_creation() -> None:
    assert _intent().intent_id != _intent().intent_id
    assert _intent().intent_id.int != 0
    assert uuid4().int != 0
