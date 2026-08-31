# mypy: ignore-errors
"""P4-C universe builder hard-filter boundary tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from seven_lens.clock.market_clock import MarketDayKind, MarketSession, RegularSessionWindow
from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.market_data.snapshots import (
    DailyBar,
    Feed,
    _BarProjectionAuthority,
    _daily_bar_fingerprint,
    assemble_market_snapshot,
    quote_input_from_record,
)
from seven_lens.screening.reasons import ClosedReason
from seven_lens.securities.contracts import (
    AssetClass,
    ListingExchange,
    SecurityId,
    SecurityStatus,
    SecuritySymbol,
    SourceRef,
    build_identity_record,
)
from seven_lens.securities.quarantine import (
    QuarantineDecision,
    QuarantineOutcome,
    QuarantinePurpose,
    QuarantineQuery,
    QuarantineReason,
    evaluate_quarantine,
    master_version_for,
)
from seven_lens.sources.adapters.alpaca import parse_assets
from seven_lens.sources.adapters.issuer_exchange import parse_exchange_notice
from seven_lens.sources.adapters.records import _build_normalized_record as build_normalized_record
from seven_lens.sources.roles import P4SourceFamily
from seven_lens.universe.builder import (
    AssetKind,
    AssetObservation,
    IdentityView,
    MarketView,
    QuarantineView,
    asset_observation_from_records,
    build_universe,
    identity_view_from_records,
)
from seven_lens.universe.contracts import (
    MAX_UNIVERSE_SNAPSHOT_BYTES,
    UniverseSnapshot,
    WholeShareFeasibility,
    _canonical_universe_wire_bytes,
    build_universe_snapshot,
)

_POLICY_HASH = "a" * 64
_SCHEMA = SchemaVersion("1.0.0")
_AS_OF = TradingDate.from_isoformat("2026-06-01")
_KNOWN_AT = UtcTimestamp.from_isoformat("2026-06-01T20:00:00.000000Z")
_SEC = SecurityId("11111111-1111-4111-8111-111111111111")
_SYM = SecuritySymbol("TEST")
_SEC2 = SecurityId("22222222-2222-4222-8222-222222222222")
_SYM2 = SecuritySymbol("TST2")
_SEC3 = SecurityId("33333333-3333-4333-8333-333333333333")
_SYM3 = SecuritySymbol("TST3")

_MASTER_VERSION = "p4b.securities.v1:" + "a" * 64
_MARKET_AS_OF = UtcTimestamp.from_isoformat("2026-06-01T14:00:00.000000Z")


def _trusted_bar(*, security_id: SecurityId, trading_date: TradingDate, close: Decimal) -> DailyBar:
    source_ref = SourceRef(
        f"bar-{security_id.value[:8]}-{trading_date.value.isoformat()}",
        P4SourceFamily.ALPACA_HISTORICAL_BARS,
        "b" * 64,
    )
    values: dict[str, object] = {
        "security_id": security_id,
        "trading_date": trading_date,
        "close": close,
        "volume": 1_000_000,
        "source_ref": source_ref,
        "feed": Feed.SIP_DELAYED,
        "available_at": UtcTimestamp(
            datetime.combine(trading_date.value, datetime.min.time(), tzinfo=UTC)
            + timedelta(hours=21)
        ),
    }
    provisional = object.__new__(DailyBar)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    authority = _BarProjectionAuthority(
        _daily_bar_fingerprint(provisional), source_ref.record_hash, "e" * 64
    )
    return DailyBar(**values, _authority=authority)  # type: ignore[arg-type]


def _asset(
    security_id: SecurityId = _SEC,
    symbol: SecuritySymbol = _SYM,
    kind: AssetKind = AssetKind.ORDINARY_COMMON_STOCK,
    active: bool = True,
    tradable: bool = True,
    observed_at: UtcTimestamp = _KNOWN_AT,
    halted: bool | None = False,
) -> AssetObservation:
    if halted is None:
        # The factory intentionally cannot turn missing official halt status
        # into an observation.  Build an authority-bound value then mutate it
        # for the fail-closed consumer test below.
        result = _asset(security_id, symbol, kind, active, tradable, observed_at, False)
        object.__setattr__(result, "halted", None)
        return result
    asset_record, exchange_record = _asset_records(
        security_id=security_id,
        symbol=symbol,
        kind=kind,
        active=active,
        tradable=tradable,
        observed_at=observed_at,
        halted=halted,
    )
    identity = _identity_record(
        security_id=security_id,
        symbol=symbol,
        asset_record=asset_record,
    )
    return asset_observation_from_records(
        asset_record,
        exchange_record,
        identity=identity,
        known_at=max(_KNOWN_AT, observed_at, key=lambda value: value.value),
    )


def _asset_records(
    *,
    security_id: SecurityId,
    symbol: SecuritySymbol,
    kind: AssetKind,
    active: bool,
    tradable: bool,
    observed_at: UtcTimestamp,
    halted: bool,
):
    asset_payload = json.dumps(
        [
            {
                "id": security_id.value,
                "symbol": symbol.value,
                "exchange": "NYSE",
                "asset_class": "us_equity",
                "status": "active" if active else "inactive",
                "tradable": tradable,
            }
        ]
    ).encode()
    exchange_payload = json.dumps(
        {
            "notices": [
                {
                    "id": f"status-{security_id.value[:8]}",
                    "title": "Security status",
                    "url": f"https://www.nyse.com/notice/{security_id.value[:8]}",
                    "exchange": "NYSE",
                    "published_at": str(observed_at),
                    "symbol": symbol.value,
                    "instrument_kind": kind.value,
                    "halted": halted,
                    "observed_at": str(observed_at),
                }
            ]
        }
    ).encode()
    return (
        parse_assets(asset_payload, retrieved_at=observed_at)[0],
        parse_exchange_notice(exchange_payload, retrieved_at=observed_at)[0],
    )


def _identity_record(
    *,
    security_id: SecurityId,
    symbol: SecuritySymbol,
    asset_record,
    resolved: bool = True,
):
    return build_identity_record(
        security_id=security_id,
        symbol=symbol,
        exchange=ListingExchange.NYSE,
        asset_class=AssetClass.US_EQUITY,
        valid_from=UtcTimestamp.from_isoformat("2025-01-01T00:00:00.000000Z"),
        available_at=UtcTimestamp.from_isoformat("2025-01-01T00:00:00.000000Z"),
        status=SecurityStatus.ACTIVE if resolved else SecurityStatus.INACTIVE,
        source_refs=(
            SourceRef(
                asset_record.record_id,
                asset_record.family,
                asset_record.record_hash,
            ),
        ),
        schema_version=_SCHEMA,
    )


def _identity(
    security_id: SecurityId = _SEC,
    resolved: bool = True,
    symbol: SecuritySymbol | None = None,
    observed_at: UtcTimestamp = _KNOWN_AT,
) -> IdentityView:
    symbols = {_SEC: _SYM, _SEC2: _SYM2, _SEC3: _SYM3}
    resolved_symbol = symbol if symbol is not None else symbols.get(security_id, _SYM)
    asset_record, _ = _asset_records(
        security_id=security_id,
        symbol=resolved_symbol,
        kind=AssetKind.ORDINARY_COMMON_STOCK,
        active=True,
        tradable=True,
        observed_at=observed_at,
        halted=False,
    )
    record = _identity_record(
        security_id=security_id,
        symbol=resolved_symbol,
        asset_record=asset_record,
        resolved=resolved,
    )
    return identity_view_from_records(
        (record,),
        security_id=security_id,
        as_of=UtcTimestamp(datetime.combine(_AS_OF.value, datetime.min.time(), tzinfo=UTC)),
        known_at=_KNOWN_AT,
    )


def _rehash_decision(
    decision: QuarantineDecision, *, outcome: QuarantineOutcome, master_version: str | None = None
) -> QuarantineDecision:
    values: dict[str, object] = {
        "security_id": decision.security_id,
        "symbol_as_of": decision.symbol_as_of,
        "master_version": master_version or decision.master_version,
        "decision_at": decision.decision_at,
        "outcome": outcome,
        "reasons": (QuarantineReason.SPLIT_DETECTED,)
        if outcome is not QuarantineOutcome.ELIGIBLE
        else (),
        "event_ids": (),
        "source_refs": decision.source_refs,
    }
    provisional = object.__new__(QuarantineDecision)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "decision_hash", "")
    values["decision_hash"] = provisional.compute_hash()
    return QuarantineDecision(**values)  # type: ignore[arg-type]


def _quarantine(
    security_id: SecurityId = _SEC,
    outcome: str = "ELIGIBLE",
    symbol: SecuritySymbol | None = None,
    master_version: str | None = None,
    observed_at: UtcTimestamp = _KNOWN_AT,
) -> QuarantineView:
    identity = _identity(security_id, symbol=symbol, observed_at=observed_at)
    decision = evaluate_quarantine(
        query=QuarantineQuery(
            purpose=QuarantinePurpose.CANDIDATE_CREATION,
            security_id=security_id,
            symbol_as_of=identity.symbol,
            decision_at=_KNOWN_AT,
            master_version=master_version_for(identity.record),
        ),
        identity_records=(identity.record,),
    )
    if outcome != "ELIGIBLE" or master_version is not None:
        decision = _rehash_decision(
            decision,
            outcome=QuarantineOutcome(outcome),
            master_version=master_version,
        )
    return QuarantineView(decision=decision)


def _market(security_id: SecurityId = _SEC, **overrides: object) -> MarketView:
    symbols = {_SEC: _SYM, _SEC2: _SYM2, _SEC3: _SYM3}
    price = overrides.pop("last", Decimal("100.00"))
    adv20_usd = overrides.pop("adv20_usd", Decimal("50000000"))
    trading_history_sessions = overrides.pop("trading_history_sessions", 300)
    freshness_ok = overrides.pop("freshness_ok", True)
    spread_ok = overrides.pop("spread_ok", True)
    if overrides:
        raise AssertionError(f"unexpected market overrides: {sorted(overrides)}")
    if type(price) is not Decimal or type(adv20_usd) is not Decimal:
        raise AssertionError("test market values must be Decimal")
    if type(trading_history_sessions) is not int:
        raise AssertionError("test trading history must be an integer")
    sessions: list[MarketSession] = []
    current = datetime(2026, 6, 1, tzinfo=UTC)
    while len(sessions) < trading_history_sessions + 1:
        if current.weekday() < 5:
            date = TradingDate(current.date())
            sessions.append(
                MarketSession(
                    trading_date=date,
                    day_kind=MarketDayKind.REGULAR,
                    regular_session=RegularSessionWindow(
                        opens_at=UtcTimestamp(current.replace(hour=13, minute=30)),
                        closes_at=UtcTimestamp(current.replace(hour=20)),
                    ),
                )
            )
        current -= timedelta(days=1)
    ordered_sessions = tuple(reversed(sessions))
    bar_close = adv20_usd / Decimal(1_000_000)
    bars = tuple(
        _trusted_bar(
            security_id=security_id,
            trading_date=session.trading_date,
            close=bar_close,
        )
        for session in ordered_sessions
        if session.trading_date.value < _MARKET_AS_OF.value.date()
    )
    observed_at = _MARKET_AS_OF
    if not freshness_ok:
        observed_at = UtcTimestamp.from_isoformat("2026-06-01T13:59:00.000000Z")
    ask = price if spread_ok else price + Decimal("0.32")
    quote_record = build_normalized_record(
        record_id=f"quote-{security_id.value[:8]}",
        family=P4SourceFamily.ALPACA_IEX_QUOTES,
        endpoint_id="latest_quote",
        schema_version=_SCHEMA,
        content_hash="a" * 64,
        retrieved_at=observed_at,
        observation_at=observed_at,
        payload={
            "symbol": symbols.get(security_id, _SYM).value,
            "bid_price": str(price),
            "ask_price": str(ask),
            "timestamp": str(observed_at),
            "feed": "iex",
        },
        material_claim=False,
        coverage_warning="IEX feed only; not full NBBO/SIP market coverage",
    )
    snapshot = assemble_market_snapshot(
        security_id=security_id,
        symbol=symbols.get(security_id, _SYM),
        as_of=_MARKET_AS_OF,
        known_at=_MARKET_AS_OF,
        quote=quote_input_from_record(quote_record),
        bars=bars,
        sessions=ordered_sessions,
    )
    return MarketView(snapshot=snapshot)


def test_eligible_universe_entry() -> None:
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(),),
        identities=(_identity(),),
        quarantines=(_quarantine(),),
        markets=(_market(),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert len(snap.entries) == 1
    entry = snap.entries[0]
    assert entry.eligible
    assert entry.reason is None
    assert entry.whole_share_feasibility is WholeShareFeasibility.NOT_EVALUATED


def test_etf_excluded() -> None:
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(kind=AssetKind.ETF),),
        identities=(_identity(),),
        quarantines=(_quarantine(),),
        markets=(_market(),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert not snap.entries[0].eligible
    assert snap.entries[0].reason is ClosedReason.UNSUPPORTED_ASSET_CLASS


def test_otc_excluded() -> None:
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(kind=AssetKind.OTC),),
        identities=(_identity(),),
        quarantines=(_quarantine(),),
        markets=(_market(),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert not snap.entries[0].eligible
    assert snap.entries[0].reason is ClosedReason.OTC_OR_EXCLUDED_INSTRUMENT


def test_inactive_excluded() -> None:
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(active=False),),
        identities=(_identity(),),
        quarantines=(_quarantine(),),
        markets=(_market(),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert not snap.entries[0].eligible
    assert snap.entries[0].reason is ClosedReason.NOT_ACTIVE_OR_TRADABLE


def test_untradable_excluded() -> None:
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(tradable=False),),
        identities=(_identity(),),
        quarantines=(_quarantine(),),
        markets=(_market(),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert not snap.entries[0].eligible
    assert snap.entries[0].reason is ClosedReason.NOT_ACTIVE_OR_TRADABLE


def test_identity_not_closed_excluded() -> None:
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(),),
        identities=(),
        quarantines=(_quarantine(),),
        markets=(_market(),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert not snap.entries[0].eligible
    assert snap.entries[0].reason is ClosedReason.IDENTITY_NOT_CLOSED


def test_quarantine_not_eligible_excluded() -> None:
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(),),
        identities=(_identity(),),
        quarantines=(_quarantine(outcome="ENTRY_BLOCKED"),),
        markets=(_market(),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert not snap.entries[0].eligible
    assert snap.entries[0].reason is ClosedReason.CORPORATE_ACTION_QUARANTINE


def test_price_below_minimum_excluded() -> None:
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(),),
        identities=(_identity(),),
        quarantines=(_quarantine(),),
        markets=(_market(last=Decimal("4.99")),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert not snap.entries[0].eligible
    assert snap.entries[0].reason is ClosedReason.PRICE_BELOW_MINIMUM


def test_adv_below_minimum_excluded() -> None:
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(),),
        identities=(_identity(),),
        quarantines=(_quarantine(),),
        markets=(_market(adv20_usd=Decimal("19999999")),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert not snap.entries[0].eligible
    assert snap.entries[0].reason is ClosedReason.ADV_BELOW_MINIMUM


def test_insufficient_trading_history_excluded() -> None:
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(),),
        identities=(_identity(),),
        quarantines=(_quarantine(),),
        markets=(_market(trading_history_sessions=251),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert not snap.entries[0].eligible
    assert snap.entries[0].reason is ClosedReason.INSUFFICIENT_TRADING_HISTORY


def test_quote_stale_excluded() -> None:
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(),),
        identities=(_identity(),),
        quarantines=(_quarantine(),),
        markets=(_market(freshness_ok=False),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert not snap.entries[0].eligible
    assert snap.entries[0].reason is ClosedReason.QUOTE_MISSING_OR_STALE


def test_spread_too_wide_excluded() -> None:
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(),),
        identities=(_identity(),),
        quarantines=(_quarantine(),),
        markets=(_market(spread_ok=False),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert not snap.entries[0].eligible
    assert snap.entries[0].reason is ClosedReason.SPREAD_TOO_WIDE


def test_canonical_stable_security_id_order() -> None:
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(
            _asset(security_id=_SEC, symbol=_SYM),
            _asset(security_id=_SEC2, symbol=_SYM2),
            _asset(security_id=_SEC3, symbol=_SYM3),
        ),
        identities=(_identity(_SEC), _identity(_SEC2), _identity(_SEC3)),
        quarantines=(_quarantine(_SEC), _quarantine(_SEC2), _quarantine(_SEC3)),
        markets=(_market(_SEC), _market(_SEC2), _market(_SEC3)),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    ids = [e.security_id.value for e in snap.entries]
    assert ids == sorted(ids)
    assert snap.verify_integrity()


def test_deterministic_replay() -> None:
    first = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(), _asset(security_id=_SEC2, symbol=_SYM2)),
        identities=(_identity(_SEC), _identity(_SEC2)),
        quarantines=(_quarantine(_SEC), _quarantine(_SEC2)),
        markets=(_market(_SEC), _market(_SEC2)),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    second = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(security_id=_SEC2, symbol=_SYM2), _asset()),
        identities=(_identity(_SEC2), _identity(_SEC)),
        quarantines=(_quarantine(_SEC2), _quarantine(_SEC)),
        markets=(_market(_SEC2), _market(_SEC)),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert first.universe_hash == second.universe_hash
    assert first.wire() == second.wire()


def test_duplicate_security_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="assets must not repeat"):
        build_universe(
            as_of=_AS_OF,
            known_at=_KNOWN_AT,
            security_master_version=_MASTER_VERSION,
            assets=(_asset(), _asset()),
            identities=(_identity(),),
            quarantines=(_quarantine(),),
            markets=(_market(),),
            policy_hash=_POLICY_HASH,
            schema_version=_SCHEMA,
        )


def test_identity_symbol_mismatch_is_not_eligible() -> None:
    mismatched = _identity(symbol=SecuritySymbol("OTHER"))
    snapshot = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(),),
        identities=(mismatched,),
        quarantines=(_quarantine(symbol=SecuritySymbol("OTHER")),),
        markets=(_market(),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert snapshot.entries[0].reason is ClosedReason.IDENTITY_NOT_CLOSED


def test_quarantine_master_version_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="master version"):
        build_universe(
            as_of=_AS_OF,
            known_at=_KNOWN_AT,
            security_master_version=_MASTER_VERSION,
            assets=(_asset(),),
            identities=(_identity(),),
            quarantines=(_quarantine(master_version="p4b.securities.v1:" + "b" * 64),),
            markets=(_market(),),
            policy_hash=_POLICY_HASH,
            schema_version=_SCHEMA,
        )


def test_future_asset_observation_is_rejected() -> None:
    future = UtcTimestamp.from_isoformat("2026-06-01T20:00:00.000001Z")
    with pytest.raises(ValueError, match="after known_at"):
        build_universe(
            as_of=_AS_OF,
            known_at=_KNOWN_AT,
            security_master_version=_MASTER_VERSION,
            assets=(_asset(observed_at=future),),
            identities=(_identity(),),
            quarantines=(_quarantine(),),
            markets=(_market(),),
            policy_hash=_POLICY_HASH,
            schema_version=_SCHEMA,
        )


def test_stale_asset_observation_is_excluded() -> None:
    """A tradability observation older than the authority-staleness limit
    proves nothing about the current state and can never become eligible."""
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(observed_at=UtcTimestamp.from_isoformat("2026-05-20T00:00:00.000000Z")),),
        identities=(_identity(),),
        quarantines=(_quarantine(),),
        markets=(_market(),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert snap.entries[0].eligible is False
    assert snap.entries[0].reason is ClosedReason.NOT_ACTIVE_OR_TRADABLE


def test_asset_observation_staleness_boundary() -> None:
    """Exactly 7 days old is current; one microsecond older is stale."""
    exactly_limit = UtcTimestamp(_KNOWN_AT.value - timedelta(days=7))
    fresh_enough = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(observed_at=exactly_limit),),
        identities=(_identity(observed_at=exactly_limit),),
        quarantines=(_quarantine(observed_at=exactly_limit),),
        markets=(_market(),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert fresh_enough.entries[0].eligible is True

    one_us_older = UtcTimestamp(exactly_limit.value - timedelta(microseconds=1))
    stale = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(observed_at=one_us_older),),
        identities=(_identity(),),
        quarantines=(_quarantine(),),
        markets=(_market(),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert stale.entries[0].reason is ClosedReason.NOT_ACTIVE_OR_TRADABLE


def test_stale_quarantine_decision_is_excluded() -> None:
    """An ELIGIBLE decision made beyond the staleness limit no longer binds:
    the quarantine gate fails closed to CORPORATE_ACTION_QUARANTINE."""
    identity = _identity()
    stale_decision = evaluate_quarantine(
        query=QuarantineQuery(
            purpose=QuarantinePurpose.CANDIDATE_CREATION,
            security_id=_SEC,
            symbol_as_of=_SYM,
            decision_at=UtcTimestamp.from_isoformat("2026-05-20T00:00:00.000000Z"),
            master_version=master_version_for(identity.record),
        ),
        identity_records=(identity.record,),
    )
    assert stale_decision.outcome is QuarantineOutcome.ELIGIBLE
    snap = build_universe(
        as_of=_AS_OF,
        known_at=_KNOWN_AT,
        security_master_version=_MASTER_VERSION,
        assets=(_asset(),),
        identities=(identity,),
        quarantines=(QuarantineView(decision=stale_decision),),
        markets=(_market(),),
        policy_hash=_POLICY_HASH,
        schema_version=_SCHEMA,
    )
    assert snap.entries[0].eligible is False
    assert snap.entries[0].reason is ClosedReason.CORPORATE_ACTION_QUARANTINE


def test_public_universe_snapshot_builder_is_not_an_authority() -> None:
    with pytest.raises(ValueError, match="trusted builder-only"):
        build_universe_snapshot()


def test_direct_universe_snapshot_constructor_is_not_an_authority() -> None:
    with pytest.raises(ValueError, match="trusted authority"):
        UniverseSnapshot(
            as_of=_AS_OF,
            known_at=_KNOWN_AT,
            security_master_version=_MASTER_VERSION,
            market_snapshot_refs=(),
            entries=(),
            policy_hash=_POLICY_HASH,
            schema_version=_SCHEMA,
            producer_version="p4c.universe.v1",
            universe_hash="a" * 64,
        )


def test_universe_snapshot_canonical_wire_byte_bound_is_closed() -> None:
    with pytest.raises(ValueError, match="canonical wire exceeds"):
        _canonical_universe_wire_bytes({"payload": "x" * (MAX_UNIVERSE_SNAPSHOT_BYTES + 1)})
