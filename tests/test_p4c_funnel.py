# mypy: ignore-errors
"""P4-C candidate funnel tests: features, winsorize/midrank, quant/evidence/focus, cluster."""

from __future__ import annotations

import json
from dataclasses import replace as dataclass_replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal, getcontext, setcontext

import pytest

from seven_lens.clock.market_clock import MarketDayKind, MarketSession, RegularSessionWindow
from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.market_data.snapshots import SplitAdjustment, split_adjustment_from_lineage
from seven_lens.screening.contracts import (
    EVIDENCE_CAP,
    FOCUS_CLOSE_CAP,
    FOCUS_OPEN_CAP,
    QUANT_CAP,
    CandidateEntry,
    CandidateStage,
    FactorStatus,
    SectorAssignment,
    _finalize_sector_assignment,
    build_candidate_set,
    build_feature_vector,
    build_sector_assignment,
)
from seven_lens.screening.funnel import (
    ClusterResult,
    EvidenceView,
    FactorInput,
    FocusWindow,
    QuarterlyFact,
    ReturnObservation,
    SessionClose,
    SharesObservation,
    _finalize_evidence_view,
    _finalize_factor_input,
    _finalize_quarterly_fact,
    _finalize_return_observation,
    _finalize_session_close,
    _finalize_shares_observation,
    _pearson,
    _pearson_meets_threshold,
    _return_observation_fingerprint,
    _session_close_fingerprint,
    _source_bound_record,
    adjusted_closes,
    assemble_ttm,
    build_clusters,
    build_feature_vectors,
    evidence_candidates,
    focus_candidates,
    quant_candidates,
    return_observations_from_record,
    select_focus_window,
)
from seven_lens.screening.manifests import (
    ClusterStatus,
    FundamentalConcept,
    SicDivision,
    cluster_manifest,
    factor_manifest,
    sector_manifest,
)
from seven_lens.securities.contracts import (
    AssetClass,
    Cik,
    ListingExchange,
    SecurityId,
    SecurityIdentityRecord,
    SecurityStatus,
    SecuritySymbol,
    SourceRef,
    build_identity_record,
)
from seven_lens.securities.corporate_actions import (
    CorporateActionState,
    CorporateActionType,
    SplitRatio,
    build_corporate_action_record,
)
from seven_lens.securities.quarantine import (
    QuarantineDecision,
    QuarantinePurpose,
    QuarantineQuery,
    evaluate_quarantine,
    master_version_for,
)
from seven_lens.sources.adapters.alpaca import parse_assets, parse_bars
from seven_lens.sources.roles import P4SourceFamily
from seven_lens.universe.contracts import (
    _UNIVERSE_SNAPSHOT_AUTHORITY,
    UniverseEntry,
    UniverseSnapshot,
    WholeShareFeasibility,
    _build_universe_snapshot,
)

_AS_OF = UtcTimestamp.from_isoformat("2026-06-01T20:00:00.000000Z")
_KNOWN = UtcTimestamp.from_isoformat("2026-06-01T20:00:00.000000Z")
_SCHEMA = SchemaVersion("1.0.0")
_UNIVERSE_POLICY = "e" * 64
_MASTER_VERSION = "p4b.securities.v1:" + "a" * 64
_SID_ZERO = SecurityId("11111111-1111-4111-8111-000000000000")

# Screening input records now have a private content-bound authority.  Keep
# the fixture calls readable while routing construction through the trusted
# test assembler; ordinary dataclasses still use the standard replace helper.
_SessionClose = SessionClose
_QuarterlyFact = QuarterlyFact
_SharesObservation = SharesObservation
_ReturnObservation = ReturnObservation
_FactorInput = FactorInput
_EvidenceView = EvidenceView
SessionClose = _finalize_session_close
QuarterlyFact = _finalize_quarterly_fact
SharesObservation = _finalize_shares_observation
FactorInput = _finalize_factor_input
EvidenceView = _finalize_evidence_view


def ReturnObservation(*args, **kwargs):  # type: ignore[no-untyped-def]
    names = ("trading_date", "value", "available_at", "security_id", "source_ref")
    if len(args) > len(names):
        raise TypeError("too many positional return-observation arguments")
    body = dict(zip(names, args, strict=False))
    body.update(kwargs)
    source_ref = body.get("source_ref")
    security_id = body.get("security_id")
    if type(source_ref) is SourceRef and type(security_id) is SecurityId:
        return _source_bound_record(
            _ReturnObservation,
            _return_observation_fingerprint,
            body,
            source_record_hash=source_ref.record_hash,
            identity_hash="e" * 64,
        )
    return _finalize_return_observation(**body)


def replace(obj, /, **changes):  # type: ignore[no-untyped-def]
    if type(obj) is _SessionClose:
        body = {
            name: getattr(obj, name)
            for name in ("trading_date", "close", "source_ref", "available_at", "security_id")
        }
        body.update(changes)
        return _finalize_session_close(**body)
    if type(obj) is _QuarterlyFact:
        body = {
            name: getattr(obj, name)
            for name in (
                "concept",
                "value",
                "period_end",
                "fiscal_year",
                "fiscal_period",
                "currency",
                "entity",
                "consolidation",
                "source_ref",
                "available_at",
                "security_id",
                "fiscal_quarter",
            )
        }
        body.update(changes)
        return _finalize_quarterly_fact(**body)
    if type(obj) is _SharesObservation:
        body = {
            name: getattr(obj, name)
            for name in (
                "value",
                "entity",
                "currency",
                "consolidation",
                "source_ref",
                "available_at",
                "security_id",
            )
        }
        body.update(changes)
        return _finalize_shares_observation(**body)
    if type(obj) is _ReturnObservation:
        body = {
            name: getattr(obj, name)
            for name in ("trading_date", "value", "available_at", "security_id", "source_ref")
        }
        body.update(changes)
        source_ref = body.get("source_ref")
        security_id = body.get("security_id")
        if type(source_ref) is SourceRef and type(security_id) is SecurityId:
            return _source_bound_record(
                _ReturnObservation,
                _return_observation_fingerprint,
                body,
                source_record_hash=source_ref.record_hash,
                identity_hash="e" * 64,
            )
        return _finalize_return_observation(**body)
    if type(obj) is _FactorInput:
        body = {
            name: getattr(obj, name)
            for name in (
                "security_id",
                "symbol",
                "closes",
                "facts",
                "shares_outstanding",
                "sessions",
                "split_adjustments",
            )
        }
        body.update(changes)
        return _finalize_factor_input(**body)
    return dataclass_replace(obj, **changes)


def _sid(n: int) -> SecurityId:
    return SecurityId(f"11111111-1111-4111-8111-{n:012d}")


def _symbol(n: int) -> SecuritySymbol:
    return SecuritySymbol(f"SYM{n}")


def _confirmed_split(
    *,
    security_id: SecurityId,
    ex_date: TradingDate,
    available_at: UtcTimestamp,
    event_id: str,
    security_identity_hash: str = "e" * 64,
) -> SplitAdjustment:
    """Build a split through the accepted P4-B lineage contract."""
    detected_at = UtcTimestamp(available_at.value - timedelta(minutes=2))
    blocked_at = UtcTimestamp(available_at.value - timedelta(minutes=1))
    declared_date = min(ex_date.value - timedelta(days=1), detected_at.value.date())
    declared_at = UtcTimestamp(datetime.combine(declared_date, datetime.min.time(), tzinfo=UTC))
    source_ref = SourceRef(f"alpaca-{event_id}", P4SourceFamily.ALPACA_CORPORATE_ACTIONS, "c" * 64)
    confirmation_ref = SourceRef(f"official-{event_id}", P4SourceFamily.SEC_EDGAR, "d" * 64)
    common = {
        "event_id": event_id,
        "security_id": security_id,
        "security_identity_hash": security_identity_hash,
        "action_type": CorporateActionType.FORWARD_SPLIT,
        "ratio": SplitRatio.from_fraction(numerator=2, denominator=1),
        "declared_at": declared_at,
        "ex_date": ex_date,
        "effective_date": ex_date,
        "schema_version": _SCHEMA,
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
        available_at=available_at,
        state=CorporateActionState.CONFIRMED,
        source_refs=(source_ref, confirmation_ref),
    )
    return split_adjustment_from_lineage((detected, blocked, confirmed))


def _session(date: TradingDate) -> MarketSession:
    if date.value.weekday() >= 5:
        return MarketSession(date, MarketDayKind.CLOSED, None)
    start = datetime.combine(date.value, datetime.min.time(), tzinfo=UTC)
    return MarketSession(
        date,
        MarketDayKind.REGULAR,
        RegularSessionWindow(
            opens_at=UtcTimestamp(start + timedelta(hours=13, minutes=30)),
            closes_at=UtcTimestamp(start + timedelta(hours=20)),
        ),
    )


def _sessions_for_closes(
    closes: tuple[SessionClose, ...],
    *,
    include_as_of: bool = True,
    as_of: UtcTimestamp = _AS_OF,
) -> tuple[MarketSession, ...]:
    dates = {close.trading_date for close in closes}
    if include_as_of:
        dates.add(TradingDate(as_of.value.date()))
    return tuple(_session(date) for date in sorted(dates, key=lambda date: date.value))


def _identity(security_id: SecurityId, symbol: SecuritySymbol) -> SecurityIdentityRecord:
    return build_identity_record(
        security_id=security_id,
        symbol=symbol,
        exchange=ListingExchange.NYSE,
        asset_class=AssetClass.US_EQUITY,
        valid_from=UtcTimestamp.from_isoformat("2024-01-01T00:00:00.000000Z"),
        available_at=UtcTimestamp.from_isoformat("2024-01-01T00:00:00.000000Z"),
        status=SecurityStatus.ACTIVE,
        cik=Cik("0000000001"),
        source_refs=(
            SourceRef(
                f"identity-{security_id.value}",
                P4SourceFamily.ALPACA_ASSETS,
                "d" * 64,
            ),
        ),
        schema_version=_SCHEMA,
    )


def _decision(security_id: SecurityId, symbol: SecuritySymbol) -> QuarantineDecision:
    identity = _identity(security_id, symbol)
    return evaluate_quarantine(
        query=QuarantineQuery(
            purpose=QuarantinePurpose.CANDIDATE_CREATION,
            security_id=security_id,
            symbol_as_of=symbol,
            decision_at=_KNOWN,
            master_version=master_version_for(identity),
        ),
        identity_records=(identity,),
    )


def _universe(
    *,
    count: int = 100,
    known_at: UtcTimestamp = _KNOWN,
    as_of: TradingDate | None = None,
) -> UniverseSnapshot:
    entries = tuple(
        UniverseEntry(
            security_id=security_id,
            symbol=symbol,
            eligible=True,
            reason=None,
            identity_hash=_identity(security_id, symbol).identity_hash,
            master_version=master_version_for(_identity(security_id, symbol)),
            market_snapshot_hash="b" * 64,
            whole_share_feasibility=WholeShareFeasibility.NOT_EVALUATED,
            quarantine_decision_hash=_decision(security_id, symbol).decision_hash,
        )
        for security_id, symbol in ((_sid(n), _symbol(n)) for n in range(count))
    )
    return _build_universe_snapshot(
        authority=_UNIVERSE_SNAPSHOT_AUTHORITY,
        as_of=as_of if as_of is not None else TradingDate(_AS_OF.value.date()),
        known_at=known_at,
        security_master_version=_MASTER_VERSION,
        market_snapshot_refs=(),
        entries=entries,
        policy_hash=_UNIVERSE_POLICY,
        schema_version=_SCHEMA,
        producer_version="p4c.universe.v1",
    )


_UNIVERSE = _universe()


def _cluster_sessions(
    returns: dict[str, tuple[ReturnObservation, ...]],
) -> tuple[MarketSession, ...]:
    dates = {TradingDate(_AS_OF.value.date())}
    dates.update(observation.trading_date for series in returns.values() for observation in series)
    return tuple(_session(date) for date in sorted(dates, key=lambda date: date.value))


def _cluster_source_ref(security_id: SecurityId) -> SourceRef:
    return SourceRef(
        f"cluster-bars-{security_id.value[:8]}",
        P4SourceFamily.ALPACA_HISTORICAL_BARS,
        "e" * 64,
    )


def _view(entry: CandidateEntry, **flags: bool) -> EvidenceView:
    return EvidenceView(
        security_id=entry.security_id,
        authority_complete=flags.get("authority_complete", True),
        evidence_fresh=flags.get("evidence_fresh", True),
        evidence_conflict=flags.get("evidence_conflict", False),
        prompt_injection_unresolved=flags.get("prompt_injection_unresolved", False),
        quarantine_decision=_decision(entry.security_id, entry.symbol),
        evidence_source_refs=(
            SourceRef(
                f"evidence-{entry.security_id.value[:8]}",
                P4SourceFamily.SEC_EDGAR,
                "f" * 64,
            ),
        ),
    )


def _bar_record(
    closes: tuple[str, ...] = ("100", "110"),
    *,
    dates: tuple[str, ...] = ("2026-05-28", "2026-05-29"),
    retrieved_at: UtcTimestamp = _KNOWN,
):
    assert len(closes) == len(dates)
    payload = {
        "symbol": _symbol(0).value,
        "bars": [
            {
                "t": f"{date}T20:00:00Z",
                "o": close,
                "h": close,
                "l": close,
                "c": close,
                "v": 100,
            }
            for date, close in zip(dates, closes, strict=True)
        ],
    }
    return parse_bars(
        json.dumps(payload).encode(),
        retrieved_at=retrieved_at,
        requested_feed="sip",
        effective_feed="sip",
        requested_timeframe="1Day",
    )[0]


def _return_sessions(*dates: str) -> tuple[MarketSession, ...]:
    return tuple(_session(TradingDate.from_isoformat(value)) for value in dates)


def _asset_record():
    payload = [
        {
            "id": _SID_ZERO.value,
            "symbol": _symbol(0).value,
            "exchange": "NYSE",
            "asset_class": "us_equity",
            "status": "active",
            "tradable": True,
        }
    ]
    return parse_assets(json.dumps(payload).encode(), retrieved_at=_KNOWN)[0]


def _identities_for(entries: tuple[CandidateEntry, ...]) -> dict[str, SecurityIdentityRecord]:
    return {
        entry.security_id.value: _identity(entry.security_id, entry.symbol) for entry in entries
    }


def _review_decision(security_id: SecurityId, symbol: SecuritySymbol) -> QuarantineDecision:
    return evaluate_quarantine(
        query=QuarantineQuery(
            purpose=QuarantinePurpose.CANDIDATE_CREATION,
            security_id=security_id,
            symbol_as_of=symbol,
            decision_at=_KNOWN,
            master_version=_MASTER_VERSION,
        ),
        identity_records=(),
    )


def _build_clusters(**values: object) -> tuple[ClusterResult, ...]:
    returns = values["returns"]
    assert isinstance(returns, dict)
    values["returns"] = {
        security_id: tuple(
            replace(
                observation,
                security_id=SecurityId(security_id),
                source_ref=_cluster_source_ref(SecurityId(security_id)),
            )
            for observation in series
        )
        for security_id, series in returns.items()
    }
    values["sessions"] = _cluster_sessions(values["returns"])  # type: ignore[arg-type]
    return build_clusters(**values)  # type: ignore[arg-type]


def _closes(
    base: float = 100.0,
    count: int = 300,
    drift: float = 0.001,
    security_id: SecurityId = _SID_ZERO,
    as_of: UtcTimestamp = _AS_OF,
) -> tuple[SessionClose, ...]:
    current = as_of.value - timedelta(days=1)
    dates: list[datetime] = []
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current -= timedelta(days=1)
    closes: list[SessionClose] = []
    for made, current in enumerate(reversed(dates)):
        closes.append(
            SessionClose(
                trading_date=TradingDate(current.date()),
                close=Decimal(str(round(base * (1 + drift) ** made, 6))),
                source_ref=SourceRef(
                    "factor-bars",
                    P4SourceFamily.ALPACA_HISTORICAL_BARS,
                    "c" * 64,
                ),
                available_at=_KNOWN,
                security_id=security_id,
            )
        )
    return tuple(closes)


def _future_closes(
    count: int = 300, security_id: SecurityId = _SID_ZERO
) -> tuple[SessionClose, ...]:
    start = datetime(2027, 1, 1, tzinfo=UTC)
    closes: list[SessionClose] = []
    made = 0
    while made < count:
        if start.weekday() < 5:
            closes.append(
                SessionClose(
                    trading_date=TradingDate(start.date()),
                    close=Decimal("100.00"),
                    source_ref=SourceRef(
                        "factor-bars-future",
                        P4SourceFamily.ALPACA_HISTORICAL_BARS,
                        "c" * 64,
                    ),
                    available_at=_KNOWN,
                    security_id=security_id,
                )
            )
            made += 1
        start += timedelta(days=1)
    return tuple(closes)


def _facts(
    base_ni: Decimal | float = 1.0e8,
    security_id: SecurityId = _SID_ZERO,
) -> tuple[QuarterlyFact, ...]:
    base = base_ni if isinstance(base_ni, Decimal) else Decimal(str(base_ni))
    facts: list[QuarterlyFact] = []
    facts.append(
        QuarterlyFact(
            concept=FundamentalConcept.ASSETS,
            value=Decimal("900000000"),
            period_end=TradingDate.from_isoformat("2024-12-31"),
            fiscal_year=2024,
            fiscal_period="Q4",
            currency="USD",
            entity="entity-1",
            consolidation="P",
            source_ref=SourceRef("factor-facts", P4SourceFamily.SEC_EDGAR, "d" * 64),
            available_at=UtcTimestamp.from_isoformat("2025-01-31T21:00:00.000000Z"),
            security_id=security_id,
        )
    )
    for period, end_month_day in (
        ("Q1", "03-31"),
        ("Q2", "06-30"),
        ("Q3", "09-30"),
        ("Q4", "12-31"),
    ):
        end = TradingDate.from_isoformat(f"2025-{end_month_day}")
        avail = UtcTimestamp.from_isoformat(f"2025-{end_month_day}T21:00:00.000000Z")
        # Assets are a positive balance-sheet magnitude independent of income sign.
        values = {
            FundamentalConcept.NET_INCOME_LOSS: base,
            FundamentalConcept.NET_CASH_OPERATING: base * Decimal("0.90"),
            # The normalizer's CapEx contract is a positive cash outflow even
            # when the issuer has negative earnings or operating cash flow.
            FundamentalConcept.CAPEX_PPE: abs(base) * Decimal("0.10"),
            FundamentalConcept.ASSETS: Decimal("1000000000"),
        }
        for concept, value in values.items():
            facts.append(
                QuarterlyFact(
                    concept=concept,
                    value=value,
                    period_end=end,
                    fiscal_year=2025,
                    fiscal_period=period,
                    currency="USD",
                    entity="entity-1",
                    consolidation="P",
                    source_ref=SourceRef("factor-facts", P4SourceFamily.SEC_EDGAR, "d" * 64),
                    available_at=avail,
                    security_id=security_id,
                )
            )
    return tuple(facts)


def _input(
    n: int,
    *,
    base: float = 100.0,
    ni: float = 1.0e8,
    as_of: UtcTimestamp = _AS_OF,
    known_at: UtcTimestamp = _KNOWN,
) -> FactorInput:
    security_id = _sid(n)
    closes = _closes(base=base, security_id=security_id, as_of=as_of)
    return FactorInput(
        security_id=security_id,
        symbol=_symbol(n),
        closes=closes,
        facts=_facts(base_ni=ni, security_id=security_id),
        shares_outstanding=SharesObservation(
            value=Decimal("1000000"),
            entity="entity-1",
            currency="USD",
            consolidation="P",
            source_ref=SourceRef(f"shares-{n}", P4SourceFamily.SEC_EDGAR, "b" * 64),
            available_at=known_at,
            security_id=security_id,
        ),
        sessions=_sessions_for_closes(closes, as_of=as_of),
    )


def _sectors(quant: tuple[CandidateEntry, ...]) -> dict[str, SectorAssignment]:
    return {
        entry.security_id.value: _finalize_sector_assignment(
            security_id=entry.security_id,
            cik="0000000001",
            sic="0100",
            division="A",
            source_ref=SourceRef(
                f"sector-{entry.security_id.value}",
                P4SourceFamily.SEC_EDGAR,
                "a" * 64,
            ),
            accession=None,
            available_at=_KNOWN,
            taxonomy_version="sec-sic-division-v1",
            taxonomy_hash=sector_manifest().manifest_hash,
        )
        for entry in quant
    }


def test_return_observations_derive_exact_simple_return_from_record() -> None:
    record = _bar_record(("100", "110"))
    observations = return_observations_from_record(
        record,
        security_id=_SID_ZERO,
        identities=(_identity(_SID_ZERO, _symbol(0)),),
        known_at=_KNOWN,
        sessions=_return_sessions("2026-05-28", "2026-05-29"),
    )

    assert len(observations) == 1
    assert observations[0].trading_date == TradingDate.from_isoformat("2026-05-29")
    assert observations[0].value == Decimal("0.1")
    assert observations[0].security_id == _SID_ZERO
    assert observations[0].source_ref == SourceRef(
        record.record_id, record.family, record.record_hash
    )


def test_return_observations_derive_negative_simple_return_from_record() -> None:
    observations = return_observations_from_record(
        _bar_record(("100", "90")),
        security_id=_SID_ZERO,
        identities=(_identity(_SID_ZERO, _symbol(0)),),
        known_at=_KNOWN,
        sessions=_return_sessions("2026-05-28", "2026-05-29"),
    )

    assert observations[0].value == Decimal("-0.1")


def test_return_observations_bind_availability_to_later_adjacent_close(monkeypatch) -> None:
    record = _bar_record()
    identity = _identity(_SID_ZERO, _symbol(0))
    source_ref = SourceRef(record.record_id, record.family, record.record_hash)
    previous = _source_bound_record(
        _SessionClose,
        _session_close_fingerprint,
        {
            "trading_date": TradingDate.from_isoformat("2026-05-28"),
            "close": Decimal("100"),
            "source_ref": source_ref,
            "available_at": UtcTimestamp(_KNOWN.value - timedelta(minutes=2)),
            "security_id": _SID_ZERO,
        },
        source_record_hash=record.record_hash,
        identity_hash=identity.identity_hash,
    )
    current = _source_bound_record(
        _SessionClose,
        _session_close_fingerprint,
        {
            "trading_date": TradingDate.from_isoformat("2026-05-29"),
            "close": Decimal("110"),
            "source_ref": source_ref,
            "available_at": UtcTimestamp(_KNOWN.value - timedelta(minutes=1)),
            "security_id": _SID_ZERO,
        },
        source_record_hash=record.record_hash,
        identity_hash=identity.identity_hash,
    )

    def stub_session_closes(*args, **kwargs):
        del args, kwargs
        return (previous, current)

    monkeypatch.setattr(
        "seven_lens.screening.funnel.session_closes_from_record",
        stub_session_closes,
    )
    observations = return_observations_from_record(
        record,
        security_id=_SID_ZERO,
        identities=(identity,),
        known_at=_KNOWN,
        sessions=_return_sessions("2026-05-28", "2026-05-29"),
    )

    assert observations[0].available_at == current.available_at


def test_return_observations_use_split_aware_closes() -> None:
    identity = _identity(_SID_ZERO, _symbol(0))
    split = _confirmed_split(
        security_id=_SID_ZERO,
        ex_date=TradingDate.from_isoformat("2026-05-29"),
        available_at=_KNOWN,
        event_id="return-split",
        security_identity_hash=identity.identity_hash,
    )
    observations = return_observations_from_record(
        _bar_record(("200", "100")),
        security_id=_SID_ZERO,
        identities=(identity,),
        known_at=_KNOWN,
        sessions=_return_sessions("2026-05-28", "2026-05-29"),
        split_adjustments=(split,),
    )

    assert observations[0].value == Decimal("0")


def test_return_observations_accept_adjacent_sessions_across_weekend() -> None:
    observations = return_observations_from_record(
        _bar_record(("100", "110"), dates=("2026-05-29", "2026-06-01")),
        security_id=_SID_ZERO,
        identities=(_identity(_SID_ZERO, _symbol(0)),),
        known_at=_KNOWN,
        sessions=_return_sessions("2026-05-29", "2026-06-01"),
    )
    assert len(observations) == 1
    assert observations[0].trading_date == TradingDate.from_isoformat("2026-06-01")


def test_return_observations_do_not_replace_missing_prior_open_close() -> None:
    observations = return_observations_from_record(
        _bar_record(("100", "110"), dates=("2026-05-28", "2026-06-01")),
        security_id=_SID_ZERO,
        identities=(_identity(_SID_ZERO, _symbol(0)),),
        known_at=_KNOWN,
        sessions=_return_sessions("2026-05-28", "2026-05-29", "2026-06-01"),
    )
    assert observations == ()


def test_return_observations_ignore_future_split() -> None:
    identity = _identity(_SID_ZERO, _symbol(0))
    future = UtcTimestamp.from_isoformat("2026-06-02T20:00:00.000000Z")
    split = _confirmed_split(
        security_id=_SID_ZERO,
        ex_date=TradingDate.from_isoformat("2026-05-29"),
        available_at=future,
        event_id="future-return-split",
        security_identity_hash=identity.identity_hash,
    )
    observations = return_observations_from_record(
        _bar_record(("200", "100")),
        security_id=_SID_ZERO,
        identities=(identity,),
        known_at=_KNOWN,
        sessions=_return_sessions("2026-05-28", "2026-05-29"),
        split_adjustments=(split,),
    )
    assert observations[0].value == Decimal("-0.5")


def test_return_observations_reject_wrong_source_family() -> None:
    with pytest.raises(ValueError, match="historical-bars authority"):
        return_observations_from_record(
            _asset_record(),
            security_id=_SID_ZERO,
            identities=(_identity(_SID_ZERO, _symbol(0)),),
            known_at=_KNOWN,
            sessions=(),
        )


def test_return_observations_reject_unavailable_record() -> None:
    future = UtcTimestamp.from_isoformat("2026-06-02T20:00:00.000000Z")
    with pytest.raises(ValueError, match="not available by known_at"):
        return_observations_from_record(
            _bar_record(retrieved_at=future),
            security_id=_SID_ZERO,
            identities=(_identity(_SID_ZERO, _symbol(0)),),
            known_at=_KNOWN,
            sessions=(),
        )


def test_trend_returns_hand_computed() -> None:
    # P(t-21) / P(t-126) - 1 with a flat price series is exactly 0.
    closes = _closes(base=100.0, count=300, drift=0.0)
    from seven_lens.screening.funnel import _trend

    assert _trend(closes, 126) == Decimal("0")
    assert _trend(closes, 252) == Decimal("0")


def test_vol63_and_max_drawdown_zero_variance() -> None:
    from seven_lens.screening.funnel import _max_drawdown252, _vol63

    closes = _closes(base=100.0, count=300, drift=0.0)
    assert _vol63(closes) == Decimal("0")
    assert _max_drawdown252(closes) == Decimal("0")


def test_vol63_population_denominator() -> None:
    # With alternating 99/101 prices the population volatility is exact.
    closes = tuple(
        SessionClose(
            trading_date=TradingDate(datetime(2024, 1, 1, tzinfo=UTC).date() + timedelta(days=i)),
            close=Decimal("99") if i % 2 == 0 else Decimal("101"),
            source_ref=SourceRef(
                "factor-bars-alternating",
                P4SourceFamily.ALPACA_HISTORICAL_BARS,
                "c" * 64,
            ),
            available_at=_KNOWN,
        )
        for i in range(300)
    )
    from seven_lens.screening.funnel import _vol63

    vol = _vol63(closes)
    assert vol is not None
    assert vol > 0


def test_ttm_assemble_exact() -> None:
    facts = _facts(base_ni=1.0e8)
    ttm = assemble_ttm(facts, Decimal("1000000"), cutoff=_AS_OF)
    assert ttm is not None
    assert ttm.ttm_net_income == Decimal("400000000")
    assert ttm.ttm_cfo == Decimal("360000000")
    assert ttm.ttm_capex == Decimal("40000000")
    assert ttm.assets_at_ttm_start == Decimal("900000000")
    assert ttm.assets_at_ttm_end == Decimal("1000000000")


def test_ttm_rejects_conflicting_direct_and_ytd_authorities() -> None:
    item = _input(0, ni=100.0)
    facts = item.facts
    quarterly_values = {
        FundamentalConcept.NET_INCOME_LOSS: Decimal("100"),
        FundamentalConcept.NET_CASH_OPERATING: Decimal("90"),
        FundamentalConcept.CAPEX_PPE: Decimal("10"),
    }
    ytd: list[QuarterlyFact] = []
    for concept, quarter_value in quarterly_values.items():
        cumulative = Decimal(0)
        for fiscal_quarter, period_end in zip(
            ("Q1", "Q2", "Q3", "Q4"),
            ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"),
            strict=True,
        ):
            cumulative += quarter_value
            value = cumulative
            if concept is FundamentalConcept.NET_INCOME_LOSS and fiscal_quarter == "Q4":
                value += Decimal("1")
            ytd.append(
                QuarterlyFact(
                    concept=concept,
                    value=value,
                    period_end=TradingDate.from_isoformat(period_end),
                    fiscal_year=2025,
                    fiscal_period="YTD",
                    fiscal_quarter=fiscal_quarter,
                    currency="USD",
                    entity="entity-1",
                    consolidation="P",
                    source_ref=SourceRef(
                        f"conflicting-ytd-{concept.value}-{fiscal_quarter}",
                        P4SourceFamily.SEC_EDGAR,
                        "f" * 64,
                    ),
                    available_at=UtcTimestamp.from_isoformat("2026-01-31T21:00:00.000000Z"),
                    security_id=_sid(0),
                )
            )

    conflicting_facts = (*facts, *ytd)
    assert assemble_ttm(conflicting_facts, Decimal("1000000"), cutoff=_AS_OF) is None

    universe = _universe(count=1)
    vector = build_feature_vectors(
        (replace(item, facts=conflicting_facts),),
        as_of=_AS_OF,
        known_at=_KNOWN,
        universe=universe,
    )[0]
    assert vector.status is FactorStatus.FACTOR_INPUT_MISSING
    assert quant_candidates((vector,), universe=universe) == ()


def test_ttm_assemble_accepts_latest_four_quarters_across_fiscal_years() -> None:
    facts = _facts(base_ni=Decimal("10"))
    rolling_periods = {
        "Q1": (2026, "Q1", "2026-03-31"),
        "Q2": (2025, "Q2", "2025-06-30"),
        "Q3": (2025, "Q3", "2025-09-30"),
        "Q4": (2025, "Q4", "2025-12-31"),
    }
    rolling = tuple(
        replace(
            fact,
            fiscal_year=rolling_periods[fact.fiscal_period][0],
            fiscal_period=rolling_periods[fact.fiscal_period][1],
            fiscal_quarter=rolling_periods[fact.fiscal_period][1],
            period_end=TradingDate.from_isoformat(rolling_periods[fact.fiscal_period][2]),
        )
        for fact in facts
        if not (fact.concept is FundamentalConcept.ASSETS and fact.fiscal_year == 2024)
    )
    rolling = (
        replace(
            next(
                fact
                for fact in facts
                if fact.concept is FundamentalConcept.ASSETS and fact.fiscal_year == 2024
            ),
            fiscal_year=2025,
            fiscal_period="Q1",
            fiscal_quarter="Q1",
            period_end=TradingDate.from_isoformat("2025-03-31"),
        ),
        *rolling,
    )

    ttm = assemble_ttm(rolling, Decimal("1000000"), cutoff=_AS_OF)

    assert ttm is not None
    assert ttm.ttm_net_income == Decimal("40")
    assert tuple((fact.fiscal_year, fact.fiscal_period) for fact in ttm.facts) == (
        (2025, "Q2"),
        (2025, "Q3"),
        (2025, "Q4"),
        (2026, "Q1"),
    )


def test_ttm_missing_quarter_is_none() -> None:
    facts = _facts(base_ni=1.0e8)
    # drop Q4 to break the lineage
    trimmed = tuple(
        f for f in facts if not (f.fiscal_period == "Q4" and f.concept is FundamentalConcept.ASSETS)
    )
    ttm = assemble_ttm(trimmed, Decimal("1000000"), cutoff=_AS_OF)
    assert ttm is None


def test_ttm_requires_prior_quarter_asset_as_true_start_balance() -> None:
    facts = _facts(base_ni=Decimal("10"))
    ttm = assemble_ttm(facts, Decimal("1000000"), cutoff=_AS_OF)
    assert ttm is not None
    assert ttm.assets_at_ttm_start == Decimal("900000000")
    assert ttm.assets_at_ttm_end == Decimal("1000000000")

    without_start = tuple(
        fact
        for fact in facts
        if not (
            fact.concept is FundamentalConcept.ASSETS
            and fact.fiscal_year == 2024
            and fact.fiscal_period == "Q4"
        )
    )
    assert assemble_ttm(without_start, Decimal("1000000"), cutoff=_AS_OF) is None


def test_ttm_future_filing_invisible() -> None:
    facts = _facts(base_ni=1.0e8)
    future = QuarterlyFact(
        concept=FundamentalConcept.ASSETS,
        value=Decimal("5e9"),
        period_end=TradingDate.from_isoformat("2025-12-31"),
        fiscal_year=2025,
        fiscal_period="Q4",
        currency="USD",
        entity="entity-1",
        consolidation="P",
        source_ref=SourceRef("future-fact", P4SourceFamily.SEC_EDGAR, "e" * 64),
        available_at=UtcTimestamp.from_isoformat("2026-12-31T21:00:00.000000Z"),
        security_id=_sid(0),
    )
    replaced = tuple(
        future if (f.fiscal_period == "Q4" and f.concept is FundamentalConcept.ASSETS) else f
        for f in facts
    )
    ttm = assemble_ttm(replaced, Decimal("1000000"), cutoff=_AS_OF)
    assert ttm is None


def test_negative_earnings_are_legal() -> None:
    facts = _facts(base_ni=-1.0e8)
    ttm = assemble_ttm(facts, Decimal("1000000"), cutoff=_AS_OF)
    assert ttm is not None
    assert ttm.ttm_net_income < 0


def test_complete_cross_section_scores_and_quant_cap() -> None:
    inputs = tuple(_input(i, base=100.0 + i * 10, ni=1.0e8 + i * 1.0e7) for i in range(8))
    universe = _universe(count=8)
    vectors = build_feature_vectors(inputs, as_of=_AS_OF, known_at=_KNOWN, universe=universe)
    assert all(v.status is FactorStatus.COMPLETE for v in vectors)
    quant = quant_candidates(vectors, universe=universe)
    assert len(quant) == 8
    assert len(quant) <= QUANT_CAP
    # descending composite ordering
    composites = [e.composite for e in quant]
    assert composites == sorted(composites, reverse=True)


def test_quant_rejects_caller_supplied_scores_that_do_not_match_raw_factors() -> None:
    universe = _universe(count=1)
    vector = build_feature_vectors((_input(0),), as_of=_AS_OF, known_at=_KNOWN, universe=universe)[
        0
    ]
    with pytest.raises(ValueError, match="authority"):
        build_feature_vector(
            security_id=vector.security_id,
            symbol=vector.symbol,
            universe_hash=vector.universe_hash,
            manifest_hash=vector.manifest_hash,
            as_of=vector.as_of,
            known_at=vector.known_at,
            status=vector.status,
            raw=vector.raw,
            trend=vector.trend,
            quality=vector.quality,
            value=vector.value,
            low_risk=vector.low_risk,
            composite=Decimal("1"),
            missing_reason=None,
            schema_version=vector.schema_version,
            price_session_dates=vector.price_session_dates,
        )


def test_public_candidate_entry_constructor_rejects_caller_forgery() -> None:
    """A caller cannot mint a stage entry with arbitrary lineage/evidence."""
    with pytest.raises(ValueError, match="stage authority"):
        CandidateEntry(
            security_id=_sid(0),
            symbol=_symbol(0),
            composite=Decimal("0"),
            trend=Decimal("0"),
            quality=Decimal("0"),
            value=Decimal("0"),
            low_risk=Decimal("0"),
            stage=CandidateStage.QUANT,
            feature_hash="a" * 64,
            universe_hash="b" * 64,
            quarantine_decision_hash="c" * 64,
        )


def test_missing_input_never_enters_quant() -> None:
    complete = _input(0)
    missing_closes = _closes(count=100, security_id=_sid(1))
    missing = FactorInput(
        security_id=_sid(1),
        symbol=_symbol(1),
        closes=missing_closes,  # insufficient for 252-day trend
        facts=_facts(security_id=_sid(1)),
        shares_outstanding=SharesObservation(
            value=Decimal("1000000"),
            entity="entity-1",
            currency="USD",
            consolidation="P",
            source_ref=SourceRef("shares-missing", P4SourceFamily.SEC_EDGAR, "b" * 64),
            available_at=_KNOWN,
            security_id=_sid(1),
        ),
        sessions=_sessions_for_closes(missing_closes),
    )
    universe = _universe(count=2)
    vectors = build_feature_vectors(
        (complete, missing), as_of=_AS_OF, known_at=_KNOWN, universe=universe
    )
    by_id = {v.security_id.value: v for v in vectors}
    assert by_id[_sid(1).value].status is FactorStatus.FACTOR_INPUT_MISSING
    quant = quant_candidates(vectors, universe=universe)
    assert all(e.security_id != _sid(1) for e in quant)


def test_latest_missing_session_close_is_factor_input_missing() -> None:
    item = _input(0)
    universe = _universe(count=1)
    vector = build_feature_vectors(
        (replace(item, closes=item.closes[:-1]),),
        as_of=_AS_OF,
        known_at=_KNOWN,
        universe=universe,
    )[0]
    assert vector.status is FactorStatus.FACTOR_INPUT_MISSING


def test_factor_builder_rejects_sparse_weekday_calendar() -> None:
    item = _input(0)
    missing_session = item.sessions[-10]
    sparse_sessions = tuple(session for session in item.sessions if session != missing_session)
    sparse_closes = tuple(
        close for close in item.closes if close.trading_date != missing_session.trading_date
    )

    with pytest.raises(ValueError, match="every weekday explicitly"):
        build_feature_vectors(
            (replace(item, sessions=sparse_sessions, closes=sparse_closes),),
            as_of=_AS_OF,
            known_at=_KNOWN,
            universe=_universe(count=1),
        )


def test_factor_builder_rejects_incomplete_cross_section() -> None:
    with pytest.raises(ValueError, match="cover exactly the eligible universe"):
        build_feature_vectors((_input(0),), as_of=_AS_OF, known_at=_KNOWN, universe=_UNIVERSE)


def test_quant_rejects_incomplete_feature_vectors() -> None:
    with pytest.raises(ValueError, match="cover exactly the eligible universe"):
        quant_candidates((), universe=_UNIVERSE)


def test_candidate_builder_rejects_forged_parent_scores() -> None:
    universe = _universe(count=1)
    vectors = build_feature_vectors((_input(0),), as_of=_AS_OF, known_at=_KNOWN, universe=universe)
    quant = quant_candidates(vectors, universe=universe)
    with pytest.raises(ValueError, match="candidate-entry authority"):
        replace(
            quant[0],
            composite=Decimal("0.9"),
            trend=Decimal("0.9"),
            quality=Decimal("0.9"),
            value=Decimal("0.9"),
            low_risk=Decimal("0.9"),
        )


def test_factor_input_rejects_cross_security_component_data() -> None:
    with pytest.raises(ValueError, match="bind to the factor security"):
        replace(_input(0), security_id=_sid(1), symbol=_symbol(1))


def test_future_session_closes_never_enter_factor_vectors() -> None:
    future_closes = _future_closes()
    item = FactorInput(
        security_id=_sid(0),
        symbol=_symbol(0),
        closes=future_closes,
        facts=_facts(),
        shares_outstanding=SharesObservation(
            value=Decimal("1000000"),
            entity="entity-1",
            currency="USD",
            consolidation="P",
            source_ref=SourceRef("shares-future", P4SourceFamily.SEC_EDGAR, "b" * 64),
            available_at=_KNOWN,
            security_id=_sid(0),
        ),
        sessions=_sessions_for_closes(future_closes),
    )
    universe = _universe(count=1)
    vectors = build_feature_vectors((item,), as_of=_AS_OF, known_at=_KNOWN, universe=universe)
    assert vectors[0].status is FactorStatus.FACTOR_INPUT_MISSING


def test_late_price_correction_never_enters_factor_vectors() -> None:
    item = _input(0)
    universe = _universe(count=1)
    corrected = list(item.closes)
    corrected[100] = replace(
        corrected[100],
        available_at=UtcTimestamp.from_isoformat("2026-06-02T00:00:00.000000Z"),
    )
    vector = build_feature_vectors(
        (replace(item, closes=tuple(corrected)),),
        as_of=_AS_OF,
        known_at=_KNOWN,
        universe=universe,
    )[0]
    assert vector.status is FactorStatus.FACTOR_INPUT_MISSING


def test_future_shares_observation_never_enters_factor_vectors() -> None:
    future_shares = SharesObservation(
        value=Decimal("1000000"),
        entity="entity-1",
        currency="USD",
        consolidation="P",
        source_ref=SourceRef("shares-future-cutoff", P4SourceFamily.SEC_EDGAR, "b" * 64),
        available_at=UtcTimestamp.from_isoformat("2026-12-31T21:00:00.000000Z"),
        security_id=_sid(0),
    )
    universe = _universe(count=1)
    vectors = build_feature_vectors(
        (replace(_input(0), shares_outstanding=future_shares),),
        as_of=_AS_OF,
        known_at=_KNOWN,
        universe=universe,
    )
    assert vectors[0].status is FactorStatus.FACTOR_INPUT_MISSING


def test_future_unused_filing_does_not_change_historical_feature_hash() -> None:
    future = QuarterlyFact(
        concept=FundamentalConcept.NET_INCOME_LOSS,
        value=Decimal("999999999"),
        period_end=TradingDate.from_isoformat("2025-12-31"),
        fiscal_year=2025,
        fiscal_period="Q4",
        currency="USD",
        entity="entity-1",
        consolidation="P",
        source_ref=SourceRef("future-unused-filing", P4SourceFamily.SEC_EDGAR, "f" * 64),
        available_at=UtcTimestamp.from_isoformat("2027-01-01T00:00:00.000000Z"),
        security_id=_sid(0),
    )
    universe = _universe(count=1)
    baseline = build_feature_vectors(
        (_input(0),), as_of=_AS_OF, known_at=_KNOWN, universe=universe
    )[0]
    replayed = build_feature_vectors(
        (replace(_input(0), facts=(*_facts(), future)),),
        as_of=_AS_OF,
        known_at=_KNOWN,
        universe=universe,
    )[0]
    assert replayed.wire() == baseline.wire()
    assert replayed.feature_hash == baseline.feature_hash


def test_tie_break_composite_then_id() -> None:
    # Two securities with identical inputs produce identical scores; the
    # stable security id ascending breaks the tie.
    first = _input(0)
    second = _input(1)
    universe = _universe(count=2)
    vectors = build_feature_vectors(
        (first, second), as_of=_AS_OF, known_at=_KNOWN, universe=universe
    )
    quant = quant_candidates(vectors, universe=universe)
    assert [e.security_id.value for e in quant] == sorted([e.security_id.value for e in quant])


def test_evidence_gate_removes_failed() -> None:
    universe = _universe(count=5)
    vectors = build_feature_vectors(
        tuple(_input(i) for i in range(5)),
        as_of=_AS_OF,
        known_at=_KNOWN,
        universe=universe,
    )
    quant = quant_candidates(vectors, universe=universe)
    views = [_view(e) for e in quant]
    # block the top candidate's quarantine
    views[0] = EvidenceView(
        security_id=quant[0].security_id,
        authority_complete=True,
        evidence_fresh=True,
        evidence_conflict=False,
        prompt_injection_unresolved=False,
        quarantine_decision=_review_decision(quant[0].security_id, quant[0].symbol),
        evidence_source_refs=(
            SourceRef(
                f"evidence-{quant[0].security_id.value[:8]}",
                P4SourceFamily.SEC_EDGAR,
                "f" * 64,
            ),
        ),
    )
    evidence = evidence_candidates(
        quant,
        views,
        sector_division=_sectors(quant),
        identity_records=_identities_for(quant),
        as_of=_AS_OF,
        universe=universe,
    )
    assert quant[0].security_id not in [e.security_id for e in evidence]
    assert len(evidence) <= EVIDENCE_CAP


def test_evidence_gate_requires_authority_and_freshness() -> None:
    universe = _universe(count=3)
    vectors = build_feature_vectors(
        tuple(_input(i) for i in range(3)),
        as_of=_AS_OF,
        known_at=_KNOWN,
        universe=universe,
    )
    quant = quant_candidates(vectors, universe=universe)
    views = tuple(
        _view(
            e,
            authority_complete=i != 0,
            evidence_fresh=i != 1,
            evidence_conflict=i == 2,
        )
        for i, e in enumerate(quant)
    )
    evidence = evidence_candidates(
        quant,
        views,
        sector_division=_sectors(quant),
        identity_records=_identities_for(quant),
        as_of=_AS_OF,
        universe=universe,
    )
    assert len(evidence) == 0


def test_evidence_gate_rejects_prompt_injection() -> None:
    universe = _universe(count=3)
    vectors = build_feature_vectors(
        tuple(_input(i) for i in range(3)),
        as_of=_AS_OF,
        known_at=_KNOWN,
        universe=universe,
    )
    quant = quant_candidates(vectors, universe=universe)
    views = tuple(_view(e, prompt_injection_unresolved=i == 0) for i, e in enumerate(quant))
    evidence = evidence_candidates(
        quant,
        views,
        sector_division=_sectors(quant),
        identity_records=_identities_for(quant),
        as_of=_AS_OF,
        universe=universe,
    )
    assert all(e.security_id != quant[0].security_id for e in evidence)


def test_unknown_sector_never_enters_evidence() -> None:
    universe = _universe(count=2)
    vectors = build_feature_vectors(
        tuple(_input(i) for i in range(2)),
        as_of=_AS_OF,
        known_at=_KNOWN,
        universe=universe,
    )
    quant = quant_candidates(vectors, universe=universe)
    views = tuple(_view(e) for e in quant)
    sectors = _sectors(quant)
    sectors[quant[0].security_id.value] = _finalize_sector_assignment(
        security_id=quant[0].security_id,
        cik="0000000001",
        sic="1800",
        division="SECTOR_UNKNOWN",
        source_ref=SourceRef(
            f"sector-unknown-{quant[0].security_id.value}",
            P4SourceFamily.SEC_EDGAR,
            "a" * 64,
        ),
        accession=None,
        available_at=_KNOWN,
        taxonomy_version="sec-sic-division-v1",
        taxonomy_hash=sector_manifest().manifest_hash,
    )
    evidence = evidence_candidates(
        quant,
        views,
        sector_division=sectors,
        identity_records=_identities_for(quant),
        as_of=_AS_OF,
        universe=universe,
    )
    assert quant[0].security_id not in [entry.security_id for entry in evidence]


def test_evidence_requires_point_in_time_sec_assignment() -> None:
    universe = _universe(count=1)
    vectors = build_feature_vectors((_input(0),), as_of=_AS_OF, known_at=_KNOWN, universe=universe)
    quant = quant_candidates(vectors, universe=universe)
    view = _view(quant[0])
    with pytest.raises(ValueError, match="SEC SIC assignments"):
        evidence_candidates(
            quant,
            (view,),
            sector_division={quant[0].security_id.value: SicDivision.A},  # type: ignore[dict-item]
            identity_records=_identities_for(quant),
            as_of=_AS_OF,
            universe=universe,
        )


def test_evidence_rejects_assignment_after_universe_known_at() -> None:
    universe = _universe(
        count=1, known_at=UtcTimestamp.from_isoformat("2026-06-01T19:00:00.000000Z")
    )
    vectors = build_feature_vectors((_input(0),), as_of=_AS_OF, known_at=_KNOWN, universe=universe)
    quant = quant_candidates(vectors, universe=universe)
    future_assignment = _finalize_sector_assignment(
        security_id=quant[0].security_id,
        cik="0000000001",
        sic="0100",
        division="A",
        source_ref=SourceRef("sector-future", P4SourceFamily.SEC_EDGAR, "a" * 64),
        accession=None,
        available_at=UtcTimestamp.from_isoformat("2026-06-01T19:00:00.000001Z"),
        taxonomy_version="sec-sic-division-v1",
        taxonomy_hash=sector_manifest().manifest_hash,
    )
    evidence = evidence_candidates(
        quant,
        (_view(quant[0]),),
        sector_division={quant[0].security_id.value: future_assignment},
        identity_records=_identities_for(quant),
        as_of=_AS_OF,
        universe=universe,
    )
    assert evidence == ()


def test_evidence_rejects_sector_cik_not_bound_to_identity() -> None:
    universe = _universe(count=1)
    vectors = build_feature_vectors((_input(0),), as_of=_AS_OF, known_at=_KNOWN, universe=universe)
    quant = quant_candidates(vectors, universe=universe)
    mismatched = _finalize_sector_assignment(
        security_id=quant[0].security_id,
        cik="0000000002",
        sic="0100",
        division="A",
        source_ref=SourceRef("sector-cik-mismatch", P4SourceFamily.SEC_EDGAR, "a" * 64),
        accession=None,
        available_at=_KNOWN,
        taxonomy_version="sec-sic-division-v1",
        taxonomy_hash=sector_manifest().manifest_hash,
    )
    assert (
        evidence_candidates(
            quant,
            (_view(quant[0]),),
            sector_division={quant[0].security_id.value: mismatched},
            identity_records=_identities_for(quant),
            as_of=_AS_OF,
            universe=universe,
        )
        == ()
    )


def test_focus_caps_12_and_5() -> None:
    universe = _universe(count=20)
    vectors = build_feature_vectors(
        tuple(_input(i) for i in range(20)),
        as_of=_AS_OF,
        known_at=_KNOWN,
        universe=universe,
    )
    quant = quant_candidates(vectors, universe=universe)
    views = tuple(_view(e) for e in quant)
    evidence = evidence_candidates(
        quant,
        views,
        sector_division=_sectors(quant),
        identity_records=_identities_for(quant),
        as_of=_AS_OF,
        universe=universe,
    )
    open_focus = focus_candidates(evidence, FocusWindow.OPEN_PLUS_60M)
    close_focus = focus_candidates(evidence, FocusWindow.CLOSE_MINUS_90M)
    assert len(open_focus) == min(len(evidence), FOCUS_OPEN_CAP)
    assert len(close_focus) == min(len(evidence), FOCUS_CLOSE_CAP)
    assert len(open_focus) <= FOCUS_OPEN_CAP
    assert len(close_focus) <= FOCUS_CLOSE_CAP
    assert all(e.stage is CandidateStage.FOCUS_OPEN for e in open_focus)
    assert all(e.stage is CandidateStage.FOCUS_CLOSE for e in close_focus)


def test_focus_prefix_caps_do_not_pad_short_evidence() -> None:
    universe = _universe(count=7)
    vectors = build_feature_vectors(
        tuple(_input(i) for i in range(7)),
        as_of=_AS_OF,
        known_at=_KNOWN,
        universe=universe,
    )
    quant = quant_candidates(vectors, universe=universe)
    evidence = evidence_candidates(
        quant,
        tuple(_view(entry) for entry in quant),
        sector_division=_sectors(quant),
        identity_records=_identities_for(quant),
        as_of=_AS_OF,
        universe=universe,
    )

    open_focus = focus_candidates(evidence, FocusWindow.OPEN_PLUS_60M)
    close_focus = focus_candidates(evidence, FocusWindow.CLOSE_MINUS_90M)
    assert len(evidence) == 7
    assert tuple(entry.security_id for entry in open_focus) == tuple(
        entry.security_id for entry in evidence
    )
    assert tuple(entry.security_id for entry in close_focus) == tuple(
        entry.security_id for entry in evidence[:FOCUS_CLOSE_CAP]
    )


def test_full_funnel_caps_and_ten_permutations_are_canonical() -> None:
    inputs = tuple(_input(i, base=100.0 + i, ni=1.0e8 + i * 1.0e6) for i in range(QUANT_CAP + 1))
    universe = _universe(count=QUANT_CAP + 1)
    vectors = build_feature_vectors(inputs, as_of=_AS_OF, known_at=_KNOWN, universe=universe)

    permutations = [
        vectors,
        tuple(reversed(vectors)),
        *(
            tuple(vectors[(index + offset) % len(vectors)] for index in range(len(vectors)))
            for offset in range(1, 9)
        ),
    ]
    assert len(permutations) == 10
    quant_results = tuple(quant_candidates(order, universe=universe) for order in permutations)
    assert all(result == quant_results[0] for result in quant_results)

    quant = quant_results[0]
    assert len(quant) == QUANT_CAP
    assert {entry.security_id for entry in quant} < {
        entry.security_id for entry in universe.eligible_entries
    }

    evidence = evidence_candidates(
        quant,
        tuple(_view(entry) for entry in quant),
        sector_division=_sectors(quant),
        identity_records=_identities_for(quant),
        as_of=_AS_OF,
        universe=universe,
    )
    assert len(evidence) == EVIDENCE_CAP
    assert tuple(entry.security_id for entry in evidence) == tuple(
        entry.security_id for entry in quant[:EVIDENCE_CAP]
    )

    focus_open = focus_candidates(evidence, FocusWindow.OPEN_PLUS_60M)
    focus_close = focus_candidates(evidence, FocusWindow.CLOSE_MINUS_90M)
    assert len(focus_open) == FOCUS_OPEN_CAP
    assert len(focus_close) == FOCUS_CLOSE_CAP
    assert tuple(entry.security_id for entry in focus_open) == tuple(
        entry.security_id for entry in evidence[:FOCUS_OPEN_CAP]
    )
    assert tuple(entry.security_id for entry in focus_close) == tuple(
        entry.security_id for entry in evidence[:FOCUS_CLOSE_CAP]
    )


def test_candidate_set_accepts_only_the_canonical_evidence_focus_prefix() -> None:
    inputs = tuple(_input(i, base=100.0 + i, ni=1.0e8 + i * 1.0e6) for i in range(QUANT_CAP + 1))
    universe = _universe(count=QUANT_CAP + 1)
    vectors = build_feature_vectors(inputs, as_of=_AS_OF, known_at=_KNOWN, universe=universe)
    quant = quant_candidates(vectors, universe=universe)
    evidence = evidence_candidates(
        quant,
        tuple(_view(entry) for entry in quant),
        sector_division=_sectors(quant),
        identity_records=_identities_for(quant),
        as_of=_AS_OF,
        universe=universe,
    )
    assert len(evidence) == EVIDENCE_CAP

    focus_open = focus_candidates(evidence, FocusWindow.OPEN_PLUS_60M)
    focus_close = focus_candidates(evidence, FocusWindow.CLOSE_MINUS_90M)
    candidate = build_candidate_set(
        as_of=_AS_OF,
        known_at=_KNOWN,
        factor_manifest_hash=factor_manifest().manifest_hash,
        cluster_manifest_hash=cluster_manifest().manifest_hash,
        universe_hash=universe.universe_hash,
        quant=quant,
        evidence=evidence,
        focus_open=focus_open,
        focus_close=focus_close,
        policy_hash="a" * 64,
        producer_version="p4c.screening.v1",
        schema_version=_SCHEMA,
        feature_vectors=vectors,
    )
    assert candidate.focus_open == focus_open
    assert candidate.focus_close == focus_close

    for wrong_focus, field_name, message in (
        (
            focus_candidates(evidence[10:22], FocusWindow.OPEN_PLUS_60M),
            "focus_open",
            "focus_open",
        ),
        (
            focus_candidates(evidence[1:6], FocusWindow.CLOSE_MINUS_90M),
            "focus_close",
            "focus_close",
        ),
    ):
        values = {
            "as_of": _AS_OF,
            "known_at": _KNOWN,
            "factor_manifest_hash": factor_manifest().manifest_hash,
            "cluster_manifest_hash": cluster_manifest().manifest_hash,
            "universe_hash": universe.universe_hash,
            "quant": quant,
            "evidence": evidence,
            "focus_open": focus_open,
            "focus_close": focus_close,
            "policy_hash": "a" * 64,
            "producer_version": "p4c.screening.v1",
            "schema_version": _SCHEMA,
            "feature_vectors": vectors,
        }
        values[field_name] = wrong_focus
        with pytest.raises(ValueError, match=f"{message} must equal the canonical evidence prefix"):
            build_candidate_set(**values)


def test_permutation_byte_identical() -> None:
    inputs_a = tuple(_input(i) for i in range(6))
    inputs_b = tuple(reversed(inputs_a))
    universe = _universe(count=6)
    va = build_feature_vectors(inputs_a, as_of=_AS_OF, known_at=_KNOWN, universe=universe)
    vb = build_feature_vectors(inputs_b, as_of=_AS_OF, known_at=_KNOWN, universe=universe)
    assert [v.feature_hash for v in va] == [v.feature_hash for v in vb]
    qa = quant_candidates(va, universe=universe)
    qb = quant_candidates(vb, universe=universe)
    assert [e.security_id.value for e in qa] == [e.security_id.value for e in qb]


def test_feature_vectors_ignore_ambient_decimal_context() -> None:
    inputs = (
        _input(0, base=100.1234567, ni=Decimal("123456789")),
        _input(1, base=101.7654321, ni=Decimal("987654321")),
    )
    universe = _universe(count=2)
    original = getcontext().copy()
    try:
        context = getcontext()
        context.prec = 6
        context.rounding = ROUND_DOWN
        low_precision = build_feature_vectors(
            inputs,
            as_of=_AS_OF,
            known_at=_KNOWN,
            universe=universe,
        )

        context.prec = 60
        context.rounding = ROUND_UP
        high_precision = build_feature_vectors(
            inputs,
            as_of=_AS_OF,
            known_at=_KNOWN,
            universe=universe,
        )
    finally:
        setcontext(original)

    assert low_precision == high_precision
    assert tuple(vector.wire() for vector in low_precision) == tuple(
        vector.wire() for vector in high_precision
    )
    assert tuple(vector.feature_hash for vector in low_precision) == tuple(
        vector.feature_hash for vector in high_precision
    )


def test_cluster_results_ignore_ambient_decimal_context() -> None:
    dates = tuple(close.trading_date for close in _closes(count=126))
    first_id = _sid(0)
    second_id = _sid(1)
    first_ref = _cluster_source_ref(first_id)
    second_ref = _cluster_source_ref(second_id)

    # Build one fixed fixture before changing the caller's context.  The
    # divisions deliberately produce non-terminating Decimal values, while
    # the production calculations below must remain context-independent.
    from decimal import localcontext

    with localcontext() as fixture_context:
        fixture_context.prec = 80
        first_values = tuple(
            Decimal(index - 63) / Decimal("97") + Decimal(index % 7) / Decimal("1000003")
            for index in range(len(dates))
        )
        second_values = tuple(
            first * Decimal("0.8") + Decimal(((index * 3) % 11) - 5) / Decimal("1000003")
            for index, first in enumerate(first_values)
        )

    first = tuple(
        ReturnObservation(
            trading_date=trading_date,
            value=value,
            available_at=_KNOWN,
            security_id=first_id,
            source_ref=first_ref,
        )
        for trading_date, value in zip(dates, first_values, strict=True)
    )
    second = tuple(
        ReturnObservation(
            trading_date=trading_date,
            value=value,
            available_at=_KNOWN,
            security_id=second_id,
            source_ref=second_ref,
        )
        for trading_date, value in zip(dates, second_values, strict=True)
    )
    returns = {first_id.value: first, second_id.value: second}
    sessions = _sessions_for_closes(_closes(count=126))

    original = getcontext().copy()
    try:
        context = getcontext()
        context.prec = 6
        context.rounding = ROUND_DOWN
        low_rho = _pearson(first_values, second_values)
        low_edge = _pearson_meets_threshold(first_values, second_values, Decimal("0.75"))
        low_results = build_clusters(
            nodes=(first_id, second_id),
            returns=returns,
            policy_hash="a" * 64,
            as_of=_AS_OF,
            sessions=sessions,
        )

        context.prec = 60
        context.rounding = ROUND_UP
        high_rho = _pearson(first_values, second_values)
        high_edge = _pearson_meets_threshold(first_values, second_values, Decimal("0.75"))
        high_results = build_clusters(
            nodes=(first_id, second_id),
            returns=returns,
            policy_hash="a" * 64,
            as_of=_AS_OF,
            sessions=sessions,
        )
    finally:
        setcontext(original)

    assert low_rho == high_rho
    assert low_edge == high_edge
    assert low_results == high_results
    assert tuple(result.cluster_id for result in low_results) == tuple(
        result.cluster_id for result in high_results
    )


def test_cluster_connected_components() -> None:
    nodes = (_sid(0), _sid(1), _sid(2))
    # Build perfectly correlated series (identical drift) for 0 and 1
    shared = _closes(base=100.0, count=300, drift=0.001)
    shared2 = _closes(base=101.0, count=300, drift=0.001)
    unrelated = _closes(base=100.0, count=300, drift=-0.001)
    returns = {
        _sid(0).value: tuple(
            ReturnObservation(c.trading_date, c.close / Decimal("101.00") - 1, _KNOWN)
            for c in shared
        ),
        _sid(1).value: tuple(
            ReturnObservation(c.trading_date, c.close / Decimal("102.00") - 1, _KNOWN)
            for c in shared2
        ),
        _sid(2).value: tuple(
            ReturnObservation(c.trading_date, c.close / Decimal("101.00") - 1, _KNOWN)
            for c in unrelated
        ),
    }
    results = _build_clusters(
        nodes=nodes,
        returns=returns,
        policy_hash="a" * 64,
        as_of=_AS_OF,
    )
    assigned = [r for r in results if r.status == "ASSIGNED"]
    assert assigned
    # At least one cluster groups the correlated pair together
    assert any(_sid(0) in c.members and _sid(1) in c.members for c in assigned)


def test_cluster_id_ignores_provenance_fields() -> None:
    dates = tuple(close.trading_date for close in _closes(count=126))

    def _result(source_ref: SourceRef) -> ClusterResult:
        observations = tuple(
            ReturnObservation(
                date,
                Decimal("0.01"),
                _KNOWN,
                security_id=_sid(0),
                source_ref=source_ref,
            )
            for date in dates
        )
        return build_clusters(
            nodes=(_sid(0),),
            returns={_sid(0).value: observations},
            policy_hash="a" * 64,
            as_of=_AS_OF,
            sessions=_cluster_sessions({_sid(0).value: observations}),
        )[0]

    first = _result(_cluster_source_ref(_sid(0)))
    second = _result(
        SourceRef(
            "cluster-bars-alternate",
            P4SourceFamily.ALPACA_HISTORICAL_BARS,
            "f" * 64,
        )
    )
    assert first.members == second.members
    assert first.cluster_id == second.cluster_id
    assert first.source_refs != second.source_refs


def test_cluster_rejects_return_series_rebound_to_another_security() -> None:
    dates = tuple(close.trading_date for close in _closes(count=126))
    observations = tuple(
        ReturnObservation(
            date,
            Decimal("0.01"),
            _KNOWN,
            security_id=_sid(0),
            source_ref=_cluster_source_ref(_sid(0)),
        )
        for date in dates
    )
    results = build_clusters(
        nodes=(_sid(1),),
        returns={_sid(1).value: observations},
        policy_hash="a" * 64,
        as_of=_AS_OF,
        sessions=_cluster_sessions({_sid(1).value: observations}),
    )
    assert results[0].status is ClusterStatus.UNKNOWN


def test_cluster_insufficient_returns_is_unknown() -> None:
    nodes = (_sid(0),)
    returns = {
        _sid(0).value: tuple(
            ReturnObservation(c.trading_date, c.close / Decimal("101") - 1, _KNOWN)
            for c in _closes(count=50)
        ),
    }
    results = _build_clusters(
        nodes=nodes,
        returns=returns,
        policy_hash="a" * 64,
        as_of=_AS_OF,
    )
    assert results[0].status == "UNKNOWN"


def test_cluster_security_below_minimum_leaves_complete_nodes_assigned() -> None:
    """Pair coverage is evaluated only between complete series: a node that
    fails the per-security minimum is UNKNOWN itself and never poisons a
    complete node, which stays a legal ASSIGNED singleton."""
    complete_id = _sid(0)
    sparse_id = _sid(1)
    window_dates = tuple(close.trading_date for close in _closes(count=126))
    complete = tuple(
        ReturnObservation(
            trading_date=trading_date,
            value=Decimal(index % 7 - 3) / Decimal("1000"),
            available_at=_KNOWN,
            security_id=complete_id,
            source_ref=_cluster_source_ref(complete_id),
        )
        for index, trading_date in enumerate(window_dates[-100:])
    )
    sparse = tuple(
        ReturnObservation(
            trading_date=trading_date,
            value=Decimal("0.01"),
            available_at=_KNOWN,
            security_id=sparse_id,
            source_ref=_cluster_source_ref(sparse_id),
        )
        for trading_date in window_dates[:50]
    )
    sessions = _sessions_for_closes(_closes(count=126))

    results = build_clusters(
        nodes=(complete_id, sparse_id),
        returns={
            complete_id.value: complete,
            sparse_id.value: sparse,
        },
        policy_hash="a" * 64,
        as_of=_AS_OF,
        sessions=sessions,
    )

    by_member = {result.members[0].value: result for result in results}
    assert by_member[sparse_id.value].status is ClusterStatus.UNKNOWN
    assert by_member[complete_id.value].status is ClusterStatus.ASSIGNED
    assert by_member[complete_id.value].members == (complete_id,)


def test_cluster_exactly_100_returns_in_126_session_window_is_assigned_singleton() -> None:
    security_id = _sid(0)
    window_dates = tuple(close.trading_date for close in _closes(count=126))
    observations = tuple(
        ReturnObservation(
            trading_date=trading_date,
            value=Decimal(index % 7 - 3) / Decimal("1000"),
            available_at=_KNOWN,
            security_id=security_id,
            source_ref=_cluster_source_ref(security_id),
        )
        for index, trading_date in enumerate(window_dates[-100:])
    )
    sessions = _sessions_for_closes(_closes(count=126))

    results = build_clusters(
        nodes=(security_id,),
        returns={security_id.value: observations},
        policy_hash="a" * 64,
        as_of=_AS_OF,
        sessions=sessions,
    )

    assert len(results) == 1
    assert results[0].status is ClusterStatus.ASSIGNED


def test_cluster_rejects_caller_authored_return_values() -> None:
    security_id = _sid(0)
    source_ref = _cluster_source_ref(security_id)
    dates = tuple(close.trading_date for close in _closes(count=126))[-100:]
    forged = tuple(
        _finalize_return_observation(
            trading_date=trading_date,
            value=Decimal("999"),
            available_at=_KNOWN,
            security_id=security_id,
            source_ref=source_ref,
        )
        for trading_date in dates
    )
    result = build_clusters(
        nodes=(security_id,),
        returns={security_id.value: forged},
        policy_hash="a" * 64,
        as_of=_AS_OF,
        sessions=_sessions_for_closes(_closes(count=126)),
    )[0]
    assert result.status is ClusterStatus.UNKNOWN


def test_cluster_accepts_source_bound_return_values() -> None:
    security_id = _sid(0)
    source_ref = _cluster_source_ref(security_id)
    dates = tuple(close.trading_date for close in _closes(count=126))[-100:]
    observations = tuple(
        ReturnObservation(
            trading_date=trading_date,
            value=Decimal(index % 7 - 3) / Decimal("1000"),
            available_at=_KNOWN,
            security_id=security_id,
            source_ref=source_ref,
        )
        for index, trading_date in enumerate(dates)
    )
    result = build_clusters(
        nodes=(security_id,),
        returns={security_id.value: observations},
        policy_hash="a" * 64,
        as_of=_AS_OF,
        sessions=_sessions_for_closes(_closes(count=126)),
    )[0]
    assert result.status is ClusterStatus.ASSIGNED
    assert result.members == (security_id,)


def test_cluster_zero_variance_singleton_is_unknown() -> None:
    flat = _closes(base=100.0, count=300, drift=0.0)
    returns = {
        _sid(0).value: tuple(
            ReturnObservation(close.trading_date, Decimal("0"), _KNOWN) for close in flat
        ),
    }
    results = _build_clusters(
        nodes=(_sid(0),),
        returns=returns,
        policy_hash="a" * 64,
        as_of=_AS_OF,
    )
    assert results[0].status == "UNKNOWN"


def test_three_digit_sic_is_zero_padded() -> None:
    assignment = _finalize_sector_assignment(
        security_id=_sid(0),
        cik="0000000001",
        sic="100",
        division="A",
        source_ref=SourceRef("sic-padding", P4SourceFamily.SEC_EDGAR, "a" * 64),
        accession=None,
        available_at=_KNOWN,
        taxonomy_version="sec-sic-division-v1",
        taxonomy_hash=sector_manifest().manifest_hash,
    )
    assert assignment.sic == "0100"


def test_public_sector_assignment_builder_rejects_self_authored_sic() -> None:
    with pytest.raises(ValueError, match="authority"):
        build_sector_assignment(
            security_id=_sid(0),
            cik="0000000001",
            sic="0100",
            division="A",
            source_ref=SourceRef("public-sic", P4SourceFamily.SEC_EDGAR, "a" * 64),
            accession=None,
            available_at=_KNOWN,
            taxonomy_version="sec-sic-division-v1",
            taxonomy_hash=sector_manifest().manifest_hash,
        )


@pytest.mark.parametrize(
    ("cik", "sic"),
    [
        # Arabic-Indic CIK; Arabic-Indic and fullwidth SIC.
        ("\u0666\u0660\u0660\u0660\u0660\u0660\u0660\u0660\u0660\u0661", "6170"),
        ("0000000001", "\u0666\u0661\u0667"),
        ("0000000001", "\uff16\uff11\uff17\uff10"),
    ],
)
def test_sector_assignment_rejects_unicode_digit_text(cik: str, sic: str) -> None:
    # CIK/SIC authorities key on ASCII digits alone; str.isdigit() would also
    # accept Unicode decimal digits, so construction must refuse them outright.
    with pytest.raises(ValueError, match="digit"):
        _finalize_sector_assignment(
            security_id=_sid(0),
            cik=cik,
            sic=sic,
            division="H",
            source_ref=SourceRef("sic-unicode", P4SourceFamily.SEC_EDGAR, "a" * 64),
            accession=None,
            available_at=_KNOWN,
            taxonomy_version="sec-sic-division-v1",
            taxonomy_hash=sector_manifest().manifest_hash,
        )


def test_future_cluster_returns_are_unknown() -> None:
    future = _future_closes()
    returns = {
        _sid(0).value: tuple(
            ReturnObservation(close.trading_date, Decimal("0.01"), _KNOWN) for close in future
        ),
    }
    results = _build_clusters(
        nodes=(_sid(0),),
        returns=returns,
        policy_hash="a" * 64,
        as_of=_AS_OF,
    )
    assert results[0].status == "UNKNOWN"


def test_future_split_is_not_applied_before_its_ex_date() -> None:
    closes = _closes(count=252)
    split = _confirmed_split(
        security_id=_sid(0),
        ex_date=TradingDate.from_isoformat("2027-01-01"),
        available_at=_KNOWN,
        event_id="future-split",
    )
    assert (
        adjusted_closes(
            closes,
            (split,),
            cutoff=_AS_OF,
            sessions=_sessions_for_closes(closes),
            security_id=_sid(0),
            known_at=_KNOWN,
        )
        == closes
    )


def test_assemble_ttm_uses_latest_complete_fiscal_year() -> None:
    current = _facts()
    current_quarters = tuple(fact for fact in current if fact.fiscal_year == 2025)
    prior_quarters = tuple(
        replace(
            fact,
            period_end=TradingDate(fact.period_end.value.replace(year=2024)),
            fiscal_year=2024,
        )
        for fact in current_quarters
    )
    prior_start = replace(
        next(fact for fact in current if fact.fiscal_year == 2024),
        period_end=TradingDate.from_isoformat("2023-12-31"),
        fiscal_year=2023,
    )
    assembled = assemble_ttm(
        (prior_start, *prior_quarters, *current_quarters),
        Decimal("1000000"),
        cutoff=_AS_OF,
        known_at=_KNOWN,
    )
    assert assembled is not None
    assert assembled.facts == tuple(
        fact for fact in current_quarters if fact.concept is FundamentalConcept.ASSETS
    )


def test_late_cluster_return_correction_is_unknown() -> None:
    observations = [
        ReturnObservation(close.trading_date, Decimal("0.01"), _KNOWN)
        for close in _closes(count=126)
    ]
    observations[50] = replace(
        observations[50],
        available_at=UtcTimestamp.from_isoformat("2026-06-02T00:00:00.000000Z"),
    )
    results = _build_clusters(
        nodes=(_sid(0),),
        returns={_sid(0).value: tuple(observations)},
        policy_hash="a" * 64,
        as_of=_AS_OF,
    )
    assert results[0].status == "UNKNOWN"


def test_cluster_pair_coverage_below_100_is_unknown_not_singletons() -> None:
    # 126 observations per security, but no common observations.  The
    # dates are intentionally synthetic; the authority boundary is pair count.
    first_series = tuple(
        ReturnObservation(
            TradingDate((datetime(2024, 1, 1) + timedelta(days=index)).date()),
            Decimal(index % 2),
            _KNOWN,
        )
        for index in range(126)
    )
    second_series = tuple(
        ReturnObservation(
            TradingDate((datetime(2024, 7, 1) + timedelta(days=index)).date()),
            Decimal(index % 2),
            _KNOWN,
        )
        for index in range(126)
    )
    results = _build_clusters(
        nodes=(_sid(0), _sid(1)),
        returns={_sid(0).value: first_series, _sid(1).value: second_series},
        policy_hash="a" * 64,
        as_of=_AS_OF,
    )
    assert all(result.status == "UNKNOWN" for result in results)


def test_factor_builder_rejects_divergent_input_calendars() -> None:
    """One exchange has one calendar: two inputs whose sessions disagree (a
    private 'holiday' marked CLOSED plus an older substitute session) must be
    rejected before any cross-section percentile is computed."""
    first = _input(0)
    second = _input(1)
    divergent: list[MarketSession] = []
    victim = None
    for s in second.sessions:
        if (
            s.regular_session is not None
            and victim is None
            and s.trading_date.value >= _KNOWN.value.date() - timedelta(days=20)
        ):
            victim = s
            divergent.append(MarketSession(s.trading_date, MarketDayKind.CLOSED, None))
        else:
            divergent.append(s)
    assert victim is not None
    older = divergent[0].trading_date.value - timedelta(days=1)
    while older.weekday() >= 5:
        older -= timedelta(days=1)
    assert all(s.trading_date.value != older for s in divergent)
    divergent.append(
        MarketSession(
            trading_date=TradingDate(older),
            day_kind=MarketDayKind.REGULAR,
            regular_session=RegularSessionWindow(
                opens_at=UtcTimestamp(
                    datetime.combine(older, datetime.min.time(), tzinfo=UTC)
                    + timedelta(hours=13, minutes=30)
                ),
                closes_at=UtcTimestamp(
                    datetime.combine(older, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
                ),
            ),
        )
    )
    divergent_sessions = tuple(sorted(divergent, key=lambda s: s.trading_date.value))

    with pytest.raises(ValueError, match="share one explicit market-session calendar"):
        build_feature_vectors(
            (first, replace(second, sessions=divergent_sessions)),
            as_of=_AS_OF,
            known_at=_KNOWN,
            universe=_universe(count=2),
        )


def test_factor_input_rejects_lying_session_window() -> None:
    """The shared NYSE window validator runs on the funnel side too: a
    calendar of 24-hour 'regular sessions' cannot reach factor evaluation."""
    item = _input(0)
    lying = tuple(
        MarketSession(
            trading_date=s.trading_date,
            day_kind=MarketDayKind.REGULAR,
            regular_session=RegularSessionWindow(
                opens_at=s.regular_session.opens_at,
                closes_at=UtcTimestamp(s.regular_session.opens_at.value + timedelta(hours=24)),
            ),
        )
        if s.regular_session is not None
        else s
        for s in item.sessions
    )
    with pytest.raises(ValueError, match="plausible"):
        replace(item, sessions=lying)


def test_focus_window_rejects_lying_session_hours() -> None:
    """select_focus_window shares the NYSE window validator: a 24-hour
    'regular session' cannot define focus deadlines."""
    lying = MarketSession(
        trading_date=TradingDate.from_isoformat("2026-06-01"),
        day_kind=MarketDayKind.REGULAR,
        regular_session=RegularSessionWindow(
            opens_at=UtcTimestamp.from_isoformat("2026-06-01T00:00:00.000000Z"),
            closes_at=UtcTimestamp.from_isoformat("2026-06-01T23:59:59.000000Z"),
        ),
    )
    with pytest.raises(ValueError, match="plausible"):
        select_focus_window(as_of=_AS_OF, session=lying)


def _split_input(
    *, shares_available_at: UtcTimestamp, split_available_at: UtcTimestamp
) -> FactorInput:
    security_id = _sid(0)
    closes = _closes(base=100.0, security_id=security_id)
    split = _confirmed_split(
        security_id=security_id,
        ex_date=closes[126].trading_date,
        available_at=split_available_at,
        event_id="split-q2",
    )
    shares = SharesObservation(
        value=Decimal("1000"),
        entity="entity-1",
        currency="USD",
        consolidation="P",
        source_ref=SourceRef("shares-q2", P4SourceFamily.SEC_EDGAR, "b" * 64),
        available_at=shares_available_at,
        security_id=security_id,
    )
    return FactorInput(
        security_id=security_id,
        symbol=_symbol(0),
        closes=closes,
        facts=_facts(security_id=security_id),
        shares_outstanding=shares,
        sessions=_sessions_for_closes(closes),
        split_adjustments=(split,),
    )


def test_shares_observation_predating_applied_split_is_missing() -> None:
    """Market cap divides a split-adjusted price by a share count: a share
    observation received before the split became known is on the wrong share
    basis and must yield FACTOR_INPUT_MISSING, never a quiet 2x yield error."""
    split_available = UtcTimestamp.from_isoformat("2026-05-01T14:00:00.000000Z")
    stale = _split_input(
        shares_available_at=UtcTimestamp.from_isoformat("2026-04-01T14:00:00.000000Z"),
        split_available_at=split_available,
    )
    universe = _universe(count=1)
    vector = build_feature_vectors((stale,), as_of=_AS_OF, known_at=_KNOWN, universe=universe)[0]
    assert vector.status is FactorStatus.FACTOR_INPUT_MISSING
    assert any(
        raw.missing_reason == "shares observation predates an applied split" for raw in vector.raw
    )


def test_shares_observation_at_split_availability_boundary_is_complete() -> None:
    """A share observation received exactly when the split became known is on
    the current basis: accepted.  A split that is announced but not yet
    applied (available after known_at) never constrains the share count."""
    split_available = UtcTimestamp.from_isoformat("2026-05-01T14:00:00.000000Z")
    boundary = _split_input(shares_available_at=split_available, split_available_at=split_available)
    universe = _universe(count=1)
    vector = build_feature_vectors((boundary,), as_of=_AS_OF, known_at=_KNOWN, universe=universe)[0]
    assert vector.status is FactorStatus.COMPLETE

    announced_only = _split_input(
        shares_available_at=UtcTimestamp.from_isoformat("2026-04-01T14:00:00.000000Z"),
        split_available_at=UtcTimestamp.from_isoformat("2026-06-01T20:00:00.000001Z"),
    )
    vector2 = build_feature_vectors(
        (announced_only,), as_of=_AS_OF, known_at=_KNOWN, universe=universe
    )[0]
    assert vector2.status is FactorStatus.COMPLETE


def test_feature_vectors_accept_same_month_universe_on_later_day() -> None:
    """A June 1 monthly universe serves the June 2 screening cutoff once its
    known_at is at or before that cutoff; exact-date equality is not the
    availability contract."""
    universe = _universe(
        count=1,
        known_at=UtcTimestamp.from_isoformat("2026-06-02T20:00:00.000000Z"),
    )
    cutoff = UtcTimestamp.from_isoformat("2026-06-02T20:00:00.000000Z")
    vectors = build_feature_vectors(
        (_input(0, as_of=cutoff, known_at=cutoff),),
        as_of=cutoff,
        known_at=cutoff,
        universe=universe,
    )
    assert vectors[0].status is FactorStatus.COMPLETE


def test_evidence_candidates_accept_same_month_universe_on_later_day() -> None:
    universe = _universe(
        count=1,
        known_at=UtcTimestamp.from_isoformat("2026-06-02T20:00:00.000000Z"),
    )
    cutoff = UtcTimestamp.from_isoformat("2026-06-02T20:00:00.000000Z")
    vectors = build_feature_vectors(
        (_input(0, as_of=cutoff, known_at=cutoff),),
        as_of=cutoff,
        known_at=cutoff,
        universe=universe,
    )
    quant = quant_candidates(vectors, universe=universe)
    evidence = evidence_candidates(
        quant,
        [_view(entry) for entry in quant],
        sector_division=_sectors(quant),
        identity_records=_identities_for(quant),
        as_of=cutoff,
        universe=universe,
    )
    assert len(evidence) == 1


def test_screening_cutoff_before_universe_known_at_fails_closed() -> None:
    universe = _universe(
        count=1,
        known_at=UtcTimestamp.from_isoformat("2026-08-03T15:00:00.000000Z"),
        as_of=TradingDate.from_isoformat("2026-08-03"),
    )
    cutoff = UtcTimestamp.from_isoformat("2026-08-03T14:59:59.000000Z")
    with pytest.raises(ValueError, match="universe was not available by screening cutoff"):
        build_feature_vectors((_input(0),), as_of=cutoff, known_at=cutoff, universe=universe)


def test_evidence_cutoff_before_universe_known_at_fails_closed() -> None:
    universe = _universe(
        count=1,
        known_at=UtcTimestamp.from_isoformat("2026-08-03T15:00:00.000000Z"),
        as_of=TradingDate.from_isoformat("2026-08-03"),
    )
    cutoff = UtcTimestamp.from_isoformat("2026-08-03T14:59:59.000000Z")
    with pytest.raises(ValueError, match="universe was not available by screening cutoff"):
        evidence_candidates(
            (),
            (),
            sector_division={},
            identity_records={},
            as_of=cutoff,
            universe=universe,
        )


def test_cross_month_universe_is_rejected_for_screening() -> None:
    universe = _universe(count=1)
    july_cutoff = UtcTimestamp.from_isoformat("2026-07-01T20:00:00.000000Z")
    with pytest.raises(ValueError, match="universe month does not match screening cutoff"):
        build_feature_vectors(
            (_input(0),),
            as_of=july_cutoff,
            known_at=july_cutoff,
            universe=universe,
        )


def test_cross_month_universe_is_rejected_for_evidence() -> None:
    universe = _universe(count=1)
    july_cutoff = UtcTimestamp.from_isoformat("2026-07-01T20:00:00.000000Z")
    with pytest.raises(ValueError, match="universe month does not match screening cutoff"):
        evidence_candidates(
            (),
            (),
            sector_division={},
            identity_records={},
            as_of=july_cutoff,
            universe=universe,
        )
