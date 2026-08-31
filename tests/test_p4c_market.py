# mypy: ignore-errors
"""P4-C market snapshot contract and ADV boundary tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from seven_lens.clock.market_clock import (
    MarketDayKind,
    MarketSession,
    RegularSessionWindow,
)
from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.market_data.snapshots import (
    MAX_MARKET_SNAPSHOT_BYTES,
    MAX_MARKET_SNAPSHOT_ITEMS,
    MAX_MARKET_SNAPSHOT_SPLITS,
    MAX_SPREAD_BPS,
    Coverage,
    DailyBar,
    Entitlement,
    Feed,
    Freshness,
    MarketSnapshot,
    QuoteInput,
    SplitAdjustment,
    _BarProjectionAuthority,
    _canonical_market_wire_bytes,
    _daily_bar_fingerprint,
    assemble_market_snapshot,
    build_market_snapshot,
    compute_adv20,
    quote_input_from_record,
    split_adjustment_from_lineage,
    validate_quote_age,
)
from seven_lens.screening.reasons import ClosedReason
from seven_lens.securities.contracts import SecurityId, SecuritySymbol, SourceRef
from seven_lens.securities.corporate_actions import (
    CorporateActionRecord,
    CorporateActionState,
    CorporateActionType,
    SplitRatio,
    build_corporate_action_record,
)
from seven_lens.sources.adapters.alpaca import parse_iex_quote
from seven_lens.sources.adapters.records import _build_normalized_record as build_normalized_record
from seven_lens.sources.roles import P4SourceFamily

_SEC = SecurityId("11111111-1111-4111-8111-111111111111")
_SYM = SecuritySymbol("TEST")
_T0 = UtcTimestamp.from_isoformat("2026-06-01T14:00:00.000000Z")
_IEX = SourceRef("quote-1", P4SourceFamily.ALPACA_IEX_QUOTES, "a" * 64)
_BAR = SourceRef("bar-1", P4SourceFamily.ALPACA_HISTORICAL_BARS, "b" * 64)
_SPLIT = SourceRef("split-1", P4SourceFamily.ALPACA_CORPORATE_ACTIONS, "c" * 64)
_SPLIT_CONFIRMATION = SourceRef("split-confirmation", P4SourceFamily.SEC_EDGAR, "d" * 64)


def _quote(
    *,
    bid: Decimal | None = None,
    ask: Decimal | None = None,
    observed_at: UtcTimestamp | None = None,
    received_at: UtcTimestamp | None = None,
    feed: Feed = Feed.IEX,
    entitlement: Entitlement = Entitlement.IEX,
) -> QuoteInput:
    observed = observed_at if observed_at is not None else _T0
    received = received_at if received_at is not None else observed
    bid_value = bid if bid is not None else Decimal("100.00")
    ask_value = ask if ask is not None else Decimal("100.05")
    record = build_normalized_record(
        record_id="quote-test",
        family=P4SourceFamily.ALPACA_IEX_QUOTES,
        endpoint_id="latest_quote",
        schema_version=SchemaVersion("1.0.0"),
        content_hash="a" * 64,
        retrieved_at=received,
        observation_at=observed,
        payload={
            "symbol": _SYM.value,
            "bid_price": bid_value if type(bid_value) is not Decimal else str(bid_value),
            "ask_price": ask_value if type(ask_value) is not Decimal else str(ask_value),
            "timestamp": str(observed),
            "feed": "iex" if feed is Feed.IEX else "sip_delayed",
        },
        material_claim=False,
        coverage_warning="IEX feed only; not full NBBO/SIP market coverage",
    )
    if entitlement is not Entitlement.IEX:
        raise ValueError("IEX source records derive only the IEX entitlement")
    return quote_input_from_record(record)


def _split_lineage(
    *,
    security_id: SecurityId = _SEC,
    ex_date: str,
    available_at: str,
    numerator: int = 2,
    denominator: int = 1,
    event_id: str = "event-split-test",
    source_ref: SourceRef = _SPLIT,
    security_identity_hash: str = "e" * 64,
) -> tuple[CorporateActionRecord, ...]:
    """Build a split only through a legal DETECTED→BLOCKED→CONFIRMED lineage."""
    ex = TradingDate.from_isoformat(ex_date)
    available = UtcTimestamp.from_isoformat(available_at)
    detected_at = UtcTimestamp(available.value - timedelta(minutes=2))
    blocked_at = UtcTimestamp(available.value - timedelta(minutes=1))
    declared_date = min(ex.value - timedelta(days=1), detected_at.value.date())
    declared_at = UtcTimestamp(datetime.combine(declared_date, datetime.min.time(), tzinfo=UTC))
    ratio = SplitRatio.from_fraction(numerator=numerator, denominator=denominator)
    common = {
        "event_id": event_id,
        "security_id": security_id,
        "security_identity_hash": security_identity_hash,
        "action_type": CorporateActionType.FORWARD_SPLIT,
        "ratio": ratio,
        "declared_at": declared_at,
        "ex_date": ex,
        "effective_date": ex,
        "schema_version": SchemaVersion("1.0.0"),
    }
    detected = build_corporate_action_record(
        **common,
        available_at=detected_at,
        state=CorporateActionState.DETECTED,
        source_refs=(source_ref,),
    )
    blocked = build_corporate_action_record(
        **common,
        available_at=blocked_at,
        state=CorporateActionState.ENTRY_BLOCKED,
        source_refs=(source_ref,),
    )
    confirmed = build_corporate_action_record(
        **common,
        available_at=available,
        state=CorporateActionState.CONFIRMED,
        source_refs=(source_ref, _SPLIT_CONFIRMATION),
    )
    return (detected, blocked, confirmed)


def _split(**values: object) -> SplitAdjustment:
    return split_adjustment_from_lineage(_split_lineage(**values))


def _session(date_text: str, kind: MarketDayKind = MarketDayKind.REGULAR) -> MarketSession:
    date = TradingDate.from_isoformat(date_text)
    if kind is MarketDayKind.CLOSED:
        return MarketSession(trading_date=date, day_kind=kind, regular_session=None)
    return MarketSession(
        trading_date=date,
        day_kind=kind,
        regular_session=RegularSessionWindow(
            opens_at=UtcTimestamp(
                datetime.combine(date.value, datetime.min.time(), tzinfo=UTC)
                + timedelta(hours=13, minutes=30)
            ),
            closes_at=UtcTimestamp(
                datetime.combine(date.value, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
            ),
        ),
    )


def _bar(
    date_text: str,
    close: str,
    volume: int = 1_000_000,
    *,
    security_id: SecurityId = _SEC,
) -> DailyBar:
    values: dict[str, object] = {
        "security_id": security_id,
        "trading_date": TradingDate.from_isoformat(date_text),
        "close": Decimal(close),
        "volume": volume,
        "source_ref": _BAR,
        "feed": Feed.SIP_DELAYED,
        "available_at": UtcTimestamp.from_isoformat(f"{date_text}T21:00:00.000000Z"),
    }
    provisional = object.__new__(DailyBar)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    authority = _BarProjectionAuthority(
        _daily_bar_fingerprint(provisional), _BAR.record_hash, "e" * 64
    )
    return DailyBar(**values, _authority=authority)  # type: ignore[arg-type]


def _adv_sessions(count: int = 25, half_days: set[str] | None = None) -> tuple[MarketSession, ...]:
    half_days = half_days or set()
    # Generate sessions ending on the as-of trading date (2026-06-01) so the
    # assembler finds a regular session for the snapshot's as_of.
    end = datetime(2026, 6, 1, tzinfo=UTC)
    sessions: list[MarketSession] = []
    current = end
    while len(sessions) < count:
        if current.weekday() < 5:
            text = current.strftime("%Y-%m-%d")
            kind = MarketDayKind.HALF_DAY if text in half_days else MarketDayKind.REGULAR
            sessions.append(_session(text, kind))
        current -= timedelta(days=1)
    return tuple(reversed(sessions))


def _bars_for(sessions: tuple[MarketSession, ...]) -> tuple[DailyBar, ...]:
    return tuple(
        _bar(s.trading_date.value.isoformat(), "100.00")
        for s in sessions
        if s.trading_date.value < _T0.value.date()
    )


def _assemble(**overrides: object) -> MarketSnapshot:
    """Assemble a legal snapshot; overrides reach the raw inputs."""
    sessions = _adv_sessions()
    values: dict[str, object] = {
        "security_id": _SEC,
        "symbol": _SYM,
        "as_of": _T0,
        "known_at": _T0,
        "quote": _quote(),
        "bars": _bars_for(sessions),
        "sessions": sessions,
    }
    values.update(overrides)
    return assemble_market_snapshot(**values)  # type: ignore[arg-type]


def test_assembler_derives_mid_spread_adv_and_hash() -> None:
    snapshot = _assemble()
    assert snapshot.mid == Decimal("100.025")
    assert snapshot.last == snapshot.mid
    assert snapshot.spread_bps == 4
    assert snapshot.adv20_usd is not None
    assert snapshot.freshness is Freshness.FRESH
    assert snapshot.snapshot_hash == snapshot.compute_hash()
    assert snapshot.verify_integrity()
    assert snapshot.coverage is Coverage.LIMITED_MARKET_COVERAGE


def test_quote_projection_is_derived_from_the_exact_p4a_record() -> None:
    record = parse_iex_quote(
        b'{"symbol":"TEST","bid_price":"100.00","ask_price":"100.05",'
        b'"timestamp":"2026-06-01T14:00:00.000000Z"}',
        retrieved_at=_T0,
        symbol=_SYM.value,
    )[0]
    quote = quote_input_from_record(record)
    assert quote.source_ref.record_hash == record.record_hash
    assert quote.symbol == _SYM
    assert quote.bid == Decimal("100.00")
    assert quote.ask == Decimal("100.05")

    object.__setattr__(quote, "bid", Decimal("1.00"))
    with pytest.raises(ValueError, match="not bound"):
        quote._verify_source_binding()


def test_split_adjustment_requires_a_validated_confirmed_lineage() -> None:
    with pytest.raises(ValueError, match="validated corporate-action lineage"):
        SplitAdjustment(
            security_id=_SEC,
            ex_date=TradingDate.from_isoformat("2026-05-20"),
            numerator=2,
            denominator=1,
            source_ref=_SPLIT,
            available_at=UtcTimestamp.from_isoformat("2026-05-19T12:00:00.000000Z"),
            confirmed=True,
        )

    split = _split(
        ex_date="2026-05-20",
        available_at="2026-05-19T12:00:00.000000Z",
    )
    assert split.effective_date == TradingDate.from_isoformat("2026-05-20")
    assert split.source_refs == (_SPLIT, _SPLIT_CONFIRMATION)
    object.__setattr__(split, "numerator", 3)
    with pytest.raises(ValueError, match="not bound"):
        split._verify_source_binding()


def test_split_adjustment_rejects_unconfirmed_or_tampered_lineage() -> None:
    split = _split(
        ex_date="2026-05-20",
        available_at="2026-05-19T12:00:00.000000Z",
    )
    assert split.event_id == "event-split-test"
    assert len(split.event_record_hash) == 64

    # The event-bound capability is re-checked at every consumer seam:
    # changing an immutable projection field cannot be smuggled into ADV.
    tampered = split
    object.__setattr__(tampered, "ex_date", TradingDate.from_isoformat("2026-05-21"))
    with pytest.raises(ValueError, match="not bound"):
        tampered._verify_source_binding()

    lineage = _split_lineage(
        ex_date="2026-05-20",
        available_at="2026-05-19T12:00:00.000000Z",
    )
    with pytest.raises(ValueError, match="CONFIRMED"):
        split_adjustment_from_lineage(lineage[:2])
    object.__setattr__(lineage[-1], "record_hash", "f" * 64)
    with pytest.raises(ValueError, match="hash"):
        split_adjustment_from_lineage(lineage)


def test_assembler_iex_mandatory_limited_coverage() -> None:
    snapshot = _assemble()
    assert snapshot.coverage is Coverage.LIMITED_MARKET_COVERAGE
    assert snapshot.coverage_warning is not None


def test_assembler_rejects_non_iex_quote() -> None:
    with pytest.raises(ValueError, match="IEX"):
        _assemble(quote=_quote(feed=Feed.SIP_DELAYED))


def test_assembler_rejects_entitlement_mismatch() -> None:
    with pytest.raises(ValueError, match="entitlement"):
        _assemble(quote=_quote(entitlement=Entitlement.SIP))


def test_assembler_rejects_bid_ask_inversion() -> None:
    with pytest.raises(ValueError, match="bid must not exceed ask"):
        _assemble(quote=_quote(bid=Decimal("100.10"), ask=Decimal("100.05")))


def test_assembler_rejects_zero_negative_bid() -> None:
    with pytest.raises(ValueError):
        _assemble(quote=_quote(bid=Decimal("0")))
    with pytest.raises(ValueError):
        _assemble(quote=_quote(bid=Decimal("-1")))


def test_assembler_rejects_float_bid() -> None:
    with pytest.raises(ValueError):
        _assemble(quote=_quote(bid=100.0))  # type: ignore[arg-type]


def test_assembler_quote_age_boundaries() -> None:
    # exactly 5 seconds is FRESH
    age5 = UtcTimestamp.from_isoformat("2026-06-01T13:59:55.000000Z")
    snap = _assemble(quote=_quote(observed_at=age5, received_at=age5))
    assert snap.freshness is Freshness.FRESH
    # just over 5 seconds is STALE
    age5_plus = UtcTimestamp.from_isoformat("2026-06-01T13:59:54.999999Z")
    snap = _assemble(quote=_quote(observed_at=age5_plus, received_at=age5_plus))
    assert snap.freshness is Freshness.STALE
    assert ClosedReason.QUOTE_MISSING_OR_STALE in snap.reasons


def test_assembler_future_quote_has_no_snapshot_authority() -> None:
    future = UtcTimestamp.from_isoformat("2026-06-01T14:00:00.000001Z")
    with pytest.raises(ValueError, match=r"visible|timestamp"):
        _assemble(quote=_quote(observed_at=future, received_at=future))


def test_assembler_rejects_quote_not_visible_at_known_at() -> None:
    known_at = UtcTimestamp.from_isoformat("2026-05-31T20:00:00.000000Z")
    with pytest.raises(ValueError, match="visible"):
        _assemble(known_at=known_at)


def test_assembler_out_of_order_received_has_no_snapshot_authority() -> None:
    observed = UtcTimestamp.from_isoformat("2026-06-01T14:00:00.000000Z")
    received = UtcTimestamp.from_isoformat("2026-06-01T13:59:00.000000Z")
    with pytest.raises(ValueError, match=r"retrieval|timestamp"):
        _assemble(quote=_quote(observed_at=observed, received_at=received))


def test_assembler_closed_market_day_has_no_snapshot_authority() -> None:
    saturday = UtcTimestamp.from_isoformat("2026-06-06T14:00:00.000000Z")
    sessions = (_session("2026-06-06", MarketDayKind.CLOSED),)
    with pytest.raises(ValueError, match="timestamp"):
        _assemble(
            as_of=saturday,
            known_at=saturday,
            quote=_quote(observed_at=saturday, received_at=saturday),
            sessions=sessions,
        )


def test_assembler_spread_30bps_boundary() -> None:
    bid = Decimal("100.00")
    ask = Decimal("100.30")  # spread ≈ 29.95 bps
    snap = _assemble(quote=_quote(bid=bid, ask=ask))
    assert snap.spread_bps <= MAX_SPREAD_BPS
    assert ClosedReason.SPREAD_TOO_WIDE not in snap.reasons
    # ask=100.32 gives spread ≈ 31.9 bps → over 30
    ask_wide = Decimal("100.32")
    snap = _assemble(quote=_quote(bid=bid, ask=ask_wide))
    assert snap.spread_bps > MAX_SPREAD_BPS
    assert ClosedReason.SPREAD_TOO_WIDE in snap.reasons


def test_assembler_rejects_forged_floor_spread_boundary() -> None:
    # 100.31 is above the exact 30 bps threshold even though floor() yields 30.
    snapshot = _assemble(quote=_quote(bid=Decimal("100.00"), ask=Decimal("100.31")))
    assert snapshot.spread_bps == 30
    assert ClosedReason.SPREAD_TOO_WIDE in snapshot.reasons


def test_assembler_spread_30bps_exact_rational_boundary() -> None:
    # (ask-bid)*20000/(bid+ask) == 30 exactly: inside the limit.
    snapshot = _assemble(quote=_quote(bid=Decimal("19970"), ask=Decimal("20030")))
    assert snapshot.spread_bps == 30
    assert ClosedReason.SPREAD_TOO_WIDE not in snapshot.reasons
    # One cent above the exact threshold: the floor is still 30, but the
    # exact quotient exceeds 30, so the reason must be present.
    snapshot = _assemble(quote=_quote(bid=Decimal("19970"), ask=Decimal("20031")))
    assert snapshot.spread_bps == 30
    assert ClosedReason.SPREAD_TOO_WIDE in snapshot.reasons


def test_assembler_spread_flag_survives_context_precision_rounding() -> None:
    # Keep the source adapter's bounded decimal shape while forcing a low
    # caller context; exact rational arithmetic must still flag the boundary.
    bid = Decimal("499249999999.9975")
    ask = Decimal("500750000000.0025")
    from decimal import localcontext

    with localcontext() as context:
        context.prec = 10
        snapshot = _assemble(quote=_quote(bid=bid, ask=ask))
    assert snapshot.spread_bps == 30
    assert ClosedReason.SPREAD_TOO_WIDE in snapshot.reasons


def test_assembler_adv_20_sessions_exact() -> None:
    sessions = _adv_sessions()
    snap = _assemble(sessions=sessions, bars=_bars_for(sessions))
    assert snap.adv20_usd is not None


def test_assembler_adv_rejects_missing_latest_qualifying_bar() -> None:
    sessions = _adv_sessions()
    bars = _bars_for(sessions)
    missing_latest = bars[:-1]
    assert (
        compute_adv20(
            missing_latest,
            sessions,
            cutoff=_T0,
            known_at=_T0,
        )
        is None
    )
    snapshot = _assemble(sessions=sessions, bars=missing_latest)
    assert snapshot.adv20_usd is None
    assert ClosedReason.ADV_BELOW_MINIMUM in snapshot.reasons


def test_adv_rejects_bar_known_after_cutoff() -> None:
    sessions = tuple(
        sorted((*_adv_sessions(), _session("2026-06-02")), key=lambda s: s.trading_date.value)
    )
    future = _bar("2026-06-02", "100.00")
    with pytest.raises(ValueError, match="cutoff"):
        compute_adv20((*_bars_for(sessions), future), sessions, cutoff=_T0)


def test_adv_rejects_bar_known_after_known_at() -> None:
    sessions = _adv_sessions()
    known_at = UtcTimestamp.from_isoformat("2026-05-29T20:00:00.000000Z")
    with pytest.raises(ValueError, match="known_at"):
        compute_adv20(
            _bars_for(sessions),
            sessions,
            cutoff=_T0,
            known_at=known_at,
        )


def test_assembler_adv_insufficient_is_reason() -> None:
    sessions = _adv_sessions(count=19)
    snap = _assemble(sessions=sessions, bars=_bars_for(sessions))
    assert snap.adv20_usd is None
    assert ClosedReason.ADV_BELOW_MINIMUM in snap.reasons


def test_assembler_rejects_sparse_weekday_calendar() -> None:
    sessions = _adv_sessions()
    missing_session = sessions[-10]
    sparse_sessions = tuple(session for session in sessions if session != missing_session)
    sparse_bars = tuple(
        bar for bar in _bars_for(sessions) if bar.trading_date != missing_session.trading_date
    )

    with pytest.raises(ValueError, match="every weekday explicitly"):
        _assemble(sessions=sparse_sessions, bars=sparse_bars)


def test_assembler_split_aware_adv() -> None:
    sessions = _adv_sessions()
    split = _split(
        ex_date="2026-05-20",
        available_at="2026-05-19T12:00:00.000000Z",
    )
    snap = _assemble(sessions=sessions, bars=_bars_for(sessions), split_adjustments=(split,))
    assert snap.adv20_usd is not None
    split_wire = snap.wire()["split_adjustments"][0]
    assert split_wire == {
        "security_id": _SEC.value,
        "ex_date": "2026-05-20",
        "numerator": 2,
        "denominator": 1,
        "event_id": split.event_id,
        "event_record_hash": split.event_record_hash,
        "security_identity_hash": "e" * 64,
        "action_type": "forward_split",
        "effective_date": "2026-05-20",
        "source_ref": {
            "record_id": _SPLIT.record_id,
            "family": _SPLIT.family.value,
            "record_hash": _SPLIT.record_hash,
        },
        "source_refs": [
            {
                "record_id": ref.record_id,
                "family": ref.family.value,
                "record_hash": ref.record_hash,
            }
            for ref in split.source_refs
        ],
        "available_at": "2026-05-19T12:00:00.000000Z",
        "confirmed": True,
    }
    object.__setattr__(split, "ex_date", TradingDate.from_isoformat("2026-05-21"))
    with pytest.raises(ValueError, match="not bound"):
        snap.wire()


def test_assembler_rejects_split_from_another_identity_version() -> None:
    sessions = _adv_sessions()
    split = split_adjustment_from_lineage(
        _split_lineage(
            ex_date="2026-05-20",
            available_at="2026-05-19T12:00:00.000000Z",
            security_identity_hash="f" * 64,
        )
    )
    with pytest.raises(ValueError, match="historical-bar identity"):
        _assemble(
            sessions=sessions,
            bars=_bars_for(sessions),
            split_adjustments=(split,),
        )


def test_assembler_future_split_not_applied() -> None:
    sessions = _adv_sessions()
    split = _split(
        ex_date="2026-12-01",
        available_at="2026-06-01T12:00:00.000000Z",
    )
    snap = _assemble(sessions=sessions, bars=_bars_for(sessions), split_adjustments=(split,))
    assert snap.adv20_usd is not None


def test_assembler_rejects_duplicate_bars() -> None:
    sessions = _adv_sessions()
    bars = _bars_for(sessions)
    with pytest.raises(ValueError, match="duplicate"):
        _assemble(sessions=sessions, bars=(*bars, bars[-1]))


def test_assembler_handles_no_session_for_as_of() -> None:
    # Missing calendar authority must not create a persistable snapshot.
    with pytest.raises(ValueError, match="timestamp"):
        _assemble(sessions=())


def test_snapshot_canonical_hash_stable() -> None:
    first = _assemble()
    second = _assemble()
    assert first.snapshot_hash == second.snapshot_hash
    assert first.wire() == second.wire()


def test_assembler_reasons_canonical_order() -> None:
    snap = _assemble()
    assert snap.reasons == tuple(sorted(snap.reasons, key=lambda r: list(ClosedReason).index(r)))


def test_validate_quote_age_helpers() -> None:
    at = UtcTimestamp.from_isoformat("2026-06-01T14:00:00.000000Z")
    age5 = UtcTimestamp.from_isoformat("2026-06-01T13:59:55.000000Z")
    assert validate_quote_age(as_of=at, observed_at=age5, received_at=age5) is Freshness.FRESH
    future = UtcTimestamp.from_isoformat("2026-06-01T14:00:00.000001Z")
    assert (
        validate_quote_age(as_of=at, observed_at=future, received_at=future) is Freshness.CONFLICT
    )
    received_future = UtcTimestamp.from_isoformat("2026-06-01T14:00:00.000001Z")
    assert (
        validate_quote_age(as_of=at, observed_at=at, received_at=received_future)
        is Freshness.CONFLICT
    )


def test_adv_19_sessions_insufficient() -> None:
    sessions = _adv_sessions(count=19)
    bars = _bars_for(sessions)
    assert compute_adv20(bars, sessions) is None


def test_adv_21_sessions_takes_latest_20() -> None:
    sessions = _adv_sessions()
    bars = _bars_for(sessions)
    old = _bar("2025-12-01", "10.00", volume=10_000_000)
    extended_sessions = tuple(
        sorted((*sessions, _session("2025-12-01")), key=lambda s: s.trading_date.value)
    )
    adv = compute_adv20((old, *bars), extended_sessions)
    assert adv == Decimal("100000000")  # the old bar is dropped


def test_adv_split_aware() -> None:
    sessions = _adv_sessions()
    bars = _bars_for(sessions)
    split = _split(
        ex_date="2026-01-20",
        available_at="2026-01-19T12:00:00.000000Z",
    )
    adv = compute_adv20(bars, sessions, split_adjustments=(split,), security_id=_SEC)
    assert adv == Decimal("100000000")


def test_adv_holiday_half_day() -> None:
    sessions = _adv_sessions(half_days={"2026-01-02"})
    bars = _bars_for(sessions)
    assert compute_adv20(bars, sessions) is not None


def test_adv_rejects_duplicate() -> None:
    sessions = _adv_sessions()
    bars = _bars_for(sessions)
    with pytest.raises(ValueError):
        compute_adv20((*bars, bars[-1]), sessions)


def test_build_market_snapshot_still_derives_hash() -> None:
    with pytest.raises(ValueError, match="not an ingestion API"):
        build_market_snapshot(
            security_id=_SEC,
            symbol=_SYM,
            as_of=_T0,
            known_at=_T0,
            received_at=_T0,
            feed=Feed.IEX,
            entitlement=Entitlement.IEX,
            bid=Decimal("100.00"),
            ask=Decimal("100.05"),
            mid=Decimal("1.0"),
            spread_bps=999,
            quote_source_ref=_IEX,
            coverage=Coverage.LIMITED_MARKET_COVERAGE,
            freshness=Freshness.FRESH,
            coverage_warning="IEX limited market coverage",
            producer_version="p4c.market.v1",
            schema_version=SchemaVersion("1.0.0"),
            last=Decimal("100.02"),
            adv20_usd=Decimal("50000000"),
            bar_feed=Feed.SIP_DELAYED,
            bar_refs=(_BAR,),
            reasons=(),
        )


def test_assembler_rejects_thin_historic_calendar() -> None:
    """A dense recent window must not mask a Monday-only historic calendar.

    The 252-session trading-history gate counts bar dates the calendar
    blesses, so every weekday across the whole bar span needs an explicit
    record; otherwise 'sessions' spaced weeks apart inflate real history.
    """
    sessions: list[MarketSession] = [_session("2026-06-01")]
    cursor = datetime(2026, 5, 29, tzinfo=UTC)
    while len(sessions) < 31:  # as-of day + 30 dense recent weekdays
        if cursor.weekday() < 5:
            sessions.append(_session(cursor.date().isoformat()))
        cursor -= timedelta(days=1)
    while len(sessions) < 263:  # + 232 Monday-only historic sessions
        if cursor.weekday() == 0:
            sessions.append(_session(cursor.date().isoformat()))
        cursor -= timedelta(days=1)
    ordered = tuple(sorted(sessions, key=lambda s: s.trading_date.value))

    with pytest.raises(ValueError, match="every weekday explicitly"):
        _assemble(sessions=ordered, bars=_bars_for(ordered))


def test_assembler_rejects_lying_session_window_hours() -> None:
    """A weekday record claiming a ~24-hour window is a structurally false
    calendar assertion no matter how dense and complete the calendar looks."""
    lying = tuple(
        MarketSession(
            trading_date=s.trading_date,
            day_kind=MarketDayKind.REGULAR,
            regular_session=RegularSessionWindow(
                opens_at=UtcTimestamp(
                    datetime.combine(s.trading_date.value, datetime.min.time(), tzinfo=UTC)
                ),
                closes_at=UtcTimestamp(
                    datetime.combine(s.trading_date.value, datetime.min.time(), tzinfo=UTC)
                    + timedelta(hours=23, minutes=59, seconds=59)
                ),
            ),
        )
        for s in _adv_sessions()
    )
    with pytest.raises(ValueError, match="plausible"):
        _assemble(sessions=lying, bars=_bars_for(lying))


def test_public_market_snapshot_reconstruction_is_not_an_authority() -> None:
    with pytest.raises(ValueError, match="trusted readback-only"):
        from seven_lens.market_data.snapshots import reconstruct_market_snapshot

        reconstruct_market_snapshot()


def test_assembler_rejects_daily_bar_for_another_security() -> None:
    sessions = _adv_sessions()
    bars = _bars_for(sessions)
    foreign = _bar(
        str(bars[0].trading_date),
        str(bars[0].close),
        bars[0].volume,
        security_id=SecurityId("22222222-2222-4222-8222-222222222222"),
    )
    with pytest.raises(ValueError, match="bind to the snapshot security"):
        _assemble(bars=(foreign, *bars[1:]))


def test_market_snapshot_item_and_split_bounds_are_closed() -> None:
    oversized_sessions = _adv_sessions(count=MAX_MARKET_SNAPSHOT_ITEMS + 1)
    with pytest.raises(ValueError, match="item bound"):
        _assemble(sessions=oversized_sessions, bars=_bars_for(oversized_sessions))

    splits = tuple(
        _split(
            ex_date=(datetime(2020, 1, 1).date() + timedelta(days=index)).isoformat(),
            available_at="2026-05-01T12:00:00.000000Z",
            event_id=f"event-split-{index}",
            source_ref=SourceRef(
                f"split-{index}", P4SourceFamily.ALPACA_CORPORATE_ACTIONS, "c" * 64
            ),
        )
        for index in range(MAX_MARKET_SNAPSHOT_SPLITS + 1)
    )
    with pytest.raises(ValueError, match="split_adjustments exceed"):
        _assemble(split_adjustments=splits)


def test_market_snapshot_canonical_wire_byte_bound_is_closed() -> None:
    with pytest.raises(ValueError, match="canonical wire exceeds"):
        _canonical_market_wire_bytes({"payload": "x" * (MAX_MARKET_SNAPSHOT_BYTES + 1)})
