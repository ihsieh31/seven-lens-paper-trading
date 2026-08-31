# mypy: ignore-errors
"""P4-C PostgreSQL authority tests: market/universe/feature/candidate storage."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import psycopg
import pytest
from psycopg import sql

from seven_lens.application.ports.p4_source_records import AppendOutcome
from seven_lens.clock.market_clock import MarketDayKind, MarketSession, RegularSessionWindow
from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.infrastructure.postgres_market_data import (
    PostgresMarketDataError,
    PostgresMarketSnapshotStore,
)
from seven_lens.infrastructure.postgres_securities import (
    PostgresP4RecordLog,
    PostgresSecurityMaster,
)
from seven_lens.infrastructure.postgres_universe import (
    PostgresCandidateSetStore,
    PostgresClusterResultStore,
    PostgresFeatureVectorStore,
    PostgresSectorAssignmentStore,
    PostgresUniverseError,
    PostgresUniverseSnapshotStore,
)
from seven_lens.market_data.snapshots import (
    MAX_MARKET_SNAPSHOT_BYTES,
    DailyBar,
    Feed,
    MarketSnapshot,
    SplitAdjustment,
    _BarProjectionAuthority,
    _daily_bar_fingerprint,
    assemble_market_snapshot,
    quote_input_from_record,
    split_adjustment_from_lineage,
)
from seven_lens.screening.contracts import (
    MAX_CANDIDATE_SET_BYTES,
    MAX_FEATURE_VECTOR_BYTES,
    MAX_SECTOR_ASSIGNMENT_BYTES,
    CandidateStage,
    FactorStatus,
    _finalize_candidate_entry,
    _finalize_feature_vector,
    _finalize_sector_assignment,
    build_candidate_set,
)
from seven_lens.screening.funnel import (
    MAX_CLUSTER_RESULT_BYTES,
    _finalize_return_observation,
    build_clusters,
)
from seven_lens.screening.manifests import sector_manifest
from seven_lens.screening.reasons import ClosedReason
from seven_lens.securities.contracts import (
    AssetClass,
    Cik,
    ListingExchange,
    SecurityId,
    SecurityStatus,
    SecuritySymbol,
    SourceRef,
    build_identity_record,
)
from seven_lens.securities.corporate_actions import (
    CorporateActionRecord,
    CorporateActionState,
    CorporateActionType,
    SplitRatio,
    build_corporate_action_record,
)
from seven_lens.securities.quarantine import (
    QuarantineOutcome,
    QuarantinePurpose,
    QuarantineQuery,
    evaluate_quarantine,
    master_version_for,
)
from seven_lens.securities.service import SecurityMasterService
from seven_lens.sources.adapters.records import (
    NormalizedSourceRecord,
)
from seven_lens.sources.adapters.records import (
    _build_normalized_record as build_normalized_record,
)
from seven_lens.sources.roles import P4SourceFamily
from seven_lens.universe.contracts import (
    _UNIVERSE_SNAPSHOT_AUTHORITY,
    MAX_UNIVERSE_SNAPSHOT_BYTES,
    UniverseEntry,
    UniverseSnapshot,
    WholeShareFeasibility,
    _build_universe_snapshot,
)

pytestmark = pytest.mark.integration

_SEC = SecurityId("11111111-1111-4111-8111-111111111111")
_SYM = SecuritySymbol("TEST")
_T0 = UtcTimestamp.from_isoformat("2026-06-01T14:00:00.000000Z")
_IDENTITY_VALID_FROM = UtcTimestamp.from_isoformat("2026-05-01T00:00:00.000000Z")
_SCHEMA = SchemaVersion("1.0.0")
_POLICY = "a" * 64


def _trusted_bar(trading_date: TradingDate) -> DailyBar:
    source_ref = _source_ref("bar-1", P4SourceFamily.ALPACA_HISTORICAL_BARS)
    values: dict[str, object] = {
        "trading_date": trading_date,
        "security_id": _SEC,
        "close": Decimal("100.00"),
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
        _daily_bar_fingerprint(provisional),
        source_ref.record_hash,
        _identity().identity_hash,
    )
    return DailyBar(**values, _authority=authority)  # type: ignore[arg-type]


def _source(
    record_id: str,
    family: P4SourceFamily,
    *,
    available_at: UtcTimestamp = _T0,
    version: str = "v1",
    cik: str = "0000320193",
    sic_only: bool = False,
    quote_bid: str = "100.00",
    quote_ask: str = "100.05",
) -> NormalizedSourceRecord:
    endpoint_id = {
        P4SourceFamily.ALPACA_ASSETS: "asset_detail",
        P4SourceFamily.ALPACA_HISTORICAL_BARS: "stock_bars",
        P4SourceFamily.ALPACA_IEX_QUOTES: "latest_quote",
        P4SourceFamily.ALPACA_CORPORATE_ACTIONS: "corporate_actions",
        P4SourceFamily.SEC_EDGAR: "submissions",
    }[family]
    payload: dict[str, object]
    if family is P4SourceFamily.ALPACA_ASSETS:
        payload = {
            "id": _SEC.value,
            "symbol": _SYM.value,
            "exchange": "NYSE",
            "asset_class": "us_equity",
            "status": "active",
            "tradable": True,
        }
    elif family is P4SourceFamily.ALPACA_HISTORICAL_BARS:
        bar_values: list[dict[str, object]] = []
        current = datetime(2026, 6, 1, tzinfo=UTC) - timedelta(days=1)
        while len(bar_values) < 252:
            if current.weekday() < 5:
                bar_values.append(
                    {
                        "t": current.strftime("%Y-%m-%dT20:00:00.000000Z"),
                        "o": "100.00",
                        "h": "100.00",
                        "l": "100.00",
                        "c": "100.00",
                        "v": 1_000_000,
                    }
                )
            current -= timedelta(days=1)
        payload = {
            "symbol": _SYM.value,
            "feed": "sip",
            "timeframe": "1Day",
            "bars": list(reversed(bar_values)),
            "next_page_token": None,
        }
    elif family is P4SourceFamily.ALPACA_IEX_QUOTES:
        payload = {
            "symbol": _SYM.value,
            "bid_price": quote_bid,
            "ask_price": quote_ask,
            "timestamp": str(_T0),
            "feed": "iex",
        }
    elif family is P4SourceFamily.ALPACA_CORPORATE_ACTIONS:
        payload = {
            "type": "split",
            "split_type": "forward",
            "cusip": None,
            "symbol": _SYM.value,
            "ex_date": "2026-05-20T00:00:00.000000Z",
            "record_date": None,
            "payment_date": None,
            "ratio": "2",
            "supported": True,
            "complete": True,
            "detection_only": True,
        }
    else:
        payload = (
            {"cik_padded": cik, "sic": "0100"}
            if sic_only
            else {
                "cik_padded": cik,
                "accession_number": "0000320193-26-000001",
                "form": "10-Q",
                "primary_document": "test-10q.htm",
                "filing_date": "2026-01-01",
            }
        )
    return build_normalized_record(
        record_id=record_id,
        family=family,
        endpoint_id=endpoint_id,
        schema_version=_SCHEMA,
        content_hash=sha256(f"{record_id}:{version}".encode()).hexdigest(),
        retrieved_at=available_at,
        available_at=available_at,
        payload=payload,
        material_claim=False,
        observation_at=available_at if family is P4SourceFamily.ALPACA_IEX_QUOTES else None,
        coverage_warning=(
            "IEX feed only; not full NBBO/SIP market coverage"
            if family is P4SourceFamily.ALPACA_IEX_QUOTES
            else None
        ),
    )


def _source_ref(record_id: str, family: P4SourceFamily, *, sic_only: bool = False) -> SourceRef:
    record = _source(record_id, family, sic_only=sic_only)
    return SourceRef(record.record_id, record.family, record.record_hash)


def _identity() -> object:
    source = _source("asset-1", P4SourceFamily.ALPACA_ASSETS)
    return build_identity_record(
        security_id=_SEC,
        symbol=_SYM,
        exchange=ListingExchange.NYSE,
        asset_class=AssetClass.US_EQUITY,
        valid_from=_IDENTITY_VALID_FROM,
        available_at=_T0,
        status=SecurityStatus.ACTIVE,
        source_refs=(SourceRef(source.record_id, source.family, source.record_hash),),
        schema_version=_SCHEMA,
        cik=Cik("0000320193"),
    )


def _decision() -> object:
    identity = _identity()
    return evaluate_quarantine(
        query=QuarantineQuery(
            purpose=QuarantinePurpose.CANDIDATE_CREATION,
            security_id=_SEC,
            symbol_as_of=_SYM,
            decision_at=_T0,
            master_version=master_version_for(identity),
        ),
        identity_records=(identity,),
    )


def _seed_lineage(connection: psycopg.Connection[object]) -> None:
    source_log = PostgresP4RecordLog(connection)
    sources = (
        _source("asset-1", P4SourceFamily.ALPACA_ASSETS),
        _source("quote-1", P4SourceFamily.ALPACA_IEX_QUOTES),
        _source("bar-1", P4SourceFamily.ALPACA_HISTORICAL_BARS),
        _source("factor-source", P4SourceFamily.SEC_EDGAR),
        _source("sic-source", P4SourceFamily.SEC_EDGAR, sic_only=True),
    )
    for source in sources:
        source_log.append(source)
    repository = PostgresSecurityMaster(connection)
    service = SecurityMasterService(repository, source_log)
    identity = _identity()
    service.register_identity(identity)
    decision = service.candidate_creation_check(
        QuarantineQuery(
            purpose=QuarantinePurpose.CANDIDATE_CREATION,
            security_id=_SEC,
            symbol_as_of=_SYM,
            decision_at=_T0,
            master_version=master_version_for(identity),
        )
    )
    assert decision.outcome is QuarantineOutcome.ELIGIBLE


def _seed_confirmed_split(
    connection: psycopg.Connection[object],
) -> tuple[SplitAdjustment, tuple[CorporateActionRecord, ...]]:
    """Persist one complete P4-B split lineage and return its P4-C projection."""
    alpaca = _source("split-alpaca", P4SourceFamily.ALPACA_CORPORATE_ACTIONS)
    confirmation = _source("split-sec", P4SourceFamily.SEC_EDGAR)
    source_log = PostgresP4RecordLog(connection)
    source_log.append(alpaca)
    source_log.append(confirmation)
    alpaca_ref = SourceRef(alpaca.record_id, alpaca.family, alpaca.record_hash)
    confirmation_ref = SourceRef(
        confirmation.record_id, confirmation.family, confirmation.record_hash
    )
    identity = _identity()
    common = {
        "event_id": "split-test",
        "security_id": _SEC,
        "security_identity_hash": identity.identity_hash,
        "action_type": CorporateActionType.FORWARD_SPLIT,
        "ratio": SplitRatio.from_fraction(numerator=2, denominator=1),
        "declared_at": UtcTimestamp.from_isoformat("2026-05-19T00:00:00.000000Z"),
        "ex_date": TradingDate.from_isoformat("2026-05-20"),
        "effective_date": TradingDate.from_isoformat("2026-05-20"),
        "available_at": _T0,
        "schema_version": _SCHEMA,
    }
    detected = build_corporate_action_record(
        **common,
        state=CorporateActionState.DETECTED,
        source_refs=(alpaca_ref,),
    )
    blocked = build_corporate_action_record(
        **common,
        state=CorporateActionState.ENTRY_BLOCKED,
        source_refs=(alpaca_ref,),
    )
    confirmed = build_corporate_action_record(
        **common,
        state=CorporateActionState.CONFIRMED,
        source_refs=(alpaca_ref, confirmation_ref),
    )
    repository = PostgresSecurityMaster(connection)
    repository.append_event(detected, previous_record_hash=None)
    repository.append_event(blocked, previous_record_hash=detected.record_hash)
    repository.append_event(confirmed, previous_record_hash=blocked.record_hash)
    lineage = (detected, blocked, confirmed)
    return split_adjustment_from_lineage(lineage), lineage


def _wire_hash(domain: str, wire: dict[str, object]) -> str:
    canonical = json.dumps(
        wire, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return sha256(domain.encode("utf-8") + b"\x00" + canonical).hexdigest()


def _append_concurrently(
    dsn: str, function_name: str, record_hash: str, wire: dict[str, object]
) -> list[str]:
    def _append() -> str:
        with psycopg.connect(dsn, autocommit=True) as connection:
            row = connection.execute(
                f"SELECT public.{function_name}(%s, %s)",
                (record_hash, psycopg.types.json.Jsonb(wire)),
            ).fetchone()
            assert row is not None
            return row[0]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_append), executor.submit(_append)]
        return [future.result() for future in futures]


def _restart_disposable_postgres(dsn: str) -> None:
    container_id = os.environ.get("SEVEN_LENS_TEST_POSTGRES_CONTAINER_ID", "")
    container_name = os.environ.get("SEVEN_LENS_TEST_POSTGRES_CONTAINER_NAME", "")
    owner_token = os.environ.get("SEVEN_LENS_TEST_POSTGRES_OWNER_TOKEN", "")
    if not container_id or not container_name or not owner_token:
        pytest.skip("server-restart proof requires the repository disposable PostgreSQL harness")
    assert re.fullmatch(r"[0-9a-f]{64}", container_id)
    assert re.fullmatch(r"seven-lens-p1c3-postgres-[0-9]+-[0-9]+", container_name)
    assert owner_token == f"{container_name}-owner"
    identity = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            '{{.Id}}|{{.Name}}|{{ index .Config.Labels "seven-lens.p1c3.owner" }}',
            container_id,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert identity == f"{container_id}|/{container_name}|{owner_token}"
    subprocess.run(
        ["docker", "restart", container_id],
        check=True,
        capture_output=True,
        text=True,
    )
    for _ in range(120):
        try:
            with psycopg.connect(dsn, connect_timeout=1) as connection:
                if connection.execute("SELECT 1").fetchone() == (1,):
                    return
        except psycopg.Error:
            time.sleep(0.5)
    pytest.fail("disposable PostgreSQL did not recover after its bounded restart")


@pytest.fixture
def p4c_connection(migrated_postgres: str) -> Iterator[psycopg.Connection[object]]:
    with psycopg.connect(migrated_postgres) as seed_connection:
        _seed_lineage(seed_connection)
    with psycopg.connect(migrated_postgres) as connection:
        yield connection


def _market_snapshot(
    *,
    quote_source_ref: SourceRef | None = None,
    quote_record: NormalizedSourceRecord | None = None,
    bid: str = "100.00",
    ask: str = "100.05",
    split_adjustments: tuple[SplitAdjustment, ...] = (),
    as_of: UtcTimestamp = _T0,
    known_at: UtcTimestamp = _T0,
) -> MarketSnapshot:
    sessions: list[MarketSession] = []
    current = datetime(2026, 6, 1, tzinfo=UTC)
    while len(sessions) < 253:
        if current.weekday() < 5:
            trading_date = TradingDate(current.date())
            sessions.append(
                MarketSession(
                    trading_date=trading_date,
                    day_kind=MarketDayKind.REGULAR,
                    regular_session=RegularSessionWindow(
                        opens_at=UtcTimestamp(current.replace(hour=13, minute=30)),
                        closes_at=UtcTimestamp(current.replace(hour=20)),
                    ),
                )
            )
        current -= timedelta(days=1)
    ordered_sessions = tuple(reversed(sessions))
    bars = tuple(
        _trusted_bar(session.trading_date)
        for session in ordered_sessions
        if session.trading_date.value < _T0.value.date()
    )
    if quote_record is None:
        quote_record = _source(
            quote_source_ref.record_id if quote_source_ref is not None else "quote-1",
            P4SourceFamily.ALPACA_IEX_QUOTES,
            quote_bid=bid,
            quote_ask=ask,
        )
    quote = quote_input_from_record(quote_record)
    if quote_source_ref is not None and quote.source_ref != quote_source_ref:
        raise ValueError("quote record does not match the requested source reference")
    return assemble_market_snapshot(
        security_id=_SEC,
        symbol=_SYM,
        as_of=as_of,
        known_at=known_at,
        quote=quote,
        bars=bars,
        sessions=ordered_sessions,
        split_adjustments=split_adjustments,
    )


def _universe_snapshot() -> UniverseSnapshot:
    identity = _identity()
    market = _market_snapshot()
    decision = _decision()
    return _build_universe_snapshot(
        authority=_UNIVERSE_SNAPSHOT_AUTHORITY,
        as_of=TradingDate.from_isoformat("2026-06-01"),
        known_at=_T0,
        security_master_version=master_version_for(identity),
        market_snapshot_refs=(market.snapshot_hash,),
        entries=(
            UniverseEntry(
                security_id=_SEC,
                symbol=_SYM,
                eligible=True,
                reason=None,
                identity_hash=identity.identity_hash,
                master_version=master_version_for(identity),
                market_snapshot_hash=market.snapshot_hash,
                whole_share_feasibility=WholeShareFeasibility.NOT_EVALUATED,
                quarantine_decision_hash=decision.decision_hash,
                quarantine_event_ids=decision.event_ids,
            ),
        ),
        policy_hash=_POLICY,
        schema_version=_SCHEMA,
        producer_version="p4c.universe.v1",
    )


def _feature_vector(*, as_of: UtcTimestamp = _T0, known_at: UtcTimestamp = _T0) -> object:
    names = (
        "trend_126_21",
        "trend_252_21",
        "roa",
        "cfo_to_assets",
        "accrual_quality",
        "earnings_yield",
        "fcf_yield",
        "vol63",
        "max_drawdown_252",
    )
    from seven_lens.screening.contracts import RawFeature
    from seven_lens.screening.manifests import factor_manifest

    universe = _universe_snapshot()
    factor_source_ref = _source_ref("factor-source", P4SourceFamily.SEC_EDGAR)
    bar_source_ref = _source_ref("bar-1", P4SourceFamily.ALPACA_HISTORICAL_BARS)
    market_sessions = _market_snapshot().sessions

    return _finalize_feature_vector(
        security_id=_SEC,
        symbol=_SYM,
        universe_hash=universe.universe_hash,
        manifest_hash=factor_manifest().manifest_hash,
        as_of=as_of,
        known_at=known_at,
        status=FactorStatus.COMPLETE,
        raw=tuple(
            RawFeature(
                name=name,
                value=Decimal("0.5"),
                formula_version="p4-factor-v1.0",
                source_refs=(bar_source_ref, factor_source_ref),
            )
            for name in names
        ),
        trend=Decimal("0.5"),
        quality=Decimal("0.5"),
        value=Decimal("0.5"),
        low_risk=Decimal("0.5"),
        composite=Decimal("0.5"),
        missing_reason=None,
        schema_version=_SCHEMA,
        price_session_dates=tuple(
            session.trading_date
            for session in market_sessions
            if session.trading_date.value < as_of.value.date()
        )[-252:],
    )


def _sector_assignment() -> object:
    from seven_lens.screening.manifests import sector_manifest

    return _finalize_sector_assignment(
        security_id=_SEC,
        cik="0000320193",
        sic="0100",
        division="A",
        source_ref=_source_ref("sic-source", P4SourceFamily.SEC_EDGAR, sic_only=True),
        accession=None,
        available_at=_T0,
        taxonomy_version="sec-sic-division-v1",
        taxonomy_hash=sector_manifest().manifest_hash,
    )


def _candidate_set(
    *,
    as_of: UtcTimestamp = _T0,
    known_at: UtcTimestamp = _T0,
    vector: object | None = None,
    sector_assignment_hash: str | None = None,
) -> object:
    from seven_lens.screening.contracts import CandidateEntry
    from seven_lens.screening.manifests import cluster_manifest, factor_manifest

    universe = _universe_snapshot()
    if vector is None:
        vector = _feature_vector(as_of=as_of, known_at=known_at)
    decision = _decision()
    assignment = _sector_assignment()

    def _entry(stage: CandidateStage) -> CandidateEntry:
        return _finalize_candidate_entry(
            security_id=_SEC,
            symbol=_SYM,
            composite=Decimal("0.5"),
            trend=Decimal("0.5"),
            quality=Decimal("0.5"),
            value=Decimal("0.5"),
            low_risk=Decimal("0.5"),
            stage=stage,
            feature_hash=vector.feature_hash,
            universe_hash=universe.universe_hash,
            quarantine_decision_hash=decision.decision_hash,
            sector_assignment_hash=(
                None
                if stage is CandidateStage.QUANT
                else sector_assignment_hash or assignment.assignment_hash
            ),
            evidence_source_refs=(
                ()
                if stage is CandidateStage.QUANT
                else (_source_ref("factor-source", P4SourceFamily.SEC_EDGAR),)
            ),
        )

    return build_candidate_set(
        as_of=as_of,
        known_at=known_at,
        factor_manifest_hash=factor_manifest().manifest_hash,
        cluster_manifest_hash=cluster_manifest().manifest_hash,
        universe_hash=universe.universe_hash,
        quant=(_entry(CandidateStage.QUANT),),
        evidence=(_entry(CandidateStage.EVIDENCE),),
        focus_open=(_entry(CandidateStage.FOCUS_OPEN),),
        focus_close=(_entry(CandidateStage.FOCUS_CLOSE),),
        policy_hash=_POLICY,
        producer_version="p4c.screening.v1",
        schema_version=_SCHEMA,
        feature_vectors=(vector,),
    )


def _cluster_result() -> object:
    market = _market_snapshot()
    dates = tuple(
        session.trading_date
        for session in market.sessions
        if session.trading_date.value < _T0.value.date()
    )[-126:]
    returns = tuple(
        _finalize_return_observation(
            trading_date=date,
            value=Decimal(str((index % 7) + 1)) / Decimal("100"),
            available_at=_T0,
            security_id=_SEC,
            source_ref=_source_ref("bar-1", P4SourceFamily.ALPACA_HISTORICAL_BARS),
        )
        for index, date in enumerate(dates)
    )
    results = build_clusters(
        nodes=(_SEC,),
        returns={_SEC.value: returns},
        policy_hash=_POLICY,
        as_of=_T0,
        sessions=market.sessions,
    )
    assert len(results) == 1
    return results[0]


def _append_market(connection: psycopg.Connection[object]) -> MarketSnapshot:
    snapshot = _market_snapshot()
    assert PostgresMarketSnapshotStore(connection).append(snapshot) in (
        AppendOutcome.APPENDED,
        AppendOutcome.IDEMPOTENT_DUPLICATE,
    )
    return snapshot


def _append_universe(connection: psycopg.Connection[object]) -> UniverseSnapshot:
    _append_market(connection)
    snapshot = _universe_snapshot()
    assert PostgresUniverseSnapshotStore(connection).append(snapshot) in (
        AppendOutcome.APPENDED,
        AppendOutcome.IDEMPOTENT_DUPLICATE,
    )
    return snapshot


def _append_feature(connection: psycopg.Connection[object]) -> object:
    _append_universe(connection)
    vector = _feature_vector()
    assert PostgresFeatureVectorStore(connection).append(vector) in (
        AppendOutcome.APPENDED,
        AppendOutcome.IDEMPOTENT_DUPLICATE,
    )
    assert PostgresSectorAssignmentStore(connection).append(_sector_assignment()) in (
        AppendOutcome.APPENDED,
        AppendOutcome.IDEMPOTENT_DUPLICATE,
    )
    return vector


def _append_candidate(connection: psycopg.Connection[object]) -> object:
    _append_feature(connection)
    candidate = _candidate_set()
    assert PostgresCandidateSetStore(connection).append(candidate) in (
        AppendOutcome.APPENDED,
        AppendOutcome.IDEMPOTENT_DUPLICATE,
    )
    return candidate


def test_market_snapshot_append_and_readback(p4c_connection) -> None:
    store = PostgresMarketSnapshotStore(p4c_connection)
    snapshot = _market_snapshot()
    assert store.append(snapshot) is AppendOutcome.APPENDED
    assert store.append(snapshot) is AppendOutcome.IDEMPOTENT_DUPLICATE
    readback = store.get(snapshot.snapshot_hash)
    assert readback is not None
    assert readback.wire() == snapshot.wire()
    assert readback.snapshot_hash == snapshot.snapshot_hash
    assert store.count() == 1


def test_split_snapshot_is_bound_to_the_current_confirmed_p4b_head(p4c_connection) -> None:
    split, lineage = _seed_confirmed_split(p4c_connection)
    store = PostgresMarketSnapshotStore(p4c_connection)
    snapshot = _market_snapshot(split_adjustments=(split,))
    assert store.append(snapshot) is AppendOutcome.APPENDED
    assert store.get(snapshot.snapshot_hash) == snapshot

    confirmed = lineage[-1]
    pending_at = UtcTimestamp(_T0.value + timedelta(seconds=1))
    pending = build_corporate_action_record(
        event_id=confirmed.event_id,
        security_id=confirmed.security_id,
        security_identity_hash=confirmed.security_identity_hash,
        action_type=confirmed.action_type,
        ratio=confirmed.ratio,
        declared_at=confirmed.declared_at,
        ex_date=confirmed.ex_date,
        effective_date=confirmed.effective_date,
        available_at=pending_at,
        state=CorporateActionState.EFFECTIVE_PENDING_RECONCILIATION,
        source_refs=confirmed.source_refs,
        schema_version=confirmed.schema_version,
    )
    PostgresSecurityMaster(p4c_connection).append_event(
        pending, previous_record_hash=confirmed.record_hash
    )
    later_snapshot = _market_snapshot(
        split_adjustments=(split,), as_of=pending_at, known_at=pending_at
    )
    with pytest.raises(PostgresMarketDataError) as error:
        store.append(later_snapshot)
    assert error.value.sqlstate == "23514"


def test_split_snapshot_rejects_foreign_p4b_identity_hash(p4c_connection) -> None:
    split, _ = _seed_confirmed_split(p4c_connection)
    p4c_connection.commit()
    wire = _market_snapshot(split_adjustments=(split,)).wire()
    wire["split_adjustments"][0]["security_identity_hash"] = "f" * 64  # type: ignore[index]
    forged_hash = _wire_hash("seven-lens.p4c.market-snapshot.v1", wire)
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_market_snapshot(%s, %s)",
            (forged_hash, psycopg.types.json.Jsonb(wire)),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"


def test_market_snapshot_append_rejects_hash_drift(p4c_connection) -> None:
    store = PostgresMarketSnapshotStore(p4c_connection)
    snapshot = _append_market(p4c_connection)
    # A changed immutable record cannot be dispatched under the old hash.
    with pytest.raises(ValueError, match="mid must be derived from bid and ask"):
        store.append(replace(snapshot, bid=Decimal("99.00")))


def test_market_snapshot_readback_rejects_tampered_derived_wire(p4c_connection) -> None:
    store = PostgresMarketSnapshotStore(p4c_connection)
    snapshot = _append_market(p4c_connection)
    p4c_connection.execute(
        "ALTER TABLE public.market_snapshots DISABLE TRIGGER market_snapshots_guard_write"
    )
    try:
        p4c_connection.execute(
            "UPDATE public.market_snapshots "
            "SET wire = jsonb_set(wire, '{mid}', to_jsonb(%s::text), false) "
            "WHERE snapshot_hash = %s",
            ("100.030", snapshot.snapshot_hash),
        )
    finally:
        p4c_connection.execute(
            "ALTER TABLE public.market_snapshots ENABLE TRIGGER market_snapshots_guard_write"
        )
        p4c_connection.commit()

    with pytest.raises(
        PostgresMarketDataError, match="stored market snapshot failed reconstruction"
    ):
        store.get(snapshot.snapshot_hash)


def test_market_function_rejects_semantically_forged_valid_hash(p4c_connection) -> None:
    wire = _market_snapshot().wire()
    wire["bid"] = "99.00"
    wire["ask"] = "99.05"
    wire["mid"] = "99.025"
    wire["last"] = "99.025"
    wire["spread_bps"] = 5
    forged_hash = _wire_hash("seven-lens.p4c.market-snapshot.v1", wire)
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_market_snapshot(%s, %s)",
            (forged_hash, psycopg.types.json.Jsonb(wire)),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"


def test_market_function_rejects_forged_adv_from_valid_hash(p4c_connection) -> None:
    wire = _market_snapshot().wire()
    wire["adv20_usd"] = "123456789.00"
    forged_hash = _wire_hash("seven-lens.p4c.market-snapshot.v1", wire)
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_market_snapshot(%s, %s)",
            (forged_hash, psycopg.types.json.Jsonb(wire)),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"


def test_market_function_rejects_forged_spread_from_valid_hash(p4c_connection) -> None:
    wire = _market_snapshot().wire()
    wire["spread_bps"] = 999
    forged_hash = _wire_hash("seven-lens.p4c.market-snapshot.v1", wire)
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_market_snapshot(%s, %s)",
            (forged_hash, psycopg.types.json.Jsonb(wire)),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"


def test_market_function_accepts_exact_floor_below_division_resolution(p4c_connection) -> None:
    # True spread is 30 bps - 4e-16: bid+ask == 1e12 exactly and
    # (ask-bid)*20000 == 3e13 - 0.0004.  A rounded-division floor reads 30 and
    # would reject the exact floor 29; the exact interval check accepts it.
    # Both prices remain inside the P4-A 12-integral/8-fractional-digit bound,
    # and the quote rides a real P4-A record so the lineage binding holds.
    bid = "499250000000.00000001"
    ask = "500749999999.99999999"
    quote_source = build_normalized_record(
        record_id="quote-pathological",
        family=P4SourceFamily.ALPACA_IEX_QUOTES,
        endpoint_id="latest_quote",
        schema_version=_SCHEMA,
        content_hash=sha256(b"quote-pathological:v1").hexdigest(),
        retrieved_at=_T0,
        available_at=_T0,
        payload={
            "symbol": _SYM.value,
            "bid_price": bid,
            "ask_price": ask,
            "timestamp": str(_T0),
            "feed": "iex",
        },
        material_claim=False,
        observation_at=_T0,
        coverage_warning="IEX feed only; not full NBBO/SIP market coverage",
    )
    PostgresP4RecordLog(p4c_connection).append(quote_source)
    snapshot = _market_snapshot(
        quote_source_ref=SourceRef(
            quote_source.record_id, quote_source.family, quote_source.record_hash
        ),
        bid=bid,
        ask=ask,
    )
    assert snapshot.spread_bps == 29
    assert ClosedReason.SPREAD_TOO_WIDE not in snapshot.reasons
    store = PostgresMarketSnapshotStore(p4c_connection)
    store.append(snapshot)
    assert store.get(snapshot.snapshot_hash) == snapshot
    p4c_connection.rollback()


def test_market_function_rejects_future_or_noncanonical_wire(p4c_connection) -> None:
    future_wire = _market_snapshot().wire()
    future_wire["known_at"] = "2026-06-01T13:59:59.000000Z"
    future_hash = _wire_hash("seven-lens.p4c.market-snapshot.v1", future_wire)
    with pytest.raises(psycopg.Error) as future_error:
        p4c_connection.execute(
            "SELECT public.append_market_snapshot(%s, %s)",
            (future_hash, psycopg.types.json.Jsonb(future_wire)),
        )
    p4c_connection.rollback()
    assert future_error.value.sqlstate == "23514"

    noncanonical_wire = _market_snapshot().wire()
    noncanonical_wire["as_of"] = "2026-06-01T10:00:00-04:00"
    noncanonical_hash = _wire_hash("seven-lens.p4c.market-snapshot.v1", noncanonical_wire)
    with pytest.raises(psycopg.Error) as noncanonical_error:
        p4c_connection.execute(
            "SELECT public.append_market_snapshot(%s, %s)",
            (noncanonical_hash, psycopg.types.json.Jsonb(noncanonical_wire)),
        )
    p4c_connection.rollback()
    assert noncanonical_error.value.sqlstate == "23514"


def test_market_function_rejects_inconsistent_freshness_reason_and_source_shape(
    p4c_connection,
) -> None:
    stale_reason_wire = _market_snapshot().wire()
    stale_reason_wire["reasons"] = ["QUOTE_MISSING_OR_STALE"]
    stale_reason_hash = _wire_hash("seven-lens.p4c.market-snapshot.v1", stale_reason_wire)
    with pytest.raises(psycopg.Error) as stale_reason_error:
        p4c_connection.execute(
            "SELECT public.append_market_snapshot(%s, %s)",
            (stale_reason_hash, psycopg.types.json.Jsonb(stale_reason_wire)),
        )
    p4c_connection.rollback()
    assert stale_reason_error.value.sqlstate == "23514"

    null_source_wire = _market_snapshot().wire()
    null_source_wire["quote_source_ref"]["record_hash"] = None  # type: ignore[index]
    null_source_hash = _wire_hash("seven-lens.p4c.market-snapshot.v1", null_source_wire)
    with pytest.raises(psycopg.Error) as null_source_error:
        p4c_connection.execute(
            "SELECT public.append_market_snapshot(%s, %s)",
            (null_source_hash, psycopg.types.json.Jsonb(null_source_wire)),
        )
    p4c_connection.rollback()
    assert null_source_error.value.sqlstate == "23514"


@pytest.mark.parametrize(
    ("function_name", "wire_limit"),
    (
        ("append_market_snapshot", MAX_MARKET_SNAPSHOT_BYTES),
        ("append_universe_snapshot", MAX_UNIVERSE_SNAPSHOT_BYTES),
        ("append_feature_vector", MAX_FEATURE_VECTOR_BYTES),
        ("append_sector_assignment", MAX_SECTOR_ASSIGNMENT_BYTES),
        ("append_candidate_set", MAX_CANDIDATE_SET_BYTES),
        ("append_cluster_result", MAX_CLUSTER_RESULT_BYTES),
    ),
)
def test_public_append_functions_enforce_canonical_wire_byte_caps(
    p4c_connection, function_name: str, wire_limit: int
) -> None:
    """Direct SQL callers cannot bypass the closed-record resource limits."""
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            f"SELECT public.{function_name}(%s, jsonb_build_object('oversized', repeat('x', %s)))",
            ("0" * 64, wire_limit),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "22023"


def test_append_functions_are_idempotent_under_two_publishers(migrated_postgres: str) -> None:
    with psycopg.connect(migrated_postgres) as connection:
        _seed_lineage(connection)
    market = _market_snapshot()
    outcomes = _append_concurrently(
        migrated_postgres, "append_market_snapshot", market.snapshot_hash, market.wire()
    )
    assert sorted(outcomes) == ["APPENDED", "IDEMPOTENT_DUPLICATE"]
    universe = _universe_snapshot()
    outcomes = _append_concurrently(
        migrated_postgres, "append_universe_snapshot", universe.universe_hash, universe.wire()
    )
    assert sorted(outcomes) == ["APPENDED", "IDEMPOTENT_DUPLICATE"]
    vector = _feature_vector()
    outcomes = _append_concurrently(
        migrated_postgres,
        "append_feature_vector",
        vector.feature_hash,  # type: ignore[attr-defined]
        vector.wire(),  # type: ignore[attr-defined]
    )
    assert sorted(outcomes) == ["APPENDED", "IDEMPOTENT_DUPLICATE"]
    assignment = _sector_assignment()
    outcomes = _append_concurrently(
        migrated_postgres,
        "append_sector_assignment",
        assignment.assignment_hash,  # type: ignore[attr-defined]
        assignment.wire(),  # type: ignore[attr-defined]
    )
    assert sorted(outcomes) == ["APPENDED", "IDEMPOTENT_DUPLICATE"]
    candidate = _candidate_set()
    outcomes = _append_concurrently(
        migrated_postgres,
        "append_candidate_set",
        candidate.candidate_hash,  # type: ignore[attr-defined]
        candidate.wire(),  # type: ignore[attr-defined]
    )
    assert sorted(outcomes) == ["APPENDED", "IDEMPOTENT_DUPLICATE"]


def test_candidate_publication_is_atomic_across_child_failure_crash_and_restart(
    migrated_postgres: str,
) -> None:
    with psycopg.connect(migrated_postgres) as connection:
        _seed_lineage(connection)
        _append_feature(connection)
        connection.commit()

    with psycopg.connect(migrated_postgres, autocommit=True) as connection:
        connection.execute(
            """
            CREATE FUNCTION public.p4c_test_fail_evidence_child()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF NEW.stage = 'EVIDENCE' THEN
                    RAISE EXCEPTION 'injected P4-C child failure';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        connection.execute(
            "CREATE TRIGGER p4c_test_fail_evidence_child "
            "BEFORE INSERT ON public.candidate_set_entries "
            "FOR EACH ROW EXECUTE FUNCTION public.p4c_test_fail_evidence_child()"
        )

    candidate = _candidate_set()
    try:
        with psycopg.connect(migrated_postgres) as connection:
            with pytest.raises(psycopg.Error, match="injected P4-C child failure"):
                connection.execute(
                    "SELECT public.append_candidate_set(%s, %s)",
                    (candidate.candidate_hash, psycopg.types.json.Jsonb(candidate.wire())),
                )
            connection.rollback()
            assert connection.execute("SELECT count(*) FROM public.candidate_sets").fetchone() == (
                0,
            )
            assert connection.execute(
                "SELECT count(*) FROM public.candidate_set_entries"
            ).fetchone() == (0,)
    finally:
        with psycopg.connect(migrated_postgres, autocommit=True) as connection:
            connection.execute(
                "DROP TRIGGER IF EXISTS p4c_test_fail_evidence_child "
                "ON public.candidate_set_entries"
            )
            connection.execute("DROP FUNCTION IF EXISTS public.p4c_test_fail_evidence_child()")

    interrupted = psycopg.connect(migrated_postgres)
    backend_pid = interrupted.execute("SELECT pg_backend_pid()").fetchone()[0]
    interrupted.execute(
        "SELECT public.append_candidate_set(%s, %s)",
        (candidate.candidate_hash, psycopg.types.json.Jsonb(candidate.wire())),
    )
    with psycopg.connect(migrated_postgres, autocommit=True) as terminator:
        assert terminator.execute("SELECT pg_terminate_backend(%s)", (backend_pid,)).fetchone() == (
            True,
        )
    interrupted.close()

    with psycopg.connect(migrated_postgres) as connection:
        assert connection.execute("SELECT count(*) FROM public.candidate_sets").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM public.candidate_set_entries"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT public.append_candidate_set(%s, %s)",
            (candidate.candidate_hash, psycopg.types.json.Jsonb(candidate.wire())),
        ).fetchone() == ("APPENDED",)
        connection.commit()

    _restart_disposable_postgres(migrated_postgres)
    with psycopg.connect(migrated_postgres) as connection:
        assert connection.execute(
            "SELECT wire FROM public.candidate_sets WHERE candidate_hash = %s",
            (candidate.candidate_hash,),
        ).fetchone() == (candidate.wire(),)
        assert connection.execute(
            "SELECT stage, count(*) FROM public.candidate_set_entries "
            "WHERE candidate_hash = %s GROUP BY stage ORDER BY stage",
            (candidate.candidate_hash,),
        ).fetchall() == [
            ("EVIDENCE", 1),
            ("FOCUS_CLOSE", 1),
            ("FOCUS_OPEN", 1),
            ("QUANT", 1),
        ]


def test_different_hash_for_same_authority_is_rejected(
    p4c_connection: psycopg.Connection[object],
) -> None:
    market = _append_market(p4c_connection)
    universe = _append_universe(p4c_connection)
    vector = _append_feature(p4c_connection)
    candidate = _append_candidate(p4c_connection)
    cases = (
        (
            "append_market_snapshot",
            market.wire(),
            "seven-lens.p4c.market-snapshot.v1",
        ),
        (
            "append_universe_snapshot",
            universe.wire(),
            "seven-lens.p4c.universe-snapshot.v1",
        ),
        (
            "append_feature_vector",
            vector.wire(),  # type: ignore[attr-defined]
            "seven-lens.p4c.feature-vector.v1",
        ),
        (
            "append_candidate_set",
            candidate.wire(),  # type: ignore[attr-defined]
            "seven-lens.p4c.candidate-set.v1",
        ),
    )

    for function_name, original_wire, domain in cases:
        original_hash = _wire_hash(domain, original_wire)
        p4c_connection.execute(
            f"SELECT public.{function_name}(%s, %s)",
            (original_hash, psycopg.types.json.Jsonb(original_wire)),
        )
        p4c_connection.commit()

        conflicting_wire = dict(original_wire)
        if function_name == "append_market_snapshot":
            sessions = conflicting_wire["sessions"]
            assert isinstance(sessions, list)
            conflicting_wire["sessions"] = [
                {
                    "trading_date": "2025-06-11",
                    "day_kind": "REGULAR",
                    "opens_at": "2025-06-11T13:30:00.000000Z",
                    "closes_at": "2025-06-11T20:00:00.000000Z",
                },
                *sessions,
            ]
        elif function_name == "append_universe_snapshot":
            conflicting_wire["policy_hash"] = "b" * 64
        elif function_name == "append_feature_vector":
            conflicting_raw = [dict(item) for item in conflicting_wire["raw"]]
            conflicting_raw[0]["value"] = "0.6"
            conflicting_wire["raw"] = conflicting_raw
        else:
            conflicting_wire["policy_hash"] = "b" * 64

        conflicting_hash = _wire_hash(domain, conflicting_wire)
        with pytest.raises(psycopg.Error) as error:
            p4c_connection.execute(
                f"SELECT public.{function_name}(%s, %s)",
                (conflicting_hash, psycopg.types.json.Jsonb(conflicting_wire)),
            )
        assert error.value.sqlstate == "23505"
        p4c_connection.rollback()


def test_universe_snapshot_append_and_readback(p4c_connection) -> None:
    _append_market(p4c_connection)
    store = PostgresUniverseSnapshotStore(p4c_connection)
    snapshot = _universe_snapshot()
    assert store.append(snapshot) is AppendOutcome.APPENDED
    assert store.append(snapshot) is AppendOutcome.IDEMPOTENT_DUPLICATE
    readback = store.get(snapshot.universe_hash)
    assert readback is not None
    assert readback.wire() == snapshot.wire()
    assert store.count() == 1


def test_feature_vector_append_and_readback(p4c_connection) -> None:
    _append_universe(p4c_connection)
    store = PostgresFeatureVectorStore(p4c_connection)
    vector = _feature_vector()
    assert store.append(vector) is AppendOutcome.APPENDED
    readback = store.get(vector.feature_hash)
    assert readback is not None
    assert readback.wire() == vector.wire()


def test_feature_vector_readback_rejects_tampered_producer_version(p4c_connection) -> None:
    _append_universe(p4c_connection)
    store = PostgresFeatureVectorStore(p4c_connection)
    vector = _feature_vector()
    assert store.append(vector) is AppendOutcome.APPENDED
    p4c_connection.execute(
        "ALTER TABLE public.feature_vectors DISABLE TRIGGER feature_vectors_guard_write"
    )
    try:
        p4c_connection.execute(
            "UPDATE public.feature_vectors "
            "SET wire = jsonb_set(wire, '{producer_version}', to_jsonb(%s::text), false) "
            "WHERE feature_hash = %s",
            ("tampered", vector.feature_hash),
        )
    finally:
        p4c_connection.execute(
            "ALTER TABLE public.feature_vectors ENABLE TRIGGER feature_vectors_guard_write"
        )
        p4c_connection.commit()

    with pytest.raises(PostgresUniverseError, match="producer_version is not approved"):
        store.get(vector.feature_hash)


def test_feature_function_rejects_direct_hash_bypass(p4c_connection) -> None:
    _append_universe(p4c_connection)
    vector = _feature_vector()
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_feature_vector(%s, %s)",
            ("0" * 64, psycopg.types.json.Jsonb(vector.wire())),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"
    assert p4c_connection.execute("SELECT count(*) FROM public.feature_vectors").fetchone() == (0,)


def test_sector_assignment_append_and_readback(p4c_connection) -> None:
    store = PostgresSectorAssignmentStore(p4c_connection)
    assignment = _sector_assignment()
    assert store.append(assignment) is AppendOutcome.APPENDED
    assert store.append(assignment) is AppendOutcome.IDEMPOTENT_DUPLICATE
    readback = store.get(assignment.assignment_hash)
    assert readback is not None
    assert readback.wire() == assignment.wire()


def test_candidate_set_append_and_readback(p4c_connection) -> None:
    _append_feature(p4c_connection)
    store = PostgresCandidateSetStore(p4c_connection)
    candidate = _candidate_set()
    assert store.append(candidate) is AppendOutcome.APPENDED
    readback = store.get(candidate.candidate_hash)
    assert readback is not None
    assert readback.wire() == candidate.wire()


def test_candidate_function_rejects_missing_sector_parent(p4c_connection) -> None:
    _append_universe(p4c_connection)
    vector = _feature_vector()
    assert PostgresFeatureVectorStore(p4c_connection).append(vector) is AppendOutcome.APPENDED
    candidate = _candidate_set(sector_assignment_hash="d" * 64)
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_candidate_set(%s, %s)",
            (candidate.candidate_hash, psycopg.types.json.Jsonb(candidate.wire())),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"
    assert p4c_connection.execute("SELECT count(*) FROM public.candidate_sets").fetchone() == (0,)


def test_candidate_function_rejects_feature_known_after_candidate_cutoff(p4c_connection) -> None:
    _append_universe(p4c_connection)
    feature_as_of = UtcTimestamp(_T0.value + timedelta(minutes=1))
    future_vector = _feature_vector(
        as_of=feature_as_of,
        known_at=UtcTimestamp(_T0.value + timedelta(seconds=1)),
    )
    assert (
        PostgresFeatureVectorStore(p4c_connection).append(future_vector) is AppendOutcome.APPENDED
    )
    candidate = _candidate_set()
    wire = candidate.wire()
    wire["as_of"] = str(feature_as_of)
    for stage in ("quant", "evidence", "focus_open", "focus_close"):
        for entry in wire[stage]:  # type: ignore[index]
            entry["feature_hash"] = future_vector.feature_hash  # type: ignore[index]
    candidate_hash = _wire_hash("seven-lens.p4c.candidate-set.v1", wire)
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_candidate_set(%s, %s)",
            (candidate_hash, psycopg.types.json.Jsonb(wire)),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"
    assert p4c_connection.execute("SELECT count(*) FROM public.candidate_sets").fetchone() == (0,)


def test_candidate_function_rejects_direct_hash_bypass(p4c_connection) -> None:
    _append_feature(p4c_connection)
    candidate = _candidate_set()
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_candidate_set(%s, %s)",
            ("0" * 64, psycopg.types.json.Jsonb(candidate.wire())),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"
    assert p4c_connection.execute("SELECT count(*) FROM public.candidate_sets").fetchone() == (0,)


def test_universe_function_rejects_partial_parent_wire(p4c_connection) -> None:
    _append_market(p4c_connection)
    wire = _universe_snapshot().wire()
    del wire["entries"]
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_universe_snapshot(%s, %s)",
            ("0" * 64, psycopg.types.json.Jsonb(wire)),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"
    assert p4c_connection.execute("SELECT count(*) FROM public.universe_snapshots").fetchone() == (
        0,
    )


def test_universe_function_rejects_forged_eligible_references(p4c_connection) -> None:
    _append_market(p4c_connection)
    wire = _universe_snapshot().wire()
    wire["entries"][0]["identity_hash"] = None  # type: ignore[index]
    forged_hash = _wire_hash("seven-lens.p4c.universe-snapshot.v1", wire)
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_universe_snapshot(%s, %s)",
            (forged_hash, psycopg.types.json.Jsonb(wire)),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"


def test_feature_function_rejects_forged_complete_raw_value(p4c_connection) -> None:
    _append_universe(p4c_connection)
    wire = _feature_vector().wire()
    wire["raw"][0]["value"] = None  # type: ignore[index]
    wire["raw"][0]["missing_reason"] = "forged"  # type: ignore[index]
    forged_hash = _wire_hash("seven-lens.p4c.feature-vector.v1", wire)
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_feature_vector(%s, %s)",
            (forged_hash, psycopg.types.json.Jsonb(wire)),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"


def test_feature_function_rejects_cross_security_raw_lineage(p4c_connection) -> None:
    _append_universe(p4c_connection)
    wire = _feature_vector().wire()
    wire["raw"][0]["security_id"] = "22222222-2222-4222-8222-222222222222"  # type: ignore[index]
    forged_hash = _wire_hash("seven-lens.p4c.feature-vector.v1", wire)
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_feature_vector(%s, %s)",
            (forged_hash, psycopg.types.json.Jsonb(wire)),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"
    assert p4c_connection.execute("SELECT count(*) FROM public.feature_vectors").fetchone() == (0,)


def test_candidate_function_rejects_forged_noncanonical_order(p4c_connection) -> None:
    _append_feature(p4c_connection)
    wire = _candidate_set().wire()
    original = dict(wire["quant"][0])  # type: ignore[index]
    lower = dict(original)
    lower["security_id"] = "22222222-2222-4222-8222-222222222222"
    lower["symbol"] = "LOWER"
    for name in ("composite", "trend", "quality", "value", "low_risk"):
        lower[name] = "0.4"
    wire["quant"] = [lower, original]
    forged_hash = _wire_hash("seven-lens.p4c.candidate-set.v1", wire)
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_candidate_set(%s, %s)",
            (forged_hash, psycopg.types.json.Jsonb(wire)),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"


def test_candidate_function_rejects_forged_parent_order(p4c_connection) -> None:
    _append_feature(p4c_connection)
    wire = _candidate_set().wire()
    first = dict(wire["quant"][0])  # type: ignore[index]
    second = dict(first)
    second["security_id"] = "22222222-2222-4222-8222-222222222222"
    second["symbol"] = "SECOND"
    for name in ("composite", "trend", "quality", "value", "low_risk"):
        second[name] = "0.4"
    wire["quant"] = [first, second]

    first_evidence = dict(first)
    first_evidence["stage"] = "EVIDENCE"
    second_evidence = dict(second)
    second_evidence["stage"] = "EVIDENCE"
    for name in ("composite", "trend", "quality", "value", "low_risk"):
        first_evidence[name] = "0.4"
        second_evidence[name] = "0.5"
    wire["evidence"] = [second_evidence, first_evidence]
    focus_open = dict(second_evidence)
    focus_open["stage"] = "FOCUS_OPEN"
    focus_close = dict(second_evidence)
    focus_close["stage"] = "FOCUS_CLOSE"
    wire["focus_open"] = [focus_open]
    wire["focus_close"] = [focus_close]
    forged_hash = _wire_hash("seven-lens.p4c.candidate-set.v1", wire)
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_candidate_set(%s, %s)",
            (forged_hash, psycopg.types.json.Jsonb(wire)),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"


def test_candidate_function_rejects_forged_child_score_lineage(p4c_connection) -> None:
    _append_feature(p4c_connection)
    wire = _candidate_set().wire()
    wire["evidence"][0]["symbol"] = "DRIFT"  # type: ignore[index]
    forged_hash = _wire_hash("seven-lens.p4c.candidate-set.v1", wire)
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_candidate_set(%s, %s)",
            (forged_hash, psycopg.types.json.Jsonb(wire)),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"


def test_candidate_function_rejects_forged_parent_feature_scores(p4c_connection) -> None:
    _append_feature(p4c_connection)
    wire = _candidate_set().wire()
    for stage in ("quant", "evidence", "focus_open", "focus_close"):
        for entry in wire[stage]:  # type: ignore[index]
            for name in ("composite", "trend", "quality", "value", "low_risk"):
                entry[name] = "0.9"  # type: ignore[index]
    forged_hash = _wire_hash("seven-lens.p4c.candidate-set.v1", wire)
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_candidate_set(%s, %s)",
            (forged_hash, psycopg.types.json.Jsonb(wire)),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"
    assert p4c_connection.execute("SELECT count(*) FROM public.candidate_sets").fetchone() == (0,)


def test_sector_function_rejects_cik_not_bound_to_identity(p4c_connection) -> None:
    foreign_source = _source(
        "foreign-sec-cik",
        P4SourceFamily.SEC_EDGAR,
        cik="0000999999",
    )
    PostgresP4RecordLog(p4c_connection).append(foreign_source)
    assignment = _finalize_sector_assignment(
        security_id=_SEC,
        cik="0000999999",
        sic="0100",
        division="A",
        source_ref=SourceRef(
            foreign_source.record_id,
            foreign_source.family,
            foreign_source.record_hash,
        ),
        accession=None,
        available_at=_T0,
        taxonomy_version="sec-sic-division-v1",
        taxonomy_hash=sector_manifest().manifest_hash,
    )
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_sector_assignment(%s, %s)",
            (assignment.assignment_hash, psycopg.types.json.Jsonb(assignment.wire())),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"


def test_sector_function_rejects_sic_not_bound_to_source(p4c_connection) -> None:
    assignment = _finalize_sector_assignment(
        security_id=_SEC,
        cik="0000320193",
        sic="0200",
        division="A",
        source_ref=_source_ref("factor-source", P4SourceFamily.SEC_EDGAR),
        accession="0000320193-26-000001",
        available_at=_T0,
        taxonomy_version="sec-sic-division-v1",
        taxonomy_hash=sector_manifest().manifest_hash,
    )
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_sector_assignment(%s, %s)",
            (assignment.assignment_hash, psycopg.types.json.Jsonb(assignment.wire())),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"


def test_append_only_guard_rejects_update(p4c_connection) -> None:
    store = PostgresMarketSnapshotStore(p4c_connection)
    snapshot = _market_snapshot()
    store.append(snapshot)
    with pytest.raises(psycopg.Error):
        p4c_connection.execute(
            "UPDATE public.market_snapshots SET bid = '99.00' WHERE snapshot_hash = %s",
            (snapshot.snapshot_hash,),
        )


@pytest.fixture
def p4c_runtime_postgres(migrated_postgres: str) -> Iterator[tuple[str, object]]:
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    from seven_lens.infrastructure.postgres_roles import provision_runtime_role

    role = "seven_lens_p4c_runtime"
    password = "p4c-disposable-runtime-only"
    with psycopg.connect(migrated_postgres) as seed_connection:
        _seed_lineage(seed_connection)
    with psycopg.connect(migrated_postgres, autocommit=True) as connection:
        if connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
    provision_runtime_role(migrated_postgres, role)
    runtime_dsn = make_conninfo(migrated_postgres, user=role, password=password)
    yield runtime_dsn, role


@pytest.fixture
def p4c_worker_postgres(migrated_postgres: str) -> Iterator[tuple[str, str, str]]:
    """Create an independent login inheriting only the NOLOGIN P4-C worker role."""

    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    from seven_lens.infrastructure.postgres_roles import (
        provision_p4c_worker_role,
        verify_p4c_worker_role,
    )

    worker_role = "seven_lens_p4c_worker"
    worker_login = "seven_lens_p4c_worker_login"
    password = "p4c-disposable-worker-only"
    with psycopg.connect(migrated_postgres, autocommit=True) as seed_connection:
        _seed_lineage(seed_connection)
        if seed_connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (worker_login,)
        ).fetchone():
            seed_connection.execute(
                sql.SQL("DROP OWNED BY {}").format(sql.Identifier(worker_login))
            )
        seed_connection.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(worker_login))
        )
        if seed_connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (worker_role,)
        ).fetchone():
            seed_connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(worker_role)))
        seed_connection.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(worker_role))
        )

    evidence = provision_p4c_worker_role(migrated_postgres, worker_role)
    assert verify_p4c_worker_role(migrated_postgres, worker_role) == evidence
    with psycopg.connect(migrated_postgres, autocommit=True) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()[0]
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} INHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            ).format(sql.Identifier(worker_login), sql.Literal(password))
        )
        connection.execute(
            sql.SQL("GRANT {} TO {}").format(
                sql.Identifier(worker_role), sql.Identifier(worker_login)
            )
        )
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database_name), sql.Identifier(worker_login)
            )
        )
        connection.execute(
            sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM {}").format(
                sql.Identifier(database_name), sql.Identifier(worker_login)
            )
        )
        connection.execute(
            sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(sql.Identifier(worker_login))
        )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(worker_login))
        )
    worker_dsn = make_conninfo(migrated_postgres, user=worker_login, password=password)
    try:
        yield worker_dsn, worker_role, worker_login
    finally:
        with psycopg.connect(migrated_postgres, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(worker_login)))
            connection.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(worker_login))
            )
            connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(worker_role)))
            connection.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(worker_role))
            )


def test_runtime_role_cannot_write_tables_directly(p4c_runtime_postgres) -> None:
    dsn, _ = p4c_runtime_postgres
    with psycopg.connect(dsn) as runtime_connection:
        with pytest.raises(psycopg.Error):
            runtime_connection.execute(
                "INSERT INTO public.market_snapshots "
                "(snapshot_hash, security_id, symbol, as_of, received_at, "
                "feed, coverage, freshness, wire) "
                "VALUES ('a'::text, 'b'::text, 'c'::text, now(), now(), "
                "'iex', 'LIMITED_MARKET_COVERAGE', 'FRESH', '{}')"
            )
        runtime_connection.rollback()
        with pytest.raises(psycopg.Error):
            runtime_connection.execute("DELETE FROM public.market_snapshots")
        runtime_connection.rollback()
        # but the runtime role can SELECT from P4-C tables
        runtime_connection.execute("SELECT count(*) FROM public.market_snapshots").fetchone()


def test_runtime_role_can_execute_p4c_functions(p4c_runtime_postgres) -> None:
    dsn, _ = p4c_runtime_postgres
    snapshot = _market_snapshot()
    with psycopg.connect(dsn) as runtime_connection:
        row = runtime_connection.execute(
            "SELECT public.append_market_snapshot(%s, %s)",
            (snapshot.snapshot_hash, psycopg.types.json.Jsonb(snapshot.wire())),
        ).fetchone()
        assert row is not None and row[0] == "APPENDED"


def test_runtime_role_cannot_execute_or_set_role_to_p4c_worker(
    p4c_runtime_postgres,
    p4c_worker_postgres,
) -> None:
    runtime_dsn, _ = p4c_runtime_postgres
    _, worker_role, _ = p4c_worker_postgres
    with psycopg.connect(runtime_dsn) as runtime_connection:
        assert runtime_connection.execute(
            "SELECT has_function_privilege(current_user, "
            "'public.append_feature_vector(text,jsonb)', 'EXECUTE'), "
            "has_function_privilege(current_user, "
            "'public.append_candidate_set(text,jsonb)', 'EXECUTE'), "
            "has_function_privilege(current_user, "
            "'public.append_cluster_result(text,jsonb)', 'EXECUTE'), "
            "pg_has_role(current_user, %s, 'MEMBER')",
            (worker_role,),
        ).fetchone() == (False, False, False, False)
        for function_name in (
            "append_feature_vector",
            "append_candidate_set",
            "append_cluster_result",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
                runtime_connection.execute(
                    f"SELECT public.{function_name}(%s, %s)",
                    ("0" * 64, psycopg.types.json.Jsonb({})),
                )
            assert error.value.sqlstate == "42501"
            runtime_connection.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
            runtime_connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(worker_role)))
        assert error.value.sqlstate == "42501"
        runtime_connection.rollback()


def test_p4c_worker_login_can_append_through_normal_repositories(
    p4c_worker_postgres,
) -> None:
    worker_dsn, _, worker_login = p4c_worker_postgres
    with psycopg.connect(worker_dsn) as worker_connection:
        assert worker_connection.execute("SELECT current_user").fetchone() == (worker_login,)
        assert worker_connection.execute(
            "SELECT has_database_privilege(current_user, current_database(), 'CONNECT'), "
            "has_database_privilege(current_user, current_database(), 'TEMPORARY'), "
            "has_schema_privilege(current_user, 'public', 'USAGE'), "
            "has_schema_privilege(current_user, 'public', 'CREATE')"
        ).fetchone() == (True, False, True, False)
        _append_market(worker_connection)
        _append_universe(worker_connection)
        vector = _append_feature(worker_connection)
        candidate = _append_candidate(worker_connection)
        result = _cluster_result()
        assert PostgresFeatureVectorStore(worker_connection).get(vector.feature_hash) == vector
        assert (
            PostgresCandidateSetStore(worker_connection).get(candidate.candidate_hash) == candidate
        )
        assert (
            PostgresClusterResultStore(worker_connection).append(result) is AppendOutcome.APPENDED
        )


def test_cluster_result_store_is_durable_and_idempotent(p4c_connection) -> None:
    result = _cluster_result()
    store = PostgresClusterResultStore(p4c_connection)
    assert store.append(result) is AppendOutcome.APPENDED
    assert store.append(result) is AppendOutcome.IDEMPOTENT_DUPLICATE
    assert store.get(result.cluster_id) == result
    assert store.results_for_as_of(_T0) == (result,)
    assert store.count() == 1


def test_cluster_function_rejects_source_for_unlisted_member(p4c_connection) -> None:
    market = _market_snapshot()
    dates = tuple(
        session.trading_date
        for session in market.sessions
        if session.trading_date.value < _T0.value.date()
    )[-126:]
    returns = tuple(
        _finalize_return_observation(
            trading_date=date,
            value=Decimal(str((index % 7) + 1)) / Decimal("100"),
            available_at=_T0,
            security_id=_SEC,
            source_ref=_source_ref("bar-1", P4SourceFamily.ALPACA_HISTORICAL_BARS),
        )
        for index, date in enumerate(dates)
    )
    result = build_clusters(
        nodes=(_SEC,),
        returns={_SEC.value: returns},
        policy_hash=_POLICY,
        as_of=_T0,
        sessions=market.sessions,
    )[0]
    wire = result.wire()
    wire["members"] = ["22222222-2222-4222-8222-222222222222"]
    forged_id = _wire_hash("seven-lens.p4c.cluster-id.v1", wire)
    with pytest.raises(psycopg.Error) as error:
        p4c_connection.execute(
            "SELECT public.append_cluster_result(%s, %s)",
            (forged_id, psycopg.types.json.Jsonb(wire)),
        )
    p4c_connection.rollback()
    assert error.value.sqlstate == "23514"
    assert p4c_connection.execute("SELECT count(*) FROM public.cluster_results").fetchone() == (0,)
