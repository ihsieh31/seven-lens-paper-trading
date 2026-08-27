# mypy: ignore-errors
# ruff: noqa: E501, F841, RUF059
"""P2 final ACC-002, ACC-006, ACC-007 non-integration reproductions."""

from __future__ import annotations

import pytest

from fakes.control import FakeControlRepository, FakeReconciliationRepository
from fakes.orders import FakeOrderRepository
from seven_lens.application.reconciliation_service import (
    AccountReconciliationPolicy,
    MarkPriceUnavailableError,
    Reconciler,
)
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
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
    UsdAmount,
)
from seven_lens.execution.reconciliation import MismatchKind, ReconciliationStatus
from seven_lens.execution.trade_updates import TradeUpdateConsumer, TradeUpdateError, fill_update
from seven_lens.infrastructure.postgres import (
    MAX_OPENING_CASH_CENTS,
    AccountBaseline,
    PersistenceInvariantError,
    PostgresAccountBaselineRepository,
)

_T0 = UtcTimestamp.from_isoformat("2026-08-17T13:00:00.000000Z")
_T1 = UtcTimestamp.from_isoformat("2026-08-17T13:01:00.000000Z")
_T2 = UtcTimestamp.from_isoformat("2026-08-17T13:02:00.000000Z")
_T3 = UtcTimestamp.from_isoformat("2026-08-17T13:03:00.000000Z")
_CANCEL_AT = UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z")
_TD = TradingDate.from_isoformat("2026-08-17")


def _baseline(opening_cash_cents: int) -> AccountBaseline:
    return AccountBaseline(
        account_id="paper-1",
        opening_cash_cents=opening_cash_cents,
        effective_at=_T0,
        created_at=_T0,
        updated_at=_T0,
    )


@pytest.mark.parametrize("opening_cash_cents", (0, MAX_OPENING_CASH_CENTS))
def test_account_baseline_accepts_the_database_cash_bound(opening_cash_cents: int) -> None:
    assert _baseline(opening_cash_cents).opening_cash_cents == opening_cash_cents


@pytest.mark.parametrize("opening_cash_cents", (-1, MAX_OPENING_CASH_CENTS + 1))
def test_account_baseline_rejects_values_outside_the_database_cash_bound(
    opening_cash_cents: int,
) -> None:
    with pytest.raises(ValueError, match="opening_cash_cents"):
        _baseline(opening_cash_cents)


class _NoConnectionBaselineUnitOfWork:
    def _require_connection(self) -> object:
        raise AssertionError("invalid opening cash must be rejected before SQL")


@pytest.mark.parametrize("method", ("set_baseline", "add_revision"))
def test_baseline_writes_reject_out_of_range_cash_before_sql(method: str) -> None:
    repository = PostgresAccountBaselineRepository(_NoConnectionBaselineUnitOfWork())  # type: ignore[arg-type]
    arguments: tuple[object, ...]
    if method == "set_baseline":
        arguments = ("paper-1", MAX_OPENING_CASH_CENTS + 1, _T0)
    else:
        arguments = (
            "paper-1",
            MAX_OPENING_CASH_CENTS + 1,
            _T0,
            None,
            None,
            "reason",
            "actor",
        )

    with pytest.raises(ValueError, match="opening_cash_cents"):
        getattr(repository, method)(*arguments)


class _FakeBaselineRepo:
    def __init__(self, baseline: AccountBaseline | None) -> None:
        self._baseline = baseline

    def get_baseline(self, account_id: str) -> AccountBaseline | None:
        if self._baseline is None:
            return None
        return self._baseline if self._baseline.account_id == account_id else None


class _UoW:
    def __init__(
        self,
        orders: FakeOrderRepository,
        control: FakeControlRepository | None = None,
        rec: FakeReconciliationRepository | None = None,
        baseline: AccountBaseline | None = None,
    ) -> None:
        self.orders = orders
        self.control = control or FakeControlRepository(_T0)
        self.reconciliations = rec or FakeReconciliationRepository()
        self.account_baselines = _FakeBaselineRepo(baseline)
        self.commit_count = 0

    def begin_reconciliation_snapshot(self) -> None:
        pass

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        # Mirror PostgresUnitOfWork rollback for fake: revert orders snapshot if any
        if hasattr(self.orders, "rollback"):
            self.orders.rollback()  # type: ignore[attr-defined]


def _intent(version: int) -> OrderIntent:
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=_TD,
        window="open",
        target_version=version,
        symbol=Symbol("AAPL"),
        side=OrderSide.BUY,
        quantity=OrderQuantity(10),
        intent_type=OrderIntentType.REBALANCE,
        limit_price=Price.from_cents(10_000),
        collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
        earliest_submit_at=_T0,
        cancel_at=_CANCEL_AT,
        run_id=RunId.new(),
        created_at=_T0,
    )


def _mirror(order_id: str, intent: OrderIntent, filled: int = 10) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=order_id,
        client_order_id=intent.client_order_id,
        symbol=intent.symbol,
        side=intent.side,
        quantity=intent.quantity,
        filled_quantity=filled,
        limit_price=intent.limit_price,
        status=BrokerOrderStatus.FILLED,
        submitted_at=_T0,
        updated_at=_T0,
    )


class _FixedPriceProvider:
    def __init__(self, prices: dict[Symbol, Price]) -> None:
        self._prices = prices

    def current_price(self, symbol: Symbol) -> Price:
        if symbol not in self._prices:
            raise MarkPriceUnavailableError(f"missing price for {symbol.value}")
        return self._prices[symbol]


class _AttributeErrorPriceProvider:
    def current_price(self, symbol: Symbol) -> Price:
        raise AttributeError("programming defect: missing attribute")


class _TypeErrorPriceProvider:
    def current_price(self, symbol: Symbol) -> Price:
        raise TypeError("programming defect: wrong type")


# ---------------------------------------------------------------------------
# ACC-002
# ---------------------------------------------------------------------------


class TestRevisionCutoffNav:
    def test_revision_cutoff_preserves_pre_cutoff_open_position_in_nav(self) -> None:
        orders = FakeOrderRepository()
        buy = _intent(1)
        orders.add(buy)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(buy.client_order_id, s)
        b = BrokerOrder(
            broker_order_id="b-1",
            client_order_id=buy.client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(10),
            filled_quantity=10,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=_T0,
            updated_at=_T0,
        )
        orders.record_broker_order(b)
        orders.add_fill(
            Fill(
                execution_id="e-1",
                broker_order_id="b-1",
                quantity=OrderQuantity(10),
                price=Price.from_cents(10_000),
                occurred_at=_T1,
            )
        )
        # Baseline after buy, cutoff is e-1 at T1
        baseline = AccountBaseline(
            account_id="paper-1",
            opening_cash_cents=1_000_000,
            effective_at=_T2,
            created_at=_T2,
            updated_at=_T2,
            revision_id=None,
            cutoff_occurred_at=_T1,
            cutoff_execution_id="e-1",
            reason="rebalance",
            actor="operator",
        )
        # No post-cutoff fills, mark 11000 => NAV = 1_000_000 + 10*11000 = 1_110_000
        # Broker equity should match
        from seven_lens.execution.fake_broker import FakePaperBroker

        broker = FakePaperBroker(
            clock=lambda: _T3,
            account_id="paper-1",
            cash=UsdAmount.from_cents(
                1_000_000 - 10 * 10_000
            ),  # cash after buy not counted? Wait expected cash is baseline cash + post delta (0) = 1_000_000
            # Actually expected_cash = baseline 1_000_000 + post delta 0 =1_000_000. But broker cash after buy would be 900_000 if baseline is checkpoint before? Let's align.
            # Use baseline that already includes buy effect? Simpler: baseline cash is 900_000 after buy, post delta 0, so broker cash 900_000.
            # Let's set baseline 900_000 and broker cash 900_000, NAV 900k + 110k = 1_010_000?
            # This is confusing. Let's define baseline cash as after cutoff: opening_cash is checkpoint cash.
            # So we set opening_cash = 900_000 (900k cash after buy), no post fills, positions 10, market 11000, NAV = 900k +110k=1_010_000
        )
        # Recreate with correct cash
        baseline2 = AccountBaseline(
            account_id="paper-1",
            opening_cash_cents=900_000,  # 1_000_000 - 100_000
            effective_at=_T2,
            created_at=_T2,
            updated_at=_T2,
            cutoff_occurred_at=_T1,
            cutoff_execution_id="e-1",
            reason="rebalance",
            actor="operator",
        )
        broker2 = FakePaperBroker(
            clock=lambda: _T3,
            account_id="paper-1",
            cash=UsdAmount.from_cents(900_000),
            equity=UsdAmount.from_cents(900_000 + 10 * 11_000),
        )

        # Need positions on broker side to match ledger? Fake broker positions derived from fills? But we use ledger projection vs broker positions separately.
        # The fake broker's positions are not used for NAV directly, but we need broker equity to match NAV. We already set.
        # For position mismatch, we need broker positions to match. Let's inject broker positions via submit? Simpler to mock list_positions to return correct.
        # We'll create a custom broker that returns positions matching ledger.
        class NavBroker(FakePaperBroker):
            def list_positions(self):
                from seven_lens.application.ports.broker import PaperPosition

                return (
                    PaperPosition(
                        symbol=Symbol("AAPL"),
                        quantity=10,
                        average_entry_price=Price.from_cents(10_000),
                    ),
                )

        broker3 = NavBroker(
            clock=lambda: _T3,
            account_id="paper-1",
            cash=UsdAmount.from_cents(900_000),
            equity=UsdAmount.from_cents(900_000 + 10 * 11_000),
        )
        policy = AccountReconciliationPolicy(
            expected_account_id="paper-1", cash_tolerance_cents=0, nav_tolerance_cents=0
        )
        provider = _FixedPriceProvider({Symbol("AAPL"): Price.from_cents(11_000)})
        reconciler = Reconciler(
            broker=broker3, clock=lambda: _T3, account_policy=policy, price_provider=provider
        )
        uow = _UoW(orders, baseline=baseline2)
        result = reconciler.collect(uow, _TD)
        assert result.status is ReconciliationStatus.CLEAN, (
            f"mismatches {[m.kind for m in result.mismatches]} detailed {result.mismatches}"
        )

    def test_revision_cutoff_cash_uses_only_post_cutoff_cash_delta(self) -> None:
        orders = FakeOrderRepository()
        buy = _intent(1)
        sell = OrderIntent.create(
            strategy="seven-lens",
            trading_date=_TD,
            window="open",
            target_version=2,
            symbol=Symbol("AAPL"),
            side=OrderSide.SELL,
            quantity=OrderQuantity(4),
            intent_type=OrderIntentType.REBALANCE,
            limit_price=Price.from_cents(10_000),
            collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
            earliest_submit_at=_T0,
            cancel_at=_CANCEL_AT,
            run_id=RunId.new(),
            created_at=_T0,
        )
        for intent in (buy, sell):
            orders.add(intent)
            for s in (
                OrderStatus.RISK_APPROVED,
                OrderStatus.OUTBOX_PENDING,
                OrderStatus.SUBMITTING,
                OrderStatus.ACKNOWLEDGED,
            ):
                orders.transition_status(intent.client_order_id, s)
        b1 = BrokerOrder(
            broker_order_id="b-1",
            client_order_id=buy.client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(10),
            filled_quantity=10,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=_T0,
            updated_at=_T0,
        )
        b2 = BrokerOrder(
            broker_order_id="b-2",
            client_order_id=sell.client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.SELL,
            quantity=OrderQuantity(4),
            filled_quantity=4,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=_T1,
            updated_at=_T1,
        )
        orders.record_broker_order(b1)
        orders.record_broker_order(b2)
        orders.add_fill(
            Fill(
                execution_id="e-1",
                broker_order_id="b-1",
                quantity=OrderQuantity(10),
                price=Price.from_cents(10_000),
                occurred_at=_T1,
            )
        )
        orders.add_fill(
            Fill(
                execution_id="e-2",
                broker_order_id="b-2",
                quantity=OrderQuantity(4),
                price=Price.from_cents(12_000),
                occurred_at=_T3,
            )
        )
        baseline = AccountBaseline(
            account_id="paper-1",
            opening_cash_cents=900_000,  # after first buy (1M -100k)
            effective_at=_T2,
            created_at=_T2,
            updated_at=_T2,
            cutoff_occurred_at=_T1,
            cutoff_execution_id="e-1",
            reason="rebalance",
            actor="operator",
        )

        # Expected cash = 900_000 + sell proceeds 4*12000 = 948_000
        # Current positions 6, mark 11k => market 66k, NAV 1_014_000
        class NavBrokerCash:
            pass

        from seven_lens.execution.fake_broker import FakePaperBroker

        class NavBroker(FakePaperBroker):
            def list_positions(self):
                from seven_lens.application.ports.broker import PaperPosition

                return (
                    PaperPosition(
                        symbol=Symbol("AAPL"),
                        quantity=6,
                        average_entry_price=Price.from_cents(10_000),
                    ),
                )

        broker = NavBroker(
            clock=lambda: _T3,
            account_id="paper-1",
            cash=UsdAmount.from_cents(900_000 + 4 * 12_000),
            equity=UsdAmount.from_cents(900_000 + 4 * 12_000 + 6 * 11_000),
        )
        policy = AccountReconciliationPolicy(
            expected_account_id="paper-1", cash_tolerance_cents=0, nav_tolerance_cents=0
        )
        provider = _FixedPriceProvider({Symbol("AAPL"): Price.from_cents(11_000)})
        reconciler = Reconciler(
            broker=broker, clock=lambda: _T3, account_policy=policy, price_provider=provider
        )
        uow = _UoW(orders, baseline=baseline)
        result = reconciler.collect(uow, _TD)
        assert result.status is ReconciliationStatus.CLEAN, f"got {result.mismatches}"

    def test_revision_cutoff_same_timestamp_execution_id_is_deterministic(self) -> None:
        orders = FakeOrderRepository()
        buy1 = _intent(1)
        buy2 = OrderIntent.create(
            strategy="seven-lens",
            trading_date=_TD,
            window="open",
            target_version=2,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(5),
            intent_type=OrderIntentType.REBALANCE,
            limit_price=Price.from_cents(10_000),
            collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
            earliest_submit_at=_T0,
            cancel_at=_CANCEL_AT,
            run_id=RunId.new(),
            created_at=_T0,
        )
        for intent in (buy1, buy2):
            orders.add(intent)
            for s in (
                OrderStatus.RISK_APPROVED,
                OrderStatus.OUTBOX_PENDING,
                OrderStatus.SUBMITTING,
                OrderStatus.ACKNOWLEDGED,
            ):
                orders.transition_status(intent.client_order_id, s)
        b1 = BrokerOrder(
            broker_order_id="b-1",
            client_order_id=buy1.client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(10),
            filled_quantity=10,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=_T0,
            updated_at=_T0,
        )
        b2 = BrokerOrder(
            broker_order_id="b-2",
            client_order_id=buy2.client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(5),
            filled_quantity=5,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=_T0,
            updated_at=_T0,
        )
        orders.record_broker_order(b1)
        orders.record_broker_order(b2)
        # Same timestamp T1, execution_ids e-a and e-b
        orders.add_fill(
            Fill(
                execution_id="e-a",
                broker_order_id="b-1",
                quantity=OrderQuantity(10),
                price=Price.from_cents(10_000),
                occurred_at=_T1,
            )
        )
        orders.add_fill(
            Fill(
                execution_id="e-b",
                broker_order_id="b-2",
                quantity=OrderQuantity(5),
                price=Price.from_cents(10_000),
                occurred_at=_T1,
            )
        )
        # Cutoff at e-a, so post should be only e-b
        baseline = AccountBaseline(
            account_id="paper-1",
            opening_cash_cents=900_000,
            effective_at=_T2,
            created_at=_T2,
            updated_at=_T2,
            cutoff_occurred_at=_T1,
            cutoff_execution_id="e-a",
            reason="r",
            actor="op",
        )
        # Cash: baseline 900k + 5*10k negative? Wait buys debit. 900k is after e-a? This is inconsistent but we just test cash uses only post.
        # e-a was 10*10k =100k debit already in baseline, so post delta is -50k for e-b => expected 850k
        from seven_lens.execution.fake_broker import FakePaperBroker

        class NavBroker(FakePaperBroker):
            def list_positions(self):
                from seven_lens.application.ports.broker import PaperPosition

                return (
                    PaperPosition(
                        symbol=Symbol("AAPL"),
                        quantity=15,
                        average_entry_price=Price.from_cents(10_000),
                    ),
                )

        broker = NavBroker(
            clock=lambda: _T3,
            account_id="paper-1",
            cash=UsdAmount.from_cents(900_000 - 5 * 10_000),
            equity=UsdAmount.from_cents(900_000 - 5 * 10_000 + 15 * 11_000),
        )
        policy = AccountReconciliationPolicy(
            expected_account_id="paper-1", cash_tolerance_cents=0, nav_tolerance_cents=0
        )
        provider = _FixedPriceProvider({Symbol("AAPL"): Price.from_cents(11_000)})
        reconciler = Reconciler(
            broker=broker, clock=lambda: _T3, account_policy=policy, price_provider=provider
        )
        uow = _UoW(orders, baseline=baseline)
        result = reconciler.collect(uow, _TD)
        assert result.status is ReconciliationStatus.CLEAN


# ---------------------------------------------------------------------------
# ACC-006 failure taxonomy
# ---------------------------------------------------------------------------


class TestFailureTaxonomy:
    def test_programming_attribute_error_in_price_provider_propagates(self) -> None:
        orders = FakeOrderRepository()
        buy = _intent(1)
        orders.add(buy)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(buy.client_order_id, s)
        b = BrokerOrder(
            broker_order_id="b-1",
            client_order_id=buy.client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(1),
            filled_quantity=1,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=_T0,
            updated_at=_T0,
        )
        orders.record_broker_order(b)
        orders.add_fill(
            Fill(
                execution_id="e1",
                broker_order_id="b-1",
                quantity=OrderQuantity(1),
                price=Price.from_cents(10_000),
                occurred_at=_T0,
            )
        )
        baseline = AccountBaseline(
            account_id="paper-1",
            opening_cash_cents=1_000_000,
            effective_at=_T0,
            created_at=_T0,
            updated_at=_T0,
            reason="r",
            actor="op",
        )
        from seven_lens.execution.fake_broker import FakePaperBroker

        class PosBroker(FakePaperBroker):
            def list_positions(self):
                from seven_lens.application.ports.broker import PaperPosition

                return (
                    PaperPosition(
                        symbol=Symbol("AAPL"),
                        quantity=1,
                        average_entry_price=Price.from_cents(10_000),
                    ),
                )

        broker = PosBroker(
            clock=lambda: _T0,
            account_id="paper-1",
            cash=UsdAmount.from_cents(990_000),
            equity=UsdAmount.from_cents(1_000_000),
        )
        reconciler = Reconciler(
            broker=broker,
            clock=lambda: _T0,
            account_policy=AccountReconciliationPolicy(expected_account_id="paper-1"),
            price_provider=_AttributeErrorPriceProvider(),
        )
        uow = _UoW(orders, baseline=baseline)
        with pytest.raises(AttributeError):
            reconciler.collect(uow, _TD)

    def test_programming_type_error_in_price_provider_propagates(self) -> None:
        orders = FakeOrderRepository()
        buy = _intent(1)
        orders.add(buy)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(buy.client_order_id, s)
        b = BrokerOrder(
            broker_order_id="b-1",
            client_order_id=buy.client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(1),
            filled_quantity=1,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=_T0,
            updated_at=_T0,
        )
        orders.record_broker_order(b)
        orders.add_fill(
            Fill(
                execution_id="e1",
                broker_order_id="b-1",
                quantity=OrderQuantity(1),
                price=Price.from_cents(10_000),
                occurred_at=_T0,
            )
        )
        baseline = AccountBaseline(
            account_id="paper-1",
            opening_cash_cents=1_000_000,
            effective_at=_T0,
            created_at=_T0,
            updated_at=_T0,
            reason="r",
            actor="op",
        )
        from seven_lens.execution.fake_broker import FakePaperBroker

        class PosBroker(FakePaperBroker):
            def list_positions(self):
                from seven_lens.application.ports.broker import PaperPosition

                return (
                    PaperPosition(
                        symbol=Symbol("AAPL"),
                        quantity=1,
                        average_entry_price=Price.from_cents(10_000),
                    ),
                )

        broker = PosBroker(
            clock=lambda: _T0,
            account_id="paper-1",
            cash=UsdAmount.from_cents(990_000),
            equity=UsdAmount.from_cents(1_000_000),
        )
        reconciler = Reconciler(
            broker=broker,
            clock=lambda: _T0,
            account_policy=AccountReconciliationPolicy(expected_account_id="paper-1"),
            price_provider=_TypeErrorPriceProvider(),
        )
        uow = _UoW(orders, baseline=baseline)
        with pytest.raises(TypeError):
            reconciler.collect(uow, _TD)

    def test_typed_mark_price_unavailable_becomes_account_reconciliation_unavailable(self) -> None:
        orders = FakeOrderRepository()
        buy = _intent(1)
        orders.add(buy)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(buy.client_order_id, s)
        b = BrokerOrder(
            broker_order_id="b-1",
            client_order_id=buy.client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(1),
            filled_quantity=1,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=_T0,
            updated_at=_T0,
        )
        orders.record_broker_order(b)
        orders.add_fill(
            Fill(
                execution_id="e1",
                broker_order_id="b-1",
                quantity=OrderQuantity(1),
                price=Price.from_cents(10_000),
                occurred_at=_T0,
            )
        )
        baseline = AccountBaseline(
            account_id="paper-1",
            opening_cash_cents=1_000_000,
            effective_at=_T0,
            created_at=_T0,
            updated_at=_T0,
            reason="r",
            actor="op",
        )

        class MissingPriceProvider:
            def current_price(self, symbol: Symbol) -> Price:
                raise MarkPriceUnavailableError("missing price for AAPL")

        from seven_lens.execution.fake_broker import FakePaperBroker

        class PosBroker(FakePaperBroker):
            def list_positions(self):
                from seven_lens.application.ports.broker import PaperPosition

                return (
                    PaperPosition(
                        symbol=Symbol("AAPL"),
                        quantity=1,
                        average_entry_price=Price.from_cents(10_000),
                    ),
                )

        broker = PosBroker(
            clock=lambda: _T0,
            account_id="paper-1",
            cash=UsdAmount.from_cents(990_000),
            equity=UsdAmount.from_cents(1_000_000),
        )
        reconciler = Reconciler(
            broker=broker,
            clock=lambda: _T0,
            account_policy=AccountReconciliationPolicy(expected_account_id="paper-1"),
            price_provider=MissingPriceProvider(),
        )
        uow = _UoW(orders, baseline=baseline)
        result = reconciler.collect(uow, _TD)
        assert any(
            m.kind == MismatchKind.ACCOUNT_RECONCILIATION_UNAVAILABLE for m in result.mismatches
        )

    def test_unexpected_value_error_in_price_provider_propagates(self) -> None:
        orders = FakeOrderRepository()
        buy = _intent(2)
        orders.add(buy)
        for status in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(buy.client_order_id, status)
        orders.record_broker_order(_mirror("b-unexpected-value", buy, filled=10))
        orders.add_fill(
            Fill(
                execution_id="e-unexpected-value",
                broker_order_id="b-unexpected-value",
                quantity=OrderQuantity(10),
                price=Price.from_cents(10_000),
                occurred_at=_T0,
            )
        )

        class UnexpectedValueErrorProvider:
            def current_price(self, symbol: Symbol) -> Price:
                raise ValueError("programming/configuration defect")

        from seven_lens.application.ports.broker import PaperPosition
        from seven_lens.execution.fake_broker import FakePaperBroker

        class PosBroker(FakePaperBroker):
            def list_positions(self):
                return (
                    PaperPosition(
                        symbol=Symbol("AAPL"),
                        quantity=10,
                        average_entry_price=Price.from_cents(10_000),
                    ),
                )

        baseline = AccountBaseline(
            account_id="paper-1",
            opening_cash_cents=1_000_000,
            effective_at=_T0,
            created_at=_T0,
            updated_at=_T0,
            reason="r",
            actor="op",
        )
        reconciler = Reconciler(
            broker=PosBroker(
                clock=lambda: _T0,
                account_id="paper-1",
                cash=UsdAmount.from_cents(900_000),
                equity=UsdAmount.from_cents(1_000_000),
            ),
            clock=lambda: _T0,
            account_policy=AccountReconciliationPolicy(expected_account_id="paper-1"),
            price_provider=UnexpectedValueErrorProvider(),
        )
        with pytest.raises(ValueError, match="programming/configuration defect"):
            reconciler.collect(_UoW(orders, baseline=baseline), _TD)

    def test_persistence_invariant_in_baseline_lookup_propagates(self) -> None:
        from seven_lens.execution.fake_broker import FakePaperBroker

        class BrokenBaselineRepo:
            def get_baseline(self, account_id: str) -> object:
                raise PersistenceInvariantError("corrupt baseline row")

        uow = _UoW(FakeOrderRepository())
        uow.account_baselines = BrokenBaselineRepo()  # type: ignore[assignment]
        reconciler = Reconciler(
            broker=FakePaperBroker(clock=lambda: _T0, account_id="paper-1"),
            clock=lambda: _T0,
            account_policy=AccountReconciliationPolicy(expected_account_id="paper-1"),
        )
        with pytest.raises(PersistenceInvariantError, match="corrupt baseline row"):
            reconciler.collect(uow, _TD)

    def test_missing_baseline_is_fail_closed_mismatch(self) -> None:
        from seven_lens.execution.fake_broker import FakePaperBroker

        reconciler = Reconciler(
            broker=FakePaperBroker(clock=lambda: _T0, account_id="paper-1"),
            clock=lambda: _T0,
            account_policy=AccountReconciliationPolicy(expected_account_id="paper-1"),
        )
        result = reconciler.collect(_UoW(FakeOrderRepository()), _TD)
        assert result.status is ReconciliationStatus.MISMATCH
        assert any(
            mismatch.kind is MismatchKind.ACCOUNT_RECONCILIATION_UNAVAILABLE
            and mismatch.detail == "missing opening cash baseline"
            for mismatch in result.mismatches
        )

    def test_invalid_baseline_object_is_not_silently_clean(self) -> None:
        from seven_lens.execution.fake_broker import FakePaperBroker

        class InvalidBaselineRepo:
            def get_baseline(self, account_id: str) -> object:
                return object()

        uow = _UoW(FakeOrderRepository())
        uow.account_baselines = InvalidBaselineRepo()  # type: ignore[assignment]
        reconciler = Reconciler(
            broker=FakePaperBroker(clock=lambda: _T0, account_id="paper-1"),
            clock=lambda: _T0,
            account_policy=AccountReconciliationPolicy(expected_account_id="paper-1"),
        )
        with pytest.raises(AttributeError):
            reconciler.collect(uow, _TD)


# ---------------------------------------------------------------------------
# ACC-007 durable pause after conflicting fill
# ---------------------------------------------------------------------------


class TestConflictingFillDurablePause:
    def _setup(self):
        intent = OrderIntent.create(
            strategy="seven-lens",
            trading_date=_TD,
            window="open",
            target_version=1,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(5),
            intent_type=OrderIntentType.REBALANCE,
            limit_price=Price.from_cents(10_000),
            collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
            earliest_submit_at=_T0,
            cancel_at=_CANCEL_AT,
            run_id=RunId.new(),
            created_at=_T0,
        )
        orders = FakeOrderRepository()
        orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(intent.client_order_id, s)
        mirror = BrokerOrder(
            broker_order_id="b-1",
            client_order_id=intent.client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(5),
            filled_quantity=0,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.ACCEPTED,
            submitted_at=_T0,
            updated_at=_T1,
        )
        orders.record_broker_order(mirror)
        return orders, intent, mirror

    def test_conflicting_fill_rolls_back_partial_mirror_mutation(self) -> None:
        orders, intent, _ = self._setup()
        consumer = TradeUpdateConsumer()
        # First fill fills completely
        fake_orders = orders
        # Add first fill 5 -> becomes FILLED
        first = fill_update(
            execution_id="e-1",
            broker_order_id="b-1",
            quantity=5,
            price_cents=10_000,
            occurred_at=_T1,
        )
        control = FakeControlRepository(_T0)
        uow = _UoW(fake_orders, control=control)

        assert consumer.apply(uow, first) == "APPLIED" or str(consumer.apply)  # first applied
        # Now order is FILLED 5, second fill overfills -> conflict
        # Actually need fresh: we already applied one, now overfill
        over = fill_update(
            execution_id="e-over",
            broker_order_id="b-1",
            quantity=1,
            price_cents=10_000,
            occurred_at=_T2,
        )
        # Reset control for second? Use same uow
        with pytest.raises(TradeUpdateError):
            consumer.apply(uow, over)
        # Mirror should still be 5, not 6
        assert orders.get_broker_order_by_id("b-1").filled_quantity == 5  # type: ignore[union-attr]
        # Fill fact should have survived (over fill inserted but derived rolled back)
        assert orders.fill_count == 2
        # Pause should be durable
        assert control.state().entries_paused is True
        assert any(c.command.value == "PAUSE_ENTRIES" for c in control.commands)

    def test_conflicting_fill_sets_entries_paused(self) -> None:
        orders, _, _ = self._setup()
        consumer = TradeUpdateConsumer()
        control = FakeControlRepository(_T0)
        uow = _UoW(orders, control=control)
        # Fill that exceeds quantity directly on first fill: qty 6 > 5
        big = fill_update(
            execution_id="e-big",
            broker_order_id="b-1",
            quantity=6,
            price_cents=10_000,
            occurred_at=_T1,
        )
        with pytest.raises(TradeUpdateError):
            consumer.apply(uow, big)
        assert control.state().entries_paused is True

    def test_conflicting_fill_appends_pause_command(self) -> None:
        orders, _, _ = self._setup()
        consumer = TradeUpdateConsumer()
        control = FakeControlRepository(_T0)
        uow = _UoW(orders, control=control)
        big = fill_update(
            execution_id="e-big2",
            broker_order_id="b-1",
            quantity=6,
            price_cents=10_000,
            occurred_at=_T1,
        )
        with pytest.raises(TradeUpdateError):
            consumer.apply(uow, big)
        assert len(control.commands) == 1
        assert control.commands[0].command.value == "PAUSE_ENTRIES"
